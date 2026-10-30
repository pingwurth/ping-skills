"""统一 CLI 脚手架(单入口 crp.py 的子命令框架)。

main 收敛全部子命令样板: argparse 子命令树、--run-dir 解析、logger 初始化、
异常到 failed 协议块的转换、Decision -> next_step -> emit -> exit_code。
子命令处理器(handlers)只需提供 cmd_<name>(args) -> (Decision, EmitContext)。

错误分两类(对齐 SKILL.md 协议节):
    StepError  : 可预期错误(状态缺失/参数缺失/提交非法), 携带完整 Decision
    Exception  : 未捕获内部错误, 统一转 failed + ask_user(exit 2)
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import config, protocol, transitions
from .logutil import setup_logger
from .models import Decision, Route, State
from .state import StateStore

# handlers 反向依赖本模块(EmitContext/load_state_or_error), 延迟到 main 内导入以断环



@dataclass
class EmitContext:
    """emit 阶段解析 next_step 所需上下文。"""

    run_dir: Optional[Path] = None
    state: Optional[State] = None
    scripts_dir: Path = transitions.SCRIPTS_DIR


class _UsageError(Exception):
    """argparse 用法错误(回修 c038: 必须转协议块, 不得裸 SystemExit)。"""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class _ProtocolParser(argparse.ArgumentParser):
    """error() 不再 SystemExit —— 交给 main 转成 exit-3 协议块。"""

    def error(self, message: str):  # type: ignore[override]
        raise _UsageError(message)


def build_parser() -> argparse.ArgumentParser:
    parser = _ProtocolParser(
        prog=config.ENTRY_FILENAME,
        description="code-review-p 确定性引擎(NEXT_STEP 协议驱动)",
    )
    sub = parser.add_subparsers(dest="cmd")

    p_start = sub.add_parser("start", help="开始一次审查: 解析 Phase 0 范围并创建运行")
    p_start.add_argument("--mode", choices=config.MODES, default=config.DEFAULT_MODE)
    p_start.add_argument("--target", default=None,
                         help="分支 / PR 号 / 提交区间 / 路径 / repo")
    p_start.add_argument("--fix", action="store_true", help="报告后进入 --fix 流程")
    p_start.add_argument("--run-dir", default=None, help="指定运行目录(缺省新建)")
    p_start.add_argument("--force", action="store_true",
                         help="存在未完成运行时不询问, 直接新建")

    p_resume = sub.add_parser("resume", help="路由器: 按状态机派生下一步工作")
    p_resume.add_argument("--run-dir", default=None)
    sub.add_parser("next", help="resume 的别名").add_argument("--run-dir", default=None)

    p_submit = sub.add_parser("submit", help="提交 LLM 工作产物")
    p_submit.add_argument("--kind", required=True,
                          choices=("overlay", "brief", "candidates", "verdicts",
                                   "details", "carrier", "fix-outcomes", "carrier1"))
    p_submit.add_argument("--file", default=None, help="提交文件路径")
    p_submit.add_argument("--transcript-dir", default=None,
                          help="--kind carrier1: Workflow 工具的 transcriptDir")
    p_submit.add_argument("--run-dir", default=None)

    p_answer = sub.add_parser("answer", help="落地用户对 ask_user 的答复")
    p_answer.add_argument("--run-dir", required=True)
    p_answer.add_argument("--key", required=True, choices=config.QUESTION_KEYS)
    p_answer.add_argument("--choice", required=True)
    p_answer.add_argument("--value", default=None, help="choice 需要值时的用户输入")

    p_status = sub.add_parser("status", help="查看运行状态")
    p_status.add_argument("--run-dir", default=None)
    p_status.add_argument("--json", action="store_true")

    p_runs = sub.add_parser("runs", help="列出当前仓库的全部运行")
    p_runs.add_argument("--json", action="store_true")

    p_report = sub.add_parser("report", help="从台账幂等重渲染报告")
    p_report.add_argument("--run-dir", default=None)

    return parser


def _internal_error_decision(exc: Exception) -> Decision:
    return Decision(status="failed", exit_code=config.EXIT_ERROR,
                    summary=f"脚本内部错误: {exc}", route=Route.ASK_USER,
                    reason="内部错误", question=f"脚本内部错误: {exc}")


def main(argv: Optional[list[str]] = None) -> int:
    """入口统一执行框架, 返回进程退出码。"""
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        cmd = getattr(args, "cmd", None)
        if not cmd:
            parser.error("缺少子命令(start/resume/submit/answer/status/runs/report)")
    except _UsageError as err:
        # c038 回归: 用法错误同样恰出一个协议块(exit 3), 调用方可按协议处理
        decision = Decision(status="failed", exit_code=config.EXIT_STATE,
                            summary=f"用法错误: {err.message}",
                            route=Route.ASK_USER, reason="参数不合法",
                            question=f"用法错误: {err.message}\n查看帮助: crp.py --help")
        rc = transitions.RouteContext()
        next_step = transitions.build_next_step(decision, rc)
        payload = protocol.payload_from_decision(config.ENTRY_FILENAME, decision, next_step)
        protocol.emit(payload)
        return decision.exit_code

    from . import handlers  # 延迟导入: handlers 依赖本模块的 EmitContext, 断循环

    handler = getattr(handlers, f"cmd_{cmd}", None)
    if handler is None:
        parser.error(f"未知子命令: {cmd}")

    ectx = EmitContext()
    try:
        decision, ectx = handler(args)
    except handlers.StepError as err:
        decision = err.decision
    except Exception as exc:  # noqa: BLE001 - 兜底: 任何未捕获异常都转协议块
        decision = _internal_error_decision(exc)

    # 尽力初始化 logger(失败不影响协议输出)
    if ectx.run_dir is not None:
        try:
            setup_logger(cmd, ectx.run_dir)
        except OSError:
            pass

    rc = transitions.RouteContext(scripts_dir=ectx.scripts_dir, run_dir=ectx.run_dir,
                                  state=ectx.state)
    try:
        next_step = transitions.build_next_step(decision, rc)
    except Exception as exc:  # noqa: BLE001 - 路由异常兜底为 ask_user
        decision = _internal_error_decision(exc)
        next_step = transitions.build_next_step(decision, rc)

    script_name = f"{config.ENTRY_FILENAME} {cmd}"
    payload = protocol.payload_from_decision(script_name, decision, next_step)
    protocol.emit(payload)
    return decision.exit_code


def load_state_or_error(store: StateStore, what: str) -> tuple[Optional[State], Optional[Decision]]:
    """加载状态; 缺失/损坏时返回状态错误 Decision(exit 3)。供 handlers 复用。"""
    state = store.load()
    if state is None:
        from .state import list_runs
        runs = list_runs(Path.cwd())
        listing = "\n".join(
            f"  - {r['run_id']} [{r.get('mode','?')}/{r.get('phase','?')}]"
            + (" (损坏)" if r.get("corrupt") else "")
            for r in runs[:10]
        ) or "  (无)"
        question = (
            f"{what}: state.json 缺失或损坏。\n"
            f"可续跑运行:\n{listing}\n"
            f"如需新建运行请执行 start; 损坏运行请人工检查后删除其运行目录。"
        )
        return None, Decision(
            status="failed", exit_code=config.EXIT_STATE, summary=f"{what}: 状态缺失",
            route=Route.ASK_USER, reason="状态缺失/损坏", question=question)
    return state, None
