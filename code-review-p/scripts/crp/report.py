"""报告渲染: 排序、置信度下限后的 cap、账目行、表格与 Details 装配。

报告的每个数字都从台账计算 —— LLM 逐字透传, 不得改写(SKILL.md 协议义务)。
Details 散文来自 details 提交; 缺项回退台账字段, 绝不留空模板。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from . import config
from .models import State


def _sev_rank(sev: Optional[str]) -> int:
    try:
        return config.SEVERITIES.index(sev or "P3")
    except ValueError:
        return len(config.SEVERITIES)


def _cat_rank(cat: Optional[str]) -> int:
    return config.CATEGORY_RANK.get(cat or "", config.CATEGORY_RANK_DEFAULT)


def rank_findings(kept: list[dict]) -> list[dict]:
    """排序: 严重度 → 类别(correctness/security/cleanup/altitude/conventions) → 置信度降序。"""
    return sorted(kept, key=lambda c: (_sev_rank(c.get("severity_final")),
                                       _cat_rank(c.get("category")),
                                       -(c.get("confidence") or 0.0)))


def scope_one_line(state: State) -> str:
    sc = state.scope or {}
    if sc.get("target"):
        return str(sc["target"])
    if sc.get("scope_command"):
        return sc["scope_command"]
    if sc.get("untracked"):
        return f"untracked×{len(sc['untracked'])}"
    return "(空范围)"


def run_ledger_line(state: State) -> str:
    acc = state.accounting
    carrier = state.carrier or {}
    carrier_txt = f"carrier {carrier.get('carrier', '?')}"
    if carrier.get("reason"):
        carrier_txt += f" — {carrier['reason']}"
    return (f"Run: {carrier_txt} · rounds={acc.get('rounds', 0)} · "
            f"candidates={acc.get('total_seen', 0)} · dupDropped={acc.get('dup_dropped', 0)} · "
            f"refuted={acc.get('refuted', 0)} · capped-out={acc.get('capped_out', 0)}")


def _finding_details(c: dict, detail: Optional[dict]) -> list[str]:
    d = detail or {}
    verdict = c.get("verdict") or "UNVERIFIED (quick — no verify pass)"
    lines = [
        f"**[{c.get('severity_final')}] {d.get('title') or c.get('summary')} — `{c.get('file')}:{c.get('line')}`**",
        f"- **Problem:** {d.get('problem') or c.get('summary')}",
        f"- **Failure scenario:** {d.get('failure_scenario') or c.get('failure_scenario')}",
        f"- **Evidence:** {d.get('evidence_override') or c.get('evidence') or '(无引证 —— 见 Open questions)'}",
        f"- **Fix:** {d.get('fix') or '(未给出)'}",
        f"- **Confidence:** {c.get('confidence')} · **Verdict:** {verdict}",
    ]
    if c.get("stale"):
        lines.insert(1, "- **⚠ stale:** 该候选锚定于范围漂移前的行号, 复核后再修。")
    return lines


def render(state: State, details: dict) -> str:
    """渲染完整报告并更新账目(kept_after_thresholds/capped_out/rounds)。"""
    kept = [c for c in state.candidates if c.get("kept")]
    ordered = rank_findings(kept)
    cap = (state.sweep_plan or {}).get("cap", 10)
    reportable = ordered[:cap]
    state.accounting["kept_after_thresholds"] = len(ordered)
    state.accounting["capped_out"] = max(0, len(ordered) - len(reportable))
    state.accounting["rounds"] = state.cursor.get("round", 0)

    detail_by_id = {d.get("id"): d for d in (details.get("details") or [])}
    out: list[str] = [f"## Review: {scope_one_line(state)}", ""]
    out.append(f"Brief: {details.get('brief_summary') or '(见运行目录 in/02-brief.md)'}")
    out.append("")
    out.append(run_ledger_line(state))
    if state.scope.get("repo_mode") or state.scope.get("whole_file_mode"):
        out.append(f"Coverage: {details.get('coverage') or '(缺失 —— LLM 未声明, 违反契约)'}")
    out.append("")

    if not reportable:
        out.append(f"No significant issues found in {scope_one_line(state)}.")
        out.append(f"Residual risks: {details.get('residual_risks') or '(未声明)'}")
        out.append(f"Testing gaps: {details.get('testing_gaps') or '(未声明)'}")
        out.append("")
        out.append("### Summary")
        out.extend(f"- {b}" for b in details.get("summary_bullets") or [])
        return "\n".join(out)

    out.append("| # | Location | Sev | Finding |")
    out.append("|---|----------|-----|---------|")
    for i, c in enumerate(reportable):
        out.append(f"| {i + 1} | {c.get('file')}:{c.get('line')} | {c.get('severity_final')} | {c.get('summary')} |")
    out.append("")
    out.append("### Details")
    out.append("")
    outcomes = {o.get("id"): o for o in (state.fix.get("outcomes") or [])}
    for c in reportable:
        out.extend(_finding_details(c, detail_by_id.get(c.get("id"))))
        if c.get("id") in outcomes:
            o = outcomes[c.get("id")]
            out.append(f"- **Fix outcome:** {o.get('outcome')}"
                       + (f"({o.get('reason')})" if o.get("reason") else "")
                       + (f" · 门禁: {o.get('gate_results')}" if o.get("gate_results") else ""))
        out.append("")
    if state.accounting.get("capped_out"):
        out.append(f"(受 cap {cap} 截断, 另有 {state.accounting['capped_out']} 条已入台账未入榜 —— 见 out/ledger.json)")
        out.append("")
    out.append("### Open questions / assumptions")
    out.append(details.get("open_questions") or "(无)")
    out.append("")
    out.append("### Summary")
    out.extend(f"- {b}" for b in details.get("summary_bullets") or [])
    return "\n".join(out)


def export_ledger(state: State, out_dir: Path) -> Path:
    """台账导出(含 REFUTED 与 dup —— 审计与续跑的完整事实)。"""
    path = out_dir / "ledger.json"
    payload = {
        "run_id": state.run_id,
        "mode": state.mode,
        "accounting": state.accounting,
        "coverage": state.coverage,
        "candidates": state.candidates,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def validate_details_file(data: object, state: State) -> list[str]:
    """details 提交校验。"""
    if not isinstance(data, dict):
        return ["details 提交必须是 JSON 对象"]
    errs: list[str] = []
    if state.scope.get("repo_mode") or state.scope.get("whole_file_mode"):
        if not str(data.get("coverage") or "").strip():
            errs.append("repo/whole-file 模式必须声明 coverage(deep-read/sampled/untouched)")
    bullets = data.get("summary_bullets")
    if not isinstance(bullets, list) or not (3 <= len(bullets) <= 5):
        errs.append("summary_bullets 必须是 3-5 条")
    kept = [c for c in state.candidates if c.get("kept")]
    kept_ids = {c.get("id") for c in kept}
    cap = (state.sweep_plan or {}).get("cap", 10)
    for i, d in enumerate(data.get("details") or []):
        if not isinstance(d, dict) or not d.get("id"):
            errs.append(f"details[{i}] 必须是含 id 的对象")
        elif d["id"] not in kept_ids:
            errs.append(f"details[{i}].id={d['id']} 不在 kept 名单")
    # 入榜者(排序后前 cap 条)必须有 details
    ranked_ids = {c.get("id") for c in rank_findings(kept)[:cap]}
    provided = {d.get("id") for d in data.get("details") or [] if isinstance(d, dict)}
    missing = ranked_ids - provided
    if missing:
        errs.append(f"入榜候选缺少 details: {sorted(missing)}")
    return errs
