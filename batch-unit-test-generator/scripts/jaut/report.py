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
            line += (f" (跳过: {m.skip_reason})" if m.skip_reason
                     else " (未纳入本次目标)")
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


def render_finish_report(state: State, met: bool = True, note: str = "",
                         unverified: bool = False) -> str:
    """渲染 SKILL.md §8 格式的完整收尾报告(finish 路由时调用)。

    met=False 表示未达标收尾(方法被跳过/预算耗尽/终验不绿), 报告"最终状态"降级为
    未达标并附 note 说明原因; 方法行已逐条列出跳过原因, 数字仍全部取自 state.json。
    unverified=True 表示环境不可信未能复核(surefire 报告缺失/无法解析/清理失败且
    无失败用例): "最终状态"显示"未复核(测试结果不可信)", 不混同未达标。
    """
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
    ])
    if unverified:
        lines.append("最终状态: 未复核(测试结果不可信)")
    else:
        lines.append("最终状态: PASS" if met else "最终状态: 未达标")
    if (not met or unverified) and note:
        lines.append(f"说明: {note}")
    lines.extend(["", "---"])
    lines.append(_cleanup_section(state))
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 批量模式渲染
# --------------------------------------------------------------------------- #
def render_class_complete_report(state: State) -> str:
    """渲染类内完成报告(批量模式, make_plan 队列空时输出)。

    子代理逐字转述此报告; 提醒终验由主流程 batch_finish 统一执行。
    "被跳过的方法"仅列出带 skip_reason 的自动/手动跳过方法; 方法组过滤产生的
    组外方法(skip_reason 为空)单独统计, 不作为未达标依据。
    """
    initial = _initial_class_rate(state)
    final = state.class_coverage.rate
    budget = state.class_round_budget
    done_count = sum(1 for m in state.methods if m.status == MethodStatus.DONE)
    skipped = [m for m in state.methods
               if m.status == MethodStatus.SKIPPED and not m.is_abstract
               and m.skip_reason]
    out_of_scope = [m for m in state.methods
                    if m.status == MethodStatus.SKIPPED and not m.is_abstract
                    and not m.skip_reason]
    abstract_count = sum(1 for m in state.methods if m.is_abstract)
    met = final >= state.threshold
    lines = [
        f"类内完成报告: {state.target_class}",
        f"初始 Line Coverage: {_fmt_rate(initial)}",
        f"当前 Line Coverage: {_fmt_rate(final)} (门槛 {state.threshold}%)",
        f"类级轮次已用: {state.class_round_used}/{budget}"
        + ("(探索模式预算已翻倍)" if state.exploration_mode else ""),
        f"方法统计: done {done_count}, skipped {len(skipped)}, 抽象/接口 {abstract_count}",
        f"全局迭代次数: {state.global_iteration}",
        f"类内状态: {'达标' if met else '未达标(类级覆盖率未达门槛)'}",
    ]
    if skipped:
        lines.append("")
        lines.append("被跳过的方法:")
        lines.extend(f"- {m.key.label()}: {m.skip_reason}" for m in skipped)
    if out_of_scope:
        lines.append("")
        lines.append(f"本组外方法: {len(out_of_scope)} 个(未纳入本组目标; "
                     "方法组模式下将由后续组处理)")
    lines.extend([
        "",
        "注意: 终验(全量 mvn + 覆盖率复核)将由主流程 batch_finish 统一执行, 本类无需类内终验。",
    ])
    return "\n".join(lines)


def render_batch_finish_report(
    batch_state: "BatchState",
    class_results: list[dict] | None = None,
) -> str:
    """渲染批量最终报告(各类 before→after、达标/未达标/跳过/失败清单、总轮次)。

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
    unmet_list: list[str] = []
    unverified_list: list[str] = []
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
        elif entry.status == "unmet":
            line += f" (未达标: {entry.skip_reason or '无可补测方法'})"
            unmet_list.append(line)
        elif entry.status == "unverified":
            # 环境不可信待复核(非终态): 环境修复后重跑 batch_finish 重新终验
            line += (f" (待环境复核: {entry.skip_reason or '测试结果不可信'}; "
                     "环境修复后重跑 batch_finish.py)")
            unverified_list.append(line)
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
        f"总自动跳过方法数: {total_escalations}",
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
    if unmet_list:
        lines.append(f"\n未达标({len(unmet_list)} 类):")
        lines.extend(unmet_list)
    if unverified_list:
        lines.append(f"\n待环境复核({len(unverified_list)} 类):")
        lines.extend(unverified_list)
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
