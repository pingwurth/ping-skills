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


def test_verify_method_budget_exhausted_escalates():
    ctx = _verify_ctx(method=_method(covered=5, missed=5),
                      iteration=config.method_round_budget())
    d = decisions.decide_after_verify(ctx)
    assert d.route == Route.ASK_USER
    assert d.status == "needs_input"
    assert d.reset_trajectory is True


def test_verify_escalation_includes_test_status():
    """升级消息应反映测试状态, 而非仅覆盖率。"""
    # 测试失败时, 升级消息应包含测试失败信息
    test = TestResult(tests=1, failures=2, errors=1, report_found=True)
    ctx = _verify_ctx(method=_method(covered=5, missed=5),
                      test=test, tests_green=False,
                      iteration=config.method_round_budget())
    d = decisions.decide_after_verify(ctx)
    assert "测试失败" in d.question
    assert "Failures 2" in d.question
    assert "Errors 1" in d.question
    assert "测试失败" in d.summary

    # 测试全绿时, 升级消息应体现测试全绿
    ctx_green = _verify_ctx(method=_method(covered=5, missed=5),
                            iteration=config.method_round_budget())
    d_green = decisions.decide_after_verify(ctx_green)
    assert "测试全绿" in d_green.question
    assert "测试全绿" in d_green.summary


def test_verify_global_budget_exhausted_escalates():
    ctx = _verify_ctx(method=_method(covered=5, missed=5),
                      iteration=2, global_iteration=config.global_round_budget())
    d = decisions.decide_after_verify(ctx)
    assert d.route == Route.ASK_USER
    assert "全局" in d.summary


def test_verify_method_budget_bonus_extends_window():
    """--grant-rounds 追加的单方法预算窗口计入有效预算, 不再每轮必问。"""
    ctx = _verify_ctx(method=_method(covered=5, missed=5),
                      iteration=config.method_round_budget(),
                      method_round_bonus=3)
    d = decisions.decide_after_verify(ctx)
    assert d.route == Route.BUILD_PROMPT          # 8 < 8+3, 继续迭代
    # 窗口耗尽后再次升级, 且消息体现有效上限
    ctx2 = _verify_ctx(method=_method(covered=5, missed=5),
                       iteration=config.method_round_budget() + 3,
                       method_round_bonus=3)
    d2 = decisions.decide_after_verify(ctx2)
    assert d2.route == Route.ASK_USER
    assert str(config.method_round_budget() + 3) in d2.question


def test_verify_global_budget_bonus_extends_window():
    """全局预算窗口同理。"""
    ctx = _verify_ctx(method=_method(covered=5, missed=5),
                      iteration=2, global_iteration=config.global_round_budget(),
                      global_round_bonus=5)
    d = decisions.decide_after_verify(ctx)
    assert d.route == Route.BUILD_PROMPT          # 30 < 30+5, 继续迭代


def test_verify_escalations_carry_resume_options():
    """所有 verify 升级都携带 resume 恢复指引(继续/跳过/调门槛/终止)。"""
    # 预算类: 继续 = make_plan --grant-rounds
    ctx = _verify_ctx(method=_method(covered=5, missed=5),
                      iteration=config.method_round_budget())
    d = decisions.decide_after_verify(ctx)
    assert [r.option for r in d.resume] == ["continue", "skip_method",
                                            "adjust_threshold", "terminate"]
    cont = d.resume[0]
    assert cont.script == "make_plan.py"
    assert cont.params == ["--grant-rounds", str(config.RESUME_GRANT_ROUNDS)]
    adjust = d.resume[2]
    assert adjust.script == "make_plan.py" and "N" in adjust.params
    terminate = d.resume[3]
    assert terminate.script == ""

    # 轨迹类(连续失败): 继续 = build_prompt(轨迹已复位, 无需追加预算)
    results = [TestOutcome(1, 0)] * config.TEST_FAIL_STREAK_ROUNDS
    ctx2 = _verify_ctx(method=_method(covered=5, missed=5, results=results),
                       test=TestResult(tests=1, failures=1, errors=0, report_found=True),
                       tests_green=False, iteration=3)
    d2 = decisions.decide_after_verify(ctx2)
    assert d2.resume[0].script == "build_prompt.py"
    assert d2.resume[1].params == ["--skip-current"]

    # 非升级决策不携带 resume
    d3 = decisions.decide_after_verify(_verify_ctx())
    assert d3.resume == []


def test_init_final_check_streak_escalation_carries_resume():
    """终验不绿升级携带恢复指引(retry 经 make_plan 路由回终验)。"""
    test = TestResult(tests=1, failures=1, errors=0, report_found=True)
    d = decisions.decide_after_init(_init_ctx(
        final_check=True, test=test, tests_green=False, scope_met=True,
        final_check_fail_streak=config.FINAL_CHECK_FAIL_STREAK_LIMIT))
    assert d.route == Route.ASK_USER
    assert [r.option for r in d.resume] == ["retry", "adjust_threshold", "terminate"]
    assert d.resume[0].script == "make_plan.py"


def test_verify_test_failure_streak_escalates_before_build_prompt():
    results = [TestOutcome(1, 0)] * config.TEST_FAIL_STREAK_ROUNDS
    ctx = _verify_ctx(method=_method(covered=5, missed=5, results=results),
                      test=TestResult(tests=1, failures=1, errors=0, report_found=True),
                      tests_green=False, iteration=3)
    d = decisions.decide_after_verify(ctx)
    assert d.route == Route.ASK_USER
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


def test_init_final_check_streak_escalates():
    test = TestResult(tests=1, failures=1, errors=0, report_found=True)
    d = decisions.decide_after_init(_init_ctx(
        final_check=True, test=test, tests_green=False, scope_met=True,
        final_check_fail_streak=config.FINAL_CHECK_FAIL_STREAK_LIMIT))
    assert d.route == Route.ASK_USER


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


def test_init_final_check_failures_outside_target_escalates():
    """终验失败用例包含非目标类 -> 立即 ask_user(技能无权修复, 不必等 streak)。"""
    fc = FailedCase(class_name="com.other.OtherTest", method="testY",
                    type="java.lang.AssertionError", message="boom")
    test = TestResult(tests=2, failures=1, errors=0, report_found=True,
                     failed_cases=[fc])
    d = decisions.decide_after_init(_init_ctx(
        final_check=True, test=test, tests_green=False, scope_met=True,
        final_check_fail_streak=0, test_simple="FooTest"))
    assert d.route == Route.ASK_USER
    assert d.on_complete is None


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
