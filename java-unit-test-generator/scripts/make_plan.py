#!/usr/bin/env python3
"""第二步: 读 state.json, 生成/推进单方法测试计划(make_plan 阶段)。

工作流位置:
    init_coverage 未达标后进入本脚本; 每次推进一个"当前最值得补测"的方法,
    是 build_prompt <-> verify_coverage 迭代循环的调度中枢(SKILL.md §1/§5)。
    同时是 ask_user 升级后用户决策的唯一落地通道(SKILL.md §6):
        --skip-current    跳过当前方法(状态写 skipped)
        --set-threshold N 调整门槛(并按新门槛重评估方法状态)
        --grant-rounds N  "继续"= 追加预算窗口(单方法/全局各 +N 轮)

行为:
    - 首次运行(state 无 plan): 计算测试类路径, 生成计划快照, 并在 stdout 打印
      [计划] 摘要(这是 stdout 允许的唯一过程输出)。
    - 每次运行: 未达标(pending)方法按覆盖率升序(并列按 missed 降序)排序,
      取首个写入 current_method 游标; 切换到新方法时清零轮次与陈旧轨迹。

职责边界:
    排序/选择/测试类路径 -> decisions; 推进判定 -> decisions.decide_after_plan;
    路由与参数 -> transitions; 本文件只做状态读写与 stdout 计划摘要。

输入:
    --workdir 或 --project-root 定位 workdir; 读取 <workdir>/state.json
    (必须已由 init_coverage 生成, 否则状态错误 exit 3)。
    用户决策三参数互斥, 一次只落地一个。

输出(NEXT_STEP):
    有未达标方法 -> run_script build_prompt.py(exit 0);
    队列空且已终验 -> finish(exit 0);
    队列空未终验 -> run_script init_coverage.py --final-check(exit 0)。

依赖: jaut.decisions / jaut.state / jaut.cli / jaut.models。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import _path_setup  # noqa: F401  — 初始化 sys.path 以导入 jaut 包

from jaut import config, decisions, report  # noqa: E402
from jaut.cli import EmitContext, add_common_args, require_workdir, run_cli  # noqa: E402
from jaut.cli import StepError  # noqa: E402
from jaut.logutil import setup_logger  # noqa: E402
from jaut.models import Decision, MethodCoverage, MethodStatus, State  # noqa: E402
from jaut.state import StateStore  # noqa: E402

SCRIPT_NAME = "make_plan"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """注册命令行参数: 共享的 --workdir / --project-root 与用户决策三参数(互斥)。"""
    add_common_args(parser)
    group = parser.add_argument_group("用户决策落地(ask_user 升级后的恢复命令, 三选一)")
    group.add_argument("--skip-current", action="store_true",
                       help="跳过当前方法: current_method 状态改 skipped 并推进游标")
    group.add_argument("--set-threshold", type=float, default=None, metavar="N",
                       help="调整覆盖率门槛(0<N≤100): 更新 state.threshold 并按新门槛重评估")
    group.add_argument("--grant-rounds", type=int, default=None, metavar="N",
                       help="追加预算窗口: 单方法与全局预算各 +N 轮, 并复位当前方法轨迹")


def handler(args: argparse.Namespace) -> tuple[Decision, EmitContext]:
    """读取状态、(可选)落地用户决策、推进方法游标并产出计划决策。

    步骤:
        1. 定位 workdir 并读取 state.json(缺失 -> 状态错误 exit 3);
        2. 落地用户决策(--skip-current / --set-threshold / --grant-rounds, 互斥);
        3. 首次运行生成完整计划并打印摘要, 否则直接取排序后的未达标方法;
        4. 推进 current_method 游标(切换方法时清零轮次/轨迹/单方法窗口)并落盘;
        5. 交 decisions.decide_after_plan 产出路由决策。

    Args:
        args: 已解析的命令行参数。

    Returns:
        (Decision, EmitContext): 决策(build_prompt / finish / final_check)与路由上下文。

    Raises:
        StepError: workdir 或 state.json 缺失、用户决策参数非法(状态错误 exit 3)。
    """
    workdir = require_workdir(args)
    store = StateStore(workdir)
    with store.locked():
        state = store.load()
        if state is None:
            raise StepError.state_error(
                f"state.json 不存在: {workdir}(需先运行 init_coverage.py)",
                question="请先运行 init_coverage.py")
        logger = setup_logger(SCRIPT_NAME, workdir)

        _apply_user_decision(args, state, logger)

        if not state.plan:
            failing = _init_plan(state, logger)
        else:
            failing = decisions.sort_failing(state.methods)

        ctx = _advance_cursor(state, failing, store, logger)
        decision = decisions.decide_after_plan(ctx)
    return decision, EmitContext(workdir=workdir, state=state)


def _apply_user_decision(args: argparse.Namespace, state: State, logger) -> None:
    """落地用户在 ask_user 升级后的决策(SKILL.md §6: 跳过/调门槛/继续)。"""
    ops = [bool(args.skip_current), args.set_threshold is not None,
           args.grant_rounds is not None]
    if sum(ops) > 1:
        raise StepError.state_error(
            "--skip-current / --set-threshold / --grant-rounds 互斥, 一次只落地一个用户决策")
    if args.skip_current:
        _skip_current(state, logger)
    elif args.set_threshold is not None:
        _set_threshold(state, args.set_threshold, logger)
    elif args.grant_rounds is not None:
        _grant_rounds(state, args.grant_rounds, logger)


def _skip_current(state: State, logger) -> None:
    """跳过当前方法: 状态改 skipped(SKILL.md §6 "跳过必须写入 state.json")。"""
    if state.current_method is None:
        raise StepError.state_error("无可跳过的当前方法(current_method 为空)")
    entry = state.find_method(state.current_method)
    if entry is None:
        raise StepError.state_error(
            f"state 中找不到当前方法: {state.current_method.label()}")
    entry.status = MethodStatus.SKIPPED
    state.validate_fail_streak = 0
    logger.info(f"用户决策: 跳过方法 {entry.key.label()}")


def _set_threshold(state: State, threshold: float, logger) -> None:
    """调整门槛: 更新 state.threshold 并按新门槛重评估方法状态。"""
    if not 0 < threshold <= 100:
        raise StepError.state_error(f"门槛必须在 (0, 100] 区间: {threshold}")
    state.threshold = threshold
    if state.plan is not None:
        state.plan["threshold"] = threshold
    _reclassify_by_threshold(state, threshold)
    state.validate_fail_streak = 0
    logger.info(f"用户决策: 门槛调整为 {threshold}%")


def _reclassify_by_threshold(state: State, threshold: float) -> None:
    """按新门槛重评估方法状态, 防止调门槛后队列与终验判定脱节。

    - done 且非抽象、覆盖率 < 新门槛 -> 打回 pending 并清轨迹(与终验打回同语义);
    - pending 且覆盖率 >= 新门槛且最近一轮测试绿 -> 晋升 done(双条件不破坏;
      无测试轨迹的方法保持 pending, 交由后续 verify 复核, fail-closed)。
    """
    for m in state.methods:
        if m.is_abstract:
            continue
        if m.status == MethodStatus.DONE and m.rate < threshold:
            m.status = MethodStatus.PENDING
            m.reset_trajectory()
        elif (m.status == MethodStatus.PENDING and m.rate >= threshold
                and m.round_test_results and not m.round_test_results[-1].failed):
            m.status = MethodStatus.DONE


def _grant_rounds(state: State, rounds: int, logger) -> None:
    """"继续": 追加预算窗口并复位当前方法轨迹(升级判定基于全新窗口)。"""
    if rounds < 1:
        raise StepError.state_error(f"--grant-rounds 必须为正整数: {rounds}")
    state.method_round_bonus += rounds
    state.global_round_bonus += rounds
    state.validate_fail_streak = 0
    if state.current_method is not None:
        entry = state.find_method(state.current_method)
        if entry is not None:
            entry.reset_trajectory()
    logger.info(
        f"用户决策: 追加预算窗口 {rounds} 轮"
        f"(单方法有效上限 {config.method_round_budget() + state.method_round_bonus} 轮, "
        f"全局有效上限 {config.global_round_budget() + state.global_round_bonus} 轮)")


def _init_plan(state: State, logger) -> list[MethodCoverage]:
    """首次运行: 计算测试类路径, 生成计划快照并打印摘要。

    副作用: 就地写入 state.test_class_file / test_class_simple / plan;
    并向 stdout 打印 [计划] 摘要(允许的唯一过程输出, 不进入协议块)。

    Args:
        state: 当前状态(会被就地补充计划相关字段)。
        logger: 文件日志器。

    Returns:
        按覆盖率升序排序后的未达标(pending)方法列表。
    """
    project_root = Path(state.project_root)
    module = state.module or "."
    module_dir = project_root if module == "." else project_root / module
    test_file = module_dir / decisions.test_class_relpath(state.target_class)
    state.test_class_file = str(test_file)
    state.test_class_simple = decisions.test_simple_name(state.target_class)

    failing = decisions.sort_failing(state.methods)
    state.plan = {
        "target_class": state.target_class,
        "threshold": state.threshold,
        "test_class_file": str(test_file),
        "methods": [{"name": m.key.name, "desc": m.key.desc,
                     "rate": round(m.rate, 2), "missed": m.missed} for m in failing],
    }
    logger.info(f"生成计划: 测试类 {test_file}, 待测方法 {len(failing)} 个")

    print(f"[计划] 目标类: {state.target_class}  门槛: {state.threshold}%  "
          f"当前类级覆盖率: {state.class_coverage.rate}%")
    print(f"[计划] 测试类: {test_file}")
    print(f"[计划] 待测方法({len(failing)} 个, 覆盖率升序):")
    for m in failing:
        print(f"  - {m.key.label()}  覆盖率 {m.rate:.1f}%  missed {m.missed}")
    return failing


def _advance_cursor(state: State, failing: list[MethodCoverage], store: StateStore,
                    logger) -> decisions.PlanContext:
    """把首个 pending 方法写入 current_method 游标, 并构造计划决策上下文。

    有未达标方法时: 若游标切换到新方法, 清零 iteration 并复位该方法轨迹,
    使升级判定只基于本次入队后的轮次; 随后原子落盘。
    队列为空时: 直接落盘, 由 decide_after_plan 决定终验或收尾。

    Args:
        state: 当前状态(会被就地更新 current_method / iteration)。
        failing: 排序后的未达标方法列表。
        store: 状态存储(用于原子落盘)。
        logger: 文件日志器。

    Returns:
        decisions.PlanContext: 供 decide_after_plan 使用的决策上下文。
    """
    coverage_path = str(store.coverage_path())
    state_path = str(store.path)

    if failing:
        nxt = failing[0]
        prev = state.current_method
        if prev is None or prev != nxt.key:
            # 切换到新方法: 轮次清零 + 清空陈旧轨迹 + 复位单方法预算窗口与规范校验计数,
            # 升级判定只基于本次入队后的轮次
            state.iteration = 0
            state.method_round_bonus = 0
            state.validate_fail_streak = 0
            nxt.reset_trajectory()
        state.current_method = nxt.key
        store.save(state)
        logger.info(f"当前方法: {nxt.key.label()} 覆盖率 {nxt.rate:.1f}%, 剩余 {len(failing)} 个")
        return decisions.PlanContext(
            has_pending=True, final_checked=state.final_checked,
            current_label=nxt.key.label(), current_rate=nxt.rate, remaining=len(failing),
            coverage_path=coverage_path, state_path=state_path)

    store.save(state)
    logger.info("无未达标方法, 进入终验或收尾")
    return decisions.PlanContext(
        has_pending=False, final_checked=state.final_checked,
        coverage_path=coverage_path, state_path=state_path,
        report=report.render_finish_report(state) if state.final_checked else "")


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
