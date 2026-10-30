"""Phase 0 范围解析: 五级阶梯的实际执行者(SKILL.md Phase 0 的确定性部分)。

阶梯契约(细节见 references/scope.md):
    1. 显式目标(分支/PR 号/提交区间/路径/repo)
    2. 当前分支的 PR(gh pr diff)
    3. 未推送提交(git diff @{upstream}...HEAD)
    4. 分支 vs 默认分支(git diff <default>...HEAD)
    5. 工作树(git diff HEAD)
任何一级的命令失败或输出为空都不是答案 —— 而是"继续下降"。
untracked(??) 文件对一切 git diff 形态不可见, 必须显式累积; 仅由 untracked
构成的 diff 不是空 diff。范围 2–4 级叠加工作树改动(评审通常发生在提交前)。

全程只读; 一切探测留痕(probes), 全空时进逃逸口(ask_user)而非静默空审。
"""

from __future__ import annotations

import fnmatch
import hashlib
import re
import subprocess
from pathlib import Path
from typing import Optional

from . import config, gitops
from .logutil import module_logger


# --------------------------------------------------------------------------- #
# 目标分类(rung 1)
# --------------------------------------------------------------------------- #
def classify_target(cwd: str | Path, target: str) -> str:
    """显式目标分类: repo / pr / range / path / branch。"""
    if target == "repo":
        return "repo"
    if re.fullmatch(r"\d+", target):
        return "pr"
    if ".." in target:
        return "range"
    p = Path(target)
    if p.is_absolute():
        root = gitops.repo_root(cwd)
        if root is not None:
            try:
                p = p.resolve().relative_to(root)
            except ValueError:
                return "branch"  # 仓库外绝对路径 —— 按 ref 尝试, 失败则逃逸口
            target = str(p)
    if (Path(cwd) / target).exists():
        return "path"
    ok, out = gitops.run(["ls-files"], cwd)
    if ok and target in out.splitlines():
        return "path"
    return "branch"


def resolve_default_branch(cwd: str | Path) -> Optional[str]:
    """默认分支解析: 远端跟踪 ref 优先(见 SKILL.md 状态探测)。"""
    ok, out = gitops.run(
        ["symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD"], cwd)
    if ok and out and out.startswith("origin/"):
        return out
    for cand in config.DEFAULT_BRANCH_CANDIDATES:
        ok, _ = gitops.run(["rev-parse", "-q", "--verify", cand], cwd)
        if ok:
            return cand
    return None


# --------------------------------------------------------------------------- #
# diff 探测(空判定 + 文件清单 + 统计)
# --------------------------------------------------------------------------- #
def _diff_files(cwd: str | Path, diff_args: list[str]) -> Optional[list[str]]:
    """`git diff --name-only <args>` 的文件清单; 命令失败返回 None(继续下降)。"""
    ok, out = gitops.run(["diff", "--name-only", *diff_args], cwd)
    if not ok:
        return None
    return [f for f in out.splitlines() if f]


def _pr_diff_files(cwd: str | Path, pr_number: Optional[int] = None) -> Optional[list[str]]:
    """`gh pr diff --name-only` 的文件清单; gh 缺失/失败返回 None(继续下降)。"""
    args = ["pr", "diff"]
    if pr_number is not None:
        args.append(str(pr_number))
    args.append("--name-only")
    try:
        proc = subprocess.run(["gh", *args], cwd=str(cwd), capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=config.GIT_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return [f for f in proc.stdout.splitlines() if f and not f.startswith(("Draft:", "Title:"))]


def _numstat(cwd: str | Path, diff_args: list[str]) -> tuple[int, int]:
    """`git diff --numstat` 求和(insertions, deletions); 失败返回 (0, 0)。"""
    ok, out = gitops.run(["diff", "--numstat", *diff_args], cwd)
    ins = dele = 0
    if not ok:
        return 0, 0
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            ins += int(parts[0])
            dele += int(parts[1])
    return ins, dele


def _file_insertions(path: Path) -> int:
    """untracked 文本的行数(二进制/超大文件计 0)。"""
    try:
        with open(path, "rb") as fp:
            return sum(1 for _ in fp)
    except OSError:
        return 0


# --------------------------------------------------------------------------- #
# 阶梯主体
# --------------------------------------------------------------------------- #
def _base_scope(cwd: Path, kind: str) -> dict:
    return {
        "kind": kind,
        "rung": 0,
        "target": None,
        "scope_command": "",
        "secondary_commands": [],
        "untracked": [],
        "base": None,
        "head": gitops.head_sha(cwd),
        "raw_files": [],
        "diff_files": [],
        "stats": {"files": 0, "insertions": 0, "deletions": 0},
        "excluded": [],
        "whole_file_mode": False,
        "repo_mode": False,
        "pr_number": None,
        "escape_hatch": None,
        "commit_log_cmd": "",
    }


def _finish_scope(cwd: Path, scope: dict, files: list[str],
                  untracked: list[str], stats: tuple[int, int]) -> dict:
    """填文件并集与统计; 返回 scope(就地修改)。"""
    scope["raw_files"] = sorted(set(files) | set(untracked))
    scope["diff_files"] = list(scope["raw_files"])
    scope["untracked"] = sorted(untracked)
    ins, dele = stats
    for u in untracked:
        ins += _file_insertions(cwd / u)
    scope["stats"] = {"files": len(scope["raw_files"]), "insertions": ins, "deletions": dele}
    return scope


def _ladder(cwd: Path, target: Optional[str], path_restricted: bool) -> dict:
    """无显式目标(或路径受限)时的 2→5 级阶梯; 逐级留痕。

    路径受限时 HEAD 先行(references/scope.md 契约, 回修 c052): 路径目标的
    主场景是"审我刚改的这块", 工作树优先于远端范围。
    """
    probes: list[dict] = []
    untracked = gitops.untracked_files(cwd)

    def restricted(args: list[str]) -> list[str]:
        return [*args, "--", target] if (path_restricted and target) else list(args)

    def untracked_in_scope() -> list[str]:
        if not (path_restricted and target):
            return untracked
        prefix = target.rstrip("/")
        return [u for u in untracked if u == prefix or u.startswith(prefix + "/")]

    def commit_log(base: Optional[str]) -> str:
        if base:
            return f"git log --oneline {base}..HEAD"
        return "git log --oneline -5"

    def accumulate_working_tree(rung: int, files: list[str]) -> tuple[list[str], list[str]]:
        """范围 2–4 级: porcelain 有暂存/未暂存改动时叠加 `git diff HEAD`(路径受限)。"""
        secondary: list[str] = []
        if rung in (2, 3, 4):
            ok, out = gitops.run(["status", "--porcelain", "-z", "-uall"], cwd)
            has_wt = ok and any(
                rec and not rec.startswith("??") for rec in out.split("\0"))
            if has_wt:
                wt_files = _diff_files(cwd, restricted(["HEAD"])) or []
                if wt_files:
                    secondary = [_display("git diff HEAD", target, path_restricted)]
                    files = sorted(set(files) | set(wt_files))
        return files, secondary

    # ── 路径受限: HEAD 先行(工作树是该路径最常见的审查对象) ──
    if path_restricted and target:
        files = _diff_files(cwd, ["HEAD", "--", target])
        probes.append({"cmd": f"git diff --name-only HEAD -- {target}",
                       "rc": 0 if files is not None else 1, "empty": not files})
        if files:
            scope = _base_scope(cwd, "worktree")
            scope.update(rung=5, scope_command=_display("git diff HEAD", target, True),
                         commit_log_cmd="git log --oneline -5")
            ins, dele = _numstat(cwd, ["HEAD", "--", target])
            return _finish_scope(cwd, scope, files, untracked_in_scope(), (ins, dele))

    # ── rung 2: 当前分支的 PR ──
    files = None if path_restricted else _pr_diff_files(cwd)
    probes.append({"cmd": "gh pr diff --name-only", "rc": 0 if files is not None else 1,
                   "empty": not files})
    if files:  # None(失败)与 [](空)都继续下降
        scope = _base_scope(cwd, "pr")
        scope.update(rung=2, scope_command="gh pr diff",
                     commit_log_cmd="gh pr view")
        files, secondary = accumulate_working_tree(2, files)
        scope["secondary_commands"] = secondary
        return _finish_scope(cwd, scope, files, untracked_in_scope(), (0, 0))

    # ── rung 3: 未推送提交 ──
    files = _diff_files(cwd, restricted(["@{upstream}...HEAD"]))
    probes.append({"cmd": "git diff --name-only @{upstream}...HEAD",
                   "rc": 0 if files is not None else 1, "empty": not files})
    if files:
        scope = _base_scope(cwd, "upstream")
        ok, up = gitops.run(["rev-parse", "--short", "@{upstream}"], cwd)
        base = up if ok else None  # 先落局部变量 —— scope.get("base") 在 update 前还是空(回修 c020)
        scope.update(rung=3, scope_command=_display("git diff @{upstream}...HEAD", target, path_restricted),
                     base=base, commit_log_cmd=commit_log(base))
        files, secondary = accumulate_working_tree(3, files)
        scope["secondary_commands"] = secondary
        ins, dele = _numstat(cwd, ["@{upstream}...HEAD"])
        return _finish_scope(cwd, scope, files, untracked_in_scope(), (ins, dele))

    # ── rung 4: 分支 vs 默认分支 ──
    default = resolve_default_branch(cwd)
    files = None
    if default and not path_restricted:
        files = _diff_files(cwd, [f"{default}...HEAD"])
    elif default and path_restricted:
        files = _diff_files(cwd, [f"{default}...HEAD", "--", target])
    probes.append({"cmd": f"git diff --name-only {default or '<无默认分支>'}...HEAD",
                   "rc": 0 if files is not None else 1, "empty": not files})
    if files:
        scope = _base_scope(cwd, "default")
        ok, base = gitops.run(["merge-base", default, "HEAD"], cwd)
        scope.update(rung=4,
                     scope_command=_display(f"git diff {default}...HEAD", target, path_restricted),
                     base=base if ok else default, commit_log_cmd=commit_log(base if ok else default))
        files, secondary = accumulate_working_tree(4, files)
        scope["secondary_commands"] = secondary
        ins, dele = _numstat(cwd, [f"{default}...HEAD"])
        return _finish_scope(cwd, scope, files, untracked_in_scope(), (ins, dele))

    # ── rung 5: 工作树(路径受限时为首级) ──
    files = _diff_files(cwd, restricted(["HEAD"]))
    probes.append({"cmd": "git diff --name-only HEAD",
                   "rc": 0 if files is not None else 1, "empty": not files})
    if files:
        scope = _base_scope(cwd, "worktree")
        scope.update(rung=5, scope_command=_display("git diff HEAD", target, path_restricted),
                     commit_log_cmd="git log --oneline -5")
        ins, dele = _numstat(cwd, ["HEAD"])
        return _finish_scope(cwd, scope, files, untracked_in_scope(), (ins, dele))

    # ── 全空: 路径目标回退 whole-file; 否则逃逸口 ──
    in_scope_untracked = untracked_in_scope()
    if path_restricted and target and not in_scope_untracked:
        scope = _base_scope(cwd, "whole_file")
        scope.update(whole_file_mode=True, rung=1,
                     scope_command=f"(whole-file) cat {target}",
                     commit_log_cmd=f"git log -p -- {target}")
        return _finish_scope(cwd, scope, [target], [], (0, 0))
    if in_scope_untracked:
        scope = _base_scope(cwd, "worktree")
        scope.update(rung=5, scope_command="", commit_log_cmd="git log --oneline -5")
        return _finish_scope(cwd, scope, [], in_scope_untracked, (0, 0))
    scope = _base_scope(cwd, "empty")
    scope["escape_hatch"] = {"probes": probes}
    return scope


def _display(cmd: str, target: Optional[str], path_restricted: bool) -> str:
    return f"{cmd} -- {target}" if (path_restricted and target) else cmd


def resolve_scope(cwd: str | Path, target: Optional[str] = None) -> dict:
    """解析审查范围; 返回 scope dict(见模块 docstring 的形状)。

    一切命令在仓库顶层执行(porcelain 路径相对顶层, 与 untracked/排除一致);
    非顶层调用由本函数归一。
    """
    cwd = Path(cwd).resolve()
    root = gitops.repo_root(cwd)
    if root is not None:
        cwd = root.resolve()

    if not target:
        return _ladder(cwd, None, False)

    kind = classify_target(cwd, target)
    untracked = gitops.untracked_files(cwd)

    if kind == "repo":
        # 空树基线: 一切 tracked 文件读作新文件(SKILL.md Phase 0 repo 语义)
        files = _diff_files(cwd, [f"{config.EMPTY_TREE_HASH}..HEAD"])
        if files is None:
            # 老版本 git 不认识 magic hash —— 交由调用方按逃逸口处理
            scope = _base_scope(cwd, "repo")
            scope["escape_hatch"] = {
                "probes": [{"cmd": f"git diff --name-only {config.EMPTY_TREE_HASH}..HEAD",
                            "rc": 1, "empty": True}]}
            return scope
        scope = _base_scope(cwd, "repo")
        scope.update(rung=1, repo_mode=True, target="repo",
                     scope_command=f"git diff {config.EMPTY_TREE_HASH}..HEAD",
                     commit_log_cmd="git log --oneline -20")
        ins, dele = _numstat(cwd, [f"{config.EMPTY_TREE_HASH}..HEAD"])
        return _finish_scope(cwd, scope, files, [], (ins, dele))

    if kind == "pr":
        files = _pr_diff_files(cwd, int(target))
        if files is None:
            scope = _base_scope(cwd, "pr")
            scope["escape_hatch"] = {
                "probes": [{"cmd": f"gh pr diff --name-only {target}", "rc": 1, "empty": True}]}
            return scope
        scope = _base_scope(cwd, "pr")
        scope.update(rung=1, target=target, pr_number=int(target),
                     scope_command=f"gh pr diff {target}",
                     commit_log_cmd=f"gh pr view {target}")
        return _finish_scope(cwd, scope, files, untracked, (0, 0))

    if kind == "range":
        files = _diff_files(cwd, [target])
        if not files:
            scope = _base_scope(cwd, "range")
            scope["escape_hatch"] = {
                "probes": [{"cmd": f"git diff --name-only {target}", "rc": 0, "empty": True}]}
            return scope
        scope = _base_scope(cwd, "range")
        base, _, head = target.partition("..")
        scope.update(rung=1, target=target, scope_command=f"git diff {target}",
                     base=base or None, head=head or None,
                     commit_log_cmd=f"git log --oneline {target}")
        ins, dele = _numstat(cwd, [target])
        return _finish_scope(cwd, scope, files, untracked, (ins, dele))

    if kind == "path":
        return _ladder(cwd, target, True)

    # branch: 与默认分支对比(三分点自 merge-base); 目标即默认分支时改用其
    # upstream(默认分支对自身的 diff 恒空, 不是答案)
    default = resolve_default_branch(cwd)
    files = None
    base_ref = default
    if default and default == target:
        ok_up, _ = gitops.run(["rev-parse", "-q", "--verify", "@{upstream}"], cwd)
        if ok_up:
            base_ref = "@{upstream}"
            files = _diff_files(cwd, [f"@{{upstream}}...{target}"])
    elif default:
        files = _diff_files(cwd, [f"{default}...{target}"])
    if not files:
        scope = _base_scope(cwd, "branch")
        scope["escape_hatch"] = {
            "probes": [{"cmd": f"git diff --name-only {base_ref or '<无默认分支>'}...{target}",
                        "rc": 0 if files is not None else 1, "empty": True}]}
        return scope
    scope = _base_scope(cwd, "branch")
    ok, base = gitops.run(["merge-base", base_ref, target], cwd)
    ok_head, head = gitops.run(["rev-parse", "--short", target], cwd)
    scope.update(rung=1, target=target,
                 scope_command=f"git diff {base_ref}...{target}",
                 base=base if ok else base_ref, head=head if ok_head else None,
                 commit_log_cmd=f"git log --oneline {base_ref}..{target}")
    ins, dele = _numstat(cwd, [f"{base_ref}...{target}"])
    return _finish_scope(cwd, scope, files, untracked, (ins, dele))


# --------------------------------------------------------------------------- #
# 排除项应用(overlay 阶段调用)
# --------------------------------------------------------------------------- #
def _matches_exclusion(path: str, pattern: str) -> bool:
    p = pattern.strip()
    if p.startswith("./"):  # 前缀剥离而非字符集剥离(回修 c022: lstrip("./") 会吃掉前导点)
        p = p[2:]
    if not p:
        return False
    if p.endswith("/"):
        return path.startswith(p)
    return path == p or path.startswith(p + "/") or fnmatch.fnmatch(path, p)


def apply_exclusions(scope: dict, patterns: list[str], source: str) -> dict:
    """把排除模式应用到 scope(就地); 返回被排除记录列表。

    排除模式为路径前缀/目录(尾 /)/glob; 排除路径完全不进审查范围。
    """
    excluded: list[dict] = []
    kept: list[str] = []
    for path in scope.get("diff_files", []):
        hit = next((pat for pat in patterns if _matches_exclusion(path, pat)), None)
        if hit is not None:
            excluded.append({"path": path, "pattern": hit, "reason": source, "applied": True})
        else:
            kept.append(path)
    scope["diff_files"] = kept
    scope["excluded"] = scope.get("excluded", []) + excluded
    scope["stats"]["files"] = len(kept)
    return scope


# --------------------------------------------------------------------------- #
# 漂移探测(中断续跑)
# --------------------------------------------------------------------------- #
def current_signature(cwd: str | Path) -> dict:
    """当前仓库签名: HEAD + 工作树 porcelain 摘要。"""
    entries = gitops.porcelain_z(cwd)
    raw = "\0".join(f"{e['xy']} {e['path']}" for e in entries)
    return {
        "head": gitops.head_sha(cwd),
        "wt": hashlib.sha1(raw.encode("utf-8")).hexdigest(),
    }


def signature_of(repo: dict) -> dict:
    """state.repo 中记录的签名。"""
    return {"head": repo.get("head"), "wt": repo.get("wt_signature")}


def drift_between(recorded: dict, current: dict) -> str:
    """比较签名: none / head_moved / working_tree_changed。"""
    if recorded.get("head") and current.get("head") and recorded["head"] != current["head"]:
        return "head_moved"
    if recorded.get("wt") and current.get("wt") and recorded["wt"] != current["wt"]:
        return "working_tree_changed"
    return "none"
