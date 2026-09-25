"""init_coverage.handler() 单测。"""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jaut import config
from jaut.cli import StepError
from jaut.models import ClassCoverage, Decision, MethodCoverage, MethodKey, MethodStatus, Route, State, TestResult
from scripts.init_coverage import handler


def _make_args(tmp_path, **overrides) -> argparse.Namespace:
    defaults = dict(
        project_root=str(tmp_path),
        target_class="com.example.MyService",
        method=None,
        threshold=80.0,
        workdir=None,
        jacoco_version=config.DEFAULT_JACOCO_VERSION,
        coverage_exclude=None,
        final_check=False,
        override_threshold=False,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


# --------------------------------------------------------------------------- #
# 成功场景
# --------------------------------------------------------------------------- #
def test_handler_baseline_mode_success(tmp_path):
    """基线模式: 正常执行，返回 make_plan 或 finish 决策。"""
    args = _make_args(tmp_path)
    mock_state = MagicMock(spec=State)
    mock_state.pending_methods.return_value = []
    mock_state.test_class_simple = "MyServiceTest"
    mock_state.threshold = 80.0
    mock_state.find_method.return_value = None

    mock_test = TestResult(tests=5, failures=0, errors=0, skipped=0, report_found=True, parse_errors=0)
    mock_class_cov = MagicMock(spec=ClassCoverage)
    mock_class_cov.rate = 100.0

    # 创建一个方法覆盖率对象用于 parse_jacoco_xml 返回
    mock_method_cov = MagicMock(spec=MethodCoverage)
    mock_method_cov.key = MagicMock()
    mock_method_cov.key.name = "doSomething"
    mock_method_cov.key.desc = "()V"
    mock_method_cov.covered = 10
    mock_method_cov.missed = 0
    mock_method_cov.rate = 100.0
    mock_method_cov.is_abstract = False
    mock_method_cov.status = MethodStatus.DONE

    with patch("scripts.init_coverage.javasrc.resolve_target") as mock_resolve, \
         patch("scripts.init_coverage.maven.find_module_for_source", return_value="."), \
         patch("scripts.init_coverage.gitops.record_git_baseline", return_value={}), \
         patch("scripts.init_coverage.maven.clean_jacoco_dirs"), \
         patch("scripts.init_coverage.maven.clean_surefire_dirs", return_value=[]), \
         patch("scripts.init_coverage.maven.run_single_cov_with_fallback") as mock_cov, \
         patch("scripts.init_coverage.jacoco.report_paths") as mock_paths, \
         patch("scripts.init_coverage.jacoco.parse_jacoco_xml") as mock_parse, \
         patch("scripts.init_coverage.jacoco.class_coverage", return_value=mock_class_cov), \
         patch("scripts.init_coverage.surefire.parse_surefire_reports", return_value=mock_test), \
         patch("scripts.init_coverage.StateStore") as MockStore, \
         patch("scripts.init_coverage.setup_logger"), \
         patch("scripts.init_coverage.Path.is_file", return_value=True):
        mock_resolve.return_value = ("com.example.MyService", Path("src/main/java/MyService.java"))
        mock_cov.return_value = (MagicMock(ok=True), None)
        mock_paths.return_value = (Path("jacoco.xml"), Path("jacoco.csv"))
        mock_parse.return_value = {"com.example.MyService": [mock_method_cov]}
        MockStore.return_value.locked.return_value.__enter__ = MagicMock(return_value=None)
        MockStore.return_value.locked.return_value.__exit__ = MagicMock(return_value=False)
        MockStore.return_value.load.return_value = mock_state
        MockStore.return_value.coverage_path.return_value = Path("coverage.json")

        decision, ectx = handler(args)

    assert ectx.workdir.exists()
    assert ectx.state is mock_state or ectx.state is not None
    # 基线即达标 -> finish, 达标轮测试汇总落盘且决策携带渲染好的报告
    assert decision.route == Route.FINISH
    assert ectx.state.final_test_summary == {"tests": 5, "failures": 0,
                                             "errors": 0, "skipped": 0}
    assert decision.report is not None
    assert "最终状态: PASS" in decision.report


def test_handler_final_check_mode_uses_prev_threshold(tmp_path):
    """终验模式: 默认沿用 init 时的门槛而非 --threshold。"""
    args = _make_args(tmp_path, final_check=True, threshold=90.0)
    prev_state = MagicMock(spec=State)
    prev_state.threshold = 80.0
    prev_state.git_baseline = {}
    prev_state.final_check_fail_streak = 0
    prev_state.pending_methods.return_value = []
    prev_state.test_class_simple = "MyServiceTest"
    prev_state.find_method.return_value = None
    prev_state.coverage_history = []
    prev_state.test_history = []
    prev_state.global_iteration = 5
    prev_state.target_method = None
    prev_state.coverage_excludes = ["com.example.dto.*"]

    mock_class_cov = MagicMock(spec=ClassCoverage)
    mock_class_cov.rate = 85.0
    mock_class_cov.to_dict.return_value = {"rate": 85.0}

    # 创建一个方法覆盖率对象用于 parse_jacoco_xml 返回
    mock_method_cov = MagicMock(spec=MethodCoverage)
    mock_method_cov.key = MagicMock()
    mock_method_cov.key.name = "doSomething"
    mock_method_cov.key.desc = "()V"
    mock_method_cov.covered = 10
    mock_method_cov.missed = 0
    mock_method_cov.rate = 85.0
    mock_method_cov.is_abstract = False
    mock_method_cov.status = MethodStatus.DONE

    with patch("scripts.init_coverage.javasrc.resolve_target") as mock_resolve, \
         patch("scripts.init_coverage.maven.find_module_for_source", return_value="."), \
         patch("scripts.init_coverage.maven.clean_jacoco_dirs"), \
         patch("scripts.init_coverage.maven.clean_surefire_dirs", return_value=[]), \
         patch("scripts.init_coverage.maven.run_single_cov_with_fallback") as mock_cov, \
         patch("scripts.init_coverage.jacoco.report_paths") as mock_paths, \
         patch("scripts.init_coverage.jacoco.parse_jacoco_xml") as mock_parse, \
         patch("scripts.init_coverage.jacoco.class_coverage", return_value=mock_class_cov), \
         patch("scripts.init_coverage.surefire.parse_surefire_reports") as mock_test, \
         patch("scripts.init_coverage.StateStore") as MockStore, \
         patch("scripts.init_coverage.setup_logger"), \
         patch("scripts.init_coverage.Path.is_file", return_value=True):
        mock_resolve.return_value = ("com.example.MyService", Path("src/main/java/MyService.java"))
        mock_cov.return_value = (MagicMock(ok=True), None)
        mock_paths.return_value = (Path("jacoco.xml"), Path("jacoco.csv"))
        mock_parse.return_value = {"com.example.MyService": [mock_method_cov]}
        mock_test.return_value = MagicMock(tests=5, failures=0, errors=0, skipped=0, is_green=MagicMock(return_value=True))
        MockStore.return_value.locked.return_value.__enter__ = MagicMock(return_value=None)
        MockStore.return_value.locked.return_value.__exit__ = MagicMock(return_value=False)
        MockStore.return_value.load.return_value = prev_state
        MockStore.return_value.coverage_path.return_value = Path("coverage.json")

        decision, ectx = handler(args)

    # 终验模式应沿用 prev_state.threshold (80.0)，而非 args.threshold (90.0)
    # 排除模式同理: 未显式传 --coverage-exclude 时沿用 init 轮, 且解析时带上
    assert ectx.state.coverage_excludes == ["com.example.dto.*"]
    mock_parse.assert_called_once_with(Path("jacoco.xml"),
                                       excludes=["com.example.dto.*"])


def test_handler_persists_coverage_excludes(tmp_path):
    """基线模式: --coverage-exclude 去空白后写入 state, 解析与类覆盖率均带 excludes。"""
    args = _make_args(tmp_path, coverage_exclude=["com.example.dto.*", " *$Builder "])
    mock_state = MagicMock(spec=State)
    mock_state.pending_methods.return_value = []
    mock_state.test_class_simple = "MyServiceTest"
    mock_state.threshold = 80.0
    mock_state.find_method.return_value = None

    mock_test = TestResult(tests=2, failures=0, errors=0, skipped=0, report_found=True, parse_errors=0)
    mock_class_cov = MagicMock(spec=ClassCoverage)
    mock_class_cov.rate = 40.0

    mock_method_cov = MagicMock(spec=MethodCoverage)
    mock_method_cov.key = MagicMock()
    mock_method_cov.key.name = "doSomething"
    mock_method_cov.key.desc = "()V"
    mock_method_cov.covered = 4
    mock_method_cov.missed = 6
    mock_method_cov.rate = 40.0
    mock_method_cov.is_abstract = False
    mock_method_cov.status = MethodStatus.PENDING

    with patch("scripts.init_coverage.javasrc.resolve_target") as mock_resolve, \
         patch("scripts.init_coverage.maven.find_module_for_source", return_value="."), \
         patch("scripts.init_coverage.gitops.record_git_baseline", return_value={}), \
         patch("scripts.init_coverage.maven.clean_jacoco_dirs"), \
         patch("scripts.init_coverage.maven.clean_surefire_dirs", return_value=[]), \
         patch("scripts.init_coverage.maven.run_single_cov_with_fallback") as mock_cov, \
         patch("scripts.init_coverage.jacoco.report_paths") as mock_paths, \
         patch("scripts.init_coverage.jacoco.parse_jacoco_xml") as mock_parse, \
         patch("scripts.init_coverage.jacoco.class_coverage", return_value=mock_class_cov) as mock_class, \
         patch("scripts.init_coverage.surefire.parse_surefire_reports", return_value=mock_test), \
         patch("scripts.init_coverage.StateStore") as MockStore, \
         patch("scripts.init_coverage.setup_logger"), \
         patch("scripts.init_coverage.Path.is_file", return_value=True):
        mock_resolve.return_value = ("com.example.MyService", Path("src/main/java/MyService.java"))
        mock_cov.return_value = (MagicMock(ok=True), None)
        mock_paths.return_value = (Path("jacoco.xml"), Path("jacoco.csv"))
        mock_parse.return_value = {"com.example.MyService": [mock_method_cov]}
        MockStore.return_value.locked.return_value.__enter__ = MagicMock(return_value=None)
        MockStore.return_value.locked.return_value.__exit__ = MagicMock(return_value=False)
        MockStore.return_value.load.return_value = mock_state
        MockStore.return_value.coverage_path.return_value = Path("coverage.json")

        decision, ectx = handler(args)

    expected = ["com.example.dto.*", "*$Builder"]      # 去除首尾空白
    assert ectx.state.coverage_excludes == expected
    mock_parse.assert_called_once_with(Path("jacoco.xml"), excludes=expected)
    assert mock_class.call_args.kwargs["excludes"] == expected
    assert decision.route == Route.MAKE_PLAN           # 未达标 -> 继续补测


# --------------------------------------------------------------------------- #
# 错误场景
# --------------------------------------------------------------------------- #
def test_handler_raises_when_project_root_missing(tmp_path):
    """项目根目录不存在时应抛出 StepError。"""
    args = _make_args(tmp_path)
    args.project_root = "/nonexistent/path"

    with patch("scripts.init_coverage.setup_logger"):
        with pytest.raises(StepError) as exc_info:
            handler(args)

    assert exc_info.value.decision.exit_code == config.EXIT_ERROR
    assert "不存在" in exc_info.value.decision.summary


def test_handler_raises_when_class_not_resolved(tmp_path):
    """无法解析目标类时应抛出 StepError。"""
    args = _make_args(tmp_path)

    with patch("scripts.init_coverage.javasrc.resolve_target", return_value=(None, None)), \
         patch("scripts.init_coverage.setup_logger"):
        with pytest.raises(StepError) as exc_info:
            handler(args)

    assert exc_info.value.decision.exit_code == config.EXIT_ERROR
    assert "无法解析目标类" in exc_info.value.decision.summary


def test_handler_raises_when_target_class_excluded(tmp_path):
    """排除模式命中目标类本身时报错(exit 3), 且不进入 mvn。"""
    args = _make_args(tmp_path, coverage_exclude=["com.example.*"])

    with patch("scripts.init_coverage.javasrc.resolve_target",
               return_value=("com.example.MyService", Path("src"))), \
         patch("scripts.init_coverage.maven.run_single_cov_with_fallback") as mock_cov, \
         patch("scripts.init_coverage.StateStore") as MockStore, \
         patch("scripts.init_coverage.setup_logger"):
        MockStore.return_value.locked.return_value.__enter__ = MagicMock(return_value=None)
        MockStore.return_value.locked.return_value.__exit__ = MagicMock(return_value=False)
        MockStore.return_value.load.return_value = None

        with pytest.raises(StepError) as exc_info:
            handler(args)

    assert exc_info.value.decision.exit_code == config.EXIT_STATE
    assert "目标类" in exc_info.value.decision.summary
    mock_cov.assert_not_called()


def test_handler_raises_when_coverage_fails(tmp_path):
    """覆盖率采集失败时应抛出 StepError。"""
    args = _make_args(tmp_path)

    with patch("scripts.init_coverage.javasrc.resolve_target", return_value=("com.example.MyService", Path("src"))), \
         patch("scripts.init_coverage.maven.find_module_for_source", return_value="."), \
         patch("scripts.init_coverage.gitops.record_git_baseline", return_value={}), \
         patch("scripts.init_coverage.maven.clean_jacoco_dirs"), \
         patch("scripts.init_coverage.maven.clean_surefire_dirs", return_value=[]), \
         patch("scripts.init_coverage.maven.run_single_cov_with_fallback") as mock_cov, \
         patch("scripts.init_coverage.StateStore") as MockStore, \
         patch("scripts.init_coverage.setup_logger"), \
         patch("scripts.init_coverage._ensure_test_file_exists", return_value=(False, Path("/tmp/stub"))):
        mock_cov.return_value = (MagicMock(ok=False), None)
        MockStore.return_value.locked.return_value.__enter__ = MagicMock(return_value=None)
        MockStore.return_value.locked.return_value.__exit__ = MagicMock(return_value=False)
        MockStore.return_value.load.return_value = None

        with pytest.raises(StepError) as exc_info:
            handler(args)

    assert exc_info.value.decision.exit_code == config.EXIT_ERROR
    assert "失败" in exc_info.value.decision.summary


def test_handler_rollback_stub_on_coverage_failure(tmp_path):
    """覆盖率采集失败时应删除桩测试文件。"""
    args = _make_args(tmp_path)
    # 创建桩测试文件
    stub_file = tmp_path / "src" / "test" / "java" / "com" / "example" / "MyServiceTest.java"
    stub_file.parent.mkdir(parents=True)
    stub_file.write_text("class MyServiceTest {}")

    with patch("scripts.init_coverage.javasrc.resolve_target", return_value=("com.example.MyService", Path("src"))), \
         patch("scripts.init_coverage.maven.find_module_for_source", return_value="."), \
         patch("scripts.init_coverage.gitops.record_git_baseline", return_value={}), \
         patch("scripts.init_coverage.maven.clean_jacoco_dirs"), \
         patch("scripts.init_coverage.maven.clean_surefire_dirs", return_value=[]), \
         patch("scripts.init_coverage.maven.run_single_cov_with_fallback") as mock_cov, \
         patch("scripts.init_coverage.StateStore") as MockStore, \
         patch("scripts.init_coverage.setup_logger"), \
         patch("scripts.init_coverage._ensure_test_file_exists", return_value=(True, stub_file)):
        mock_cov.return_value = (MagicMock(ok=False), None)
        MockStore.return_value.locked.return_value.__enter__ = MagicMock(return_value=None)
        MockStore.return_value.locked.return_value.__exit__ = MagicMock(return_value=False)
        MockStore.return_value.load.return_value = None

        with pytest.raises(StepError):
            handler(args)

    # 桩文件应被删除
    assert not stub_file.is_file()


def test_handler_cleans_stub_on_fallback_success(tmp_path):
    """快速路径失败但降级成功时仍应清理桩测试文件。"""
    args = _make_args(tmp_path)
    stub_file = tmp_path / "src" / "test" / "java" / "com" / "example" / "MyServiceTest.java"
    stub_file.parent.mkdir(parents=True)
    stub_file.write_text("class MyServiceTest {}")

    mock_class_cov = MagicMock(spec=ClassCoverage)
    mock_class_cov.rate = 100.0
    mock_class_cov.to_dict.return_value = {"rate": 100.0}
    mock_test = TestResult(tests=1, failures=0, errors=0, skipped=0,
                           report_found=True, parse_errors=0)
    mock_method_cov = MagicMock(spec=MethodCoverage)
    mock_method_cov.key = MagicMock()
    mock_method_cov.key.name = "doSomething"
    mock_method_cov.key.desc = "()V"
    mock_method_cov.covered = 10
    mock_method_cov.missed = 0
    mock_method_cov.rate = 100.0
    mock_method_cov.is_abstract = False
    mock_method_cov.status = MethodStatus.DONE

    with patch("scripts.init_coverage.javasrc.resolve_target",
               return_value=("com.example.MyService", Path("src/main/java/MyService.java"))), \
         patch("scripts.init_coverage.maven.find_module_for_source", return_value="."), \
         patch("scripts.init_coverage.gitops.record_git_baseline", return_value={}), \
         patch("scripts.init_coverage.maven.clean_jacoco_dirs"), \
         patch("scripts.init_coverage.maven.clean_surefire_dirs", return_value=[]), \
         patch("scripts.init_coverage.maven.run_single_cov_with_fallback") as mock_cov, \
         patch("scripts.init_coverage.jacoco.report_paths",
               return_value=(Path("jacoco.xml"), Path("jacoco.csv"))), \
         patch("scripts.init_coverage.jacoco.parse_jacoco_xml",
               return_value={"com.example.MyService": [mock_method_cov]}), \
         patch("scripts.init_coverage.jacoco.class_coverage", return_value=mock_class_cov), \
         patch("scripts.init_coverage.surefire.parse_surefire_reports", return_value=mock_test), \
         patch("scripts.init_coverage.StateStore") as MockStore, \
         patch("scripts.init_coverage.setup_logger"), \
         patch("scripts.init_coverage.Path.is_file", return_value=True), \
         patch("scripts.init_coverage._ensure_test_file_exists", return_value=(True, stub_file)):
        # 快速路径失败, 降级成功
        mock_cov.return_value = (MagicMock(ok=True), "/tmp/fast.log")
        mock_state = MagicMock(spec=State)
        mock_state.pending_methods.return_value = []
        mock_state.test_class_simple = "MyServiceTest"
        mock_state.threshold = 80.0
        mock_state.find_method.return_value = None
        MockStore.return_value.locked.return_value.__enter__ = MagicMock(return_value=None)
        MockStore.return_value.locked.return_value.__exit__ = MagicMock(return_value=False)
        MockStore.return_value.load.return_value = mock_state
        MockStore.return_value.coverage_path.return_value = Path("coverage.json")

        decision, _ = handler(args)

    # 降级成功时桩文件也应被清理
    assert not stub_file.is_file()
    assert decision.route == Route.FINISH


# --------------------------------------------------------------------------- #
# P2-6: 源文件前置校验
# --------------------------------------------------------------------------- #
def test_handler_raises_when_source_not_found(tmp_path):
    """FQCN 可解析但源文件不存在时应抛出 StepError (P2-6 fail-fast)。"""
    args = _make_args(tmp_path, target_class="com.nonexist.Foo")

    with patch("scripts.init_coverage.javasrc.resolve_target",
               return_value=("com.nonexist.Foo", None)), \
         patch("scripts.init_coverage.javasrc.diagnose_missing_source",
               return_value="确认 FQCN 'com.nonexist.Foo' 拼写正确且文件在 src/main/java 下"), \
         patch("scripts.init_coverage.setup_logger"):
        with pytest.raises(StepError) as exc_info:
            handler(args)

    assert exc_info.value.decision.exit_code == config.EXIT_ERROR
    assert "无法定位" in exc_info.value.decision.summary
    assert "com.nonexist.Foo" in exc_info.value.decision.summary


# --------------------------------------------------------------------------- #
# P0-3: --method 模式假通过修复
# --------------------------------------------------------------------------- #
def _make_method_cov(name: str, desc: str = "()V", covered: int = 10,
                     missed: int = 0, rate: float = 100.0) -> MagicMock:
    """构建用于 parse_jacoco_xml 返回的 mock MethodCoverage。"""
    m = MagicMock(spec=MethodCoverage)
    m.key = MagicMock()
    m.key.name = name
    m.key.desc = desc
    m.covered = covered
    m.missed = missed
    m.rate = rate
    m.is_abstract = False
    m.status = MethodStatus.DONE
    return m


def _handler_patch_context(tmp_path, parsed_methods, class_rate: float = 100.0,
                           test_result: TestResult | None = None):
    """构建 handler 所需的全量 patch 上下文管理器，返回 (mock_cov, MockStore) 元组。"""
    mock_class_cov = MagicMock(spec=ClassCoverage)
    mock_class_cov.rate = class_rate
    mock_class_cov.to_dict.return_value = {"rate": class_rate}

    mock_test = test_result if test_result is not None else TestResult(
        tests=1, failures=0, errors=0, skipped=0, report_found=True, parse_errors=0)

    patches = {
        "scripts.init_coverage.javasrc.resolve_target": (
            "com.example.MyService", Path("src/main/java/MyService.java")),
        "scripts.init_coverage.maven.find_module_for_source": ".",
        "scripts.init_coverage.gitops.record_git_baseline": {},
        "scripts.init_coverage.maven.clean_jacoco_dirs": None,
        "scripts.init_coverage.maven.clean_surefire_dirs": [],
        "scripts.init_coverage.jacoco.report_paths": (Path("jacoco.xml"), Path("jacoco.csv")),
        "scripts.init_coverage.jacoco.parse_jacoco_xml":
            {"com.example.MyService": parsed_methods},
        "scripts.init_coverage.jacoco.class_coverage": mock_class_cov,
        "scripts.init_coverage.surefire.parse_surefire_reports": mock_test,
        "scripts.init_coverage.setup_logger": MagicMock(),
        "scripts.init_coverage.Path.is_file": True,
    }

    # 使用逐一 patch 以保证兼容性
    from contextlib import ExitStack
    stack = ExitStack()
    for target, ret in patches.items():
        stack.enter_context(patch(target, return_value=ret))

    mock_cov = stack.enter_context(
        patch("scripts.init_coverage.maven.run_single_cov_with_fallback",
              return_value=(MagicMock(ok=True), None)))
    MockStore = stack.enter_context(
        patch("scripts.init_coverage.StateStore"))
    MockStore.return_value.locked.return_value.__enter__ = MagicMock(return_value=None)
    MockStore.return_value.locked.return_value.__exit__ = MagicMock(return_value=False)
    MockStore.return_value.load.return_value = None
    MockStore.return_value.coverage_path.return_value = Path("coverage.json")
    return stack, mock_cov, MockStore


def test_handler_method_mode_unmatched_raises(tmp_path):
    """P0-3: --method 拼错时应抛出 StepError 而非静默假 PASS。

    根因: method_arg 不匹配任何方法 -> 全 SKIPPED -> pending=0 -> scope_met=True
         -> init_is_met(method_mode=True) 直接返回 True -> 零测试假 finish。
    修复: 基线模式下校验 method_arg 至少命中一个 parsed 条目。
    """
    method_cov_do = _make_method_cov("doSomething")
    method_cov_run = _make_method_cov("runTask")
    args = _make_args(tmp_path, method="doSomethingTypo")

    stack, _, _ = _handler_patch_context(tmp_path, [method_cov_do, method_cov_run])
    with stack:
        with pytest.raises(StepError) as exc_info:
            handler(args)

    err = exc_info.value.decision
    assert err.exit_code == config.EXIT_STATE
    assert "doSomethingTypo" in err.summary
    assert "未匹配" in err.summary
    # question 应列出候选方法名
    assert "doSomething" in err.question
    assert "runTask" in err.question


def test_handler_method_mode_matched_proceeds(tmp_path):
    """P0-3 反例: --method 命中真实方法时不应抛错，正常走后续逻辑。"""
    method_cov = _make_method_cov("doSomething")
    args = _make_args(tmp_path, method="doSomething")

    mock_state = MagicMock(spec=State)
    mock_state.pending_methods.return_value = []
    mock_state.test_class_simple = "MyServiceTest"
    mock_state.threshold = 80.0
    mock_state.find_method.return_value = None

    stack, _, MockStore = _handler_patch_context(tmp_path, [method_cov])
    MockStore.return_value.load.return_value = mock_state
    with stack:
        # method 命中时不应抛错, 应正常返回决策
        decision, ectx = handler(args)

    assert decision is not None
    assert ectx.state is not None


def test_handler_method_mode_skipped_target_finishes_unmet(tmp_path):
    """method 模式: 目标方法被跳过(未覆盖) -> 未达标收尾, 不得判 PASS(SKILL §7)。

    根因: scope_met 只按 pending 队列判空, 被跳过的目标方法不是 pending ->
         init_is_met(method_mode=True) 只看 scope_met + tests_green -> 假 PASS。
    修复: method 模式下 scope_met 还要求目标方法自身 done。
    """
    args = _make_args(tmp_path, method="doStuff", final_check=True)
    skipped = MethodCoverage(
        key=MethodKey("doStuff", "()V"), covered=5, missed=5,
        status=MethodStatus.SKIPPED, initial_rate=50.0,
        skip_reason="单方法迭代已达上限 8 轮仍未达标(当前 50.0%)")
    prev_state = State(
        project_root=str(tmp_path), target_class="com.example.MyService",
        target_method="doStuff", module=".", threshold=80.0,
        test_class_simple="MyServiceTest", methods=[skipped],
        class_coverage=ClassCoverage.of(90, 10), global_iteration=9)
    parsed_target = MethodCoverage(key=MethodKey("doStuff", "()V"), covered=5, missed=5)

    stack, _, MockStore = _handler_patch_context(tmp_path, [parsed_target])
    MockStore.return_value.load.return_value = prev_state
    with stack:
        decision, ectx = handler(args)

    assert decision.route == Route.FINISH
    assert "最终状态: 未达标" in decision.report
    assert "目标方法 doStuff 已跳过" in decision.report
    assert ectx.state.final_checked is False


def test_handler_method_mode_done_target_passes_despite_class_rate(tmp_path):
    """method 模式反例: 目标方法达标即 PASS, 类级覆盖率不参与判定(SKILL §7)。"""
    args = _make_args(tmp_path, method="doStuff", final_check=True)
    done = MethodCoverage(
        key=MethodKey("doStuff", "()V"), covered=9, missed=1,
        status=MethodStatus.DONE, initial_rate=50.0)
    prev_state = State(
        project_root=str(tmp_path), target_class="com.example.MyService",
        target_method="doStuff", module=".", threshold=80.0,
        test_class_simple="MyServiceTest", methods=[done],
        class_coverage=ClassCoverage.of(12, 88), global_iteration=9)
    parsed_target = MethodCoverage(key=MethodKey("doStuff", "()V"), covered=9, missed=1)

    stack, _, MockStore = _handler_patch_context(tmp_path, [parsed_target],
                                                 class_rate=12.0)
    MockStore.return_value.load.return_value = prev_state
    with stack:
        decision, ectx = handler(args)

    assert decision.route == Route.FINISH
    assert "最终状态: PASS" in decision.report
    assert ectx.state.final_checked is True


def test_handler_method_mode_overload_skipped_target_finishes_unmet(tmp_path):
    """method 模式: --method 按方法名限定范围, 同名重载全部在范围内。

    回归: 目标方法判定用 next() 只取首个同名条目 -> 结果依赖 methods 顺序;
         "一个重载 done、另一个重载被跳过" 会被误判 PASS。必须每个同名重载都
         done 才算范围满足。
    """
    args = _make_args(tmp_path, method="doStuff", final_check=True)
    done = MethodCoverage(
        key=MethodKey("doStuff", "()V"), covered=9, missed=1,
        status=MethodStatus.DONE, initial_rate=50.0)
    skipped = MethodCoverage(
        key=MethodKey("doStuff", "(I)V"), covered=0, missed=10,
        status=MethodStatus.SKIPPED, initial_rate=0.0,
        skip_reason="单方法迭代已达上限 8 轮仍未达标(当前 0.0%)")
    prev_state = State(
        project_root=str(tmp_path), target_class="com.example.MyService",
        target_method="doStuff", module=".", threshold=80.0,
        test_class_simple="MyServiceTest", methods=[done, skipped],
        class_coverage=ClassCoverage.of(90, 10), global_iteration=9)
    parsed = [MethodCoverage(key=MethodKey("doStuff", "()V"), covered=9, missed=1),
              MethodCoverage(key=MethodKey("doStuff", "(I)V"), covered=0, missed=10)]

    stack, _, MockStore = _handler_patch_context(tmp_path, parsed, class_rate=10.0)
    MockStore.return_value.load.return_value = prev_state
    with stack:
        decision, ectx = handler(args)

    assert decision.route == Route.FINISH
    assert "最终状态: 未达标" in decision.report
    assert ectx.state.final_checked is False


def test_build_method_entries_clears_skip_reason_on_promotion():
    """终验把已跳过的达标方法晋升 done 时清空 skip_reason(done ⇒ 无跳过原因)。"""
    from scripts.init_coverage import _build_method_entries

    prev = MethodCoverage(
        key=MethodKey("doStuff", "()V"), covered=5, missed=5,
        status=MethodStatus.SKIPPED, skip_reason="类级预算耗尽(30/30 轮), 未达标方法不再迭代")
    prev_state = State(project_root="/r", target_class="com.example.MyService",
                       methods=[prev])
    parsed = [MethodCoverage(key=MethodKey("doStuff", "()V"), covered=9, missed=1)]

    entries = _build_method_entries(parsed, None, prev_state, 80.0, True)

    assert entries[0].status == MethodStatus.DONE
    assert entries[0].skip_reason is None


def test_unmet_note_baseline_not_green_not_called_final():
    """基线轮(非终验)测试未通过: 措辞为"测试未通过", 不得误称"终验测试未通过"。"""
    from scripts.init_coverage import _unmet_note

    test = TestResult(tests=1, failures=1, errors=0, report_found=True)
    note = _unmet_note(False, False, test, 2, 50.0, 80.0, None, final_check=False)
    assert note.startswith("测试未通过")
    assert "终验" not in note

    note_final = _unmet_note(False, False, test, 2, 50.0, 80.0, None,
                             final_check=True)
    assert note_final.startswith("终验测试未通过")


def test_unmet_note_env_uncertain_says_unverified():
    """环境不可信: 说明行按"未能复核"措辞, 不称测试失败。"""
    from scripts.init_coverage import _unmet_note

    test = TestResult(tests=0, failures=0, errors=0, report_found=False)
    note = _unmet_note(False, False, test, 0, 70.0, 80.0, None,
                       final_check=True, env_uncertain=True)
    assert "测试结果不可信" in note
    assert "未能确认达标" in note
    assert "未通过" not in note


def test_handler_source_not_found_includes_diagnosis(tmp_path):
    """源文件未找到时 question 应包含诊断信息。"""
    args = _make_args(tmp_path, target_class="com.y.Foo")

    with patch("scripts.init_coverage.javasrc.resolve_target",
               return_value=("com.y.Foo", None)), \
         patch("scripts.init_coverage.javasrc.diagnose_missing_source",
               return_value="找到同名文件: /project/src/main/java/com/x/Foo.java(检查包名是否正确)") as mock_diag, \
         patch("scripts.init_coverage.setup_logger"):
        with pytest.raises(StepError) as exc_info:
            handler(args)

    mock_diag.assert_called_once()
    question = exc_info.value.decision.question
    assert "诊断" in question
    assert "同名文件" in question
    assert "包名是否正确" in question


# --------------------------------------------------------------------------- #
# _ensure_test_file_exists 单测
# --------------------------------------------------------------------------- #
def test_ensure_test_file_exists_creates_stub_when_missing(tmp_path):
    """测试文件不存在时应创建桩测试类。"""
    from scripts.init_coverage import _ensure_test_file_exists
    # 创建项目结构
    (tmp_path / "src" / "main" / "java" / "com" / "example").mkdir(parents=True)
    (tmp_path / "pom.xml").write_text("<project/>")
    mock_logger = MagicMock()
    created, stub_file = _ensure_test_file_exists(tmp_path, ".", "com.example.Foo", mock_logger)
    assert created is True
    assert stub_file.is_file()
    assert "public class FooTest" in stub_file.read_text()


def test_ensure_test_file_exists_returns_false_when_exists(tmp_path):
    """测试文件已存在时不应创建。"""
    from scripts.init_coverage import _ensure_test_file_exists
    test_file = tmp_path / "src" / "test" / "java" / "com" / "example" / "FooTest.java"
    test_file.parent.mkdir(parents=True)
    test_file.write_text("class FooTest {}")
    created, stub_file = _ensure_test_file_exists(tmp_path, ".", "com.example.Foo", MagicMock())
    assert created is False
    assert stub_file == test_file


def test_ensure_test_file_exists_with_module(tmp_path):
    """多模块项目中应正确创建桩测试类。"""
    from scripts.init_coverage import _ensure_test_file_exists
    module_dir = tmp_path / "module-a"
    (module_dir / "src" / "main" / "java" / "com" / "example").mkdir(parents=True)
    (module_dir / "pom.xml").write_text("<project/>")
    mock_logger = MagicMock()
    created, stub_file = _ensure_test_file_exists(tmp_path, "module-a", "com.example.Foo", mock_logger)
    assert created is True
    assert stub_file.is_file()
    assert "public class FooTest" in stub_file.read_text()


def test_ensure_test_file_exists_bare_class_name(tmp_path):
    """无包名的裸类名应生成正确的桩测试类(无 package 声明)。"""
    from scripts.init_coverage import _ensure_test_file_exists
    (tmp_path / "src" / "main" / "java").mkdir(parents=True)
    (tmp_path / "pom.xml").write_text("<project/>")
    mock_logger = MagicMock()
    created, stub_file = _ensure_test_file_exists(tmp_path, ".", "BareClass", mock_logger)
    assert created is True
    content = stub_file.read_text()
    assert "public class BareClassTest" in content
    assert "package" not in content


def test_handler_unmet_final_check_persists_test_summary(tmp_path):
    """未达标终验收尾: 测试汇总同样落盘, 报告如实显示本轮测试数字而非"未记录"。

    根因: final_test_summary 只在 met=True 时写盘, met=False 路径的收尾报告
         "测试:"段固定输出 未记录, 而同报告的 note 却引用刚解析的 surefire 数字。
    """
    args = _make_args(tmp_path, final_check=True)
    done = MethodCoverage(key=MethodKey("doStuff", "()V"), covered=9, missed=1,
                          status=MethodStatus.DONE, initial_rate=50.0)
    prev_state = State(
        project_root=str(tmp_path), target_class="com.example.MyService",
        module=".", threshold=80.0, test_class_simple="MyServiceTest",
        methods=[done], class_coverage=ClassCoverage.of(70, 30), global_iteration=9)
    parsed = [MethodCoverage(key=MethodKey("doStuff", "()V"), covered=9, missed=1)]

    stack, _, MockStore = _handler_patch_context(tmp_path, parsed, class_rate=70.0)
    MockStore.return_value.load.return_value = prev_state
    with stack:
        decision, ectx = handler(args)

    assert decision.route == Route.FINISH
    assert "最终状态: 未达标" in decision.report
    # 终验轮(无论达标与否)测试汇总落盘, 收尾报告显示真实数字
    assert ectx.state.final_test_summary == {"tests": 1, "failures": 0,
                                             "errors": 0, "skipped": 0}
    assert "Tests run: 1" in decision.report
    assert "未记录" not in decision.report


def test_handler_final_check_env_uncertain_finishes_unverified(tmp_path):
    """终验 surefire 报告缺失(无失败用例)达 streak 上限 -> 未复核收尾。

    报告"最终状态"为未复核(测试结果不可信), 说明行与决策 summary 均按"未能复核"
    措辞, 不混同普通测试失败; final_checked 保持 False(环境修复后可重跑复核)。
    """
    args = _make_args(tmp_path, final_check=True)
    done = MethodCoverage(key=MethodKey("doStuff", "()V"), covered=9, missed=1,
                          status=MethodStatus.DONE, initial_rate=50.0)
    prev_state = State(
        project_root=str(tmp_path), target_class="com.example.MyService",
        module=".", threshold=80.0, test_class_simple="MyServiceTest",
        methods=[done], class_coverage=ClassCoverage.of(70, 30), global_iteration=9,
        final_check_fail_streak=config.FINAL_CHECK_FAIL_STREAK_LIMIT - 1)
    parsed = [MethodCoverage(key=MethodKey("doStuff", "()V"), covered=9, missed=1)]
    uncertain = TestResult(tests=0, failures=0, errors=0, skipped=0,
                           report_found=False, parse_errors=0)

    stack, _, MockStore = _handler_patch_context(
        tmp_path, parsed, class_rate=70.0, test_result=uncertain)
    MockStore.return_value.load.return_value = prev_state
    with stack:
        decision, ectx = handler(args)

    assert decision.route == Route.FINISH
    assert "最终状态: 未复核(测试结果不可信)" in decision.report
    assert "测试结果不可信" in decision.report
    assert "未能复核达标" in decision.summary
    assert "未达标" not in decision.summary
    assert ectx.state.final_checked is False
