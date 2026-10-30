"""对抗验证: 票据校验、裁决聚合(quorum/严格多数/平局落 PLAUSIBLE)、升级补透镜、
keep 阈值与置信度下限、轮次收敛(dry 连击 / maxRounds)。

聚合语义与 workflows/deep-review.js 的 aggregateVerdict 逐点对齐:
    空votes→PLAUSIBLE; 非高危单透镜裁决; 高危需≥2票; 严格多数 REFUTED 才杀;
    平局 = 不确定 → PLAUSIBLE。升级规则: P2/P3 被单透镜票升为 P0/P1 →
    补齐其余透镜再终裁(P0/P1 永远不躺在单票上)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from . import config
from .models import State


def is_high(severity: Optional[str]) -> bool:
    return severity in config.HIGH_SEVERITIES


def aggregate_verdict(votes: list[dict], high: bool) -> str:
    """票数法(与 JS aggregateVerdict 对齐; tests/test_aggregate.py 锁死)。"""
    if not votes:
        return "PLAUSIBLE"       # 全部反驳者失败 —— 既非赞成也非反对
    if not high:
        return votes[0].get("verdict", "PLAUSIBLE")  # 非高危: 单 correctness 透镜裁决
    if len(votes) < 2:
        return "PLAUSIBLE"       # 法定票数不足
    refuted = sum(1 for v in votes if v.get("verdict") == "REFUTED")
    if refuted > len(votes) / 2:
        return "REFUTED"         # 严格多数; 平局存活
    confirmed = sum(1 for v in votes if v.get("verdict") == "CONFIRMED")
    return "CONFIRMED" if confirmed >= (len(votes) + 1) // 2 else "PLAUSIBLE"


def validate_votes_file(data: object, state: State) -> list[str]:
    """verdicts 提交文件校验(含 id 存在性与 pending 匹配)。"""
    if not isinstance(data, dict):
        return ["提交文件必须是 JSON 对象 {votes: [...]}"]
    votes = data.get("votes")
    if not isinstance(votes, list) or not votes:
        return ["votes 必须是非空数组"]
    if len(votes) > config.MAX_VOTES_PER_FILE:
        return [f"votes 数 {len(votes)} 超单文件上限 {config.MAX_VOTES_PER_FILE}"]
    pending = {p["id"]: p for p in (state.cursor.get("pending_verify") or [])}
    errs: list[str] = []
    for i, v in enumerate(votes):
        w = f"votes[{i}]"
        if not isinstance(v, dict):
            errs.append(f"{w}: 必须是对象")
            continue
        cid = v.get("id")
        if not isinstance(cid, str) or not cid:
            errs.append(f"{w}: id 必须是非空字符串")
        elif cid not in pending:
            errs.append(f"{w}: 候选 {cid} 不在待验证批次(已终裁或未知 id)")
        if v.get("lens") not in config.LENSES:
            errs.append(f"{w}: lens 必须是 {'/'.join(config.LENSES)}")
        if v.get("verdict") not in config.VERDICTS:
            errs.append(f"{w}: verdict 必须是 {'/'.join(config.VERDICTS)}")
        ev = v.get("evidence")
        if not isinstance(ev, str) or not ev.strip():
            errs.append(f"{w}: evidence 必须非空(无引证不下裁决)")
        elif len(ev) > config.MAX_EVIDENCE_CHARS:
            errs.append(f"{w}: evidence 长度超上限 {config.MAX_EVIDENCE_CHARS}")
        if v.get("severity") not in config.SEVERITIES:
            errs.append(f"{w}: severity 必须是 {'/'.join(config.SEVERITIES)}")
        if v.get("source") not in ("agent", "inline"):
            errs.append(f"{w}: source 必须是 agent|inline")
    return errs


@dataclass
class VoteOutcome:
    finalized: list[str] = field(default_factory=list)   # 本批完成终裁的 id
    upgraded: list[str] = field(default_factory=list)    # 升级补透镜的 id
    errors: list[str] = field(default_factory=list)


def _best_vote(votes: list[dict], verdict: str) -> Optional[dict]:
    for v in votes:
        if v.get("verdict") == verdict:
            return v
    return votes[0] if votes else None


def _finalize_candidate(state: State, cand: dict) -> None:
    """单候选终裁: 聚合 → severity_final → keep 阈值 + 置信度下限。"""
    votes = cand.get("votes") or []
    high_before = is_high(cand.get("severity"))
    verdict = aggregate_verdict(votes, high_before)
    best = _best_vote(votes, verdict)
    severity_final = (best or {}).get("severity") or cand.get("severity", "P3")
    cand["verdict"] = verdict
    cand["severity_final"] = severity_final
    cand["evidence"] = (best or {}).get("evidence", "")
    cand["status"] = "verified"

    acc = state.accounting
    if verdict == "REFUTED":
        cand["kept"] = False
        cand["drop_reason"] = "refuted"
        acc["refuted"] = acc.get("refuted", 0) + 1
        return
    keep = (state.sweep_plan or {}).get("keep", config.KEEP_NONE)
    threshold_ok = (
        (verdict == "CONFIRMED")
        or (keep == config.KEEP_CONFIRMED_PLAUSIBLE and verdict == "PLAUSIBLE")
        or (keep == config.KEEP_CONFIRMED_P01 and verdict == "PLAUSIBLE"
            and is_high(severity_final))
    )
    conf = cand.get("confidence", 0.0)
    if not threshold_ok:
        cand["kept"] = False
        cand["drop_reason"] = "mode_threshold"
    elif conf < config.CONFIDENCE_FLOOR:
        cand["kept"] = False
        cand["drop_reason"] = "below_floor"
        acc["below_floor"] = acc.get("below_floor", 0) + 1
    else:
        cand["kept"] = True
        cand["drop_reason"] = None


def apply_votes(state: State, votes: list[dict]) -> VoteOutcome:
    """并入票据并终裁已集齐透镜的候选(调用方已通过 validate_votes_file)。

    高危候选(P0/P1): 需 3 透镜各一票; 非高危: correctness 单票。
    升级: 非高危候选的单票 severity 为 P0/P1 → 补其余透镜(不终裁)。
    """
    outcome = VoteOutcome()
    pending = state.cursor.setdefault("pending_verify", [])
    by_id = {p["id"]: p for p in pending}
    cand_by_id = {c.get("id"): c for c in state.candidates}

    for v in votes:
        cand = cand_by_id.get(v["id"])
        if cand is None:
            outcome.errors.append(f"候选 {v['id']} 不在台账")
            continue
        cand.setdefault("votes", []).append({
            "lens": v.get("lens"), "verdict": v.get("verdict"),
            "evidence": v.get("evidence"), "severity": v.get("severity"),
            "source": v.get("source", "inline"),
        })

    # 逐 pending 检查是否集齐
    still_pending: list[dict] = []
    for item in pending:
        cand = cand_by_id.get(item["id"])
        if cand is None:
            continue
        votes_c = cand.get("votes") or []
        got_lenses = {v.get("lens") for v in votes_c}
        need = item.get("lenses") or ["correctness"]
        if not set(need) <= got_lenses:
            still_pending.append(item)
            continue
        # 单透镜非高危的升级判定(补齐其余透镜再终裁)
        original_high = is_high(cand.get("severity"))
        if not original_high and len(need) == 1:
            only = votes_c[0]
            if is_high(only.get("severity")):
                # 升级: 改写 severity(finder 猜测让位于 refuter 判定),
                # 后续聚合按高危法定票数走 —— P0/P1 永不躺在单票上
                cand["severity"] = only.get("severity")
                rest = [l for l in config.LENSES if l not in need]
                still_pending.append({"id": item["id"], "lenses": rest})
                outcome.upgraded.append(item["id"])
                continue
        _finalize_candidate(state, cand)
        outcome.finalized.append(item["id"])

    state.cursor["pending_verify"] = still_pending
    return outcome


def required_lenses(severity: Optional[str]) -> list[str]:
    """高危 3 透镜, 其余单 correctness(与 deep-review.js 的 refute 路径一致)。"""
    return list(config.LENSES) if is_high(severity) else ["correctness"]


def finalize_candidate(state: State, cand: dict) -> None:
    """单候选终裁的公开入口(journal 载体摄取路径使用)。"""
    _finalize_candidate(state, cand)


def finalize_without_verify(state: State) -> None:
    """quick(无验证轮)的终裁: 唯一闸门是置信度下限, verdict 保持 null。"""
    acc = state.accounting
    for c in state.candidates:
        if c.get("status") != "fresh":
            continue
        c["status"] = "unverified"
        if c.get("confidence", 0.0) < config.CONFIDENCE_FLOOR:
            c["kept"] = False
            c["drop_reason"] = "below_floor"
            acc["below_floor"] = acc.get("below_floor", 0) + 1
        else:
            c["kept"] = True


def build_verify_queue(state: State, ids: list[str]) -> None:
    """把 fresh 候选排入验证队列(按严重度定透镜)。"""
    queue = []
    by_id = {c.get("id"): c for c in state.candidates}
    for cid in ids:
        cand = by_id.get(cid)
        if cand is None:
            continue
        queue.append({"id": cid, "lenses": required_lenses(cand.get("severity"))})
    state.cursor["pending_verify"] = queue


def finalize_round(state: State) -> str:
    """轮收尾: 记账 + 收敛判定; 返回 'gap' | 'report'。

    dry = 该轮零新候选(Phase 4 契约: 全部新候选死在验证里不算 dry);
    收敛 = dry_streak >= dry_required; 硬顶 = round >= max_rounds。
    """
    acc = state.accounting
    hist = acc.setdefault("round_history", [])
    round_no = state.cursor.get("round", 0)
    params = config.mode_params(state.mode)
    dry_required = (state.sweep_plan or {}).get("dry_required", params["dry_required"])
    max_rounds = (state.sweep_plan or {}).get("max_rounds", params["max_rounds"])

    entry = next((h for h in hist if h.get("round") == round_no), None)
    if entry is None:
        entry = {"round": round_no, "kind": state.cursor.get("round_kind", "sweep"),
                 "fresh": 0, "dup": 0, "kept_after_verify": 0, "dry": False}
        hist.append(entry)
    entry["kept_after_verify"] = sum(1 for c in state.candidates if c.get("kept"))
    fresh = entry.get("fresh", 0)
    entry["dry"] = fresh == 0
    if fresh == 0:
        state.cursor["dry_streak"] = state.cursor.get("dry_streak", 0) + 1
    else:
        state.cursor["dry_streak"] = 0

    if round_no >= max_rounds:
        return "report"
    if dry_required and state.cursor.get("dry_streak", 0) >= dry_required:
        return "report"
    if not dry_required:
        return "report"  # quick/standard(无 gap 循环诉求) —— standard 的 gap 由 dry_required=1 开启
    return "gap"
