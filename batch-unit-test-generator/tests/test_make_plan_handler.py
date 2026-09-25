"""make_plan.handler() 单测。"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jaut import config, decisions
from jaut.cli import StepError
from jaut.models import (ClassCoverage, Decision, MethodCoverage, MethodKey,
                         MethodStatus, Route, State, TestOutcome)
from scripts.make_plan import handler


def _make_args(workdir: str, **over) -> argparse.Namespace:
    args = argparse.Namespace(workdir=workdir, project_root=None,
                              skip_current=False, set_threshold=None, grant_rounds=None,
                              unskip=None)
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
        threshold=80.0,
        class_coverage=ClassCoverage.of(85, 15),  # 终验结论与当前口径一致
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


def test_handler_stale_final_check_reruns_final_check(tmp_path):
    """终验结论过期(覆盖率/门槛口径漂移): 按未终验处理, 重走 final-check 实测。

    根因: final_checked 由上一轮 PASS 持久化, 调门槛/终验后覆盖率回落时队列仍空,
    直接按默认 met=True 渲染收尾报告会输出与实测相反的"最终状态: PASS"。
    """
    method = _make_method("method1", covered=10, missed=0)
    method.status = MethodStatus.DONE
    state = State(
        project_root=str(tmp_path),
        target_class="com.example.MyService",
        threshold=90.0,                             # 门槛上调后终验结论过期
        class_coverage=ClassCoverage.of(85, 15),    # 85% < 90%
        methods=[method],
        current_method=method.key,
        final_checked=True,                         # 上一轮 80% 门槛时终验通过
        plan={"target_class": "com.example.MyService", "methods": []},
    )

    with patch("scripts.make_plan.StateStore") as MockStore, \
         patch("scripts.make_plan.setup_logger"):
        MockStore.return_value.locked.return_value.__enter__ = MagicMock(return_value=None)
        MockStore.return_value.locked.return_value.__exit__ = MagicMock(return_value=False)
        MockStore.return_value.load.return_value = state
        MockStore.return_value.coverage_path.return_value = tmp_path / "coverage.json"

        decision, ectx = handler(_make_args(str(tmp_path)))

    assert decision.route == Route.FINAL_CHECK
    assert decision.report is None


def test_handler_stale_final_check_method_mode_uses_target_scope(tmp_path):
    """method 模式的终验结论口径是目标方法本身: 类级覆盖率低于门槛不算漂移。"""
    target = _make_method("bar", covered=10, missed=0)
    target.status = MethodStatus.DONE
    state = State(
        project_root=str(tmp_path),
        target_class="com.example.MyService",
        target_method="bar",
        threshold=80.0,
        class_coverage=ClassCoverage.of(20, 80),    # 类级远未达门槛, method 模式无关
        methods=[target],
        current_method=target.key,
        final_checked=True,
        plan={"target_class": "com.example.MyService", "methods": []},
        final_test_summary={"tests": 3, "failures": 0, "errors": 0, "skipped": 0},
    )

    with patch("scripts.make_plan.StateStore") as MockStore, \
         patch("scripts.make_plan.setup_logger"):
        MockStore.return_value.locked.return_value.__enter__ = MagicMock(return_value=None)
        MockStore.return_value.locked.return_value.__exit__ = MagicMock(return_value=False)
        MockStore.return_value.load.return_value = state
        MockStore.return_value.coverage_path.return_value = tmp_path / "coverage.json"

        decision, ectx = handler(_make_args(str(tmp_path)))

    assert decision.route == Route.FINISH          # 目标方法达标, 结论仍有效
    assert decision.report is not None
    assert "最终状态: PASS" in decision.report


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
# 用户决策落地(SKILL.md §6: --skip-current / --set-threshold / --grant-rounds / --unskip)
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


def test_handler_method_group_keeps_auto_skipped_methods_terminal(tmp_path):
    """方法组过滤: 带 skip_reason 的自动跳过方法不被 --method-group 复活。"""
    auto = _make_method("autoSkipped")
    auto.status = MethodStatus.SKIPPED
    auto.skip_reason = "单方法迭代已达上限 8 轮仍未达标(当前 50.0%)"
    in_group = _make_method("groupMethod")     # 组内残留跳过(无 skip_reason): 应复活
    in_group.status = MethodStatus.SKIPPED
    outside = _make_method("outsideMethod")    # 组外 pending: 应跳过
    state = State(
        project_root=str(tmp_path),
        target_class="com.example.Big",
        methods=[auto, in_group, outside],
        current_method=None,
        plan={"target_class": "com.example.Big", "methods": []},
    )

    with patch("scripts.make_plan.setup_logger"), _patched_store(state):
        handler(_make_args(str(tmp_path), method_group="groupMethod"))

    assert auto.status == MethodStatus.SKIPPED        # 终态保持, 不被复活
    assert auto.skip_reason is not None
    assert in_group.status == MethodStatus.PENDING    # 组内无原因跳过 -> 复活
    assert outside.status == MethodStatus.SKIPPED     # 组外 pending -> 本轮跳过
    assert outside.skip_reason is None


# --------------------------------------------------------------------------- #
# --unskip 恢复被跳过的方法(SKILL.md §6)
# --------------------------------------------------------------------------- #
def _skipped_method(name: str, covered: int = 5, missed: int = 5,
                    reason: str = "单方法迭代已达上限 8 轮仍未达标(当前 50.0%)",
                    desc: str = "()V"):
    m = _make_method(name, desc=desc, covered=covered, missed=missed)
    m.status = MethodStatus.SKIPPED
    m.skip_reason = reason
    m.round_rates = [50.0] * config.METHOD_ROUND_BUDGET
    m.round_test_results = [TestOutcome(0, 0)] * config.METHOD_ROUND_BUDGET
    return m


def _batch_state(methods, **over) -> State:
    defaults = dict(
        project_root="/tmp", target_class="com.example.Big", methods=methods,
        current_method=methods[0].key if methods else None, batch_mode=True,
        plan={"target_class": "com.example.Big", "threshold": 80.0, "methods": []})
    defaults.update(over)
    return State(**defaults)


def test_handler_unskip_reopens_class_budget_window(tmp_path):
    """--unskip: 类级预算耗尽时同步重开类级窗口, 否则 verify_coverage 会立刻再次跳过。"""
    method = _skipped_method("doStuff",
                             reason="类级预算耗尽(30/30 轮), 未达标方法不再迭代")
    state = _batch_state(
        [method], iteration=config.METHOD_ROUND_BUDGET,
        global_iteration=config.GLOBAL_ROUND_BUDGET, validate_fail_streak=4,
        class_round_used=config.batch_class_round_budget())
    assert state.class_budget_exhausted is True

    with patch("scripts.make_plan.setup_logger"), _patched_store(state):
        decision = handler(_make_args(str(tmp_path), unskip="doStuff"))[0]

    assert method.status == MethodStatus.PENDING
    assert method.skip_reason is None
    assert method.round_rates == [] and method.round_test_results == []
    assert state.iteration == 0                    # 游标未切换, 就地重开单方法窗口
    assert state.method_round_bonus == 0
    assert state.global_round_bonus == config.GLOBAL_ROUND_BUDGET
    assert state.class_round_bonus == config.batch_class_round_budget()
    assert state.class_budget_exhausted is False  # 类级预检查不再命中
    assert state.validate_fail_streak == 0
    assert decision.route == Route.BUILD_PROMPT


def test_handler_unskip_exploration_mode_keeps_doubled_budget(tmp_path):
    """探索模式: 重开窗口后有效预算 = 翻倍基础预算 + 追加窗口。"""
    method = _skipped_method("doStuff")
    base = config.batch_class_round_budget()
    state = _batch_state([method], exploration_mode=True,
                         class_round_used=base * config.EXPLORATION_MODE_MULTIPLIER)

    with patch("scripts.make_plan.setup_logger"), _patched_store(state):
        handler(_make_args(str(tmp_path), unskip="doStuff"))

    assert state.class_round_bonus == base
    assert state.class_round_budget == base * config.EXPLORATION_MODE_MULTIPLIER + base
    assert state.class_budget_exhausted is False


def test_handler_unskip_combines_with_grant_rounds(tmp_path):
    """--unskip 与 --grant-rounds 组合: 恢复后再追加 N 轮窗口。"""
    method = _skipped_method("doStuff")
    state = _batch_state([method])

    with patch("scripts.make_plan.setup_logger"), _patched_store(state):
        handler(_make_args(str(tmp_path), unskip="doStuff", grant_rounds=8))

    assert method.status == MethodStatus.PENDING
    assert state.global_round_bonus == config.GLOBAL_ROUND_BUDGET + 8
    assert state.method_round_bonus == 8


def test_handler_unskip_switches_cursor_to_revived_method(tmp_path):
    """--unskip: 当前方法是别的方法时, 游标切到复活的低覆盖率方法。"""
    current = _make_method("current", covered=12, missed=3)    # 80%, 仍 pending
    revived = _skipped_method("revived", covered=1, missed=9)  # 10%, 排序更靠前
    state = _batch_state([current, revived], current_method=current.key, iteration=5)

    with patch("scripts.make_plan.setup_logger"), _patched_store(state):
        handler(_make_args(str(tmp_path), unskip="revived"))

    assert revived.status == MethodStatus.PENDING
    assert state.current_method == revived.key
    assert state.iteration == 0


@pytest.mark.parametrize("names, match", [
    ("noSuchMethod", "找不到方法"),
    ("doneMethod", "只有被跳过的方法才能恢复"),
    ("outOfScope", "无跳过原因"),
    (" , ", "需要方法名列表"),
])
def test_handler_unskip_rejects_invalid_targets(tmp_path, names, match):
    """--unskip 非法目标(不存在/非 skipped/无跳过原因/空列表): 状态错误。"""
    done = _make_method("doneMethod", covered=10, missed=0)
    done.status = MethodStatus.DONE
    out_of_scope = _make_method("outOfScope")  # 方法组范围外, 无 skip_reason
    out_of_scope.status = MethodStatus.SKIPPED
    state = _batch_state([done, out_of_scope], current_method=None)

    with patch("scripts.make_plan.setup_logger"), _patched_store(state):
        with pytest.raises(StepError) as exc_info:
            handler(_make_args(str(tmp_path), unskip=names))

    assert match in exc_info.value.decision.summary
    assert done.status == MethodStatus.DONE  # 校验失败不留半成品状态
    assert out_of_scope.status == MethodStatus.SKIPPED


def test_handler_unskip_revives_all_skipped_overloads(tmp_path):
    """--unskip: 同名重载全部被跳过时, 一次恢复所有重载。

    反例(修复前): next() 只命中首个同名条目, 其余重载仍 skipped ->
    _target_method_done 恒为 False, method 模式永远无法达标。
    """
    no_arg = _skipped_method("doStuff", covered=2, missed=8, desc="()V")
    with_arg = _skipped_method("doStuff", covered=4, missed=6, desc="(I)V")
    state = State(
        project_root=str(tmp_path),
        target_class="com.example.MyService",
        target_method="doStuff",
        methods=[no_arg, with_arg],
        current_method=None,
        plan={"target_class": "com.example.MyService", "threshold": 80.0, "methods": []},
    )

    with patch("scripts.make_plan.setup_logger"), _patched_store(state):
        handler(_make_args(str(tmp_path), unskip="doStuff"))

    assert no_arg.status == MethodStatus.PENDING
    assert with_arg.status == MethodStatus.PENDING
    assert no_arg.skip_reason is None and with_arg.skip_reason is None
    assert no_arg.round_rates == [] and with_arg.round_rates == []


def test_handler_unskip_revives_skipped_overload_when_sibling_is_done(tmp_path):
    """--unskip: 一个重载已 done、另一个被跳过时, 只恢复被跳过的重载。

    反例(修复前): next() 命中已 done 的首个重载 -> 报"只有被跳过的方法才能
    恢复", 被跳过的重载无法通过任何 CLI 路径恢复。
    """
    done = _make_method("doStuff", desc="()V", covered=10, missed=0)
    done.status = MethodStatus.DONE
    skipped = _skipped_method("doStuff", desc="(I)V", covered=4, missed=6)
    state = State(
        project_root=str(tmp_path),
        target_class="com.example.MyService",
        target_method="doStuff",
        methods=[done, skipped],
        current_method=None,
        plan={"target_class": "com.example.MyService", "threshold": 80.0, "methods": []},
    )

    with patch("scripts.make_plan.setup_logger"), _patched_store(state):
        handler(_make_args(str(tmp_path), unskip="doStuff"))

    assert skipped.status == MethodStatus.PENDING   # 被跳过的重载已恢复
    assert skipped.skip_reason is None
    assert done.status == MethodStatus.DONE         # 已达标的重载不受影响


def test_handler_unskip_and_skip_current_are_mutually_exclusive(tmp_path):
    """--unskip 与 --skip-current 同用: 状态错误。"""
    state = _batch_state([_skipped_method("doStuff")], current_method=None)
    with patch("scripts.make_plan.setup_logger"), _patched_store(state):
        with pytest.raises(StepError) as exc_info:
            handler(_make_args(str(tmp_path), unskip="doStuff", skip_current=True))

    assert "互斥" in exc_info.value.decision.summary


def test_handler_unskip_survives_method_group_filter(tmp_path):
    """--unskip + --method-group: 组过滤先落地, 恢复的方法不会被当轮改回无原因跳过。

    反例(修复前): 顺序颠倒 -> 刚复活的方法被组过滤立即置 skipped 且无 skip_reason,
    之后连 --unskip 都会以"无跳过原因"拒绝恢复。
    """
    out_of_group = _skipped_method("futureMethod", covered=0, missed=10)
    in_group = _make_method("groupMethod", covered=5, missed=5)
    state = _batch_state([out_of_group, in_group], current_method=None)

    with patch("scripts.make_plan.setup_logger"), _patched_store(state):
        handler(_make_args(str(tmp_path), method_group="groupMethod",
                           unskip="futureMethod"))

    assert out_of_group.status == MethodStatus.PENDING   # 恢复生效
    assert out_of_group.skip_reason is None
    assert state.current_method == out_of_group.key      # 0% 覆盖率, 排序最靠前

    # 下一轮组过滤(子代理按当前组重新委派)会把它按"组外"再次置 skipped(无原因);
    # 这是设计行为: 待其所属组成为当前组时自动复活, 期间也可再次 --unskip
    with patch("scripts.make_plan.setup_logger"), _patched_store(state):
        handler(_make_args(str(tmp_path), method_group="groupMethod"))

    assert out_of_group.status == MethodStatus.SKIPPED
    assert out_of_group.skip_reason is None
