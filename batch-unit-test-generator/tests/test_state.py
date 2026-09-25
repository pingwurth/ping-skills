"""state 单测: State 序列化往返、from_dict 容错默认、StateStore 原子读写。"""

from __future__ import annotations

from pathlib import Path

from jaut import config
from jaut.models import (
    ClassCoverage,
    MethodCoverage,
    MethodKey,
    MethodStatus,
    State,
    TestOutcome,
)
from jaut.state import StateStore, default_workdir


def _sample_state() -> State:
    m = MethodCoverage(key=MethodKey("foo", "(int)"), covered=8, missed=2,
                       status=MethodStatus.PENDING, round_rates=[50.0, 80.0],
                       round_test_results=[TestOutcome(1, 0)])
    return State(project_root="/repo", workdir="/repo/.agent/x", threshold=80.0,
                 target_class="com.x.Foo", target_method=None, source_file="/repo/Foo.java",
                 module=".", mvn_log="/repo/mvn.log", git_baseline={"a": "abc123"},
                 iteration=2, global_iteration=5, current_method=MethodKey("foo", "(int)"),
                 class_coverage=ClassCoverage.of(80, 20), methods=[m])


def test_default_workdir():
    assert default_workdir("/repo") == Path("/repo").joinpath(*config.WORKDIR_PARTS)


def test_state_round_trip():
    s = _sample_state()
    restored = State.from_dict(s.to_dict())
    assert restored.target_class == "com.x.Foo"
    assert restored.global_iteration == 5
    assert restored.current_method == MethodKey("foo", "(int)")
    assert restored.methods[0].round_rates == [50.0, 80.0]
    assert restored.methods[0].round_test_results[0].failures == 1
    assert restored.class_coverage.covered == 80


def test_state_round_trip_budget_window_fields():
    """预算追加窗口与规范校验计数序列化往返(问题1/问题3)。"""
    s = _sample_state()
    s.method_round_bonus = 3
    s.global_round_bonus = 6
    s.validate_fail_streak = 4
    restored = State.from_dict(s.to_dict())
    assert restored.method_round_bonus == 3
    assert restored.global_round_bonus == 6
    assert restored.validate_fail_streak == 4


def test_state_round_trip_class_round_bonus():
    """类级预算追加窗口(--unskip 落地)序列化往返; 为 0 时不写出该字段。"""
    s = _sample_state()
    s.batch_mode = True
    s.class_round_used = 30
    assert "class_round_bonus" not in s.to_dict()   # 未追加时不落盘
    s.class_round_bonus = 30
    data = s.to_dict()
    assert data["class_round_bonus"] == 30
    assert State.from_dict(data).class_round_bonus == 30
    legacy = State.from_dict({"project_root": "/r", "target_class": "c"})
    assert legacy.class_round_bonus == 0


def test_state_round_trip_skip_reason():
    """跳过原因序列化往返; 未跳过的方法不写出该字段, 旧 state.json 读取为 None。"""
    s = _sample_state()
    untouched = MethodCoverage(key=MethodKey("bar", "(int)"), covered=5, missed=5)
    s.methods = [s.methods[0], untouched]
    s.methods[0].status = MethodStatus.SKIPPED
    s.methods[0].skip_reason = "连续 3 轮测试失败不收敛(每轮 Failures/Errors: [(1, 0)])"
    data = s.to_dict()
    assert data["methods"][0]["skip_reason"].startswith("连续 3 轮测试失败不收敛")
    assert "skip_reason" not in data["methods"][1]
    restored = State.from_dict(data)
    assert restored.methods[0].skip_reason == s.methods[0].skip_reason
    assert restored.methods[1].skip_reason is None


def test_from_dict_budget_window_fields_default_zero():
    """旧版本 state.json 无这些字段时默认 0(兼容断点续跑)。"""
    s = State.from_dict({"project_root": "/r", "target_class": "com.x.Foo"})
    assert s.method_round_bonus == 0
    assert s.global_round_bonus == 0
    assert s.validate_fail_streak == 0


def test_from_dict_tolerates_missing_keys():
    s = State.from_dict({"project_root": "/r", "target_class": "com.x.Foo"})
    assert s.global_iteration == 0            # 新增字段的默认值
    assert s.threshold == config.DEFAULT_THRESHOLD
    assert s.methods == []
    assert s.current_method is None
    assert s.jacoco_version == config.DEFAULT_JACOCO_VERSION
    assert s.coverage_excludes == []


def test_optional_fields_omitted_when_unset():
    d = State(project_root="/r", target_class="c").to_dict()
    assert "plan" not in d and "test_class_file" not in d
    assert "worktree_branch" not in d and "final_test_summary" not in d
    assert "coverage_excludes" not in d


def test_coverage_excludes_round_trip():
    """coverage_excludes 序列化往返; 为空时不写入 state.json。"""
    s = _sample_state()
    s.coverage_excludes = ["com.foo.dto.*", "*$Builder"]
    d = s.to_dict()
    assert d["coverage_excludes"] == ["com.foo.dto.*", "*$Builder"]
    assert State.from_dict(d).coverage_excludes == ["com.foo.dto.*", "*$Builder"]


def test_state_round_trip_finish_report_fields():
    """worktree_branch / final_test_summary / initial_rate 序列化往返。"""
    s = _sample_state()
    s.worktree_branch = "worktree20260908"
    s.final_test_summary = {"tests": 3, "failures": 0, "errors": 0, "skipped": 0}
    s.methods[0].initial_rate = 42.5
    d = s.to_dict()
    assert d["worktree_branch"] == "worktree20260908"
    assert d["final_test_summary"]["tests"] == 3
    assert d["methods"][0]["initial_rate"] == 42.5

    restored = State.from_dict(d)
    assert restored.worktree_branch == "worktree20260908"
    assert restored.final_test_summary["skipped"] == 0
    assert restored.methods[0].initial_rate == 42.5


def test_state_finish_report_fields_default_none():
    """旧版本 state.json 缺新字段时容忍加载, 默认 None。"""
    s = State.from_dict({"project_root": "/r", "target_class": "c"})
    assert s.worktree_branch is None
    assert s.final_test_summary is None
    assert all(m.initial_rate is None for m in s.methods)


def test_state_store_atomic_round_trip(tmp_path: Path):
    store = StateStore(tmp_path)
    assert store.load() is None                # 尚无文件
    store.save(_sample_state())
    assert store.path.is_file()
    assert not (tmp_path / (config.STATE_FILENAME + ".tmp")).exists()  # 临时文件已替换
    loaded = store.load()
    assert loaded is not None and loaded.target_class == "com.x.Foo"


def test_state_store_load_corrupt_returns_none(tmp_path: Path):
    store = StateStore(tmp_path)
    store.path.write_text("{ not json", encoding="utf-8")
    assert store.load() is None


def test_write_coverage(tmp_path: Path):
    store = StateStore(tmp_path)
    p = store.write_coverage({"target_class": "com.x.Foo", "methods": []})
    assert p.is_file() and p.name == config.COVERAGE_FILENAME


def test_locked_creates_lock_file(tmp_path: Path):
    store = StateStore(tmp_path)
    with store.locked():
        assert (tmp_path / (config.STATE_FILENAME + ".lock")).is_file()


def test_locked_sequential_round_trip(tmp_path: Path):
    store = StateStore(tmp_path)
    with store.locked():
        store.save(_sample_state())
    with store.locked():
        loaded = store.load()
    assert loaded is not None and loaded.target_class == "com.x.Foo"


def test_state_class_budget_properties():
    """类级预算口径由模型自身计算: 非批量模式恒不耗尽, 追加窗口计入有效预算。"""
    base = config.batch_class_round_budget()
    single_mode = State(project_root="/r", target_class="c",
                        class_round_used=base * 10)
    assert single_mode.class_budget_exhausted is False   # 单类技能语义不参与判定

    batch = State(project_root="/r", target_class="c", batch_mode=True,
                  class_round_used=base, class_round_bonus=base,
                  exploration_mode=True)
    assert batch.class_round_budget == base * config.EXPLORATION_MODE_MULTIPLIER + base
    assert batch.class_budget_exhausted is False
    batch.class_round_used = batch.class_round_budget
    assert batch.class_budget_exhausted is True


def test_state_skip_pending_methods_sweeps_with_unified_reason():
    """类级预算耗尽的批量跳过与原因串由模型单点提供(verify_coverage / batch_finish 共用)。"""
    base = config.batch_class_round_budget()
    pending = MethodCoverage(key=MethodKey("a", "()V"), covered=1, missed=9,
                             status=MethodStatus.PENDING)
    done = MethodCoverage(key=MethodKey("b", "()V"), covered=5, missed=5,
                          status=MethodStatus.DONE)
    state = State(project_root="/r", target_class="c", batch_mode=True,
                  class_round_used=base, methods=[pending, done])

    assert state.class_budget_exhausted is True
    reason = state.class_budget_skip_reason
    skipped = state.skip_pending_methods(reason)

    assert [m.key.name for m in skipped] == ["a"]
    assert pending.status == MethodStatus.SKIPPED
    assert pending.skip_reason == f"类级预算耗尽({base}/{base} 轮), 未达标方法不再迭代"
    assert done.status == MethodStatus.DONE        # 非 pending 不被扫到
    assert done.skip_reason is None
