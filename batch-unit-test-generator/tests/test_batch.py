"""批量层测试: batch_diff 过滤链、BatchState 操作、report 批量渲染。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jaut import config
from jaut.models import (
    BatchClassEntry, BatchState, ClassCoverage, MethodCoverage,
    MethodKey, MethodStatus, State,
)
from jaut.report import render_class_complete_report, render_batch_finish_report


# --------------------------------------------------------------------------- #
# batch_diff 过滤辅助
# --------------------------------------------------------------------------- #
def test_is_test_file():
    from batch_diff import is_test_file
    assert is_test_file("src/test/java/com/foo/FooTest.java")
    assert is_test_file("src/main/java/com/foo/FooTest.java")  # Test 后缀
    assert is_test_file("src/main/java/com/foo/TestFoo.java")  # Test 前缀
    assert not is_test_file("src/main/java/com/foo/FooService.java")
    assert not is_test_file("src/main/java/com/foo/Foo.java")


def test_is_test_file_edge_cases():
    from batch_diff import is_test_file
    assert not is_test_file("src/main/java/com/foo/Tester.java")  # Tester 不算
    assert is_test_file("src/main/java/com/foo/FooTests.java")
    assert is_test_file("src/main/java/com/foo/FooTestCase.java")


def test_matches_jacoco_pattern():
    from batch_diff import matches_jacoco_pattern
    assert matches_jacoco_pattern(
        "src/main/java/com/foo/vo/Bar.java", "**/vo/*.class")
    assert not matches_jacoco_pattern(
        "src/main/java/com/foo/Bar.java", "**/vo/*.class")
    assert matches_jacoco_pattern(
        "src/main/java/com/foo/Bar.java", "com/foo/*.class")


def test_matches_jacoco_pattern_double_star_middle():
    from batch_diff import matches_jacoco_pattern
    assert matches_jacoco_pattern(
        "src/main/java/com/foo/a/b/Bar.java", "com/foo/**/Bar.class")


# --------------------------------------------------------------------------- #
# BatchState 操作
# --------------------------------------------------------------------------- #
def test_batch_state_find_entry():
    bs = BatchState(classes=[
        BatchClassEntry(fqcn="com.A", simple_name="A", source_file="",
                       module=".", workdir="", status="pending"),
        BatchClassEntry(fqcn="com.B", simple_name="B", source_file="",
                       module=".", workdir="", status="done"),
    ])
    assert bs.find_entry("com.A") is not None
    assert bs.find_entry("com.A").fqcn == "com.A"
    assert bs.find_entry("com.C") is None


def test_batch_state_next_pending_skips_done():
    bs = BatchState(classes=[
        BatchClassEntry(fqcn="A", simple_name="A", source_file="",
                       module=".", workdir="", status="done"),
        BatchClassEntry(fqcn="B", simple_name="B", source_file="",
                       module=".", workdir="", status="skipped"),
        BatchClassEntry(fqcn="C", simple_name="C", source_file="",
                       module=".", workdir="", status="pending"),
    ])
    nxt = bs.next_pending()
    assert nxt is not None
    assert nxt.fqcn == "C"


def test_batch_state_next_pending_recheck():
    bs = BatchState(classes=[
        BatchClassEntry(fqcn="A", simple_name="A", source_file="",
                       module=".", workdir="", status="done"),
        BatchClassEntry(fqcn="B", simple_name="B", source_file="",
                       module=".", workdir="", status="recheck"),
    ])
    nxt = bs.next_pending()
    assert nxt is not None
    assert nxt.fqcn == "B"


def test_batch_state_next_pending_empty():
    bs = BatchState(classes=[
        BatchClassEntry(fqcn="A", simple_name="A", source_file="",
                       module=".", workdir="", status="done"),
    ])
    assert bs.next_pending() is None


# --------------------------------------------------------------------------- #
# report 批量渲染
# --------------------------------------------------------------------------- #
def _make_batch_state():
    return BatchState(
        project_root="/worktree",
        target_branch="master",
        threshold=80.0,
        classes=[
            BatchClassEntry(
                fqcn="com.foo.A", simple_name="A", source_file="",
                module=".", workdir="/wt/.agent/batch-unit-test-generator/classes/A",
                status="done", baseline_rate=50.0, final_rate=85.0,
                rounds_used=10, escalations=1),
            BatchClassEntry(
                fqcn="com.bar.B", simple_name="B", source_file="",
                module="mod1", workdir="/wt/.agent/batch-unit-test-generator/classes/B",
                status="skipped", baseline_rate=0.0, skip_reason="用户跳过",
                rounds_used=0, escalations=0),
            BatchClassEntry(
                fqcn="com.baz.C", simple_name="C", source_file="",
                module=".", workdir="/wt/.agent/batch-unit-test-generator/classes/C",
                status="failed", baseline_rate=30.0, skip_reason="编译失败",
                rounds_used=5, escalations=2),
        ],
        initial_tests={"tests": 10, "failures": 2, "errors": 0},
    )


def test_render_class_complete_report():
    state = State(
        target_class="com.foo.A",
        threshold=80.0,
        batch_mode=True,
        class_round_used=10,
        class_coverage=ClassCoverage.of(80, 20),
        methods=[
            MethodCoverage(key=MethodKey("a", "()"), covered=8, missed=2,
                           status=MethodStatus.DONE, initial_rate=50.0),
            MethodCoverage(key=MethodKey("b", "()"), covered=0, missed=0,
                           status=MethodStatus.DONE, initial_rate=100.0),  # 抽象
        ],
    )
    report_text = render_class_complete_report(state)
    assert "com.foo.A" in report_text
    assert "类级轮次已用: 10" in report_text
    assert "done 2" in report_text
    assert "终验" in report_text


def test_render_batch_finish_report():
    bs = _make_batch_state()
    report_text = render_batch_finish_report(bs)
    assert "批量单元测试最终报告" in report_text
    assert "com.foo.A" in report_text
    assert "com.bar.B" in report_text
    assert "com.baz.C" in report_text
    assert "达标" in report_text
    assert "跳过" in report_text
    assert "失败" in report_text
    assert "总轮次: 15" in report_text
    assert "总升级次数: 3" in report_text


def test_render_batch_finish_report_recheck():
    bs = _make_batch_state()
    bs.classes[0].status = "recheck"
    report_text = render_batch_finish_report(bs)
    assert "需重验" in report_text


# --------------------------------------------------------------------------- #
# config 批量常量
# --------------------------------------------------------------------------- #
def test_batch_config_constants():
    assert config.batch_class_round_budget() == 30
    assert config.BATCH_AUTO_GRANT is True
    assert config.DEFAULT_TARGET_BRANCHES == ("master", "main", "develop")
    assert config.BATCH_STATE_FILENAME == "batch_state.json"
    assert config.CLASSES_SUBDIR == "classes"


def test_config_workdir_parts():
    assert config.WORKDIR_PARTS == (".agent", "batch-unit-test-generator")


# --------------------------------------------------------------------------- #
# _infer_escalation_reason 升级原因推断
# --------------------------------------------------------------------------- #
from batch_update import _infer_escalation_reason  # noqa: E402
from jaut.models import TestOutcome  # noqa: E402


def _make_state_for_reason(
    *,
    threshold: float = 70.0,
    class_round_used: int = 5,
    current_method: MethodKey | None = None,
    round_rates: list[float] | None = None,
    round_test_results: list[TestOutcome] | None = None,
) -> State:
    """构造用于测试 _infer_escalation_reason 的最小 State。"""
    methods: list[MethodCoverage] = []
    if current_method is not None:
        m = MethodCoverage(
            key=current_method, covered=0, missed=100,
            status=MethodStatus.PENDING)
        m.round_rates = list(round_rates or [])
        m.round_test_results = list(round_test_results or [])
        methods.append(m)
    return State(
        project_root="/tmp", workdir="/tmp/.agent/test",
        threshold=threshold, target_class="com.example.Foo",
        target_method=None, source_file="Foo.java",
        module=".", jacoco_version="0.8.12",
        current_method=current_method, methods=methods,
        class_coverage=ClassCoverage(covered=50, missed=50),
        class_round_used=class_round_used)


def test_infer_escalation_reason_class_round_budget():
    """类级轮次耗尽时返回对应原因。"""
    state = _make_state_for_reason(class_round_used=config.batch_class_round_budget())
    reason = _infer_escalation_reason(state)
    assert "类级迭代已达上限" in reason
    assert str(config.batch_class_round_budget()) in reason


def test_infer_escalation_reason_method_round_budget():
    """当前方法轮次耗尽时返回对应原因。"""
    key = MethodKey("doStuff", "()V")
    state = _make_state_for_reason(
        current_method=key,
        round_rates=[10.0] * config.METHOD_ROUND_BUDGET)
    reason = _infer_escalation_reason(state)
    assert "单方法上限" in reason
    assert "doStuff" in reason


def test_infer_escalation_reason_test_failure_streak():
    """连续测试失败不收敛时返回对应原因。"""
    key = MethodKey("doStuff", "()V")
    state = _make_state_for_reason(
        current_method=key,
        round_rates=[10.0, 10.0, 10.0],
        round_test_results=[TestOutcome(1, 0)] * config.TEST_FAIL_STREAK_ROUNDS)
    reason = _infer_escalation_reason(state)
    assert "连续" in reason
    assert "测试失败" in reason


def test_infer_escalation_reason_no_improvement():
    """连续覆盖率无提升时返回对应原因。"""
    key = MethodKey("doStuff", "()V")
    state = _make_state_for_reason(
        threshold=70.0,
        current_method=key,
        round_rates=[10.0, 10.0, 10.0])
    reason = _infer_escalation_reason(state)
    assert "覆盖率无提升" in reason


def test_infer_escalation_reason_no_improvement_near_threshold():
    """接近门槛时不报告"无提升"(避免误报)。"""
    key = MethodKey("doStuff", "()V")
    state = _make_state_for_reason(
        threshold=70.0,
        current_method=key,
        round_rates=[64.0, 64.0, 64.0])  # >= 70 * 0.9 = 63
    reason = _infer_escalation_reason(state)
    assert "无提升" not in reason


def test_infer_escalation_reason_no_method():
    """无当前方法时返回空串(无法推断)。"""
    state = _make_state_for_reason(current_method=None, class_round_used=5)
    reason = _infer_escalation_reason(state)
    assert reason == ""
