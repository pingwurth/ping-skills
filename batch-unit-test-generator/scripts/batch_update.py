#!/usr/bin/env python3
"""批量交付落账(batch_update 阶段)。

工作流位置:
    子代理完成类内迭代(或升级穿透)后, 由主流程调用本脚本落账。
    读取类的 state.json 判定交付状态, 更新 batch_state.json。

流程:
    定位当前 in_progress 类 → 读其 state.json → 判定:
      - 全部方法 done/skipped 或队列空 → done
      - 升级穿透态(ask_user 未决) → 保持 in_progress, 转述升级问题
    → 落账(final_rate/rounds_used/自动跳过方法数(escalations 字段)/attempts)
    → 有剩余 → run_script batch_next; 无剩余 → run_script batch_finish

输入:
    --project-root(必填)
    [--fqcn <FQCN>] [--skip-class <FQCN> --reason <text>] [--mark-failed <FQCN> --reason <text>]

输出(NEXT_STEP):
    落账成功 → run_script batch_next.py(继续)或 batch_finish.py(无剩余);
    升级穿透 → ask_user(转述升级问题, 用户决策后主流程落地)。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import _path_setup  # noqa: F401

from jaut import config, decisions  # noqa: E402
from jaut.cli import EmitContext, StepError, run_cli  # noqa: E402
from jaut.lockutil import batch_lock  # noqa: E402
from jaut.logutil import setup_logger  # noqa: E402
from jaut.models import BatchState, Decision, MethodStatus, Route  # noqa: E402
from jaut.state import StateStore, default_workdir  # noqa: E402

SCRIPT_NAME = "batch_update"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--fqcn", default=None, help="显式指定类(默认取当前 in_progress)")
    parser.add_argument("--skip-class", default=None, metavar="FQCN",
                        help="跳过指定类(状态写 skipped)")
    parser.add_argument("--reason", default=None, help="跳过/失败原因")
    parser.add_argument("--mark-failed", default=None, metavar="FQCN",
                        help="标记指定类为失败")
    parser.add_argument("--workdir", default=None)


def handler(args: argparse.Namespace) -> tuple[Decision, EmitContext]:
    project_root = Path(args.project_root).resolve()
    batch_workdir = (Path(args.workdir).resolve() if args.workdir
                     else default_workdir(project_root))
    batch_workdir.mkdir(parents=True, exist_ok=True)
    logger = setup_logger(SCRIPT_NAME, batch_workdir)

    batch_state_path = batch_workdir / config.BATCH_STATE_FILENAME
    if not batch_state_path.is_file():
        raise StepError.state_error(
            f"batch_state.json 不存在: {batch_state_path}",
            question="请先运行 batch_init.py")

    with batch_lock(batch_state_path):
        with open(batch_state_path, encoding="utf-8") as f:
            batch_data = json.load(f)
        batch_state = BatchState.from_dict(batch_data)

        # --skip-class 模式
        if args.skip_class:
            entry = batch_state.find_entry(args.skip_class)
            if entry is None:
                raise StepError.state_error(f"未找到类: {args.skip_class}")
            entry.status = "skipped"
            entry.skip_reason = args.reason or "用户跳过"
            _save_batch_state(batch_state_path, batch_state)
            logger.info(f"跳过类: {args.skip_class}({entry.skip_reason})")
            return _continue_or_finish(batch_state, batch_workdir, logger)

        # --mark-failed 模式
        if args.mark_failed:
            entry = batch_state.find_entry(args.mark_failed)
            if entry is None:
                raise StepError.state_error(f"未找到类: {args.mark_failed}")
            entry.status = "failed"
            entry.skip_reason = args.reason or "标记失败"
            _save_batch_state(batch_state_path, batch_state)
            logger.info(f"标记失败: {args.mark_failed}({entry.skip_reason})")
            return _continue_or_finish(batch_state, batch_workdir, logger)

        # 默认模式: 定位当前 in_progress
        fqcn = args.fqcn
        entry = None
        if fqcn:
            entry = batch_state.find_entry(fqcn)
        else:
            for c in batch_state.classes:
                if c.status == "in_progress":
                    entry = c
                    fqcn = c.fqcn
                    break

        if entry is None:
            raise StepError.state_error(
                "未找到 in_progress 类(且未指定 --fqcn)",
                question="请通过 --fqcn 指定类, 或检查 batch_state.json")

        # 读类 state.json 判定交付状态
        class_workdir = Path(entry.workdir)
        class_store = StateStore(class_workdir)
        class_state = class_store.load()

        if class_state is None:
            logger.warning(f"类 {fqcn} 的 state.json 不存在, 标记为 failed")
            entry.status = "failed"
            entry.skip_reason = "state.json 不存在"
        else:
            # 判定是否完成
            pending = class_state.pending_methods()
            all_done = not pending

            if all_done:
                # 方法组模式: 当前组完成; 仅当剩余组仍有可调度方法才推进 ——
                # 类级预算耗尽后剩余组为零工作(复活的方法会被 verify_coverage
                # 预检查立即再跳过), 逐组再委派只是空转子代理, 应直接落账
                if entry.has_method_groups:
                    has_more = (_remaining_groups_have_work(entry, class_state)
                                and entry.advance_group())
                    if has_more:
                        # 还有后续组, 标记为 pending 让 batch_next 再次认领
                        entry.status = "pending"
                        entry.claimed_at = None
                        entry.escalations = _auto_skipped_count(class_state)
                        completed_group = entry.current_group  # advance_group already incremented
                        logger.info(
                            f"类 {fqcn} 方法组 {completed_group}/{len(entry.method_groups)} "
                            f"完成, 推进到下一组")
                        _save_batch_state(batch_state_path, batch_state)
                        # 继续认领(batch_next 会拿到下一组)
                        decision = Decision(
                            status="success", exit_code=config.EXIT_OK,
                            summary=f"类 {fqcn} 方法组完成, 推进到下一组",
                            route=Route.BATCH_NEXT, reason="方法组完成, 继续下一组")
                        return decision, EmitContext(
                            workdir=batch_workdir,
                            scripts_dir=Path(__file__).resolve().parent)
                    else:
                        # 所有组完成(或剩余组已无可调度方法)
                        entry.status = "done"
                        entry.final_rate = class_state.class_coverage.rate
                        entry.rounds_used = class_state.class_round_used
                        entry.escalations = _auto_skipped_count(class_state)
                        skipped_groups = len(entry.method_groups) - entry.current_group - 1
                        if skipped_groups > 0:
                            logger.info(
                                f"类 {fqcn} 剩余 {skipped_groups} 个方法组已无可调度"
                                "方法(类级预算耗尽或方法均已跳过), 不再逐组委派")
                        logger.info(
                            f"类 {fqcn} 所有方法组完成: 覆盖率 {entry.baseline_rate:.1f}% -> "
                            f"{entry.final_rate:.1f}%, 轮次 {entry.rounds_used}")
                else:
                    # 非方法组模式: 直接完成
                    entry.status = "done"
                    entry.final_rate = class_state.class_coverage.rate
                    entry.rounds_used = class_state.class_round_used
                    entry.escalations = _auto_skipped_count(class_state)
                    logger.info(
                        f"类 {fqcn} 完成: 覆盖率 {entry.baseline_rate:.1f}% -> "
                        f"{entry.final_rate:.1f}%, 轮次 {entry.rounds_used}")
            elif class_state.current_method is not None:
                # 异常中断(预算耗尽的正常路径已改为类内自动跳过, 不会落到这里):
                # 通常是 mvn 环境/依赖失败(exit 2)或用户主动中止, 保持 in_progress 交由用户决定
                logger.info(f"类 {fqcn} 仍有 {len(pending)} 个 pending 方法, 保持 in_progress")
                entry.rounds_used = class_state.class_round_used
                _save_batch_state(batch_state_path, batch_state)

                # 转述中断原因(含子代理实际中断线索)
                reason_detail = _infer_escalation_reason(class_state)
                question = (
                    f"类 {fqcn} 的子代理报告类内迭代未完成"
                    f"(剩余 {len(pending)} 个待补方法, 已用 {entry.rounds_used} 轮)。"
                    f"{reason_detail}"
                    "请选择: 继续委派 / 跳过该类 / 终止批量。"
                )
                decision = Decision(
                    status="needs_input", exit_code=config.EXIT_CONTINUE,
                    summary=f"类 {fqcn} 未完成, 需用户决策",
                    route=Route.ASK_USER, reason="子代理未完成, 需用户决策",
                    question=question)
                return decision, EmitContext(workdir=batch_workdir)
            else:
                # 队列空但未终验 → 在批量模式下等同于 done
                entry.status = "done"
                entry.final_rate = class_state.class_coverage.rate
                entry.rounds_used = class_state.class_round_used
                entry.escalations = _auto_skipped_count(class_state)
                logger.info(
                    f"类 {fqcn} 完成(队列空): 覆盖率 {entry.baseline_rate:.1f}% -> "
                    f"{entry.final_rate:.1f}%, 轮次 {entry.rounds_used}")

        _save_batch_state(batch_state_path, batch_state)

        # 统计已完成数(unmet 与 skipped/failed 同为终态, 见 BatchState.all_done)
        done_count = sum(1 for c in batch_state.classes
                         if c.status in ("done", "skipped", "failed", "unmet"))
        total = len(batch_state.classes)
        summary = f"类 {fqcn} 落账完成({entry.status}): 已完成 {done_count}/{total}"
        if entry.final_rate is not None:
            summary += f", before {entry.baseline_rate:.1f}% -> after {entry.final_rate:.1f}%"

        logger.info(summary)
        return _continue_or_finish(batch_state, batch_workdir, logger,
                                    custom_summary=summary)


def _auto_skipped_count(class_state) -> int:
    """类内被自动跳过(带 skip_reason 的终态跳过)的方法数。

    统计口径与 make_plan --unskip 的复活口径互补: 组过滤跳过(无 skip_reason)
    不计入; 自动跳过(预算耗尽/连续失败/无提升/规范违规等)计入。落账时写入
    entry.escalations(历史字段名, 升级穿透移除后语义改为本计数), 供最终报告
    汇总"总自动跳过方法数"。
    """
    return sum(1 for m in class_state.methods
               if m.status == MethodStatus.SKIPPED and m.skip_reason)


def _remaining_groups_have_work(entry: "BatchClassEntry", class_state) -> bool:
    """剩余方法组是否仍有可调度方法(决定是否推进组/继续委派)。

    - 类级预算已耗尽: make_plan --method-group 复活的组内方法会被
      verify_coverage 预检查立即再跳过, 剩余组必为零工作, 不再逐组委派
      空转子代理(--unskip 追加预算后 class_budget_exhausted 自然转假, 可续跑);
    - 其余情况看剩余组是否有 PENDING 或仅组过滤跳过的 SKIPPED
      (skip_reason 为空, make_plan 会复活); 带原因的自动跳过为终态, 不可复活。
    """
    if class_state.class_budget_exhausted:
        return False
    remaining_names = {name
                       for group in entry.method_groups[entry.current_group + 1:]
                       for name in group}
    if not remaining_names:
        return False
    return any(m.key.name in remaining_names
               and (m.status == MethodStatus.PENDING
                    or (m.status == MethodStatus.SKIPPED
                        and m.skip_reason is None))
               for m in class_state.methods)


def _infer_escalation_reason(class_state) -> str:
    """从子代理 state.json 的轨迹中推断类内中断原因(异常中断时的人工决策辅助)。

    判定复用 decisions 的升级原语(test_failure_streak/no_improvement)与
    decide_after_verify 的有效预算口径(基础 + bonus), 避免两处逐行复刻导致
    阈值/口径漂移。按优先级检查:
        1. 类级轮次耗尽(有效类级预算, 含探索模式与追加窗口)
        2. 当前方法迭代轮次耗尽(METHOD_ROUND_BUDGET + method_round_bonus)
        3. 连续测试失败不收敛(decisions.test_failure_streak)
        4. 连续覆盖率无提升(decisions.no_improvement)

    Returns:
        描述中断原因的字符串(含句号前缀), 无法推断时返回空串。
    """
    # 1) 类级轮次耗尽(探索模式预算翻倍, 用有效预算判定与显示)
    budget = class_state.class_round_budget
    if class_state.class_round_used >= budget:
        return (f"原因: 类级迭代已达上限 {budget} 轮"
                f"(已用 {class_state.class_round_used} 轮)。")

    current = class_state.current_method
    if current is None:
        return ""

    method = class_state.find_method(current)
    if method is None:
        return ""

    # 2) 当前方法轮次耗尽(含 --grant-rounds 追加窗口, 与 decide_after_verify 同口径)
    method_budget = config.METHOD_ROUND_BUDGET + class_state.method_round_bonus
    if len(method.round_rates) >= method_budget:
        return (f"原因: 方法 {current.label()} 已迭代 {len(method.round_rates)} 轮, "
                f"达单方法上限 {method_budget} 轮"
                f"(当前覆盖率 {method.rate:.1f}%, 门槛 {class_state.threshold}%)。")

    # 3) 连续测试失败不收敛(复用 decisions.test_failure_streak)
    if decisions.test_failure_streak(method.round_test_results):
        limit = config.TEST_FAIL_STREAK_ROUNDS
        tail = method.round_test_results[-limit:]
        trajectory = [(r.failures, r.errors) for r in tail]
        return (f"原因: 方法 {current.label()} 连续 {limit} 轮测试失败"
                f"(每轮 Failures/Errors: {trajectory})。")

    # 4) 连续覆盖率无提升(复用 decisions.no_improvement)
    if decisions.no_improvement(method.round_rates, threshold=class_state.threshold):
        limit = config.NO_IMPROVEMENT_ROUNDS
        tail_rates = method.round_rates[-limit:]
        return (f"原因: 方法 {current.label()} 连续 {limit} 轮覆盖率无提升"
                f"(最近轨迹: {[round(r, 1) for r in tail_rates]}, 当前 {method.rate:.1f}%)。")

    return ""


def _continue_or_finish(batch_state: BatchState, batch_workdir: Path,
                        logger, custom_summary: str = "") -> tuple[Decision, EmitContext]:
    """有剩余 → run_script batch_next; 无剩余 → run_script batch_finish。"""
    if batch_state.all_done():
        logger.info("所有类已处理完毕, 进入批量终验")
        decision = Decision(
            status="success", exit_code=config.EXIT_OK,
            summary=custom_summary or "所有类已处理, 进入批量终验",
            route=Route.BATCH_FINISH, reason="无待认领类, 批量终验")
    else:
        logger.info("有剩余类, 继续认领")
        decision = Decision(
            status="success", exit_code=config.EXIT_OK,
            summary=custom_summary or "落账完成, 继续认领下一类",
            route=Route.BATCH_NEXT, reason="继续认领下一类")
    return decision, EmitContext(workdir=batch_workdir,
                                 scripts_dir=Path(__file__).resolve().parent)


def _save_batch_state(path: Path, batch_state: BatchState) -> None:
    """原子写入 batch_state.json。"""
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(batch_state.to_dict(), f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def main(argv: list[str] | None = None) -> int:
    return run_cli(SCRIPT_NAME, add_arguments, handler, argv)


if __name__ == "__main__":
    sys.exit(main())
