#!/usr/bin/env python3
"""第一步(技能每次执行的第一步): 查找并选择 / 新建 git worktree 目录。

工作流位置:
    技能永远先运行本脚本确认/选择工作树; 后续所有脚本以选定工作树为 <project-root>,
    测试代码、覆盖率报告、state.json 等产物全部落在工作树内, 与用户主工作区隔离
    (SKILL.md §1 "第一步: 工作树确认")。

协议驱动(不阻塞 stdin):
    在传入"当前工作目录"(git 仓库)的上级目录中的 '<目录名>.worktrees' 容器内操作:
        无参数    : 有已有 worktree -> ask_user 列出候选(调用方带 --choice/--new 重跑);
                    无 -> ask_user 确认是否新建(调用方带 --new --force 重跑)
        --list    : 仅列出已存在的 worktree(信息型 finish)
        --choice N: 选择第 N 个已有 worktree(1-based) -> finish
        --new [名]: 新建(省略名称用默认名) -> finish

用法:
    python scripts/select_worktree.py <当前工作目录>
    python scripts/select_worktree.py <当前工作目录> --choice N
    python scripts/select_worktree.py <当前工作目录> --new [名称]

输出(NEXT_STEP):
    选定/新建成功 -> finish(exit 0), 工作树绝对路径见 deliverables 与
    artifacts[].kind == "worktree"; 需用户选择 -> ask_user(exit 1);
    参数非法/git 失败 -> ask_user(exit 2, failed)。

职责边界:
    容器定位/列举/名校验/派生/新建等领域逻辑见 jaut/worktree.py;
    本文件仅做 CLI 编排与协议输出。

依赖: jaut.worktree / jaut.cli / jaut.models / jaut.state。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import _path_setup  # noqa: F401  — 初始化 sys.path 以导入 jaut 包

from jaut import config, worktree as wt  # noqa: E402
from jaut.cli import EmitContext, StepError, run_cli  # noqa: E402
from jaut.logutil import setup_logger  # noqa: E402
from jaut.models import Decision, ResumeOption, Route  # noqa: E402
from jaut.state import default_workdir  # noqa: E402

SCRIPT_NAME = "select_worktree"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """注册命令行参数: 位置参数 WORK_DIR(必填) 与 --list/--choice/--new 三种操作模式。"""
    parser.add_argument("work_dir", metavar="WORK_DIR",
                        help="当前工作目录(git 仓库根目录, 必填, 无默认值)")
    parser.add_argument("--list", action="store_true", help="仅列出已存在的 worktree")
    parser.add_argument("--choice", type=int, default=None, metavar="N",
                        help="选择第 N 个已有 worktree(1-based)")
    parser.add_argument("--new", nargs="?", const=config.WORKTREE_DEFAULT_NEW_SENTINEL,
                        metavar="NAME", help="新建 worktree; 可附目录名, 省略则用默认名")
    parser.add_argument("--base", default=None, metavar="REF",
                        help="新建 worktree 时基于指定的 git ref(分支/标签/commit), 而非当前 HEAD")
    parser.add_argument("--force", action="store_true",
                        help="跳过确认提示(新建确认及未提交变更警告), 强制创建 worktree")


def _ready(path: str, summary: str,
           uncommitted: list[str] | None = None) -> Decision:
    """构造 worktree 就绪的 finish 决策。

    Args:
        path: 选定/新建的工作树绝对路径。
        summary: 结论前缀(如"已选择 worktree")。
        uncommitted: 新建时检测到的未提交变更列表; 非空时追加警告到 summary。

    Returns:
        Decision: finish 路由, 路径同时作为 deliverable 与 artifact(kind=worktree)。
    """
    metrics: dict = {"worktree": path}
    if uncommitted:
        summary += f"; ⚠ 工作区有 {len(uncommitted)} 个未提交变更不会被带入 worktree"
        metrics["uncommitted_changes"] = uncommitted
    return Decision(status="success", exit_code=config.EXIT_OK, summary=f"{summary}: {path}",
                    route=Route.FINISH, reason="worktree 已就绪", deliverables=[path],
                    artifacts=[{"path": path, "kind": "worktree"}], metrics=metrics)


def handler(args: argparse.Namespace) -> tuple[Decision, EmitContext]:
    """校验工作目录、定位容器并分派到具体操作模式。

    Args:
        args: 已解析的命令行参数(WORK_DIR 与 --list/--choice/--new)。

    Returns:
        (Decision, EmitContext): 决策与路由上下文(本脚本无 workdir/state, 用默认 EmitContext)。

    Raises:
        StepError: 当前工作目录不存在, 或 worktree 操作失败(WorktreeError 转 exit 2)。
    """
    repo_root = Path(args.work_dir).resolve()
    if not repo_root.is_dir():
        raise StepError.exec_error(f"当前工作目录不存在: {repo_root}")
    logger = setup_logger(SCRIPT_NAME, default_workdir(repo_root))

    base_dir = wt.parent_dir(args.work_dir)
    dir_name = wt.current_dir_name(args.work_dir)
    repo_cwd = str(repo_root)
    container = wt.resolve_container(base_dir, dir_name)
    logger.info(f"work_dir={repo_cwd}, 容器目录={container}, "
                f"list={args.list}, choice={args.choice}, new={args.new}")

    try:
        decision = _dispatch(args, base_dir, dir_name, container, repo_cwd, logger)
    except wt.WorktreeError as exc:
        raise StepError.exec_error(str(exc)) from exc
    return decision, EmitContext()


def _dispatch(args, base_dir, dir_name, container, repo_cwd, logger) -> Decision:
    """按操作模式分派: --list / --choice / --new / 无参数(请求确认新建或请求选择)。

    Args:
        args: 已解析的命令行参数。
        base_dir: 当前工作目录的上级目录(容器所在处)。
        dir_name: 当前工作目录名(用于派生容器名/默认 worktree 名/分支名)。
        container: '<目录名>.worktrees' 容器的期望绝对路径。
        repo_cwd: git 命令执行的仓库目录。
        logger: 文件日志器。

    Returns:
        Decision: finish(选定/列出) 或 ask_user(需用户选择)。

    Raises:
        StepError: 编号越界或目录名非法(exit 2)。
        wt.WorktreeError: git worktree add 失败(由 handler 转 StepError)。
    """
    has_container = wt.find_container(base_dir, dir_name) is not None



    # --list: 仅报告已存在的 worktree(信息型 finish)
    if args.list:
        worktrees = wt.list_worktrees(container, logger) if has_container else []
        if not worktrees:
            summary = (f"工作树目录 {container} 下没有已存在的 worktree" if has_container
                       else f"上级目录 {base_dir} 下不存在工作树目录 "
                            f"'{dir_name}{config.WORKTREE_CONTAINER_SUFFIX}'")
            return Decision(status="success", exit_code=config.EXIT_OK, summary=summary,
                            route=Route.FINISH, reason="无已存在 worktree", deliverables=[],
                            metrics={"container": container, "worktrees": []})
        summary = f"在 {container} 下找到 {len(worktrees)} 个 worktree 目录"
        return Decision(status="success", exit_code=config.EXIT_OK, summary=summary,
                        route=Route.FINISH, reason="已列出 worktree", deliverables=worktrees,
                        artifacts=[{"path": p, "kind": "worktree"} for p in worktrees],
                        metrics={"container": container, "count": len(worktrees),
                                 "worktrees": worktrees})

    # --choice N: 选择第 N 个已有 worktree
    if args.choice is not None:
        worktrees = wt.list_worktrees(container, logger)
        if not 1 <= args.choice <= len(worktrees):
            raise StepError.exec_error(
                f"编号超出范围: {args.choice}(共 {len(worktrees)} 个已有 worktree)")
        return _ready(worktrees[args.choice - 1], "已选择 worktree")

    # --new [NAME]: 新建 worktree(省略名称用默认名)
    if args.new is not None:
        name = None if args.new == config.WORKTREE_DEFAULT_NEW_SENTINEL else args.new
        if name is not None and not wt.validate_new_name(name, logger):
            raise StepError.exec_error(f"目录名非法: {name}")
        target_name = name or wt.default_worktree_name(dir_name)
        uncommitted = wt.has_uncommitted_changes(repo_cwd, logger)
        # P1-3: 工作区脏时询问用户是否继续
        if uncommitted and not args.force:
            question = (
                f"工作区有 {len(uncommitted)} 个未提交变更, "
                f"新建 worktree 将基于 {args.base or '当前 HEAD'}, "
                f"这些变更不会被带入 worktree。\n"
                f"未提交变更: {', '.join(uncommitted[:5])}")
            if len(uncommitted) > 5:
                question += f" ... 等共 {len(uncommitted)} 个文件"
            question += "\n\n请选择:\n[1] 继续新建 worktree(基于 HEAD)\n[2] 取消操作"
            logger.warning(f"工作区脏, 请求用户确认: {len(uncommitted)} 个未提交变更")
            resume = [
                ResumeOption(option="continue", label="继续新建 worktree(基于 HEAD)",
                             script="select_worktree.py", 
                             params=["--new", target_name, "--force"] + 
                                    (["--base", args.base] if args.base else [])),
                ResumeOption(option="terminate", label="取消操作")
            ]
            return Decision(
                status="needs_input", exit_code=config.EXIT_CONTINUE,
                summary=f"工作区有 {len(uncommitted)} 个未提交变更, 需用户确认",
                route=Route.ASK_USER,
                reason="工作区有未提交变更, 需用户决定是否继续",
                question=question,
                resume=resume,
                metrics={"uncommitted_changes": uncommitted, "target_name": target_name})
        path = wt.create_new_worktree(container, target_name, dir_name, repo_cwd, logger,
                                      base_ref=args.base)
        return _ready(path, "已新建 worktree", uncommitted=uncommitted)

    # 无参数: 有已有 worktree 则请求选择(ask_user), 否则请求用户确认是否新建
    worktrees = wt.list_worktrees(container, logger)
    if not worktrees:
        target_name = wt.default_worktree_name(dir_name)
        # 如果用户已确认(--force), 直接新建
        if args.force:
            logger.info("用户已确认, 直接新建 worktree")
            uncommitted = wt.has_uncommitted_changes(repo_cwd, logger)
            path = wt.create_new_worktree(container, target_name, dir_name, repo_cwd, logger,
                                          base_ref=args.base)
            return _ready(path, "已新建 worktree", uncommitted=uncommitted)
        # 否则先询问用户是否新建
        logger.info("无已存在 worktree, 请求用户确认是否新建")
        uncommitted = wt.has_uncommitted_changes(repo_cwd, logger)
        question_parts = [
            f"在 {container} 下未找到已有 worktree, 是否新建?",
            "",
            f"默认名称: {target_name}",
            f"基于: {args.base or '当前 HEAD'}",
        ]
        if uncommitted:
            question_parts.append("")
            question_parts.append(f"⚠ 工作区有 {len(uncommitted)} 个未提交变更, 这些变更不会被带入 worktree")
            question_parts.append(f"未提交变更: {', '.join(uncommitted[:5])}")
            if len(uncommitted) > 5:
                question_parts[-1] += f" ... 等共 {len(uncommitted)} 个文件"
        question_parts.extend([
            "",
            "请选择:",
            "[1] 新建默认 worktree",
            "[2] 自定义名称新建",
            "[3] 取消操作",
        ])
        question = "\n".join(question_parts)
        resume = [
            ResumeOption(option="continue", label="新建默认 worktree",
                         script="select_worktree.py",
                         params=["--new", target_name, "--force"] +
                                (["--base", args.base] if args.base else [])),
            ResumeOption(option="custom", label="自定义名称新建",
                         script="select_worktree.py",
                         params=["--new", "N", "--force"] +
                                (["--base", args.base] if args.base else []),
                         note="N 替换为用户指定的名称"),
            ResumeOption(option="terminate", label="取消操作")
        ]
        return Decision(
            status="needs_input", exit_code=config.EXIT_CONTINUE,
            summary="未找到已有 worktree, 需用户确认是否新建",
            route=Route.ASK_USER,
            reason="需用户决定是否新建 worktree",
            question=question,
            resume=resume,
            metrics={"container": container, "target_name": target_name,
                     "uncommitted_changes": uncommitted})

    options = "\n".join(f"[{i}] {Path(p).name}" for i, p in enumerate(worktrees, start=1))
    question = (f"工作树目录 {container} 下已有 {len(worktrees)} 个 worktree, 请选择:\n"
                f"{options}\n重新运行并指定 --choice N 选择其一, 或用 --new [名称] 新建。")
    logger.info("存在多个 worktree, 输出 ask_user 请求选择")
    return Decision(status="needs_input", exit_code=config.EXIT_CONTINUE,
                    summary=f"存在 {len(worktrees)} 个已有 worktree, 需用户选择",
                    route=Route.ASK_USER, reason="需用户选择或新建 worktree", question=question,
                    artifacts=[{"path": p, "kind": "worktree"} for p in worktrees],
                    metrics={"container": container, "count": len(worktrees),
                             "worktrees": worktrees})


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
