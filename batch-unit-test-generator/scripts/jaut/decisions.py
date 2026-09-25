"""纯决策核心(零 I/O、可单测)。

三个决策函数对应三处流程分叉, 输入为已采集好的类型化上下文, 输出为 Decision:
    decide_after_init   : 基线 / final-check 门槛判定
    decide_after_plan   : 方法队列推进 / 终验 / 收尾
    decide_after_verify : 单方法迭代双条件判定 + 预算/升级(SKILL §5/§6)

升级策略(按优先级, 命中即返回):
    覆盖率达标且测试全绿 -> done
    单方法轮次 >= METHOD_ROUND_BUDGET + method_round_bonus -> 自动跳过当前方法
    全局轮次   >= GLOBAL_ROUND_BUDGET + global_round_bonus  -> 自动跳过全部 pending
    连续 TEST_FAIL_STREAK_ROUNDS(3) 轮测试失败 -> 自动跳过当前方法
    连续 NO_IMPROVEMENT_ROUNDS(3) 轮覆盖率无提升 -> 自动跳过当前方法
    测试不绿 -> 回 build_prompt 修复
    覆盖率未达标 -> 回 build_prompt 继续

预算耗尽不再 ask_user(子代理无感, 也不穿透主流程): 一律自动跳过并记录原因,
由入口脚本写入 MethodCoverage.skip_reason 后路由 make_plan 推进下一方法;
队列空时由 decide_after_plan 收尾(批量模式输出类内完成报告)。

决策函数不修改任何状态; 需要落地到 state 的动作(标 done / 复位轨迹 / 跳过方法)
以 Decision 的 mark_done / reset_trajectory / auto_skip_* 标志回传, 由入口脚本
应用后再持久化。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from . import config
from .models import (
    ClassCoverage,
    Decision,
    MethodCoverage,
    MethodStatus,
    NextCommand,
    Route,
    State,
    TestOutcome,
    TestResult,
)

# --------------------------------------------------------------------------- #
# 升级判定原语(纯函数)
# --------------------------------------------------------------------------- #
def no_improvement(round_rates: list[float], limit: int = config.NO_IMPROVEMENT_ROUNDS,
                   threshold: Optional[float] = None) -> bool:
    """连续 limit 轮覆盖率无提升: 最近 limit 轮非递增 且 未接近门槛。

    语义: "无提升" 不仅看趋势(非递增), 还看绝对值: 若当前覆盖率已接近门槛(>=90%),
    则即使趋势平坦也不视为"无提升"(用户可能已满足于当前水平)。
    """
    if len(round_rates) < limit:
        return False
    tail = round_rates[-limit:]
    trend_flat = all(tail[i] <= tail[i - 1] for i in range(1, len(tail)))
    if not trend_flat:
        return False
    # 趋势平坦时, 检查是否已接近门槛
    if threshold is not None and max(round_rates) >= threshold * config.NO_IMPROVEMENT_NEAR_THRESHOLD_FACTOR:
        return False
    return True


def test_failure_streak(round_results: list[TestOutcome],
                        limit: int = config.TEST_FAIL_STREAK_ROUNDS) -> bool:
    """连续 limit 轮测试失败(Failures+Errors>0)不收敛。"""
    if len(round_results) < limit:
        return False
    return all(r.failed for r in round_results[-limit:])


# --------------------------------------------------------------------------- #
# 计划阶段域函数(纯)
# --------------------------------------------------------------------------- #
def sort_failing(methods: list[MethodCoverage]) -> list[MethodCoverage]:
    """未达标(pending)方法按覆盖率升序排序, 并列按 missed 行数降序。"""
    failing = [m for m in methods if m.status == MethodStatus.PENDING]
    return sorted(failing, key=lambda m: (m.rate, -m.missed))


def select_next_method(methods: list[MethodCoverage]) -> Optional[MethodCoverage]:
    """选出当前最值得补测的方法(排序后首个), 无则 None。"""
    failing = sort_failing(methods)
    return failing[0] if failing else None


def test_class_relpath(fqcn: str) -> str:
    """测试类相对模块的路径: src/test/java/<同包>/<类名>Test.java。"""
    pkg, _, simple = fqcn.rpartition(".")
    pkg_dir = pkg.replace(".", "/")
    if pkg_dir:
        return f"src/test/java/{pkg_dir}/{simple}Test.java"
    return f"src/test/java/{simple}Test.java"


def test_simple_name(fqcn: str) -> str:
    """测试类简单名: 取 FQCN 最后一段并追加 'Test'。

    例: 'com.foo.UserService' -> 'UserServiceTest'
    """
    return fqcn.rsplit(".", 1)[-1] + "Test"


# --------------------------------------------------------------------------- #
# 自动跳过(预算耗尽/不收敛的统一动作)
# --------------------------------------------------------------------------- #
def _auto_skip(ctx: VerifyContext, metrics: dict, artifacts: list, *,
               reason: str, summary: str,
               skip_all: bool = False) -> Decision:
    """预算耗尽/不收敛: 跳过当前方法(或全部 pending)并回 make_plan 推进队列。

    skip_all=True 时同时跳过所有剩余 pending 方法(全局/类级预算耗尽的语义);
    两种情况都路由 MAKE_PLAN, 由 decide_after_plan 决定下一个方法或收尾。
    入口脚本据 auto_skip_* 标志落地 state。
    """
    metrics = dict(metrics, skip_reason=reason, skip_all_pending=skip_all)
    return Decision(
        status="success", exit_code=config.EXIT_CONTINUE, summary=summary,
        route=Route.MAKE_PLAN,
        reason="预算耗尽, 自动跳过" if not skip_all else "预算耗尽, 跳过全部未达标方法",
        artifacts=artifacts, metrics=metrics,
        auto_skip_method=True, auto_skip_reason=reason,
        skip_all_pending=skip_all, reset_trajectory=True)


# --------------------------------------------------------------------------- #
# verify 上下文与决策
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class VerifyContext:
    """verify_coverage 决策输入(mvn 已成功且覆盖率报告已产出)。"""

    method: MethodCoverage          # 已刷新 after 覆盖率与轨迹的当前方法
    before_rate: float
    class_cov: ClassCoverage
    test: TestResult
    threshold: float
    tests_green: bool
    uncleaned: bool
    iteration: int                  # 已 +1 的单方法轮次
    global_iteration: int           # 已 +1 的全局轮次
    mvn_log: str
    surefire_report_dir: str
    test_simple: str
    test_class_file: str
    # 预算追加窗口(make_plan --grant-rounds 落地; 判定时计入有效预算)
    method_round_bonus: int = 0
    global_round_bonus: int = 0

    @property
    def label(self) -> str:
        return self.method.key.label()


def decide_after_verify(ctx: VerifyContext) -> Decision:
    """单方法迭代决策(见模块 docstring 的优先级)。

    按优先级依次判定:
        1. 双条件达标(覆盖率达标 + 测试全绿) -> done
        2. 单方法预算耗尽 -> 自动跳过当前方法
        3. 全局预算耗尽 -> 自动跳过全部未达标方法
        4. 连续测试失败 -> 自动跳过当前方法
        5. 连续无提升 -> 自动跳过当前方法
        6. 测试不绿 -> 回 build_prompt 修复
        7. 覆盖率未达标 -> 回 build_prompt 继续
    """
    after_rate = ctx.method.rate
    coverage_met = ctx.method.coverage_met(ctx.threshold)
    metrics = _verify_metrics(ctx, after_rate)
    artifacts = _verify_artifacts(ctx)

    # 1) 双条件达标
    if ctx.tests_green and coverage_met:
        test_status = _test_status(ctx)
        return Decision(
            status="success", exit_code=config.EXIT_OK,
            summary=(f"方法 {ctx.label} 达标(覆盖率 {ctx.before_rate:.1f}% -> "
                     f"{after_rate:.1f}%, {test_status})"),
            route=Route.MAKE_PLAN, reason="取下一个未达标方法",
            artifacts=artifacts, metrics=metrics, mark_done=True)
    # 2) 单方法预算耗尽(含此前追加的窗口) -> 自动跳过当前方法
    method_budget = config.METHOD_ROUND_BUDGET + ctx.method_round_bonus
    if ctx.iteration >= method_budget:
        return _auto_skip(
            ctx, metrics, artifacts,
            reason=f"单方法迭代已达上限 {method_budget} 轮仍未达标(当前 {after_rate:.1f}%)",
            summary=(f"方法 {ctx.label} 已达单方法迭代上限 {method_budget} 轮"
                     f"(当前 {after_rate:.1f}%, {_test_status(ctx)}), 自动跳过"))
    # 3) 全局预算耗尽(含此前追加的窗口) -> 自动跳过全部未达标方法
    global_budget = config.GLOBAL_ROUND_BUDGET + ctx.global_round_bonus
    if ctx.global_iteration >= global_budget:
        return _auto_skip(
            ctx, metrics, artifacts, skip_all=True,
            reason=f"全局迭代已达上限 {global_budget} 轮, 未达标方法不再迭代",
            summary=(f"全局迭代已达上限 {global_budget} 轮"
                     f"(方法 {ctx.label} 当前 {after_rate:.1f}%, {_test_status(ctx)}), "
                     "跳过全部未达标方法"))
    # 4) 连续测试失败不收敛 -> 自动跳过当前方法
    if test_failure_streak(ctx.method.round_test_results):
        trajectory = [(r.failures, r.errors) for r in ctx.method.round_test_results]
        return _auto_skip(
            ctx, metrics, artifacts,
            reason=(f"连续 {config.TEST_FAIL_STREAK_ROUNDS} 轮测试失败不收敛"
                    f"(每轮 Failures/Errors: {trajectory})"),
            summary=(f"方法 {ctx.label} 连续 {config.TEST_FAIL_STREAK_ROUNDS} 轮测试失败不收敛, "
                     "自动跳过"))
    # 5) 连续覆盖率无提升 -> 自动跳过当前方法
    if no_improvement(ctx.method.round_rates, threshold=ctx.threshold):
        trajectory = list(ctx.method.round_rates)
        return _auto_skip(
            ctx, metrics, artifacts,
            reason=(f"连续 {config.NO_IMPROVEMENT_ROUNDS} 轮覆盖率无提升"
                    f"(覆盖率轨迹: {trajectory})"),
            summary=(f"方法 {ctx.label} 连续 {config.NO_IMPROVEMENT_ROUNDS} 轮覆盖率无提升"
                     f"(当前 {after_rate:.1f}%, {_test_status(ctx)}), 自动跳过"))
    # 6) 测试不绿 -> 回 build_prompt 修复失败用例
    if not ctx.tests_green:
        summary, instructions = _verify_not_green(ctx, after_rate)
        return Decision(
            status="failed", exit_code=config.EXIT_CONTINUE, summary=summary,
            route=Route.BUILD_PROMPT, reason="测试失败, 重新组装提示词修复失败用例",
            instructions=instructions, artifacts=artifacts, metrics=metrics)
    # 7) 覆盖率未达标 -> 回 build_prompt 继续
    return Decision(
        status="success", exit_code=config.EXIT_CONTINUE,
        summary=(f"方法 {ctx.label} 未达标: {ctx.before_rate:.1f}% -> {after_rate:.1f}% "
                 f"(门槛 {ctx.threshold}%), 第 {ctx.iteration} 轮"),
        route=Route.BUILD_PROMPT, reason="重新组装提示词继续迭代",
        artifacts=artifacts, metrics=metrics)


def _test_status(ctx: VerifyContext) -> str:
    """测试状态描述, 用于升级消息中补充测试状态(避免只报覆盖率误导用户)。"""
    if ctx.tests_green:
        if ctx.test.skipped > 0:
            return f"测试全绿(但有 {ctx.test.skipped} 个测试被跳过)"
        return "测试全绿"
    status = f"测试失败(Failures {ctx.test.failures}, Errors {ctx.test.errors})"
    if ctx.test.skipped > 0:
        status += f", 跳过 {ctx.test.skipped} 个"
    return status


def _verify_metrics(ctx: VerifyContext, after_rate: float) -> dict:
    return {
        "iteration": ctx.iteration,
        "global_iteration": ctx.global_iteration,
        "method_round_bonus": ctx.method_round_bonus,
        "global_round_bonus": ctx.global_round_bonus,
        "before": round(ctx.before_rate, 2),
        "after": round(after_rate, 2),
        "threshold": ctx.threshold,
        "class_rate": round(ctx.class_cov.rate, 2),
        "tests_run": ctx.test.tests,
        "tests_failed": ctx.test.failures,
        "tests_errors": ctx.test.errors,
        "tests_skipped": ctx.test.skipped,
        "tests_green": ctx.tests_green,
    }


def _verify_artifacts(ctx: VerifyContext) -> list[dict]:
    artifacts = [{"path": ctx.mvn_log, "kind": "mvn_log"}]
    if ctx.test.report_found:
        artifacts.append({"path": ctx.surefire_report_dir, "kind": "surefire_report"})
    return artifacts


def _verify_not_green(ctx: VerifyContext, after_rate: float) -> tuple[str, str]:
    """测试不绿时按成因(报告缺失 / 清理失败 / 存在失败用例)构造 summary 与 instructions。"""
    test = ctx.test
    only_edit = f"注意: 只能修改 {ctx.test_class_file or '测试文件'}。"
    if not test.report_found:
        summary = (f"方法 {ctx.label}: mvn 成功但未产出测试报告, 无法确认测试通过, "
                   f"第 {ctx.iteration} 轮")
        instructions = ("mvn 成功但未产出 surefire-reports 测试报告, 测试可能根本没有执行, "
                        "请排查测试类名/包路径与 -Dtest 是否匹配"
                        f"(当前 -Dtest={ctx.test_simple}), 确认测试类存在且包含可执行的 @Test 方法, "
                        f"修复后重新验证。详见 {ctx.mvn_log}。{only_edit}")
        return summary, instructions
    if test.failures == 0 and test.errors == 0 and test.parse_errors == 0 and ctx.uncleaned:
        summary = (f"方法 {ctx.label}: surefire 报告目录清理失败, 本轮测试结果不可信, "
                   f"第 {ctx.iteration} 轮")
        instructions = (f"surefire 报告目录清理失败(可能被 IDE/进程占用), 本轮测试结果不可信, "
                        f"请关闭占用进程后重试。{only_edit}")
        return summary, instructions
    fail_lines = test.fail_lines()
    summary = (f"方法 {ctx.label} 测试失败(Failures {test.failures}, Errors {test.errors}), "
               f"覆盖率 {after_rate:.1f}% 均不达标, 第 {ctx.iteration} 轮")
    if test.parse_errors:
        summary += f", {test.parse_errors} 个 surefire 报告无法解析, 按失败计"
    if ctx.uncleaned:
        summary += " (注意: surefire 目录清理失败, 测试结果可能含陈旧数据)"
    instructions = ("上一轮测试存在失败用例(断言/异常), 覆盖率不达标, "
                    "请修复以下失败用例:\n"
                    + "\n".join(f"- {line}" for line in fail_lines)
                    + f"\n先阅读 {ctx.mvn_log} 定位失败根因, 再修改测试代码。{only_edit}")
    return summary, instructions


# --------------------------------------------------------------------------- #
# init / final-check 上下文与决策
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class InitContext:
    """init_coverage 决策输入(基线或 final-check)。"""

    final_check: bool
    method_mode: bool               # 是否限定单方法(target_method 非空)
    class_rate: float
    threshold: float
    failing_count: int              # pending 方法数
    scope_met: bool                 # 范围已满足: 无 pending; method 模式还要求目标方法 done
    class_met: bool                 # 类级覆盖率 >= threshold
    test: TestResult
    tests_green: bool
    uncleaned: bool
    final_check_fail_streak: int    # 已更新后的终验不绿连续计数
    coverage_path: str
    state_path: str
    mvn_log: str
    test_simple: Optional[str] = None
    # finish 时由入口脚本渲染好的收尾报告(report.render_finish_report), 逐字透传
    report: Optional[str] = None

    @property
    def env_uncertain(self) -> bool:
        """结果不可信且无真实失败用例(见模块函数 env_uncertain)。"""
        return env_uncertain(self.test, self.uncleaned)


def init_is_met(scope_met: bool, tests_green: bool, class_met: bool, method_mode: bool) -> bool:
    """init/final-check 达标判定(单一事实源, 供入口更新 streak 与决策共用)。

    method 模式只看 scope_met + 测试全绿; class 模式还需类级覆盖率达标。
    """
    if not (scope_met and tests_green):
        return False
    return True if method_mode else class_met


def env_uncertain(test: TestResult, uncleaned: bool) -> bool:
    """终验不绿且无真实失败用例(报告缺失/无法解析/目录清理失败): 结果不可信。

    fail-closed 口径与 TestResult.is_green 对齐: 三类"不可信"信号任一命中且无
    失败用例时, 决策与报告须按"未能复核"处理, 不得混同普通测试失败。
    """
    if test.failed_cases or test.failures or test.errors:
        return False
    return (not test.report_found) or test.parse_errors > 0 or uncleaned


def decide_after_init(ctx: InitContext) -> Decision:
    """init_coverage 阶段决策: 基线/终验门槛判定。

    按优先级依次判定:
        1. 达标且测试全绿 -> finish
        2. 终验连续不绿且无待修复方法 -> finish(未达标收尾/未复核收尾, 不再 ask_user)
        3. 测试不绿 -> 回 make_plan 修复(终验失败用例含非目标类 -> 未达标收尾;
           报告缺失/清理失败等环境问题 -> 重试, 由 final_check_fail_streak 兜底)
        4. 覆盖率/范围未达标 -> 无 pending 方法则未达标收尾, 否则回 make_plan 补测
    """
    met = init_is_met(ctx.scope_met, ctx.tests_green, ctx.class_met, ctx.method_mode)
    metrics = _init_metrics(ctx)
    coverage_artifact = {"path": ctx.coverage_path, "kind": "coverage"}

    # 1) 达标且测试全绿 -> finish
    if met:
        return Decision(
            status="success", exit_code=config.EXIT_OK,
            summary=(f"覆盖率达标: 类级 {ctx.class_rate:.2f}% ≥ {ctx.threshold}%, 无未达标方法, "
                     f"测试全绿(Tests run: {ctx.test.tests}, Failures: 0, Errors: 0)"),
            route=Route.FINISH, reason="覆盖率达标且测试全绿, 任务完成",
            deliverables=[ctx.coverage_path, ctx.state_path],
            artifacts=[coverage_artifact], metrics=metrics, report=ctx.report)
    # 2) 终验连续不绿且无待修复方法 -> 未达标收尾/未复核收尾(禁止无限循环, 不再 ask_user)
    # 用 failing_count(而非 scope_met)判"已无待修复方法": method 模式下目标方法被
    # 跳过时 scope_met 为 False 但队列已空, 若用 scope_met 本分支永不触发,
    # 终验不绿的有限重试将失去上界(严格语义只用于达标判定 init_is_met)
    if (ctx.final_check and not ctx.tests_green and ctx.failing_count == 0
            and ctx.final_check_fail_streak >= config.FINAL_CHECK_FAIL_STREAK_LIMIT):
        if ctx.env_uncertain:
            # 环境不可信(报告缺失/无法解析/目录清理失败, 无失败用例): 不混同
            # "测试失败", 按未复核收尾; 环境修复后可重新终验复核
            return Decision(
                status="success", exit_code=config.EXIT_OK,
                summary=(f"终验连续 {ctx.final_check_fail_streak} 轮测试结果不可信"
                         "(surefire 报告缺失/无法解析或目录清理失败)且无待修复方法, "
                         "未能复核达标; 环境修复后可重新终验复核"),
                route=Route.FINISH, reason="终验环境不可信且无可修复方法, 未复核收尾",
                deliverables=[ctx.coverage_path, ctx.state_path],
                artifacts=[coverage_artifact, {"path": ctx.mvn_log, "kind": "mvn_log"}],
                metrics=metrics, report=ctx.report)
        fail_lines = _annotated_fail_lines(ctx)
        return Decision(
            status="success", exit_code=config.EXIT_OK,
            summary=(f"终验连续 {ctx.final_check_fail_streak} 轮测试不绿且无待修复方法, "
                     "未达标收尾。失败用例清单: " + "; ".join(fail_lines)),
            route=Route.FINISH, reason="终验不达标且无可修复方法, 未达标收尾",
            deliverables=[ctx.coverage_path, ctx.state_path],
            artifacts=[coverage_artifact, {"path": ctx.mvn_log, "kind": "mvn_log"}],
            metrics=metrics, report=ctx.report)
    # 3) 测试不绿 -> failed, 回 make_plan 修复
    if not ctx.tests_green:
        # 终验时所有方法已done, 按失败用例归属分流
        if ctx.final_check and ctx.failing_count == 0:
            if _failures_all_in_target_class(ctx):
                # 失败用例全在目标测试类 -> 直接路由 write_code 修复
                summary, instructions = _init_not_green(ctx)
                # 将对应方法打回 pending 并路由 write_code
                # 注意: 这里不修改 state, 由入口脚本根据 decision 标志处理
                return Decision(
                    status="failed", exit_code=config.EXIT_CONTINUE, summary=summary,
                    route=Route.WRITE_CODE, reason="终验失败用例全在目标测试类, 直接修复",
                    instructions=instructions,
                    on_complete=NextCommand("make_plan.py"),
                    artifacts=[coverage_artifact, {"path": ctx.mvn_log, "kind": "mvn_log"}],
                    metrics=metrics, reset_trajectory=True)
            elif ctx.test.failed_cases:
                # 包含非目标类失败 -> 未达标收尾(技能无权修复, 不必等 streak)
                fail_lines = _annotated_fail_lines(ctx)
                return Decision(
                    status="success", exit_code=config.EXIT_OK,
                    summary=("终验失败用例包含非目标类, 技能无权修复, 未达标收尾。"
                             "失败用例清单: " + "; ".join(fail_lines)),
                    route=Route.FINISH, reason="终验不绿且包含非目标类失败, 未达标收尾",
                    deliverables=[ctx.coverage_path, ctx.state_path],
                    artifacts=[coverage_artifact, {"path": ctx.mvn_log, "kind": "mvn_log"}],
                    metrics=metrics, report=ctx.report)
            # 报告缺失/清理失败等环境问题(无失败用例但不绿): 落入通用路径重试,
            # 由 final_check_fail_streak 兜底, 不直接终态化
        # 非终验或还有待修复方法 -> 保持原有逻辑
        summary, instructions = _init_not_green(ctx)
        return Decision(
            status="failed", exit_code=config.EXIT_CONTINUE, summary=summary,
            route=Route.MAKE_PLAN, reason="测试失败, 回 make_plan.py 继续修复",
            instructions=instructions,
            artifacts=[coverage_artifact, {"path": ctx.mvn_log, "kind": "mvn_log"}],
            metrics=metrics)
    # 4) 测试全绿但覆盖率/范围未达标
    if ctx.final_check:
        summary = f"终验未达标: 类级 {ctx.class_rate:.2f}%, 未达标方法 {ctx.failing_count} 个"
    else:
        summary = (f"覆盖率未达标: 类级 {ctx.class_rate:.2f}% (门槛 {ctx.threshold}%), "
                   f"未达标方法 {ctx.failing_count} 个")
    # 无可补测方法(方法已 done/skipped)却仍未达标 -> 未达标收尾, 否则回计划阶段补测。
    # 判定用 failing_count(还有 pending 才值得回计划): method 模式下 scope_met 还会因
    # 目标方法被跳过而为 False, 此时无 pending 可补, 回 make_plan 只会空转
    if ctx.failing_count == 0:
        return Decision(
            status="success", exit_code=config.EXIT_OK,
            summary=(f"无可补测方法(方法已完成或被跳过), 类级覆盖率 {ctx.class_rate:.2f}% "
                     f"未达门槛 {ctx.threshold}%, 未达标收尾"),
            route=Route.FINISH, reason="无可补测方法且未达标, 未达标收尾",
            deliverables=[ctx.coverage_path, ctx.state_path],
            artifacts=[coverage_artifact, {"path": ctx.mvn_log, "kind": "mvn_log"}],
            metrics=metrics, report=ctx.report)
    return Decision(
        status="success", exit_code=config.EXIT_CONTINUE, summary=summary,
        route=Route.MAKE_PLAN, reason="未达标, 进入计划阶段逐方法补测试",
        artifacts=[coverage_artifact, {"path": ctx.mvn_log, "kind": "mvn_log"}],
        metrics=metrics)


def _init_metrics(ctx: InitContext) -> dict:
    return {
        "class_rate": round(ctx.class_rate, 2),
        "threshold": ctx.threshold,
        "failing_methods": ctx.failing_count,
        "final_check": ctx.final_check,
        "tests_run": ctx.test.tests,
        "tests_failed": ctx.test.failures,
        "tests_errors": ctx.test.errors,
        "tests_skipped": ctx.test.skipped,
        "tests_green": ctx.tests_green,
    }


def _annotated_fail_lines(ctx: InitContext) -> list[str]:
    """失败清单, 终验时按目标测试类归属标注([目标类] / [非目标类-需人工处理])。"""
    lines: list[str] = []
    for fc in ctx.test.failed_cases:
        line = fc.summary_line()
        if ctx.test_simple:
            cls_simple = fc.class_name.rsplit(".", 1)[-1]
            prefix = "[目标类] " if cls_simple == ctx.test_simple else "[非目标类-需人工处理] "
            line = prefix + line
        lines.append(line)
    return lines


def _failures_all_in_target_class(ctx: InitContext) -> bool:
    """判断失败用例是否全属于目标测试类。

    返回 True: 所有失败用例都属于目标测试类, 可以路由到 write_code 修复。
    返回 False: 包含非目标类失败, 或 failed_cases 为空(报告缺失/清理失败等环境问题),
                或目标测试类名未知; 调用方按 failed_cases 是否为空区分两种成因。
    """
    if not ctx.test_simple:
        return False
    if not ctx.test.failed_cases:
        return False  # 无失败用例但测试不绿 = 报告缺失/环境问题, 不属于目标类修复范畴
    return len(ctx.test.failures_in_class(ctx.test_simple)) == len(ctx.test.failed_cases)


def _init_not_green(ctx: InitContext) -> tuple[str, str]:
    test = ctx.test
    only_edit = "注意: 只能修改测试代码, 不得修改 src/main/java 下的业务代码。"
    if not test.report_found:
        summary = f"mvn 成功但未产出测试报告, 无法确认测试通过(类级 {ctx.class_rate:.2f}%)"
        instructions = ("mvn 成功但未产出 surefire-reports 测试报告, 测试可能根本没有执行, "
                        "请排查测试类名/包路径是否匹配, 确认存在可执行的 @Test 方法, "
                        f"修复后重新验证。详见 {ctx.mvn_log}。{only_edit}")
        return summary, instructions
    if test.failures == 0 and test.errors == 0 and test.parse_errors == 0 and ctx.uncleaned:
        summary = f"surefire 报告目录清理失败, 本轮测试结果不可信(类级 {ctx.class_rate:.2f}%)"
        instructions = (f"surefire 目录清理失败(可能被 IDE/杀软等进程占用), 本轮测试结果不可信, "
                        f"请关闭占用进程后重试。{only_edit}")
        return summary, instructions
    fail_lines = _annotated_fail_lines(ctx)
    summary = (f"测试未全绿(Tests run {test.tests}, Failures {test.failures}, "
               f"Errors {test.errors}), 覆盖率 {ctx.class_rate:.2f}% 不达标")
    if test.parse_errors:
        summary += f", {test.parse_errors} 个 surefire 报告无法解析, 按失败计"
    if fail_lines:
        summary += "; 失败用例: " + "; ".join(fail_lines)
    if ctx.uncleaned:
        summary += " (注意: surefire 目录清理失败, 测试结果可能含陈旧数据)"
    instructions = ("存在测试失败用例(断言/异常), 覆盖率不达标, "
                    "请修复以下失败用例:\n"
                    + "\n".join(f"- {line}" for line in fail_lines)
                    + f"\n先阅读 {ctx.mvn_log} 定位失败根因, 再修改测试代码。{only_edit}")
    return summary, instructions


# --------------------------------------------------------------------------- #
# plan 上下文与决策
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class PlanContext:
    """make_plan 决策输入。"""

    has_pending: bool
    final_checked: bool
    current_label: str = ""
    current_rate: float = 0.0
    remaining: int = 0
    coverage_path: str = ""
    state_path: str = ""
    # finish 时由入口脚本渲染好的收尾报告(report.render_finish_report), 逐字透传
    report: str = ""
    # 批量模式: 队列空且未终验时路由 FINISH 而非 FINAL_CHECK(终验由主流程统一执行)
    batch_mode: bool = False
    # 批量模式类内完成报告(report.render_class_complete_report, 逐字透传)
    class_complete_report: str = ""


def decide_after_plan(ctx: PlanContext) -> Decision:
    """make_plan 阶段决策: 方法队列推进/终验/收尾。

    按优先级依次判定:
        1. 有未达标方法 -> build_prompt
        2. 队列空且已终验 -> finish
        3. 队列空未终验 -> final-check
    """
    # 有未达标方法 -> build_prompt
    if ctx.has_pending:
        return Decision(
            status="success", exit_code=config.EXIT_OK,
            summary=(f"当前方法 {ctx.current_label}(覆盖率 {ctx.current_rate:.1f}%), "
                     f"剩余未达标 {ctx.remaining} 个"),
            route=Route.BUILD_PROMPT, reason="为当前方法组装编写提示词",
            metrics={"remaining": ctx.remaining, "current_method": ctx.current_label,
                     "current_rate": round(ctx.current_rate, 2)})
    # 队列空且已终验 -> finish
    if ctx.final_checked:
        return Decision(
            status="success", exit_code=config.EXIT_OK, summary="全部方法达标且终验通过",
            route=Route.FINISH, reason="任务完成",
            deliverables=[ctx.coverage_path, ctx.state_path], report=ctx.report or None)
    # 批量模式: 队列空未终验 -> FINISH(携带类内完成报告, 终验由主流程 batch_finish 统一执行)
    if ctx.batch_mode:
        return Decision(
            status="success", exit_code=config.EXIT_OK,
            summary="类内全部方法达标(批量模式, 跳过类内终验)",
            route=Route.FINISH, reason="批量模式类内完成, 终验由主流程统一执行",
            deliverables=[ctx.coverage_path, ctx.state_path],
            report=ctx.class_complete_report or None)
    # 队列空未终验 -> final-check
    return Decision(
        status="success", exit_code=config.EXIT_OK, summary="全部方法达标, 进入全量终验",
        route=Route.FINAL_CHECK, reason="队列为空, 全量复核类级覆盖率")
