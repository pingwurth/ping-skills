"""state 单测: State 序列化往返、from_dict 容错默认、StateStore 原子读写、
batch_state.json 共享读写(save/load_batch_state)。"""

from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import patch

from jaut import config
from jaut.models import (
    MANUAL_SKIP_REASON,
    BatchClassEntry,
    BatchState,
    ClassCoverage,
    MethodCoverage,
    MethodKey,
    MethodStatus,
    SKIP_SOURCE_AUTO,
    SKIP_SOURCE_MANUAL,
    State,
    TestOutcome,
)
from jaut.state import StateStore, default_workdir, load_batch_state, save_batch_state


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


def test_state_round_trip_skip_source():
    """跳过来源序列化往返: 手动/自动可区分, 未跳过的方法不写出该字段。"""
    s = _sample_state()
    untouched = MethodCoverage(key=MethodKey("bar", "(int)"), covered=5, missed=5)
    s.methods = [s.methods[0], untouched]
    s.methods[0].status = MethodStatus.SKIPPED
    s.methods[0].skip_reason = MANUAL_SKIP_REASON
    s.methods[0].skip_source = SKIP_SOURCE_MANUAL
    data = s.to_dict()
    assert data["methods"][0]["skip_source"] == "manual"
    assert "skip_source" not in data["methods"][1]

    restored = State.from_dict(data)
    assert restored.methods[0].skip_source == SKIP_SOURCE_MANUAL
    assert restored.methods[0].is_auto_skipped is False   # 手动跳过不计自动跳过
    assert restored.methods[1].skip_source is None


def test_from_dict_migrates_legacy_skip_source():
    """旧 state.json 只写 skip_reason: 按原因迁移来源, 手动/自动仍可区分。

    旧版本手动跳过(skip_reason=MANUAL_SKIP_REASON)与自动跳过共用 skip_reason 一个字段,
    迁移后前者标 manual, 后者标 auto, 不把手动跳过计进"总自动跳过方法数"。
    """
    def _legacy(name, status="skipped", **extra):
        return {"name": name, "desc": "()V", "covered": 0, "missed": 10,
                "status": status, **extra}

    manual = MethodCoverage.from_dict(_legacy("m1", skip_reason=MANUAL_SKIP_REASON))
    auto = MethodCoverage.from_dict(
        _legacy("m2", skip_reason="单方法迭代已达上限 8 轮仍未达标(当前 50.0%)"))
    group_filtered = MethodCoverage.from_dict(_legacy("m3"))
    assert manual.skip_source == SKIP_SOURCE_MANUAL and manual.is_auto_skipped is False
    assert auto.skip_source == SKIP_SOURCE_AUTO and auto.is_auto_skipped is True
    assert group_filtered.skip_source is None    # 组外过滤跳过: 无原因也无来源
    # 显式 skip_source 优先于按原因推断
    explicit = MethodCoverage.from_dict(
        _legacy("m4", skip_reason=MANUAL_SKIP_REASON, skip_source=SKIP_SOURCE_AUTO))
    assert explicit.skip_source == SKIP_SOURCE_AUTO


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


def test_state_round_trip_git_main_source_files():
    """删除检测快照序列化往返; 为空时不写出该字段, 旧 state.json 读取为空列表。"""
    s = _sample_state()
    s.git_main_source_files = ["src/main/java/com/x/A.java", "src/main/java/com/x/B.java"]
    d = s.to_dict()
    assert d["git_main_source_files"] == s.git_main_source_files
    assert State.from_dict(d).git_main_source_files == s.git_main_source_files
    empty = State(project_root="/r", target_class="c").to_dict()
    assert "git_main_source_files" not in empty
    assert State.from_dict({"project_root": "/r", "target_class": "c"}).git_main_source_files == []


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


@patch("jaut.state.module_logger")
def test_locked_stale_idle_lock_no_warning(mock_logger, tmp_path: Path):
    """空闲留存的锁文件(>24 小时未刷新但无人持有)属正常设计, 不产生告警。"""
    store = StateStore(tmp_path)
    lock_file = tmp_path / (config.STATE_FILENAME + ".lock")
    lock_file.write_text("", encoding="utf-8")
    stale = time.time() - 2 * 86400
    os.utime(lock_file, (stale, stale))

    with store.locked():
        assert lock_file.stat().st_mtime > stale  # 获锁仍刷新 mtime

    mock_logger().warning.assert_not_called()


@patch("jaut.state.module_logger")
@patch("jaut.state._lock_held", return_value=True)
def test_locked_stale_held_lock_warns_without_delete_hint(mock_held, mock_logger, tmp_path: Path):
    """锁 >24 小时未刷新且仍被持有 => 提示疑似挂起, 不再建议手动删除锁文件。"""
    store = StateStore(tmp_path)
    lock_file = tmp_path / (config.STATE_FILENAME + ".lock")
    lock_file.write_text("", encoding="utf-8")
    stale = time.time() - 2 * 86400
    os.utime(lock_file, (stale, stale))

    with store.locked():
        pass

    mock_held.assert_called_once()
    msg = mock_logger().warning.call_args[0][0]
    assert "24 小时" in msg and "删除" not in msg


def test_lock_held_probe_tracks_real_lock(tmp_path: Path):
    """_lock_held 非阻塞探测真实持锁状态, 且探测后立即释放不残留持锁。"""
    from jaut.state import _lock_file, _lock_held, _unlock_file

    lock_file = tmp_path / (config.STATE_FILENAME + ".lock")
    lock_file.write_text("", encoding="utf-8")
    assert _lock_held(lock_file) is False

    holder = open(lock_file, "a", encoding="utf-8")
    try:
        _lock_file(holder)
        assert _lock_held(lock_file) is True
    finally:
        _unlock_file(holder)
        holder.close()
    assert _lock_held(lock_file) is False


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


# --------------------------------------------------------------------------- #
# batch_state.json 共享读写(batch_init/next/update/finish 四入口上移复用)
# --------------------------------------------------------------------------- #
def _sample_batch_state(project_root: str) -> BatchState:
    return BatchState(
        project_root=project_root, target_branch="master", diff_mode="two",
        threshold=80.0, classes=[
            BatchClassEntry(fqcn="com.A", simple_name="A", source_file="A.java",
                            module=".", workdir="wd", status="pending",
                            baseline_rate=40.0),
        ])


def test_save_and_load_batch_state_round_trip(tmp_path: Path):
    """共享写入原子(tmp+fsync+replace)且可读回等价对象。"""
    path = tmp_path / "batch_state.json"
    bs = _sample_batch_state(str(tmp_path))
    save_batch_state(path, bs)
    assert path.is_file()
    loaded = load_batch_state(path)
    assert loaded is not None
    assert loaded == bs


def test_load_batch_state_missing_returns_none(tmp_path: Path):
    assert load_batch_state(tmp_path / "absent.json") is None


def test_load_batch_state_corrupt_json_returns_none(tmp_path: Path):
    """非法 JSON 降级 None(入口转 StepError, 不裸穿透 traceback)。"""
    path = tmp_path / "batch_state.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_batch_state(path) is None


def test_load_batch_state_non_object_returns_none(tmp_path: Path):
    path = tmp_path / "batch_state.json"
    path.write_text("[1, 2]", encoding="utf-8")
    assert load_batch_state(path) is None


def test_load_batch_state_bad_field_types_returns_none(tmp_path: Path):
    """字段类型坏(schema_version 非数字/classes 非列表)降级 None。"""
    path = tmp_path / "batch_state.json"
    path.write_text('{"schema_version": "not-a-number"}', encoding="utf-8")
    assert load_batch_state(path) is None
    path.write_text('{"classes": {"a": 1}}', encoding="utf-8")
    assert load_batch_state(path) is None
