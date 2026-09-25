"""batch_finish.handler() 单测: 未达标终态化与重验判定(Critical#1 回归)。

背景: 自动跳过语义落地后, "队列空 + 类级未达标" 曾被无条件置 recheck,
导致 batch_finish <-> batch_next 零进展空转; 现改为: 仍有补测空间 ->
recheck 重入队; 无补测空间 -> unmet 终态(不再重验); 环境不可信 ->
unverified 非终态(重跑本脚本复核)。另覆盖失败归因须含本类非标准命名
测试类(FooTests/FooIT)的回归。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from jaut import config
from jaut.models import (
    BatchClassEntry,
    BatchState,
    ClassCoverage,
    FailedCase,
    MethodCoverage,
    MethodKey,
    MethodStatus,
    Route,
    State,
    TestResult,
)
from scripts.batch_finish import handler


def _make_args(project_root: Path, workdir: Path) -> argparse.Namespace:
    return argparse.Namespace(project_root=str(project_root), skip_mvn=True,
                              workdir=str(workdir))


def _locked_mock():
    """创建正确的 locked context manager mock。"""
    mock = MagicMock()
    mock.__enter__ = MagicMock(return_value=None)
    mock.__exit__ = MagicMock(return_value=False)
    return mock


def _make_entry(tmp_path: Path, **overrides) -> BatchClassEntry:
    defaults = dict(
        fqcn="com.example.A", simple_name="A", source_file="A.java",
        module=".", workdir=str(tmp_path / "classes" / "A"), status="done",
        baseline_rate=40.0)
    defaults.update(overrides)
    return BatchClassEntry(**defaults)


def _write_batch_state(workdir: Path, entry: BatchClassEntry,
                       threshold: float = 80.0) -> None:
    workdir.mkdir(parents=True, exist_ok=True)
    batch_state = BatchState(project_root=str(workdir), target_branch="master",
                             threshold=threshold, classes=[entry])
    (workdir / config.BATCH_STATE_FILENAME).write_text(
        json.dumps(batch_state.to_dict(), ensure_ascii=False), encoding="utf-8")


def _read_batch_state(workdir: Path) -> dict:
    return json.loads((workdir / config.BATCH_STATE_FILENAME).read_text(encoding="utf-8"))


def _make_class_state(tmp_path: Path, methods: list[MethodCoverage],
                      **overrides) -> State:
    defaults = dict(
        project_root=str(tmp_path), target_class="com.example.A",
        module=".", threshold=80.0, batch_mode=True, class_round_used=5,
        class_coverage=ClassCoverage.of(40, 60), methods=methods)
    defaults.update(overrides)
    return State(**defaults)


def _green(tests: int = 10) -> TestResult:
    return TestResult(tests=tests, failures=0, errors=0, report_found=True)


def _run(tmp_path: Path, workdir: Path, class_state: State, final_rate: float,
         test: TestResult):
    """以 mock 环境运行 handler(跳过 mvn/锁/日志, 真实走判定与落盘)。"""
    with patch("scripts.batch_finish.setup_logger"), \
         patch("scripts.batch_finish.batch_lock", return_value=_locked_mock()), \
         patch("scripts.batch_finish.jacoco.aggregate_jacoco_xml", return_value={}), \
         patch("scripts.batch_finish.jacoco.aggregate_class_coverage",
               return_value=MagicMock(rate=final_rate)), \
         patch("scripts.batch_finish.surefire.aggregate_surefire_reports",
               return_value=test), \
         patch("scripts.batch_finish.StateStore") as MockStore:
        MockStore.return_value.locked.return_value = _locked_mock()
        MockStore.return_value.load.return_value = class_state
        return handler(_make_args(tmp_path, workdir))


def test_handler_unmet_terminal_when_no_compensable_methods(tmp_path):
    """终验不达标且方法均已自动跳过 -> unmet 终态, 不再 recheck 重验。"""
    workdir = tmp_path / "batchwork"
    _write_batch_state(workdir, _make_entry(tmp_path))
    skipped = MethodCoverage(
        key=MethodKey("doStuff", "()V"), covered=10, missed=20,
        status=MethodStatus.SKIPPED,
        skip_reason="连续 3 轮测试失败, 自动跳过")
    class_state = _make_class_state(tmp_path, methods=[skipped])

    decision, _ = _run(tmp_path, workdir, class_state, final_rate=33.3,
                       test=_green())

    assert decision.route == Route.FINISH
    assert decision.exit_code == config.EXIT_OK
    data = _read_batch_state(workdir)
    assert data["classes"][0]["status"] == "unmet"
    assert "未达标终态" in data["classes"][0]["skip_reason"]
    assert "未达标(1 类)" in decision.report


def test_handler_recheck_when_compensable_methods_exist(tmp_path):
    """终验不达标但存在未达标 done 方法 -> 打回 pending 并 recheck 重验。"""
    workdir = tmp_path / "batchwork"
    _write_batch_state(workdir, _make_entry(tmp_path))
    done = MethodCoverage(
        key=MethodKey("doStuff", "()V"), covered=10, missed=20,
        status=MethodStatus.DONE, initial_rate=0.0, round_rates=[10.0])
    class_state = _make_class_state(tmp_path, methods=[done])

    decision, _ = _run(tmp_path, workdir, class_state, final_rate=33.3,
                       test=_green())

    assert decision.route == Route.BATCH_NEXT
    assert decision.exit_code == config.EXIT_CONTINUE
    assert done.status == MethodStatus.PENDING   # done 且未达标 -> 打回
    assert done.round_rates == []                # 轨迹复位
    assert class_state.final_checked is False
    data = _read_batch_state(workdir)
    assert data["classes"][0]["status"] == "recheck"


def test_handler_unmet_when_class_budget_exhausted(tmp_path):
    """类级预算耗尽: 剩余 pending 标记跳过, 直接 unmet 终态(不空转重验)。"""
    workdir = tmp_path / "batchwork"
    _write_batch_state(workdir, _make_entry(tmp_path))
    pending = MethodCoverage(
        key=MethodKey("doStuff", "()V"), covered=10, missed=20,
        status=MethodStatus.PENDING)
    class_state = _make_class_state(
        tmp_path, methods=[pending],
        class_round_used=config.batch_class_round_budget())

    decision, _ = _run(tmp_path, workdir, class_state, final_rate=33.3,
                       test=_green())

    assert decision.route == Route.FINISH
    assert pending.status == MethodStatus.SKIPPED
    assert "类级预算耗尽" in pending.skip_reason
    data = _read_batch_state(workdir)
    assert data["classes"][0]["status"] == "unmet"
    assert "类级预算已耗尽" in data["classes"][0]["skip_reason"]


def test_handler_skips_mvn_when_all_classes_are_terminal(tmp_path):
    """全部类为终态(skipped/unmet/failed): 不跑全量 mvn, 直接出最终报告。"""
    workdir = tmp_path / "batchwork"
    entries = [
        _make_entry(tmp_path, fqcn="com.example.Skipped", simple_name="Skipped",
                    status="skipped", skip_reason="用户跳过"),
        _make_entry(tmp_path, fqcn="com.example.Unmet", simple_name="Unmet",
                    status="unmet", skip_reason="未达标终态: 覆盖率 33.3% < 80.0%"),
        _make_entry(tmp_path, fqcn="com.example.Failed", simple_name="Failed",
                    status="failed", skip_reason="state.json 不存在"),
    ]
    workdir.mkdir(parents=True, exist_ok=True)
    batch_state = BatchState(project_root=str(workdir), target_branch="master",
                             threshold=80.0, classes=entries)
    (workdir / config.BATCH_STATE_FILENAME).write_text(
        json.dumps(batch_state.to_dict(), ensure_ascii=False), encoding="utf-8")

    with patch("scripts.batch_finish.setup_logger"), \
         patch("scripts.batch_finish.batch_lock", return_value=_locked_mock()), \
         patch("scripts.batch_finish.maven.clean_jacoco_dirs") as mock_clean, \
         patch("scripts.batch_finish.maven.build_batch_mvn_cmd") as mock_mvn, \
         patch("scripts.batch_finish.maven.run_mvn") as mock_run, \
         patch("scripts.batch_finish.jacoco.aggregate_jacoco_xml") as mock_xml, \
         patch("scripts.batch_finish.surefire.aggregate_surefire_reports") as mock_sf:
        # skip_mvn=False: 这里要验证的是脚本自身的"无活跃类"短路, 而非外部跳过
        args = argparse.Namespace(project_root=str(tmp_path), skip_mvn=False,
                                  workdir=str(workdir))
        decision, _ = handler(args)

    assert decision.route == Route.FINISH
    assert decision.exit_code == config.EXIT_OK
    mock_clean.assert_not_called()    # 不清理旧报告
    mock_mvn.assert_not_called()      # 不白跑全量 mvn
    mock_run.assert_not_called()
    mock_xml.assert_not_called()      # 不解析覆盖率
    mock_sf.assert_not_called()
    assert "未达标 1" in decision.summary and "跳过 1" in decision.summary
    assert "失败 1" in decision.summary
    assert "com.example.Unmet" in decision.report
    assert "com.example.Failed" in decision.report


def test_handler_terminal_failed_class_is_not_revaluated(tmp_path):
    """failed 类只进报告: 不再被当成活跃类重新测覆盖率/改状态。"""
    workdir = tmp_path / "batchwork"
    done_entry = _make_entry(tmp_path, fqcn="com.example.A")
    failed_entry = _make_entry(tmp_path, fqcn="com.example.B", simple_name="B",
                               status="failed", skip_reason="state.json 不存在",
                               workdir=str(tmp_path / "classes" / "B_absent"))
    workdir.mkdir(parents=True, exist_ok=True)
    batch_state = BatchState(project_root=str(workdir), target_branch="master",
                             threshold=80.0, classes=[done_entry, failed_entry])
    (workdir / config.BATCH_STATE_FILENAME).write_text(
        json.dumps(batch_state.to_dict(), ensure_ascii=False), encoding="utf-8")

    done = MethodCoverage(key=MethodKey("doStuff", "()V"), covered=18, missed=2,
                          status=MethodStatus.DONE, initial_rate=50.0)
    class_state = _make_class_state(tmp_path, methods=[done])

    with patch("scripts.batch_finish.setup_logger"), \
         patch("scripts.batch_finish.batch_lock", return_value=_locked_mock()), \
         patch("scripts.batch_finish.jacoco.aggregate_jacoco_xml", return_value={}), \
         patch("scripts.batch_finish.jacoco.aggregate_class_coverage",
               return_value=MagicMock(rate=85.0)) as mock_cov, \
         patch("scripts.batch_finish.surefire.aggregate_surefire_reports",
               return_value=_green()), \
         patch("scripts.batch_finish.StateStore") as MockStore:
        MockStore.return_value.locked.return_value = _locked_mock()
        MockStore.return_value.load.return_value = class_state
        decision, _ = handler(_make_args(tmp_path, workdir))

    assert decision.route == Route.FINISH
    assert [c.args[2] for c in mock_cov.call_args_list] == ["com.example.A"]  # failed 不复核
    data = _read_batch_state(workdir)
    statuses = {c["fqcn"]: c["status"] for c in data["classes"]}
    assert statuses["com.example.B"] == "failed"   # 状态不被改写
    assert "失败 1" in decision.summary


def test_handler_forwards_coverage_excludes_to_final_measurement(tmp_path):
    """终验复核沿用类 state.json 的 coverage_excludes: 否则排除配置被静默忽略, 覆盖率回落。"""
    workdir = tmp_path / "batchwork"
    _write_batch_state(workdir, _make_entry(tmp_path))
    done = MethodCoverage(key=MethodKey("doStuff", "()V"), covered=18, missed=2,
                          status=MethodStatus.DONE, initial_rate=50.0)
    class_state = _make_class_state(
        tmp_path, methods=[done],
        coverage_excludes=["*$Builder", "*$MapperImpl"])

    with patch("scripts.batch_finish.setup_logger"), \
         patch("scripts.batch_finish.batch_lock", return_value=_locked_mock()), \
         patch("scripts.batch_finish.jacoco.aggregate_jacoco_xml") as mock_xml, \
         patch("scripts.batch_finish.jacoco.aggregate_class_coverage") as mock_cov, \
         patch("scripts.batch_finish.surefire.aggregate_surefire_reports",
               return_value=_green()), \
         patch("scripts.batch_finish.StateStore") as MockStore:
        mock_xml.return_value = {}
        mock_cov.return_value = MagicMock(rate=85.0)
        MockStore.return_value.locked.return_value = _locked_mock()
        MockStore.return_value.load.return_value = class_state
        decision, _ = handler(_make_args(tmp_path, workdir))

    excludes = ["*$Builder", "*$MapperImpl"]
    assert mock_xml.call_args.kwargs["excludes"] == excludes
    assert mock_cov.call_args.kwargs["excludes"] == excludes
    assert decision.route == Route.FINISH


def test_handler_confirms_done_when_other_class_tests_fail(tmp_path):
    """失败用例属于别的测试类: 本类自身达标 -> done, 不被整批红灯打成终态 unmet。

    根因: tests_green 是整批聚合值, 直接用它判每类会把无辜的类终态化(unmet 不可复活)。
    """
    workdir = tmp_path / "batchwork"
    _write_batch_state(workdir, _make_entry(tmp_path))
    done = MethodCoverage(key=MethodKey("doStuff", "()V"), covered=18, missed=2,
                          status=MethodStatus.DONE, initial_rate=50.0)
    class_state = _make_class_state(tmp_path, methods=[done],
                                    test_class_simple="ATest")   # 本类测试类
    red_elsewhere = TestResult(
        tests=10, failures=1, errors=0, report_found=True,
        failed_cases=[FailedCase(class_name="com.example.BTest", method="m",
                                 type="java.lang.AssertionError", message="boom")])

    decision, _ = _run(tmp_path, workdir, class_state, final_rate=85.0,
                       test=red_elsewhere)

    assert decision.route == Route.FINISH
    data = _read_batch_state(workdir)
    assert data["classes"][0]["status"] == "done"   # 无辜类不被终态化
    assert class_state.final_checked is True
    assert "达标 1" in decision.summary


def test_handler_unmet_when_own_tests_fail_without_compensable_methods(tmp_path):
    """失败用例属于本类且无补测空间: unmet 终态, 原因标注为本类测试不绿。"""
    workdir = tmp_path / "batchwork"
    _write_batch_state(workdir, _make_entry(tmp_path))
    done = MethodCoverage(key=MethodKey("doStuff", "()V"), covered=18, missed=2,
                          status=MethodStatus.DONE, initial_rate=50.0)
    class_state = _make_class_state(tmp_path, methods=[done],
                                    test_class_simple="ATest")
    own_red = TestResult(
        tests=10, failures=1, errors=0, report_found=True,
        failed_cases=[FailedCase(class_name="com.example.ATest", method="m",
                                 type="java.lang.AssertionError", message="boom")])

    decision, _ = _run(tmp_path, workdir, class_state, final_rate=85.0, test=own_red)

    assert decision.route == Route.FINISH
    data = _read_batch_state(workdir)
    assert data["classes"][0]["status"] == "unmet"
    assert "本类测试不绿" in data["classes"][0]["skip_reason"]


def test_handler_unverified_when_environment_uncertain_without_pending(tmp_path):
    """surefire 报告缺失(环境不可信)且无补测空间: 置 unverified 非终态(待复核)。

    曾为 unmet 终态: unmet 不可复活, 会把"环境修好后即可确认"的类永久记成
    未达标; unverified 不在 _TERMINAL_STATUSES, 重跑 batch_finish 会重新终验。
    """
    workdir = tmp_path / "batchwork"
    _write_batch_state(workdir, _make_entry(tmp_path))
    done = MethodCoverage(key=MethodKey("doStuff", "()V"), covered=18, missed=2,
                          status=MethodStatus.DONE, initial_rate=50.0)
    class_state = _make_class_state(tmp_path, methods=[done],
                                    test_class_simple="ATest")

    decision, _ = _run(tmp_path, workdir, class_state, final_rate=85.0,
                       test=TestResult(tests=0, report_found=False))

    assert decision.route == Route.FINISH
    data = _read_batch_state(workdir)
    assert data["classes"][0]["status"] == "unverified"
    reason = data["classes"][0]["skip_reason"]
    assert "测试结果不可信" in reason
    assert "待环境复核" in reason
    assert "本类测试不绿" not in reason
    assert class_state.final_checked is False
    assert "待环境复核 1" in decision.summary
    assert "待环境复核" in decision.report


def test_handler_unverified_when_report_unparseable(tmp_path):
    """surefire 报告存在但无法解析(parse_errors>0): 结果不可信, 不得假标达标。

    回归: env_uncertain 曾只判 report_found/uncleaned, 漏掉 parse_errors ——
    不可解析的报告不产出 failed_cases, own_failures 为空, 类被 fail-open 标 done。
    """
    workdir = tmp_path / "batchwork"
    _write_batch_state(workdir, _make_entry(tmp_path))
    done = MethodCoverage(key=MethodKey("doStuff", "()V"), covered=18, missed=2,
                          status=MethodStatus.DONE, initial_rate=50.0)
    class_state = _make_class_state(tmp_path, methods=[done],
                                    test_class_simple="ATest")

    decision, _ = _run(tmp_path, workdir, class_state, final_rate=85.0,
                       test=TestResult(tests=0, report_found=True, parse_errors=2))

    assert decision.route == Route.FINISH
    data = _read_batch_state(workdir)
    assert data["classes"][0]["status"] == "unverified"   # 不得假标 done
    assert "测试结果不可信" in data["classes"][0]["skip_reason"]
    assert class_state.final_checked is False
    assert "待环境复核 1" in decision.summary


def test_handler_unverified_class_is_rechecked_after_env_fixed(tmp_path):
    """环境修复后重跑 batch_finish: unverified 类被重新终验, 达标则确认 done。"""
    workdir = tmp_path / "batchwork"
    _write_batch_state(workdir, _make_entry(tmp_path))
    done = MethodCoverage(key=MethodKey("doStuff", "()V"), covered=18, missed=2,
                          status=MethodStatus.DONE, initial_rate=50.0)
    class_state = _make_class_state(tmp_path, methods=[done],
                                    test_class_simple="ATest")

    # 第一次: 环境不可信 → unverified
    decision, _ = _run(tmp_path, workdir, class_state, final_rate=85.0,
                       test=TestResult(tests=0, report_found=False))
    assert _read_batch_state(workdir)["classes"][0]["status"] == "unverified"

    # 第二次(环境修复, 测试绿): unverified 为非终态 → 被重新终验 → done
    decision, _ = _run(tmp_path, workdir, class_state, final_rate=85.0,
                       test=_green())
    assert decision.route == Route.FINISH
    data = _read_batch_state(workdir)
    assert data["classes"][0]["status"] == "done"
    assert class_state.final_checked is True
    assert "达标 1" in decision.summary


def test_handler_unmet_when_own_tests_fail_with_nonstandard_name(tmp_path):
    """失败用例属于本类非标准命名的测试类(如 FooTests): 不得误判为"其他类的失败"。

    根因: 终验 mvn 执行 _find_test_classes 收集的 Test/Tests/IT 三种命名, 但
    失败归因只按 test_class_simple 匹配, FooTests 的失败被漏算 → 类被假标 done。
    """
    workdir = tmp_path / "batchwork"
    _write_batch_state(workdir, _make_entry(tmp_path))
    # 既有测试类 ATests.java(非 make_plan 固定的 ATest 命名)
    test_dir = tmp_path / "src" / "test" / "java" / "com" / "example"
    test_dir.mkdir(parents=True, exist_ok=True)
    (test_dir / "ATests.java").write_text("class ATests {}", encoding="utf-8")
    done = MethodCoverage(key=MethodKey("doStuff", "()V"), covered=18, missed=2,
                          status=MethodStatus.DONE, initial_rate=50.0)
    class_state = _make_class_state(tmp_path, methods=[done],
                                    test_class_simple="ATest")
    own_red = TestResult(
        tests=10, failures=1, errors=0, report_found=True,
        failed_cases=[FailedCase(class_name="com.example.ATests", method="m",
                                 type="java.lang.AssertionError", message="boom")])

    decision, _ = _run(tmp_path, workdir, class_state, final_rate=85.0, test=own_red)

    assert decision.route == Route.FINISH
    data = _read_batch_state(workdir)
    assert data["classes"][0]["status"] == "unmet"   # 不再被假标 done
    assert "本类测试不绿" in data["classes"][0]["skip_reason"]


def test_handler_rechecks_with_pending_when_environment_uncertain(tmp_path):
    """环境不可信但仍有 pending 方法: 走 recheck 重验, 且不改动方法状态。"""
    workdir = tmp_path / "batchwork"
    _write_batch_state(workdir, _make_entry(tmp_path))
    pending = MethodCoverage(key=MethodKey("doStuff", "()V"), covered=5, missed=5,
                             status=MethodStatus.PENDING)
    class_state = _make_class_state(tmp_path, methods=[pending],
                                    test_class_simple="ATest")

    decision, _ = _run(tmp_path, workdir, class_state, final_rate=50.0,
                       test=TestResult(tests=0, report_found=False))

    assert decision.route == Route.BATCH_NEXT
    assert pending.status == MethodStatus.PENDING   # 环境不可信时不打回/不跳过
    data = _read_batch_state(workdir)
    assert data["classes"][0]["status"] == "recheck"


def test_handler_confirms_done_when_met(tmp_path):
    """终验达标且测试绿 -> 确认 done 并进入最终报告。"""
    workdir = tmp_path / "batchwork"
    _write_batch_state(workdir, _make_entry(tmp_path))
    done = MethodCoverage(key=MethodKey("doStuff", "()V"), covered=18, missed=2,
                          status=MethodStatus.DONE, initial_rate=50.0)
    class_state = _make_class_state(tmp_path, methods=[done])

    decision, _ = _run(tmp_path, workdir, class_state, final_rate=85.0,
                       test=_green())

    assert decision.route == Route.FINISH
    assert decision.exit_code == config.EXIT_OK
    assert "达标 1" in decision.summary
    data = _read_batch_state(workdir)
    assert data["classes"][0]["status"] == "done"
    assert data["classes"][0]["final_rate"] == 85.0