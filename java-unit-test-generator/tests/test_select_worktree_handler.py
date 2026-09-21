"""select_worktree.handler() 单测。"""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jaut import config
from jaut.cli import StepError
from jaut.models import Decision, Route
from scripts.select_worktree import handler


def _make_args(work_dir: str, **overrides) -> argparse.Namespace:
    defaults = dict(work_dir=work_dir, list=False, choice=None, new=None,
                   base=None, force=False)
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


# --------------------------------------------------------------------------- #
# 成功场景
# --------------------------------------------------------------------------- #
def test_handler_auto_creates_when_no_existing(tmp_path):
    """无参数且无已有 worktree: 先询问用户是否新建。"""
    repo_dir = tmp_path / "myrepo"
    repo_dir.mkdir()
    args = _make_args(str(repo_dir))

    with patch("scripts.select_worktree.setup_logger"), \
         patch("scripts.select_worktree.wt.parent_dir", return_value=str(tmp_path)), \
         patch("scripts.select_worktree.wt.current_dir_name", return_value="myrepo"), \
         patch("scripts.select_worktree.wt.resolve_container", return_value=str(tmp_path / "myrepo.worktrees")), \
         patch("scripts.select_worktree.wt.find_container", return_value=None), \
         patch("scripts.select_worktree.wt.list_worktrees", return_value=[]), \
         patch("scripts.select_worktree.wt.default_worktree_name", return_value="myrepo.worktree20260908"), \
         patch("scripts.select_worktree.wt.has_uncommitted_changes", return_value=[]), \
         patch("scripts.select_worktree.wt.create_new_worktree") as mock_create:
        mock_create.return_value = str(tmp_path / "myrepo.worktrees" / "myrepo.worktree20260908")
        decision, ectx = handler(args)

    # 现在行为是先询问用户，而不是自动新建
    assert decision.route == Route.ASK_USER
    assert "需用户确认是否新建" in decision.summary
    assert len(decision.resume) == 3  # 3个选项：默认新建、自定义名称、取消
    assert decision.resume[0].option == "continue"
    assert decision.resume[1].option == "custom"
    assert decision.resume[2].option == "terminate"
    mock_create.assert_not_called()


def test_handler_auto_creates_with_force_when_no_existing(tmp_path):
    """无参数且无已有 worktree 且 --force: 直接新建(用户已确认)。"""
    repo_dir = tmp_path / "myrepo"
    repo_dir.mkdir()
    args = _make_args(str(repo_dir), force=True)

    with patch("scripts.select_worktree.setup_logger"), \
         patch("scripts.select_worktree.wt.parent_dir", return_value=str(tmp_path)), \
         patch("scripts.select_worktree.wt.current_dir_name", return_value="myrepo"), \
         patch("scripts.select_worktree.wt.resolve_container", return_value=str(tmp_path / "myrepo.worktrees")), \
         patch("scripts.select_worktree.wt.find_container", return_value=None), \
         patch("scripts.select_worktree.wt.list_worktrees", return_value=[]), \
         patch("scripts.select_worktree.wt.default_worktree_name", return_value="myrepo.worktree20260908"), \
         patch("scripts.select_worktree.wt.has_uncommitted_changes", return_value=[]), \
         patch("scripts.select_worktree.wt.create_new_worktree") as mock_create:
        mock_create.return_value = str(tmp_path / "myrepo.worktrees" / "myrepo.worktree20260908")
        decision, ectx = handler(args)

    assert decision.route == Route.FINISH
    assert "已新建 worktree" in decision.summary
    mock_create.assert_called_once()


def test_handler_list_reports_existing(tmp_path):
    """--list: 报告已存在的 worktree。"""
    repo_dir = tmp_path / "myrepo"
    repo_dir.mkdir()
    args = _make_args(str(repo_dir), list=True)
    existing = [str(tmp_path / "wt1"), str(tmp_path / "wt2")]

    with patch("scripts.select_worktree.setup_logger"), \
         patch("scripts.select_worktree.wt.parent_dir", return_value=str(tmp_path)), \
         patch("scripts.select_worktree.wt.current_dir_name", return_value="myrepo"), \
         patch("scripts.select_worktree.wt.resolve_container", return_value=str(tmp_path / "myrepo.worktrees")), \
         patch("scripts.select_worktree.wt.find_container", return_value=str(tmp_path / "myrepo.worktrees")), \
         patch("scripts.select_worktree.wt.list_worktrees", return_value=existing):
        decision, ectx = handler(args)

    assert decision.route == Route.FINISH
    assert len(decision.deliverables) == 2


def test_handler_choice_selects_existing(tmp_path):
    """--choice 2: 选择第 2 个已有 worktree。"""
    repo_dir = tmp_path / "myrepo"
    repo_dir.mkdir()
    args = _make_args(str(repo_dir), choice=2)
    existing = [str(tmp_path / "wt1"), str(tmp_path / "wt2")]

    with patch("scripts.select_worktree.setup_logger"), \
         patch("scripts.select_worktree.wt.parent_dir", return_value=str(tmp_path)), \
         patch("scripts.select_worktree.wt.current_dir_name", return_value="myrepo"), \
         patch("scripts.select_worktree.wt.resolve_container", return_value=str(tmp_path / "myrepo.worktrees")), \
         patch("scripts.select_worktree.wt.find_container", return_value=str(tmp_path / "myrepo.worktrees")), \
         patch("scripts.select_worktree.wt.list_worktrees", return_value=existing):
        decision, ectx = handler(args)

    assert decision.route == Route.FINISH
    assert decision.deliverables[0] == str(tmp_path / "wt2")


def test_handler_new_creates_named_worktree(tmp_path):
    """--new my-feature: 新建指定名称的 worktree。"""
    repo_dir = tmp_path / "myrepo"
    repo_dir.mkdir()
    args = _make_args(str(repo_dir), new="my-feature")

    with patch("scripts.select_worktree.setup_logger"), \
         patch("scripts.select_worktree.wt.parent_dir", return_value=str(tmp_path)), \
         patch("scripts.select_worktree.wt.current_dir_name", return_value="myrepo"), \
         patch("scripts.select_worktree.wt.resolve_container", return_value=str(tmp_path / "myrepo.worktrees")), \
         patch("scripts.select_worktree.wt.find_container", return_value=str(tmp_path / "myrepo.worktrees")), \
         patch("scripts.select_worktree.wt.validate_new_name", return_value=True), \
         patch("scripts.select_worktree.wt.has_uncommitted_changes", return_value=[]), \
         patch("scripts.select_worktree.wt.create_new_worktree") as mock_create:
        mock_create.return_value = str(tmp_path / "myrepo.worktrees" / "my-feature")
        decision, ectx = handler(args)

    assert decision.route == Route.FINISH
    assert "已新建" in decision.summary


def test_handler_no_args_asks_user_when_existing(tmp_path):
    """无参数且有已有 worktree: ask_user 请求选择。"""
    repo_dir = tmp_path / "myrepo"
    repo_dir.mkdir()
    args = _make_args(str(repo_dir))
    existing = [str(tmp_path / "wt1"), str(tmp_path / "wt2")]

    with patch("scripts.select_worktree.setup_logger"), \
         patch("scripts.select_worktree.wt.parent_dir", return_value=str(tmp_path)), \
         patch("scripts.select_worktree.wt.current_dir_name", return_value="myrepo"), \
         patch("scripts.select_worktree.wt.resolve_container", return_value=str(tmp_path / "myrepo.worktrees")), \
         patch("scripts.select_worktree.wt.find_container", return_value=str(tmp_path / "myrepo.worktrees")), \
         patch("scripts.select_worktree.wt.list_worktrees", return_value=existing):
        decision, ectx = handler(args)

    assert decision.route == Route.ASK_USER
    assert "请选择" in decision.question


# --------------------------------------------------------------------------- #
# 错误场景
# --------------------------------------------------------------------------- #
def test_handler_raises_when_work_dir_missing(tmp_path):
    """工作目录不存在时应抛出 StepError。"""
    args = _make_args(str(tmp_path / "nonexistent"))

    with patch("scripts.select_worktree.setup_logger"):
        with pytest.raises(StepError) as exc_info:
            handler(args)

    assert exc_info.value.decision.exit_code == config.EXIT_ERROR
    assert "不存在" in exc_info.value.decision.summary


def test_handler_raises_when_choice_out_of_range(tmp_path):
    """--choice 超出范围时应抛出 StepError。"""
    repo_dir = tmp_path / "myrepo"
    repo_dir.mkdir()
    args = _make_args(str(repo_dir), choice=5)
    existing = [str(tmp_path / "wt1")]

    with patch("scripts.select_worktree.setup_logger"), \
         patch("scripts.select_worktree.wt.parent_dir", return_value=str(tmp_path)), \
         patch("scripts.select_worktree.wt.current_dir_name", return_value="myrepo"), \
         patch("scripts.select_worktree.wt.resolve_container", return_value=str(tmp_path / "myrepo.worktrees")), \
         patch("scripts.select_worktree.wt.find_container", return_value=str(tmp_path / "myrepo.worktrees")), \
         patch("scripts.select_worktree.wt.list_worktrees", return_value=existing):
        with pytest.raises(StepError) as exc_info:
            handler(args)

    assert exc_info.value.decision.exit_code == config.EXIT_ERROR
    assert "超出范围" in exc_info.value.decision.summary


def test_handler_raises_when_new_name_invalid(tmp_path):
    """--new 目录名非法时应抛出 StepError。"""
    repo_dir = tmp_path / "myrepo"
    repo_dir.mkdir()
    args = _make_args(str(repo_dir), new="../bad-name")

    with patch("scripts.select_worktree.setup_logger"), \
         patch("scripts.select_worktree.wt.parent_dir", return_value=str(tmp_path)), \
         patch("scripts.select_worktree.wt.current_dir_name", return_value="myrepo"), \
         patch("scripts.select_worktree.wt.resolve_container", return_value=str(tmp_path / "myrepo.worktrees")), \
         patch("scripts.select_worktree.wt.find_container", return_value=str(tmp_path / "myrepo.worktrees")), \
         patch("scripts.select_worktree.wt.validate_new_name", return_value=False):
        with pytest.raises(StepError) as exc_info:
            handler(args)

    assert exc_info.value.decision.exit_code == config.EXIT_ERROR
    assert "非法" in exc_info.value.decision.summary


# --------------------------------------------------------------------------- #
# P0-5: 未提交变更警告 (更新: 现在需要 --force 才会继续)
# --------------------------------------------------------------------------- #
def test_handler_new_with_force_creates_despite_dirty(tmp_path):
    """--new --force 时有未提交变更: 应直接创建并携带警告信息。"""
    repo_dir = tmp_path / "myrepo"
    repo_dir.mkdir()
    args = _make_args(str(repo_dir), new="feature-x", force=True)
    dirty_files = ["src/Main.java", "README.md"]

    with patch("scripts.select_worktree.setup_logger"), \
         patch("scripts.select_worktree.wt.parent_dir", return_value=str(tmp_path)), \
         patch("scripts.select_worktree.wt.current_dir_name", return_value="myrepo"), \
         patch("scripts.select_worktree.wt.resolve_container", return_value=str(tmp_path / "myrepo.worktrees")), \
         patch("scripts.select_worktree.wt.find_container", return_value=str(tmp_path / "myrepo.worktrees")), \
         patch("scripts.select_worktree.wt.validate_new_name", return_value=True), \
         patch("scripts.select_worktree.wt.has_uncommitted_changes", return_value=dirty_files), \
         patch("scripts.select_worktree.wt.create_new_worktree") as mock_create:
        mock_create.return_value = str(tmp_path / "myrepo.worktrees" / "feature-x")
        decision, ectx = handler(args)

    assert decision.route == Route.FINISH
    assert "2 个未提交变更" in decision.summary
    assert decision.metrics["uncommitted_changes"] == dirty_files


# --------------------------------------------------------------------------- #
# P1-3: 工作区脏时询问用户
# --------------------------------------------------------------------------- #
def test_handler_new_asks_user_when_dirty(tmp_path):
    """--new 时有未提交变更且无 --force: 应 ask_user 询问用户。"""
    repo_dir = tmp_path / "myrepo"
    repo_dir.mkdir()
    args = _make_args(str(repo_dir), new="feature-x")
    dirty_files = ["src/Main.java", "README.md"]

    with patch("scripts.select_worktree.setup_logger"), \
         patch("scripts.select_worktree.wt.parent_dir", return_value=str(tmp_path)), \
         patch("scripts.select_worktree.wt.current_dir_name", return_value="myrepo"), \
         patch("scripts.select_worktree.wt.resolve_container", return_value=str(tmp_path / "myrepo.worktrees")), \
         patch("scripts.select_worktree.wt.find_container", return_value=str(tmp_path / "myrepo.worktrees")), \
         patch("scripts.select_worktree.wt.validate_new_name", return_value=True), \
         patch("scripts.select_worktree.wt.has_uncommitted_changes", return_value=dirty_files):
        decision, ectx = handler(args)

    assert decision.route == Route.ASK_USER
    assert "未提交变更" in decision.question
    assert decision.resume is not None and len(decision.resume) > 0


def test_handler_new_creates_when_dirty_with_force(tmp_path):
    """--new 时有未提交变更但有 --force: 应直接创建。"""
    repo_dir = tmp_path / "myrepo"
    repo_dir.mkdir()
    args = _make_args(str(repo_dir), new="feature-x", force=True)
    dirty_files = ["src/Main.java", "README.md"]

    with patch("scripts.select_worktree.setup_logger"), \
         patch("scripts.select_worktree.wt.parent_dir", return_value=str(tmp_path)), \
         patch("scripts.select_worktree.wt.current_dir_name", return_value="myrepo"), \
         patch("scripts.select_worktree.wt.resolve_container", return_value=str(tmp_path / "myrepo.worktrees")), \
         patch("scripts.select_worktree.wt.find_container", return_value=str(tmp_path / "myrepo.worktrees")), \
         patch("scripts.select_worktree.wt.validate_new_name", return_value=True), \
         patch("scripts.select_worktree.wt.has_uncommitted_changes", return_value=dirty_files), \
         patch("scripts.select_worktree.wt.create_new_worktree") as mock_create:
        mock_create.return_value = str(tmp_path / "myrepo.worktrees" / "feature-x")
        decision, ectx = handler(args)

    assert decision.route == Route.FINISH
    assert "已新建" in decision.summary


def test_handler_no_args_asks_user_with_base(tmp_path):
    """无参数 + --base 且无已有 worktree: ask_user 的 resume 应包含 --base。"""
    repo_dir = tmp_path / "myrepo"
    repo_dir.mkdir()
    args = _make_args(str(repo_dir), base="refs/heads/develop")

    with patch("scripts.select_worktree.setup_logger"), \
         patch("scripts.select_worktree.wt.parent_dir", return_value=str(tmp_path)), \
         patch("scripts.select_worktree.wt.current_dir_name", return_value="myrepo"), \
         patch("scripts.select_worktree.wt.resolve_container", return_value=str(tmp_path / "myrepo.worktrees")), \
         patch("scripts.select_worktree.wt.find_container", return_value=None), \
         patch("scripts.select_worktree.wt.list_worktrees", return_value=[]), \
         patch("scripts.select_worktree.wt.default_worktree_name", return_value="myrepo.worktree20260908"), \
         patch("scripts.select_worktree.wt.has_uncommitted_changes", return_value=[]):
        decision, ectx = handler(args)

    assert decision.route == Route.ASK_USER
    # resume[0] 应携带 --base 参数
    assert "--base" in decision.resume[0].params
    assert "refs/heads/develop" in decision.resume[0].params


def test_handler_no_args_asks_user_when_dirty(tmp_path):
    """无参数自动新建时有未提交变更且无 --force: 应 ask_user 询问用户。"""
    repo_dir = tmp_path / "myrepo"
    repo_dir.mkdir()
    args = _make_args(str(repo_dir))
    dirty_files = ["src/Main.java"]

    with patch("scripts.select_worktree.setup_logger"), \
         patch("scripts.select_worktree.wt.parent_dir", return_value=str(tmp_path)), \
         patch("scripts.select_worktree.wt.current_dir_name", return_value="myrepo"), \
         patch("scripts.select_worktree.wt.resolve_container", return_value=str(tmp_path / "myrepo.worktrees")), \
         patch("scripts.select_worktree.wt.find_container", return_value=None), \
         patch("scripts.select_worktree.wt.list_worktrees", return_value=[]), \
         patch("scripts.select_worktree.wt.default_worktree_name", return_value="myrepo.worktree20260908"), \
         patch("scripts.select_worktree.wt.has_uncommitted_changes", return_value=dirty_files):
        decision, ectx = handler(args)

    assert decision.route == Route.ASK_USER
    assert "未提交变更" in decision.question





# --------------------------------------------------------------------------- #
# P1-3: --base 参数支持
# --------------------------------------------------------------------------- #
def test_handler_new_with_base_ref(tmp_path):
    """--new --base refs/heads/develop: 应将 base_ref 传递给 create_new_worktree。"""
    repo_dir = tmp_path / "myrepo"
    repo_dir.mkdir()
    args = _make_args(str(repo_dir), new="feature-x", base="refs/heads/develop")

    with patch("scripts.select_worktree.setup_logger"), \
         patch("scripts.select_worktree.wt.parent_dir", return_value=str(tmp_path)), \
         patch("scripts.select_worktree.wt.current_dir_name", return_value="myrepo"), \
         patch("scripts.select_worktree.wt.resolve_container", return_value=str(tmp_path / "myrepo.worktrees")), \
         patch("scripts.select_worktree.wt.find_container", return_value=str(tmp_path / "myrepo.worktrees")), \
         patch("scripts.select_worktree.wt.validate_new_name", return_value=True), \
         patch("scripts.select_worktree.wt.has_uncommitted_changes", return_value=[]), \
         patch("scripts.select_worktree.wt.create_new_worktree") as mock_create:
        mock_create.return_value = str(tmp_path / "myrepo.worktrees" / "feature-x")
        decision, ectx = handler(args)

    assert decision.route == Route.FINISH
    mock_create.assert_called_once()
    call_args = mock_create.call_args
    assert call_args.kwargs.get("base_ref") == "refs/heads/develop" or call_args.args[5] == "refs/heads/develop"
