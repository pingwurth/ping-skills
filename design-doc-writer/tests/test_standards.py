"""coding-standards 参考接线 + kb-scan 规范文档探测/登记契约。"""
from __future__ import annotations

from pathlib import Path

import dd as ddmod
from conftest import read_ledger, write_ledger

SKILL_ROOT = Path(__file__).resolve().parent.parent
REF = SKILL_ROOT / "references" / "coding-standards.md"


def test_coding_standards_reference_exists_and_wired():
    """参考存在且三处契约文件都接线（防改名漂移，口径同 test_dimensions_enumeration_stable）。"""
    assert REF.is_file()
    text = REF.read_text(encoding="utf-8")
    for kw in ("优先级", "命名基线", "码值定义规范", "调研清单"):
        assert kw in text, f"coding-standards.md 缺关键节：{kw}"
    skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    assert "coding-standards.md" in skill
    assert "命名与码值合规" in skill  # 硬约束 9
    for name in ("doc-templates.md", "question-dimensions.md"):
        other = (SKILL_ROOT / "references" / name).read_text(encoding="utf-8")
        assert "coding-standards.md" in other, f"{name} 未接线 coding-standards.md"


def test_coding_standards_reference_has_no_placeholder_words():
    """参考自身不含占位符禁词（会被照抄进文档触发门禁）。"""
    text = REF.read_text(encoding="utf-8")
    hits = [p for p in ddmod.PLACEHOLDERS if p in text]
    assert not hits, f"coding-standards.md 含占位符禁词：{hits}"


def test_kb_scan_detects_standards_docs(dd, booted):
    (booted / "docs").mkdir()
    (booted / "docs" / "开发规范.md").write_text("# 规范\n", encoding="utf-8")
    (booted / "CONTRIBUTING.md").write_text("# c\n", encoding="utf-8")
    (booted / "docs" / "部署手册.md").write_text("# m\n", encoding="utf-8")  # 运维文档：不命中
    code, out = dd("kb-scan")
    assert code == 0, out
    led = read_ledger(booted)
    got = [(e["path"], e["source"]) for e in led["kb"]["standards_docs"]]
    assert got == [("CONTRIBUTING.md", "auto"), ("docs/开发规范.md", "auto")]
    assert led["kb"]["primary"] is None  # 规范文档不参与 primary 评选
    assert "规范文档" in out and "coding-standards.md" in out
    assert "部署手册" not in out


def test_kb_scan_standards_flag_registers_and_survives_rescan(dd, booted):
    (booted / "docs").mkdir()
    (booted / "docs" / "开发规范.md").write_text("# 规范\n", encoding="utf-8")
    rel = "team/sql-rule.md"
    p = booted / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("# sql\n", encoding="utf-8")
    code, out = dd("kb-scan", "--standards", rel)
    assert code == 0, out
    led = read_ledger(booted)
    got = {e["path"]: e["source"] for e in led["kb"]["standards_docs"]}
    assert got == {"docs/开发规范.md": "auto", rel: "manual"}
    assert dd("status")[1].count(rel) >= 1  # status 展示规范文档
    # 重扫不带 flag：自动项重探测、手动项保留
    code, out = dd("kb-scan")
    assert code == 0, out
    led = read_ledger(booted)
    got = {e["path"]: e["source"] for e in led["kb"]["standards_docs"]}
    assert got == {"docs/开发规范.md": "auto", rel: "manual"}


def test_kb_scan_standards_missing_path_errors(dd, booted):
    code, out = dd("kb-scan", "--standards", "nope.md")
    assert code == 2
    assert "--standards" in out


def test_ledger_without_standards_docs_still_valid(dd, grilling):
    """旧台账无 standards_docs 键：加载与校验不受影响（向后兼容）。"""
    led = read_ledger(grilling)
    led["kb"].pop("standards_docs", None)
    write_ledger(grilling, led)
    assert dd("status")[0] == 0


def _minimal_ledger() -> dict:
    return {
        "version": ddmod.LEDGER_VERSION,
        "project": {"feature": "f", "updated_at": "x"},
        "phase": "intake",
        "kb": {"detected": [], "openwiki_mcp": False, "primary": None},
        "questions": [],
        "docs": {
            "analysis": {"path": "a.md", "status": "draft", "confirmed_at": None, "because": None},
            "design": {"path": "d.md", "status": "draft", "confirmed_at": None, "because": None},
        },
        "manifest": {"sections": [], "reason": "r"},
        "adrs_dir": "docs/adr",
        "history": [],
    }


def test_validate_ledger_standards_docs_optional_but_typed():
    base = _minimal_ledger()
    assert not ddmod.validate_ledger(base)
    base["kb"]["standards_docs"] = [{"path": "docs/x.md", "source": "auto"},
                                    {"path": "/abs/手册.pdf", "source": "manual"}]
    assert not ddmod.validate_ledger(base)
    base["kb"]["standards_docs"] = [{"path": "docs/x.md", "source": "wat"}]
    assert any("standards_docs" in e for e in ddmod.validate_ledger(base))
    base["kb"]["standards_docs"] = [{"source": "auto"}]  # 缺 path
    assert any("standards_docs" in e for e in ddmod.validate_ledger(base))


def test_detect_standards_docs_direct_call(tmp_path):
    (tmp_path / "doc").mkdir()  # doc/ 别名根也探测
    (tmp_path / "doc" / "coding-standard.md").write_text("x", encoding="utf-8")
    (tmp_path / "STYLEGUIDE.md").write_text("x", encoding="utf-8")
    (tmp_path / "readme.md").write_text("x", encoding="utf-8")
    (tmp_path / "sub").mkdir()  # 深层目录不扫
    (tmp_path / "sub" / "规范.md").write_text("x", encoding="utf-8")
    got = ddmod.detect_standards_docs(tmp_path)
    assert got == ["STYLEGUIDE.md", "doc/coding-standard.md"]
