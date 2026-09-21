#!/usr/bin/env python3
"""第三/五/六步: 为当前方法组装 LLM 编写提示词(build_prompt 阶段)。

工作流位置:
    make_plan 选定 current_method 后调用本脚本。本脚本不写代码、不跑 mvn,
    只把"编写测试所需的全部上下文"组装成提示词, 交回 LLM 在 write_code 阶段落地;
    LLM 保存测试文件后直接进入 validate_rules.py(见 SKILL.md §5)。

职责边界:
    - 提示词分节组装委托 jaut/prompt.PromptBuilder(其内部用 javasrc 静态抽取目标
      方法及其调用的本类 private 方法源码, 宁多勿漏; 并引用 UnitTestRules.md 绝对路径)。
    - 本文件仅做: 读取 state -> 调用 builder -> 输出 write_code 协议块。

输入:
    --workdir 或 --project-root 定位 workdir; 读取 <workdir>/state.json,
    必须已含 current_method(否则按状态错误 exit 3)。

输出(NEXT_STEP):
    type=write_code, instructions=完整提示词; status=success, exit_code=0。
    artifacts 附测试类文件路径(kind=test_file), metrics 附当前轮次(iteration+1)。

依赖: jaut.prompt / jaut.state / jaut.cli / jaut.models。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import _path_setup  # noqa: F401  — 初始化 sys.path 以导入 jaut 包

from jaut import config, prompt  # noqa: E402
from jaut.cli import EmitContext, StepError, add_common_args, require_workdir, run_cli  # noqa: E402
from jaut.logutil import setup_logger  # noqa: E402
from jaut.models import Decision, Route  # noqa: E402
from jaut.state import StateStore  # noqa: E402

SCRIPT_NAME = "build_prompt"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """注册命令行参数: 复用共享的 --workdir / --project-root(二选一即可定位 workdir)。"""
    add_common_args(parser)


def handler(args: argparse.Namespace) -> tuple[Decision, EmitContext]:
    """读取状态、组装提示词并产出 write_code 决策。

    步骤:
        1. 由 --workdir / --project-root 定位 workdir;
        2. 读取 <workdir>/state.json, 校验 state 与 current_method 均存在;
        3. 以 references/UnitTestRules.md 绝对路径构造 PromptBuilder 并组装提示词;
        4. 返回 write_code 决策(instructions 为提示词全文)。

    Args:
        args: 已解析的命令行参数。

    Returns:
        (Decision, EmitContext): write_code 决策与用于路由/emit 的上下文。

    Raises:
        StepError: workdir 缺失, 或 state.json / current_method 缺失(状态错误 exit 3)。
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

        scripts_dir = Path(__file__).resolve().parent
        builder = prompt.PromptBuilder(prompt.default_rules_file(scripts_dir))
        text = builder.build(state, workdir)

        current = state.current_method
        logger.info(f"提示词已组装, 当前方法 {current.name}, 轮次 {state.iteration + 1}")
        decision = Decision(
            status="success", exit_code=config.EXIT_OK,
            summary=f"请按提示词为方法 {current.label()} 编写测试",
            route=Route.WRITE_CODE, reason="LLM 编写单方法测试", instructions=text,
            artifacts=[{"path": state.test_class_file, "kind": "test_file"}],
            metrics={"iteration": state.iteration + 1})
    return decision, EmitContext(workdir=workdir, state=state)


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
