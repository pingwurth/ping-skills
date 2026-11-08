"""git 调用统一封装（P0-2 仓库目录贯通 + P2-4 分支名注入校验）。

所有 git 命令统一走 ``git -C <repo>``，使脚本从任意工作目录调用时
行为一致；分支/引用名在传入 git 前统一校验，阻断以 ``-`` 开头的
选项注入（如 ``--output=/evil`` 写任意文件）。

F43/F05 加固:
  - 所有 git 调用带挂起防护（GIT_TIMEOUT_SECONDS 秒超时，超时抛
    subprocess.TimeoutExpired，由调用方按执行错误/退出码 2 处理）
  - 统一附加 ``-c core.quotepath=false``，非 ASCII 路径原样输出，
    不做 C 风格引号转义；文件清单类解析另用 ``-z`` NUL 分隔形态
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

# git 挂起防护超时（秒）: 与 pwiki util.py / crp gitops 的 git 包装器同量级。
# 凭据或 ssh 提示、NFS 挂起、陈旧的 index.lock 等场景下，超时按执行错误
# （退出码 2）处理而非无限阻塞 CI（F43）。
GIT_TIMEOUT_SECONDS = 30


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

    Raises:
        subprocess.TimeoutExpired: 命令超过 GIT_TIMEOUT_SECONDS 秒未结束（F43），
            调用方应按执行错误（退出码 2）处理
    """
    return subprocess.run(
        # core.quotepath=false: 非 ASCII(如中文)路径原样输出，杜绝 C 引号
        # 转义形态到达下游路径匹配（F05）
        ["git", "-C", str(repo), "-c", "core.quotepath=false", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
        timeout=GIT_TIMEOUT_SECONDS,
    )


def strip_c_quotes(path: str) -> str:
    """剥去 git quotePath C 引号形态路径的首尾双引号（F05 兜底）。

    ``-z`` / ``core.quotepath=false`` 下不应再出现引号包裹的路径，
    保留此兜底以兼容其他来源（如历史产物文件）中的转义路径。
    """
    if len(path) >= 2 and path.startswith('"') and path.endswith('"'):
        return path[1:-1]
    return path


def get_changed_files(repo: Path, branch: str) -> list[str]:
    """获取当前工作区与目标分支之间的变更文件列表（规范化后的相对路径）。

    命令形态：``git -C <repo> diff -z --name-only --diff-filter=ACMRT <ref> --``
    分支参数位于选项之后、路径分隔符 ``--`` 之前，降低选项注入面。

    ``-z`` 使路径以 NUL 分隔**原样**输出：非 ASCII 路径不再被 C 引号
    转义（``"src/...\\344\\270\\255...java"``），中文命名的 Java 文件
    不再因归一化后路径失配被下游门禁静默剔除（F05）。

    Raises:
        ValueError: 分支名非法
        subprocess.CalledProcessError: git 执行失败
        subprocess.TimeoutExpired: git 执行超时
    """
    validate_ref(branch)
    r = git(repo, "diff", "-z", "--name-only", "--diff-filter=ACMRT", branch, "--", check=True)
    return [normalize_file_path(strip_c_quotes(x.strip())) for x in r.stdout.split("\0") if x.strip()]


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
    """按文件缓存新增行内容（F33 进程内批量化）。

    构造时用**一次**树级 ``git diff -U0 <branch> --``，按 ``+++ b/<path>``
    文件头切出 per-file 新增行映射；``get`` 走进程内查表。取代旧版对每个
    文件 fork 一次 ``git diff <branch> -- <file>`` 的做法（200 文件分支
    从 ~200 次顺序 fork 降为 1 次；``get`` 返回形状与语义不变：各文件
    新增行以 ``\\n`` 连接，未知文件返回空字符串）。

    键经 normalize_for_matching 规范化（反斜杠转正斜杠）；绝对路径自动
    剥去仓库根前缀后二次查找，对齐旧版 ``git diff -- <绝对路径>`` 的
    接受语义。git 执行失败时映射为空（调用方按"无法确认新增"保守处理），
    执行超时抛 subprocess.TimeoutExpired（执行错误，退出码 2）。
    """

    def __init__(self, repo: Path, branch: str):
        validate_ref(branch)
        self._repo = repo
        self._branch = branch
        self._added_by_file = self._load_tree_diff(repo, branch)
        self._lock = threading.Lock()  # 映射构造后只读；保留锁兼容既有共享用法

    @staticmethod
    def _load_tree_diff(repo: Path, branch: str) -> dict[str, str]:
        """一次树级 diff 构建路径 → 新增行文本（"\n" 连接）映射。"""
        r = git(repo, "diff", "-U0", branch, "--")
        buckets: dict[str, list[str]] = {}
        current: list[str] | None = None
        for line in r.stdout.splitlines():
            if line.startswith("+++ "):
                header = line[4:].strip()
                # 新增/修改文件头为 b/<path>；删除文件为 /dev/null（无新增行）
                if header.startswith("b/"):
                    key = normalize_for_matching(strip_c_quotes(header[2:]))
                    current = buckets.setdefault(key, [])
                else:
                    current = None
                continue
            if current is not None and line.startswith("+"):
                # 新增行（"+++" 文件头已被上一分支消费；-U0 无上下文行）
                current.append(line[1:])
        return {key: "\n".join(lines) for key, lines in buckets.items()}

    def _lookup(self, key: str) -> str | None:
        with self._lock:
            return self._added_by_file.get(key)

    def get(self, file_path: str) -> str:
        """查表返回文件的新增行文本；未知文件返回空字符串。"""
        key = normalize_for_matching(file_path)
        added = self._lookup(key)
        if added is None:
            repo_prefix = normalize_for_matching(str(self._repo)).rstrip("/") + "/"
            if key.startswith(repo_prefix):
                added = self._lookup(key[len(repo_prefix):])
        return added if added is not None else ""


def resolve_repo(cli_repo: str | None) -> Path:
    """解析 --repo 参数为绝对路径，并验证是 git 仓库。"""
    repo = Path(cli_repo or ".").resolve()
    if not repo.is_dir():
        print(f"错误: 仓库目录不存在: {repo}", file=sys.stderr)
        raise SystemExit(2)
    try:
        r = git(repo, "rev-parse", "--git-dir")
    except subprocess.TimeoutExpired:
        # git 挂起（凭据提示/挂载问题等）按执行错误处理（F43）
        print(f"错误: git rev-parse 执行超时({GIT_TIMEOUT_SECONDS}s): {repo}", file=sys.stderr)
        raise SystemExit(2)
    if r.returncode != 0:
        # 结构化错误提示（E001），退出码契约保持 2 不变
        print_error(ErrorCode.E001_GIT_NOT_REPO, location=str(repo), detail=r.stderr.strip())
        raise SystemExit(2)
    return repo
