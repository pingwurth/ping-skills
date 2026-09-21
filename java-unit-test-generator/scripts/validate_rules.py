#!/usr/bin/env python3
"""第四步前半: 测试规范机器校验(validate_rules 阶段, 硬规则违规阻断)。

工作流位置:
    LLM 在 write_code 阶段保存测试文件后进入本脚本(SKILL.md §5)。本脚本是 mvn
    执行前的规范闸门: 违规直接打回 write_code 修复, 通过才进入 verify_coverage.py。
    违规修复轮不计入迭代轮次, 但连续 VALIDATE_FAIL_STREAK_LIMIT(5) 轮未通过
    即升级 ask_user(SKILL.md §6, 禁止 write_code <-> validate_rules 无限循环)。

校验规则(来源 references/UnitTestRules.md, 硬规则由 jaut/rules 引擎判定):
    规则 c: 禁止 catch 与裸 try; 不带 catch 的 try-with-resources 放行(异常用 assertThrows)。
    规则 d: git status 对比 state 基线, **/src/main/java/** 不得有基线之外的变更。
    (软约束 a/b/e 不在此机器判定, 仅在提示词层约束。)

职责边界:
    源码净化 -> javasrc.strip_comments_and_strings(行号与原文件一致);
    规则判定 -> rules.run_hard_rules(可扩展注册表); 本文件只做编排与协议输出。

输入:
    --workdir 或 --project-root 定位 workdir; --test-file 可显式指定测试文件(优先于 state)。
    读取 <workdir>/state.json 取 test_class_file / project_root / git_baseline。

输出(NEXT_STEP):
    全部通过 -> run_script verify_coverage.py(success, exit 0);
    有违规 / 测试文件缺失 -> write_code(success, exit 1), instructions 附行号级违规清单
    或创建骨架要求, 第 2 轮起附最近一次 mvn 日志提示;
    连续 5 轮未通过 -> ask_user(exit 1), 附违规清单与用户选项恢复指引。

依赖: jaut.rules / jaut.javasrc / jaut.prompt / jaut.state / jaut.cli / jaut.models。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import _path_setup  # noqa: F401  — 初始化 sys.path 以导入 jaut 包

from jaut import config, javasrc, prompt, rules  # noqa: E402
from jaut.cli import EmitContext, add_common_args, require_workdir, run_cli  # noqa: E402
from jaut.logutil import setup_logger  # noqa: E402
from jaut.models import Decision, ResumeOption, Route, State  # noqa: E402
from jaut.state import StateStore  # noqa: E402

SCRIPT_NAME = "validate_rules"
_WRITE_CODE_REASON = "规范违规, 需修复后重新校验(本轮不计入迭代轮次)"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """注册命令行参数: 共享 --workdir / --project-root, 另加 --test-file 显式指定测试文件。"""
    add_common_args(parser)
    parser.add_argument("--test-file", default=None, help="显式指定测试文件(优先于 state)")


def handler(args: argparse.Namespace) -> tuple[Decision, EmitContext]:
    """定位测试文件、执行硬规则校验并产出决策。

    步骤:
        1. 定位 workdir 并读取 state.json(可能为 None, 允许仅凭 --test-file 校验);
        2. 解析测试文件路径(--test-file 优先, 其次 state.test_class_file);
        3. 测试文件缺失/不存在 -> write_code(要求创建骨架);
        4. 净化源码后运行硬规则引擎(c/d), 汇总违规;
        5. 有违规 -> 计数连续违规轮, 达上限升级 ask_user, 否则 write_code(附违规清单);
        6. 全部通过 -> 清零违规计数, verify_coverage。

    Args:
        args: 已解析的命令行参数。

    Returns:
        (Decision, EmitContext): verify_coverage / write_code / ask_user 决策与路由上下文。

    Raises:
        StepError: workdir 缺失(状态错误 exit 3)。
    """
    workdir = require_workdir(args)
    store = StateStore(workdir)
    with store.locked():
        state = store.load()
        logger = setup_logger(SCRIPT_NAME, workdir)
        rules_file = prompt.default_rules_file(Path(__file__).resolve().parent)

        test_file = args.test_file or (state.test_class_file if state else None)
        project_root = (state.project_root if state else None) or args.project_root

        if not test_file:
            return _write_code("无法定位测试类文件",
                               "state.json 中缺少 test_class_file, 请先运行 make_plan.py",
                               state, workdir)

        test_path = Path(test_file)
        if not test_path.is_file():
            streak = _bump_fail_streak(state, store)
            if streak >= config.VALIDATE_FAIL_STREAK_LIMIT:
                return _escalate_validate(streak, rules_file, workdir, state)
            return _write_code(
                f"测试类不存在: {test_file}",
                f"只能创建 {test_file} 这一个文件(JUnit5 + Mockito 骨架, 包名与被测类一致), "
                f"不得修改任何其他文件。规范见 {rules_file}", state, workdir)

        content = test_path.read_text(encoding="utf-8", errors="replace")
        ctx = rules.RuleContext(
            stripped_source=javasrc.strip_comments_and_strings(content),
            project_root=project_root,
            git_baseline=state.git_baseline if state else {})
        violations = rules.run_hard_rules(ctx)
        logger.info(f"校验 {test_file}: 违规 {len(violations)} 处")

        if violations:
            streak = _bump_fail_streak(state, store)
            if streak >= config.VALIDATE_FAIL_STREAK_LIMIT:
                return _escalate_validate(streak, rules_file, workdir, state,
                                           violations)
            detail = "\n".join(f"- {v.render()}" for v in violations)
            return _write_code(
                f"规范校验未通过: {len(violations)} 处违规(连续第 {streak} 轮)",
                f"只能修改 {test_file} 这一个文件, 请逐条修复以下规范违规后保存:\n"
                f"{detail}\n规范权威文档: {rules_file}", state, workdir)

        if state is not None and state.validate_fail_streak:
            state.validate_fail_streak = 0
            store.save(state)
            logger.info("规范校验通过, 连续违规计数已清零")

        decision = Decision(status="success", exit_code=config.EXIT_OK, summary="规范校验全部通过",
                            route=Route.VERIFY_COVERAGE, reason="进入覆盖率复核")
        return decision, EmitContext(workdir=workdir, state=state)


def _bump_fail_streak(state: State | None, store: StateStore) -> int:
    """连续违规计数 +1 并落盘; 返回更新后的计数(state 为 None 时返回 0 不计数)。

    达上限时复位为 0(升级后全新窗口, 与轨迹复位同语义), 调用方据返回值升级。
    """
    if state is None:
        return 0
    state.validate_fail_streak += 1
    streak = state.validate_fail_streak
    if streak >= config.VALIDATE_FAIL_STREAK_LIMIT:
        state.validate_fail_streak = 0
    store.save(state)
    return streak


def _escalate_validate(streak: int, rules_file: str,
                       workdir: Path, state: State | None,
                       violations: list | None = None) -> tuple[Decision, EmitContext]:
    """连续违规达上限: 升级 ask_user(SKILL.md §6, 规范修复循环同样有预算)。"""
    resume = [
        ResumeOption(option="continue", label="继续修复",
                     script="validate_rules.py", params=[],
                     note="按此前违规清单修复后重新运行本脚本校验"),
        ResumeOption(option="skip_method", label="跳过该方法",
                     script="make_plan.py", params=["--skip-current"]),
        ResumeOption(option="terminate", label="终止"),
    ]
    detail = ""
    if violations:
        detail = "\n最近一轮违规清单:\n" + "\n".join(f"- {v.render()}" for v in violations)
    question = (f"测试代码已连续 {streak} 轮规范校验未通过"
                f"{'(测试类文件未创建)' if violations is None else ''}, "
                "自动修复疑似不收敛, 可能是场景确实需要与硬规则冲突的写法"
                "(如规则 c 禁止 catch 而场景需要资源清理)。"
                f"继续修复 / 跳过该方法 / 终止? 规范权威文档: {rules_file}{detail}")
    decision = Decision(
        status="needs_input", exit_code=config.EXIT_CONTINUE,
        summary=f"规范校验连续 {streak} 轮未通过, 升级给用户决策",
        route=Route.ASK_USER, reason="规范修复循环达上限, 需用户决策",
        question=question, resume=resume)
    return decision, EmitContext(workdir=workdir, state=state)


def _write_code(summary: str, instructions: str, state: State | None, workdir: Path | None = None) -> tuple[Decision, EmitContext]:
    """构造 write_code 决策(违规/缺失修复), 并附带路由上下文。

    第 2 轮起(state.iteration >= 1)在 instructions 末尾追加最近一次 mvn 日志路径提示,
    引导 LLM 先读日志定位问题。违规修复轮不计入迭代轮次(不修改 iteration)。

    Args:
        summary: 面向调用方的一句话结论。
        instructions: 交给 LLM 的修复指引。
        state: 当前状态(可能为 None; 用于判断轮次与回填 mvn 日志路径)。

    Returns:
        (Decision, EmitContext): write_code 决策(success, exit 1)与上下文。
    """
    if state is not None and state.iteration >= 1 and state.mvn_log:
        instructions += f"\n请先阅读最近一次 mvn 执行日志定位问题再修复: {state.mvn_log}"
    decision = Decision(status="success", exit_code=config.EXIT_CONTINUE, summary=summary,
                        route=Route.WRITE_CODE, reason=_WRITE_CODE_REASON, instructions=instructions)
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
