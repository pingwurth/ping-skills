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

批量模式额外渲染:
    render_class_complete_report  -> 类内完成报告(子代理输出, 提醒终验由主流程统一执行)
    render_batch_finish_report    -> 批量最终报告(各类 before→after、达标/跳过/失败清单)
"""

from __future__ import annotations

from typing import Optional, TYPE_CHECKING

from .models import MethodStatus, State
from . import config

if TYPE_CHECKING:
    from .models import BatchState

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
        f"   rm -rf {worktree}/.agent/batch-unit-test-generator",
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


# --------------------------------------------------------------------------- #
# 批量模式渲染
# --------------------------------------------------------------------------- #
def render_class_complete_report(state: State) -> str:
    """渲染类内完成报告(批量模式, make_plan 队列空时输出)。

    子代理逐字转述此报告; 提醒终验由主流程 batch_finish 统一执行。
    """
    initial = _initial_class_rate(state)
    final = state.class_coverage.rate
    done_count = sum(1 for m in state.methods if m.status == MethodStatus.DONE)
    skipped_count = sum(1 for m in state.methods if m.status == MethodStatus.SKIPPED)
    pending_count = sum(1 for m in state.methods if m.is_abstract)
    lines = [
        f"类内完成报告: {state.target_class}",
        f"初始 Line Coverage: {_fmt_rate(initial)}",
        f"当前 Line Coverage: {_fmt_rate(final)}",
        f"类级轮次已用: {state.class_round_used}/{config.batch_class_round_budget()}",
        f"方法统计: done {done_count}, skipped {skipped_count}, 抽象/接口 {pending_count}",
        f"全局迭代次数: {state.global_iteration}",
        "",
        "注意: 终验(全量 mvn + 覆盖率复核)将由主流程 batch_finish 统一执行, 本类无需类内终验。",
    ]
    return "\n".join(lines)


def render_batch_finish_report(
    batch_state: "BatchState",
    class_results: list[dict] | None = None,
) -> str:
    """渲染批量最终报告(各类 before→after、达标/跳过/失败清单、总轮次)。

    Args:
        batch_state: BatchState 对象, 含各类条目。
        class_results: 各类终验结果列表(每项含 fqcn, final_rate, test_summary 等);
                      None 时从 batch_state.classes 取已记录的 final_rate。
    """
    results = class_results or []
    result_map = {r.get("fqcn"): r for r in results if r.get("fqcn")}
    done_list: list[str] = []
    skipped_list: list[str] = []
    failed_list: list[str] = []
    recheck_list: list[str] = []
    total_rounds = 0
    total_escalations = 0
    for entry in batch_state.classes:
        total_rounds += entry.rounds_used
        total_escalations += entry.escalations
        before = f"{entry.baseline_rate:.1f}%"
        r = result_map.get(entry.fqcn)
        after_val = (r.get("final_rate") if r else entry.final_rate)
        after = f"{after_val:.1f}%" if after_val is not None else _UNRECORDED
        line = f"  - {entry.fqcn}: {before} -> {after}"
        if entry.status == "done":
            done_list.append(line)
        elif entry.status == "skipped":
            line += f" (跳过: {entry.skip_reason or '未说明'})"
            skipped_list.append(line)
        elif entry.status == "failed":
            line += f" (失败: {entry.skip_reason or '未说明'})"
            failed_list.append(line)
        elif entry.status == "recheck":
            line += " (需重验)"
            recheck_list.append(line)
    initial = batch_state.initial_tests
    lines = [
        "批量单元测试最终报告",
        "=" * 50,
        f"目标分支: {batch_state.target_branch}",
        f"门槛: {batch_state.threshold}%",
        f"类别总数: {len(batch_state.classes)}",
        f"初始测试: Tests {initial.get('tests', 0)}, "
        f"Failures {initial.get('failures', 0)}, Errors {initial.get('errors', 0)}",
        f"总轮次: {total_rounds}",
        f"总升级次数: {total_escalations}",
        "",
    ]
    if done_list:
        lines.append(f"达标({len(done_list)} 类):")
        lines.extend(done_list)
    if skipped_list:
        lines.append(f"\n跳过({len(skipped_list)} 类):")
        lines.extend(skipped_list)
    if failed_list:
        lines.append(f"\n失败({len(failed_list)} 类):")
        lines.extend(failed_list)
    if recheck_list:
        lines.append(f"\n需重验({len(recheck_list)} 类):")
        lines.extend(recheck_list)
    lines.extend([
        "",
        "---",
        "收尾提醒(技能不会自动执行, 避免误删未合并代码, 请按顺序手动操作):",
        f"\n1. 合并分支: 本次所有改动都在工作树 {batch_state.project_root} 内, "
        "请将其合并回目标分支(git merge 或提交 PR)。",
        "\n2. 合并确认后, 清理工作树内未跟踪的中间产物:",
        f"   rm -rf {batch_state.project_root}/.agent/batch-unit-test-generator",
        "\n3. 删除工作树目录与对应分支:",
        f"   git worktree remove {batch_state.project_root}",
    ])
    return "\n".join(lines)
