"""2026-11-09 max 审查回修回归：清理与效率（G 簇）的行为面。

G1/G2（去双读/单一 ADR 扫描）与 G7（utils 镜像模块）已有行为级覆盖
（编号分配、D6 单句柄、stdlib 测试）；此处补 G3 事实浮现与只写清理、
G4 PASS 发射、G5 组合守卫、G6 graphlib 批量路径。
"""
from __future__ import annotations

from conftest import make_analysis, read_ledger, write_doc


# ---------- G3：--facts 存得进也取得出 ----------

def test_g3_facts_surfaced_in_frontier_and_status(dd, booted):
    assert dd("phase", "analyzing")[0] == 0
    code, out = dd("q-add", "--dimension", "technical", "--title", "同步还是异步",
                   "--body", "数据量大", "--recommendation", "推荐异步",
                   "--facts", "src/jobs/ 已有 3 个同类异步任务")
    assert code == 0
    code, out = dd("frontier")
    assert code == 1 and "事实：src/jobs/ 已有 3 个同类异步任务" in out
    code, out = dd("status")
    assert code == 0 and "Q001" in out and "src/jobs/ 已有 3 个" in out


def test_g3_write_only_fields_removed_from_fresh_ledger(dd, repo):
    assert dd("init", "--feature", "f")[0] == 0
    led = read_ledger(repo)
    assert "repo_root" not in led["project"] and "created_at" not in led["project"]
    assert "scanned_at" not in led["kb"] and "note" not in led["kb"]
    assert "updated_at" not in led["manifest"]
    assert dd("kb-scan")[0] == 0
    led = read_ledger(repo)
    assert "scanned_at" not in led["kb"] and "note" not in led["kb"]


# ---------- G4：PASS 级真实发出（不再永远 PASS 0） ----------

def test_g4_pass_items_emitted(dd, grilling):
    assert dd("q-answer", "Q001", "--answer", "x")[0] == 0
    assert dd("q-answer", "Q002", "--answer", "y")[0] == 0
    write_doc(grilling, make_analysis())
    code, out = dd("check", "--doc", "analysis")
    assert code == 0
    assert "[PASS]" in out and "sections-ok" in out and "status-ok" in out
    assert "PASS 0" not in out


# ---------- G5：flag 组合不再静默丢弃 ----------

def test_g5_batch_with_single_flags_rejected(dd, grilling, tmp_path):
    batch = tmp_path / "questions.json"
    batch.write_text('[{"dimension":"api","title":"t","body":"b","recommendation":"r"}]',
                     encoding="utf-8")
    code, out = dd("q-add", "--batch", batch, "--title", "混传")
    assert code == 2 and "互斥" in out
    led = read_ledger(grilling)
    assert len(led["questions"]) == 2  # 未部分写入


def test_g5_reason_without_defer_rejected(dd, grilling):
    code, out = dd("q-answer", "Q001", "--answer", "决策", "--reason", "说明")
    assert code == 2 and "--reason" in out
    q = read_ledger(grilling)["questions"][0]
    assert q["status"] == "open"  # 未写入


def test_g5_adr_slug_without_adr_rejected(dd, grilling):
    code, out = dd("q-answer", "Q001", "--answer", "决策", "--adr-slug", "orphan")
    assert code == 2 and "--adr-slug" in out


# ---------- G6：graphlib 批量路径（大批量依赖链可用） ----------

def test_g6_large_batch_chain_ok(dd, grilling, tmp_path):
    """200 问链式依赖批量写入：graphlib 一次拓扑遍历，无 RecursionError。"""
    import json
    specs = [{"dimension": "scope", "title": f"问题{i:03d}", "body": "b",
              "recommendation": "r"} for i in range(200)]
    batch = tmp_path / "many.json"
    batch.write_text(json.dumps(specs, ensure_ascii=False), encoding="utf-8")
    code, out = dd("q-add", "--batch", batch)
    assert code == 0, out[:200]
    led = read_ledger(grilling)
    assert len(led["questions"]) == 202
