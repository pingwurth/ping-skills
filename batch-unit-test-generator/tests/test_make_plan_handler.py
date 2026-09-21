"""make_plan.handler() 单测。"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jaut import config
from jaut.cli import StepError
from jaut.models import Decision, MethodCoverage, MethodKey, MethodStatus, Route, State, TestOutcome
from scripts.make_plan import handler


def _make_args(workdir: str, **over) -> argparse.Namespace:
    args = argparse.Namespace(workdir=workdir, project_root=None,
                              skip_current=False, set_threshold=None, grant_rounds=None)
    for key, value in over.items():
        setattr(args, key, value)
    return args


def _make_method(name: str, desc: str = "()V", covered: int = 0, missed: int = 10) -> MethodCoverage:
    return MethodCoverage(key=MethodKey(name=name, desc=desc), covered=covered, missed=missed)


# --------------------------------------------------------------------------- #
# 成功场景
# --------------------------------------------------------------------------- #
def test_handler_first_run_generates_plan(tmp_path):
    """首次运行(state 无 plan): 生成计划并返回 build_prompt 决策。"""
    method = _make_method("doSomething", "(I)V", covered=5, missed=10)
    state = State(
        project_root=str(tmp_path),
        target_class="com.example.MyService",
        methods=[method],
        current_method=None,
        iteration=0,
    )

    with patch("scripts.make_plan.StateStore") as MockStore, \
         patch("scripts.make_plan.setup_logger"), \
         patch("scripts.make_plan.decisions.decide_after_plan") as mock_decide:
        MockStore.return_value.locked.return_value.__enter__ = MagicMock(return_value=None)
        MockStore.return_value.locked.return_value.__exit__ = MagicMock(return_value=False)
        MockStore.return_value.load.return_value = state
        MockStore.return_value.coverage_path.return_value = tmp_path / "coverage.json"
        mock_decide.return_value = Decision(
            status="success", exit_code=0, summary="build_prompt",
            route=Route.BUILD_PROMPT, reason="有未达标方法")

        decision, ectx = handler(_make_args(str(tmp_path)))

    assert state.plan is not None
    assert state.current_method == method.key


def test_handler_advances_cursor_to_next_method(tmp_path):
    """后续运行: 推进 current_method 游标到下一个未达标方法。"""
    method1 = _make_method("method1", covered=5, missed=10)
    method1.status = MethodStatus.DONE
    method2 = _make_method("method2", covered=3, missed=12)
    state = State(
        project_root=str(tmp_path),
        target_class="com.example.MyService",
        methods=[method1, method2],
        current_method=method1.key,
        iteration=5,
        plan={"target_class": "com.example.MyService", "methods": []},
    )

    with patch("scripts.make_plan.StateStore") as MockStore, \
         patch("scripts.make_plan.setup_logger"), \
         patch("scripts.make_plan.decisions.decide_after_plan") as mock_decide:
        MockStore.return_value.locked.return_value.__enter__ = MagicMock(return_value=None)
        MockStore.return_value.locked.return_value.__exit__ = MagicMock(return_value=False)
        MockStore.return_value.load.return_value = state
        MockStore.return_value.coverage_path.return_value = tmp_path / "coverage.json"
        mock_decide.return_value = Decision(
            status="success", exit_code=0, summary="build_prompt",
            route=Route.BUILD_PROMPT, reason="有未达标方法")

        decision, ectx = handler(_make_args(str(tmp_path)))

    assert state.current_method == method2.key
    assert state.iteration == 0  # 切换方法时轮次清零


def test_handler_queue_empty_triggers_final_check(tmp_path):
    """队列空且未终验: 触发 final_check。"""
    method = _make_method("method1", covered=10, missed=0)
    method.status = MethodStatus.DONE
    state = State(
        project_root=str(tmp_path),
        target_class="com.example.MyService",
        methods=[method],
        current_method=method.key,
        plan={"target_class": "com.example.MyService", "methods": []},
    )

    with patch("scripts.make_plan.StateStore") as MockStore, \
         patch("scripts.make_plan.setup_logger"), \
         patch("scripts.make_plan.decisions.decide_after_plan") as mock_decide:
        MockStore.return_value.locked.return_value.__enter__ = MagicMock(return_value=None)
        MockStore.return_value.locked.return_value.__exit__ = MagicMock(return_value=False)
        MockStore.return_value.load.return_value = state
        MockStore.return_value.coverage_path.return_value = tmp_path / "coverage.json"
        mock_decide.return_value = Decision(
            status="success", exit_code=0, summary="final_check",
            route=Route.FINAL_CHECK, reason="队列空, 需终验")

        decision, ectx = handler(_make_args(str(tmp_path)))

    assert decision.route == Route.FINAL_CHECK


def test_handler_queue_empty_after_final_check_finishes(tmp_path):
    """队列空且已终验: finish。"""
    method = _make_method("method1", covered=10, missed=0)
    method.status = MethodStatus.DONE
    state = State(
        project_root=str(tmp_path),
        target_class="com.example.MyService",
        methods=[method],
        current_method=method.key,
        final_checked=True,
        plan={"target_class": "com.example.MyService", "methods": []},
    )

    with patch("scripts.make_plan.StateStore") as MockStore, \
         patch("scripts.make_plan.setup_logger"), \
         patch("scripts.make_plan.decisions.decide_after_plan") as mock_decide:
        MockStore.return_value.locked.return_value.__enter__ = MagicMock(return_value=None)
        MockStore.return_value.locked.return_value.__exit__ = MagicMock(return_value=False)
        MockStore.return_value.load.return_value = state
        MockStore.return_value.coverage_path.return_value = tmp_path / "coverage.json"
        mock_decide.return_value = Decision(
            status="success", exit_code=0, summary="finish",
            route=Route.FINISH, reason="全部完成")

        decision, ectx = handler(_make_args(str(tmp_path)))

    assert decision.route == Route.FINISH


def test_handler_finish_decision_carries_rendered_report(tmp_path):
    """队列空且已终验(不 mock 决策): finish 决策携带基于 state 渲染的报告。"""
    method = _make_method("method1", covered=10, missed=0)
    method.status = MethodStatus.DONE
    method.initial_rate = 50.0
    state = State(
        project_root=str(tmp_path),
        target_class="com.example.MyService",
        methods=[method],
        current_method=method.key,
        final_checked=True,
        plan={"target_class": "com.example.MyService", "methods": []},
        coverage_history=[{"phase": "init", "class_rate": 50.0}],
        final_test_summary={"tests": 3, "failures": 0, "errors": 0, "skipped": 0},
        worktree_branch="worktree20260908",
    )

    with patch("scripts.make_plan.StateStore") as MockStore, \
         patch("scripts.make_plan.setup_logger"):
        MockStore.return_value.locked.return_value.__enter__ = MagicMock(return_value=None)
        MockStore.return_value.locked.return_value.__exit__ = MagicMock(return_value=False)
        MockStore.return_value.load.return_value = state
        MockStore.return_value.coverage_path.return_value = tmp_path / "coverage.json"

        decision, ectx = handler(_make_args(str(tmp_path)))

    assert decision.route == Route.FINISH
    assert decision.report is not None
    assert "最终状态: PASS" in decision.report
    assert "worktree20260908" in decision.report


# --------------------------------------------------------------------------- #
# 错误场景
# --------------------------------------------------------------------------- #
def test_handler_raises_when_state_missing(tmp_path):
    """state.json 不存在时应抛出 StepError。"""
    with patch("scripts.make_plan.StateStore") as MockStore, \
         patch("scripts.make_plan.setup_logger"):
        MockStore.return_value.locked.return_value.__enter__ = MagicMock(return_value=None)
        MockStore.return_value.locked.return_value.__exit__ = MagicMock(return_value=False)
        MockStore.return_value.load.return_value = None

        with pytest.raises(StepError) as exc_info:
            handler(_make_args(str(tmp_path)))

    assert exc_info.value.decision.exit_code == config.EXIT_STATE
    assert "state.json" in exc_info.value.decision.summary


# --------------------------------------------------------------------------- #
# 用户决策落地(SKILL.md §6: --skip-current / --set-threshold / --grant-rounds)
# --------------------------------------------------------------------------- #
@contextmanager
def _patched_store(state):
    with patch("scripts.make_plan.StateStore") as MockStore:
        MockStore.return_value.locked.return_value.__enter__ = MagicMock(return_value=None)
        MockStore.return_value.locked.return_value.__exit__ = MagicMock(return_value=False)
        MockStore.return_value.load.return_value = state
        yield MockStore.return_value


def test_handler_skip_current_marks_skipped_and_advances(tmp_path):
    """--skip-current: 当前方法状态改 skipped, 游标推进到下一个未达标方法。"""
    low = _make_method("low", covered=5, missed=5)      # 50%
    high = _make_method("high", covered=9, missed=1)    # 90%
    state = State(
        project_root=str(tmp_path),
        target_class="com.example.MyService",
        methods=[low, high],
        current_method=low.key,
        iteration=7,
        method_round_bonus=2,
        plan={"target_class": "com.example.MyService", "threshold": 80.0, "methods": []},
    )

    with patch("scripts.make_plan.setup_logger"), _patched_store(state) as store:
        handler(_make_args(str(tmp_path), skip_current=True))

    assert low.status == MethodStatus.SKIPPED
    assert state.current_method == high.key   # 推进到下一个未达标方法
    assert state.iteration == 0               # 切换方法时轮次清零
    assert state.method_round_bonus == 0     # 单方法窗口复位
    store.save.assert_called()


def test_handler_skip_current_without_current_method_errors(tmp_path):
    """--skip-current 但 current_method 为空: 状态错误。"""
    state = State(
        project_root=str(tmp_path),
        target_class="com.example.MyService",
        methods=[_make_method("m")],
        current_method=None,
        plan={"target_class": "com.example.MyService", "methods": []},
    )

    with patch("scripts.make_plan.setup_logger"), _patched_store(state):
        with pytest.raises(StepError) as exc_info:
            handler(_make_args(str(tmp_path), skip_current=True))

    assert exc_info.value.decision.exit_code == config.EXIT_STATE


def test_handler_grant_rounds_extends_both_budget_windows(tmp_path):
    """--grant-rounds: 两个预算窗口各 +N, 轨迹复位, 当前方法保持。"""
    method = _make_method("low", covered=5, missed=5)
    method.round_rates = [50.0, 50.0, 50.0]
    method.round_test_results = [TestOutcome(1, 0)] * 3
    state = State(
        project_root=str(tmp_path),
        target_class="com.example.MyService",
        methods=[method],
        current_method=method.key,
        iteration=config.METHOD_ROUND_BUDGET,
        global_iteration=config.GLOBAL_ROUND_BUDGET,
        validate_fail_streak=4,
        plan={"target_class": "com.example.MyService", "threshold": 80.0, "methods": []},
    )

    with patch("scripts.make_plan.setup_logger"), _patched_store(state) as store:
        handler(_make_args(str(tmp_path), grant_rounds=3))

    assert state.method_round_bonus == 3
    assert state.global_round_bonus == 3
    assert state.validate_fail_streak == 0
    assert method.round_rates == [] and method.round_test_results == []  # 全新窗口
    assert state.current_method == method.key  # 未切换方法
    store.save.assert_called()


def test_handler_grant_rounds_rejects_non_positive(tmp_path):
    """--grant-rounds 非正数: 状态错误。"""
    state = State(project_root=str(tmp_path), target_class="c",
                 methods=[_make_method("m")], current_method=None,
                 plan={"target_class": "c", "methods": []})
    with patch("scripts.make_plan.setup_logger"), _patched_store(state):
        with pytest.raises(StepError):
            handler(_make_args(str(tmp_path), grant_rounds=0))


def test_handler_set_threshold_updates_and_reclassifies(tmp_path):
    """--set-threshold: 更新门槛并按新门槛重评估(pending 晋升 / fail-closed 不晋升)。"""
    # rate 90% 的 done 方法, 新门槛 80: 达标不打回(打回场景由提高门槛用例覆盖)
    done_below = _make_method("doneBelow", covered=9, missed=1)
    done_below.status = MethodStatus.DONE
    done_below.round_rates = [90.0]
    # rate 85% 的 pending 方法, 最近一轮测试绿: 新门槛 80 -> 晋升 done
    pending_above = _make_method("pendingAbove", covered=17, missed=3)
    pending_above.round_test_results = [TestOutcome(0, 0)]
    # rate 85% 的 pending 方法, 但最近一轮测试失败: 保持 pending(fail-closed)
    pending_red = _make_method("pendingRed", covered=17, missed=3)
    pending_red.round_test_results = [TestOutcome(2, 0)]
    # rate 85% 的 pending 方法, 无测试轨迹: 保持 pending(交由 verify 复核)
    pending_no_history = _make_method("pendingNoHistory", covered=17, missed=3)
    # 抽象方法(covered+missed==0)不受影响
    abstract_m = _make_method("abstractM", covered=0, missed=0)

    state = State(
        project_root=str(tmp_path),
        target_class="com.example.MyService",
        methods=[done_below, pending_above, pending_red, pending_no_history, abstract_m],
        current_method=pending_above.key,
        plan={"target_class": "com.example.MyService", "threshold": 80.0, "methods": []},
    )

    with patch("scripts.make_plan.setup_logger"), _patched_store(state):
        handler(_make_args(str(tmp_path), set_threshold=80.0))

    assert state.threshold == 80.0
    assert state.plan["threshold"] == 80.0
    assert done_below.status == MethodStatus.DONE          # 90% >= 80, 不打回
    assert pending_above.status == MethodStatus.DONE       # 85% >= 80 且最近一轮绿
    assert pending_red.status == MethodStatus.PENDING      # 最近一轮失败, 不晋升
    assert pending_no_history.status == MethodStatus.PENDING  # 无测试轨迹, 不晋升


def test_handler_set_threshold_raises_method_back_to_pending(tmp_path):
    """提高门槛: 已 done 但覆盖率低于新门槛的方法打回 pending 并清轨迹。"""
    done_below = _make_method("doneBelow", covered=9, missed=1)  # 90%
    done_below.status = MethodStatus.DONE
    done_below.round_rates = [90.0]
    state = State(
        project_root=str(tmp_path),
        target_class="com.example.MyService",
        methods=[done_below],
        current_method=done_below.key,
        plan={"target_class": "com.example.MyService", "threshold": 80.0, "methods": []},
    )

    with patch("scripts.make_plan.setup_logger"), _patched_store(state):
        handler(_make_args(str(tmp_path), set_threshold=95.0))

    assert done_below.status == MethodStatus.PENDING
    assert done_below.round_rates == []
    assert state.threshold == 95.0


def test_handler_set_threshold_rejects_out_of_range(tmp_path):
    """门槛越界(0 或 >100): 状态错误。"""
    state = State(project_root=str(tmp_path), target_class="c",
                 methods=[_make_method("m")], current_method=None,
                 plan={"target_class": "c", "methods": []})
    with patch("scripts.make_plan.setup_logger"), _patched_store(state):
        for bad in (0.0, 100.5, -1.0):
            with pytest.raises(StepError):
                handler(_make_args(str(tmp_path), set_threshold=bad))


def test_handler_user_decision_flags_are_mutually_exclusive(tmp_path):
    """三个用户决策参数同时给出: 状态错误。"""
    state = State(project_root=str(tmp_path), target_class="c",
                 methods=[_make_method("m")], current_method=None,
                 plan={"target_class": "c", "methods": []})
    with patch("scripts.make_plan.setup_logger"), _patched_store(state):
        with pytest.raises(StepError) as exc_info:
            handler(_make_args(str(tmp_path), skip_current=True, grant_rounds=3))

    assert "互斥" in exc_info.value.decision.summary
