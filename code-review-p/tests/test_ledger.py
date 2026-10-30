"""台账去重(与 JS 奇偶校验夹具共享)、校验拒收、入账计数、幂等。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from crp import config, ledger
from crp.models import State
from gitrepo import RepoBuilder

FIXTURES = Path(__file__).parent / "fixtures"
CASES = json.loads((FIXTURES / "dedup_cases.json").read_text(encoding="utf-8"))


# ── 与 JS 共享夹具的奇偶校验(逐用例) ─────────────────────────────────
@pytest.mark.parametrize("case", CASES["dup_cases"], ids=[c["name"] for c in CASES["dup_cases"]])
def test_去重奇偶校验(case):
    seen = case["seen"]
    cand = case["cand"]
    hit = ledger.find_dup(seen, cand)
    assert (hit is not None) == case["dup"], case["name"]


@pytest.mark.parametrize("case", CASES["norm_cases"], ids=[c["f"] for c in CASES["norm_cases"]])
def test_路径归一奇偶校验(case):
    assert ledger.norm_file(case["f"], case["root"]) == case["expected"]


# ── 大小写/边界 ──────────────────────────────────────────────────────
def test_摘要归一保留字母数字剥离标点():
    # 连续标点折叠为单空格(JS 的 [^\p{L}\p{N}]+ 语义), 下划线剥离
    assert ledger.norm_summary("Missing await!! on fetch_User") == "missing await on fetch user"
    assert ledger.norm_summary("令牌比较,NaN通过。") == "令牌比较 nan通过"


def test_token包含cjk_bigram():
    toks = ledger.tokens_of("令牌比较先于过期解析")
    assert "令牌" in toks and "解析" in toks  # bigram 入
    assert "解" not in toks and "析" not in toks  # 单字不入


def test_候选校验_严重度逃逸与超长拒收():
    base = {"file": "a.py", "line": 1, "summary": "s", "failure_scenario": "f",
            "confidence": 0.5, "severity": "P1", "category": "correctness"}
    assert ledger.validate_candidate(base, "c") == []
    bad = dict(base, severity="P4")
    assert any("severity" in e for e in ledger.validate_candidate(bad, "c"))
    bad = dict(base, confidence=1.5)
    assert any("confidence" in e for e in ledger.validate_candidate(bad, "c"))
    bad = dict(base, failure_scenario="")
    assert any("failure_scenario" in e for e in ledger.validate_candidate(bad, "c"))
    bad = dict(base, summary="x" * (config.MAX_SUMMARY_CHARS + 1))
    assert any("summary" in e for e in ledger.validate_candidate(bad, "c"))
    bad = dict(base, line="42")
    assert any("line" in e for e in ledger.validate_candidate(bad, "c"))


def test_入账_去重计数与coverage(tmp_path):
    repo = RepoBuilder.init(tmp_path)
    repo.commit({"a.py": "1\n"}, "init")
    state = State(repo={"cwd": str(repo.root), "slug": "x", "head": "h", "is_git": True},
                  mode="standard", sweep_plan={"keep": config.KEEP_CONFIRMED_P01})
    data = {"items": [{"angle": "C", "coverage": {"ran": True}, "candidates": [
        {"file": str(repo.root / "a.py"), "line": 10, "summary": "Missing await on fetchUser before reading result",
         "failure_scenario": "f", "confidence": 0.9, "severity": "P1", "category": "correctness"},
        {"file": "a.py", "line": 12, "summary": "Missing await on fetchUser before reading result",
         "failure_scenario": "f", "confidence": 0.9, "severity": "P1", "category": "correctness"},
    ]}]}
    result = ledger.ingest_candidates(state, data, origin="sweep", round_no=1)
    assert len(result.fresh_ids) == 1 and result.dup_count == 1  # 绝对路径已归一并判重
    assert state.accounting["total_seen"] == 2  # 台账含 dup 条目(曾见一切)
    cov = state.coverage["angles"]["C"]
    assert cov == {"ran": True, "raw": 2, "fresh": 1, "dropped": 0}
    # dup 条目带 dup_of 指回原条目
    dup_entry = next(c for c in state.candidates if c["status"] == "dup")
    assert dup_entry["dup_of"] == result.fresh_ids[0]


def test_入账_单角度超8计dropped():
    state = State(mode="quick")
    cands = [{"file": f"f{i}.py", "line": i, "summary": f"defect {i} unique summary {i}",
              "failure_scenario": "f", "confidence": 0.9, "severity": "P2",
              "category": "correctness"} for i in range(10)]
    result = ledger.ingest_candidates(state, {"items": [{"angle": "A", "candidates": cands}]},
                                      origin="sweep", round_no=1)
    assert result.dropped_over_cap == 2
    assert state.coverage["angles"]["A"]["raw"] == config.MAX_CANDIDATES_PER_ANGLE


def test_轮内多提交累计round_history():
    state = State(mode="standard")
    batch = lambda n: {"items": [{"angle": "A", "candidates": [
        {"file": f"x{n}.py", "line": 1, "summary": f"unique defect number {n}",
         "failure_scenario": "f", "confidence": 0.9, "severity": "P2",
         "category": "correctness"}]}]}
    ledger.ingest_candidates(state, batch(1), origin="sweep", round_no=1)
    ledger.ingest_candidates(state, batch(2), origin="sweep", round_no=1)
    hist = state.accounting["round_history"]
    assert len(hist) == 1 and hist[0]["fresh"] == 2
