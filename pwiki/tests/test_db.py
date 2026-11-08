"""db 层: 认领原子性(头条并发修复)、租约回收、失配孤儿路径、v3 表。"""

from __future__ import annotations

import threading

import pytest

from pwiki_lib import config, db


@pytest.fixture()
def wdb(tmp_path):
    conn = db.WikiDB(str(tmp_path / "root"))
    yield conn
    conn.close()


def _mk_items(n=5, kind="page"):
    return [{"id": f"p{i}", "title": f"页{i}", "parent": "" if i == 0 else "p0",
             "kind": kind, "ordinal": i} for i in range(n)]


def test_schema_v3_tables_present(wdb):
    tables = {r[0] for r in wdb.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    for t in ("db_meta", "partition", "run", "run_log", "page_job", "page",
              "module", "file_hash", "glossary", "reconcile"):
        assert t in tables
    assert wdb.meta_get("schema_version") == db.SCHEMA_VERSION


def test_v3_columns_present(wdb):
    cols = {r[1] for r in wdb.conn.execute("PRAGMA table_info(page_job)")}
    for c in ("style", "scope", "required_sections", "title_check", "pack_key",
              "report", "kind_rank", "depth", "claim_token", "claimed_at",
              "lease_expires_at"):
        assert c in cols


def test_queue_jobs_precomputes_rank_depth(wdb, tmp_path):
    pid = wdb.partition("main", "zh")["id"]
    run_id = wdb.create_run(pid, "generate", "start", "FULL",
                            "DOCUMENT_GENERATING", "c" * 40, "h" * 8, {}, "deep")
    items = ([{"id": "sheet-src", "kind": "module_sheet", "title": "概述", "ordinal": 0}] +
             [{"id": "card-x", "kind": "card", "title": "lodash", "ordinal": 0}] +
             _mk_items(4))
    wdb.queue_jobs(run_id, items, style="deep")
    job = wdb.job(run_id, "sheet-src")
    assert job["kind_rank"] == 0 and job["depth"] == 0
    job = wdb.job(run_id, "card-x")
    assert job["kind_rank"] == 1
    job = wdb.job(run_id, "p0")
    assert job["kind_rank"] == 2 and job["depth"] == 0
    job = wdb.job(run_id, "p3")
    assert job["depth"] == 1 and job["style"] == "deep"


def test_claim_knowledge_first_bottom_up(wdb):
    """认领顺序: sheet → card → 页(自底向上: 叶先于父)。"""
    pid = wdb.partition("main", "zh")["id"]
    run_id = wdb.create_run(pid, "generate", "start", "FULL",
                            "DOCUMENT_GENERATING", "c" * 40, "h" * 8, {})
    items = ([{"id": "p-root", "title": "根", "parent": "", "kind": "page", "ordinal": 0},
              {"id": "p-child", "title": "子", "parent": "p-root", "kind": "page", "ordinal": 1},
              {"id": "p-grand", "title": "孙", "parent": "p-child", "kind": "page", "ordinal": 2},
              {"id": "s-1", "title": "概述", "kind": "module_sheet", "ordinal": 0},
              {"id": "c-1", "title": "卡", "kind": "card", "ordinal": 0}])
    wdb.queue_jobs(run_id, items)
    order = [row["page_id"] for row in
             (wdb.claim_jobs(run_id) + wdb.claim_jobs(run_id) + wdb.claim_jobs(run_id) +
              wdb.claim_jobs(run_id) + wdb.claim_jobs(run_id))]
    assert order == ["s-1", "c-1", "p-grand", "p-child", "p-root"]


def test_claim_atomic_no_duplicate_under_threads(wdb):
    """8 线程 × 50 认领: 无重复、无丢失(头条修复的锤击测试)。"""
    pid = wdb.partition("main", "zh")["id"]
    run_id = wdb.create_run(pid, "generate", "start", "FULL",
                            "DOCUMENT_GENERATING", "c" * 40, "h" * 8, {})
    n_jobs = 200
    items = [{"id": f"job-{i}", "title": f"j{i}", "parent": "",
              "kind": "page", "ordinal": i} for i in range(n_jobs)]
    wdb.queue_jobs(run_id, items)

    claimed = []
    lock = threading.Lock()
    errors = []

    def worker():
        try:
            conn = db.WikiDB(wdb.root)
            try:
                for _ in range(50):
                    rows = conn.claim_jobs(run_id, count=1, lease_minutes=30)
                    with lock:
                        claimed.extend(r["page_id"] for r in rows)
            finally:
                conn.close()
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    assert len(claimed) == n_jobs           # 全部认领完
    assert len(set(claimed)) == n_jobs      # 无重复
    states = dict((r["page_id"], r["state"]) for r in wdb.jobs(run_id))
    assert set(states.values()) == {"processing"}


def test_lease_expiry_reclaim(wdb):
    """租约过期的 processing 由下一次 claim 回收为 pending 再被认领。"""
    pid = wdb.partition("main", "zh")["id"]
    run_id = wdb.create_run(pid, "generate", "start", "FULL",
                            "DOCUMENT_GENERATING", "c" * 40, "h" * 8, {})
    wdb.queue_jobs(run_id, _mk_items(1))
    [row] = wdb.claim_jobs(run_id)
    # 直接把租约改到过去
    wdb.conn.execute(
        "UPDATE page_job SET lease_expires_at='2000-01-01T00:00:00.000Z' "
        "WHERE run_id=? AND page_id=?", (run_id, row["page_id"]))
    [again] = wdb.claim_jobs(run_id)
    assert again["page_id"] == row["page_id"]
    assert again["attempts"] == 2
    assert again["last_error"] == "lease_expired_reclaimed"
    assert again["claim_token"] != row["claim_token"]


def test_set_job_state_token_mismatch(wdb):
    """token 失配 → 返回 False(调用方走孤儿稿), 状态不动。"""
    pid = wdb.partition("main", "zh")["id"]
    run_id = wdb.create_run(pid, "generate", "start", "FULL",
                            "DOCUMENT_GENERATING", "c" * 40, "h" * 8, {})
    wdb.queue_jobs(run_id, _mk_items(1))
    [row] = wdb.claim_jobs(run_id)
    ok = wdb.set_job_state(run_id, row["page_id"], "completed",
                           content="正文", claim_token="wrong-token")
    assert ok is False
    assert wdb.job(run_id, row["page_id"])["state"] == "processing"
    ok = wdb.set_job_state(run_id, row["page_id"], "completed",
                           content="正文", claim_token=row["claim_token"])
    assert ok is True
    job = wdb.job(run_id, row["page_id"])
    assert job["state"] == "completed"
    assert job["content"] == "正文"
    assert job["content_hash"]
    assert job["claim_token"] == ""   # 完成即释放


def test_extend_lease_and_release_all(wdb):
    pid = wdb.partition("main", "zh")["id"]
    run_id = wdb.create_run(pid, "generate", "start", "FULL",
                            "DOCUMENT_GENERATING", "c" * 40, "h" * 8, {})
    wdb.queue_jobs(run_id, _mk_items(2))
    rows = wdb.claim_jobs(run_id, count=2)
    assert len(wdb.leased_jobs(run_id)) == 2
    # r4 #65: extend_lease 已删(submit 不顺延租约, 文档契约同口径);
    # 释放语义仍是 cancel 的关键路径
    assert wdb.release_all_claims(run_id) == 2
    assert wdb.leased_jobs(run_id) == []


def test_glossary_and_reconcile(wdb):
    pid = wdb.partition("main", "zh")["id"]
    wdb.glossary_add_terms(pid, ["BFF", "RunSnapshot", " "], source_page_id="p1")
    wdb.glossary_add_terms(pid, ["BFF"], source_page_id="p2")
    # r4 #66: glossary_set_definition 已删(无生产写入方; definition 列
    # 保留为手编 DB 的读侧)
    wdb.conn.execute(
        "INSERT INTO glossary(partition_id,term,definition,source_page_id,"
        "updated_at) VALUES(?,?,?,?,?) "
        "ON CONFLICT(partition_id,term) DO UPDATE SET definition=excluded.definition",
        (pid, "BFF", "Backend For Frontend", "", "2026-11-09T00:00:00Z"))
    terms = wdb.glossary_all(pid)
    names = [t["term"] for t in terms]
    assert names == ["BFF", "RunSnapshot"]     # 空白被拒, 按字母序
    assert terms[0]["definition"] == "Backend For Frontend"

    rid = wdb.reconcile_add(pid, "title_mismatch",
                            {"page_id": "p1", "proposed": "新标题"})
    pending = wdb.reconcile_pending(pid)
    assert len(pending) == 1 and pending[0]["kind"] == "title_mismatch"
    wdb.reconcile_resolve(rid, "applied")
    assert wdb.reconcile_pending(pid) == []


def test_active_run_document_failed_resumable(wdb):
    """DOCUMENT_FAILED 永远留在可恢复集合; 唯取消/完成才退出。"""
    pid = wdb.partition("main", "zh")["id"]
    r1 = wdb.create_run(pid, "generate", "start", "FULL", "DOCUMENT_FAILED",
                        "c" * 40, "h" * 8, {})
    wdb.update_run(r1, finished_at=config.now_iso())
    assert wdb.active_run(pid)["id"] == r1      # DOCUMENT_FAILED 可恢复
    r2 = wdb.create_run(pid, "update", "increment", "INCREMENT", "COMPLETED",
                        "c" * 40, "h" * 8, {})
    wdb.update_run(r2, finished_at=config.now_iso())
    assert wdb.active_run(pid)["id"] == r1      # r1 仍可恢复(排序取最新也轮不到 r2)
    wdb.update_run(r1, state="CANCELLED")
    assert wdb.active_run(pid) is None
