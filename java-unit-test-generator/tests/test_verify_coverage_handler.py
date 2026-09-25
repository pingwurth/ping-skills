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
         patch("scripts.verify_coverage.maven.run_fast_single_cov") as mock_fast, \
         patch("scripts.verify_coverage.maven.run_mvn_test_with_jacoco") as mock_fallback, \
         patch("scripts.verify_coverage.maven.parse_compile_errors", return_value=[]), \
         patch("scripts.verify_coverage.surefire.parse_surefire_reports", return_value=mock_test), \
         patch("scripts.verify_coverage.jacoco.report_paths", return_value=(Path("x.xml"), Path("x.csv"))), \
         patch("scripts.verify_coverage.Path.is_file", return_value=True), \
         patch("scripts.verify_coverage._record_observation") as mock_record, \
         patch("scripts.verify_coverage.decisions.decide_after_verify") as mock_decide:
        MockStore.return_value.locked.return_value = _locked_mock()
        MockStore.return_value.load.return_value = state
        mock_fast.return_value = MagicMock(ok=True)
        mock_fallback.assert_not_called()
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
         patch("scripts.verify_coverage.maven.run_fast_single_cov") as mock_fast, \
         patch("scripts.verify_coverage.maven.run_mvn_test_with_jacoco") as mock_fallback, \
         patch("scripts.verify_coverage.maven.parse_compile_errors", return_value=[]), \
         patch("scripts.verify_coverage.surefire.parse_surefire_reports", return_value=mock_test), \
         patch("scripts.verify_coverage.jacoco.report_paths", return_value=(Path("x.xml"), Path("x.csv"))), \
         patch("scripts.verify_coverage.Path.is_file", return_value=True), \
         patch("scripts.verify_coverage._record_observation") as mock_record, \
         patch("scripts.verify_coverage.decisions.decide_after_verify") as mock_decide:
        MockStore.return_value.locked.return_value = _locked_mock()
        MockStore.return_value.load.return_value = state
        mock_fast.return_value = MagicMock(ok=True)
        mock_fallback.assert_not_called()
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
         patch("scripts.verify_coverage.maven.run_fast_single_cov") as mock_fast, \
         patch("scripts.verify_coverage.maven.run_mvn_test_with_jacoco") as mock_fallback, \
         patch("scripts.verify_coverage.maven.parse_compile_errors", return_value=[]), \
         patch("scripts.verify_coverage.surefire.parse_surefire_reports", return_value=mock_test), \
         patch("scripts.verify_coverage.jacoco.report_paths", return_value=(Path("x.xml"), Path("x.csv"))), \
         patch("scripts.verify_coverage.Path.is_file", return_value=True), \
         patch("scripts.verify_coverage._record_observation") as mock_record, \
         patch("scripts.verify_coverage.decisions.decide_after_verify") as mock_decide:
        MockStore.return_value.locked.return_value = _locked_mock()
        MockStore.return_value.load.return_value = state
        mock_fast.return_value = MagicMock(ok=True)
        mock_fallback.assert_not_called()
        mock_record.return_value = (method_entry, 80.0)
        mock_decision = MagicMock()
        mock_decision.mark_done = True
        mock_decision.reset_trajectory = False
        mock_decision.auto_skip_method = False
        mock_decision.skip_all_pending = False
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
         patch("scripts.verify_coverage.maven.run_fast_single_cov") as mock_fast, \
         patch("scripts.verify_coverage.maven.run_mvn_test_with_jacoco") as mock_fallback, \
         patch("scripts.verify_coverage.maven.parse_compile_errors", return_value=[]), \
         patch("scripts.verify_coverage.surefire.parse_surefire_reports", return_value=mock_test), \
         patch("scripts.verify_coverage.jacoco.report_paths", return_value=(Path("x.xml"), Path("x.csv"))), \
         patch("scripts.verify_coverage.Path.is_file", return_value=True), \
         patch("scripts.verify_coverage._record_observation") as mock_record, \
         patch("scripts.verify_coverage.decisions.decide_after_verify") as mock_decide:
        MockStore.return_value.locked.return_value = _locked_mock()
        MockStore.return_value.load.return_value = state
        mock_fast.return_value = MagicMock(ok=True)
        mock_fallback.assert_not_called()
        mock_record.return_value = (method_entry, 50.0)
        mock_decide.return_value = MagicMock(mark_done=False, reset_trajectory=False,
                                             auto_skip_method=False, skip_all_pending=False,
                                             route=Route.BUILD_PROMPT)

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


def test_handler_raises_when_both_coverage_methods_fail(tmp_path):
    """fast-single-cov.sh 与 mvn 降级方案均失败时应抛出 StepError。"""
    state = _make_state(tmp_path)

    with patch("scripts.verify_coverage.StateStore") as MockStore, \
         patch("scripts.verify_coverage.setup_logger"), \
         patch("scripts.verify_coverage.maven.clean_jacoco_dirs"), \
         patch("scripts.verify_coverage.maven.clean_surefire_dirs", return_value=[]), \
         patch("scripts.verify_coverage.maven.run_fast_single_cov", return_value=MagicMock(ok=False)), \
         patch("scripts.verify_coverage.maven.run_mvn_test_with_jacoco", return_value=MagicMock(ok=False)):
        MockStore.return_value.locked.return_value = _locked_mock()
        MockStore.return_value.load.return_value = state

        with pytest.raises(StepError) as exc_info:
            handler(_make_args(str(tmp_path)))

    assert exc_info.value.decision.exit_code == config.EXIT_ERROR
    assert "覆盖率采集失败" in exc_info.value.decision.summary or "mvn" in exc_info.value.decision.summary.lower()


def test_handler_raises_when_mvn_fails(tmp_path):
    """mvn 执行失败(非编译错误)时应抛出 StepError(ask_user)。"""
    state = _make_state(tmp_path)

    with patch("scripts.verify_coverage.StateStore") as MockStore, \
         patch("scripts.verify_coverage.setup_logger"), \
         patch("scripts.verify_coverage.maven.clean_jacoco_dirs"), \
         patch("scripts.verify_coverage.maven.clean_surefire_dirs", return_value=[]), \
         patch("scripts.verify_coverage.maven.run_fast_single_cov", return_value=MagicMock(ok=False)), \
         patch("scripts.verify_coverage.maven.run_mvn_test_with_jacoco", return_value=MagicMock(ok=False)), \
         patch("scripts.verify_coverage.maven.parse_compile_errors", return_value=[]):
        MockStore.return_value.locked.return_value = _locked_mock()
        MockStore.return_value.load.return_value = state

        with pytest.raises(StepError) as exc_info:
            handler(_make_args(str(tmp_path)))

    assert exc_info.value.decision.exit_code == config.EXIT_ERROR
    assert "依赖解析或环境问题" in exc_info.value.decision.summary


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
         patch("scripts.verify_coverage.maven.run_fast_single_cov", return_value=MagicMock(ok=False)), \
         patch("scripts.verify_coverage.maven.run_mvn_test_with_jacoco", return_value=MagicMock(ok=False)), \
         patch("scripts.verify_coverage.maven.parse_compile_errors", return_value=compile_errors) as mock_parse:
        MockStore.return_value.locked.return_value = _locked_mock()
        MockStore.return_value.load.return_value = state
        
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
         patch("scripts.verify_coverage.maven.run_fast_single_cov", return_value=MagicMock(ok=False)), \
         patch("scripts.verify_coverage.maven.run_mvn_test_with_jacoco", return_value=MagicMock(ok=False)), \
         patch("scripts.verify_coverage.maven.parse_compile_errors", return_value=compile_errors):
        MockStore.return_value.locked.return_value = _locked_mock()
        MockStore.return_value.load.return_value = state
        state.find_method = MagicMock(return_value=None)
        state.methods = []

        handler(_make_args(str(tmp_path)))

    # 验证: test_history 被追加
    assert len(state.test_history) == 1
    assert state.test_history[0]["method"] == "doSomething()V"
    assert state.test_history[0]["round"] == 2
    assert "compile_errors" in state.test_history[0]
    assert "Test.java:10:5" in state.test_history[0]["compile_errors"][0]


# --------------------------------------------------------------------------- #
# 预算耗尽自动跳过(不再 ask_user)
# --------------------------------------------------------------------------- #
def _real_state(tmp_path) -> State:
    """真实 State(非 Mock): 自动跳过要落地 status/skip_reason, 需要真实容器。"""
    m1 = MethodCoverage(key=MethodKey("foo", "()V"), covered=5, missed=5)
    m2 = MethodCoverage(key=MethodKey("bar", "()V"), covered=3, missed=7)
    return State(
        project_root=str(tmp_path),
        target_class="com.example.MyService",
        current_method=m1.key,
        module=".",
        mvn_log=str(tmp_path / "mvn.log"),
        test_class_file=str(tmp_path / "MyServiceTest.java"),
        test_class_simple="MyServiceTest",
        iteration=8,
        global_iteration=8,
        threshold=80.0,
        methods=[m1, m2],
    )


def _run_with_decision(tmp_path, state, decision, method_entry, before=50.0):
    mock_test = TestResult(tests=1, failures=0, errors=0, report_found=True, parse_errors=0)
    with patch("scripts.verify_coverage.StateStore") as MockStore, \
         patch("scripts.verify_coverage.setup_logger"), \
         patch("scripts.verify_coverage.maven.clean_jacoco_dirs"), \
         patch("scripts.verify_coverage.maven.clean_surefire_dirs", return_value=[]), \
         patch("scripts.verify_coverage.maven.run_fast_single_cov") as mock_fast, \
         patch("scripts.verify_coverage.maven.parse_compile_errors", return_value=[]), \
         patch("scripts.verify_coverage.surefire.parse_surefire_reports", return_value=mock_test), \
         patch("scripts.verify_coverage.jacoco.report_paths", return_value=(Path("x.xml"), Path("x.csv"))), \
         patch("scripts.verify_coverage.Path.is_file", return_value=True), \
         patch("scripts.verify_coverage._record_observation", return_value=(method_entry, before)), \
         patch("scripts.verify_coverage.decisions.decide_after_verify", return_value=decision):
        MockStore.return_value.locked.return_value = _locked_mock()
        MockStore.return_value.load.return_value = state
        mock_fast.return_value = MagicMock(ok=True)
        return handler(_make_args(str(tmp_path)))


def test_handler_auto_skip_marks_method_skipped_with_reason(tmp_path):
    """auto_skip_method 决策: 当前方法改 skipped 并写入原因, 计数清零, 回 make_plan。"""
    state = _real_state(tmp_path)
    state.validate_fail_streak = 3
    decision = Decision(status="success", exit_code=config.EXIT_CONTINUE,
                        summary="预算耗尽, 自动跳过", route=Route.MAKE_PLAN,
                        reason="预算耗尽, 自动跳过",
                        auto_skip_method=True,
                        auto_skip_reason="单方法迭代已达上限 8 轮仍未达标(当前 50.0%)",
                        reset_trajectory=True)

    result, _ = _run_with_decision(tmp_path, state, decision, state.methods[0])

    assert result.route == Route.MAKE_PLAN
    assert state.methods[0].status == MethodStatus.SKIPPED
    assert state.methods[0].skip_reason == "单方法迭代已达上限 8 轮仍未达标(当前 50.0%)"
    assert state.validate_fail_streak == 0
    # 其余方法不受影响
    assert state.methods[1].status == MethodStatus.PENDING


def test_handler_skip_all_pending_marks_every_pending_method(tmp_path):
    """skip_all_pending(全局预算耗尽): 当前方法连同其余 pending 一并跳过。"""
    state = _real_state(tmp_path)
    decision = Decision(status="success", exit_code=config.EXIT_CONTINUE,
                        summary="全局预算耗尽", route=Route.MAKE_PLAN,
                        reason="全局预算耗尽, 跳过全部未达标方法",
                        auto_skip_method=True,
                        auto_skip_reason="全局迭代已达上限 30 轮, 未达标方法不再迭代",
                        skip_all_pending=True)

    result, _ = _run_with_decision(tmp_path, state, decision, state.methods[0])

    assert result.route == Route.MAKE_PLAN
    for m in state.methods:
        assert m.status == MethodStatus.SKIPPED
        assert m.skip_reason == "全局迭代已达上限 30 轮, 未达标方法不再迭代"
