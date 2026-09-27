"""批量层测试: batch_diff 过滤链、BatchState 操作、report 批量渲染。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jaut import config
from jaut.models import (
    BatchClassEntry, BatchState, ClassCoverage, MethodCoverage,
    MethodKey, MethodStatus, Route, State,
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
    assert "总自动跳过方法数: 3" in report_text


def test_render_batch_finish_report_recheck():
    bs = _make_batch_state()
    bs.classes[0].status = "recheck"
    report_text = render_batch_finish_report(bs)
    assert "需重验" in report_text


def test_render_batch_finish_report_unverified():
    """unverified(环境不可信待复核)类单列一节, 提示重跑 batch_finish 复核。"""
    bs = _make_batch_state()
    bs.classes[0].status = "unverified"
    bs.classes[0].skip_reason = "待环境复核: 测试结果不可信(surefire 报告缺失)"
    report_text = render_batch_finish_report(bs)
    assert "待环境复核(1 类)" in report_text
    assert "com.foo.A" in report_text
    assert "重跑 batch_finish.py" in report_text


# --------------------------------------------------------------------------- #
# config 批量常量
# --------------------------------------------------------------------------- #
def test_batch_config_constants():
    assert config.batch_class_round_budget() == 30
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
    method_round_bonus: int = 0,
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
        class_round_used=class_round_used,
        method_round_bonus=method_round_bonus)


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


def test_infer_escalation_reason_method_round_budget_includes_bonus():
    """--grant-rounds 追加窗口计入单方法上限: 转述给用户的轮数与真实口径一致。"""
    key = MethodKey("doStuff", "()V")
    bonus = 8
    # 追加后上限 = 8 + 8 = 16: 10 轮尚未达上限, 不得误报"单方法上限"
    state = _make_state_for_reason(
        current_method=key, round_rates=[50.0, 51.0] * 5,   # 有提升趋势, 排除无提升
        method_round_bonus=bonus)
    assert "单方法上限" not in _infer_escalation_reason(state)

    # 16 轮达含 bonus 的上限, 数字如实转述
    state = _make_state_for_reason(
        current_method=key, round_rates=[10.0] * (config.METHOD_ROUND_BUDGET + bonus),
        method_round_bonus=bonus)
    reason = _infer_escalation_reason(state)
    assert "单方法上限" in reason
    assert str(config.METHOD_ROUND_BUDGET + bonus) in reason


# --------------------------------------------------------------------------- #
# batch_next 有效预算(探索模式)与 batch_update 进度统计
# --------------------------------------------------------------------------- #
def _make_budget_entry(tmp_path: Path, **overrides) -> BatchClassEntry:
    """构造 diff 行数触发的"大类"条目(此类不拆分方法组)。"""
    defaults = dict(fqcn="com.example.Large", simple_name="Large",
                    source_file="Large.java", module=".",
                    workdir=str(tmp_path / "classes" / "Large"),
                    status="pending", diff_add=config.LARGE_CLASS_DIFF_THRESHOLD)
    defaults.update(overrides)
    return BatchClassEntry(**defaults)


def test_effective_class_budget_uses_exploration_mode(tmp_path):
    """探索模式预算翻倍: 信号取 exploration_mode, 不取方法组(两者判定条件不同)。"""
    from unittest.mock import patch
    from batch_next import _effective_class_budget

    entry = _make_budget_entry(tmp_path)
    assert not entry.has_method_groups  # diff 行数触发的类没有方法组
    state = State(project_root=str(tmp_path), target_class="com.example.Large",
                  module=".", threshold=80.0, batch_mode=True, exploration_mode=True)

    with patch("batch_next.StateStore") as MockStore:
        MockStore.return_value.load.return_value = state
        budget, exploring = _effective_class_budget(entry)

    assert exploring is True
    assert budget == (config.batch_class_round_budget()
                      * config.EXPLORATION_MODE_MULTIPLIER)


def test_effective_class_budget_falls_back_without_class_state(tmp_path):
    """类 state.json 尚不存在时退回基础预算, 且不宣称探索模式。"""
    from unittest.mock import patch
    from batch_next import _effective_class_budget

    with patch("batch_next.StateStore") as MockStore:
        MockStore.return_value.load.return_value = None
        budget, exploring = _effective_class_budget(_make_budget_entry(tmp_path))

    assert (budget, exploring) == (config.batch_class_round_budget(), False)


def test_batch_update_progress_counts_unmet_as_terminal(tmp_path):
    """unmet 与 done/skipped/failed 同为终态: 进度统计漏计会让计数永远到不了满分。"""
    import argparse
    from unittest.mock import MagicMock, patch
    from batch_update import handler as update_handler

    workdir = tmp_path / "batchwork"
    workdir.mkdir(parents=True, exist_ok=True)
    unmet_entry = BatchClassEntry(fqcn="com.example.B", simple_name="B",
                                  source_file="B.java", module=".", workdir="",
                                  status="unmet", baseline_rate=20.0)
    entry = BatchClassEntry(fqcn="com.example.A", simple_name="A",
                            source_file="A.java", module=".",
                            workdir=str(tmp_path / "classes" / "A"),
                            status="in_progress", baseline_rate=40.0)
    batch_state = BatchState(project_root=str(tmp_path), target_branch="master",
                             threshold=80.0, classes=[entry, unmet_entry])
    (workdir / config.BATCH_STATE_FILENAME).write_text(
        json.dumps(batch_state.to_dict(), ensure_ascii=False), encoding="utf-8")

    finished = MethodCoverage(key=MethodKey("doStuff", "()V"), covered=10, missed=0,
                              status=MethodStatus.DONE)
    class_state = State(project_root=str(tmp_path), target_class="com.example.A",
                        module=".", threshold=80.0, batch_mode=True,
                        class_coverage=ClassCoverage.of(10, 0), methods=[finished])

    locked = MagicMock()
    locked.__enter__ = MagicMock(return_value=None)
    locked.__exit__ = MagicMock(return_value=False)
    with patch("batch_update.setup_logger"), \
         patch("batch_update.batch_lock", return_value=locked), \
         patch("batch_update.StateStore") as MockStore:
        MockStore.return_value.load.return_value = class_state
        decision, _ = update_handler(argparse.Namespace(
            project_root=str(tmp_path), fqcn="com.example.A", skip_class=None,
            reason=None, mark_failed=None, workdir=str(workdir)))

    assert "已完成 2/2" in decision.summary


# --------------------------------------------------------------------------- #
# batch_update 方法组推进: 预算耗尽后不再逐组空转委派
# --------------------------------------------------------------------------- #
def _make_group_entry(tmp_path: Path, groups: list[list[str]],
                      current_group: int = 0) -> BatchClassEntry:
    """构造带方法组的 in_progress 类条目。"""
    return BatchClassEntry(
        fqcn="com.example.Big", simple_name="Big", source_file="Big.java",
        module=".", workdir=str(tmp_path / "classes" / "Big"),
        status="in_progress", baseline_rate=40.0,
        method_groups=groups, current_group=current_group)


def _make_group_state(methods: list[MethodCoverage], *,
                      class_round_used: int = 0) -> State:
    return State(project_root="/tmp", target_class="com.example.Big",
                 module=".", threshold=80.0, batch_mode=True,
                 class_coverage=ClassCoverage.of(40, 60),
                 methods=methods, class_round_used=class_round_used)


def test_remaining_groups_have_work_false_when_budget_exhausted(tmp_path):
    """类级预算耗尽: 即使剩余组方法可复活(组过滤跳过, 无原因)也不再推进。

    根因: 复活的方法会被 verify_coverage 预检查立即再跳过, 每个剩余组都是
    一次零工作的子代理委派。
    """
    from batch_update import _remaining_groups_have_work

    entry = _make_group_entry(tmp_path, groups=[["m1"], ["m2"], ["m3"]])
    m2 = MethodCoverage(key=MethodKey("m2", "()V"), status=MethodStatus.SKIPPED)
    m3 = MethodCoverage(key=MethodKey("m3", "()V"), status=MethodStatus.SKIPPED)
    state = _make_group_state([m2, m3],
                              class_round_used=config.batch_class_round_budget())

    assert state.class_budget_exhausted
    assert _remaining_groups_have_work(entry, state) is False


def test_remaining_groups_have_work_true_for_group_filter_skips(tmp_path):
    """预算未耗尽: 剩余组的组过滤跳过(无 skip_reason)可被 make_plan 复活。"""
    from batch_update import _remaining_groups_have_work

    entry = _make_group_entry(tmp_path, groups=[["m1"], ["m2"]])
    m2 = MethodCoverage(key=MethodKey("m2", "()V"), status=MethodStatus.SKIPPED)
    state = _make_group_state([m2], class_round_used=1)

    assert _remaining_groups_have_work(entry, state) is True


def test_remaining_groups_have_work_false_for_reasoned_skips(tmp_path):
    """预算未耗尽但剩余组方法均已带因跳过/完成: 无可调度方法。"""
    from batch_update import _remaining_groups_have_work

    entry = _make_group_entry(tmp_path, groups=[["m1"], ["m2"], ["m3"]])
    m2 = MethodCoverage(key=MethodKey("m2", "()V"), status=MethodStatus.SKIPPED,
                        skip_reason="类级预算耗尽(30/30 轮), 未达标方法不再迭代")
    m3 = MethodCoverage(key=MethodKey("m3", "()V"), status=MethodStatus.DONE)
    state = _make_group_state([m2, m3], class_round_used=1)

    assert _remaining_groups_have_work(entry, state) is False


def test_batch_update_finalizes_when_budget_exhausted_mid_groups(tmp_path):
    """类级预算在第 1 组耗尽: 不再推进组 2/3 委派, 直接落账 done 进终验。"""
    import argparse
    from unittest.mock import MagicMock, patch
    from batch_update import handler as update_handler

    workdir = tmp_path / "batchwork"
    workdir.mkdir(parents=True, exist_ok=True)
    entry = _make_group_entry(tmp_path, groups=[["m1"], ["m2"], ["m3"]])
    batch_state = BatchState(project_root=str(tmp_path), target_branch="master",
                             threshold=80.0, classes=[entry])
    (workdir / config.BATCH_STATE_FILENAME).write_text(
        json.dumps(batch_state.to_dict(), ensure_ascii=False), encoding="utf-8")

    m1 = MethodCoverage(key=MethodKey("m1", "()V"), covered=10, missed=0,
                        status=MethodStatus.DONE)
    m2 = MethodCoverage(key=MethodKey("m2", "()V"), status=MethodStatus.SKIPPED)
    m3 = MethodCoverage(key=MethodKey("m3", "()V"), status=MethodStatus.SKIPPED)
    class_state = _make_group_state(
        [m1, m2, m3], class_round_used=config.batch_class_round_budget())

    locked = MagicMock()
    locked.__enter__ = MagicMock(return_value=None)
    locked.__exit__ = MagicMock(return_value=False)
    with patch("batch_update.setup_logger"), \
         patch("batch_update.batch_lock", return_value=locked), \
         patch("batch_update.StateStore") as MockStore:
        MockStore.return_value.load.return_value = class_state
        decision, _ = update_handler(argparse.Namespace(
            project_root=str(tmp_path), fqcn=None, skip_class=None,
            reason=None, mark_failed=None, workdir=str(workdir)))

    assert decision.route.value != "batch_next"   # 不再委派剩余组
    data = json.loads((workdir / config.BATCH_STATE_FILENAME).read_text(
        encoding="utf-8"))
    cls = data["classes"][0]
    assert cls["status"] == "done"
    assert cls["current_group"] == 0               # 组索引不再走完


def test_batch_update_advances_group_when_work_remains(tmp_path):
    """预算未耗尽且剩余组有组过滤跳过: 照常推进组并重新委派(不误伤正常流程)。"""
    import argparse
    from unittest.mock import MagicMock, patch
    from batch_update import handler as update_handler

    workdir = tmp_path / "batchwork"
    workdir.mkdir(parents=True, exist_ok=True)
    entry = _make_group_entry(tmp_path, groups=[["m1"], ["m2"]])
    batch_state = BatchState(project_root=str(tmp_path), target_branch="master",
                             threshold=80.0, classes=[entry])
    (workdir / config.BATCH_STATE_FILENAME).write_text(
        json.dumps(batch_state.to_dict(), ensure_ascii=False), encoding="utf-8")

    m1 = MethodCoverage(key=MethodKey("m1", "()V"), covered=10, missed=0,
                        status=MethodStatus.DONE)
    m2 = MethodCoverage(key=MethodKey("m2", "()V"), status=MethodStatus.SKIPPED)
    class_state = _make_group_state([m1, m2], class_round_used=1)

    locked = MagicMock()
    locked.__enter__ = MagicMock(return_value=None)
    locked.__exit__ = MagicMock(return_value=False)
    with patch("batch_update.setup_logger"), \
         patch("batch_update.batch_lock", return_value=locked), \
         patch("batch_update.StateStore") as MockStore:
        MockStore.return_value.load.return_value = class_state
        decision, _ = update_handler(argparse.Namespace(
            project_root=str(tmp_path), fqcn=None, skip_class=None,
            reason=None, mark_failed=None, workdir=str(workdir)))

    assert decision.route.value == "batch_next"
    data = json.loads((workdir / config.BATCH_STATE_FILENAME).read_text(
        encoding="utf-8"))
    cls = data["classes"][0]
    assert cls["status"] == "pending"
    assert cls["current_group"] == 1               # 正常推进到组 2


# --------------------------------------------------------------------------- #
# batch_update 落账统计自动跳过方法数(escalations 字段)
# --------------------------------------------------------------------------- #
def test_batch_update_counts_auto_skipped_methods(tmp_path):
    """落账时把类内自动跳过(带 skip_reason)的方法数写入 entry.escalations。

    根因: 升级穿透移除后 escalations 只有三处置 0, 无任何递增路径, 最终报告
         "总自动跳过方法数"(原"总升级次数")恒为 0, 与实际发生的自动跳过无关。
    """
    import argparse
    from unittest.mock import MagicMock, patch
    from batch_update import handler as update_handler

    workdir = tmp_path / "batchwork"
    workdir.mkdir(parents=True, exist_ok=True)
    entry = BatchClassEntry(fqcn="com.example.A", simple_name="A",
                            source_file="A.java", module=".",
                            workdir=str(tmp_path / "classes" / "A"),
                            status="in_progress", baseline_rate=40.0)
    batch_state = BatchState(project_root=str(tmp_path), target_branch="master",
                             threshold=80.0, classes=[entry])
    (workdir / config.BATCH_STATE_FILENAME).write_text(
        json.dumps(batch_state.to_dict(), ensure_ascii=False), encoding="utf-8")

    done = MethodCoverage(key=MethodKey("m1", "()V"), covered=10, missed=0,
                          status=MethodStatus.DONE)
    auto_skipped = MethodCoverage(key=MethodKey("m2", "()V"), covered=0, missed=10,
                                  status=MethodStatus.SKIPPED,
                                  skip_reason="连续 3 轮测试失败不收敛, 自动跳过")
    group_filtered = MethodCoverage(key=MethodKey("m3", "()V"), covered=0, missed=10,
                                    status=MethodStatus.SKIPPED)  # 组过滤跳过, 不计
    class_state = State(project_root=str(tmp_path), target_class="com.example.A",
                        module=".", threshold=80.0, batch_mode=True,
                        class_coverage=ClassCoverage.of(40, 60),
                        methods=[done, auto_skipped, group_filtered])

    locked = MagicMock()
    locked.__enter__ = MagicMock(return_value=None)
    locked.__exit__ = MagicMock(return_value=False)
    with patch("batch_update.setup_logger"), \
         patch("batch_update.batch_lock", return_value=locked), \
         patch("batch_update.StateStore") as MockStore:
        MockStore.return_value.load.return_value = class_state
        decision, _ = update_handler(argparse.Namespace(
            project_root=str(tmp_path), fqcn=None, skip_class=None,
            reason=None, mark_failed=None, workdir=str(workdir)))

    data = json.loads((workdir / config.BATCH_STATE_FILENAME).read_text(
        encoding="utf-8"))
    assert data["classes"][0]["escalations"] == 1   # 只计带 skip_reason 的自动跳过
    assert decision.report is not None or decision.summary  # 落账完成


# --------------------------------------------------------------------------- #
# batch_init 覆盖率排除模式(P2-5): 逐类精确字面量, 取代永不命中的全局通配
# --------------------------------------------------------------------------- #
def _make_candidate(project_root: Path, fqcn: str, content: str) -> dict:
    pkg, _, simple = fqcn.rpartition(".")
    rel = f"src/main/java/{pkg.replace('.', '/')}/{simple}.java" if pkg \
        else f"src/main/java/{simple}.java"
    path = project_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return {"fqcn": fqcn, "source_file": rel, "module": "."}


def _excludes_args(extra=None):
    import argparse
    return argparse.Namespace(coverage_exclude=extra)


def test_build_coverage_excludes_builder_literal(tmp_path):
    """@Builder 生成内部类 <FQCN>$<Simple>Builder(结尾 SimpleBuilder, 非 *$Builder)。"""
    from batch_init import _build_coverage_excludes
    cand = _make_candidate(tmp_path, "com.foo.Foo", "public class Foo {\n@Builder\n}\n")
    excludes = _build_coverage_excludes([cand], _excludes_args(), tmp_path)
    assert "com.foo.Foo$FooBuilder" in excludes
    assert not any("*" in e for e in excludes)   # 不再有全局通配


def test_build_coverage_excludes_superbuilder_impl(tmp_path):
    """@SuperBuilder 额外生成 <Simple>BuilderImpl。"""
    from batch_init import _build_coverage_excludes
    cand = _make_candidate(tmp_path, "com.foo.Super", "public class Super {\n@SuperBuilder\n}\n")
    excludes = _build_coverage_excludes([cand], _excludes_args(), tmp_path)
    assert "com.foo.Super$SuperBuilder" in excludes
    assert "com.foo.Super$SuperBuilderImpl" in excludes


def test_build_coverage_excludes_mapper_toplevel_impl(tmp_path):
    """MapStruct @Mapper 生成同包顶层类 <pkg>.<Simple>Impl(无 $)。"""
    from batch_init import _build_coverage_excludes
    cand = _make_candidate(tmp_path, "com.foo.Map", "public interface Map {\n@Mapper\n}\n")
    excludes = _build_coverage_excludes([cand], _excludes_args(), tmp_path)
    assert "com.foo.MapImpl" in excludes
    assert not any("$" in e for e in excludes)


def test_build_coverage_excludes_value_data_not_excluded(tmp_path):
    """@Value/@Data 不生成新类(仅变换原类字节码), 不再保守排除。"""
    from batch_init import _build_coverage_excludes
    cand = _make_candidate(tmp_path, "com.foo.Vo", "public class Vo {\n@Value\n@Data\n}\n")
    excludes = _build_coverage_excludes([cand], _excludes_args(), tmp_path)
    assert excludes == []


def test_build_coverage_excludes_multiple_annotations_all_appended(tmp_path):
    """命中多个注解时全部追加(旧版 break 会吞掉后续模式)。"""
    from batch_init import _build_coverage_excludes
    cand = _make_candidate(
        tmp_path, "com.foo.Both", "public class Both {\n@Builder\n@Mapper\n}\n")
    excludes = _build_coverage_excludes([cand], _excludes_args(), tmp_path)
    assert "com.foo.Both$BothBuilder" in excludes
    assert "com.foo.BothImpl" in excludes


def test_build_coverage_excludes_explicit_patterns_passthrough(tmp_path):
    """显式 --coverage-exclude 原样保留(方法级 @Builder 等场景的唯一出路)。"""
    from batch_init import _build_coverage_excludes
    cand = _make_candidate(tmp_path, "com.foo.Plain", "public class Plain {}\n")
    excludes = _build_coverage_excludes(
        [cand], _excludes_args(["Foo$FooBuilder", " com.bar.** "]), tmp_path)
    assert "Foo$FooBuilder" in excludes
    assert "com.bar.**" in excludes   # strip 清理空白


# --------------------------------------------------------------------------- #
# batch_init _select_classes 校验(P3-19): 不静默空选
# --------------------------------------------------------------------------- #
def test_select_classes_top_n_invalid():
    """top:0 / top:abc 报 state_error, 不静默空选假 finish。"""
    from jaut.cli import StepError
    from batch_init import _select_classes
    with pytest.raises(StepError) as ei:
        _select_classes([], "top:0")
    assert "正整数" in ei.value.decision.summary
    with pytest.raises(StepError):
        _select_classes([], "top:abc")


def test_select_classes_unknown_fqcn_lists_candidates():
    """拼错 FQCN 报错并列出候选清单, 引导用户核对。"""
    from jaut.cli import StepError
    from batch_init import _select_classes
    candidates = [{"fqcn": "com.foo.A"}, {"fqcn": "com.foo.B"}]
    with pytest.raises(StepError) as ei:
        _select_classes(candidates, "com.foo.C")
    assert "com.foo.A" in (ei.value.decision.question or "")
    assert "候选清单" in (ei.value.decision.question or "")


def test_select_classes_all_and_top_and_fqcn():
    from batch_init import _select_classes
    candidates = [{"fqcn": "com.foo.A"}, {"fqcn": "com.foo.B"}]
    assert _select_classes(candidates, "all") == candidates
    assert _select_classes(candidates, "top:1") == candidates[:1]
    assert _select_classes(candidates, "com.foo.B") == [candidates[1]]


# --------------------------------------------------------------------------- #
# batch_diff git 失败显式报错(P2-7): 不再静默吞错伪装"无候选类"
# --------------------------------------------------------------------------- #
def _git_result(ok: bool, stdout=None, stderr=None, returncode=0):
    from jaut.proc import CommandResult
    return CommandResult(ok=ok, returncode=returncode if ok else 128,
                         stdout=stdout, stderr=stderr)


def test_get_diff_files_git_diff_failure_raises(tmp_path):
    """git diff 失败(分支拼错/仓库异常)抛 exec_error, 含 stderr 上下文。"""
    from jaut.cli import StepError
    from batch_diff import _get_diff_files
    from unittest.mock import patch
    with patch("jaut.proc.resolve_tool", return_value="/usr/bin/git"), \
         patch("jaut.proc.run_command",
               side_effect=[_git_result(True, stdout="abc123\n"),
                            _git_result(False, stderr="fatal: bad revision")]):
        with pytest.raises(StepError) as ei:
            _get_diff_files(tmp_path, "master", "two")
    assert "git diff 失败" in ei.value.decision.summary
    assert "fatal: bad revision" in ei.value.decision.summary


def test_get_diff_files_three_merge_base_failure_suggests_two(tmp_path):
    """显式三点模式 merge-base 失败: 报错并建议 --diff-mode two, 不再裸
    AttributeError(None.strip())。"""
    from jaut.cli import StepError
    from batch_diff import _get_diff_files
    from unittest.mock import patch
    with patch("jaut.proc.resolve_tool", return_value="/usr/bin/git"), \
         patch("jaut.proc.run_command",
               return_value=_git_result(False, stderr="fatal: not a valid ref")):
        with pytest.raises(StepError) as ei:
            _get_diff_files(tmp_path, "master", "three")
    assert "--diff-mode two" in (ei.value.decision.question or "")


def test_get_diff_files_empty_stdout_returns_empty_list(tmp_path):
    """差异真正为空: 返回 [], 不误报 git 故障。"""
    from batch_diff import _get_diff_files
    from unittest.mock import patch
    with patch("jaut.proc.resolve_tool", return_value="/usr/bin/git"), \
         patch("jaut.proc.run_command",
               side_effect=[_git_result(True, stdout="abc123\n"),
                            _git_result(True, stdout="")]):
        assert _get_diff_files(tmp_path, "master", "two") == []


# --------------------------------------------------------------------------- #
# batch_diff 确认门禁(P2-8/P3-12): question 用真实 --classes 参数,
# candidates.json 落盘 diff_mode
# --------------------------------------------------------------------------- #
def test_batch_diff_gate_question_and_diff_mode_persisted(tmp_path):
    """门禁 question/resume 引导真实存在的 --classes 参数(旧文案 --all/--top N
    不存在, 逐字执行会 argparse exit 2); diff_mode 写入 candidates.json。"""
    import argparse
    import json as _json
    from unittest.mock import MagicMock, patch
    from batch_diff import handler as diff_handler

    workdir = tmp_path / "diffwork"
    workdir.mkdir(parents=True, exist_ok=True)
    src = tmp_path / "src" / "main" / "java" / "com" / "foo" / "Foo.java"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text(
        "package com.foo;\npublic class Foo {\n"
        "    public int add(int a, int b) { return a + b; }\n}\n",
        encoding="utf-8")

    locked = MagicMock()
    locked.__enter__ = MagicMock(return_value=None)
    locked.__exit__ = MagicMock(return_value=False)
    with patch("batch_diff.setup_logger"), \
         patch("batch_diff._resolve_target_branch", return_value="master"), \
         patch("batch_diff._get_diff_files",
               return_value=[("src/main/java/com/foo/Foo.java", 10, 2)]), \
         patch("batch_diff.gitops.current_branch", return_value="feature"), \
         patch("batch_diff.maven.find_module_for_source", return_value="."), \
         patch("batch_diff._detect_existing_test", return_value=False):
        decision, _ = diff_handler(argparse.Namespace(
            project_root=str(tmp_path), target="auto", diff_mode="two",
            exclude_glob=None, sort_policy="module-coverage",
            workdir=str(workdir)))

    assert decision.route == Route.ASK_USER
    # P2-8: 引导命令必须逐字可执行(真实参数形态)
    assert "--classes all --threshold 80" in decision.question
    assert "--classes top:N" in decision.question
    assert "--all" not in decision.question
    assert "--top N" not in decision.question
    resume_continue = decision.resume[0]
    assert resume_continue.script == "batch_init.py"
    assert "--classes" in resume_continue.params
    assert "all" in resume_continue.params
    # P3-12: diff_mode 落盘, 断点续跑时 batch_state 记录真实值(旧恒 three)
    data = _json.loads((workdir / "batch_candidates.json").read_text(
        encoding="utf-8"))
    assert data["diff_mode"] == "two"
    assert data["target_branch"] == "master"
    assert data["candidates"][0]["fqcn"] == "com.foo.Foo"


# --------------------------------------------------------------------------- #
# batch_update P1-1: 子代理未启动类内迭代 → ask_user, 不再静默标 done
# --------------------------------------------------------------------------- #
def test_batch_update_unstarted_class_asks_user(tmp_path):
    """in_progress 类 state 可读但无进行中方法且仍有 pending: 不得静默标
    done(0 轮、基线率冒充 final_rate 的假落账), 应 ask_user 附 3 个 resume。"""
    import argparse
    from unittest.mock import MagicMock, patch
    from batch_update import handler as update_handler

    workdir = tmp_path / "batchwork"
    workdir.mkdir(parents=True, exist_ok=True)
    entry = BatchClassEntry(fqcn="com.example.A", simple_name="A",
                            source_file="A.java", module=".",
                            workdir=str(tmp_path / "classes" / "A"),
                            status="in_progress", baseline_rate=40.0)
    batch_state = BatchState(project_root=str(tmp_path), target_branch="master",
                             threshold=80.0, classes=[entry])
    (workdir / config.BATCH_STATE_FILENAME).write_text(
        json.dumps(batch_state.to_dict(), ensure_ascii=False), encoding="utf-8")

    pending = MethodCoverage(key=MethodKey("doStuff", "()V"), covered=0,
                             missed=10, status=MethodStatus.PENDING)
    class_state = State(project_root=str(tmp_path), target_class="com.example.A",
                        module=".", threshold=80.0, batch_mode=True,
                        class_coverage=ClassCoverage.of(0, 10), methods=[pending])

    locked = MagicMock()
    locked.__enter__ = MagicMock(return_value=None)
    locked.__exit__ = MagicMock(return_value=False)
    with patch("batch_update.setup_logger"), \
         patch("batch_update.batch_lock", return_value=locked), \
         patch("batch_update.StateStore") as MockStore:
        MockStore.return_value.load.return_value = class_state
        decision, _ = update_handler(argparse.Namespace(
            project_root=str(tmp_path), fqcn="com.example.A", skip_class=None,
            reason=None, mark_failed=None, workdir=str(workdir)))

    assert decision.route == Route.ASK_USER
    assert "子代理未开始类内迭代" in decision.question
    assert "继续委派" in decision.question
    assert "跳过该类" in decision.question
    assert "终止批量" in decision.question
    options = {r.option for r in decision.resume}
    assert options == {"continue", "skip_class", "terminate"}
    skip_resume = next(r for r in decision.resume if r.option == "skip_class")
    assert skip_resume.script == "batch_update.py"
    assert "--skip-class" in skip_resume.params
    # 落账不失真: 状态保持 in_progress 等待用户决策
    data = json.loads((workdir / config.BATCH_STATE_FILENAME).read_text(
        encoding="utf-8"))
    assert data["classes"][0]["status"] == "in_progress"


# --------------------------------------------------------------------------- #
# batch_init P1-2: 桩兜底 + 假 finish 拦截(零报告不 exit 0) + 失败回滚
# --------------------------------------------------------------------------- #
def test_batch_init_no_report_raises_and_rolls_back_stub(tmp_path):
    """mvn "成功"但零模块产出 jacoco 报告: fail-closed 抛 exec_error(旧行为
    全类剔为"无覆盖率数据"后 exit 0 假 finish); 同时回滚本次创建的桩测试。"""
    import argparse
    from unittest.mock import MagicMock, patch
    from jaut.proc import CommandResult
    from jaut.cli import StepError
    from batch_init import handler as init_handler

    workdir = tmp_path / "batchwork"
    workdir.mkdir(parents=True, exist_ok=True)
    # 候选类: 无任何已存在测试类 → collect 会创建桩
    candidates = [{
        "fqcn": "com.foo.Solo", "source_file": "src/main/java/com/foo/Solo.java",
        "module": ".", "diff_add": 5, "diff_del": 0,
        "has_existing_tests": False, "method_count": 1,
    }]
    (workdir / "batch_candidates.json").write_text(
        json.dumps({"target_branch": "master", "diff_mode": "two",
                    "candidates": candidates, "sort_policy": "module-coverage"}),
        encoding="utf-8")

    locked = MagicMock()
    locked.__enter__ = MagicMock(return_value=None)
    locked.__exit__ = MagicMock(return_value=False)
    with patch("batch_init.setup_logger"), \
         patch("batch_init.gitops.record_git_baseline", return_value={}), \
         patch("batch_init.gitops.current_branch", return_value="feat"), \
         patch("batch_init.maven.is_multi_module", return_value=False), \
         patch("batch_init.maven.clean_jacoco_dirs"), \
         patch("batch_init.maven.clean_surefire_dirs", return_value=[]), \
         patch("batch_init.maven.build_batch_mvn_cmd", return_value=["mvn", "test"]), \
         patch("batch_init.maven.run_mvn",
               return_value=CommandResult(ok=True, returncode=0)), \
         patch("batch_init.jacoco.aggregate_jacoco_xml", return_value={}):
        with pytest.raises(StepError) as ei:
            init_handler(argparse.Namespace(
                project_root=str(tmp_path), classes="all", threshold=80.0,
                target="auto", coverage_exclude=None,
                jacoco_version="0.8.12", workdir=str(workdir)))

    assert "所有涉及模块均未产出" in ei.value.decision.summary
    # 桩测试文件已回滚: 不在用户项目留下意外文件
    stub = (tmp_path / "src" / "test" / "java" / "com" / "foo" / "SoloTest.java")
    assert not stub.exists()
    # 失败不落 batch_state(下次可重跑 init)
    assert not (workdir / config.BATCH_STATE_FILENAME).exists()


def test_batch_init_mvn_failure_rolls_back_stub(tmp_path):
    """mvn 失败: StepError 且桩测试文件回滚(不污染用户项目)。"""
    import argparse
    from unittest.mock import MagicMock, patch
    from jaut.proc import CommandResult
    from jaut.cli import StepError
    from batch_init import handler as init_handler

    workdir = tmp_path / "batchwork"
    workdir.mkdir(parents=True, exist_ok=True)
    candidates = [{
        "fqcn": "com.foo.Solo", "source_file": "src/main/java/com/foo/Solo.java",
        "module": ".", "diff_add": 5, "diff_del": 0,
        "has_existing_tests": False, "method_count": 1,
    }]
    (workdir / "batch_candidates.json").write_text(
        json.dumps({"target_branch": "master", "diff_mode": "two",
                    "candidates": candidates, "sort_policy": "module-coverage"}),
        encoding="utf-8")

    locked = MagicMock()
    locked.__enter__ = MagicMock(return_value=None)
    locked.__exit__ = MagicMock(return_value=False)
    with patch("batch_init.setup_logger"), \
         patch("batch_init.gitops.record_git_baseline", return_value={}), \
         patch("batch_init.gitops.current_branch", return_value="feat"), \
         patch("batch_init.maven.is_multi_module", return_value=False), \
         patch("batch_init.maven.clean_jacoco_dirs"), \
         patch("batch_init.maven.clean_surefire_dirs", return_value=[]), \
         patch("batch_init.maven.build_batch_mvn_cmd", return_value=["mvn", "test"]), \
         patch("batch_init.maven.run_mvn",
               return_value=CommandResult(ok=False, returncode=1,
                                          stderr="BUILD FAILURE")):
        with pytest.raises(StepError) as ei:
            init_handler(argparse.Namespace(
                project_root=str(tmp_path), classes="all", threshold=80.0,
                target="auto", coverage_exclude=None,
                jacoco_version="0.8.12", workdir=str(workdir)))

    assert "mvn 执行失败" in ei.value.decision.summary
    stub = (tmp_path / "src" / "test" / "java" / "com" / "foo" / "SoloTest.java")
    assert not stub.exists()
