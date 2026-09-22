"""worktree 领域逻辑单测: 名称校验、默认名、分支派生、容器定位与列举。"""

from __future__ import annotations

import datetime
import logging
from pathlib import Path

import pytest

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


# --------------------------------------------------------------------------- #
# --clear-history: 清理历史 worktree 与 create_new_worktree 分支复用回退
# --------------------------------------------------------------------------- #
def _init_repo_with_commit(path: Path) -> None:
    """初始化带一次提交的 git 仓库(clear/建树用例的公共前置)。"""
    import subprocess
    subprocess.run(["git", "init"], cwd=str(path), capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(path), capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(path), capture_output=True)
    (path / "README.md").write_text("init", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(path), capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(path), capture_output=True)


def test_clear_all_worktrees_removes_and_returns_paths(tmp_path: Path):
    """清理全部历史 worktree: 路径消失、返回列表正确、分支保留。"""
    import subprocess
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo_with_commit(repo)
    container = tmp_path / ("repo" + config.WORKTREE_CONTAINER_SUFFIX)
    container.mkdir()
    p1 = wt.create_new_worktree(str(container), "repo.worktree20260101", "repo", str(repo), _log)
    p2 = wt.create_new_worktree(str(container), "repo.worktree20260102", "repo", str(repo), _log)

    removed = wt.clear_all_worktrees(str(container), str(repo), _log)

    assert sorted(removed) == sorted([p1, p2])
    assert wt.list_worktrees(str(container), _log) == []
    assert not Path(p1).exists() and not Path(p2).exists()
    branches = subprocess.run(["git", "branch", "--list"], cwd=str(repo),
                              capture_output=True, text=True).stdout
    assert "worktree20260101" in branches and "worktree20260102" in branches


def test_clear_all_worktrees_removes_dirty(tmp_path: Path):
    """脏 worktree(含未跟踪文件)也应被强制移除——清理始终 --force, 不做脏树询问。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo_with_commit(repo)
    container = tmp_path / ("repo" + config.WORKTREE_CONTAINER_SUFFIX)
    container.mkdir()
    path = wt.create_new_worktree(str(container), "repo.worktree20260101", "repo", str(repo), _log)
    (Path(path) / "dirty.txt").write_text("uncommitted", encoding="utf-8")

    removed = wt.clear_all_worktrees(str(container), str(repo), _log)

    assert removed == [path]
    assert not Path(path).exists()
    assert wt.list_worktrees(str(container), _log) == []


def test_clear_all_worktrees_idempotent_when_no_container(tmp_path: Path):
    """容器不存在时清理为幂等空操作。"""
    assert wt.clear_all_worktrees(str(tmp_path / "nope"), str(tmp_path), _log) == []


def test_create_new_worktree_reuses_existing_branch(tmp_path: Path):
    """分支已存在(清理保留分支/同日默认名)时同名重建: 复用分支而非报 branch already exists。"""
    import subprocess
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo_with_commit(repo)
    container = tmp_path / ("repo" + config.WORKTREE_CONTAINER_SUFFIX)
    container.mkdir()
    path = wt.create_new_worktree(str(container), "repo.worktree20260101", "repo", str(repo), _log)
    # 移除 worktree 但保留分支(模拟 --clear-history 后的仓库状态)
    subprocess.run(["git", "worktree", "remove", "--force", path],
                   cwd=str(repo), capture_output=True)

    path2 = wt.create_new_worktree(str(container), "repo.worktree20260101", "repo", str(repo), _log)

    assert path2 == path
    assert Path(path2).is_dir()


def test_create_new_worktree_base_ref_when_branch_exists(tmp_path: Path):
    """分支已存在且指定 base_ref: 强制重置到 base_ref 后检出派生分支(非 detached)。"""
    import subprocess
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo_with_commit(repo)
    container = tmp_path / ("repo" + config.WORKTREE_CONTAINER_SUFFIX)
    container.mkdir()
    path = wt.create_new_worktree(str(container), "repo.worktree20260101", "repo", str(repo), _log)
    subprocess.run(["git", "worktree", "remove", "--force", path],
                   cwd=str(repo), capture_output=True)
    # 二次提交, HEAD 前进; 再把分支强制重置回首个提交
    (repo / "second.txt").write_text("s2", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(repo), capture_output=True)
    subprocess.run(["git", "commit", "-m", "second"], cwd=str(repo), capture_output=True)
    first = subprocess.run(["git", "rev-parse", "HEAD~1"], cwd=str(repo),
                           capture_output=True, text=True).stdout.strip()

    path2 = wt.create_new_worktree(str(container), "repo.worktree20260101", "repo",
                                   str(repo), _log, base_ref=first)

    assert path2 == path
    assert Path(path2).is_dir()
    branch = subprocess.run(["git", "branch", "--show-current"], cwd=path2,
                            capture_output=True, text=True).stdout.strip()
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=path2,
                          capture_output=True, text=True).stdout.strip()
    assert branch == "worktree20260101"   # 非 detached
    assert head == first                   # 落在 base_ref 上


def test_create_new_worktree_force_resets_free_branch(tmp_path: Path):
    """分支已存在且空闲 + base_ref: refs/heads/<branch> 被重置到 base_ref。"""
    import subprocess
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo_with_commit(repo)
    container = tmp_path / ("repo" + config.WORKTREE_CONTAINER_SUFFIX)
    container.mkdir()
    path = wt.create_new_worktree(str(container), "repo.worktree20260101", "repo", str(repo), _log)
    subprocess.run(["git", "worktree", "remove", "--force", path],
                   cwd=str(repo), capture_output=True)
    (repo / "second.txt").write_text("s2", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(repo), capture_output=True)
    subprocess.run(["git", "commit", "-m", "second"], cwd=str(repo), capture_output=True)
    first = subprocess.run(["git", "rev-parse", "HEAD~1"], cwd=str(repo),
                           capture_output=True, text=True).stdout.strip()

    path2 = wt.create_new_worktree(str(container), "repo.worktree20260101", "repo",
                                   str(repo), _log, base_ref=first)

    ref = subprocess.run(["git", "rev-parse", "refs/heads/worktree20260101"],
                         cwd=str(repo), capture_output=True, text=True).stdout.strip()
    assert ref == first
    branch = subprocess.run(["git", "branch", "--show-current"], cwd=path2,
                            capture_output=True, text=True).stdout.strip()
    assert branch == "worktree20260101"


def test_create_new_worktree_branch_checked_out_elsewhere_raises(tmp_path: Path):
    """派生分支已在其他路径检出时 create 抛 WorktreeError, 目标目录不创建。"""
    import subprocess
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo_with_commit(repo)
    container = tmp_path / ("repo" + config.WORKTREE_CONTAINER_SUFFIX)
    container.mkdir()
    path = wt.create_new_worktree(str(container), "repo.worktree20260101", "repo", str(repo), _log)
    # 无 '<目录名>.' 前缀的同名目标派生出同一分支 worktree20260101, 但路径不同
    target = str(container / "worktree20260101")
    try:
        with pytest.raises(wt.WorktreeError, match="已被检出"):
            wt.create_new_worktree(str(container), "worktree20260101", "repo",
                                   str(repo), _log)
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", path],
                       cwd=str(repo), capture_output=True)
    assert not Path(target).exists()


def test_preflight_rejects_invalid_base_ref(tmp_path: Path):
    """预检: base_ref 无效 -> WorktreeError, 且不触碰历史 worktree。"""
    import subprocess
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo_with_commit(repo)
    container = tmp_path / ("repo" + config.WORKTREE_CONTAINER_SUFFIX)
    container.mkdir()
    path = wt.create_new_worktree(str(container), "repo.worktree20260101", "repo", str(repo), _log)

    with pytest.raises(wt.WorktreeError, match="不存在或无效"):
        wt.preflight_new_worktree(str(container), "repo.worktree20260102", "repo",
                                  str(repo), "no-such-ref", _log)

    assert Path(path).exists()   # 历史保留


def test_preflight_allows_branch_checked_out_inside_container(tmp_path: Path):
    """预检: 分支检出在容器内(将被 clear 释放)不算冲突。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo_with_commit(repo)
    container = tmp_path / ("repo" + config.WORKTREE_CONTAINER_SUFFIX)
    container.mkdir()
    path = wt.create_new_worktree(str(container), "repo.worktree20260101", "repo", str(repo), _log)
    # 同分支已在容器内 path 检出; 预检同名目标 -> free_paths 包含 path, 通过
    result = wt.preflight_new_worktree(str(container), "repo.worktree20260101", "repo",
                                       str(repo), None, _log)
    assert result == path
    # 清理以免影响后续(本测试内)
    import subprocess
    subprocess.run(["git", "worktree", "remove", "--force", path],
                   cwd=str(repo), capture_output=True)


def test_preflight_rejects_branch_checked_out_in_primary(tmp_path: Path):
    """预检: 同名分支在主仓/容器外检出 -> WorktreeError 并指明检出路径。"""
    import subprocess
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo_with_commit(repo)
    # 在主仓创建并检出目标分支(模拟同日分支被主仓占用)
    subprocess.run(["git", "checkout", "-b", "worktree20260101"],
                   cwd=str(repo), capture_output=True)
    container = tmp_path / ("repo" + config.WORKTREE_CONTAINER_SUFFIX)
    container.mkdir()

    with pytest.raises(wt.WorktreeError, match="已被检出"):
        wt.preflight_new_worktree(str(container), "repo.worktree20260101", "repo",
                                  str(repo), None, _log)
    subprocess.run(["git", "checkout", "-"], cwd=str(repo), capture_output=True)


def test_preflight_rejects_target_path_non_directory(tmp_path: Path):
    """预检: 目标路径是文件 -> WorktreeError。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo_with_commit(repo)
    container = tmp_path / ("repo" + config.WORKTREE_CONTAINER_SUFFIX)
    container.mkdir()
    (container / "repo.worktree20260101").write_text("not a dir", encoding="utf-8")

    with pytest.raises(wt.WorktreeError, match="不是目录"):
        wt.preflight_new_worktree(str(container), "repo.worktree20260101", "repo",
                                  str(repo), None, _log)


def test_effective_base_description_matrix(tmp_path: Path):
    """F6: 4 种 分支存在 × base_ref 组合的描述。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo_with_commit(repo)
    # 分支不存在
    assert wt.effective_base_description("nope", None, str(repo)) == "当前 HEAD"
    assert wt.effective_base_description("nope", "v1.0", str(repo)) == "v1.0"
    # 分支存在
    import subprocess
    subprocess.run(["git", "branch", "exist-b"], cwd=str(repo), capture_output=True)
    desc = wt.effective_base_description("exist-b", None, str(repo))
    assert desc == "已有分支 exist-b 的当前 tip"
    desc2 = wt.effective_base_description("exist-b", "v1.0", str(repo))
    assert desc2 == "已有分支 exist-b 强制重置到 v1.0"
