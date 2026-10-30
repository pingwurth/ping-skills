"""声明式工作流路由: 将 Decision.route 解析为具体 next_step 对象。

单入口世界里的路由目标恒为 crp.py + 子命令:
    - RESUME     -> run_script {script: <scripts>/crp.py, params: ["resume","--run-dir",D]}
    - DO_WORK    -> {type: do_work, instructions, context, output, shape, submit}
    - FINISH     -> deliverables + report(引擎渲染, 逐字透传)
    - ASK_USER   -> question + resume(选项 params 已含 --run-dir/--key/--choice,
                    此处只解析 script 绝对路径, 不再自动前缀参数)
    - ABORT      -> message(逐字转述后立即终止, 无 resume)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import config
from .models import Decision, NextCommand, ResumeOption, Route, State
from .protocol import make_next_step

# scripts/ 目录(crp/ 的上一级)
SCRIPTS_DIR = Path(__file__).resolve().parent.parent

# run_script 路由的目标入口(单入口: 恒为 crp.py)
_ENTRY_PATH = SCRIPTS_DIR / config.ENTRY_FILENAME


@dataclass
class RouteContext:
    """解析路由所需的路径与状态。"""

    scripts_dir: Path = SCRIPTS_DIR
    run_dir: Optional[Path] = None
    state: Optional[State] = None


def entry_path(rc: RouteContext) -> Path:
    return rc.scripts_dir / config.ENTRY_FILENAME


def build_next_step(decision: Decision, rc: RouteContext) -> dict:
    """由 Decision + 路由上下文组装 next_step 对象。"""
    route = decision.route

    if route == Route.DO_WORK:
        step = make_next_step("do_work", decision.reason,
                              instructions=decision.instructions or "")
        if decision.context:
            step["context"] = decision.context
        if decision.output:
            step["output"] = decision.output
        if decision.shape:
            step["shape"] = decision.shape
        cmd = decision.submit or NextCommand(config.ENTRY_FILENAME, [])
        step["submit"] = _resolve_command(cmd, rc)
        return step

    if route == Route.FINISH:
        step = make_next_step("finish", decision.reason, deliverables=decision.deliverables)
        if decision.report:
            step["report"] = decision.report
        return step

    if route == Route.ASK_USER:
        step = make_next_step("ask_user", decision.reason, question=decision.question or "")
        resume = _resume_entries(decision, rc)
        if resume:
            step["resume"] = resume
        return step

    if route == Route.ABORT:
        # 立即终止: Decision.question 承载须逐字转述给用户的 message(空时依次兜底
        # summary/reason); 不带 resume —— 不等待用户答复
        return make_next_step("abort", decision.reason,
                              message=decision.question or decision.summary or decision.reason)

    # run_script 路由(RESUME): 决策自带 route_params 时逐字使用; 否则默认组装
    if rc.run_dir is None or str(rc.run_dir).strip() == "":
        raise ValueError(
            f"路由 {route} 需要有效的 run_dir, 但收到 {rc.run_dir!r}; "
            f"请检查调用方是否正确设置了 RouteContext.run_dir"
        )
    params = list(decision.route_params) or ["resume", "--run-dir", str(rc.run_dir)]
    step = make_next_step("run_script", decision.reason,
                          script=str(entry_path(rc)), params=params)
    if decision.instructions:
        step["instructions"] = decision.instructions
    return step


def _resume_entries(decision: Decision, rc: RouteContext) -> list[dict]:
    """把 Decision.resume 解析为 next_step.resume 条目。

    语义级 ResumeOption 只含入口文件名与完整增量参数; 此处补全 scripts/ 绝对
    路径。terminate 等无脚本选项只输出 option/label(/note), 不携带 script/params。
    """
    entries: list[dict] = []
    for opt in decision.resume:
        entry: dict = {"option": opt.option, "label": opt.label}
        if opt.note:
            entry["note"] = opt.note
        if opt.script:
            entry["script"] = str(rc.scripts_dir / opt.script)
            entry["params"] = list(opt.params)
        entries.append(entry)
    return entries


def _resolve_command(cmd: NextCommand, rc: RouteContext) -> dict:
    """把语义级 NextCommand 解析为 next_step.submit 条目(补全入口绝对路径)。

    调用方写完 output 文件后逐字执行 script+params, 不得增删参数。
    """
    return {
        "script": str(rc.scripts_dir / cmd.script),
        "params": list(cmd.params),
    }
