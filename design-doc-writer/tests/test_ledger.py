"""台账基建：init 建档 / 恢复模式 / 原子写 / 损坏与手编拒绝。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from conftest import read_ledger, write_ledger


def test_init_creates_ledger_and_defaults(dd, repo):
    code, out = dd("init", "--feature", "order-export")
    assert code == 0
    led = read_ledger(repo)
    assert led["phase"] == "intake"
    assert led["project"]["feature"] == "order-export"
    assert led["docs"]["analysis"]["path"] == "docs/design/order-export/requirements-analysis.md"
    assert led["docs"]["design"]["path"] == "docs/design/order-export/design.md"
    assert led["adrs_dir"] == "docs/adr"
    assert len(led["manifest"]["sections"]) == 16  # 6 analysis + 10 design
    # shape=api 预置：ui 停用，cache/release 默认停用
    sec = {s["id"]: s["enabled"] for s in led["manifest"]["sections"]}
    assert sec["ui"] is False and sec["cache"] is False and sec["release"] is False
    assert sec["overview"] is True and sec["tdd"] is True
    assert led["history"][0]["event"] == "init"
    assert out.rstrip().splitlines()[-1].startswith("next_step: ")
    # 日志已建立
    assert (repo / ".agent" / "design-doc-writer" / "dd.log").is_file()


def test_init_shape_web_enables_ui_cache(dd, repo):
    assert dd("init", "--feature", "f", "--shape", "web")[0] == 0
    sec = {s["id"]: s["enabled"] for s in read_ledger(repo)["manifest"]["sections"]}
    assert sec["ui"] is True and sec["cache"] is True and sec["release"] is False


def test_init_rejects_bad_feature(dd, repo):
    code, out = dd("init", "--feature", "a b/c")
    assert code == 2
    assert "错误" in out


def test_init_resume_never_resets(dd, repo):
    dd("init", "--feature", "order-export")
    before = (repo / ".agent" / "design-doc-writer" / "ledger.json").read_text(encoding="utf-8")
    code, out = dd("init", "--feature", "another-feature")  # 不同 feature 也不重置
    assert code == 0
    after = (repo / ".agent" / "design-doc-writer" / "ledger.json").read_text(encoding="utf-8")
    assert before == after
    assert "恢复模式" in out


def test_init_warns_when_agent_not_ignored(dd, tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".gitignore").write_text("node_modules\n", encoding="utf-8")
    code, out = dd("init", "--feature", "f", "--repo", tmp_path)
    assert code == 0
    assert "提醒" in out and ".gitignore" in out


def test_status_without_ledger_exit2(dd, repo):
    code, out = dd("status")
    assert code == 2
    assert "错误" in out and "init" in out
    assert out.rstrip().splitlines()[-1].startswith("next_step: ")


def test_corrupt_ledger_exit2(dd, booted):
    (booted / ".agent" / "design-doc-writer" / "ledger.json").write_text("{oops", encoding="utf-8")
    code, out = dd("status")
    assert code == 2
    assert "损坏" in out


def test_hand_edited_version_exit2(dd, booted):
    led = read_ledger(booted)
    led["version"] = 99
    write_ledger(booted, led)
    code, out = dd("status")
    assert code == 2
    assert "禁止手编" in out or "version" in out


def test_missing_fields_exit2(dd, booted):
    led = read_ledger(booted)
    del led["docs"]
    write_ledger(booted, led)
    assert dd("status")[0] == 2


def test_atomic_write_survives_serialize_error(booted, monkeypatch):
    """序列化抛异常时原文件完好、无 .tmp 残留。"""
    import dd as ddmod  # noqa: N813 — scripts/dd.py

    lp = booted / ".agent" / "design-doc-writer" / "ledger.json"
    before = lp.read_text(encoding="utf-8")
    ledger = ddmod.load_ledger(booted)
    ledger["phase"] = "analyzing"

    def boom(*a, **k):
        raise RuntimeError("disk full simulation")

    monkeypatch.setattr(ddmod.json, "dump", boom)
    with pytest.raises(RuntimeError):
        ddmod.save_ledger(booted, ledger)
    assert lp.read_text(encoding="utf-8") == before
    assert not list((lp.parent).glob("ledger.json.tmp-*"))


def test_imports_are_stdlib_only():
    """纯 stdlib 约束：AST 解析 dd.py 的顶层 import，全部落在 stdlib。"""
    import ast

    import dd as ddmod  # noqa: N813
    src = Path(ddmod.__file__).read_text(encoding="utf-8")
    mods: set[str] = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            mods.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            mods.add(node.module.split(".")[0])
    nonstd = {m for m in mods if m not in set(sys.stdlib_module_names)}
    assert not nonstd, f"非 stdlib 依赖：{nonstd}"
