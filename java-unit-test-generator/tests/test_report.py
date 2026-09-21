"""report.render_finish_report 单测: 完整报告、数据降级与收尾命令顺序。"""

from __future__ import annotations

from jaut import report
from jaut.models import ClassCoverage, MethodCoverage, MethodKey, MethodStatus, State


def _state(**over) -> State:
    base = dict(
        project_root="/wt/myproj.worktree20260908",
        target_class="com.example.FooService",
        threshold=80.0,
        class_coverage=ClassCoverage.of(90, 10),
        global_iteration=12,
        worktree_branch="worktree20260908",
        final_test_summary={"tests": 15, "failures": 0, "errors": 0, "skipped": 1},
        coverage_history=[{"phase": "init", "class_rate": 50.0}],
        methods=[
            MethodCoverage(key=MethodKey("foo", "()V"), covered=9, missed=1,
                           status=MethodStatus.DONE, initial_rate=40.0),
            MethodCoverage(key=MethodKey("bar", "(I)V"), covered=0, missed=0,
                           status=MethodStatus.DONE),
        ],
    )
    base.update(over)
    return State(**base)


def test_render_full_report():
    text = report.render_finish_report(_state())
    assert "目标类: com.example.FooService" in text
    assert "初始 Line Coverage: 50.00%" in text
    assert "最终 Line Coverage: 90.00%" in text
    assert "覆盖率提升: +40.00" in text
    assert "foo()V: 40.00% -> 90.00%" in text
    assert "bar(I)V: 抽象/接口方法, 无需测试" in text
    assert "总迭代次数: 12" in text
    assert "Tests run: 15" in text
    assert "Failures: 0" in text
    assert "Skipped: 1" in text
    assert "最终状态: PASS" in text


def test_cleanup_commands_in_order_with_known_branch():
    text = report.render_finish_report(_state())
    assert "'worktree20260908'" in text
    assert "git worktree remove /wt/myproj.worktree20260908" in text
    assert "git branch -d worktree20260908" in text
    # 步骤顺序: 清理中间产物在删除工作树之前(未跟踪目录会阻塞 worktree remove)
    assert text.index("2. 合并确认后") < text.index("3. 删除工作树目录")


def test_cleanup_commands_with_unknown_branch():
    text = report.render_finish_report(_state(worktree_branch=None))
    assert "git worktree list" in text          # 提示用户自行确认
    assert "git branch -d <分支名>" in text      # 不虚构具体分支名


def test_render_degrades_when_history_missing():
    """旧版 state.json 缺 initial_rate/final_test_summary/coverage_history 时降级。"""
    m = MethodCoverage(key=MethodKey("baz", "()V"), covered=5, missed=5,
                       status=MethodStatus.DONE)
    state = _state(coverage_history=[], methods=[m], final_test_summary=None)
    text = report.render_finish_report(state)
    assert "初始 Line Coverage: 未记录" in text
    assert "覆盖率提升: 未记录" in text
    assert "baz()V: 未记录 -> 50.00%" in text
    assert "Tests run: 未记录" in text


def test_render_negative_improvement():
    text = report.render_finish_report(
        _state(coverage_history=[{"phase": "init", "class_rate": 95.0}]))
    assert "覆盖率提升: -5.00" in text


def test_render_skipped_method_annotated():
    m = MethodCoverage(key=MethodKey("other", "()V"), covered=5, missed=5,
                       status=MethodStatus.SKIPPED, initial_rate=50.0)
    text = report.render_finish_report(_state(methods=[m]))
    assert "other()V: 50.00% -> 50.00% (未纳入本次目标)" in text
