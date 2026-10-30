"""报告渲染: 排序/cap/账目行/no-findings 契约/coverage 强制/fix outcomes。"""

from __future__ import annotations

from crp import config, report
from crp.models import State


def _kept(cid, sev, cat, conf, file="a.py", line=1):
    return {"id": cid, "file": file, "line": line, "summary": f"summary {cid}",
            "failure_scenario": "fs", "confidence": conf, "severity": sev,
            "category": cat, "angle": "A", "round": 1, "origin": "sweep",
            "status": "verified", "dup_of": None, "votes": [],
            "verdict": "CONFIRMED", "evidence": "quoted line",
            "severity_final": sev, "kept": True, "drop_reason": None,
            "stale": False, "rank": None}


def _state(mode="standard", candidates=None, **scope_extra) -> State:
    params = config.mode_params(mode)
    sc = {"diff_files": ["a.py"], "untracked": [], "scope_command": "git diff HEAD",
          "stats": {"files": 1, "insertions": 1, "deletions": 0}}
    sc.update(scope_extra)
    return State(mode=mode, scope=sc, cursor={"round": 1, "round_kind": "sweep", "awaiting": ""},
                 sweep_plan={"cap": params["cap"], "keep": params["keep"]},
                 candidates=candidates or [],
                 accounting={"rounds": 1, "total_seen": 0, "dup_dropped": 0, "refuted": 0,
                             "below_floor": 0, "kept_after_thresholds": 0, "capped_out": 0,
                             "round_history": []},
                 carrier={"carrier": 3, "reason": "无子代理工具"})


DETAILS = {"brief_summary": "改动 X", "coverage": "", "open_questions": "无",
           "residual_risks": "r", "testing_gaps": "t",
           "summary_bullets": ["b1", "b2", "b3"], "details": []}


def test_排序_严重度类别置信度():
    cands = [
        _kept("c1", "P2", "correctness", 0.9),
        _kept("c2", "P1", "cleanup", 0.6),
        _kept("c3", "P1", "security", 0.7),
        _kept("c4", "P1", "security", 0.95),
        _kept("c5", "P0", "conventions", 0.5),
    ]
    ranked = report.rank_findings(cands)
    assert [c["id"] for c in ranked] == ["c5", "c4", "c3", "c2", "c1"]


def test_cap截断与账目():
    cands = [_kept(f"c{i}", "P2", "correctness", 0.9) for i in range(1, 16)]  # 15 条 kept, cap 10
    st = _state(candidates=cands)
    text = report.render(st, DETAILS)
    assert st.accounting["capped_out"] == 5
    assert "capped-out=5" in text
    assert "summary c10" in text and "summary c11" not in text
    assert "另有 5 条" in text


def test_账目行取自台账():
    st = _state()
    st.accounting.update(total_seen=43, dup_dropped=28, refuted=5)
    text = report.render(st, DETAILS)
    assert "candidates=43 · dupDropped=28 · refuted=5" in text
    assert "carrier 3 — 无子代理工具" in text


def test_no_findings契约():
    text = report.render(_state(candidates=[]), DETAILS)
    assert "No significant issues found in" in text
    assert "Residual risks: r" in text and "Testing gaps: t" in text
    assert "### Summary" in text


def test_repo模式coverage强制():
    st = _state(repo_mode=True)
    det = dict(DETAILS, coverage="")
    errs = report.validate_details_file(det, st)
    assert any("coverage" in e for e in errs)
    det2 = dict(DETAILS, coverage="deep-read A · sampled B")
    assert not any("coverage" in e for e in report.validate_details_file(det2, st))
    text = report.render(st, det2)
    assert "Coverage: deep-read A · sampled B" in text


def test_details缺入榜者拒收():
    cands = [_kept("c1", "P1", "correctness", 0.9)]
    st = _state(candidates=cands)
    errs = report.validate_details_file(DETAILS, st)  # details 为空
    assert any("缺少 details" in e for e in errs)
    det = dict(DETAILS, details=[{"id": "c1", "title": "t", "problem": "p",
                                  "failure_scenario": "f", "fix": "x"}])
    assert report.validate_details_file(det, st) == []


def test_summary_bullets数量契约():
    bad = dict(DETAILS, summary_bullets=["only one"])
    assert any("summary_bullets" in e for e in report.validate_details_file(bad, _state()))


def test_fix_outcomes进报告():
    cands = [_kept("c1", "P1", "correctness", 0.9)]
    st = _state(candidates=cands)
    st.fix = {"outcomes": [{"id": "c1", "outcome": "fixed", "reason": "",
                            "gate_results": "make check ✓"}]}
    text = report.render(st, DETAILS)
    assert "**Fix outcome:** fixed" in text and "make check ✓" in text


def test_stale标记可见():
    cands = [_kept("c1", "P1", "correctness", 0.9)]
    cands[0]["stale"] = True
    text = report.render(_state(candidates=cands), DETAILS)
    assert "⚠ stale" in text
