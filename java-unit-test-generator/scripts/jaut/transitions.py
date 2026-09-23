"""声明式工作流路由(SKILL.md §1 的 DAG 集中表达)。

将 Decision.route 解析为具体 next_step 对象: run_script 路由在此集中解析目标脚本
绝对路径与参数, 消除各入口脚本硬编码 SCRIPTS_DIR/"xxx.py" 的分散。write_code /
finish / ask_user 路由直接由 Decision 携带的 instructions / deliverables / question
组装; ask_user 的 resume(用户选项恢复命令)在此补全脚本绝对路径与 --workdir;
abort(立即终止)路由把 Decision.question 作为 message 输出, 不带 resume。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .models import Decision, NextCommand, Route, State
from .protocol import make_next_step

# scripts/ 目录(jaut/ 的上一级)
SCRIPTS_DIR = Path(__file__).resolve().parent.parent

# run_script 路由 -> 目标脚本文件名
_ROUTE_SCRIPT = {
    Route.MAKE_PLAN: "make_plan.py",
    Route.BUILD_PROMPT: "build_prompt.py",
    Route.VERIFY_COVERAGE: "verify_coverage.py",
    Route.FINAL_CHECK: "init_coverage.py",
    Route.SELECT_WORKTREE: "select_worktree.py",
}


@dataclass
class RouteContext:
    """解析 run_script 路由所需的路径与状态。"""

    scripts_dir: Path = SCRIPTS_DIR
    workdir: Optional[Path] = None
    state: Optional[State] = None


def build_next_step(decision: Decision, rc: RouteContext) -> dict:
    """由 Decision + 路由上下文组装 next_step 对象。"""
    route = decision.route
    if route == Route.WRITE_CODE:
        step = make_next_step("write_code", decision.reason,
                              instructions=decision.instructions or "")
        cmd = decision.on_complete or NextCommand("validate_rules.py")
        step["on_complete"] = _resolve_command(cmd, rc)
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
        # 立即终止: Decision.question 承载须逐字转述给用户的 message(question 空时
        # 依次兜底 summary/reason, 保证 message 非空); 不带 resume —— 不等待用户答复,
        # 调用方转述 message 后即结束本技能
        return make_next_step("abort", decision.reason,
                              message=decision.question or decision.summary or decision.reason)

    # run_script 路由: 决策自带 route_params 时逐字使用(select_worktree 以位置参数
    # WORK_DIR 开头, 不加 --workdir 前缀, 调用方可逐字执行); 否则按路由默认组装
    script = str(rc.scripts_dir / _ROUTE_SCRIPT[route])
    params = list(decision.route_params) or _params_for(route, rc)
    step = make_next_step("run_script", decision.reason, script=script, params=params)
    if decision.instructions:
        step["instructions"] = decision.instructions
    return step


def _resume_entries(decision: Decision, rc: RouteContext) -> list[dict]:
    """把 Decision.resume 解析为 next_step.resume 条目。

    语义级 ResumeOption 只含脚本文件名与增量参数; 此处补全 scripts/ 绝对路径,
    发出方携带 workdir 时前缀 --workdir(select_worktree 的 params 自带位置参数
    WORK_DIR, 不前缀 --workdir 亦不接受该参数),
    调用方可逐字执行。terminate 等无脚本选项只输出 option/label(/note),
    不携带 script/params。
    """
    entries: list[dict] = []
    for opt in decision.resume:
        entry: dict = {"option": opt.option, "label": opt.label}
        if opt.note:
            entry["note"] = opt.note
        if opt.script:
            entry["script"] = str(rc.scripts_dir / opt.script)
            params = list(opt.params)
            if rc.workdir is not None:
                params = ["--workdir", str(rc.workdir)] + params
            entry["params"] = params
        entries.append(entry)
    return entries


def _resolve_command(cmd: NextCommand, rc: RouteContext) -> dict:
    """把语义级 NextCommand 解析为 next_step.on_complete 条目。

    补全 scripts/ 绝对路径, 并统一前缀 --workdir(与 _resume_entries 同逻辑)。
    调用方保存测试文件后逐字执行 script+params, 不得增删参数。
    """
    entry: dict = {"script": str(rc.scripts_dir / cmd.script)}
    params = list(cmd.params)
    if rc.workdir is not None:
        params = ["--workdir", str(rc.workdir)] + params
    entry["params"] = params
    return entry


def _params_for(route: Route, rc: RouteContext) -> list[str]:
    """各 run_script 路由的命令行参数。"""
    # 防御性检查: workdir 不能为空，否则会产生无效的 --workdir "" 参数
    if rc.workdir is None or str(rc.workdir).strip() == "":
        raise ValueError(
            f"_params_for 路由 {route} 需要有效的 workdir，"
            f"但收到 {rc.workdir!r}。请检查调用方是否正确设置了 RouteContext.workdir"
        )
    workdir = str(rc.workdir)
    if route == Route.FINAL_CHECK:
        st = rc.state
        if st is None:
            raise ValueError("FINAL_CHECK 路由需要 state 以回填 --project-root/--class")
        return ["--project-root", st.project_root,
                "--class", st.target_class,
                "--workdir", workdir,
                "--jacoco-version", st.jacoco_version,
                "--final-check"]
    return ["--workdir", workdir]
