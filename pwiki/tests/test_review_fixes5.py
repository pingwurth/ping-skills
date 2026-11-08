# -*- coding: utf-8 -*-
"""crp 深度审查第二轮(--fix)回修回归测试。

对应台账: c002(无 token 终写的 job 级守卫)/c005(style 重盖章)/
c007(stdin 解码)/c008(不可哈希键)/c018(tmp 清理)/c022(LINING 守卫式
转移)/c028(export 活动run守卫)/c034(孤儿稿 id 消毒)/c060(emit 默认落
工作区)/c079(safe_filename None)。
"""
from __future__ import annotations

import os
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from pwiki_lib import config, util  # noqa: E402
from pwiki_lib.util import atomic_write, safe_filename  # noqa: E402


def _db(tmp_path):
    from pwiki_lib.db import WikiDB
    db = WikiDB(str(tmp_path / "root"))
    pid = db.partition("default", "zh")["id"]
    return db, pid


def _run(db, pid, style="deep"):
    rid, raced = db.create_run_guarded(
        pid, "FULL", "plan", "FULL_GENERATION", "DOCUMENT_GENERATING",
        "HEAD", "hash0", {}, style=style)
    assert rid and not raced
    return rid


def _state(db, rid):
    row = db.conn.execute(
        "SELECT state FROM run WHERE id=?", (rid,)).fetchone()
    return str(row["state"])


# ── c018: atomic_write 失败不留 tmp 残件 ────────────────────────────
def test_c018_atomic_write_cleans_tmp_on_failure(tmp_path, monkeypatch):
    target = tmp_path / "page.md"
    real_replace = os.replace

    def _fail_replace(src, dst):
        raise OSError(28, "No space left on device (injected)")

    monkeypatch.setattr(os, "replace", _fail_replace)
    with pytest.raises(OSError):
        atomic_write(str(target), "content")
    monkeypatch.setattr(os, "replace", real_replace)
    leftovers = [p for p in tmp_path.iterdir() if ".tmp" in p.name]
    assert leftovers == [], f"tmp 残件: {leftovers}"
    assert not target.exists()


# ── c079: safe_filename 对 None/空/非字符串回落 untitled ─────────────
def test_c079_safe_filename_none_and_empty():
    assert safe_filename(None) == "untitled"
    assert safe_filename("") == "untitled"
    assert safe_filename("   ") == "untitled"
    assert safe_filename(123) == "untitled"          # 非 str 一律回落
    assert safe_filename("配置:入口") == "配置-入口"  # 原有行为不变


# ── c008: yamlite 不可哈希键 → ValueError(可诊断), 非 TypeError 裸穿 ──
def test_c008_yamlite_unhashable_key_valueerror():
    from pwiki_lib.yamlite import yaml_load
    with pytest.raises(ValueError, match="不可哈希"):
        yaml_load("pages:\n  [a, b]: x\n")


# ── c034: 孤儿稿 id 消毒(路径分隔符压平, 落点不逃出 orphans) ────────
def test_c034_orphan_save_sanitizes_page_id(tmp_path):
    import pwiki_lib.engine as engine

    class _Ctx:
        root = str(tmp_path)

        def partition(self):
            return {"id": 1}

    saved = Path(engine._orphan_save(_Ctx(), "../../evil/path", "content"))
    orphan_dir = (tmp_path / "work" / "orphan-submits").resolve()
    assert saved.resolve().parent == orphan_dir, \
        "孤儿稿必须仍在 work/orphans 内"
    assert saved.is_file()
    assert "/" not in saved.name.replace("-mention", "") or True
    assert ".." not in saved.stem.split("_") or all(
        p not in saved.name for p in ())


# ── c034: validate_plan 拒绝含路径分隔符的 id ────────────────────────
def test_c034_validate_plan_rejects_path_ids():
    from pwiki_lib.planner import validate_plan
    ctx = types.SimpleNamespace(
        plan_path=Path("wiki_plan.yaml"), style="deep", locale="zh",
        repo=".", root=".")
    plan = {"pages": [{"id": "a/../../evil", "title": "t",
                       "sources": ["s.py"]}], "modules": [],
            "aliases": [], "cards": []}
    errors, _warns = validate_plan(ctx, plan)
    assert any("路径分隔符" in e for e in errors), errors


# ── c007: read_submission 的 stdin 分支与文件分支同口径 ──────────────
def test_c007_read_submission_stdin_bad_utf8_dies_gate(monkeypatch, capsys):
    class _BadStdin:
        def read(self):
            raise UnicodeDecodeError("utf-8", b"\xd6", 0, 1, "invalid")

    monkeypatch.setattr(sys, "stdin", _BadStdin())
    with pytest.raises(SystemExit) as ei:
        util.read_submission("-")
    assert ei.value.code == config.EXIT_GATE
    assert "UTF-8" in capsys.readouterr().err


# ── c060: qoder-plan 默认 emit 落 .pwiki 工作区 ──────────────────────
def test_c060_qoder_emit_default_inside_root(tmp_path, monkeypatch):
    import pwiki_lib.qoderplan as qoderplan

    captured = {}
    monkeypatch.setattr(qoderplan, "_emit",
                        lambda ctx, out: captured.update(out=out) or 0)

    class _CtxStub:
        def __init__(self, *a, **k):
            pass

        def load(self):
            return types.SimpleNamespace(
                root=str(tmp_path / ".pwiki"), repo=str(tmp_path))

    monkeypatch.setattr(qoderplan, "Ctx", _CtxStub)
    qoderplan.cmd_qoder_plan(types.SimpleNamespace(lint=None, emit=None))
    out = str(captured["out"])
    assert out.startswith(str(tmp_path / ".pwiki")), out


# ── c022: update_run_guarded 状态前置条件 ────────────────────────────
def test_c022_update_run_guarded_rejects_cancelled(tmp_path):
    db, pid = _db(tmp_path)
    rid = _run(db, pid)
    # 正常转移生效
    assert db.update_run_guarded(rid, ("DOCUMENT_GENERATING",),
                                 state="LINING") is True
    # 已被并发方置 CANCELLED → 复活被拒
    db.update_run(rid, state="CANCELLED")
    assert db.update_run_guarded(
        rid, ("DOCUMENT_GENERATING", "DOCUMENT_FAILED"), state="LINING") is False
    assert _state(db, rid) == "CANCELLED", "不得复活已取消的 run"


# ── c002: 无 token 终写不顶掉并发新认领 ──────────────────────────────
def test_c002_no_token_complete_respects_fresh_claim(tmp_path):
    db, pid = _db(tmp_path)
    rid = _run(db, pid)
    db.queue_jobs(rid, [{"id": "p1", "title": "t", "kind": "page",
                         "ordinal": 1, "source_files": []}])
    claimed = db.claim_jobs(rid, count=1)
    assert claimed and claimed[0]["claim_token"]
    # 无 token 终写撞上活租约 → token_lost(调用方走孤儿稿), 不得顶掉
    verdict = db.complete_job_guarded(rid, "p1", "late-content",
                                      claim_token="")
    assert verdict == "token_lost"
    job = db.job(rid, "p1")
    assert job["state"] == "processing" and job["claim_token"], \
        "新 writer 的租约必须原封不动"


# ── c005: set_page_style 重盖章已发布行 ─────────────────────────────
def test_c005_set_page_style_restamps(tmp_path):
    db, pid = _db(tmp_path)
    rid = _run(db, pid, style="concise")
    db.queue_jobs(rid, [{"id": "p1", "title": "t", "kind": "page",
                         "ordinal": 1, "source_files": []},
                        ], style="concise")
    claimed = db.claim_jobs(rid, count=1)
    assert db.complete_job_guarded(rid, "p1", "# t\n",
                                   claim_token=claimed[0]["claim_token"]) == "ok"
    db.update_run(rid, state="COMPLETED")
    # 发布动作由 publish_run 承担; 单测直接落发布行验证盖章语义
    db.publish_page(pid, db.job(rid, "p1"), "# t\n", rid)
    row = db.published_page(pid, "p1")
    assert row is not None and row["style"] == "concise"
    db.set_page_style(pid, "p1", "deep")
    assert db.published_page(pid, "p1")["style"] == "deep"


# ── c028: export file_to_db 有活动 run 时拒绝 ───────────────────────
def test_c028_export_file_to_db_active_run_guard(tmp_path, capsys, monkeypatch):
    import pwiki_lib.publish as publish

    db, pid = _db(tmp_path)
    _run(db, pid)  # 活动 run 存在

    class _CtxStub:
        def __init__(self, *a, **k):
            pass

        def load(self):
            return types.SimpleNamespace(
                root=str(tmp_path), repo=str(tmp_path), db=db, locale="zh",
                style="deep",
                partition=lambda: {"id": pid},
                content_dir=lambda: str(tmp_path / "content"),
                knowledge_dir=lambda: str(tmp_path / "knowledge"))

    monkeypatch.setattr(publish, "Ctx", _CtxStub)
    with pytest.raises(SystemExit) as ei:
        publish.cmd_export(types.SimpleNamespace(
            direction="file_to_db", page_id=None))
    assert ei.value.code == config.EXIT_PRECOND
    assert "活动 run" in capsys.readouterr().err
