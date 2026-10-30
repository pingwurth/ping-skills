"""裁决聚合: quorum/严格多数/平局(移植 JS 矩阵)+ 升级补透镜 + keep 阈值/下限。"""

from __future__ import annotations

from crp import config, verify
from crp.models import State

V = lambda verdict, severity="P2": {"verdict": verdict, "severity": severity}  # noqa: E731


def test_票数法矩阵_与js对齐():
    cases = [
        ([], True, "PLAUSIBLE"),            # 全部失败 —— 既非赞成也非反对
        ([V("REFUTED")], True, "PLAUSIBLE"),   # 法定票数不足: 单票不能杀 P0/P1
        ([V("CONFIRMED")], True, "PLAUSIBLE"),  # 单票也不能确认 P0/P1
        ([V("CONFIRMED"), V("REFUTED"), V("PLAUSIBLE")], True, "PLAUSIBLE"),  # 平局=不确定
        ([V("REFUTED"), V("REFUTED"), V("CONFIRMED")], True, "REFUTED"),      # 严格多数反驳
        ([V("CONFIRMED"), V("CONFIRMED"), V("PLAUSIBLE")], True, "CONFIRMED"),  # 多数确认
        ([V("CONFIRMED"), V("PLAUSIBLE"), V("PLAUSIBLE")], True, "PLAUSIBLE"),  # 少数确认
        ([V("REFUTED")], False, "REFUTED"),    # 非高危: 单 correctness 透镜裁决
        ([V("CONFIRMED")], False, "CONFIRMED"),
    ]
    for votes, high, expected in cases:
        got = verify.aggregate_verdict(votes, high)
        assert got == expected, f"{[v['verdict'] for v in votes]} high={high} -> {got}, 期望 {expected}"


def _state(mode="standard", keep=None) -> State:
    params = config.mode_params(mode)
    return State(mode=mode, sweep_plan={"keep": keep or params["keep"],
                                        "cap": params["cap"], "dry_required": params["dry_required"],
                                        "max_rounds": params["max_rounds"]})


def _cand(cid, severity="P2", confidence=0.9):
    return {"id": cid, "file": "a.py", "line": 1, "summary": "s", "failure_scenario": "f",
            "confidence": confidence, "severity": severity, "category": "correctness",
            "angle": "A", "round": 1, "origin": "sweep", "status": "fresh", "dup_of": None,
            "votes": [], "verdict": None, "evidence": "", "severity_final": severity,
            "kept": False, "drop_reason": None, "stale": False, "rank": None}


def test_标准模式keep阈值_confirmed加高危plausible():
    state = _state("standard")
    state.candidates = [_cand("c1", "P1"), _cand("c2", "P2"), _cand("c3", "P2")]
    state.cursor["pending_verify"] = [
        {"id": "c1", "lenses": ["correctness", "reproduction", "security"]},
        {"id": "c2", "lenses": ["correctness"]},
        {"id": "c3", "lenses": ["correctness"]},
    ]
    votes = {"votes": [
        {"id": "c1", "lens": "correctness", "verdict": "PLAUSIBLE", "evidence": "q", "severity": "P1", "source": "inline"},
        {"id": "c1", "lens": "reproduction", "verdict": "PLAUSIBLE", "evidence": "q", "severity": "P1", "source": "inline"},
        {"id": "c1", "lens": "security", "verdict": "CONFIRMED", "evidence": "q", "severity": "P1", "source": "inline"},
        # P2 PLAUSIBLE 在 standard 被阈值丢弃
        {"id": "c2", "lens": "correctness", "verdict": "PLAUSIBLE", "evidence": "q", "severity": "P2", "source": "inline"},
        # REFUTED 出局
        {"id": "c3", "lens": "correctness", "verdict": "REFUTED", "evidence": "q", "severity": "P2", "source": "inline"},
    ]}
    assert verify.validate_votes_file(votes, state) == []
    outcome = verify.apply_votes(state, votes["votes"])
    assert outcome.finalized == ["c1", "c2", "c3"]
    by_id = {c["id"]: c for c in state.candidates}
    assert by_id["c1"]["kept"] is True          # P1 PLAUSIBLE → standard 保留
    assert by_id["c2"]["drop_reason"] == "mode_threshold"
    assert by_id["c3"]["drop_reason"] == "refuted"
    assert state.accounting["refuted"] == 1


def test_升级规则_p2被单票升p1补透镜():
    state = _state("deep")
    state.candidates = [_cand("c1", "P2")]
    state.cursor["pending_verify"] = [{"id": "c1", "lenses": ["correctness"]}]
    votes = {"votes": [{"id": "c1", "lens": "correctness", "verdict": "CONFIRMED",
                        "evidence": "q", "severity": "P1", "source": "agent"}]}
    assert verify.validate_votes_file(votes, state) == []
    outcome = verify.apply_votes(state, votes["votes"])
    assert outcome.upgraded == ["c1"] and outcome.finalized == []
    assert state.candidates[0]["severity"] == "P1"  # 升级改写 severity → 高危法定票数
    assert state.cursor["pending_verify"] == [{"id": "c1", "lenses": ["reproduction", "security"]}]


def test_置信度下限在任何模式生效():
    state = _state("deep")
    state.candidates = [_cand("c1", "P1", confidence=0.4)]
    state.candidates[0]["votes"] = [
        {"lens": "correctness", "verdict": "CONFIRMED", "evidence": "q", "severity": "P1", "source": "agent"},
        {"lens": "reproduction", "verdict": "CONFIRMED", "evidence": "q", "severity": "P1", "source": "agent"},
        {"lens": "security", "verdict": "CONFIRMED", "evidence": "q", "severity": "P1", "source": "agent"},
    ]
    verify.finalize_candidate(state, state.candidates[0])
    assert state.candidates[0]["drop_reason"] == "below_floor"
    assert state.candidates[0]["kept"] is False
    assert state.accounting["below_floor"] == 1


def test_票据校验_未知id与空evidence拒收():
    state = _state()
    state.cursor["pending_verify"] = [{"id": "c1", "lenses": ["correctness"]}]
    bad = {"votes": [{"id": "cX", "lens": "correctness", "verdict": "CONFIRMED",
                      "evidence": "", "severity": "P1", "source": "inline"}]}
    errs = verify.validate_votes_file(bad, state)
    assert any("不在待验证批次" in e for e in errs)
    assert any("evidence" in e for e in errs)


def test_quick无验证终判_only_floor():
    state = _state("quick")
    state.candidates = [_cand("c1", confidence=0.6), _cand("c2", confidence=0.3), _cand("c3", confidence=0.9)]
    verify.finalize_without_verify(state)
    kept = {c["id"]: c["kept"] for c in state.candidates}
    assert kept == {"c1": True, "c2": False, "c3": True}
    assert all(c["verdict"] is None for c in state.candidates)
