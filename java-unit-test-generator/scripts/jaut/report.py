"""Finish 收尾报告渲染(纯函数, 零 I/O)。

SKILL.md §8 的报告内容由脚本生成而非 LLM 拼装: 数字全部来自 state.json 的
持久化数据, 分支名来自 init 阶段的 git 查询, LLM 只逐字转述(见 Decision.report
与 next_step.report)。缺失的历史数据(旧版 state.json 未记录)降级显示"未记录",
不得虚构。

数据来源:
    目标类/门槛/迭代数   -> state.target_class / threshold / global_iteration
    初始类级覆盖率       -> coverage_history 首个含 class_rate 的条目(init 轮)
    最终类级覆盖率       -> state.class_coverage.rate
    方法 before          -> MethodCoverage.initial_rate(init 基线快照)
    测试汇总             -> state.final_test_summary(达标轮 surefire 结果)
    分支名/工作树路径    -> state.worktree_branch / project_root
"""

from __future__ import annotations

from typing import Optional

from .models import MethodStatus, State

_UNRECORDED = "未记录"


def _fmt_rate(value: Optional[float]) -> str:
    return f"{value:.2f}%" if value is not None else _UNRECORDED


def _initial_class_rate(state: State) -> Optional[float]:
    """coverage_history 首个含 class_rate 的条目即 init 基线。"""
    for entry in state.coverage_history:
        if "class_rate" in entry:
            return float(entry["class_rate"])
    return None


def _method_lines(state: State) -> list[str]:
    lines: list[str] = []
    for m in state.methods:
        if m.is_abstract:
            lines.append(f"{m.key.label()}: 抽象/接口方法, 无需测试")
            continue
        line = f"{m.key.label()}: {_fmt_rate(m.initial_rate)} -> {_fmt_rate(m.rate)}"
        if m.status == MethodStatus.SKIPPED:
            line += " (未纳入本次目标)"
        lines.append(line)
    return lines


def _cleanup_section(state: State) -> str:
    worktree = state.project_root
    branch = state.worktree_branch
    lines = [
        "收尾提醒(技能不会自动执行, 避免误删未合并代码, 请按顺序手动操作):",
        f"\n1. 合并分支: 本次所有改动都在工作树 {worktree} 内, "
        + (f"所在分支为 '{branch}', 请将其合并回目标分支(git merge 或提交 PR)。"
           if branch else
           "分支名未能确认(detached HEAD 或 git 不可用), 请先执行 "
           "git worktree list / git branch 确认后再合并。"),
        "\n2. 合并确认后, 清理工作树内未跟踪的中间产物(否则会阻塞 git worktree remove):",
        f"   rm -rf {worktree}/.agent/java-unit-test-generator",
        "\n3. 删除工作树目录与对应分支:",
        f"   git worktree remove {worktree}",
    ]
    if branch:
        lines.append(f"   git branch -d {branch}")
    else:
        lines.append("   git branch -d <分支名>  # 分支名以第 1 步确认为准")
    return "\n".join(lines)


def render_finish_report(state: State) -> str:
    """渲染 SKILL.md §8 格式的完整收尾报告(仅在 finish 路由时调用)。"""
    initial = _initial_class_rate(state)
    final = state.class_coverage.rate
    if initial is not None:
        improvement = f"{final - initial:+.2f}"
    else:
        improvement = _UNRECORDED

    summary = state.final_test_summary or {}
    lines = [
        f"目标类: {state.target_class}",
        f"初始 Line Coverage: {_fmt_rate(initial)}",
        f"最终 Line Coverage: {_fmt_rate(final)}",
        f"覆盖率提升: {improvement} 个百分点",
        "",
        "方法:",
    ]
    lines.extend(_method_lines(state) or [_UNRECORDED])
    lines.extend([
        "",
        f"总迭代次数: {state.global_iteration}",
        "",
        "测试:",
        f"Tests run: {summary.get('tests', _UNRECORDED)}",
        f"Failures: {summary.get('failures', _UNRECORDED)}",
        f"Errors: {summary.get('errors', _UNRECORDED)}",
        f"Skipped: {summary.get('skipped', _UNRECORDED)}",
        "",
        "最终状态: PASS",
        "",
        "---",
    ])
    lines.append(_cleanup_section(state))
    return "\n".join(lines)
