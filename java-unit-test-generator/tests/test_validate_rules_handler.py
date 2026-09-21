"""validate_rules.handler() 单测。"""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jaut import config
from jaut.cli import StepError
from jaut.models import Decision, Route, State
from scripts.validate_rules import handler


def _make_args(workdir: str, test_file: str | None = None) -> argparse.Namespace:
    return argparse.Namespace(workdir=workdir, project_root=None, test_file=test_file)


# --------------------------------------------------------------------------- #
# 成功场景
# --------------------------------------------------------------------------- #
def test_handler_passes_when_no_violations(tmp_path):
    """无违规时返回 verify_coverage 决策。"""
    test_file = tmp_path / "MyServiceTest.java"
    test_file.write_text("class MyServiceTest { @Test void test() {} }")

    state = State(
        project_root=str(tmp_path),
        target_class="com.example.MyService",
        test_class_file=str(test_file),
        git_baseline={}
    )
    args = _make_args(str(tmp_path))

    with patch("scripts.validate_rules.StateStore") as MockStore, \
         patch("scripts.validate_rules.setup_logger"), \
         patch("scripts.validate_rules.javasrc.strip_comments_and_strings", return_value="stripped"), \
         patch("scripts.validate_rules.rules.run_hard_rules", return_value=[]), \
         patch("scripts.validate_rules.prompt.default_rules_file", return_value=Path("rules.md")):
        MockStore.return_value.load.return_value = state

        decision, ectx = handler(args)

    assert decision.route == Route.VERIFY_COVERAGE
    assert "通过" in decision.summary


def test_handler_returns_write_code_on_violations(tmp_path):
    """有违规时返回 write_code 决策。"""
    test_file = tmp_path / "MyServiceTest.java"
    test_file.write_text("class MyServiceTest {}")

    state = State(
        project_root=str(tmp_path),
        target_class="com.example.MyService",
        test_class_file=str(test_file),
        git_baseline={}
    )
    violation = MagicMock()
    violation.render.return_value = "违规: 禁止 catch"

    args = _make_args(str(tmp_path))

    with patch("scripts.validate_rules.StateStore") as MockStore, \
         patch("scripts.validate_rules.setup_logger"), \
         patch("scripts.validate_rules.javasrc.strip_comments_and_strings", return_value="stripped"), \
         patch("scripts.validate_rules.rules.run_hard_rules", return_value=[violation]), \
         patch("scripts.validate_rules.prompt.default_rules_file", return_value=Path("rules.md")):
        MockStore.return_value.load.return_value = state

        decision, ectx = handler(args)

    assert decision.route == Route.WRITE_CODE
    assert "违规" in decision.summary


def test_handler_uses_test_file_from_args_over_state(tmp_path):
    """--test-file 优先于 state.test_class_file。"""
    test_file = tmp_path / "CustomTest.java"
    test_file.write_text("class CustomTest {}")

    state = State(
        project_root=str(tmp_path),
        target_class="com.example.MyService",
        test_class_file=str(tmp_path / "OtherTest.java"),
        git_baseline={}
    )
    args = _make_args(str(tmp_path), test_file=str(test_file))

    with patch("scripts.validate_rules.StateStore") as MockStore, \
         patch("scripts.validate_rules.setup_logger"), \
         patch("scripts.validate_rules.javasrc.strip_comments_and_strings", return_value="stripped"), \
         patch("scripts.validate_rules.rules.run_hard_rules", return_value=[]), \
         patch("scripts.validate_rules.prompt.default_rules_file", return_value=Path("rules.md")):
        MockStore.return_value.load.return_value = state

        decision, ectx = handler(args)

    assert decision.route == Route.VERIFY_COVERAGE


def test_handler_works_when_state_is_none(tmp_path):
    """state 为 None 时仍可凭 --test-file 校验。"""
    test_file = tmp_path / "Test.java"
    test_file.write_text("class Test {}")

    args = _make_args(str(tmp_path), test_file=str(test_file))

    with patch("scripts.validate_rules.StateStore") as MockStore, \
         patch("scripts.validate_rules.setup_logger"), \
         patch("scripts.validate_rules.javasrc.strip_comments_and_strings", return_value="stripped"), \
         patch("scripts.validate_rules.rules.run_hard_rules", return_value=[]), \
         patch("scripts.validate_rules.prompt.default_rules_file", return_value=Path("rules.md")):
        MockStore.return_value.load.return_value = None

        decision, ectx = handler(args)

    assert decision.route == Route.VERIFY_COVERAGE


# --------------------------------------------------------------------------- #
# 错误/边界场景
# --------------------------------------------------------------------------- #
def test_handler_returns_write_code_when_no_test_file(tmp_path):
    """无法定位测试文件时返回 write_code 要求创建骨架。"""
    state = State(
        project_root=str(tmp_path),
        target_class="com.example.MyService",
        test_class_file=None,
    )
    args = _make_args(str(tmp_path))

    with patch("scripts.validate_rules.StateStore") as MockStore, \
         patch("scripts.validate_rules.setup_logger"), \
         patch("scripts.validate_rules.prompt.default_rules_file", return_value=Path("rules.md")):
        MockStore.return_value.load.return_value = state

        decision, ectx = handler(args)

    assert decision.route == Route.WRITE_CODE
    assert "无法定位" in decision.summary


def test_handler_returns_write_code_when_test_file_not_exists(tmp_path):
    """测试文件不存在时返回 write_code 要求创建。"""
    state = State(
        project_root=str(tmp_path),
        target_class="com.example.MyService",
        test_class_file=str(tmp_path / "nonexistent.java"),
    )
    args = _make_args(str(tmp_path))

    with patch("scripts.validate_rules.StateStore") as MockStore, \
         patch("scripts.validate_rules.setup_logger"), \
         patch("scripts.validate_rules.prompt.default_rules_file", return_value=Path("rules.md")):
        MockStore.return_value.load.return_value = state

        decision, ectx = handler(args)

    assert decision.route == Route.WRITE_CODE
    assert "不存在" in decision.summary


def test_handler_includes_mvn_log_hint_after_first_round(tmp_path):
    """第 2 轮起(write_code)附带 mvn 日志提示。"""
    test_file = tmp_path / "Test.java"
    test_file.write_text("class Test {}")

    state = State(
        project_root=str(tmp_path),
        target_class="com.example.MyService",
        test_class_file=None,
        iteration=2,
        mvn_log=str(tmp_path / "mvn.log"),
    )
    args = _make_args(str(tmp_path))

    with patch("scripts.validate_rules.StateStore") as MockStore, \
         patch("scripts.validate_rules.setup_logger"), \
         patch("scripts.validate_rules.prompt.default_rules_file", return_value=Path("rules.md")):
        MockStore.return_value.load.return_value = state

        decision, ectx = handler(args)

    assert decision.route == Route.WRITE_CODE
    assert "mvn.log" in decision.instructions


# --------------------------------------------------------------------------- #
# 违规循环预算(问题3: 连续 N 轮未通过升级 ask_user)
# --------------------------------------------------------------------------- #
def _violating_file(tmp_path):
    test_file = tmp_path / "MyServiceTest.java"
    test_file.write_text("class MyServiceTest {}")
    return test_file


def _run_with_violations(tmp_path, state, violations):
    violation = MagicMock()
    violation.render.return_value = "违规: 禁止 catch"
    args = _make_args(str(tmp_path))
    with patch("scripts.validate_rules.StateStore") as MockStore, \
         patch("scripts.validate_rules.setup_logger"), \
         patch("scripts.validate_rules.javasrc.strip_comments_and_strings", return_value="s"), \
         patch("scripts.validate_rules.rules.run_hard_rules", return_value=violations), \
         patch("scripts.validate_rules.prompt.default_rules_file", return_value=Path("rules.md")):
        MockStore.return_value.load.return_value = state
        decision, ectx = handler(args)
    return decision


def test_violation_increments_and_persists_streak(tmp_path):
    """有违规: 计数 +1 落盘, 仍走 write_code。"""
    state = State(project_root=str(tmp_path), target_class="c",
                 test_class_file=str(_violating_file(tmp_path)), git_baseline={})
    decision = _run_with_violations(tmp_path, state, [MagicMock()])
    assert decision.route == Route.WRITE_CODE
    assert state.validate_fail_streak == 1


def test_streak_at_limit_escalates_and_resets_window(tmp_path):
    """连续达上限(5): 升级 ask_user 并携带 resume, 计数复位为全新窗口。"""
    state = State(project_root=str(tmp_path), target_class="c",
                 test_class_file=str(_violating_file(tmp_path)), git_baseline={})
    state.validate_fail_streak = config.VALIDATE_FAIL_STREAK_LIMIT - 1
    decision = _run_with_violations(tmp_path, state, [MagicMock()])
    assert decision.route == Route.ASK_USER
    assert decision.status == "needs_input"
    assert str(config.VALIDATE_FAIL_STREAK_LIMIT) in decision.question
    # 升级后全新窗口: 计数已复位
    assert state.validate_fail_streak == 0
    # resume: 继续修复(validate_rules)/跳过方法(make_plan --skip-current)/终止
    options = {r.option: r for r in decision.resume}
    assert set(options) == {"continue", "skip_method", "terminate"}
    assert options["continue"].script == "validate_rules.py"
    assert options["skip_method"].params == ["--skip-current"]
    assert options["terminate"].script == ""


def test_missing_test_file_counts_toward_streak(tmp_path):
    """测试文件缺失同样计入违规循环(是 write_code 失败的一种形态)。"""
    state = State(project_root=str(tmp_path), target_class="c",
                 test_class_file=str(tmp_path / "nonexistent.java"))
    state.validate_fail_streak = config.VALIDATE_FAIL_STREAK_LIMIT - 1
    args = _make_args(str(tmp_path))
    with patch("scripts.validate_rules.StateStore") as MockStore, \
         patch("scripts.validate_rules.setup_logger"), \
         patch("scripts.validate_rules.prompt.default_rules_file", return_value=Path("rules.md")):
        MockStore.return_value.load.return_value = state
        decision, ectx = handler(args)
    assert decision.route == Route.ASK_USER


def test_pass_resets_streak(tmp_path):
    """校验通过: 计数清零并落盘。"""
    test_file = tmp_path / "MyServiceTest.java"
    test_file.write_text("class MyServiceTest {}")
    state = State(project_root=str(tmp_path), target_class="c",
                 test_class_file=str(test_file), git_baseline={})
    state.validate_fail_streak = 3
    args = _make_args(str(tmp_path))
    with patch("scripts.validate_rules.StateStore") as MockStore, \
         patch("scripts.validate_rules.setup_logger"), \
         patch("scripts.validate_rules.javasrc.strip_comments_and_strings", return_value="s"), \
         patch("scripts.validate_rules.rules.run_hard_rules", return_value=[]), \
         patch("scripts.validate_rules.prompt.default_rules_file", return_value=Path("rules.md")):
        MockStore.return_value.load.return_value = state
        decision, ectx = handler(args)
    assert decision.route == Route.VERIFY_COVERAGE
    assert state.validate_fail_streak == 0


def test_stateless_mode_does_not_count(tmp_path):
    """state 为 None(仅凭 --test-file)时不计数, 维持无状态用法。"""
    test_file = tmp_path / "Test.java"
    test_file.write_text("class Test {}")
    args = _make_args(str(tmp_path), test_file=str(test_file))
    with patch("scripts.validate_rules.StateStore") as MockStore, \
         patch("scripts.validate_rules.setup_logger"), \
         patch("scripts.validate_rules.javasrc.strip_comments_and_strings", return_value="s"), \
         patch("scripts.validate_rules.rules.run_hard_rules", return_value=[MagicMock()]), \
         patch("scripts.validate_rules.prompt.default_rules_file", return_value=Path("rules.md")):
        MockStore.return_value.load.return_value = None
        decision, ectx = handler(args)
    assert decision.route == Route.WRITE_CODE  # 不升级、不落盘
