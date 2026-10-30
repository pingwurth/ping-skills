"""git 只读操作封装(Phase 0 阶梯与漂移探测的唯一 git 通道)。

约定:
    - 一切 git 调用只读; 超时 GIT_TIMEOUT_SECONDS 秒。
    - 任何非零退出码或空输出都由调用方按"继续下降"处理, 本层不抛异常 ——
      run() 返回 (ok, stdout); 探测失败是阶梯的正常输入而非错误。
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

from . import config
from .logutil import module_logger


def run(args: list[str], cwd: str | Path) -> tuple[bool, str]:
    """执行 git 子命令, 返回 (退出码是否为 0, stdout 去尾空白)。

    超时/可执行缺失一律视为失败(ok=False), 不抛异常。
    """
    try:
        proc = subprocess.run(
            ["git", *args], cwd=str(cwd), capture_output=True, text=True,
            timeout=config.GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        module_logger().debug(f"git {args[0]} 执行失败({exc}) cwd={cwd}")
        return False, ""
    return proc.returncode == 0, proc.stdout.strip()


def is_git_repo(cwd: str | Path) -> bool:
    ok, _ = run(["rev-parse", "--is-inside-work-tree"], cwd)
    return ok


def repo_root(cwd: str | Path) -> Optional[Path]:
    """仓库顶层目录; 非 git 仓库返回 None。"""
    ok, out = run(["rev-parse", "--show-toplevel"], cwd)
    return Path(out) if ok and out else None


def head_sha(cwd: str | Path) -> Optional[str]:
    ok, out = run(["rev-parse", "HEAD"], cwd)
    return out if ok and out else None


def porcelain_z(cwd: str | Path) -> list[dict]:
    """`git status --porcelain -z` 的结构化解析。

    -z 以 NUL 分隔记录, 可安全处理含空格/引号/非 ASCII 的路径(Windows 引号
    包裹问题); 每条记录 XY<sp>path 或 XY<sp>path<NUL>origpath(重命名)。
    """
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain", "-z", "-uall"],
            cwd=str(cwd), capture_output=True, text=True,
            timeout=config.GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []
    entries: list[dict] = []
    fields = proc.stdout.split("\0")
    i = 0
    while i < len(fields):
        rec = fields[i]
        if not rec:
            i += 1
            continue
        xy, path = rec[:2], rec[3:]
        entry = {"xy": xy, "path": path}
        # 重命名/拷贝记录后跟原始路径
        if xy and xy[0] in "RC" and path and i + 1 < len(fields) and fields[i + 1]:
            entry["orig_path"] = fields[i + 1]
            i += 1
        entries.append(entry)
        i += 1
    return entries


def untracked_files(cwd: str | Path) -> list[str]:
    """porcelain 中的 `??` 路径(git diff 不可见, Phase 0 必须显式累积)。"""
    return [e["path"] for e in porcelain_z(cwd) if e["xy"] == "??"]
