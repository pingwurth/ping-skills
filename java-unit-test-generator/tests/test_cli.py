"""cli 统一 CLI 脚手架单测。"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jaut import config
from jaut.cli import (
    EmitContext,
    StepError,
    _internal_error_decision,
    add_common_args,
    require_workdir,
    resolve_workdir,
    run_cli,
)
from jaut.models import Decision, Route


# --------------------------------------------------------------------------- #
# StepError
# --------------------------------------------------------------------------- #
def test_step_error_stores_decision():
    d = Decision(status="failed", exit_code=config.EXIT_ERROR, summary="boom",
                 route=Route.ASK_USER, reason="boom", question="boom")
    err = StepError(d)
    assert err.decision is d
    assert str(err) == "boom"


def test_step_error_state_error():
    err = StepError.state_error("state missing", question="please provide state")
    assert err.decision.exit_code == config.EXIT_STATE
    assert err.decision.status == "failed"
    assert err.decision.route == Route.ASK_USER
    assert "state missing" in err.decision.summary
    assert "please provide state" in err.decision.question


def test_step_error_state_error_default_question():
    err = StepError.state_error("state missing")
    assert err.decision.question == "state missing"


def test_step_error_exec_error():
    err = StepError.exec_error("mvn failed", artifacts=["/path/to/log"])
    assert err.decision.exit_code == config.EXIT_ERROR
    assert err.decision.status == "failed"
    assert err.decision.route == Route.ASK_USER
    assert err.decision.artifacts == ["/path/to/log"]


def test_step_error_exec_error_default_artifacts():
    err = StepError.exec_error("mvn failed")
    assert err.decision.artifacts == []


def test_step_error_abort_factory():
    err = StepError.abort("缺少索引", "缺少 CodeGraph 索引, 彻底中止任务",
                          message="请执行 codegraph init")
    assert err.decision.status == "failed"
    assert err.decision.exit_code == config.EXIT_ERROR
    assert err.decision.route == Route.ABORT
    assert err.decision.reason == "缺少 CodeGraph 索引, 彻底中止任务"
    assert err.decision.question == "请执行 codegraph init"
    assert err.decision.resume == []   # abort 无 resume


def test_step_error_abort_message_fallback_to_summary():
    for empty in (None, ""):
        err = StepError.abort("summary 文案", "reason 文案", message=empty)
        assert err.decision.question == "summary 文案"


# --------------------------------------------------------------------------- #
# EmitContext
# --------------------------------------------------------------------------- #
def test_emit_context_defaults():
    ctx = EmitContext()
    assert ctx.workdir is None
    assert ctx.state is None
    assert ctx.scripts_dir is not None


def test_emit_context_with_values(tmp_path):
    state = MagicMock()
    ctx = EmitContext(workdir=tmp_path, state=state, scripts_dir=Path("/custom"))
    assert ctx.workdir == tmp_path
    assert ctx.state is state
    assert ctx.scripts_dir == Path("/custom")


# --------------------------------------------------------------------------- #
# add_common_args
# --------------------------------------------------------------------------- #
def test_add_common_args_adds_workdir():
    parser = argparse.ArgumentParser()
    add_common_args(parser)
    args = parser.parse_args(["--workdir", "/some/path"])
    assert args.workdir == "/some/path"


def test_add_common_args_adds_project_root():
    parser = argparse.ArgumentParser()
    add_common_args(parser)
    args = parser.parse_args(["--project-root", "/project"])
    assert args.project_root == "/project"


def test_add_common_args_defaults_none():
    parser = argparse.ArgumentParser()
    add_common_args(parser)
    args = parser.parse_args([])
    assert args.workdir is None
    assert args.project_root is None


# --------------------------------------------------------------------------- #
# resolve_workdir
# --------------------------------------------------------------------------- #
def test_resolve_workdir_from_workdir(tmp_path):
    args = argparse.Namespace(workdir=str(tmp_path), project_root=None)
    result = resolve_workdir(args)
    assert result == tmp_path.resolve()


def test_resolve_workdir_from_project_root(tmp_path):
    args = argparse.Namespace(workdir=None, project_root=str(tmp_path))
    result = resolve_workdir(args)
    # default_workdir 会追加 .agent/java-unit-test-generator
    assert result is not None
    assert ".agent" in str(result)


def test_resolve_workdir_prefers_workdir(tmp_path):
    args = argparse.Namespace(workdir=str(tmp_path), project_root="/other")
    result = resolve_workdir(args)
    assert result == tmp_path.resolve()


def test_resolve_workdir_returns_none_when_both_missing():
    args = argparse.Namespace(workdir=None, project_root=None)
    result = resolve_workdir(args)
    assert result is None


# --------------------------------------------------------------------------- #
# require_workdir
# --------------------------------------------------------------------------- #
def test_require_workdir_returns_path(tmp_path):
    args = argparse.Namespace(workdir=str(tmp_path), project_root=None)
    result = require_workdir(args)
    assert result == tmp_path.resolve()


def test_require_workdir_raises_when_missing():
    args = argparse.Namespace(workdir=None, project_root=None)
    with pytest.raises(StepError) as exc_info:
        require_workdir(args)
    assert exc_info.value.decision.exit_code == config.EXIT_STATE
    assert "--workdir" in exc_info.value.decision.summary


# --------------------------------------------------------------------------- #
# _internal_error_decision
# --------------------------------------------------------------------------- #
def test_internal_error_decision():
    exc = ValueError("something broke")
    d = _internal_error_decision(exc)
    assert d.exit_code == config.EXIT_ERROR
    assert d.status == "failed"
    assert d.route == Route.ASK_USER
    assert "something broke" in d.summary
    assert "something broke" in d.question


# --------------------------------------------------------------------------- #
# run_cli
# --------------------------------------------------------------------------- #
def _add_arguments(parser):
    """测试用 add_arguments: 添加共享参数。"""
    add_common_args(parser)


def _make_handler(decision=None, ectx=None, raise_step_error=False, raise_exception=False):
    """构造 handler 函数。"""
    def handler(args):
        if raise_step_error:
            raise StepError.state_error("handler error")
        if raise_exception:
            raise ValueError("handler exception")
        d = decision or Decision(status="success", exit_code=config.EXIT_OK,
                                 summary="ok", route=Route.FINISH, reason="done")
        return d, ectx or EmitContext()
    return handler


def test_run_cli_success(tmp_path, capsys):
    handler = _make_handler()
    with patch("jaut.cli.setup_logger"):
        rc = run_cli("test_script", _add_arguments, handler,
                     argv=["--workdir", str(tmp_path)])
    assert rc == config.EXIT_OK


def test_run_cli_step_error(tmp_path, capsys):
    handler = _make_handler(raise_step_error=True)
    with patch("jaut.cli.setup_logger"):
        rc = run_cli("test_script", _add_arguments, handler,
                     argv=["--workdir", str(tmp_path)])
    assert rc == config.EXIT_STATE


def test_run_cli_exception(tmp_path, capsys):
    handler = _make_handler(raise_exception=True)
    with patch("jaut.cli.setup_logger"):
        rc = run_cli("test_script", _add_arguments, handler,
                     argv=["--workdir", str(tmp_path)])
    assert rc == config.EXIT_ERROR


def test_run_cli_handler_sets_ectx(tmp_path, capsys):
    ectx = EmitContext(workdir=tmp_path, state=MagicMock())
    handler = _make_handler(ectx=ectx)
    with patch("jaut.cli.setup_logger") as mock_logger:
        rc = run_cli("test_script", _add_arguments, handler,
                     argv=["--workdir", str(tmp_path)])
    assert rc == config.EXIT_OK
    mock_logger.assert_called_once()


def test_run_cli_logger_init_failure_ignored(tmp_path, capsys):
    handler = _make_handler(ectx=EmitContext(workdir=tmp_path))
    with patch("jaut.cli.setup_logger", side_effect=OSError("no write access")):
        rc = run_cli("test_script", _add_arguments, handler,
                     argv=["--workdir", str(tmp_path)])
    # logger 初始化失败不应影响退出码
    assert rc == config.EXIT_OK


def test_run_cli_transition_error_fallback(tmp_path, capsys):
    handler = _make_handler()
    # 第一次调用抛异常, 第二次返回正常值(兜底逻辑)
    with patch("jaut.cli.setup_logger"), \
         patch("jaut.cli.transitions.build_next_step") as mock_build:
        mock_build.side_effect = [ValueError("bad transition"), {"type": "finish"}]
        rc = run_cli("test_script", _add_arguments, handler,
                     argv=["--workdir", str(tmp_path)])
    # 路由异常应被兜底为 EXIT_ERROR
    assert rc == config.EXIT_ERROR
    assert mock_build.call_count == 2


def test_run_cli_add_arguments_called(tmp_path, capsys):
    called = []
    def add_arguments(parser):
        called.append(True)
        add_common_args(parser)
        parser.add_argument("--custom")
    handler = _make_handler()
    with patch("jaut.cli.setup_logger"):
        run_cli("test_script", add_arguments, handler,
                argv=["--workdir", str(tmp_path), "--custom", "value"])
    assert len(called) == 1


def test_run_cli_emits_protocol(tmp_path, capsys):
    handler = _make_handler()
    with patch("jaut.cli.setup_logger"), \
         patch("jaut.cli.protocol.emit") as mock_emit:
        run_cli("test_script", _add_arguments, handler,
                argv=["--workdir", str(tmp_path)])
    mock_emit.assert_called_once()
    payload = mock_emit.call_args[0][0]
    assert "next_step" in payload


def test_run_cli_no_workdir_still_runs(tmp_path, capsys):
    """handler 不设置 workdir 时仍可运行(只是不初始化 logger)。"""
    ectx = EmitContext()  # workdir=None
    handler = _make_handler(ectx=ectx)
    with patch("jaut.cli.setup_logger") as mock_logger:
        rc = run_cli("test_script", _add_arguments, handler, argv=[])
    assert rc == config.EXIT_OK
    mock_logger.assert_not_called()
