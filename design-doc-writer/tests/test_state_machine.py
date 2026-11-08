"""状态机：q-add / q-answer / phase 的合法与非法流转。"""
from __future__ import annotations

import pytest

from conftest import read_ledger


# ---------- q-add ----------

def test_q_add_rejected_at_intake(dd, booted):
    code, out = dd("q-add", "--dimension", "scope", "--title", "t",
                   "--body", "b", "--recommendation", "r")
    assert code == 2
    assert "intake" in out


def test_q_add_auto_advances_to_grilling(dd, booted):
    assert dd("phase", "analyzing")[0] == 0
    code, out = dd("q-add", "--dimension", "goal", "--title", "真实目标？",
                   "--body", "b", "--recommendation", "r")
    assert code == 0
    led = read_ledger(booted)
    assert led["phase"] == "grilling"
    assert led["questions"][0]["id"] == "Q001"
    assert led["questions"][0]["round"] == 1
    assert any("自动推进" in h["detail"] for h in led["history"])


def test_q_add_assigns_sequential_ids_and_round(dd, grilling):
    led = read_ledger(grilling)
    assert [x["id"] for x in led["questions"]] == ["Q001", "Q002"]
    assert led["questions"][1]["round"] == 2  # 1 + max(dep.round)


def test_q_add_rejects_unknown_dimension(dd, grilling):
    code, out = dd("q-add", "--dimension", "bogus", "--title", "t",
                   "--body", "b", "--recommendation", "r")
    assert code == 2 and "dimension" in out


def test_q_add_rejects_missing_recommendation(dd, grilling):
    code, out = dd("q-add", "--dimension", "scope", "--title", "t", "--body", "b")
    assert code == 2 and "recommendation" in out


def test_q_add_rejects_unknown_dep(dd, grilling):
    code, out = dd("q-add", "--dimension", "scope", "--title", "t",
                   "--body", "b", "--recommendation", "r", "--depends-on", "Q999")
    assert code == 2 and "Q999" in out


def test_q_add_batch_respects_order(dd, grilling, tmp_path):
    batch = tmp_path / "questions.json"
    batch.write_text(
        '[{"dimension":"api","title":"接口形态","body":"b","recommendation":"REST"},'
        '{"dimension":"compat","title":"兼容策略","body":"b","recommendation":"双写","depends_on":["Q003"]}]',
        encoding="utf-8")
    code, out = dd("q-add", "--batch", batch)
    assert code == 0
    led = read_ledger(grilling)
    assert [x["id"] for x in led["questions"]] == ["Q001", "Q002", "Q003", "Q004"]
    assert led["questions"][3]["depends_on"] == ["Q003"]


def test_assert_acyclic_rejects_cycle():
    import dd as ddmod
    ledger = {"questions": [
        {"id": "Q001", "depends_on": ["Q002"]},
        {"id": "Q002", "depends_on": ["Q001"]},
    ]}
    with pytest.raises(ddmod.DDError, match="成环"):
        ddmod._assert_acyclic(ledger)


# ---------- q-answer ----------

def test_q_answer_decision(dd, grilling):
    code, out = dd("q-answer", "Q001", "--answer", "最小集：只做订单导出")
    assert code == 0
    q = read_ledger(grilling)["questions"][0]
    assert q["status"] == "answered" and q["answer"] == "最小集：只做订单导出"
    assert q["answered_at"]


def test_q_answer_reanswer_records_history(dd, grilling):
    dd("q-answer", "Q001", "--answer", "第一版决策")
    dd("q-answer", "Q001", "--answer", "改主意了")
    q = read_ledger(grilling)["questions"][0]
    assert q["answer"] == "改主意了"
    assert any("重答" in h["detail"] for h in read_ledger(grilling)["history"])


def test_q_answer_defer_with_reason(dd, grilling):
    code, out = dd("q-answer", "Q001", "--defer", "--reason", "等上游排期")
    assert code == 0
    q = read_ledger(grilling)["questions"][0]
    assert q["status"] == "deferred" and q["answer"] is None


def test_q_answer_defer_requires_reason(dd, grilling):
    code, out = dd("q-answer", "Q001", "--defer")
    assert code == 2 and "--reason" in out


def test_q_answer_unknown_id(dd, grilling):
    code, out = dd("q-answer", "Q999", "--answer", "x")
    assert code == 2 and "Q999" in out


def test_q_answer_rejected_before_grilling(dd, booted):
    assert dd("phase", "analyzing")[0] == 0
    code, out = dd("q-answer", "Q001", "--answer", "x")  # 尚无问题且阶段过早
    assert code == 2


def test_q_answer_adr_assigns_numbered_path(dd, grilling):
    code, out = dd("q-answer", "Q001", "--answer", "异步", "--adr", "--adr-slug", "async-job")
    assert code == 0
    q = read_ledger(grilling)["questions"][0]
    assert q["adr"]["path"] == "docs/adr/0001-async-job.md"
    assert "ADR 分配" in out or "docs/adr/0001" in out


def test_q_answer_adr_numbering_scans_existing(dd, grilling):
    (grilling / "docs" / "adr").mkdir(parents=True)
    (grilling / "docs" / "adr" / "0003-x.md").write_text("# x\n", encoding="utf-8")
    dd("q-answer", "Q001", "--answer", "d1")
    dd("q-answer", "Q002", "--answer", "d2", "--adr", "--adr-slug", "next")
    qs = read_ledger(grilling)["questions"]
    assert qs[1]["adr"]["path"] == "docs/adr/0004-next.md"


def test_q_answer_adr_slug_validation(dd, grilling):
    code, out = dd("q-answer", "Q001", "--answer", "x", "--adr", "--adr-slug", "Bad Slug")
    assert code == 2 and "slug" in out
    code, out = dd("q-answer", "Q001", "--defer", "--reason", "r", "--adr", "--adr-slug", "ok")
    assert code == 2 and "--answer" in out  # --adr 只能搭配 --answer


# ---------- phase ----------

def test_phase_rejects_unknown_and_illegal_edges(dd, booted):
    assert dd("phase", "bogus")[0] == 2
    assert dd("phase", "grilling")[0] == 2      # 跳级
    assert dd("phase", "design-doc")[0] == 2    # 跳级
    dd("phase", "analyzing")
    assert dd("phase", "analysis-doc")[0] == 2  # 跳过 grilling
    assert dd("phase", "done")[0] == 2          # done 只能经 finish


def test_phase_noop_same_phase(dd, booted):
    code, out = dd("phase", "intake")
    assert code == 0 and "no-op" in out


def test_phase_happy_chain(dd, grilling):
    assert dd("phase", "analysis-doc")[0] == 0


def test_phase_design_doc_blocked_until_analysis_confirmed(dd, grilling):
    dd("phase", "analysis-doc")
    code, out = dd("phase", "design-doc")
    assert code == 2 and "confirmed" in out


def test_phase_backflow_edges(dd, grilling):
    # grilling → analyzing（补充调研）
    assert dd("phase", "analyzing")[0] == 0
    assert dd("phase", "grilling")[0] == 0


def test_phase_done_is_terminal(dd, grilling):
    led = read_ledger(grilling)
    led["phase"] = "done"
    from conftest import write_ledger
    write_ledger(grilling, led)
    assert dd("phase", "grilling")[0] == 2


# ---------- manifest ----------

def test_manifest_enable_disable_with_reason(dd, booted):
    code, out = dd("manifest", "--enable", "cache,release", "--disable", "ui",
                   "--reason", "web 项目补缓存与灰度")
    assert code == 0
    sec = {s["id"]: s["enabled"] for s in read_ledger(booted)["manifest"]["sections"]}
    assert sec["cache"] and sec["release"] and not sec["ui"]
    led = read_ledger(booted)
    assert led["manifest"]["reason"] == "web 项目补缓存与灰度"


def test_manifest_requires_reason_and_known_ids(dd, booted):
    assert dd("manifest", "--enable", "cache")[0] == 2          # 缺 --reason
    assert dd("manifest", "--enable", "bogus", "--reason", "r")[0] == 2
