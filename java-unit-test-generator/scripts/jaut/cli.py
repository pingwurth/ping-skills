"""统一 CLI 脚手架。

run_cli 收敛所有入口脚本的样板: argparse 构建、--workdir/--project-root 解析、
logger 初始化、异常到 failed 协议块的转换、Decision -> next_step -> emit -> exit_code。
入口脚本只需提供 add_arguments(parser) 与 handler(args) -> (Decision, EmitContext)。

错误分两类(对齐 SKILL.md §2 Exit Code):
    StepError  : 可预期错误(状态缺失/参数缺失/环境缺失/操作失败), 携带完整 Decision
    Exception  : 未捕获内部错误, 统一转 failed + ask_user(exit 2)
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from . import config, protocol, transitions
from .logutil import setup_logger
from .models import Decision, Route, State
from .state import default_workdir


class StepError(Exception):
    """可预期错误; 携带足以直接生成协议块的 Decision。"""

    def __init__(self, decision: Decision) -> None:
        self.decision = decision
        super().__init__(decision.summary)

    # -- 便捷构造 ----------------------------------------------------------- #
    @classmethod
    def state_error(cls, summary: str, question: Optional[str] = None) -> "StepError":
        """状态/协议错误(exit 3): state 缺失、参数缺失等。"""
        return cls(Decision(status="failed", exit_code=config.EXIT_STATE, summary=summary,
                            route=Route.ASK_USER, reason=summary,
                            question=question or summary))

    @classmethod
    def exec_error(cls, summary: str, question: Optional[str] = None,
                   artifacts: Optional[list] = None) -> "StepError":
        """脚本执行错误(exit 2): mvn 失败、环境缺失、报告缺失等。"""
        return cls(Decision(status="failed", exit_code=config.EXIT_ERROR, summary=summary,
                            route=Route.ASK_USER, reason=summary,
                            question=question or summary, artifacts=artifacts or []))


@dataclass
class EmitContext:
    """emit 阶段解析 next_step 所需上下文。"""

    workdir: Optional[Path] = None
    state: Optional[State] = None
    scripts_dir: Path = transitions.SCRIPTS_DIR


# handler 签名: 接收 argparse.Namespace, 返回 (Decision, EmitContext)
Handler = Callable[[argparse.Namespace], tuple[Decision, EmitContext]]
ArgBuilder = Callable[[argparse.ArgumentParser], None]


def add_common_args(parser: argparse.ArgumentParser) -> None:
    """共享参数: --workdir 与 --project-root(二选一即可定位 workdir)。"""
    parser.add_argument("--workdir", default=None)
    parser.add_argument("--project-root", default=None)


def resolve_workdir(args: argparse.Namespace) -> Optional[Path]:
    """由 --workdir 或 --project-root 解析 workdir; 均缺失返回 None。"""
    workdir = getattr(args, "workdir", None)
    if workdir:
        return Path(workdir).resolve()
    project_root = getattr(args, "project_root", None)
    if project_root:
        return default_workdir(project_root)
    return None


def require_workdir(args: argparse.Namespace) -> Path:
    """解析 workdir, 缺失则抛状态错误(exit 3)。"""
    workdir = resolve_workdir(args)
    if workdir is None:
        raise StepError.state_error("缺少 --workdir 或 --project-root",
                                    question="请提供 --workdir 或 --project-root")
    return workdir


def _internal_error_decision(exc: Exception) -> Decision:
    return Decision(status="failed", exit_code=config.EXIT_ERROR,
                    summary=f"脚本内部错误: {exc}", route=Route.ASK_USER,
                    reason="内部错误", question=f"脚本内部错误: {exc}")


def run_cli(script_name: str, add_arguments: ArgBuilder, handler: Handler,
            argv: Optional[list[str]] = None) -> int:
    """入口脚本统一执行框架, 返回进程退出码。"""
    script_file = f"{script_name}.py"
    parser = argparse.ArgumentParser(description=script_name)
    add_arguments(parser)
    args = parser.parse_args(argv)

    ectx = EmitContext()
    try:
        decision, ectx = handler(args)
    except StepError as err:
        decision = err.decision
    except Exception as exc:  # noqa: BLE001 - 兜底: 任何未捕获异常都转协议块
        decision = _internal_error_decision(exc)

    # 尽力初始化 logger(失败不影响协议输出)
    if ectx.workdir is not None:
        try:
            setup_logger(script_name, ectx.workdir)
        except OSError:
            pass

    rc = transitions.RouteContext(scripts_dir=ectx.scripts_dir, workdir=ectx.workdir,
                                  state=ectx.state)
    try:
        next_step = transitions.build_next_step(decision, rc)
    except Exception as exc:  # noqa: BLE001 - 路由异常兜底为 ask_user
        decision = _internal_error_decision(exc)
        next_step = transitions.build_next_step(decision, rc)

    payload = protocol.payload_from_decision(script_file, decision, next_step)
    protocol.emit(payload)
    return decision.exit_code
