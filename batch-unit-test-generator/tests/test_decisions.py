"""decisions 决策核心单测: 升级判定原语 + verify/init/plan 三处决策分叉。"""

from __future__ import annotations

from jaut import config, decisions
from jaut.models import (
    ClassCoverage,
    FailedCase,
    MethodCoverage,
    MethodKey,
    MethodStatus,
    Route,
    State,
    TestOutcome,
    TestResult,
)


def _method(covered=0, missed=10, rates=None, results=None, status=MethodStatus.PENDING):
    return MethodCoverage(
        key=MethodKey(name="foo", desc="(int)"),
        covered=covered, missed=missed, status=status,
        round_rates=list(rates or []),
        round_test_results=list(results or []),
    )


def _green(n=1):
    return TestResult(tests=n, failures=0, errors=0, report_found=True)


def _verify_ctx(**over):
    base = dict(
        method=_method(covered=9, missed=1),
        before_rate=10.0,
        class_cov=ClassCoverage.of(90, 10),
        test=_green(),
        threshold=80.0,
        tests_green=True,
        uncleaned=False,
        iteration=1,
        global_iteration=1,
        mvn_log="/w/mvn.log",
        surefire_report_dir="/w/surefire",
        test_simple="FooTest",
        test_class_file="/w/FooTest.java",
    )
    base.update(over)
    return decisions.VerifyContext(**base)


# --------------------------------------------------------------------------- #
# 升级判定原语
# --------------------------------------------------------------------------- #
def test_no_improvement_needs_three_non_increasing():
    assert decisions.no_improvement([10.0, 20.0]) is False       # 不足 3 轮
    assert decisions.no_improvement([30.0, 20.0, 20.0]) is True   # 非递增
    assert decisions.no_improvement([10.0, 20.0, 30.0]) is False  # 有提升


def test_test_failure_streak():
    green = TestOutcome(0, 0)
    red = TestOutcome(1, 0)
    assert decisions.test_failure_streak([red, red]) is False
    assert decisions.test_failure_streak([green, red, red, red]) is True
    assert decisions.test_failure_streak([red, red, green, red]) is False


# --------------------------------------------------------------------------- #
# decide_after_verify
# --------------------------------------------------------------------------- #
def test_verify_met_marks_done_and_routes_make_plan():
    d = decisions.decide_after_verify(_verify_ctx())
    assert d.route == Route.MAKE_PLAN
    assert d.exit_code == config.EXIT_OK
    assert d.mark_done is True


def test_verify_coverage_below_threshold_routes_build_prompt():
    ctx = _verify_ctx(method=_method(covered=5, missed=5))  # 50% < 80%
    d = decisions.decide_after_verify(ctx)
    assert d.route == Route.BUILD_PROMPT
    assert d.exit_code == config.EXIT_CONTINUE
    assert d.mark_done is False


def test_verify_method_budget_exhausted_auto_skips():
    """单方法预算耗尽不再 ask_user, 改为自动跳过当前方法。"""
    ctx = _verify_ctx(method=_method(covered=5, missed=5),
                      iteration=config.METHOD_ROUND_BUDGET)
    d = decisions.decide_after_verify(ctx)
    assert d.route == Route.MAKE_PLAN
    assert d.exit_code == config.EXIT_CONTINUE
    assert d.auto_skip_method is True
    assert d.skip_all_pending is False
    assert str(config.METHOD_ROUND_BUDGET) in d.auto_skip_reason
    assert d.reset_trajectory is True


def test_verify_auto_skip_summary_includes_test_status():
    """跳过消息应反映测试状态, 而非仅覆盖率。"""
    test = TestResult(tests=1, failures=2, errors=1, report_found=True)
    ctx = _verify_ctx(method=_method(covered=5, missed=5),
                      test=test, tests_green=False,
                      iteration=config.METHOD_ROUND_BUDGET)
    d = decisions.decide_after_verify(ctx)
    assert "测试失败" in d.summary
    assert "Failures 2" in d.summary
    assert "Errors 1" in d.summary

    # 测试全绿时, 跳过消息应体现测试全绿
    ctx_green = _verify_ctx(method=_method(covered=5, missed=5),
                            iteration=config.METHOD_ROUND_BUDGET)
    d_green = decisions.decide_after_verify(ctx_green)
    assert "测试全绿" in d_green.summary


def test_verify_global_budget_exhausted_skips_all_pending():
    """全局预算耗尽 -> 跳过全部未达标方法(不再 ask_user)。"""
    ctx = _verify_ctx(method=_method(covered=5, missed=5),
                      iteration=2, global_iteration=config.GLOBAL_ROUND_BUDGET)
    d = decisions.decide_after_verify(ctx)
    assert d.route == Route.MAKE_PLAN
    assert d.auto_skip_method is True
    assert d.skip_all_pending is True
    assert "全局" in d.summary
    assert "全局" in d.auto_skip_reason


def test_verify_method_budget_bonus_extends_window():
    """追加的单方法预算窗口计入有效预算(窗口内不跳过)。"""
    ctx = _verify_ctx(method=_method(covered=5, missed=5),
                      iteration=config.METHOD_ROUND_BUDGET,
                      method_round_bonus=3)
    d = decisions.decide_after_verify(ctx)
    assert d.route == Route.BUILD_PROMPT          # 8 < 8+3, 继续迭代
    assert d.auto_skip_method is False
    # 窗口耗尽后自动跳过, 且原因体现有效上限
    ctx2 = _verify_ctx(method=_method(covered=5, missed=5),
                       iteration=config.METHOD_ROUND_BUDGET + 3,
                       method_round_bonus=3)
    d2 = decisions.decide_after_verify(ctx2)
    assert d2.route == Route.MAKE_PLAN
    assert d2.auto_skip_method is True
    assert str(config.METHOD_ROUND_BUDGET + 3) in d2.auto_skip_reason


def test_verify_global_budget_bonus_extends_window():
    """全局预算窗口同理。"""
    ctx = _verify_ctx(method=_method(covered=5, missed=5),
                      iteration=2, global_iteration=config.GLOBAL_ROUND_BUDGET,
                      global_round_bonus=5)
    d = decisions.decide_after_verify(ctx)
    assert d.route == Route.BUILD_PROMPT          # 30 < 30+5, 继续迭代
    assert d.auto_skip_method is False


def test_verify_budget_decisions_never_ask_user():
    """预算/轨迹类耗尽一律自动跳过: 不产生 ask_user, 也不携带 resume。"""
    cases = [
        # 单方法预算耗尽
        _verify_ctx(method=_method(covered=5, missed=5),
                    iteration=config.METHOD_ROUND_BUDGET),
        # 全局预算耗尽
        _verify_ctx(method=_method(covered=5, missed=5),
                    iteration=2, global_iteration=config.GLOBAL_ROUND_BUDGET),
        # 连续测试失败
        _verify_ctx(method=_method(covered=5, missed=5,
                                   results=[TestOutcome(1, 0)] * config.TEST_FAIL_STREAK_ROUNDS),
                    test=TestResult(tests=1, failures=1, errors=0, report_found=True),
                    tests_green=False, iteration=3),
        # 连续无提升
        _verify_ctx(method=_method(covered=5, missed=5,
                                   rates=[30.0, 30.0, 30.0]),
                    iteration=3),
    ]
    for ctx in cases:
        d = decisions.decide_after_verify(ctx)
        assert d.route == Route.MAKE_PLAN
        assert d.status == "success"
        assert d.auto_skip_method is True
        assert d.auto_skip_reason
        assert d.resume == []
        assert d.question is None

    # 非跳过决策同样不携带 resume
    assert decisions.decide_after_verify(_verify_ctx()).resume == []


def test_init_final_check_streak_finishes_unmet():
    """终验连续不绿且无待修复方法 -> 未达标收尾(不再 ask_user)。"""
    test = TestResult(tests=1, failures=1, errors=0, report_found=True)
    d = decisions.decide_after_init(_init_ctx(
        final_check=True, test=test, tests_green=False, scope_met=True,
        final_check_fail_streak=config.FINAL_CHECK_FAIL_STREAK_LIMIT,
        report="REPORT-TEXT"))
    assert d.route == Route.FINISH
    assert d.exit_code == config.EXIT_OK
    assert d.resume == []
    assert d.report == "REPORT-TEXT"


def test_init_final_check_env_uncertain_streak_finishes_unverified():
    """终验连续结果不可信(无失败用例)达上限 -> 未复核收尾, 不混同测试失败。

    回归: 该路径曾与“测试不绿”共用未达标 summary, 而报告按未复核渲染,
    两者自相矛盾。
    """
    test = TestResult(tests=0, failures=0, errors=0, report_found=False)
    d = decisions.decide_after_init(_init_ctx(
        final_check=True, test=test, tests_green=False, scope_met=True,
        final_check_fail_streak=config.FINAL_CHECK_FAIL_STREAK_LIMIT,
        report="REPORT-TEXT"))
    assert d.route == Route.FINISH
    assert d.exit_code == config.EXIT_OK
    assert "未能复核达标" in d.summary
    assert "未达标" not in d.summary
    assert d.report == "REPORT-TEXT"


def test_verify_test_failure_streak_auto_skips_before_build_prompt():
    results = [TestOutcome(1, 0)] * config.TEST_FAIL_STREAK_ROUNDS
    ctx = _verify_ctx(method=_method(covered=5, missed=5, results=results),
                      test=TestResult(tests=1, failures=1, errors=0, report_found=True),
                      tests_green=False, iteration=3)
    d = decisions.decide_after_verify(ctx)
    assert d.route == Route.MAKE_PLAN
    assert d.auto_skip_method is True
    assert "测试失败" in d.auto_skip_reason
    assert d.reset_trajectory is True


def test_verify_not_green_single_round_routes_build_prompt_with_instructions():
    test = TestResult(tests=1, failures=1, errors=0, report_found=True,
                      failed_cases=[])
    ctx = _verify_ctx(method=_method(covered=5, missed=5), test=test,
                      tests_green=False, iteration=1)
    d = decisions.decide_after_verify(ctx)
    assert d.route == Route.BUILD_PROMPT
    assert d.status == "failed"
    assert d.instructions  # 附修复指引


def test_verify_report_missing_fail_closed_instructions():
    test = TestResult(report_found=False)  # 未产出报告
    ctx = _verify_ctx(method=_method(covered=5, missed=5), test=test,
                      tests_green=False, iteration=1)
    d = decisions.decide_after_verify(ctx)
    assert d.route == Route.BUILD_PROMPT
    assert "未产出" in d.instructions


# --------------------------------------------------------------------------- #
# decide_after_init
# --------------------------------------------------------------------------- #
def _init_ctx(**over):
    base = dict(
        final_check=False, method_mode=False, class_rate=90.0, threshold=80.0,
        failing_count=0, scope_met=True, class_met=True, test=_green(),
        tests_green=True, uncleaned=False, final_check_fail_streak=0,
        coverage_path="/w/coverage.json", state_path="/w/state.json",
        mvn_log="/w/mvn.log", test_simple="FooTest",
    )
    base.update(over)
    return decisions.InitContext(**base)


def test_init_is_met_matrix():
    assert decisions.init_is_met(True, True, True, False) is True
    assert decisions.init_is_met(True, True, False, False) is False   # class 模式需类级达标
    assert decisions.init_is_met(True, True, False, True) is True     # method 模式忽略类级
    assert decisions.init_is_met(False, True, True, False) is False   # 仍有 pending
    assert decisions.init_is_met(True, False, True, False) is False   # 测试不绿


def test_init_met_finishes():
    d = decisions.decide_after_init(_init_ctx())
    assert d.route == Route.FINISH
    assert d.exit_code == config.EXIT_OK
    assert "/w/coverage.json" in d.deliverables


def test_init_met_finish_carries_report():
    """finish 决策逐字透传入口脚本渲染好的收尾报告。"""
    d = decisions.decide_after_init(_init_ctx(report="REPORT-TEXT"))
    assert d.route == Route.FINISH
    assert d.report == "REPORT-TEXT"
    # 未提供 report 时不携带
    assert decisions.decide_after_init(_init_ctx()).report is None


def test_init_not_green_routes_make_plan_failed():
    test = TestResult(tests=1, failures=1, errors=0, report_found=True)
    d = decisions.decide_after_init(_init_ctx(test=test, tests_green=False, scope_met=False,
                                              failing_count=1))
    assert d.route == Route.MAKE_PLAN
    assert d.status == "failed"


def test_init_final_check_streak_finishes_unmet_with_failures_listed():
    test = TestResult(tests=1, failures=1, errors=0, report_found=True)
    d = decisions.decide_after_init(_init_ctx(
        final_check=True, test=test, tests_green=False, scope_met=True,
        final_check_fail_streak=config.FINAL_CHECK_FAIL_STREAK_LIMIT))
    assert d.route == Route.FINISH
    assert str(config.FINAL_CHECK_FAIL_STREAK_LIMIT) in d.summary


def test_init_final_check_failures_in_target_routes_write_code_with_make_plan():
    """终验失败用例全在目标测试类(且未达 streak 上限) -> 直接 write_code,
    on_complete=make_plan.py(LLM 修复后回接 make_plan, 自动路由回终验)。"""
    fc = FailedCase(class_name="com.example.FooTest", method="testX",
                    type="java.lang.AssertionError", message="boom")
    test = TestResult(tests=2, failures=1, errors=0, report_found=True,
                     failed_cases=[fc])
    d = decisions.decide_after_init(_init_ctx(
        final_check=True, test=test, tests_green=False, scope_met=True,
        final_check_fail_streak=0, test_simple="FooTest"))
    assert d.route == Route.WRITE_CODE
    assert d.on_complete is not None
    assert d.on_complete.script == "make_plan.py"
    assert d.reset_trajectory is True


def test_init_final_check_failures_outside_target_finishes_unmet():
    """终验失败用例包含非目标类 -> 未达标收尾(技能无权修复, 不必等 streak)。"""
    fc = FailedCase(class_name="com.other.OtherTest", method="testY",
                    type="java.lang.AssertionError", message="boom")
    test = TestResult(tests=2, failures=1, errors=0, report_found=True,
                     failed_cases=[fc])
    d = decisions.decide_after_init(_init_ctx(
        final_check=True, test=test, tests_green=False, scope_met=True,
        final_check_fail_streak=0, test_simple="FooTest"))
    assert d.route == Route.FINISH
    assert d.on_complete is None
    assert "非目标类" in d.summary


def test_init_final_check_environment_issue_retries_make_plan():
    """终验不绿但无失败用例(报告缺失/清理失败) -> 走重试路径, 不误判未达标收尾。"""
    # 报告缺失: 无失败用例但测试不绿 -> 重试
    test = TestResult(tests=0, failures=0, errors=0, report_found=False)
    d = decisions.decide_after_init(_init_ctx(
        final_check=True, test=test, tests_green=False, scope_met=True,
        final_check_fail_streak=0, test_simple="FooTest"))
    assert d.route == Route.MAKE_PLAN
    assert d.exit_code == config.EXIT_CONTINUE
    assert d.status == "failed"

    # 报告齐全但 surefire 目录清理失败(环境问题) -> 同样重试
    test2 = TestResult(tests=3, failures=0, errors=0, report_found=True)
    d2 = decisions.decide_after_init(_init_ctx(
        final_check=True, test=test2, tests_green=False, scope_met=True,
        final_check_fail_streak=0, test_simple="FooTest", uncleaned=True))
    assert d2.route == Route.MAKE_PLAN
    assert d2.exit_code == config.EXIT_CONTINUE

    # streak 达上限时仍有界收敛为未达标收尾(有界重试, 不无限空转)
    d3 = decisions.decide_after_init(_init_ctx(
        final_check=True, test=TestResult(report_found=False), tests_green=False,
        scope_met=True,
        final_check_fail_streak=config.FINAL_CHECK_FAIL_STREAK_LIMIT,
        test_simple="FooTest"))
    assert d3.route == Route.FINISH


def test_init_method_mode_skipped_target_red_tests_streak_finishes_unmet():
    """method 模式: 目标方法被跳过(scope_met=False)且队列已空, 终验连续不绿达上限
    -> 仍有界收敛为未达标收尾。

    回归: 分支曾用 scope_met 判"已无待修复方法", method 模式下该值为 False ->
        分支永不触发 -> 终验不绿失去上界(与 failing_count==0 的终态判定矛盾)。
    """
    d = decisions.decide_after_init(_init_ctx(
        final_check=True, method_mode=True, scope_met=False, failing_count=0,
        tests_green=False,
        test=TestResult(tests=1, failures=1, errors=0, report_found=True),
        final_check_fail_streak=config.FINAL_CHECK_FAIL_STREAK_LIMIT))
    assert d.route == Route.FINISH
    assert d.exit_code == config.EXIT_OK


def test_init_method_mode_skipped_target_failures_in_target_routes_write_code():
    """method 模式: 目标方法被跳过但队列已空, 终验失败用例全在目标测试类
    -> 仍走自动修复(write_code), 不因 scope_met=False 而误判无可修复方法。
    """
    fc = FailedCase(class_name="com.example.FooTest", method="testX",
                    type="java.lang.AssertionError", message="boom")
    d = decisions.decide_after_init(_init_ctx(
        final_check=True, method_mode=True, scope_met=False, failing_count=0,
        tests_green=False, test_simple="FooTest",
        test=TestResult(tests=2, failures=1, errors=0, report_found=True,
                        failed_cases=[fc]),
        final_check_fail_streak=0))
    assert d.route == Route.WRITE_CODE
    assert d.on_complete is not None
    assert d.on_complete.script == "make_plan.py"


# --------------------------------------------------------------------------- #
# decide_after_plan + 计划域函数
# --------------------------------------------------------------------------- #
def test_plan_has_pending_routes_build_prompt():
    d = decisions.decide_after_plan(decisions.PlanContext(
        has_pending=True, final_checked=False, current_label="foo(int)",
        current_rate=40.0, remaining=2))
    assert d.route == Route.BUILD_PROMPT


def test_plan_empty_not_checked_routes_final_check():
    d = decisions.decide_after_plan(decisions.PlanContext(has_pending=False, final_checked=False))
    assert d.route == Route.FINAL_CHECK


def test_plan_empty_checked_finishes():
    d = decisions.decide_after_plan(decisions.PlanContext(
        has_pending=False, final_checked=True,
        coverage_path="/w/coverage.json", state_path="/w/state.json"))
    assert d.route == Route.FINISH


def test_plan_empty_checked_finish_carries_report():
    d = decisions.decide_after_plan(decisions.PlanContext(
        has_pending=False, final_checked=True, report="REPORT-TEXT"))
    assert d.route == Route.FINISH
    assert d.report == "REPORT-TEXT"


def test_sort_failing_orders_by_rate_then_missed():
    a = _method(covered=1, missed=9)     # 10%
    b = _method(covered=5, missed=5)     # 50%
    a.key = MethodKey("a", "()")
    b.key = MethodKey("b", "()")
    done = _method(covered=10, missed=0, status=MethodStatus.DONE)
    ordered = decisions.sort_failing([b, done, a])
    assert [m.key.name for m in ordered] == ["a", "b"]  # done 被排除, 升序


def test_test_class_relpath():
    assert decisions.test_class_relpath("com.x.FooService") == \
        "src/test/java/com/x/FooServiceTest.java"
    assert decisions.test_class_relpath("Foo") == "src/test/java/FooTest.java"


# --------------------------------------------------------------------------- #
# 类级预算(批量模式)
# --------------------------------------------------------------------------- #
def _batch_state(**over):
    base = dict(project_root="/r", target_class="com.x.Foo", batch_mode=True,
                class_round_used=0, exploration_mode=False)
    base.update(over)
    return State(**base)


def test_class_budget_exhausted_only_in_batch_mode():
    """非批量模式(单类技能语义)不参与类级预算判定。"""
    state = _batch_state(batch_mode=False, class_round_used=999)
    assert state.class_budget_exhausted is False


def test_class_budget_exhausted_at_base_budget():
    budget = config.batch_class_round_budget()
    assert _batch_state(class_round_used=budget - 1).class_budget_exhausted is False
    assert _batch_state(class_round_used=budget).class_budget_exhausted is True


def test_class_budget_doubles_in_exploration_mode():
    """探索模式(大类拆分)预算翻倍: 基础预算用尽仍未耗尽, 翻倍后耗尽。"""
    budget = config.batch_class_round_budget()
    assert _batch_state(exploration_mode=True).class_round_budget == \
        budget * config.EXPLORATION_MODE_MULTIPLIER
    assert _batch_state(
        class_round_used=budget, exploration_mode=True).class_budget_exhausted is False
    assert _batch_state(
        class_round_used=budget * config.EXPLORATION_MODE_MULTIPLIER,
        exploration_mode=True).class_budget_exhausted is True


def test_class_budget_bonus_extends_effective_budget():
    """--unskip 追加的类级窗口计入有效预算, 耗尽判定随之放宽。"""
    budget = config.batch_class_round_budget()
    state = _batch_state(class_round_used=budget, class_round_bonus=budget)
    assert state.class_round_budget == budget * 2
    assert state.class_budget_exhausted is False
