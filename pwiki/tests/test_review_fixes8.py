"""2026-11-09 第六轮 max 审查批次①回修的回归测试 —— 三条主线:
栅栏盲/风格链分叉(#1/#8/#15/#29/#30/#32)、finish-update 字段合并缺口
(#2/#3)、run 状态机无守卫 update_run 残留(#16/#17/#18/#19)。

对应报告: docs/code-review/2026-11-09-pwiki-max-r6.{md,json}(仓库根)。
编号沿用报告序号(#N = JSON 第 N 条)。
"""

from __future__ import annotations

import json
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from pwiki_lib import config
from pwiki_lib.config import now_iso

from test_e2e import _fixture, _write_all_jobs
from conftest import _cli, _ctx


def _ns(repo, root, **kw):
    base = {"repo": repo.path, "root": root, "locale": "zh"}
    base.update(kw)
    return types.SimpleNamespace(**base)


# --------------------------------------------------------------------------- #
# #1/#30: compose 的旧目录剥除与注入位都栅栏感知
# --------------------------------------------------------------------------- #

_FENCE_TOC_PAGE = """# 支付模块

<cite>
引用文件
- [README.md](file://README.md)
</cite>

## 概述

真的概述 [README.md:1-2](file://README.md#L1-L2)。

## 示例

```markdown
## 目录

1. [概述](#概述)
```

## 架构

架构事实。

## 组件

组件事实。
"""


def test_r6_01_toc_strip_fence_aware(tmp_path):
    """示例栅栏里的 '## 目录' 不再连带栅栏闭合符一起被剥 —— 发布文件
    栅栏完整, 且合成幂等(旧裸 sub 每次 finish 确定性重复损毁)。"""
    from pwiki_lib import publish
    repo, root = _fixture(tmp_path)
    ctx = _ctx(repo, root)
    p = {"content": _FENCE_TOC_PAGE, "path": "zh/content/pay.md",
         "kind": "page"}
    out, _ = publish.compose_export_content(ctx, p, "concise", {"zh/content/pay.md"})
    # 栅栏完整(开闭成对), 栅栏内的示例目录原样保留
    assert out.count("```") == 2, "栅栏闭合符被旧 TOC_SECTION_RE 吃掉"
    assert "```markdown\n## 目录" in out
    # '## 架构' 的正文没有被吞
    assert "架构事实。" in out and "组件事实。" in out
    # 新编号目录注入且正文里只有一个 '## 目录' 标题(栅栏里那个保留)
    from pwiki_lib.gates import strip_code_fences
    assert strip_code_fences(out).count("\n## 目录\n") == 1
    assert "1. [概述](#" in out
    # 幂等: 对已合成产物再合成一遍不变(旧代码每轮重复删/重复注)
    out2, _ = publish.compose_export_content(
        ctx, {"content": out, "path": p["path"], "kind": "page"},
        "concise", {"zh/content/pay.md"})
    assert out2 == out


def test_r6_30_toc_fallback_keeps_cite_and_fence(tmp_path):
    """手写 '## 目录' 节内自带 <cite>(concise 合法): 该节被剥后 cite 块
    要补回, 编号目录注在 cite 之后; 栅栏内 '# 注释' 不是 H1 注入位。"""
    from pwiki_lib import publish
    repo, root = _fixture(tmp_path)
    ctx = _ctx(repo, root)
    page = """# 网关

## 目录

1. [概述](#概述)

<cite>
引用文件
- [README.md](file://README.md)
</cite>

## 概述

概述 [README.md:1-2](file://README.md#L1-L2)。

```bash
# 注释不是标题
echo hi
```

## 架构

架构事实。

## 组件

组件事实。
"""
    p = {"content": page, "path": "zh/content/gw.md", "kind": "page"}
    out, _ = publish.compose_export_content(ctx, p, "concise",
                                            {"zh/content/gw.md"})
    # cite 块存在(编号目录注入点在它后面), 编号目录在栅栏之前
    assert "</cite>" in out
    assert out.index("</cite>") < out.index("\n## 目录\n") < \
        out.index("```bash")
    assert "# 注释不是标题" in out
    # 栅栏没被劈开
    assert out.count("```") == 2


# --------------------------------------------------------------------------- #
# #29: submit 摘要提取先剥栅栏再去 cite
# --------------------------------------------------------------------------- #

def test_r6_29_submit_summary_no_cross_fence_cite(tmp_path):
    """正文孤立 <cite> 不再与栅栏内示例 </cite> 跨界配对吞掉正文尾巴。"""
    from pwiki_lib import engine
    repo, root = _fixture(tmp_path)
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    ctx = _ctx(repo, root)
    run = ctx.db.active_run(ctx.partition()["id"])
    job = None
    while True:
        got = ctx.db.claim_jobs(run["id"], count=1)
        if not got:
            break
        if got[0]["kind"] == "page":
            job = got[0]
            break
    assert job is not None
    from test_review_fixes import _write_job_file
    page_file = _write_job_file(root, job)
    body = open(page_file, encoding="utf-8").read()
    # 首段插入孤立 <cite>(无闭合), 栅栏内放示例 </cite>
    marker = "页面摘要。"
    assert marker in body
    body = body.replace(
        marker,
        marker + "这里出现孤立 <cite> 标记, 后面还是正文。\n\n"
        "```xml\n<example>\n</cite>\n</example>\n```", 1)
    with open(page_file, "w", encoding="utf-8") as fh:
        fh.write(body)
    assert engine.cmd_submit(_ns(
        repo, root, page_id=job["page_id"], file=page_file, summary="",
        key_topics="", report=None, lenient=False, force=False,
        claim=job["claim_token"])) == config.EXIT_OK
    row = ctx.db.job(run["id"], job["page_id"])
    assert row["summary"].startswith("页面摘要。"), \
        f"摘要被跨界配对吞坏: {row['summary'][:80]!r}"


# --------------------------------------------------------------------------- #
# #32: BM25 字段提取剥栅栏
# --------------------------------------------------------------------------- #

def test_r6_32_search_fields_strip_fences():
    from pwiki_lib.search import _fields
    content = """```yaml
# Example config
key: v
```
# 真标题

## 真小节

正文。
"""
    title, headings, body = _fields(content)
    flat = " ".join(title + headings)
    assert "example" not in flat.lower() and "config" not in flat.lower(), \
        "栅栏内 '# Example config' 混进标题/小节池"
    assert "真" in title and "标" in title, f"真标题没进标题池: {title}"
    assert "正" in body and "文" in body


# --------------------------------------------------------------------------- #
# #2: kept-merge 同步 check_item 对账的全部目录字段
# --------------------------------------------------------------------------- #

def test_r6_02_kept_merge_syncs_reconciliation_fields(tmp_path, capsys):
    """手编 objective/required_sections/pack_key 后 update→finish 一轮落地,
    第二轮 update noop —— 旧代码'比而不合'造成无限 0-job INCREMENT 循环。"""
    from pwiki_lib import planner
    repo, root = _fixture(tmp_path)
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    _write_all_jobs(repo, root)
    assert _cli(["finish", "--repo", repo.path, "--root", root,
                 "--yes"]) == config.EXIT_OK
    ctx = _ctx(repo, root)
    pid = ctx.partition()["id"]

    plan = planner.load_plan(ctx)
    pg = next(p for p in plan["pages"] if p.get("kind") != "module_sheet")
    pg["objective"] = "r6 手编目标"
    pg["required_sections"] = ["手编节A", "手编节B"]
    pg["pack_key"] = "handmade-pack"
    planner.save_plan(ctx, plan)

    capsys.readouterr()
    assert _cli(["update", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    out = capsys.readouterr().out
    assert "(catalog_changed" in out, f"手编目录字段未被识别为 moved: {out}"
    assert _cli(["finish", "--repo", repo.path, "--root", root,
                 "--yes"]) == config.EXIT_OK
    row = ctx.db.published_page(pid, str(pg["id"]))
    assert row is not None
    assert row["objective"] == "r6 手编目标", "kept-merge 仍不写 objective"
    assert json.loads(row["required_sections"]) == ["手编节A", "手编节B"]
    assert row["pack_key"] == "handmade-pack"

    # 第二轮: 目录已收敛 → noop(旧代码: catalog_changed 永不收敛)
    capsys.readouterr()
    assert _cli(["update", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    assert "no update required (noop)" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# #3: merge_manual 三路合并(显式清空/序号 0 也是人工编辑)
# --------------------------------------------------------------------------- #

def test_r6_03_merge_manual_three_way():
    from pwiki_lib.planner import MANUAL_FIELDS
    from pwiki_lib.themes import merge_manual

    def run(old, prev, new):
        old_items = [{"id": "a", **old}]
        engine_prev = {"a": prev} if prev is not None else None
        return merge_manual([{"id": "a", **new}], old_items, MANUAL_FIELDS,
                            engine_prev=engine_prev)[0]

    # 手工清空 parent(提升为顶层页的唯一途径)被保留, 不回填新规划值
    got = run(old={"parent": None, "title": "t"},
              prev={"parent": "dom", "title": "t"},
              new={"parent": "dom"})
    assert got["parent"] is None
    # 删键(整行删掉)同样是清空
    got = run(old={"title": "t"}, prev={"parent": "dom", "title": "t"},
              new={"parent": "dom"})
    assert got.get("parent") is None
    # ordinal 0 与快照不同 → 手改, 保留(启发式下 0 因 falsy 被丢)
    got = run(old={"ordinal": 0}, prev={"ordinal": 2}, new={"ordinal": 2})
    assert got["ordinal"] == 0
    # 旧值 == 快照(引擎自己写的, 未手改) → 新规划值落地(根页可再挂子树)
    got = run(old={"parent": None}, prev={"parent": None},
              new={"parent": "dom"})
    assert got["parent"] == "dom"
    # 无快照(首跑/基线被删)退回启发式: ordinal 0 不再因 falsy 被吞
    got = run(old={"ordinal": 0}, prev=None, new={"ordinal": 2})
    assert got["ordinal"] == 0


def test_r6_03_manual_baseline_roundtrip(tmp_path):
    """基线按 id 增量并入: layers 只记 pages 时, 卡片的既有基线不丢。"""
    from pwiki_lib.themes import (load_manual_baseline,
                                  save_manual_baseline)
    repo, root = _fixture(tmp_path)
    ctx = _ctx(repo, root)
    # fixture 的 plan --seed 已写过整份基线 —— 增量并入不抹既有条目
    seeded = set(load_manual_baseline(ctx))
    assert seeded, "seed 路线没有写基线"
    save_manual_baseline(ctx, [
        {"id": "card-x", "title": "卡", "parent": "p", "ordinal": 1}])
    save_manual_baseline(ctx, [{"id": "pg-y", "title": "页"}])
    snap = load_manual_baseline(ctx)
    assert {"card-x", "pg-y"} <= set(snap), "增量并入丢条目"
    assert seeded <= set(snap), "layers 提交清掉了卡片基线"
    assert snap["card-x"]["ordinal"] == 1
    assert "parent" in snap["card-x"]
    # 坏文件(手编/半写) → 空基线退回启发式, 不裸崩
    with open(os.path.join(root, "work", "manual-baseline.json"),
              "w", encoding="utf-8") as fh:
        fh.write("{broken json")
    assert load_manual_baseline(ctx) == {}


# --------------------------------------------------------------------------- #
# #16/#17/#18/#19: run 状态机取消/恢复类转换全部守卫式
# --------------------------------------------------------------------------- #

def _begin(tmp_path):
    repo, root = _fixture(tmp_path)
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    ctx = _ctx(repo, root)
    run = ctx.db.active_run(ctx.partition()["id"])
    assert run is not None
    return repo, root, ctx, run


def test_r6_16_pause_write_is_state_guarded(tmp_path, monkeypatch):
    """pause 的 PAUSED 写带状态前置: 读检查后 run 被并发 finish 置
    LINING 时, 裸 UPDATE 曾把 LINING 覆盖成 PAUSED(wiki 已发布而 run
    卡活动态)。"""
    from pwiki_lib import engine
    repo, root, ctx, run = _begin(tmp_path)
    rid = run["id"]
    # 并发 finish 抢先置 LINING; pause 读到的还是陈旧 DG 快照
    ctx.db.update_run(rid, state="LINING")
    stale = dict(ctx.db.conn.execute(
        "SELECT * FROM run WHERE id=?", (rid,)).fetchone())
    monkeypatch.setattr(engine, "_active",
                        lambda _ctx: (ctx.partition(), stale))
    with pytest.raises(SystemExit) as ei:
        _cli(["pause", "--repo", repo.path, "--root", root])
    assert ei.value.code == config.EXIT_PRECOND
    assert ctx.db.active_run(ctx.partition()["id"])["state"] == "LINING"


def test_r6_17_resume_write_is_state_guarded(tmp_path, monkeypatch):
    """resume 的 DG 写只在仍是 PAUSED 时生效: 并发 cancel 已置 CANCELLED
    时不再复活已取消的 run。"""
    from pwiki_lib import engine
    repo, root, ctx, run = _begin(tmp_path)
    rid = run["id"]
    assert _cli(["pause", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    stale = dict(ctx.db.conn.execute(
        "SELECT * FROM run WHERE id=?", (rid,)).fetchone())
    assert stale["state"] == "PAUSED"
    # 并发 cancel(PAUSED ∈ 可取消态)
    ctx.db.update_run(rid, state="CANCELLED", finished_at=now_iso())
    monkeypatch.setattr(engine, "_active",
                        lambda _ctx: (ctx.partition(), stale))
    with pytest.raises(SystemExit) as ei:
        _cli(["resume", "--repo", repo.path, "--root", root])
    assert ei.value.code == config.EXIT_PRECOND
    assert ctx.db.active_run(ctx.partition()["id"]) is None, \
        "已取消的 run 被复活成活动态"


def test_r6_18_lining_rejected_write_is_state_guarded(tmp_path, monkeypatch):
    """finish 的 lining-被拒分支落 DOCUMENT_FAILED 走守卫: 竞争的第二个
    finish 已终写 COMPLETED 时不把终态复活成活动态。"""
    from pwiki_lib import gates
    repo, root = _fixture(tmp_path)
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    ctx = _ctx(repo, root)
    run = ctx.db.active_run(ctx.partition()["id"])
    _write_all_jobs(repo, root)
    # 一份必挂 LINING 的稿(引用不存在的文件 —— 引用真实性永不放宽)
    bad = ("# 坏稿\n\n引用 [nope/ghost.py:1-2](file://nope/ghost.py#L1-L2)。\n")
    ctx.db.conn.execute(
        "UPDATE page_job SET content=? WHERE run_id=? AND kind='page' "
        "AND state='completed'",
        (bad, run["id"]))

    flipped = []
    orig_gate = gates.gate_item

    def spy(content, item, repo_, style, phase="submit", mod_titles=None):
        if not flipped:   # 第一个 completed job 进门禁时并发方终写
            flipped.append(1)
            ctx.db.update_run(run["id"], state="COMPLETED", stage="done",
                              finished_at=now_iso())
        return orig_gate(content, item, repo_, style, phase=phase,
                         mod_titles=mod_titles)

    monkeypatch.setattr(gates, "gate_item", spy)
    with pytest.raises(SystemExit) as ei:
        _cli(["finish", "--repo", repo.path, "--root", root, "--yes"])
    assert ei.value.code in (config.EXIT_PRECOND, config.EXIT_GATE)
    assert ctx.db.active_run(ctx.partition()["id"]) is None, \
        "COMPLETED 终态被 lining-rejected 分支复活"


def test_r6_19_begin_post_queue_write_is_state_guarded(tmp_path,
                                                       monkeypatch):
    """begin 在 queue_jobs 之后的 DG 写带状态前置: 排队窗口内被并发
    cancel/begin --force 取消的 run 不翻回活动态。"""
    from pwiki_lib.db import WikiDB
    repo, root = _fixture(tmp_path)
    orig = WikiDB.queue_jobs

    def spy(self, run_id, items, style=""):
        # 排队落库的同时, 并发 cancel 合法取消本 run(带 claim 释放)
        self.update_run(run_id, state="CANCELLED", finished_at=now_iso())
        self.release_all_claims(run_id)
        return orig(self, run_id, items, style=style)

    monkeypatch.setattr(WikiDB, "queue_jobs", spy)
    with pytest.raises(SystemExit) as ei:
        _cli(["begin", "--repo", repo.path, "--root", root])
    assert ei.value.code == config.EXIT_PRECOND
    ctx = _ctx(repo, root)
    assert ctx.db.active_run(ctx.partition()["id"]) is None, \
        "排队窗口被取消的 run 被 begin 翻回活动态"


# --------------------------------------------------------------------------- #
# #15: file_to_db 空盖章行的兜底链与 db_to_file 同序
# --------------------------------------------------------------------------- #

def test_r6_15_file_to_db_empty_stamp_fallback(tmp_path, capsys):
    """行戳为空(手编/旧库行)且 plan≠last_run 时, 两个 export 方向解析出
    同一风格: db_to_file 按 plan(concise)注入, file_to_db 也按 plan 剥除
    —— 注入物不再被吸收进 page.content。"""
    repo, root = _fixture(tmp_path)
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    _write_all_jobs(repo, root)
    assert _cli(["finish", "--repo", repo.path, "--root", root,
                 "--yes"]) == config.EXIT_OK
    ctx = _ctx(repo, root)
    pid = ctx.partition()["id"]
    # 模拟旧库/手编现场: 行无盖章, 最后一个 run 盖 deep(与 plan 相反)
    ctx.db.conn.execute("UPDATE page SET style='' WHERE partition_id=?",
                        (pid,))
    ctx.db.conn.execute(
        "UPDATE run SET style='deep' WHERE id=(SELECT id FROM run WHERE "
        "partition_id=? ORDER BY started_at DESC LIMIT 1)", (pid,))
    page = next(p for p in ctx.db.published_pages(pid) if p["kind"] == "page")
    before_hash = page["content_hash"]

    assert _cli(["export", "--direction", "db_to_file", "--repo", repo.path,
                 "--root", root]) == config.EXIT_OK
    assert _cli(["export", "--direction", "file_to_db", "--repo", repo.path,
                 "--root", root]) == config.EXIT_OK
    out = capsys.readouterr().out
    row = ctx.db.published_page(pid, page["page_id"])
    # 注入层(编号目录)被剥掉, DB 回到 writer 原稿: 哈希不变、无吸收
    assert row["content_hash"] == before_hash, \
        "注入物被吸收进 page.content(bug10 防线被绕过)"
    assert "\n## 目录\n" not in (row["content"] or "")
    assert "吸收 0" in out


# --------------------------------------------------------------------------- #
# #8: validate --content 按行自己的盖章复检
# --------------------------------------------------------------------------- #

def test_r6_08_validate_content_per_row_stamps(tmp_path):
    """混合盖章树(行全 concise、手改 plan style: deep)validate 不再按
    plan 级风格把引擎自己发布的树打 exit 2。"""
    from pwiki_lib import planner
    repo, root = _fixture(tmp_path)
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    _write_all_jobs(repo, root)
    assert _cli(["finish", "--repo", repo.path, "--root", root,
                 "--yes"]) == config.EXIT_OK
    ctx = _ctx(repo, root)
    plan = planner.load_plan(ctx)
    assert (plan.get("style") or ctx.style) == "concise"
    plan["style"] = "deep"                     # 手改 plan, 行盖章仍是 concise
    planner.save_plan(ctx, plan)
    assert _cli(["validate", "--content", "--repo", repo.path,
                 "--root", root]) == config.EXIT_OK
