#!/usr/bin/env python3
"""第一步(技能每次执行的第一步): 查找并选择 / 新建 git worktree 目录。

工作流位置:
    技能永远先运行本脚本确认/选择工作树; 后续所有脚本以选定工作树为 <project-root>,
    测试代码、覆盖率报告、state.json 等产物全部落在工作树内, 与用户主工作区隔离
    (SKILL.md §1 "第一步: 工作树确认")。

协议驱动(不阻塞 stdin):
    在传入"当前工作目录"(git 仓库)的上级目录中的 '<目录名>.worktrees' 容器内操作:
        无参数    : 有已有 worktree -> ask_user 列出候选(选已有/默认新建/清理重建
                    均附 resume 命令可逐字执行, 自定义新建见 question 指引;
                    亦可 --choice/--new 重跑);
                    无 -> ask_user 确认是否新建(调用方带 --new --force 重跑)
        --list    : 仅列出已存在的 worktree; 有 -> 信息型 finish, 无 -> run_script
                    直接给出新建命令(params 以 WORK_DIR 开头, 可逐字执行)
        --choice N: 选择第 N 个已有 worktree(1-based) -> finish
        --new [名]: 新建(省略名称用默认名) -> finish
        --clear-history: 预检(base ref/目标路径/分支检出)通过后清理全部历史
                    worktree(分支保留; **历史树未提交内容永久删除**)再新建默认名 -> finish
    resume 的 params 以位置参数 WORK_DIR(当前工作目录)开头, 调用方可逐字执行。

用法:
    python scripts/select_worktree.py <当前工作目录>
    python scripts/select_worktree.py <当前工作目录> --choice N
    python scripts/select_worktree.py <当前工作目录> --new [名称]
    python scripts/select_worktree.py <当前工作目录> --clear-history

前置条件(仅创建意图模式 --new/--clear-history/无参数执行, 在一切 worktree 操作前
按以下顺序检查, 任一缺失即彻底中断; --list 只读列出, 跳过前置检查;
--choice 跳过前置检查但仍执行下述 worktree 索引复制+同步):
    1. `<cli> mcp list` 的 stdout+stderr 中必须能判定 codegraph 条目状态为
       connected(codegraph MCP 已连接; 多行 JSON/表格/否定态判定见
       _mcp_output_says_connected)。
       <cli> 由脚本第 3 层上级目录决定: 安装布局为
       <project>/.<tool>/skills/<skill>/scripts/本文件, 取 Path(__file__).parents[3]
       (一般为 .opencode/.claude/.qoder/.qwen/.codex)去掉前导 '.', 如 .claude -> claude;
       无法推断(非 '.' 开头目录, 如源码仓内直接运行)同样中止。
    2. 工程根目录(位置参数 WORK_DIR)必须存在 CodeGraph 索引目录 .codegraph。
    缺失时在一切 worktree 操作之前彻底中断任务 -> abort(exit 2, failed), message
    分别提示用户执行 `codegraph install` / `codegraph init` 后重新运行本技能
    (无 resume; 调用方转述 message 后立即终止本技能, 不得重试)。

worktree CodeGraph 索引(--choice 与创建意图模式选定/新建成功返回前执行,
见 _ensure_worktree_codegraph):
    目标 worktree 已有 .codegraph -> 跳过(不重复复制/同步);
    否则复制主工程 .codegraph(剔除 daemon.pid/daemon.sock/daemon.log 与
    *.db-shm/*.db-wal 运行时文件)并在 worktree 根目录执行 `codegraph sync -q`;
    主工程缺 .codegraph -> 提示 `codegraph init`;复制或同步失败 ->
    abort(exit 2, failed), message 提示手动 cp + codegraph sync 后重新调用本技能。

输出(NEXT_STEP):
    选定/新建成功 -> finish(exit 0), 工作树绝对路径见 deliverables 与
    artifacts[].kind == "worktree"; 需用户选择 -> ask_user(exit 1);
    --list 无已存在 worktree -> run_script(exit 1), 下一步为
    select_worktree.py WORK_DIR --new <默认名> --force(新建命令, 可逐字执行);
    参数非法/git 失败 -> ask_user(exit 2, failed);
    缺少 .codegraph 索引/codegraph MCP 未连接/worktree 索引复制或
    codegraph sync 失败 -> abort(exit 2, failed)。

职责边界:
    容器定位/列举/名校验/派生/新建/预检等领域逻辑见 jaut/worktree.py;
    本文件仅做 CLI 编排与协议输出。

依赖: jaut.worktree / jaut.cli / jaut.models / jaut.state。
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
import time
from pathlib import Path

import _path_setup  # noqa: F401  — 初始化 sys.path 以导入 jaut 包

from jaut import config, worktree as wt  # noqa: E402
from jaut.cli import EmitContext, StepError, run_cli  # noqa: E402
from jaut.logutil import setup_logger  # noqa: E402
from jaut.models import Decision, ResumeOption, Route  # noqa: E402
from jaut.proc import run_command  # noqa: E402
from jaut.state import default_workdir  # noqa: E402

SCRIPT_NAME = "select_worktree"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """注册命令行参数: 位置参数 WORK_DIR(必填) 与 --list/--choice/--new/--clear-history 四种操作模式。"""
    parser.add_argument("work_dir", metavar="WORK_DIR",
                        help="当前工作目录(git 仓库根目录, 必填, 无默认值)")
    parser.add_argument("--list", action="store_true", help="仅列出已存在的 worktree")
    parser.add_argument("--choice", type=int, default=None, metavar="N",
                        help="选择第 N 个已有 worktree(1-based)")
    parser.add_argument("--new", nargs="?", const=config.WORKTREE_DEFAULT_NEW_SENTINEL,
                        metavar="NAME", help="新建 worktree; 可附目录名, 省略则用默认名")
    parser.add_argument("--clear-history", action="store_true",
                        help="清理容器内全部历史 worktree(强制, 分支保留; "
                             "历史树未提交内容永久删除)后新建默认名 worktree")
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


def _ready_with_codegraph(path: str, summary: str, *, repo_root: str,
                          logger, uncommitted: list[str] | None = None) -> Decision:
    """确保 worktree 内 .codegraph 就绪(已有则跳过), 再构造 finish 决策。

    Args:
        path: 选定/新建的工作树绝对路径。
        summary: 结论前缀。
        repo_root: 主工程根目录(复制源 .codegraph 的所在)。
        logger: 文件日志器。
        uncommitted: 新建时检测到的未提交变更列表(透传 _ready)。

    Returns:
        Decision: finish 路由(同 _ready)。

    Raises:
        StepError: 主工程缺 .codegraph, 或复制/codegraph sync 失败(abort, exit 2)。
    """
    _ensure_worktree_codegraph(repo_root, path, logger)
    return _ready(path, summary, uncommitted=uncommitted)


def _base_params(args: argparse.Namespace) -> list[str]:
    """--base 附加参数(有值时)。"""
    return ["--base", args.base] if args.base else []


def _resume_params(args: argparse.Namespace, *params: str,
                   with_base: bool = True) -> list[str]:
    """F3: resume params 以位置参数 WORK_DIR 开头, 可逐字执行。

    Args:
        args: 已解析命令行(work_dir 为解析后的仓库根)。
        *params: 增量 flag 参数。
        with_base: 是否在尾部附加 --base(如有)。
    """
    out = [str(args.work_dir), *params]
    if with_base:
        out += _base_params(args)
    return out


def _dirty_fragment(uncommitted: list[str], limit: int = 5) -> str:
    """共享脏文件渲染: 前 limit 个 + 截断说明。"""
    fragment = f"未提交变更: {', '.join(uncommitted[:limit])}"
    if len(uncommitted) > limit:
        fragment += f" ... 等共 {len(uncommitted)} 个文件"
    return fragment


def _dirty_confirm_decision(args, *, uncommitted: list[str], base_desc: str,
                            continue_label: str, question_head: str,
                            summary: str, reason: str, metrics: dict,
                            continue_params: list[str],
                            logger) -> Decision:
    """A/B 共用脏确认块(F7): question + resume continue/terminate。"""
    question = (
        f"{question_head}\n"
        f"新建 worktree 将基于 {base_desc}, "
        f"这些变更不会被带入 worktree。\n"
        f"{_dirty_fragment(uncommitted)}\n"
        f"\n请选择:\n[1] {continue_label}\n[2] 取消操作")
    logger.warning(f"工作区脏, 请求用户确认: {len(uncommitted)} 个未提交变更")
    merged_metrics = {"uncommitted_changes": uncommitted, **metrics}
    resume = [
        ResumeOption(option="continue", label=continue_label,
                     script="select_worktree.py",
                     params=_resume_params(args, *continue_params)),
        ResumeOption(option="terminate", label="取消操作"),
    ]
    return Decision(
        status="needs_input", exit_code=config.EXIT_CONTINUE,
        summary=summary, route=Route.ASK_USER, reason=reason,
        question=question, resume=resume, metrics=merged_metrics)


def _require_codegraph(repo_root: Path, logger) -> None:
    """前置检查: 工程根目录必须存在 CodeGraph 索引目录 .codegraph, 缺失则彻底中断任务。

    创建意图模式在 MCP 检查之后执行本检查(--list/--choice 不创建 worktree, 跳过);
    缺失时在一切 worktree 操作之前抛 StepError.abort,
    输出 failed + abort(exit 2), message 提示执行 `codegraph init`。

    Args:
        repo_root: 工程根目录(位置参数 WORK_DIR 解析后的绝对路径)。
        logger: 文件日志器。

    Raises:
        StepError: .codegraph 目录不存在(exit 2, abort 路由, 无 resume)。
    """
    if (repo_root / ".codegraph").is_dir():
        return
    logger.error(f"工程根目录缺少 .codegraph 索引, 任务中止: {repo_root}")
    question = (
        f"工程根目录 {repo_root} 下不存在 .codegraph 目录(CodeGraph 索引未初始化), "
        f"本任务已中止。\n"
        f"请由用户手动在工程根目录执行以下命令完成索引初始化:\n"
        f"    codegraph init\n"
        f"命令由用户手动执行完毕后, 再由用户重新调用本技能"
        f"(技能不会代为执行该命令, 也不会自动重试)。"
    )
    raise StepError.abort(
        summary=f"工程根目录缺少 .codegraph 索引, 任务已中止: {repo_root}",
        reason="缺少 CodeGraph 索引(.codegraph), 彻底中止任务",
        message=question)


def _ensure_worktree_codegraph(repo_root, worktree_path: str, logger) -> None:
    """选定/新建返回前: 确保 worktree 根目录有可用的 CodeGraph 索引。

    目标 worktree 已有 .codegraph -> 跳过(不重复复制/同步); 否则从主工程
    repo_root 复制(剔除 daemon 运行时文件与 SQLite WAL/SHM 侧文件), 再在
    worktree 根目录执行 `codegraph sync -q` 对齐该树检出的代码。

    Args:
        repo_root: 主工程根目录(复制源, Path 或 str)。
        worktree_path: 目标 worktree 绝对路径。
        logger: 文件日志器。

    Raises:
        StepError: 主工程缺 .codegraph(复用 _require_codegraph 的
            `codegraph init` 指引), 或复制/codegraph sync 失败
            (exit 2, abort 路由, 无 resume; 复制失败先清半成品再中止,
             sync 失败保留已复制的副本)。
    """
    target_cg = Path(worktree_path) / ".codegraph"
    if target_cg.is_dir():
        logger.info(f"worktree 已有 .codegraph, 跳过复制与同步: {target_cg}")
        return
    src_root = Path(repo_root)
    _require_codegraph(src_root, logger)  # 主工程缺索引 -> abort(codegraph init 指引)
    src_cg = src_root / ".codegraph"
    try:
        shutil.copytree(src_cg, target_cg,
                        ignore=shutil.ignore_patterns(
                            "daemon.pid", "daemon.sock", "daemon.log",
                            "*.db-shm", "*.db-wal"))
    except (shutil.Error, OSError) as exc:
        shutil.rmtree(target_cg, ignore_errors=True)  # 清半成品, 重跑不误入跳过分支
        logger.error(f"复制 .codegraph 到 worktree 失败: {target_cg} <- {src_cg}: {exc}")
        question = (
            f"将工程根目录的 .codegraph 复制到 worktree 失败, 本任务已中止。\n"
            f"源: {src_cg}\n目标: {target_cg}\n原因: {exc}\n"
            f"请由用户手动执行以下命令完成索引复制与同步:\n"
            f"    cp -a {src_cg} {target_cg}\n"
            f"    cd {worktree_path} && codegraph sync\n"
            f"命令由用户手动执行完毕后, 再由用户重新调用本技能"
            f"(技能不会代为执行这些命令, 也不会自动重试)。"
        )
        raise StepError.abort(
            summary=f"复制 .codegraph 到 worktree 失败, 任务已中止: {target_cg}",
            reason="复制 CodeGraph 索引(.codegraph)失败, 彻底中止任务",
            message=question) from exc
    result = run_command(["codegraph", "sync", "-q"], cwd=str(worktree_path),
                         timeout=config.CODEGRAPH_SYNC_TIMEOUT_SECONDS)
    if result.launch_failed:
        detail = "`codegraph` 命令不存在或无法执行"
    elif result.timed_out:
        detail = f"`codegraph sync` 执行超时({config.CODEGRAPH_SYNC_TIMEOUT_SECONDS}s)"
    elif not result.ok:
        err = (result.stderr or result.stdout or "").strip()
        detail = (f"`codegraph sync` 失败(退出码 {result.returncode})"
                  + (f": {err}" if err else ""))
    else:
        logger.info(f"codegraph sync 完成: {worktree_path}")
        return
    logger.error(f"worktree 内 codegraph sync 失败: {worktree_path}: {detail}")
    question = (
        f"在 worktree 中执行 `codegraph sync` 失败, 本任务已中止。\n"
        f"worktree: {worktree_path}\n原因: {detail}\n"
        f".codegraph 已复制到该 worktree, 仅索引同步失败。\n"
        f"请由用户手动在该 worktree 目录执行以下命令完成索引同步:\n"
        f"    codegraph sync\n"
        f"命令由用户手动执行完毕后, 再由用户重新调用本技能"
        f"(技能不会代为执行该命令, 也不会自动重试)。"
    )
    raise StepError.abort(
        summary=f"worktree 内 codegraph sync 失败, 任务已中止: {worktree_path}",
        reason="codegraph sync 失败, 彻底中止任务",
        message=question)


# codegraph 条目切段: 从 "codegraph" 到下一个 "codegraph" 之间为一个服务条目
_MCP_NAME = re.compile(r"codegraph", re.I)
# 状态词: [not|dis|un] 可选前缀 + connected; 分隔符允许空白/下划线/连字符;
# lookaround 用 [A-Za-z0-9] 而非 [\w-], 使 codegraph_connected / codegraph_disconnected 可入段
_MCP_STATUS = re.compile(
    r"(?<![A-Za-z0-9])(?:(?:not[\s_\-]+|dis[\s_\-]*|un[\s_\-]*))?connected(?![A-Za-z0-9])",
    re.I)
_MCP_LIST_TIMEOUT_SECONDS = 5
# 无匹配时重试, 规避宿主 CLI 冷启动瞬态(如尚未进入 connected 状态)导致的误中止;
# 连续全部落空才判定未连接(launch_failed/timed_out 不重试, 立即判定)
_MCP_LIST_ATTEMPTS = 3
_MCP_RETRY_DELAY_SECONDS = 1


def _mcp_output_says_connected(output: str) -> bool:
    """由 `<cli> mcp list` 输出判定 codegraph MCP 条目状态是否为 connected。

    判定语义「段内首个状态词定段」: 先折叠全部空白(兼容多行 JSON 与表格),
    再按 "codegraph" 出现位置切段(到下一个 codegraph 或文末), 段内首个状态词
    为独立 connected(不含 not/dis/un 前缀)才判已连接; 任一段命中即 True。

    代表场景:
        "CodeGraph  CONNECTED"                         -> True (表格行)
        "codegraph: http://... - not connected"        -> False(否定短语)
        "codegraph_disconnected"                       -> False(复合词否定)
        多行 JSON {"status": "connected"}               -> True (折叠后可入段)
        codegraph connected 且其他 server disconnected  -> True (他段状态不串扰)
        codegraph disconnected 且其他 server connected  -> False(段内首词定段)
        仅 stderr 命中                                  -> True (调用方拼接 stdout+stderr)
        "codegraph_connected" 复合词                    -> True (对称放行)
    残留边界(fail-closed): "codegraph was disconnected, now connected" 判 False。
    """
    text = re.sub(r"\s+", " ", output or "")
    for seg in re.split(_MCP_NAME, text)[1:]:  # parts[0] 是首个 codegraph 之前, 丢弃
        m = _MCP_STATUS.search(seg)  # 段内首个状态词定段
        if m and m.group(0).lower() == "connected":
            return True
    return False


def _host_dir_name() -> str:
    """脚本第 3 层上级目录名: 安装布局 <project>/.<tool>/skills/<skill>/scripts/本文件 中的 <project>/.<tool>。"""
    try:
        return Path(__file__).resolve().parents[3].name
    except IndexError:
        return ""


def _mcp_cli_for_host_dir(host_dir_name: str) -> str | None:
    """宿主目录名 -> MCP CLI 名: `.claude` -> `claude`, `.opencode` -> `opencode`, ...。

    Args:
        host_dir_name: 第 3 层上级目录名(一般为 .opencode/.claude/.qoder/.qwen/.codex)。

    Returns:
        去掉前导 '.' 的 CLI 名; 非 '.' 开头(如源码仓目录)时返回 None(无法推断)。
    """
    if host_dir_name.startswith(".") and len(host_dir_name) > 1:
        return host_dir_name[1:]
    return None


def _mcp_cli_name() -> str | None:
    """按脚本第 3 层上级目录推断 MCP CLI 名(`.claude` -> `claude`); 推断失败返回 None。"""
    return _mcp_cli_for_host_dir(_host_dir_name())


def _codegraph_mcp_connected() -> tuple[bool, str]:
    """执行 `<cli> mcp list`, 由 stdout+stderr 判定 codegraph MCP 是否已连接。

    <cli> 由 _mcp_cli_name() 从脚本第 3 层上级目录推断(.claude -> claude 等)。
    判定语义见 _mcp_output_says_connected。无匹配时最多尝试 `_MCP_LIST_ATTEMPTS`
    次(间隔 `_MCP_RETRY_DELAY_SECONDS` 秒, 规避宿主 CLI 冷启动瞬态); CLI 无法
    推断/命令不存在(launch_failed)/执行超时(timed_out)不重试, 立即判定。

    Returns:
        (connected, detail): connected=True 表示已连接; detail 为未连接原因(供报错)。
    """
    cli = _mcp_cli_name()
    if cli is None:
        return False, (f"无法由脚本第 3 层上级目录名 {_host_dir_name()!r} 推断 MCP CLI"
                       f"(期望 .opencode/.claude/.qoder/.qwen/.codex 等 '.' 开头目录)")
    detail = ""
    for attempt in range(1, _MCP_LIST_ATTEMPTS + 1):
        result = run_command([cli, "mcp", "list"], timeout=_MCP_LIST_TIMEOUT_SECONDS)
        if result.launch_failed:
            return False, f"{cli} 命令不存在或无法执行"
        if result.timed_out:
            return False, f"{cli} mcp list 执行超时({_MCP_LIST_TIMEOUT_SECONDS}s)"
        if _mcp_output_says_connected(f"{result.stdout or ''}\n{result.stderr or ''}"):
            return True, ""
        # 非零退出码不算硬失败(宿主 CLI 可能带非零退出打印状态表), 仅无匹配才重试
        detail = (f"`{cli} mcp list` 输出无 codegraph connected 匹配"
                  f"(退出码 {result.returncode})")
        if attempt < _MCP_LIST_ATTEMPTS:
            time.sleep(_MCP_RETRY_DELAY_SECONDS)
    return False, f"{detail}; 连续 {_MCP_LIST_ATTEMPTS} 次均未连接"


def _require_codegraph_mcp(logger) -> None:
    """前置检查 1: 宿主 MCP 客户端(opencode/claude/...)的 codegraph MCP 必须已连接, 否则彻底中断任务。

    创建意图模式在 .codegraph 目录检查之前、一切 worktree 操作之前执行
    (--list/--choice 不创建 worktree, 跳过); 未连接时抛 StepError.abort,
    输出 failed + abort(exit 2), message 提示执行 `codegraph install`。

    Args:
        logger: 文件日志器。

    Raises:
        StepError: codegraph MCP 未连接(exit 2, abort 路由, 无 resume)。
    """
    connected, detail = _codegraph_mcp_connected()
    if connected:
        return
    logger.error(f"codegraph MCP 未连接, 任务中止: {detail}")
    question = (
        f"宿主 MCP 客户端({_mcp_cli_name() or '未知 CLI'}) 的 codegraph MCP 未连接, "
        f"本任务已中止。\n"
        f"原因: {detail}\n"
        f"请由用户手动执行以下命令完成 MCP 配置:\n"
        f"    codegraph install\n"
        f"命令由用户手动执行完毕后, 再由用户重新调用本技能"
        f"(技能不会代为执行该命令, 也不会自动重试)。"
    )
    raise StepError.abort(
        summary=f"codegraph MCP 未连接, 任务已中止: {detail}",
        reason="codegraph MCP 未连接(需 codegraph install), 彻底中止任务",
        message=question)


def handler(args: argparse.Namespace) -> tuple[Decision, EmitContext]:
    """校验工作目录与创建意图模式的 CodeGraph 前置条件, 定位容器并分派到具体操作模式。

    Args:
        args: 已解析的命令行参数(WORK_DIR 与 --list/--choice/--new)。

    Returns:
        (Decision, EmitContext): 决策与路由上下文(本脚本无 workdir/state, 用默认 EmitContext)。

    Raises:
        StepError: 当前工作目录不存在, codegraph MCP 未连接,
            工程根目录缺少 .codegraph 索引, 或 worktree 操作失败(WorktreeError 转 exit 2),
            或选定/新建的 worktree 内 .codegraph 复制/codegraph sync 失败(abort, exit 2)。
            CodeGraph 前置检查仅创建意图模式(--new/--clear-history/无参数)执行;
            --list/--choice 不创建 worktree, 跳过前置检查(--choice 仍执行
            worktree 索引复制+同步)。
    """
    repo_root = Path(args.work_dir).resolve()
    if not repo_root.is_dir():
        raise StepError.exec_error(f"当前工作目录不存在: {repo_root}")
    logger = setup_logger(SCRIPT_NAME, default_workdir(repo_root))
    # 前置检查仅创建意图模式执行: --list/--choice 只列出/选择已有 worktree, 不创建, 跳过
    if not (args.list or args.choice is not None):
        _require_codegraph_mcp(logger)   # 前置检查 1: MCP 连接
        _require_codegraph(repo_root, logger)   # 前置检查 2: .codegraph 索引目录

    base_dir = wt.parent_dir(args.work_dir)
    dir_name = wt.current_dir_name(args.work_dir)
    repo_cwd = str(repo_root)
    container = wt.resolve_container(base_dir, dir_name)
    logger.info(f"work_dir={repo_cwd}, 容器目录={container}, "
                f"list={args.list}, choice={args.choice}, new={args.new}, "
                f"clear={args.clear_history}")

    try:
        decision = _dispatch(args, base_dir, dir_name, container, repo_cwd, logger)
    except wt.WorktreeError as exc:
        raise StepError.exec_error(str(exc)) from exc
    return decision, EmitContext()


def _dispatch(args, base_dir, dir_name, container, repo_cwd, logger) -> Decision:
    """按操作模式分派: --list / --choice / --clear-history / --new / 无参数(请求确认新建或请求选择)。

    Args:
        args: 已解析的命令行参数。
        base_dir: 当前工作目录的上级目录(容器所在处)。
        dir_name: 当前工作目录名(用于派生容器名/默认 worktree 名/分支名)。
        container: '<目录名>.worktrees' 容器的期望绝对路径。
        repo_cwd: git 命令执行的仓库目录。
        logger: 文件日志器。

    Returns:
        Decision: finish(选定/列出/清理新建)、ask_user(需用户选择/确认),
            或 run_script(--list 无已存在 worktree, 给出新建命令)。

    Raises:
        StepError: 编号越界或目录名非法(exit 2)。
        wt.WorktreeError: 预检/worktree add/remove 失败(由 handler 转 StepError)。
    """
    has_container = wt.find_container(base_dir, dir_name) is not None

    # --list: 仅报告已存在的 worktree(信息型 finish);
    # 无已存在 worktree -> run_script 直接给出新建命令(下一步可逐字执行)
    if args.list:
        worktrees = wt.list_worktrees(container, logger) if has_container else []
        if not worktrees:
            summary = (f"工作树目录 {container} 下没有已存在的 worktree" if has_container
                       else f"上级目录 {base_dir} 下不存在工作树目录 "
                            f"'{dir_name}{config.WORKTREE_CONTAINER_SUFFIX}'")
            target_name = wt.default_worktree_name(dir_name)
            params = _resume_params(args, "--new", target_name, "--force")
            logger.info(f"无已存在 worktree, 组装新建命令: select_worktree.py {params}")
            return Decision(status="success", exit_code=config.EXIT_CONTINUE,
                            summary=f"{summary}, 已给出新建 worktree 命令",
                            route=Route.SELECT_WORKTREE,
                            reason="无已存在 worktree, 执行新建 worktree 命令",
                            route_params=params,
                            metrics={"container": container, "worktrees": [],
                                     "create_params": params})
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
        return _ready_with_codegraph(worktrees[args.choice - 1], "已选择 worktree",
                                     repo_root=repo_cwd, logger=logger)

    # --clear-history: 预检后强制清理全部历史 worktree(分支保留)后新建默认名
    if args.clear_history:
        target_name = wt.default_worktree_name(dir_name)
        branch = wt.branch_name_for(target_name, dir_name)
        uncommitted = wt.has_uncommitted_changes(repo_cwd, logger)
        # P1-3 镜像 --new: 主工作区脏时询问是否继续(脏历史树由清理强制删除, 不询问)
        if uncommitted and not args.force:
            base_desc = wt.effective_base_description(branch, args.base, repo_cwd)
            question_head = (
                f"工作区有 {len(uncommitted)} 个未提交变更。\n"
                f"⚠ 清理会强制删除全部历史工作树, 其未提交内容将永久删除, 不可恢复。")
            return _dirty_confirm_decision(
                args, uncommitted=uncommitted, base_desc=base_desc,
                continue_label="继续清理历史工作树并新建",
                question_head=question_head,
                summary=f"工作区有 {len(uncommitted)} 个未提交变更, 需用户确认清理并新建",
                reason="工作区有未提交变更, 需用户决定是否清理并新建",
                metrics={}, continue_params=["--clear-history", "--force"],
                logger=logger)
        # F1: 预检通过前绝不 clear
        wt.preflight_new_worktree(container, target_name, dir_name, repo_cwd,
                                  args.base, logger)
        cleared = wt.clear_all_worktrees(container, repo_cwd, logger)
        try:
            path = wt.create_new_worktree(container, target_name, dir_name, repo_cwd, logger,
                                          base_ref=args.base)
        except wt.WorktreeError as exc:
            raise wt.WorktreeError(f"{exc}(历史 worktree 已被清除, 无法回滚)") from exc
        summary = (f"已清理 {len(cleared)} 个历史 worktree 并新建" if cleared
                   else "已新建 worktree(无历史 worktree)")
        logger.info(f"清理完成({len(cleared)} 个), 新建: {path}")
        return _ready_with_codegraph(path, summary, repo_root=repo_cwd,
                                     logger=logger, uncommitted=uncommitted)

    # --new [NAME]: 新建 worktree(省略名称用默认名)
    if args.new is not None:
        name = None if args.new == config.WORKTREE_DEFAULT_NEW_SENTINEL else args.new
        if name is not None and not wt.validate_new_name(name, logger):
            raise StepError.exec_error(f"目录名非法: {name}")
        target_name = name or wt.default_worktree_name(dir_name)
        branch = wt.branch_name_for(target_name, dir_name)
        uncommitted = wt.has_uncommitted_changes(repo_cwd, logger)
        # P1-3: 工作区脏时询问用户是否继续
        if uncommitted and not args.force:
            base_desc = wt.effective_base_description(branch, args.base, repo_cwd)
            continue_label = f"继续新建 worktree({base_desc})"
            return _dirty_confirm_decision(
                args, uncommitted=uncommitted, base_desc=base_desc,
                continue_label=continue_label,
                question_head=f"工作区有 {len(uncommitted)} 个未提交变更。",
                summary=f"工作区有 {len(uncommitted)} 个未提交变更, 需用户确认",
                reason="工作区有未提交变更, 需用户决定是否继续",
                metrics={"target_name": target_name},
                continue_params=["--new", target_name, "--force"],
                logger=logger)
        path = wt.create_new_worktree(container, target_name, dir_name, repo_cwd, logger,
                                      base_ref=args.base)
        return _ready_with_codegraph(path, "已新建 worktree", repo_root=repo_cwd,
                                     logger=logger, uncommitted=uncommitted)

    # 无参数: 有已有 worktree 则请求选择(ask_user), 否则请求用户确认是否新建
    worktrees = wt.list_worktrees(container, logger)
    if not worktrees:
        target_name = wt.default_worktree_name(dir_name)
        branch = wt.branch_name_for(target_name, dir_name)
        # 如果用户已确认(--force), 直接新建
        if args.force:
            logger.info("用户已确认, 直接新建 worktree")
            uncommitted = wt.has_uncommitted_changes(repo_cwd, logger)
            path = wt.create_new_worktree(container, target_name, dir_name, repo_cwd, logger,
                                          base_ref=args.base)
            return _ready_with_codegraph(path, "已新建 worktree", repo_root=repo_cwd,
                                         logger=logger, uncommitted=uncommitted)
        # 否则先询问用户是否新建
        logger.info("无已存在 worktree, 请求用户确认是否新建")
        uncommitted = wt.has_uncommitted_changes(repo_cwd, logger)
        base_desc = wt.effective_base_description(branch, args.base, repo_cwd)
        question_parts = [
            f"在 {container} 下未找到已有 worktree, 是否新建?",
            "",
            f"默认名称: {target_name}",
            f"基于: {base_desc}",
        ]
        if uncommitted:
            question_parts.append("")
            question_parts.append(
                f"⚠ 工作区有 {len(uncommitted)} 个未提交变更, 这些变更不会被带入 worktree")
            question_parts.append(_dirty_fragment(uncommitted))
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
                         params=_resume_params(args, "--new", target_name, "--force")),
            ResumeOption(option="custom", label="自定义名称新建",
                         script="select_worktree.py",
                         params=_resume_params(args, "--new", "N", "--force"),
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

    n = len(worktrees)
    new_label = "新建默认 worktree"
    # F4: 菜单即警示永久删除
    clear_label = "清理历史工作树并创建新的工作树(历史树未提交内容将被永久删除)"
    options = "\n".join(f"[{i}] {Path(p).name}" for i, p in enumerate(worktrees, start=1))
    question = (f"工作树目录 {container} 下已有 {n} 个 worktree, 请选择:\n"
                f"{options}\n"
                f"[{n + 1}] {new_label}\n"
                f"[{n + 2}] {clear_label}\n"
                f"也可运行 --new [名称] 自定义名称新建; 上述选项在 resume 中均有对应命令, 逐字执行。")
    target_name = wt.default_worktree_name(dir_name)
    resume = [ResumeOption(option="choice", label=Path(p).name,
                           script="select_worktree.py",
                           params=_resume_params(args, "--choice", str(i), with_base=False))
              for i, p in enumerate(worktrees, start=1)]
    resume.append(ResumeOption(option="continue", label=new_label,
                               script="select_worktree.py",
                               params=_resume_params(args, "--new", target_name, "--force")))
    resume.append(ResumeOption(option="clear_history", label=clear_label,
                               script="select_worktree.py",
                               params=_resume_params(args, "--clear-history", "--force")))
    logger.info("存在多个 worktree, 输出 ask_user 请求选择")
    return Decision(status="needs_input", exit_code=config.EXIT_CONTINUE,
                    summary=f"存在 {len(worktrees)} 个已有 worktree, 需用户选择",
                    route=Route.ASK_USER, reason="需用户选择或新建 worktree", question=question,
                    resume=resume,
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
