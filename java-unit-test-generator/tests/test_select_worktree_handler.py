"""select_worktree.handler() 单测。"""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jaut import config, worktree as wt
from jaut.cli import StepError
from jaut.models import Decision, Route
from scripts.select_worktree import handler


def _make_args(work_dir: str, **overrides) -> argparse.Namespace:
    defaults = dict(work_dir=work_dir, list=False, choice=None, new=None,
                   clear_history=False, base=None, force=False)
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


# --------------------------------------------------------------------------- #
# --clear-history: 清理历史工作树并创建新的工作树
# --------------------------------------------------------------------------- #
def test_handler_no_args_existing_offers_full_resume(tmp_path):
    """无参数且有历史 worktree: 选已有/默认新建/清理均附 resume 机读条目。"""
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
    assert "[3] 新建默认 worktree" in decision.question
    assert "[4] 清理历史工作树并创建新的工作树" in decision.question
    assert "永久删除" in decision.question   # F4
    # N+2 条: 2 个 choice + 默认新建 + 清理重建
    assert [r.option for r in decision.resume] == ["choice", "choice", "continue", "clear_history"]
    wd = str(repo_dir)
    assert decision.resume[0].params == [wd, "--choice", "1"]
    assert decision.resume[1].params == [wd, "--choice", "2"]
    assert decision.resume[0].label in decision.question   # wt1
    cont = decision.resume[2]
    assert cont.params[0] == wd and cont.params[1] == "--new" and "--force" in cont.params
    clear = decision.resume[3]
    assert clear.label == "清理历史工作树并创建新的工作树(历史树未提交内容将被永久删除)"
    assert clear.script == "select_worktree.py"
    assert clear.params[:3] == [wd, "--clear-history", "--force"]
    assert clear.label in decision.question
    assert len(decision.artifacts) == 2


def test_handler_no_args_existing_clear_resume_carries_base(tmp_path):
    """无参数 + --base 且有历史 worktree: 新建与清理的 resume 应携带 --base。"""
    repo_dir = tmp_path / "myrepo"
    repo_dir.mkdir()
    args = _make_args(str(repo_dir), base="refs/heads/develop")
    existing = [str(tmp_path / "wt1")]

    with patch("scripts.select_worktree.setup_logger"), \
         patch("scripts.select_worktree.wt.parent_dir", return_value=str(tmp_path)), \
         patch("scripts.select_worktree.wt.current_dir_name", return_value="myrepo"), \
         patch("scripts.select_worktree.wt.resolve_container", return_value=str(tmp_path / "myrepo.worktrees")), \
         patch("scripts.select_worktree.wt.find_container", return_value=str(tmp_path / "myrepo.worktrees")), \
         patch("scripts.select_worktree.wt.list_worktrees", return_value=existing):
        decision, ectx = handler(args)

    assert decision.route == Route.ASK_USER
    clear = next(r for r in decision.resume if r.option == "clear_history")
    cont = next(r for r in decision.resume if r.option == "continue")
    for opt in (clear, cont):
        assert "--base" in opt.params
        assert "refs/heads/develop" in opt.params


def test_handler_clear_history_creates_default(tmp_path):
    """--clear-history 主工作区干净: 预检 -> 清理 -> 新建, 输出 finish。"""
    repo_dir = tmp_path / "myrepo"
    repo_dir.mkdir()
    args = _make_args(str(repo_dir), clear_history=True, base="refs/heads/develop")
    created = str(tmp_path / "myrepo.worktrees" / "myrepo.worktree20260908")
    order = []

    with patch("scripts.select_worktree.setup_logger"), \
         patch("scripts.select_worktree.wt.parent_dir", return_value=str(tmp_path)), \
         patch("scripts.select_worktree.wt.current_dir_name", return_value="myrepo"), \
         patch("scripts.select_worktree.wt.resolve_container", return_value=str(tmp_path / "myrepo.worktrees")), \
         patch("scripts.select_worktree.wt.find_container", return_value=str(tmp_path / "myrepo.worktrees")), \
         patch("scripts.select_worktree.wt.list_worktrees", return_value=[str(tmp_path / "myrepo.worktrees" / "old")]), \
         patch("scripts.select_worktree.wt.default_worktree_name", return_value="myrepo.worktree20260908"), \
         patch("scripts.select_worktree.wt.has_uncommitted_changes", return_value=[]), \
         patch("scripts.select_worktree.wt.preflight_new_worktree",
               side_effect=lambda *a, **k: order.append("preflight") or "path"), \
         patch("scripts.select_worktree.wt.clear_all_worktrees") as mock_clear, \
         patch("scripts.select_worktree.wt.create_new_worktree") as mock_create:
        mock_clear.side_effect = lambda *a, **k: order.append("clear") or ["/old"]
        mock_create.side_effect = lambda *a, **k: order.append("create") or created
        decision, ectx = handler(args)

    assert decision.route == Route.FINISH
    assert order == ["preflight", "clear", "create"]
    assert mock_create.call_args.kwargs.get("base_ref") == "refs/heads/develop"
    assert "已清理 1 个历史 worktree 并新建" in decision.summary


def test_handler_clear_history_main_dirty_asks_user(tmp_path):
    """--clear-history 主工作区脏且无 --force: ask_user 确认, resume 携带 WORK_DIR + --clear-history --force。"""
    repo_dir = tmp_path / "myrepo"
    repo_dir.mkdir()
    args = _make_args(str(repo_dir), clear_history=True)

    with patch("scripts.select_worktree.setup_logger"), \
         patch("scripts.select_worktree.wt.parent_dir", return_value=str(tmp_path)), \
         patch("scripts.select_worktree.wt.current_dir_name", return_value="myrepo"), \
         patch("scripts.select_worktree.wt.resolve_container", return_value=str(tmp_path / "myrepo.worktrees")), \
         patch("scripts.select_worktree.wt.find_container", return_value=str(tmp_path / "myrepo.worktrees")), \
         patch("scripts.select_worktree.wt.has_uncommitted_changes", return_value=["src/Main.java"]), \
         patch("scripts.select_worktree.wt.clear_all_worktrees") as mock_clear, \
         patch("scripts.select_worktree.wt.create_new_worktree") as mock_create:
        decision, ectx = handler(args)

    assert decision.route == Route.ASK_USER
    assert "未提交变更" in decision.question
    assert "永久删除" in decision.question   # F4
    assert len(decision.resume) == 2
    assert decision.resume[0].option == "continue"
    assert decision.resume[0].params[:3] == [str(repo_dir), "--clear-history", "--force"]
    assert decision.resume[1].option == "terminate"
    mock_clear.assert_not_called()
    mock_create.assert_not_called()


def test_handler_clear_history_main_dirty_with_force_creates(tmp_path):
    """--clear-history 主工作区脏但带 --force: 跳过确认, 预检后清理并新建。"""
    repo_dir = tmp_path / "myrepo"
    repo_dir.mkdir()
    args = _make_args(str(repo_dir), clear_history=True, force=True)
    created = str(tmp_path / "myrepo.worktrees" / "myrepo.worktree20260908")

    with patch("scripts.select_worktree.setup_logger"), \
         patch("scripts.select_worktree.wt.parent_dir", return_value=str(tmp_path)), \
         patch("scripts.select_worktree.wt.current_dir_name", return_value="myrepo"), \
         patch("scripts.select_worktree.wt.resolve_container", return_value=str(tmp_path / "myrepo.worktrees")), \
         patch("scripts.select_worktree.wt.find_container", return_value=str(tmp_path / "myrepo.worktrees")), \
         patch("scripts.select_worktree.wt.list_worktrees", return_value=[]), \
         patch("scripts.select_worktree.wt.default_worktree_name", return_value="myrepo.worktree20260908"), \
         patch("scripts.select_worktree.wt.has_uncommitted_changes", return_value=["src/Main.java"]), \
         patch("scripts.select_worktree.wt.preflight_new_worktree", return_value="path"), \
         patch("scripts.select_worktree.wt.clear_all_worktrees") as mock_clear, \
         patch("scripts.select_worktree.wt.create_new_worktree") as mock_create:
        mock_clear.return_value = []
        mock_create.return_value = created
        decision, ectx = handler(args)

    assert decision.route == Route.FINISH
    mock_clear.assert_called_once()
    mock_create.assert_called_once()


def test_handler_clear_history_without_existing_still_creates(tmp_path):
    """零历史 worktree 时 --clear-history 幂等跳过清理, 照常新建并 finish。"""
    repo_dir = tmp_path / "myrepo"
    repo_dir.mkdir()
    args = _make_args(str(repo_dir), clear_history=True, force=True)
    created = str(tmp_path / "myrepo.worktrees" / "myrepo.worktree20260908")

    with patch("scripts.select_worktree.setup_logger"), \
         patch("scripts.select_worktree.wt.parent_dir", return_value=str(tmp_path)), \
         patch("scripts.select_worktree.wt.current_dir_name", return_value="myrepo"), \
         patch("scripts.select_worktree.wt.resolve_container", return_value=str(tmp_path / "myrepo.worktrees")), \
         patch("scripts.select_worktree.wt.find_container", return_value=None), \
         patch("scripts.select_worktree.wt.list_worktrees", return_value=[]), \
         patch("scripts.select_worktree.wt.default_worktree_name", return_value="myrepo.worktree20260908"), \
         patch("scripts.select_worktree.wt.has_uncommitted_changes", return_value=[]), \
         patch("scripts.select_worktree.wt.preflight_new_worktree", return_value="path"), \
         patch("scripts.select_worktree.wt.clear_all_worktrees") as mock_clear, \
         patch("scripts.select_worktree.wt.create_new_worktree") as mock_create:
        mock_clear.return_value = []
        mock_create.return_value = created
        decision, ectx = handler(args)

    assert decision.route == Route.FINISH
    assert "无历史 worktree" in decision.summary
    mock_clear.assert_called_once()
    mock_create.assert_called_once()


# --------------------------------------------------------------------------- #
# F1/F3/F4/F6 新增契约
# --------------------------------------------------------------------------- #
def test_handler_all_resume_params_carry_work_dir(tmp_path):
    """菜单/D/A 脏确认路径: 所有带 script 的 resume params[0] 均为 WORK_DIR 且无 --workdir。"""
    repo_dir = tmp_path / "myrepo"
    repo_dir.mkdir()
    wd = str(repo_dir)
    existing = [str(tmp_path / "wt1"), str(tmp_path / "wt2")]

    # 菜单路径
    with patch("scripts.select_worktree.setup_logger"), \
         patch("scripts.select_worktree.wt.parent_dir", return_value=str(tmp_path)), \
         patch("scripts.select_worktree.wt.current_dir_name", return_value="myrepo"), \
         patch("scripts.select_worktree.wt.resolve_container", return_value=str(tmp_path / "myrepo.worktrees")), \
         patch("scripts.select_worktree.wt.find_container", return_value=str(tmp_path / "myrepo.worktrees")), \
         patch("scripts.select_worktree.wt.list_worktrees", return_value=existing):
        menu, _ = handler(_make_args(str(repo_dir)))
    for r in menu.resume:
        if r.script:
            assert r.params[0] == wd
            assert "--workdir" not in r.params

    # D 无历史路径
    with patch("scripts.select_worktree.setup_logger"), \
         patch("scripts.select_worktree.wt.parent_dir", return_value=str(tmp_path)), \
         patch("scripts.select_worktree.wt.current_dir_name", return_value="myrepo"), \
         patch("scripts.select_worktree.wt.resolve_container", return_value=str(tmp_path / "myrepo.worktrees")), \
         patch("scripts.select_worktree.wt.find_container", return_value=None), \
         patch("scripts.select_worktree.wt.list_worktrees", return_value=[]), \
         patch("scripts.select_worktree.wt.default_worktree_name", return_value="myrepo.worktree20260908"), \
         patch("scripts.select_worktree.wt.has_uncommitted_changes", return_value=[]):
        d_path, _ = handler(_make_args(str(repo_dir)))
    for r in d_path.resume:
        if r.script:
            assert r.params[0] == wd
            assert "--workdir" not in r.params

    # A clear-history 脏确认路径
    with patch("scripts.select_worktree.setup_logger"), \
         patch("scripts.select_worktree.wt.parent_dir", return_value=str(tmp_path)), \
         patch("scripts.select_worktree.wt.current_dir_name", return_value="myrepo"), \
         patch("scripts.select_worktree.wt.resolve_container", return_value=str(tmp_path / "myrepo.worktrees")), \
         patch("scripts.select_worktree.wt.find_container", return_value=str(tmp_path / "myrepo.worktrees")), \
         patch("scripts.select_worktree.wt.has_uncommitted_changes", return_value=["a.txt"]):
        a_path, _ = handler(_make_args(str(repo_dir), clear_history=True))
    for r in a_path.resume:
        if r.script:
            assert r.params[0] == wd
            assert "--workdir" not in r.params


def test_handler_clear_history_preflight_failure_blocks_clear(tmp_path):
    """预检失败: 不 clear、不 create, StepError 透出预检错误。"""
    repo_dir = tmp_path / "myrepo"
    repo_dir.mkdir()
    args = _make_args(str(repo_dir), clear_history=True, force=True, base="no-such-ref")

    with patch("scripts.select_worktree.setup_logger"), \
         patch("scripts.select_worktree.wt.parent_dir", return_value=str(tmp_path)), \
         patch("scripts.select_worktree.wt.current_dir_name", return_value="myrepo"), \
         patch("scripts.select_worktree.wt.resolve_container", return_value=str(tmp_path / "myrepo.worktrees")), \
         patch("scripts.select_worktree.wt.find_container", return_value=str(tmp_path / "myrepo.worktrees")), \
         patch("scripts.select_worktree.wt.list_worktrees", return_value=[str(tmp_path / "old")]), \
         patch("scripts.select_worktree.wt.default_worktree_name", return_value="myrepo.worktree20260908"), \
         patch("scripts.select_worktree.wt.has_uncommitted_changes", return_value=[]), \
         patch("scripts.select_worktree.wt.preflight_new_worktree",
               side_effect=wt.WorktreeError("指定的 base ref 'no-such-ref' 不存在或无效")), \
         patch("scripts.select_worktree.wt.clear_all_worktrees") as mock_clear, \
         patch("scripts.select_worktree.wt.create_new_worktree") as mock_create:
        with pytest.raises(StepError) as exc_info:
            handler(args)

    assert "no-such-ref" in exc_info.value.decision.summary
    mock_clear.assert_not_called()
    mock_create.assert_not_called()


def test_handler_clear_history_create_failure_states_history_cleared(tmp_path):
    """预检+清理成功但 create 失败: 错误信息注明历史已清除。"""
    repo_dir = tmp_path / "myrepo"
    repo_dir.mkdir()
    args = _make_args(str(repo_dir), clear_history=True, force=True)

    with patch("scripts.select_worktree.setup_logger"), \
         patch("scripts.select_worktree.wt.parent_dir", return_value=str(tmp_path)), \
         patch("scripts.select_worktree.wt.current_dir_name", return_value="myrepo"), \
         patch("scripts.select_worktree.wt.resolve_container", return_value=str(tmp_path / "myrepo.worktrees")), \
         patch("scripts.select_worktree.wt.find_container", return_value=str(tmp_path / "myrepo.worktrees")), \
         patch("scripts.select_worktree.wt.list_worktrees", return_value=[]), \
         patch("scripts.select_worktree.wt.default_worktree_name", return_value="myrepo.worktree20260908"), \
         patch("scripts.select_worktree.wt.has_uncommitted_changes", return_value=[]), \
         patch("scripts.select_worktree.wt.preflight_new_worktree", return_value="path"), \
         patch("scripts.select_worktree.wt.clear_all_worktrees", return_value=["/old"]), \
         patch("scripts.select_worktree.wt.create_new_worktree",
               side_effect=wt.WorktreeError("git worktree add 失败: boom")):
        with pytest.raises(StepError) as exc_info:
            handler(args)

    summary = exc_info.value.decision.summary
    assert "历史 worktree 已被清除" in summary


def test_handler_menu_clear_option_warns_permanent_deletion(tmp_path):
    """菜单 [N+2] 与 clear resume label 均含永久删除警示且字符串一致。"""
    repo_dir = tmp_path / "myrepo"
    repo_dir.mkdir()
    existing = [str(tmp_path / "wt1"), str(tmp_path / "wt2")]

    with patch("scripts.select_worktree.setup_logger"), \
         patch("scripts.select_worktree.wt.parent_dir", return_value=str(tmp_path)), \
         patch("scripts.select_worktree.wt.current_dir_name", return_value="myrepo"), \
         patch("scripts.select_worktree.wt.resolve_container", return_value=str(tmp_path / "myrepo.worktrees")), \
         patch("scripts.select_worktree.wt.find_container", return_value=str(tmp_path / "myrepo.worktrees")), \
         patch("scripts.select_worktree.wt.list_worktrees", return_value=existing):
        decision, _ = handler(_make_args(str(repo_dir)))

    clear = next(r for r in decision.resume if r.option == "clear_history")
    assert "永久删除" in clear.label
    assert clear.label in decision.question


def test_handler_new_dirty_uses_effective_base_description(tmp_path):
    """--new 脏确认: question/label 使用 effective_base_description, 不硬编码基于 HEAD。"""
    repo_dir = tmp_path / "myrepo"
    repo_dir.mkdir()
    sentinel = "已有分支 worktreeX 的当前 tip"

    with patch("scripts.select_worktree.setup_logger"), \
         patch("scripts.select_worktree.wt.parent_dir", return_value=str(tmp_path)), \
         patch("scripts.select_worktree.wt.current_dir_name", return_value="myrepo"), \
         patch("scripts.select_worktree.wt.resolve_container", return_value=str(tmp_path / "myrepo.worktrees")), \
         patch("scripts.select_worktree.wt.find_container", return_value=str(tmp_path / "myrepo.worktrees")), \
         patch("scripts.select_worktree.wt.validate_new_name", return_value=True), \
         patch("scripts.select_worktree.wt.has_uncommitted_changes", return_value=["a.txt"]), \
         patch("scripts.select_worktree.wt.effective_base_description", return_value=sentinel):
        decision, _ = handler(_make_args(str(repo_dir), new="feature-x"))

    assert decision.route == Route.ASK_USER
    assert sentinel in decision.question
    assert sentinel in decision.resume[0].label
    assert "基于 HEAD" not in decision.resume[0].label


def test_handler_no_args_base_line_uses_effective_base_description(tmp_path):
    """D 路径 基于: 行使用 effective_base_description。"""
    repo_dir = tmp_path / "myrepo"
    repo_dir.mkdir()
    sentinel = "已有分支 worktreeY 的当前 tip"

    with patch("scripts.select_worktree.setup_logger"), \
         patch("scripts.select_worktree.wt.parent_dir", return_value=str(tmp_path)), \
         patch("scripts.select_worktree.wt.current_dir_name", return_value="myrepo"), \
         patch("scripts.select_worktree.wt.resolve_container", return_value=str(tmp_path / "myrepo.worktrees")), \
         patch("scripts.select_worktree.wt.find_container", return_value=None), \
         patch("scripts.select_worktree.wt.list_worktrees", return_value=[]), \
         patch("scripts.select_worktree.wt.default_worktree_name", return_value="myrepo.worktree20260908"), \
         patch("scripts.select_worktree.wt.has_uncommitted_changes", return_value=[]), \
         patch("scripts.select_worktree.wt.effective_base_description", return_value=sentinel):
        decision, _ = handler(_make_args(str(repo_dir)))

    assert f"基于: {sentinel}" in decision.question


def test_handler_list_wins_over_clear_history(tmp_path):
    """--list 优先于 --clear-history(模式分派优先级 list > choice > clear-history > new)。"""
    repo_dir = tmp_path / "myrepo"
    repo_dir.mkdir()
    args = _make_args(str(repo_dir), list=True, clear_history=True)

    with patch("scripts.select_worktree.setup_logger"), \
         patch("scripts.select_worktree.wt.parent_dir", return_value=str(tmp_path)), \
         patch("scripts.select_worktree.wt.current_dir_name", return_value="myrepo"), \
         patch("scripts.select_worktree.wt.resolve_container", return_value=str(tmp_path / "myrepo.worktrees")), \
         patch("scripts.select_worktree.wt.find_container", return_value=None), \
         patch("scripts.select_worktree.wt.list_worktrees", return_value=[]), \
         patch("scripts.select_worktree.wt.clear_all_worktrees") as mock_clear:
        decision, ectx = handler(args)

    assert decision.route == Route.FINISH
    mock_clear.assert_not_called()
