#!/usr/bin/env python3
"""批量认领与委派(batch_next 阶段)。

工作流位置:
    batch_init 完成后 / batch_update 落账后, 由主流程调用本脚本认领下一类
    并输出子代理委派要件。子代理据此从 make_plan.py 开始类内迭代循环。

流程:
    读 batch_state.json(文件锁) → 取首个 pending/recheck → 标记 in_progress
    → 输出 write_code 型委派指令(worktree/FQCN/类workdir/scripts路径/门槛/预算)
    → 无 pending/recheck → run_script batch_finish.py

断点续跑:
    已有 in_progress 类直接再次输出其委派要件; --reset-claim 复位僵死 in_progress。

输入:
    --project-root(必填) [--reset-claim <FQCN>]

输出(NEXT_STEP):
    有待认领类 → ask_user(委派指令, 子代理据此执行);
    无待认领类 → run_script batch_finish.py。
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path

import _path_setup  # noqa: F401

from jaut import config  # noqa: E402
from jaut.cli import EmitContext, StepError, run_cli  # noqa: E402
from jaut.lockutil import batch_lock  # noqa: E402
from jaut.logutil import setup_logger  # noqa: E402
from jaut.models import BatchState, Decision, Route  # noqa: E402
from jaut.state import default_workdir  # noqa: E402

SCRIPT_NAME = "batch_next"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--reset-claim", default=None, metavar="FQCN",
                        help="复位指定类的 in_progress 状态为 pending")
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

        # --reset-claim: 复位僵死 in_progress
        if args.reset_claim:
            entry = batch_state.find_entry(args.reset_claim)
            if entry is None:
                raise StepError.state_error(f"未找到类: {args.reset_claim}")
            if entry.status == "in_progress":
                entry.status = "pending"
                entry.claimed_at = None
                logger.info(f"复位 in_progress -> pending: {args.reset_claim}")
            _save_batch_state(batch_state_path, batch_state)

        # 优先处理已有 in_progress(断点续跑)
        in_progress = None
        for c in batch_state.classes:
            if c.status == "in_progress":
                in_progress = c
                break

        if in_progress is not None:
            entry = in_progress
            logger.info(f"断点续跑: 类 {entry.fqcn} 仍为 in_progress")
        else:
            # 取首个 pending/recheck
            entry = batch_state.next_pending()
            if entry is None:
                # 无待认领类 → run_script batch_finish
                logger.info("无 pending/recheck 类, 进入批量终验")
                decision = Decision(
                    status="success", exit_code=config.EXIT_OK,
                    summary="所有类已处理, 进入批量终验",
                    route=Route.BATCH_FINISH, reason="无待认领类, 批量终验")
                ectx = EmitContext(workdir=batch_workdir,
                                   scripts_dir=Path(__file__).resolve().parent)
                return decision, ectx

            # 标记 in_progress
            entry.status = "in_progress"
            entry.claimed_at = datetime.now(timezone.utc).isoformat()
            entry.attempts += 1
            _save_batch_state(batch_state_path, batch_state)

        # 输出委派要件
        remaining_budget = config.batch_class_round_budget() - entry.rounds_used
        scripts_dir = Path(__file__).resolve().parent

        # 构建 make_plan 命令(大类附带 --method-group)
        make_plan_cmd = (
            f"python {shlex.quote(str(scripts_dir / 'make_plan.py'))}"
            f" --workdir {shlex.quote(entry.workdir)}"
        )
        if entry.has_method_groups:
            group_methods = entry.current_group_methods()
            group_idx = entry.current_group
            group_total = len(entry.method_groups)
            make_plan_cmd += f" --method-group {shlex.quote(','.join(group_methods))}"
            group_info = (
                f"  - 方法组: 第 {group_idx + 1}/{group_total} 组"
                f"({len(group_methods)} 个方法)\n"
                f"  - 已完成组: {len(entry.completed_groups)}/{group_total}\n"
            )
        else:
            group_info = ""

        delegation = (
            f"子代理委派要件(batch-class-writer):\n"
            f"  - worktree(项目根): {project_root}\n"
            f"  - 目标类 FQCN: {entry.fqcn}\n"
            f"  - 类 workdir: {entry.workdir}\n"
            f"  - scripts 目录: {scripts_dir}\n"
            f"  - 门槛: {batch_state.threshold}%\n"
            f"  - 类级剩余预算: {remaining_budget} 轮"
            f"(已用 {entry.rounds_used}/{config.batch_class_round_budget()})\n"
            f"  - 认领序号: {entry.attempts}\n"
            f"{group_info}"
            f"\n"
            f"请以子代理 batch-class-writer 身份, 从以下命令开始:\n"
            f"  {make_plan_cmd}\n"
            f"严格按 NEXT_STEP 协议驱动类内循环。"
        )

        summary = (f"认领类 {entry.fqcn}(第 {entry.attempts} 次尝试, "
                   f"基线 {entry.baseline_rate:.1f}%, 剩余预算 {remaining_budget} 轮)")

        decision = Decision(
            status="needs_input", exit_code=config.EXIT_CONTINUE,
            summary=summary,
            route=Route.ASK_USER, reason="委派子代理执行类内迭代",
            question=delegation)
        return decision, EmitContext(workdir=batch_workdir)


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
