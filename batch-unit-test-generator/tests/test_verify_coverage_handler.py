"""verify_coverage.handler() 单测。"""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jaut import config
from jaut.cli import StepError
from jaut.models import Decision, MethodCoverage, MethodKey, MethodStatus, Route, State, TestResult
from scripts.verify_coverage import handler


def _make_args(workdir: str) -> argparse.Namespace:
    return argparse.Namespace(workdir=workdir, project_root=None)


def _make_state(tmp_path, **overrides) -> State:
    defaults = dict(
        project_root=str(tmp_path),
        target_class="com.example.MyService",
        current_method=MagicMock(name="doSomething"),
        module=".",
        mvn_log=str(tmp_path / "mvn.log"),
        jacoco_version=config.DEFAULT_JACOCO_VERSION,
        test_class_simple="MyServiceTest",
        test_class_file=str(tmp_path / "MyServiceTest.java"),
        iteration=1,
        global_iteration=5,
        threshold=80.0,
        class_coverage=MagicMock(rate=50.0),
    )
    defaults.update(overrides)
    return MagicMock(spec=State, **defaults)


def _locked_mock():
    """创建正确的 locked context manager mock。"""
    mock = MagicMock()
    mock.__enter__ = MagicMock(return_value=None)
    mock.__exit__ = MagicMock(return_value=False)
    return mock


# --------------------------------------------------------------------------- #
# 成功场景
# --------------------------------------------------------------------------- #
def test_handler_returns_build_prompt_when_not_met(tmp_path):
    """覆盖率未达标时返回 build_prompt 决策继续迭代。"""
    state = _make_state(tmp_path)
    method_entry = MagicMock(spec=MethodCoverage)
    method_entry.key = state.current_method
    method_entry.rate = 30.0
    method_entry.status = MethodStatus.PENDING

    mock_test = TestResult(tests=5, failures=0, errors=0, skipped=0, report_found=True, parse_errors=0)

    with patch("scripts.verify_coverage.StateStore") as MockStore, \
         patch("scripts.verify_coverage.setup_logger"), \
         patch("scripts.verify_coverage.maven.clean_jacoco_dirs"), \
         patch("scripts.verify_coverage.maven.clean_surefire_dirs", return_value=[]), \
         patch("scripts.verify_coverage.maven.run_single_cov_with_fallback") as mock_cov, \
         patch("scripts.verify_coverage.surefire.parse_surefire_reports", return_value=mock_test), \
         patch("scripts.verify_coverage.jacoco.report_paths", return_value=(Path("x.xml"), Path("x.csv"))), \
         patch("scripts.verify_coverage.Path.is_file", return_value=True), \
         patch("scripts.verify_coverage._record_observation") as mock_record, \
         patch("scripts.verify_coverage.decisions.decide_after_verify") as mock_decide:
        MockStore.return_value.locked.return_value = _locked_mock()
        MockStore.return_value.load.return_value = state
        mock_cov.return_value = (MagicMock(ok=True), None)
        mock_record.return_value = (method_entry, 30.0)
        mock_decide.return_value = Decision(
            status="success", exit_code=config.EXIT_CONTINUE, summary="build_prompt",
            route=Route.BUILD_PROMPT, reason="未达标")

        decision, ectx = handler(_make_args(str(tmp_path)))

    assert decision.route == Route.BUILD_PROMPT


def test_handler_returns_make_plan_when_met(tmp_path):
    """覆盖率达标且测试全绿时返回 make_plan 决策。"""
    state = _make_state(tmp_path)
    method_entry = MagicMock(spec=MethodCoverage)
    method_entry.key = state.current_method
    method_entry.rate = 95.0
    method_entry.status = MethodStatus.DONE

    mock_test = TestResult(tests=5, failures=0, errors=0, skipped=0, report_found=True, parse_errors=0)

    with patch("scripts.verify_coverage.StateStore") as MockStore, \
         patch("scripts.verify_coverage.setup_logger"), \
         patch("scripts.verify_coverage.maven.clean_jacoco_dirs"), \
         patch("scripts.verify_coverage.maven.clean_surefire_dirs", return_value=[]), \
         patch("scripts.verify_coverage.maven.run_single_cov_with_fallback") as mock_cov, \
         patch("scripts.verify_coverage.surefire.parse_surefire_reports", return_value=mock_test), \
         patch("scripts.verify_coverage.jacoco.report_paths", return_value=(Path("x.xml"), Path("x.csv"))), \
         patch("scripts.verify_coverage.Path.is_file", return_value=True), \
         patch("scripts.verify_coverage._record_observation") as mock_record, \
         patch("scripts.verify_coverage.decisions.decide_after_verify") as mock_decide:
        MockStore.return_value.locked.return_value = _locked_mock()
        MockStore.return_value.load.return_value = state
        mock_cov.return_value = (MagicMock(ok=True), None)
        mock_record.return_value = (method_entry, 80.0)
        mock_decide.return_value = Decision(
            status="success", exit_code=config.EXIT_OK, summary="make_plan",
            route=Route.MAKE_PLAN, reason="方法完成")

        decision, ectx = handler(_make_args(str(tmp_path)))

    assert decision.route == Route.MAKE_PLAN


def test_handler_marks_done_when_decision_says_so(tmp_path):
    """当决策要求 mark_done 时，应将方法状态标记为 DONE。"""
    state = _make_state(tmp_path)
    method_entry = MagicMock(spec=MethodCoverage)
    method_entry.key = state.current_method
    method_entry.rate = 95.0
    method_entry.status = MethodStatus.PENDING

    mock_test = TestResult(tests=5, failures=0, errors=0, skipped=0, report_found=True, parse_errors=0)

    with patch("scripts.verify_coverage.StateStore") as MockStore, \
         patch("scripts.verify_coverage.setup_logger"), \
         patch("scripts.verify_coverage.maven.clean_jacoco_dirs"), \
         patch("scripts.verify_coverage.maven.clean_surefire_dirs", return_value=[]), \
         patch("scripts.verify_coverage.maven.run_single_cov_with_fallback") as mock_cov, \
         patch("scripts.verify_coverage.surefire.parse_surefire_reports", return_value=mock_test), \
         patch("scripts.verify_coverage.jacoco.report_paths", return_value=(Path("x.xml"), Path("x.csv"))), \
         patch("scripts.verify_coverage.Path.is_file", return_value=True), \
         patch("scripts.verify_coverage._record_observation") as mock_record, \
         patch("scripts.verify_coverage.decisions.decide_after_verify") as mock_decide:
        MockStore.return_value.locked.return_value = _locked_mock()
        MockStore.return_value.load.return_value = state
        mock_cov.return_value = (MagicMock(ok=True), None)
        mock_record.return_value = (method_entry, 80.0)
        mock_decision = MagicMock()
        mock_decision.mark_done = True
        mock_decision.reset_trajectory = False
        mock_decision.route = Route.MAKE_PLAN
        mock_decide.return_value = mock_decision

        handler(_make_args(str(tmp_path)))

    assert method_entry.status == MethodStatus.DONE


def test_handler_saves_state_after_decision(tmp_path):
    """决策落地后应原子保存 state。"""
    state = _make_state(tmp_path)
    method_entry = MagicMock(spec=MethodCoverage)
    method_entry.key = MagicMock()
    method_entry.key.label.return_value = "doSomething()V"
    method_entry.rate = 50.0

    mock_test = TestResult(tests=5, failures=0, errors=0, skipped=0, report_found=True, parse_errors=0)

    with patch("scripts.verify_coverage.StateStore") as MockStore, \
         patch("scripts.verify_coverage.setup_logger"), \
         patch("scripts.verify_coverage.maven.clean_jacoco_dirs"), \
         patch("scripts.verify_coverage.maven.clean_surefire_dirs", return_value=[]), \
         patch("scripts.verify_coverage.maven.run_single_cov_with_fallback") as mock_cov, \
         patch("scripts.verify_coverage.surefire.parse_surefire_reports", return_value=mock_test), \
         patch("scripts.verify_coverage.jacoco.report_paths", return_value=(Path("x.xml"), Path("x.csv"))), \
         patch("scripts.verify_coverage.Path.is_file", return_value=True), \
         patch("scripts.verify_coverage._record_observation") as mock_record, \
         patch("scripts.verify_coverage.decisions.decide_after_verify") as mock_decide:
        MockStore.return_value.locked.return_value = _locked_mock()
        MockStore.return_value.load.return_value = state
        mock_cov.return_value = (MagicMock(ok=True), None)
        mock_record.return_value = (method_entry, 50.0)
        mock_decide.return_value = MagicMock(mark_done=False, reset_trajectory=False, route=Route.BUILD_PROMPT)

        handler(_make_args(str(tmp_path)))

    MockStore.return_value.save.assert_called_once_with(state)


# --------------------------------------------------------------------------- #
# 错误场景
# --------------------------------------------------------------------------- #
def test_handler_raises_when_state_missing(tmp_path):
    """state.json 不存在时应抛出 StepError。"""
    with patch("scripts.verify_coverage.StateStore") as MockStore, \
         patch("scripts.verify_coverage.setup_logger"):
        MockStore.return_value.locked.return_value = _locked_mock()
        MockStore.return_value.load.return_value = None

        with pytest.raises(StepError) as exc_info:
            handler(_make_args(str(tmp_path)))

    assert exc_info.value.decision.exit_code == config.EXIT_STATE
    assert "state.json" in exc_info.value.decision.summary


def test_handler_raises_when_no_current_method(tmp_path):
    """state 存在但 current_method 为 None 时应抛出 StepError。"""
    state = _make_state(tmp_path)
    state.current_method = None

    with patch("scripts.verify_coverage.StateStore") as MockStore, \
         patch("scripts.verify_coverage.setup_logger"):
        MockStore.return_value.locked.return_value = _locked_mock()
        MockStore.return_value.load.return_value = state

        with pytest.raises(StepError) as exc_info:
            handler(_make_args(str(tmp_path)))

    assert exc_info.value.decision.exit_code == config.EXIT_STATE
    assert "current_method" in exc_info.value.decision.summary


def test_handler_raises_when_mvn_not_found(tmp_path):
    """覆盖率采集失败时应抛出 StepError。"""
    state = _make_state(tmp_path)

    with patch("scripts.verify_coverage.StateStore") as MockStore, \
         patch("scripts.verify_coverage.setup_logger"), \
         patch("scripts.verify_coverage.maven.clean_jacoco_dirs"), \
         patch("scripts.verify_coverage.maven.clean_surefire_dirs", return_value=[]), \
         patch("scripts.verify_coverage.maven.run_single_cov_with_fallback") as mock_cov, \
         patch("scripts.verify_coverage.maven.parse_compile_errors", return_value=[]):
        mock_cov.return_value = (MagicMock(ok=False), None)
        MockStore.return_value.locked.return_value = _locked_mock()
        MockStore.return_value.load.return_value = state

        with pytest.raises(StepError) as exc_info:
            handler(_make_args(str(tmp_path)))

    assert exc_info.value.decision.exit_code == config.EXIT_ERROR
    assert "失败" in exc_info.value.decision.summary


def test_handler_routes_to_write_code_on_compile_error(tmp_path):
    """P0-6 修复: 编译失败时应路由到 write_code 而非 ask_user，且轮次计数递增。"""
    from jaut.maven import CompileError
    
    state = _make_state(tmp_path, iteration=2, global_iteration=5)
    method_key = MagicMock()
    method_key.label.return_value = "doSomething()V"
    state.current_method = method_key
    state.test_history = []
    
    method_entry = MagicMock(spec=MethodCoverage)
    method_entry.key = method_key
    method_entry.rate = 30.0
    method_entry.status = MethodStatus.PENDING
    
    compile_errors = [
        CompileError(file="MyService.java", line=42, col=10, message="cannot find symbol"),
        CompileError(file="MyService.java", line=55, col=5, message="incompatible types"),
    ]

    with patch("scripts.verify_coverage.StateStore") as MockStore, \
         patch("scripts.verify_coverage.setup_logger"), \
         patch("scripts.verify_coverage.maven.clean_jacoco_dirs"), \
         patch("scripts.verify_coverage.maven.clean_surefire_dirs", return_value=[]), \
         patch("scripts.verify_coverage.maven.run_single_cov_with_fallback") as mock_cov, \
         patch("scripts.verify_coverage.maven.parse_compile_errors", return_value=compile_errors) as mock_parse:
        MockStore.return_value.locked.return_value = _locked_mock()
        MockStore.return_value.load.return_value = state
        mock_cov.return_value = (MagicMock(ok=False), None)
        
        # 模拟 find_method 返回 None(首次遇到该方法)
        state.find_method = MagicMock(return_value=None)
        state.methods = []

        decision, ectx = handler(_make_args(str(tmp_path)))

    # 验证: 路由到 WRITE_CODE
    assert decision.route == Route.WRITE_CODE
    assert decision.exit_code == config.EXIT_CONTINUE
    assert "编译失败" in decision.summary
    assert "2 个错误" in decision.summary
    
    # 验证: 轮次计数递增
    assert state.iteration == 3  # 原来是2，现在应该是3
    assert state.global_iteration == 6  # 原来是5，现在应该是6
    
    # 验证: instructions 包含编译错误详情
    assert "MyService.java:42:10" in decision.instructions
    assert "MyService.java:55:5" in decision.instructions
    
    # 验证: state 被保存
    MockStore.return_value.save.assert_called_once_with(state)


def test_handler_compile_error_increments_test_history(tmp_path):
    """P0-6 修复: 编译失败时应将错误信息记录到 test_history。"""
    from jaut.maven import CompileError
    
    state = _make_state(tmp_path, iteration=1, global_iteration=1)
    method_key = MagicMock()
    method_key.label.return_value = "doSomething()V"
    state.current_method = method_key
    state.test_history = []
    
    method_entry = MagicMock(spec=MethodCoverage)
    method_entry.key = method_key
    method_entry.rate = 0.0
    method_entry.status = MethodStatus.PENDING
    
    compile_errors = [CompileError(file="Test.java", line=10, col=5, message="syntax error")]

    with patch("scripts.verify_coverage.StateStore") as MockStore, \
         patch("scripts.verify_coverage.setup_logger"), \
         patch("scripts.verify_coverage.maven.clean_jacoco_dirs"), \
         patch("scripts.verify_coverage.maven.clean_surefire_dirs", return_value=[]), \
         patch("scripts.verify_coverage.maven.run_single_cov_with_fallback") as mock_cov, \
         patch("scripts.verify_coverage.maven.parse_compile_errors", return_value=compile_errors):
        MockStore.return_value.locked.return_value = _locked_mock()
        MockStore.return_value.load.return_value = state
        mock_cov.return_value = (MagicMock(ok=False), None)
        state.find_method = MagicMock(return_value=None)
        state.methods = []

        handler(_make_args(str(tmp_path)))

    # 验证: test_history 被追加
    assert len(state.test_history) == 1
    assert state.test_history[0]["method"] == "doSomething()V"
    assert state.test_history[0]["round"] == 2
    assert "compile_errors" in state.test_history[0]
    assert "Test.java:10:5" in state.test_history[0]["compile_errors"][0]


def test_handler_fallback_success(tmp_path):
    """快速路径失败但降级成功时不应抛出 StepError。"""
    state = _make_state(tmp_path)
    method_entry = MagicMock(spec=MethodCoverage)
    method_entry.key = state.current_method
    method_entry.rate = 80.0
    method_entry.status = MethodStatus.DONE

    mock_test = TestResult(tests=5, failures=0, errors=0, skipped=0, report_found=True, parse_errors=0)

    with patch("scripts.verify_coverage.StateStore") as MockStore, \
         patch("scripts.verify_coverage.setup_logger"), \
         patch("scripts.verify_coverage.maven.clean_jacoco_dirs"), \
         patch("scripts.verify_coverage.maven.clean_surefire_dirs", return_value=[]), \
         patch("scripts.verify_coverage.maven.run_single_cov_with_fallback") as mock_cov, \
         patch("scripts.verify_coverage.surefire.parse_surefire_reports", return_value=mock_test), \
         patch("scripts.verify_coverage.jacoco.report_paths", return_value=(Path("x.xml"), Path("x.csv"))), \
         patch("scripts.verify_coverage.Path.is_file", return_value=True), \
         patch("scripts.verify_coverage._record_observation") as mock_record, \
         patch("scripts.verify_coverage.decisions.decide_after_verify") as mock_decide:
        MockStore.return_value.locked.return_value = _locked_mock()
        MockStore.return_value.load.return_value = state
        # 快速路径失败, 降级成功
        mock_cov.return_value = (MagicMock(ok=True), "/tmp/fast.log")
        mock_record.return_value = (method_entry, 80.0)
        mock_decide.return_value = Decision(
            status="success", exit_code=config.EXIT_OK, summary="make_plan",
            route=Route.MAKE_PLAN, reason="方法完成")

        decision, ectx = handler(_make_args(str(tmp_path)))

    assert decision.route == Route.MAKE_PLAN
    # 确认使用了降级路径(通过 log_path 非 None 可知)
    mock_cov.assert_called_once()
