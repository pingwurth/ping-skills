#!/usr/bin/env python3
"""第四步后半(迭代核心): 定向复跑测试并做单方法双条件判定(verify_coverage 阶段)。

工作流位置:
    validate_rules 通过后进入本脚本(SKILL.md §5)。定向只跑目标测试类, 复核当前方法
    覆盖率与测试结果, 是 build_prompt <-> verify_coverage 迭代循环的收敛判定点。

流程:
    clean 旧报告 -> fast-single-cov.sh(编译+测试+JaCoCo覆盖率) -> 解析 jacoco 覆盖率
    -> 解析 surefire 结果 -> 记录本轮观测(轮次 +1、before->after) -> 交 decisions 决策。

职责边界:
    构建/清理/执行覆盖率采集 -> maven.run_single_cov_with_fallback; 覆盖率解析 -> jacoco; 测试解析 -> surefire;
    双条件判定/预算/跳过 -> decisions.decide_after_verify(纯函数);
    本文件负责"记录观测到 state"与"把决策回传的 mark_done/reset_trajectory/auto_skip_* 落地后持久化"。

判定与预算(SKILL.md §5/§6, 详见 decisions):
    覆盖率达标且测试全绿 -> done -> make_plan; 单方法 >=8 轮 / 全局 >=30 轮 /
    连续 3 轮失败 / 连续 3 轮无提升 / 类级预算(默认 30 轮, 探索模式翻倍)耗尽
    -> 自动跳过并写入 skip_reason -> make_plan; 测试不绿或覆盖率未达标 -> build_prompt。

输入:
    --workdir 或 --project-root 定位 workdir; 读取 <workdir>/state.json,
    必须已含 current_method(否则状态错误 exit 3)。

输出(NEXT_STEP):
    达标/跳过 -> run_script make_plan.py(exit 0); 未达标 -> run_script build_prompt.py(exit 1);
    mvn/报告/环境失败 -> ask_user(exit 2, failed)。
    artifacts 附 mvn.log 与 surefire-reports; metrics 附 before/after/轮次/测试计数。

关键约束:
    - 测试是否通过以 surefire 为唯一依据(fail-closed), -Dmaven.test.failure.ignore=true
      只保证失败轮仍产出覆盖率报告, 不作为达标依据。
    - 每次执行前清理旧报告; state.json 原子写入。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import _path_setup  # noqa: F401  — 初始化 sys.path 以导入 jaut 包

from jaut import config, decisions, jacoco, maven, surefire  # noqa: E402
from jaut.cli import EmitContext, StepError, add_common_args, require_workdir, run_cli  # noqa: E402
from jaut.logutil import setup_logger  # noqa: E402
from jaut.models import Decision, MethodCoverage, MethodStatus, Route, State, TestOutcome  # noqa: E402
from jaut.state import StateStore  # noqa: E402

SCRIPT_NAME = "verify_coverage"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """注册命令行参数: 复用共享的 --workdir / --project-root(二选一即可定位 workdir)。"""
    add_common_args(parser)


def handler(args: argparse.Namespace) -> tuple[Decision, EmitContext]:
    """定向复跑测试、记录本轮观测并产出迭代决策。

    步骤:
        1. 定位 workdir 并读取 state.json, 校验 current_method 存在;
        2. 清理旧报告 -> fast-single-cov.sh(编译+测试+JaCoCo覆盖率); 失败即 StepError(不改状态);
        3. 解析 surefire(fail-closed 绿灯)与 jacoco 覆盖率;
        4. _record_observation 把本轮结果写入 state(轮次 +1);
        5. decisions.decide_after_verify 产出决策;
        6. 落地 mark_done / reset_trajectory / auto_skip_* 后原子保存 state。

    Args:
        args: 已解析的命令行参数。

    Returns:
        (Decision, EmitContext): 决策(make_plan / build_prompt)与路由上下文。

    Raises:
        StepError: workdir/state/current_method 缺失(exit 3), 或 mvn/报告/环境失败(exit 2)。
    """
    workdir = require_workdir(args)
    store = StateStore(workdir)
    with store.locked():
        state = store.load()
        if state is None or state.current_method is None:
            raise StepError.state_error(
                "state.json 缺失或无 current_method(需先运行 make_plan.py)",
                question="请先运行 make_plan.py")
        logger = setup_logger(SCRIPT_NAME, workdir)

        project_root = state.project_root
        module = state.module or "."
        mvn_log = state.mvn_log or str(workdir / config.MVN_LOG_FILENAME)
        test_simple = state.test_class_simple or decisions.test_simple_name(state.target_class)

        # 类级预算预检查(批量模式): 耗尽则跳过全部未达标方法, 不再执行无谓的 mvn。
        # state 已就地落地, auto_skip_* 仅作决策语义标记(与 decide_after_verify
        # 的 _auto_skip 同形), 路由 MAKE_PLAN 继续类内队列。
        if state.class_budget_exhausted:
            budget = state.class_round_budget
            reason = state.class_budget_skip_reason
            skipped = state.skip_pending_methods(reason)   # 与 batch_finish 共用同一次扫描
            store.save(state)
            logger.info(f"{reason}; 跳过 {len(skipped)} 个未达标方法")
            return Decision(
                status="success", exit_code=config.EXIT_CONTINUE,
                summary=f"类级预算耗尽({budget} 轮), 跳过全部未达标方法",
                route=Route.MAKE_PLAN, reason="类级预算耗尽, 自动跳过全部未达标方法",
                auto_skip_method=True, auto_skip_reason=reason,
                skip_all_pending=True,
                metrics={"class_round_used": state.class_round_used,
                         "class_round_budget": budget,
                         "skipped_methods": len(skipped)}), \
                EmitContext(workdir=workdir, state=state)

        # 清理旧报告 -> fast-single-cov.sh(失败降级 mvn test jacoco:report)
        # 依赖模块已在 init_coverage 阶段预装, 传 no_am=True 跳过重编译
        maven.clean_jacoco_dirs(project_root, [module])
        uncleaned = maven.clean_surefire_dirs(project_root, [module])
        if uncleaned:
            logger.warning(f"surefire 目录清理失败, 本轮测试结果可能含陈旧数据: {uncleaned}")
        logger.info(f"执行覆盖率采集: module={module}, class={state.target_class}, test={test_simple}")
        result, _fast_cov_log = maven.run_single_cov_with_fallback(
            project_root, module, state.target_class, test_simple, mvn_log,
            no_am=True, jacoco_version=state.jacoco_version, state=state)
        if not result.ok:
            # P0-6 修复: 区分编译失败与环境/依赖失败
            # 编译失败 -> 记录观测(轮次+1)并路由到 write_code 自愈
            # 依赖/环境失败 -> ask_user(exit 2)
            compile_errors = maven.parse_compile_errors(mvn_log)
            if compile_errors:
                # 编译失败: 记录观测(轮次+1)并路由到 write_code
                logger.info(f"检测到 {len(compile_errors)} 个编译错误, 路由到 write_code 自愈")
                # 记录本轮观测(即使编译失败也要记录, 确保轮次计数递增)
                entry = state.find_method(state.current_method)  # type: ignore[arg-type]
                if entry is None:
                    entry = MethodCoverage(key=state.current_method, covered=0, missed=0,
                                           status=MethodStatus.PENDING)
                    state.methods.append(entry)
                entry.round_rates.append(entry.rate)
                entry.round_test_results.append(TestOutcome(failures=0, errors=0))  # 编译失败不计入测试失败轨迹
                state.iteration += 1
                state.global_iteration += 1
                if state.batch_mode:
                    state.class_round_used += 1
                state.test_history.append(
                    {"method": state.current_method.label(),
                     "round": state.iteration,
                     "failures": [], "errors": [], "compile_errors": [e.summary_line() for e in compile_errors]})
                store.save(state)
                
                # 构造包含编译错误详情的 instructions
                only_edit = f"注意: 只能修改 {state.test_class_file or '测试文件'}。"
                error_lines = [e.summary_line() for e in compile_errors[:10]]  # 最多显示10个
                instructions = ("编译失败, 请修复以下错误:\n"
                                + "\n".join(f"- {line}" for line in error_lines)
                                + f"\n详见 {mvn_log}。{only_edit}")
                
                decision = Decision(
                    status="failed", exit_code=config.EXIT_CONTINUE,
                    summary=f"编译失败({len(compile_errors)} 个错误), 第 {state.iteration} 轮",
                    route=Route.WRITE_CODE, reason="编译失败, 路由到 write_code 修复",
                    instructions=instructions,
                    artifacts=[{"path": mvn_log, "kind": "mvn_log"}],
                    metrics={"iteration": state.iteration, "global_iteration": state.global_iteration,
                             "compile_errors": len(compile_errors)})
                return decision, EmitContext(workdir=workdir, state=state)
            else:
                # 依赖解析失败/环境缺失 -> ask_user(exit 2, 环境问题仍需人工介入)
                raise StepError.exec_error("mvn 执行失败(依赖解析或环境问题)",
                                           question=f"mvn 失败, 详见 {mvn_log}",
                                           artifacts=[{"path": mvn_log, "kind": "mvn_log"}])

        # surefire 结果(唯一判定测试是否通过)
        test = surefire.parse_surefire_reports(project_root, module)
        tests_green = test.is_green(bool(uncleaned))
        if not test.report_found:
            logger.warning("mvn 成功但未产出 surefire-reports, 本轮按测试失败计(fail-closed)")
        if test.parse_errors:
            logger.warning(f"{test.parse_errors} 个 surefire 报告无法解析, 按失败计(fail-closed)")
        for fc in test.failed_cases:
            logger.info(f"[TEST-FAIL] {fc.summary_line()}")
        if test.skipped > 0:
            logger.warning(
                f"[SKIPPED] 本轮有 {test.skipped} 个测试被跳过(可能使用了 @Disabled), "
                f"请确认是否有意为之; 被跳过的测试不计入 failures, 但可能导致覆盖率虚高")
        xml_path, csv_path = jacoco.report_paths(project_root, module)
        if not xml_path.is_file():
            raise StepError.exec_error(f"未找到覆盖率报告 {xml_path}",
                                       question=f"详见 {mvn_log}",
                                       artifacts=[{"path": mvn_log, "kind": "mvn_log"}])

        entry, before_rate = _record_observation(state, xml_path, csv_path, test,
                                                 tests_green, bool(uncleaned))
        class_cov = state.class_coverage

        ctx = decisions.VerifyContext(
            method=entry, before_rate=before_rate, class_cov=class_cov, test=test,
            threshold=state.threshold, tests_green=tests_green, uncleaned=bool(uncleaned),
            iteration=state.iteration, global_iteration=state.global_iteration,
            method_round_bonus=state.method_round_bonus,
            global_round_bonus=state.global_round_bonus,
            mvn_log=mvn_log,
            surefire_report_dir=str(surefire.report_paths(project_root, module)),
            test_simple=test_simple, test_class_file=state.test_class_file)
        decision = decisions.decide_after_verify(ctx)

        # 决策落地到 state
        if decision.mark_done:
            entry.status = MethodStatus.DONE
        elif decision.auto_skip_method:
            # 预算耗尽/不收敛: 自动跳过并记录原因(不再 ask_user 穿透)
            entry.status = MethodStatus.SKIPPED
            entry.skip_reason = decision.auto_skip_reason
            state.validate_fail_streak = 0
            logger.info(f"自动跳过方法 {entry.key.label()}: {decision.auto_skip_reason}")
        if decision.reset_trajectory:
            entry.reset_trajectory()
        if decision.skip_all_pending:
            for m in state.methods:
                if m.status == MethodStatus.PENDING:
                    m.status = MethodStatus.SKIPPED
                    m.skip_reason = decision.auto_skip_reason
            logger.info(f"跳过全部未达标方法: {decision.auto_skip_reason}")
        store.save(state)
        logger.info(f"方法 {entry.key.label()}: {before_rate:.1f}% -> {entry.rate:.1f}% "
                    f"(轮次 {state.iteration}/{config.METHOD_ROUND_BUDGET + state.method_round_bonus}, "
                    f"全局 {state.global_iteration}/{config.GLOBAL_ROUND_BUDGET + state.global_round_bonus}, "
                    f"类级 {state.class_round_used}/{state.class_round_budget}, "
                    f"门槛 {state.threshold}%, 测试绿={tests_green}, 路由={decision.route.value})")
    return decision, EmitContext(workdir=workdir, state=state)


def _record_observation(state: State, xml_path: Path, csv_path: Path,
                        test, tests_green: bool,
                        uncleaned: bool) -> tuple[MethodCoverage, float]:
    """把本轮覆盖率/测试结果记录进 state(就地修改), 返回 (当前方法条目, before 覆盖率)。

    记录内容:
        - 刷新当前方法的 covered/missed(取自本轮 jacoco), 追加覆盖率轨迹与测试结果轨迹;
        - 刷新类级覆盖率(CSV 优先, XML 兜底);
        - iteration 与 global_iteration 各 +1; 追加 coverage_history;
        - 测试不绿时追加 test_history(含失败用例清单)。

    Args:
        state: 当前状态(会被就地更新)。
        xml_path: 本轮 jacoco.xml 路径。
        csv_path: 本轮 jacoco.csv 路径。
        test: surefire 解析结果(TestResult)。
        tests_green: fail-closed 绿灯判定结果。
        uncleaned: surefire 目录是否清理失败(影响 test_failure_streak 的兜底计数)。

    Returns:
        (MethodCoverage, float): 当前方法条目(已含 after 覆盖率)与本轮 before 覆盖率。
    """
    current = state.current_method
    parsed_all = jacoco.parse_jacoco_xml(xml_path, excludes=state.coverage_excludes)
    parsed = parsed_all.get(state.target_class, [])
    new_entry = next((m for m in parsed if m.key == current), None)

    entry = state.find_method(current)  # type: ignore[arg-type]
    if entry is None:
        entry = MethodCoverage(key=current, covered=0, missed=0, status=MethodStatus.PENDING)
        state.methods.append(entry)

    before_rate = entry.rate
    if new_entry is not None:
        entry.covered, entry.missed = new_entry.covered, new_entry.missed
    after_rate = entry.rate
    entry.round_rates.append(after_rate)
    entry.round_test_results.append(
        TestOutcome(failures=test.failure_count_for_trajectory(uncleaned), errors=test.errors))

    # 类级覆盖率刷新(CSV 优先, XML 兜底; 沿用 init 轮的排除模式) + 轮次递增
    state.class_coverage = jacoco.class_coverage(xml_path, csv_path, state.target_class,
                                                parsed_xml=parsed_all,
                                                excludes=state.coverage_excludes)
    state.iteration += 1
    state.global_iteration += 1
    # 批量模式: 类级轮次同步递增(供升级判定)
    if state.batch_mode:
        state.class_round_used += 1
    state.coverage_history.append(
        {"method": current.label(), "round": state.iteration,  # type: ignore[union-attr]
         "before": round(before_rate, 2), "after": round(after_rate, 2)})
    if not tests_green:
        state.test_history.append(
            {"method": current.label(), "round": state.iteration,  # type: ignore[union-attr]
             "failures": test.failures, "errors": test.errors, "skipped": test.skipped, "failed": test.fail_lines()})
    return entry, before_rate


def main(argv: list[str] | None = None) -> int:
    """脚本入口: 委托 cli.run_cli 统一处理参数解析、异常兜底与协议输出。

    Args:
        argv: 命令行参数(默认取 sys.argv[1:]); 显式传入便于测试。

    Returns:
        进程退出码(取自 Decision.exit_code)。
    """
    return run_cli(SCRIPT_NAME, add_arguments, handler, argv)


if __name__ == "__main__":
    sys.exit(main())
