"""git 调用统一封装（P0-2 仓库目录贯通 + P2-4 分支名注入校验）。

所有 git 命令统一走 ``git -C <repo>``，使脚本从任意工作目录调用时
行为一致；分支/引用名在传入 git 前统一校验，阻断以 ``-`` 开头的
选项注入（如 ``--output=/evil`` 写任意文件）。
"""

from __future__ import annotations

import re
import subprocess
import sys
import threading
from pathlib import Path

from .errors import ErrorCode, print_error
from .text import normalize_file_path, normalize_for_matching

# 分支/引用名安全模式：
#   - 不允许以 "-" 开头（防止被解释为 git 选项）
#   - 允许字母数字开头，后续可含 word 字符、/、.、@、-
#   - 长度限制 200
BRANCH_RE = re.compile(r"^(?!-)[A-Za-z0-9][\w./@-]{0,199}$")


def validate_ref(ref: str) -> str:
    """校验分支/引用名合法性，非法时抛出 ValueError。

    额外拒绝包含 ".." 的引用（git 中 a..b 是范围语法，且常用于路径穿越）。
    """
    if not ref or not BRANCH_RE.match(ref) or ".." in ref:
        raise ValueError(f"非法分支/引用名: {ref!r}")
    return ref


def git(repo: Path, *args: str, check: bool = False) -> subprocess.CompletedProcess:
    """在指定仓库目录执行 git 命令。

    Args:
        repo: 仓库根目录（作为 ``git -C`` 参数）
        args: git 子命令及参数
        check: True 时非零退出码抛出 CalledProcessError

    Returns:
        CompletedProcess（stdout/stderr 已按 utf-8 解码）
    """
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
    )


def get_changed_files(repo: Path, branch: str) -> list[str]:
    """获取当前工作区与目标分支之间的变更文件列表（规范化后的相对路径）。

    命令形态：``git -C <repo> diff --name-only --diff-filter=ACMRT <ref> --``
    分支参数位于选项之后、路径分隔符 ``--`` 之前，降低选项注入面。

    Raises:
        ValueError: 分支名非法
        subprocess.CalledProcessError: git 执行失败
    """
    validate_ref(branch)
    r = git(repo, "diff", "--name-only", "--diff-filter=ACMRT", branch, "--", check=True)
    return [normalize_file_path(x.strip()) for x in r.stdout.splitlines() if x.strip()]


def get_added_lines(repo: Path, branch: str, file_path: str) -> str:
    """获取文件在 git diff 中的所有新增行内容（以 + 开头、排除 +++ 文件头）。

    git 执行失败时返回空字符串（调用方按"无法确认新增"保守处理）。
    """
    validate_ref(branch)
    r = git(repo, "diff", branch, "--", file_path)
    return "\n".join(
        line[1:] for line in r.stdout.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )


class AddedLinesCache:
    """按文件缓存新增行内容, 避免对同一文件重复 fork git 进程(P1-6)。

    缓存键经 normalize_for_matching 规范化(反斜杠转正斜杠),
    避免同一文件的相对/反斜杠写法导致缓存失效;
    内部加锁, 支持多线程共享同一实例。
    """

    def __init__(self, repo: Path, branch: str):
        self._repo = repo
        self._branch = branch
        self._cache: dict[str, str] = {}
        self._lock = threading.Lock()

    def get(self, file_path: str) -> str:
        key = normalize_for_matching(file_path)
        with self._lock:
            cached = self._cache.get(key)
        if cached is not None:
            return cached
        # git 在 Windows 上同样接受正斜杠路径, 统一用规范化后的路径调用
        added = get_added_lines(self._repo, self._branch, key)
        with self._lock:
            self._cache[key] = added
        return added


def resolve_repo(cli_repo: str | None) -> Path:
    """解析 --repo 参数为绝对路径，并验证是 git 仓库。"""
    repo = Path(cli_repo or ".").resolve()
    if not repo.is_dir():
        print(f"错误: 仓库目录不存在: {repo}", file=sys.stderr)
        raise SystemExit(2)
    r = git(repo, "rev-parse", "--git-dir")
    if r.returncode != 0:
        # 结构化错误提示（E001），退出码契约保持 2 不变
        print_error(ErrorCode.E001_GIT_NOT_REPO, location=str(repo), detail=r.stderr.strip())
        raise SystemExit(2)
    return repo
