"""worktree 领域逻辑单测: 名称校验、默认名、分支派生、容器定位与列举。"""

from __future__ import annotations

import datetime
import logging
from pathlib import Path

from jaut import config, worktree as wt

_log = logging.getLogger("test_worktree")


def test_current_dir_name_and_parent(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    assert wt.current_dir_name(str(repo)) == "repo"
    assert wt.parent_dir(str(repo)) == str(tmp_path)


def test_default_worktree_name_format():
    today = datetime.date.today().strftime("%Y%m%d")
    assert wt.default_worktree_name("repo") == f"repo{config.WORKTREE_NAME_SUFFIX}{today}"


def test_branch_name_for_strips_prefix():
    assert wt.branch_name_for("repo.worktree20260903", "repo") == "worktree20260903"
    assert wt.branch_name_for("feature-x", "repo") == "feature-x"   # 无前缀原样返回


def test_validate_new_name():
    assert wt.validate_new_name("good_name", _log) is True
    assert wt.validate_new_name("", _log) is False
    assert wt.validate_new_name(".", _log) is False
    assert wt.validate_new_name("bad*name", _log) is False


def test_find_and_resolve_container(tmp_path: Path):
    base = str(tmp_path)
    # 容器不存在: find 返回 None, resolve 返回期望路径
    assert wt.find_container(base, "repo") is None
    expected = str(tmp_path / ("repo" + config.WORKTREE_CONTAINER_SUFFIX))
    assert wt.resolve_container(base, "repo") == expected
    # 创建容器后 find 命中
    Path(expected).mkdir()
    assert wt.find_container(base, "repo") == expected


def test_list_worktrees(tmp_path: Path):
    container = tmp_path / ("repo" + config.WORKTREE_CONTAINER_SUFFIX)
    container.mkdir()
    (container / "b").mkdir()
    (container / "a").mkdir()
    (container / "file.txt").write_text("x", encoding="utf-8")   # 非目录应忽略
    found = wt.list_worktrees(str(container), _log)
    assert [Path(p).name for p in found] == ["a", "b"]           # 排序且仅目录


def test_list_worktrees_missing_container_returns_empty(tmp_path: Path):
    assert wt.list_worktrees(str(tmp_path / "nope"), _log) == []


# --------------------------------------------------------------------------- #
# P0-5: has_uncommitted_changes
# --------------------------------------------------------------------------- #
def test_has_uncommitted_changes_clean_repo(tmp_path: Path):
    """干净仓库应返回空列表。"""
    import subprocess
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(tmp_path), capture_output=True)
    (tmp_path / "README.md").write_text("init", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(tmp_path), capture_output=True)
    assert wt.has_uncommitted_changes(str(tmp_path), _log) == []


def test_has_uncommitted_changes_dirty_repo(tmp_path: Path):
    """有未提交变更时应返回变更文件列表。"""
    import subprocess
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(tmp_path), capture_output=True)
    (tmp_path / "README.md").write_text("init", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(tmp_path), capture_output=True)
    # 引入未提交变更
    (tmp_path / "dirty.txt").write_text("new file", encoding="utf-8")
    changes = wt.has_uncommitted_changes(str(tmp_path), _log)
    assert len(changes) >= 1
    assert any("dirty.txt" in c for c in changes)


def test_has_uncommitted_changes_non_repo(tmp_path: Path):
    """非 git 仓库应返回空列表(容错)。"""
    assert wt.has_uncommitted_changes(str(tmp_path), _log) == []
