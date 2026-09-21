"""git worktree 领域逻辑。

在传入的"当前工作目录"(git 仓库)的上级目录中操作 '<目录名>.worktrees' 容器:
列举已有 worktree、校验/派生名称、通过 `git worktree add -b` 新建。
从旧 select_worktree.py 抽离, 与 CLI/协议解耦, git 调用统一走 proc.run_command。
"""

from __future__ import annotations

import datetime
import logging
import os
from pathlib import Path

from . import config
from .proc import run_command


class WorktreeError(Exception):
    """可预期的操作失败(参数非法/git 失败等), 由入口脚本转成 failed 协议块。"""


def current_dir_name(work_dir: str) -> str:
    """当前工作目录的名称(最后一级目录名)。"""
    return os.path.basename(os.path.abspath(work_dir))


def parent_dir(work_dir: str) -> str:
    """当前工作目录的上级目录。"""
    return os.path.dirname(os.path.abspath(work_dir))


def find_container(base_dir: str, dir_name: str) -> str | None:
    """在 base_dir 下查找名为 '<目录名>.worktrees' 的目录; 非目录/不存在返回 None。"""
    candidate = os.path.join(base_dir, dir_name + config.WORKTREE_CONTAINER_SUFFIX)
    return candidate if os.path.isdir(candidate) else None


def resolve_container(base_dir: str, dir_name: str) -> str:
    """定位容器目录的期望路径(不实际创建, git worktree add 会自动建父目录)。"""
    return find_container(base_dir, dir_name) or os.path.join(
        base_dir, dir_name + config.WORKTREE_CONTAINER_SUFFIX)


def list_worktrees(container: str, logger: logging.Logger) -> list[str]:
    """列出容器目录下所有 worktree 子目录(绝对路径, 按名称排序)。

    容器尚未创建(首次使用)视为正常, 返回空列表。
    """
    found: list[str] = []
    if not os.path.isdir(container):
        return found
    try:
        entries = os.listdir(container)
    except OSError as exc:
        logger.warning(f"无法读取目录 {container}: {exc}")
        return found
    for entry in entries:
        full_path = os.path.join(container, entry)
        if os.path.isdir(full_path):
            found.append(full_path)
    found.sort()
    return found


def validate_new_name(name: str, logger: logging.Logger) -> bool:
    """校验新建 worktree 目录名是否合法(无路径分隔符/非法字符)。"""
    if not name:
        return False
    if os.path.sep in name or name in (".", ".."):
        logger.error("目录名不能包含路径分隔符或为 . / ..")
        return False
    if any(c in config.WORKTREE_NAME_DISALLOWED_CHARS for c in name):
        logger.error(f"目录名包含非法字符 {config.WORKTREE_NAME_DISALLOWED_CHARS}: {name}")
        return False
    return True


def default_worktree_name(dir_name: str) -> str:
    """默认 worktree 目录名: <目录名>.worktree<yyyyMMdd>。"""
    today = datetime.date.today().strftime("%Y%m%d")
    return f"{dir_name}{config.WORKTREE_NAME_SUFFIX}{today}"


def branch_name_for(target_name: str, dir_name: str) -> str:
    """由 worktree 目录名派生 git 分支名: 剔除 '<目录名>.' 前缀。

    例: dir_name='ping-code-review', target='ping-code-review.worktree20260903'
        -> 'worktree20260903'; 不含前缀的名称原样返回。
    """
    prefix = dir_name + "."
    return target_name[len(prefix):] if target_name.startswith(prefix) else target_name


def create_new_worktree(container: str, target_name: str, dir_name: str,
                        repo_cwd: str, logger: logging.Logger,
                        base_ref: str | None = None) -> str:
    """通过 `git worktree add -b <branch> <path>` 新建工作树, 返回绝对路径。

    Args:
        container: 容器目录路径。
        target_name: worktree 目录名。
        dir_name: 当前工作目录名(用于派生分支名)。
        repo_cwd: git 仓库根目录。
        logger: 日志器。
        base_ref: 可选的 git ref(分支/标签/commit), 基于此创建 worktree。
                  默认为 None(使用当前 HEAD)。

    Returns:
        新建 worktree 的绝对路径。

    Raises:
        WorktreeError: 目标目录已存在且非目录, 或 git 命令执行失败。
    """
    target_path = os.path.abspath(os.path.join(container, target_name))
    if os.path.exists(target_path):
        if os.path.isdir(target_path):
            logger.info(f"目标目录已存在, 直接使用: {target_path}")
            return target_path
        raise WorktreeError(f"目标路径已存在但不是目录: {target_path}")

    branch = branch_name_for(target_name, dir_name)
    # P1-3: 支持 --base 参数, 基于指定 ref 创建 worktree
    if base_ref:
        cmd = ["git", "worktree", "add", "-b", branch, target_path, base_ref]
    else:
        cmd = ["git", "worktree", "add", "-b", branch, target_path]
    logger.info(f"新建 worktree: {' '.join(cmd)} (cwd={repo_cwd})")
    result = run_command(cmd, cwd=repo_cwd, capture=True)
    if result.launch_failed:
        raise WorktreeError("未找到 git 命令, 请确认已在 PATH 中且当前位于 git 仓库内")
    if not result.ok:
        err = (result.stderr or result.stdout or "").strip()
        if base_ref and "is not a commit" in (err or ""):
            raise WorktreeError(f"指定的 base ref '{base_ref}' 不存在或无效: {err}")
        raise WorktreeError(f"git worktree add 失败: {err}" if err else "git worktree add 失败")
    return target_path


def has_uncommitted_changes(repo_cwd: str, logger: logging.Logger) -> list[str]:
    """检查工作区是否有未提交的变更(staged / unstaged / untracked)。

    通过 ``git status --porcelain`` 检测: 有输出即表示存在未提交变更。
    返回变更文件路径列表(空列表表示工作区干净); git 不可用或非仓库返回空列表。
    """
    result = run_command(
        ["git", "status", "--porcelain"],
        cwd=repo_cwd, capture=True,
    )
    if not result.ok or not result.stdout:
        return []
    changes = [line[3:] for line in result.stdout.splitlines() if len(line) > 3]
    if changes:
        logger.warning(
            f"工作区有 {len(changes)} 个未提交变更, "
            f"新建 worktree 将基于 HEAD, 这些变更不会被带入")
    return changes


def worktree_abs(path: str) -> Path:
    """规范化 worktree 路径为绝对 Path。"""
    return Path(path).resolve()
