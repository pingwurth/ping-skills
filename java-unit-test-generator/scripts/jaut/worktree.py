"""git worktree 领域逻辑。

在传入的"当前工作目录"(git 仓库)的上级目录中操作 '<目录名>.worktrees' 容器:
列举已有 worktree、校验/派生名称、通过 `git worktree add -b` 新建
(已有分支 + base_ref 时先 `git branch -f` 强制重置该分支再检出)。
调用方须在 clear_all_worktrees 之前先跑 preflight_new_worktree 预检
(base ref / 目标路径 / 分支检出冲突), 预检失败则历史 worktree 保留。
从旧 select_worktree.py 抽离, 与 CLI/协议解耦, git 调用统一走 proc.run_command。
"""

from __future__ import annotations

import datetime
import logging
import os
import shutil
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


def ref_exists(base_ref: str, repo_cwd: str) -> bool:
    """git rev-parse --verify --quiet <ref> 可解析则 True。"""
    return run_command(
        ["git", "rev-parse", "--verify", "--quiet", base_ref],
        cwd=repo_cwd, capture=True).ok


def validate_base_ref(base_ref: str | None, repo_cwd: str,
                      logger: logging.Logger) -> None:
    """base_ref 非空且不可解析时抛 WorktreeError。"""
    if base_ref and not ref_exists(base_ref, repo_cwd):
        logger.error(f"base ref 不存在或无效: {base_ref}")
        raise WorktreeError(f"指定的 base ref '{base_ref}' 不存在或无效")


def branch_checkout_path(branch: str, repo_cwd: str) -> str | None:
    """分支当前检出路径; 未被任何 worktree 检出返回 None。"""
    result = run_command(
        ["git", "for-each-ref", "--format=%(worktreepath)", f"refs/heads/{branch}"],
        cwd=repo_cwd, capture=True)
    if not result.ok:
        return None
    out = (result.stdout or "").strip()
    return out or None


def ensure_branch_checkout_ok(branch: str, target_path: str, repo_cwd: str,
                              logger: logging.Logger,
                              free_paths: set[str] | None = None) -> None:
    """分支已被检出且检出路径将被保留时抛 WorktreeError。

    Args:
        free_paths: 将被 clear 释放的容器路径集合(其中的检出不算冲突);
                    None 表示不允许任何外部检出。
    """
    checkout = branch_checkout_path(branch, repo_cwd)
    if checkout is None:
        return
    allowed = set(free_paths or set())
    allowed.add(os.path.abspath(target_path))
    if os.path.abspath(checkout) in allowed:
        return
    logger.error(f"分支 {branch} 已被检出: {checkout} (目标: {target_path})")
    raise WorktreeError(
        f"分支 '{branch}' 已被检出于 {checkout}, 无法在 {target_path} 创建 worktree")


def effective_base_description(branch: str, base_ref: str | None,
                               repo_cwd: str) -> str:
    """F6 单一事实源: 描述新 worktree 实际会基于什么。"""
    branch_exists = ref_exists(f"refs/heads/{branch}", repo_cwd)
    if branch_exists and base_ref:
        return f"已有分支 {branch} 强制重置到 {base_ref}"
    if branch_exists:
        return f"已有分支 {branch} 的当前 tip"
    return base_ref or "当前 HEAD"


def preflight_new_worktree(container: str, target_name: str, dir_name: str,
                           repo_cwd: str, base_ref: str | None,
                           logger: logging.Logger) -> str:
    """F1 预检(clear 前唯一入口), 返回 target_path。

    1. validate_base_ref(base_ref)
    2. target_path 存在且非目录 -> WorktreeError
    3. ensure_branch_checkout_ok(free_paths=set(list_worktrees(container))):
       容器内检出将被 clear 释放, 不算冲突; 主仓/外部检出算冲突。

    Raises:
        WorktreeError: 预检失败(历史 worktree 应保留, 调用方不得 clear)。
    """
    validate_base_ref(base_ref, repo_cwd, logger)
    target_path = os.path.abspath(os.path.join(container, target_name))
    if os.path.exists(target_path) and not os.path.isdir(target_path):
        raise WorktreeError(f"目标路径已存在但不是目录: {target_path}")
    branch = branch_name_for(target_name, dir_name)
    free_paths = set(list_worktrees(container, logger))
    ensure_branch_checkout_ok(branch, target_path, repo_cwd, logger,
                              free_paths=free_paths)
    return target_path


def _force_reset_free_branch(branch: str, base_ref: str, repo_cwd: str,
                             logger: logging.Logger) -> None:
    """git branch -f <branch> <base_ref>; 失败抛 WorktreeError。"""
    cmd = ["git", "branch", "-f", branch, base_ref]
    logger.info(f"强制重置分支: {' '.join(cmd)} (cwd={repo_cwd})")
    result = run_command(cmd, cwd=repo_cwd, capture=True)
    if result.launch_failed:
        raise WorktreeError("未找到 git 命令, 请确认已在 PATH 中且当前位于 git 仓库内")
    if not result.ok:
        err = (result.stderr or result.stdout or "").strip()
        raise WorktreeError(
            f"git branch -f 失败: {err}" if err else "git branch -f 失败")


def create_new_worktree(container: str, target_name: str, dir_name: str,
                        repo_cwd: str, logger: logging.Logger,
                        base_ref: str | None = None) -> str:
    """通过 `git worktree add` 新建工作树, 返回绝对路径。

    分支矩阵:
        - 分支已存在 + base_ref: 校验 base_ref、确认分支未被外部检出后
          `git branch -f` 强制重置该分支到 base_ref, 再 `worktree add <path> <branch>`
          (落在派生分支上, 非 detached)。
        - 分支已存在 + 无 base_ref: 复用已有分支(旧 tip), `worktree add <path> <branch>`。
        - 分支不存在 + base_ref: `worktree add -b <branch> <path> <base_ref>`。
        - 分支不存在 + 无 base_ref: `worktree add -b <branch> <path>`(当前 HEAD)。

    Args:
        container: 容器目录路径。
        target_name: worktree 目录名。
        dir_name: 当前工作目录名(用于派生分支名)。
        repo_cwd: git 仓库根目录。
        logger: 日志器。
        base_ref: 可选的 git ref(分支/标签/commit)。

    Returns:
        新建 worktree 的绝对路径。

    Raises:
        WorktreeError: 目标目录已存在且非目录, base_ref 无效,
            分支被外部检出, 或 git 命令执行失败。
    """
    target_path = os.path.abspath(os.path.join(container, target_name))
    if os.path.exists(target_path):
        if os.path.isdir(target_path):
            logger.info(f"目标目录已存在, 直接使用: {target_path}")
            return target_path
        raise WorktreeError(f"目标路径已存在但不是目录: {target_path}")

    branch = branch_name_for(target_name, dir_name)
    branch_exists = run_command(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=repo_cwd, capture=True).ok
    if branch_exists and base_ref:
        validate_base_ref(base_ref, repo_cwd, logger)
        ensure_branch_checkout_ok(branch, target_path, repo_cwd, logger)
        _force_reset_free_branch(branch, base_ref, repo_cwd, logger)
        cmd = ["git", "worktree", "add", target_path, branch]
        logger.info(f"分支 {branch} 已强制重置到 {base_ref}, 按该分支创建 worktree")
    elif branch_exists:
        ensure_branch_checkout_ok(branch, target_path, repo_cwd, logger)
        cmd = ["git", "worktree", "add", target_path, branch]
        logger.info(f"分支 {branch} 已存在, 复用该分支创建 worktree")
    elif base_ref:
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


def clear_all_worktrees(container: str, repo_cwd: str,
                        logger: logging.Logger) -> list[str]:
    """强制清理容器下全部历史 worktree, 返回已清理的绝对路径列表(无则 [], 幂等)。

    **调用方须先跑 preflight_new_worktree**; 本函数一旦执行, 历史 worktree
    的未提交内容将被永久删除且不可回滚。
    始终 ``git worktree remove --force``(历史树几乎必脏, 不做脏树询问);
    分支保留不删除; 全部移除后 ``git worktree prune`` 清陈旧元数据;
    容器目录保留(空目录无害, git worktree add 兼容已存在的空父目录)。

    Raises:
        WorktreeError: git 命令不可用, 或 git worktree remove 失败
            (容器内非有效注册的残留目录会 prune 重试后 rmtree 兜底, 不报错)。
    """
    paths = list_worktrees(container, logger)
    if not paths:
        return []
    removed: list[str] = []
    for path in paths:
        cmd = ["git", "worktree", "remove", "--force", path]
        logger.info(f"清理 worktree: {' '.join(cmd)} (cwd={repo_cwd})")
        result = run_command(cmd, cwd=repo_cwd, capture=True)
        if result.launch_failed:
            raise WorktreeError(
                "未找到 git 命令, 请确认已在 PATH 中且当前位于 git 仓库内")
        if result.ok:
            removed.append(path)
            continue
        err = (result.stderr or result.stdout or "").strip()
        not_registered = any(s in err for s in (
            "is not a working tree", "not a valid worktree", "did not match any file"))
        if not_registered:
            # 陈旧注册/野目录: prune 后重试一次, 仍失败则目录兜底删除
            run_command(["git", "worktree", "prune"], cwd=repo_cwd, capture=True)
            retry = run_command(cmd, cwd=repo_cwd, capture=True)
            if retry.ok:
                removed.append(path)
                continue
            logger.warning(f"worktree 非有效注册, 目录兜底删除: {path}")
            shutil.rmtree(path, ignore_errors=True)
            removed.append(path)
            continue
        raise WorktreeError(
            f"git worktree remove 失败: {err}" if err else "git worktree remove 失败")
    prune = run_command(["git", "worktree", "prune"], cwd=repo_cwd, capture=True)
    if not prune.ok and not prune.launch_failed:
        logger.warning("git worktree prune 失败(清理已完成后仅影响陈旧元数据)")
    return removed


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
            f"这些变更不会被带入新建的 worktree")
    return changes


def worktree_abs(path: str) -> Path:
    """规范化 worktree 路径为绝对 Path。"""
    return Path(path).resolve()
