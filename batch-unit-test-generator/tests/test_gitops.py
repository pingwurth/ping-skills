"""gitops 单测: current_branch 查询/降级, record_git_main_source_files init 快照。"""

from __future__ import annotations

import subprocess

from jaut import gitops


def _git(cwd, *args) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True)


def _make_repo(tmp_path) -> None:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    _git(tmp_path, "add", "a.txt")
    _git(tmp_path, "commit", "-q", "-m", "init")


def test_current_branch_in_repo(tmp_path):
    _make_repo(tmp_path)
    assert gitops.current_branch(tmp_path) == "main"


def test_current_branch_detached_returns_none(tmp_path):
    _make_repo(tmp_path)
    _git(tmp_path, "checkout", "-q", "--detach")
    assert gitops.current_branch(tmp_path) is None


def test_current_branch_not_a_repo_returns_none(tmp_path):
    assert gitops.current_branch(tmp_path) is None


# --------------------------------------------------------------------------- #
# record_git_main_source_files: 规则 d 删除检测的 "init 时存在" 快照
# --------------------------------------------------------------------------- #
def _commit_main_files(tmp_path, *rel_paths: str) -> None:
    for rel in rel_paths:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"// {rel}\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "add main files")


def test_record_git_main_source_files_scope_and_order(tmp_path):
    """仅含 init 时存在的 src/main/java 文件, 非主源码路径排除, 路径含空格不破坏解析。"""
    _make_repo(tmp_path)
    _commit_main_files(tmp_path, "src/main/java/com/x/B.java",
                       "src/main/java/com/x/A.java",
                       "src/main/java/com/x/With Space.java")
    assert gitops.record_git_main_source_files(tmp_path) == [
        "src/main/java/com/x/A.java",
        "src/main/java/com/x/B.java",
        "src/main/java/com/x/With Space.java",
    ]


def test_record_git_main_source_files_excludes_pre_deleted(tmp_path):
    """init 前已删除(未暂存/已暂存)的文件不在快照中, 既有删除据此豁免。"""
    _make_repo(tmp_path)
    _commit_main_files(tmp_path, "src/main/java/com/x/A.java",
                       "src/main/java/com/x/B.java",
                       "src/main/java/com/x/C.java")
    (tmp_path / "src/main/java/com/x/B.java").unlink()          # 未暂存删除(索引仍在)
    _git(tmp_path, "rm", "-q", "src/main/java/com/x/C.java")   # 已暂存删除(索引已移除)
    assert gitops.record_git_main_source_files(tmp_path) == [
        "src/main/java/com/x/A.java"]


def test_record_git_main_source_files_not_a_repo_returns_empty(tmp_path):
    assert gitops.record_git_main_source_files(tmp_path) == []
