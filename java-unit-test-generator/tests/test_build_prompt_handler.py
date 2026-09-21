"""build_prompt.handler() 单测。"""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jaut import config
from jaut.cli import StepError
from jaut.models import Decision, Route, State
from scripts.build_prompt import handler


def _make_args(workdir: str) -> argparse.Namespace:
    return argparse.Namespace(workdir=workdir, project_root=None)


# --------------------------------------------------------------------------- #
# 成功场景
# --------------------------------------------------------------------------- #
def test_handler_returns_write_code_decision(tmp_path):
    """正常情况: state 有 current_method, 返回 write_code 决策。"""
    state = State(
        project_root=str(tmp_path),
        target_class="com.example.MyService",
        current_method=MagicMock(name="doSomething"),
        iteration=2,
    )
    with patch("scripts.build_prompt.StateStore") as MockStore, \
         patch("scripts.build_prompt.setup_logger"), \
         patch("scripts.build_prompt.prompt.PromptBuilder") as MockBuilder:
        MockStore.return_value.load.return_value = state
        MockBuilder.return_value.build.return_value = "prompt text"
        decision, ectx = handler(_make_args(str(tmp_path)))

    assert decision.route in (Route.WRITE_CODE, Route.BUILD_PROMPT)
    assert ectx.workdir == tmp_path
    assert ectx.state is state


def test_handler_builds_prompt_with_rules_file(tmp_path):
    """确认 PromptBuilder 使用了正确的 rules 文件路径。"""
    state = State(
        project_root=str(tmp_path),
        target_class="com.example.MyService",
        current_method=MagicMock(name="doSomething"),
    )
    with patch("scripts.build_prompt.StateStore") as MockStore, \
         patch("scripts.build_prompt.setup_logger"), \
         patch("scripts.build_prompt.prompt.PromptBuilder") as MockBuilder:
        MockStore.return_value.load.return_value = state
        MockBuilder.return_value.build.return_value = "prompt"
        handler(_make_args(str(tmp_path)))

    call_args = MockBuilder.call_args
    rules_file = call_args[0][0]
    assert rules_file.name == "UnitTestRules.md"


# --------------------------------------------------------------------------- #
# 状态错误
# --------------------------------------------------------------------------- #
def test_handler_raises_when_state_missing(tmp_path):
    """state.json 不存在时应抛出 StepError。"""
    with patch("scripts.build_prompt.StateStore") as MockStore, \
         patch("scripts.build_prompt.setup_logger"):
        MockStore.return_value.load.return_value = None
        with pytest.raises(StepError) as exc_info:
            handler(_make_args(str(tmp_path)))

    assert exc_info.value.decision.exit_code == config.EXIT_STATE
    assert "state.json" in exc_info.value.decision.summary


def test_handler_raises_when_no_current_method(tmp_path):
    """state 存在但 current_method 为 None 时应抛出 StepError。"""
    state = State(
        project_root=str(tmp_path),
        target_class="com.example.MyService",
        current_method=None,
    )
    with patch("scripts.build_prompt.StateStore") as MockStore, \
         patch("scripts.build_prompt.setup_logger"):
        MockStore.return_value.load.return_value = state
        with pytest.raises(StepError) as exc_info:
            handler(_make_args(str(tmp_path)))

    assert exc_info.value.decision.exit_code == config.EXIT_STATE
    assert "current_method" in exc_info.value.decision.summary
