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
