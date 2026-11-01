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


# ── P4 回修回归: 渲染(2026-11-02 全面审查) ───────────────────────────
def test_p4_表格转义竖线与换行():
    """P4 回归: summary 原样插进 markdown 表行 —— 'if (a || b) inverted' 使
    4 列表出现 5 列, 换行直接拆行; 该表是协议要求逐字转述的引擎产物。"""
    cand = _kept("c1", "P1", "correctness", 0.9)
    cand["summary"] = "if (a || b) inverted\nguard split across lines"
    st = _state(candidates=[cand])
    text = report.render(st, dict(DETAILS, details=[{"id": "c1", "title": "t",
                                                     "problem": "p", "failure_scenario": "f",
                                                     "fix": "x"}]))
    row = next(l for l in text.splitlines() if l.startswith("| 1 |"))
    # 未转义的竖线恰 5 个(4 列); summary 内的 | 已转义为 \| 不破坏列数
    assert row.replace("\\|", "").count("|") == 5, "4 列表恰 5 个未转义分隔竖线"
    assert "inverted" in row and "guard split across lines" in row  # 换行归一为空格
    assert "\\|" in row, "summary 内的 | 必须转义"


def test_p4_parity注记上报告():
    """P4 回归: parity_notes 只写 state.accounting、渲染从不读 —— '分歧记入
    报告 parity 注记(可见, 不静默)' 的承诺落空, 只能翻 ledger.json 才看得见。"""
    st = _state(candidates=[_kept("c1", "P1", "correctness", 0.9)])
    st.accounting["parity_notes"] = ["原始候选总数不一致: js=8 py=10(人工查 workflow)"]
    text = report.render(st, dict(DETAILS))
    assert "parity: 原始候选总数不一致" in text
    assert text.index("parity:") < text.index("Run:") + 200, "注记应在账目行附近可见"


def test_p4_零发现报告含open_questions():
    """P4 回归: 零发现分支提前返回不输出 Open questions 节 —— 未决疑点恰好在
    最需要 Caveat 的场景全部丢失(与有发现路径不对称)。"""
    st = _state(candidates=[])
    text = report.render(st, dict(DETAILS, open_questions="门禁 make check 被拒, 无法验证 X"))
    assert "### Open questions / assumptions" in text
    assert "无法验证 X" in text
