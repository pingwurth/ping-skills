"""gitops.current_branch 单测: 真实 git 仓库内查询、detached 与非仓库降级。"""

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
