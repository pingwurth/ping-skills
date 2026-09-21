"""git 操作: 变更路径采集、基线记录与主源码路径判定。

基线用于规则 d(禁止修改 src/main/java): init 阶段快照既有变更, validate 阶段
对比当前变更, 基线之外的 src/main/java 改动即违规。git 不可用/非仓库时降级
(返回 None / 空基线), 由调用方决定跳过检查。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional

from . import config
from .proc import resolve_tool, run_command


def is_main_source_path(path: str) -> bool:
    """路径是否落在 **/src/main/java/** 范围内。"""
    segments = path.replace("\\", "/").split("/")
    return any(segments[i:i + 3] == ["src", "main", "java"]
               for i in range(len(segments) - 2))


def git_changed_paths(project_root: str | Path) -> Optional[set[str]]:
    """git 工作区当前变更/未跟踪文件路径集合(统一正斜杠)。

    git 不可用或非仓库返回 None; 忽略删除项与未跟踪目录本身。
    """
    git = resolve_tool("git")
    if git is None:
        return None
    cwd = str(project_root)

    status = run_command(
        [git, "-c", "core.quotepath=false", "status", "--porcelain=v1", "-z"],
        cwd=cwd, capture=True, timeout=config.GIT_COMMAND_TIMEOUT_SECONDS,
    )
    if not status.ok or status.stdout is None:
        return None

    # -z 模式: NUL 分隔, 无引号转义
    # 格式: XY PATH\0XY PATH\0... (重命名: XY ORIG\0 DEST\0)
    entries = status.stdout.split("\0")
    paths: set[str] = set()
    i = 0
    while i < len(entries):
        entry = entries[i]
        if len(entry) < 4:
            i += 1
            continue
        xy, path = entry[:2], entry[3:]
        # 重命名: 下一个条目是新路径
        if " -> " in path:
            path = path.split(" -> ")[-1]
        elif "R" in xy or "C" in xy:
            # -z 模式下重命名/复制: ORIG\0 DEST\0
            i += 1
            if i < len(entries):
                path = entries[i]
        if "D" in xy or (xy == "??" and path.endswith("/")):
            i += 1
            continue                   # 删除项 / 未跟踪目录本身不计
        paths.add(path.replace("\\", "/"))
        i += 1

    others = run_command(
        [git, "-c", "core.quotepath=false", "ls-files", "--others", "--exclude-standard"],
        cwd=cwd, capture=True, timeout=config.GIT_COMMAND_TIMEOUT_SECONDS,
    )
    if others.ok and others.stdout:
        paths.update(
            line.strip().strip('"').replace("\\", "/")
            for line in others.stdout.splitlines() if line.strip()
        )
    return paths


def git_deleted_main_source_paths(project_root: str | Path) -> Optional[set[str]]:
    """获取 **/src/main/java/** 下被删除的文件路径集合(统一正斜杠)。

    用于规则 d 检测: 删除主源码文件同样属于违规行为。
    git 不可用或非仓库返回 None。
    """
    git = resolve_tool("git")
    if git is None:
        return None
    cwd = str(project_root)

    status = run_command(
        [git, "-c", "core.quotepath=false", "status", "--porcelain=v1", "-z"],
        cwd=cwd, capture=True, timeout=config.GIT_COMMAND_TIMEOUT_SECONDS,
    )
    if not status.ok or status.stdout is None:
        return None

    entries = status.stdout.split("\0")
    deleted: set[str] = set()
    i = 0
    while i < len(entries):
        entry = entries[i]
        if len(entry) < 4:
            i += 1
            continue
        xy, path = entry[:2], entry[3:]
        # 重命名: 下一个条目是新路径
        if " -> " in path:
            path = path.split(" -> ")[-1]
        elif "R" in xy or "C" in xy:
            # -z 模式下重命名/复制: ORIG\0 DEST\0
            i += 1
            if i < len(entries):
                path = entries[i]
        # 只收集删除项且路径落在 src/main/java 下
        if "D" in xy and is_main_source_path(path):
            deleted.add(path.replace("\\", "/"))
        i += 1
    return deleted


def file_content_hash(file_path: str | Path) -> Optional[str]:
    """计算文件内容的 SHA-256 哈希值。

    Args:
        file_path: 文件路径(绝对或相对)

    Returns:
        str | None: 文件内容的 SHA-256 哈希值(小写十六进制), 文件不存在或读取失败返回 None
    """
    path = Path(file_path)
    if not path.is_file():
        return None
    try:
        content = path.read_bytes()
        return hashlib.sha256(content).hexdigest()
    except (OSError, IOError):
        return None


def record_git_baseline(project_root: str | Path) -> dict[str, str]:
    """记录基线: 当前 **/src/main/java/** 下的既有变更及其内容哈希(规则 d 豁免依据)。

    Returns:
        dict[str, str]: {相对路径: 文件内容 SHA-256 哈希}
    """
    paths = git_changed_paths(project_root)
    if paths is None:
        return {}
    result: dict[str, str] = {}
    for p in sorted(paths):
        if is_main_source_path(p):
            full_path = Path(project_root) / p
            h = file_content_hash(full_path)
            if h is not None:
                result[p] = h
    return result


def current_branch(project_root: str | Path) -> Optional[str]:
    """工作树当前分支名(git rev-parse --abbrev-ref HEAD)。

    分支名是运行时事实, 不可由目录名推导(已有 worktree 的分支名与目录名无必然关系)。
    detached HEAD(输出 "HEAD")、git 不可用、非仓库一律返回 None, 由调用方降级处理。
    """
    git = resolve_tool("git")
    if git is None:
        return None
    result = run_command([git, "rev-parse", "--abbrev-ref", "HEAD"],
                         cwd=str(project_root), capture=True)
    if not result.ok or result.stdout is None:
        return None
    branch = result.stdout.strip()
    if not branch or branch == "HEAD":
        return None
    return branch
