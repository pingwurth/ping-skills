"""confirm：门禁前置 / Draft→Confirmed / 状态行同步 / 双确认链。"""
from __future__ import annotations

from conftest import make_analysis, make_design, read_ledger, write_doc


def test_confirm_rejected_when_gate_fails(dd, grilling):
    code, out = dd("confirm", "--doc", "analysis", "--because", "用户说好")
    assert code == 1  # 文档不存在/问题未清 → 门禁未过
    assert "保持 draft" in out
    led = read_ledger(grilling)
    assert led["docs"]["analysis"]["status"] == "draft"


def test_confirm_rejected_when_questions_open(dd, grilling):
    write_doc(grilling, make_analysis())
    code, out = dd("confirm", "--doc", "analysis", "--because", "x")
    assert code == 1 and "open-questions" in out


def test_confirm_success_records_and_syncs_status_line(dd, grilling):
    assert dd("q-answer", "Q001", "--answer", "最小集")[0] == 0
    assert dd("q-answer", "Q002", "--answer", "异步")[0] == 0
    p = write_doc(grilling, make_analysis())
    code, out = dd("confirm", "--doc", "analysis", "--because", "用户：按这个做")
    assert code == 0, out
    led = read_ledger(grilling)
    d = led["docs"]["analysis"]
    assert d["status"] == "confirmed" and d["because"] == "用户：按这个做" and d["confirmed_at"]
    assert any(h["event"] == "confirm" and "用户：按这个做" in h["detail"] for h in led["history"])
    # 文档状态行被脚本同步
    assert "状态: Confirmed" in p.read_text(encoding="utf-8")
    # 同步后复检不再报状态行漂移
    assert dd("check", "--doc", "analysis")[0] == 0


def test_confirm_design_requires_analysis_confirmed(dd, grilling):
    write_doc(grilling, make_design(), analysis=False)
    code, out = dd("confirm", "--doc", "design", "--because", "x")
    assert code == 1 and "design-prereq" in out


def test_full_chain_confirm_design_then_finish(dd, grilling):
    assert dd("q-answer", "Q001", "--answer", "最小集")[0] == 0
    assert dd("q-answer", "Q002", "--answer", "异步")[0] == 0
    write_doc(grilling, make_analysis())
    assert dd("confirm", "--doc", "analysis", "--because", "a1")[0] == 0
    assert dd("phase", "design-doc")[0] == 0
    write_doc(grilling, make_design(), analysis=False)
    assert dd("confirm", "--doc", "design", "--because", "d1")[0] == 0
    code, out = dd("finish")
    assert code == 0, out
    led = read_ledger(grilling)
    assert led["phase"] == "done"
    assert "交付摘要" in out


def test_finish_before_double_confirm(dd, grilling):
    code, out = dd("finish")
    assert code == 1 and "analysis" in out and "design" in out
