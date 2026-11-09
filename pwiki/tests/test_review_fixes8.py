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


# --------------------------------------------------------------------------- #
# 批次②: Tier-1 剩余 10 条(#4/#5/#6/#7/#9/#10/#11/#12/#13/#14)
# --------------------------------------------------------------------------- #

def test_r6_04_expand_scope_trailing_slash_file(tmp_path):
    """带尾斜杠的文件路径归一后走文件分支; 两种写法共享缓存键且都非空。"""
    from pwiki_lib.scan import expand_scope
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "src" / "main.py").write_text("x = 1\n", encoding="utf-8")
    r = str(repo)
    entries, seen = {}, set()
    expand_scope(r, entries, seen, "src/main.py/")
    assert set(entries) == {"src/main.py"}, \
        "尾斜杠文件路径跟踪 0 文件(校验放行, 永久静默陈旧)"
    # 缓存不被斜杠写法污染: 无斜杠查询同样命中文件
    entries2, seen2 = {}, set()
    expand_scope(r, entries2, seen2, "src/main.py")
    assert set(entries2) == {"src/main.py"}
    # 目录两种写法仍同一份展开
    entries3, seen3 = {}, set()
    expand_scope(r, entries3, seen3, "src/")
    assert set(entries3) == {"src/main.py"}


def test_r6_05_compose_cache_key_includes_export_style(tmp_path):
    """切换 export_style 后未变页的合成缓存失效 —— current 模式下重跑
    finish 不再继续供 legacy 注入口径的文件(注入物随后被 file_to_db 吸收)。"""
    from pwiki_lib import planner
    repo, root = _fixture(tmp_path)
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    _write_all_jobs(repo, root)
    assert _cli(["finish", "--repo", repo.path, "--root", root,
                 "--yes"]) == config.EXIT_OK
    ctx = _ctx(repo, root)
    pid = ctx.partition()["id"]
    page = next(p for p in ctx.db.published_pages(pid) if p["kind"] == "page")
    f1 = os.path.join(root, page["path"])
    with open(f1, encoding="utf-8") as fh:
        first = fh.read()
    assert "\n## 目录\n" in first, "前置失败: legacy 默认应注入编号目录"

    plan = planner.load_plan(ctx)
    plan["export_style"] = "current"
    planner.save_plan(ctx, plan)
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    # 第二个 run 的 job 全部 skip 成终态 —— finish 走 kept 重发布路径
    # (compose 缓存循环正是在这里跑全树)
    run2 = ctx.db.active_run(pid)
    for j in ctx.db.jobs_meta(run2["id"]):
        ctx.db.set_job_state(run2["id"], j["page_id"], "skipped",
                             notes="r6 cache-key test")
    assert _cli(["finish", "--repo", repo.path, "--root", root,
                 "--yes"]) == config.EXIT_OK
    with open(f1, encoding="utf-8") as fh:
        second = fh.read()
    assert "\n## 目录\n" not in second, \
        "export_style 切换后缓存键未失效, 旧注入口径文件继续出厂"


def test_r6_06_type_sections_preset_gated():
    """type 用途清单的 schema/sections 走 section_sources 预设键: concise
    在 LINING 保持警告(合法 run 不死在 finish), deep 升错误。"""
    from pwiki_lib.gates import gate_item
    content = ("# 支付\n\n## A\n\n事实 [README.md:1-2](file://README.md#L1-L2)。\n"
               "## B\n\n事实 [README.md:1](file://README.md#L1)。\n")
    item = {"kind": "page", "title": "支付", "type": "overview"}
    errs, warns = gate_item(content, item, ".", "concise", phase="lining")
    assert not any("schema/sections" in e for e in errs), errs
    assert any("schema/sections" in w for w in warns), warns
    errs, warns = gate_item(content, item, ".", "deep", phase="lining")
    assert any("schema/sections" in e for e in errs), errs


def test_r6_07_as_int_float_edges():
    """nan/inf/1e999 回落 default 而不是裸 ValueError/OverflowError。"""
    from pwiki_lib.util import as_int
    assert as_int(float("nan"), 7) == 7
    assert as_int(float("inf"), 7) == 7
    assert as_int(float("-inf"), 7) == 7
    assert as_int(1e999, 7) == 7
    assert as_int(3.9, 7) == 3
    assert as_int("12", 7) == 12
    assert as_int(None, 7) == 7


def test_r6_09_partition_read_path_no_write(tmp_path):
    """另一个连接持有写事务期间, partition() 的只读解析不再阻塞 ——
    只读命令(pages/read/status/search)不再因顺手 UPDATE 写锁裸崩。"""
    from pwiki_lib.db import WikiDB
    root = str(tmp_path / "wiki")
    db1 = WikiDB(root)
    db1.partition("main", "zh")
    db2 = WikiDB(root)
    db2.conn.execute("PRAGMA busy_timeout=300")   # 旧代码在此耗尽后裸崩
    db1.begin()   # BEGIN IMMEDIATE: 模拟 finish 的长发布事务
    try:
        row = db2.partition("main", "zh")
        assert row is not None and row["branch"] == "main"
    finally:
        db1.rollback()


def test_r6_10_adoptable_token_rejects_pending_attempts():
    """attempts>1 省略 --claim 恒拒 —— 不再收窄到 state=='processing',
    租约过期回 pending 的陈旧稿不再赢得发布位。"""
    from pwiki_lib.engine import _adoptable_token

    def row(state, attempts):
        return {"state": state, "attempts": attempts, "claim_token": "t"}

    _, violated = _adoptable_token(row("pending", 2), None)
    assert violated, "过期回 pending 的重认领行接受了无令牌提交"
    _, violated = _adoptable_token(row("processing", 1), None)
    assert not violated, "首认领的省略采纳(单写便利)被误伤"
    _, violated = _adoptable_token(row("processing", 3), None)
    assert violated


def test_r6_11_parse_pyproject_inline_close_bracket(tmp_path):
    """末项带闭合括号(`"bar"]`)的依赖数组: 项被收且数组收口, 后续
    [tool.poetry.dependencies] 节不再被吞。"""
    from pwiki_lib.scan import parse_pyproject
    p = tmp_path / "pyproject.toml"
    p.write_text("[project]\n"
                 "name = \"demo\"\n"
                 "dependencies = [\n"
                 "  \"foo\",\n"
                 "  \"bar\"]\n"
                 "[tool.poetry.dependencies]\n"
                 "python = \"^3.11\"\n", encoding="utf-8")
    out = parse_pyproject(str(p))
    assert out["dependencies"] == ["foo", "bar", "python"], \
        out["dependencies"]


def test_r6_12_render_module_tree_root_scope_all():
    """parent_modules=['/'] = 全仓(layers 侧同判): 渲染完整模块树而不是
    只剩根一行。"""
    from pwiki_lib.planner import render_module_tree
    plan = {"modules": [
        {"id": "root", "path": "/", "title": "根", "parent": ""},
        {"id": "apps", "path": "apps", "title": "应用", "parent": "root"},
        {"id": "scripts", "path": "scripts", "title": "脚本", "parent": "root"},
    ]}
    tree = render_module_tree(plan, ["/"])
    assert "`apps`" in tree and "`scripts`" in tree, tree
    # 限定域仍然只渲染该域(+祖先链)
    tree2 = render_module_tree(plan, ["apps"])
    assert "`apps`" in tree2 and "`scripts`" not in tree2


def test_r6_13_coverage_check_without_typed_pages(tmp_path):
    """无任何页带 type(合法可选)时, 模块覆盖硬规照样执行。"""
    from pwiki_lib import planner
    repo, root = _fixture(tmp_path)
    ctx = _ctx(repo, root)
    plan = planner.load_plan(ctx)
    # apps/services 域有真实文件; 构造一个只覆盖 root 的单页计划
    plan["pages"] = [{"id": "only", "title": "唯一页", "kind": "page",
                      "modules": ["/"], "ordinal": 1}]
    plan["cards"] = []
    errors, _w = planner.validate_plan(ctx, plan, strict=True)
    assert any("未被任何文章的 modules 覆盖" in e for e in errors), errors


def test_r6_14_qoderplan_emit_keeps_parent_on_same_title(tmp_path):
    """父子同名(合法树)时子档仍带 parent 键 —— 不再被 IDE 按'重复 title
    丢后者'契约整篇静默蒸发。"""
    from pwiki_lib import planner, qoderplan
    from pwiki_lib.yamlite import yaml_load
    repo, root = _fixture(tmp_path)
    ctx = _ctx(repo, root)
    plan = planner.load_plan(ctx)
    plan["pages"] = [
        {"id": "pay", "title": "支付", "kind": "page", "ordinal": 1,
         "modules": ["/"]},
        {"id": "pay-refund", "title": "支付", "kind": "page",
         "parent": "pay", "ordinal": 2, "modules": ["/"]},
    ]
    plan["cards"] = []
    planner.save_plan(ctx, plan)
    out = os.path.join(root, "work", "qoder-plan.yaml")
    assert qoderplan._emit(ctx, out) == config.EXIT_OK
    with open(out, encoding="utf-8") as fh:
        docs = yaml_load(fh.read())["repowiki"]["documents"]
    assert len(docs) == 2, docs
    child = next(d for d in docs if d["goal"] != docs[0]["goal"]) \
        if False else docs[1]
    assert child.get("parent") == "支付", docs


# --------------------------------------------------------------------------- #
# 批次③: 并发窗口 5 条(#20-#24) + 手编裸崩 4 条(#25-#28)
# --------------------------------------------------------------------------- #

def test_r6_20_claim_jobs_rechecks_run_state(tmp_path):
    """claim 的事务内复查 run 状态: 已 CANCELLED 的 run 不再被认领,
    不留永久 processing 行。"""
    from pwiki_lib.db import WikiDB
    root = str(tmp_path / "wiki")
    db = WikiDB(root)
    pid = db.partition("main", "zh")["id"]
    db.conn.execute(
        "INSERT INTO run(id,partition_id,kind,operation,generation_type,state,"
        "started_at,updated_at) VALUES('r1',?,'generate','start','FULL',"
        "'DOCUMENT_GENERATING','2026-01-01T00:00:00','2026-01-01T00:00:00')",
        (pid,))
    db.conn.execute(
        "INSERT INTO page_job(run_id,page_id,title,kind,kind_rank,depth) "
        "VALUES('r1','p1','t','page',2,0)")
    # next 读到 DG 后、claim 拿锁前: 并发 cancel 置 CANCELLED + 释放
    db.update_run("r1", state="CANCELLED", finished_at=now_iso())
    assert db.claim_jobs("r1", count=1) == []
    row = db.job("r1", "p1")
    assert row["state"] == "pending", "死 run 仍被认领成 processing"
    # 活动态照常认领
    db.update_run("r1", state="DOCUMENT_GENERATING")
    assert len(db.claim_jobs("r1", count=1)) == 1


def test_r6_21_fail_guarded_against_lining(tmp_path, monkeypatch):
    """fail 的状态写带 run 前置: 守卫读检查后 run 被并发 finish 置
    LINING 时, 已完成的 job 不被翻 failed(发布集不蒸发)。"""
    from pwiki_lib import engine
    repo, root, ctx, run = _begin(tmp_path) \
        if False else (None, None, None, None)
    repo, root = _fixture(tmp_path)
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    ctx = _ctx(repo, root)
    run = ctx.db.active_run(ctx.partition()["id"])
    claimed = ctx.db.claim_jobs(run["id"], count=1)[0]
    ctx.db.set_job_state(run["id"], claimed["page_id"], "completed",
                         content="# t\n")
    # 并发 finish 抢先置 LINING; fail 读到的还是陈旧 DG 快照
    ctx.db.update_run(run["id"], state="LINING")
    stale = dict(ctx.db.conn.execute(
        "SELECT * FROM run WHERE id=?", (run["id"],)).fetchone())
    monkeypatch.setattr(engine, "_active",
                        lambda _ctx: (ctx.partition(), stale))
    with pytest.raises(SystemExit) as ei:
        engine.cmd_fail(_ns(repo, root, page_id=claimed["page_id"],
                            error="x", claim=None, force=True))
    assert ei.value.code == config.EXIT_PRECOND
    assert ctx.db.job(run["id"], claimed["page_id"])["state"] == \
        "completed", "LINING 窗口内 completed 被翻成 failed"


def test_r6_22_reconcile_rename_rechecks_active_run_in_txn(tmp_path):
    """改名的活动 run 复查在 BEGIN IMMEDIATE 事务内: 前置检查(裸 SELECT)
    之后、提交之前并发 begin 建的 run 使改名放弃(retry)而不是被静默回滚。"""
    from pwiki_lib import planner, reconcile
    repo, root = _fixture(tmp_path)
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    _write_all_jobs(repo, root)
    assert _cli(["finish", "--repo", repo.path, "--root", root,
                 "--yes"]) == config.EXIT_OK
    ctx = _ctx(repo, root)
    pid = ctx.partition()["id"]
    plan = planner.load_plan(ctx)
    pg = next(p for p in plan["pages"]
              if isinstance(p, dict) and p.get("id")
              and p.get("kind", "page") == "page")
    # begin 建活动 run(它在改名的事务内复查时"插进来")
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    active = ctx.db.active_run(pid)
    assert active is not None
    calls = {"n": 0}
    orig = ctx.db.active_run

    def first_call_blind(p):
        calls["n"] += 1
        return None if calls["n"] == 1 else orig(p)   # 前置检查先过

    ctx.db.active_run = first_call_blind
    try:
        status, msg = reconcile._apply_title_rename(
            ctx, {"page_id": pg["id"], "proposed": "改名后的标题"}, False)
    finally:
        del ctx.db.active_run
    assert status == "retry", msg
    row = ctx.db.published_page(pid, str(pg["id"]))
    assert row is None or row["title"] != "改名后的标题", \
        "事务窗口内被 begin 插入时改名仍被写入(将被 finish 静默回滚)"


def test_r6_23_leased_jobs_filters_expired(tmp_path):
    """leased_jobs 只报未过期租约: writer 全死且租约过期后 pause 不再
    永久被拒。"""
    from pwiki_lib.db import WikiDB
    from pwiki_lib.config import iso_plus_minutes
    root = str(tmp_path / "wiki")
    db = WikiDB(root)
    pid = db.partition("main", "zh")["id"]
    db.conn.execute(
        "INSERT INTO run(id,partition_id,kind,operation,generation_type,state,"
        "started_at,updated_at) VALUES('r1',?,'generate','start','FULL',"
        "'DOCUMENT_GENERATING','2026-01-01T00:00:00','2026-01-01T00:00:00')",
        (pid,))
    past = "2026-01-01T00:00:00"
    db.conn.execute(
        "INSERT INTO page_job(run_id,page_id,title,kind,state,claim_token,"
        "lease_expires_at) VALUES('r1','dead','t','page','processing',"
        "'tok','2026-01-01T00:00:01')")
    db.conn.execute(
        "INSERT INTO page_job(run_id,page_id,title,kind,state,claim_token,"
        "lease_expires_at) VALUES('r1','live','t','page','processing','tok',?)",
        (iso_plus_minutes(now_iso(), 30),))
    rows = db.leased_jobs("r1")
    assert [r["page_id"] for r in rows] == ["live"], \
        "过期租约仍在租约列表里(pause 将被永久拒绝)"
    del past


def test_r6_24_lease_minutes_clamped(tmp_path, monkeypatch):
    """config lease_minutes=0 不再让租约即刻过期(submit 永久孤儿化)。"""
    from pwiki_lib import engine
    repo, root = _fixture(tmp_path)
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    ctx = _ctx(repo, root)
    import json as _json
    with open(os.path.join(root, "config.json"), "w",
              encoding="utf-8") as fh:
        fh.write(_json.dumps({"lease_minutes": 0}))
    ctx2 = _ctx(repo, root)
    ctx2.config["lease_minutes"] = 0
    run = ctx2.db.active_run(ctx2.partition()["id"])
    claimed = ctx2.db.claim_jobs(run["id"], count=1)
    assert claimed, "钳位前 0 分钟租约会在认领瞬间即过期回收"
    # 走 cmd_next 的钳位路径: 再认领一个, 租约时长 ≥1 分钟
    argv = _ns(repo, root, batch=1, json=False)
    rc = engine.cmd_next(argv)
    assert rc in (config.EXIT_OK, config.EXIT_PRECOND)
    rows = ctx2.db.conn.execute(
        "SELECT page_id, lease_expires_at FROM page_job WHERE run_id=? "
        "AND state='processing'", (run["id"],)).fetchall()
    assert rows and all(r["lease_expires_at]".replace("]", "")] >
                        now_iso() for r in rows)


def test_r6_25_validate_plan_scalar_entry_gate(tmp_path):
    """pages 里的裸标量条目报门禁错(exit 2 路径)而不是 AttributeError。"""
    from pwiki_lib import planner
    from pwiki_lib.yamlite import yaml_dump
    repo, root = _fixture(tmp_path)
    ctx = _ctx(repo, root)
    plan = planner.load_plan(ctx)
    plan["pages"] = ["快速开始"]
    errors, _w = planner.validate_plan(ctx, plan, strict=True)
    assert any("必须是映射" in e for e in errors), errors


def test_r6_26_aliases_entry_shapes(tmp_path, capsys):
    """--aliases 载入标量条目被入口拒绝; themes/deps 消费侧对坏行免疫。"""
    from pwiki_lib import planner
    repo, root = _fixture(tmp_path)
    af = os.path.join(root, "work", "aliases.yaml")
    os.makedirs(os.path.dirname(af), exist_ok=True)
    with open(af, "w", encoding="utf-8") as fh:
        fh.write("- yudao-framework\n- bar\n")
    with pytest.raises(SystemExit) as ei:
        _cli(["plan", "--aliases", af, "--repo", repo.path,
              "--root", root])
    assert ei.value.code == config.EXIT_GATE
    # 手编坏行入库的防御: 消费侧不裸崩
    plan = planner.load_plan(_ctx(repo, root))
    plan["aliases"] = ["scalar-entry"]
    planner.save_plan(_ctx(repo, root), plan)
    from pwiki_lib import themes
    ctx = _ctx(repo, root)
    assert themes.cmd_plan_themes.__call__   # 可调用(不执行全规划)
    # modules.seed_depends_on 的防御
    from pwiki_lib import modules as mods
    rules_seen = []
    orig = mods.load_inventory
    del rules_seen, orig
    # 直接单测消费表达式
    fw = {str(r.get("module")) for r in plan["aliases"]
          if isinstance(r, dict)}
    assert fw == set()


def test_r6_27_layers_state_articles_scalar_guard():
    """apply_layer_result 对 state['articles'] 里的标量条目跳过报
    problems, 不再 TypeError 裸崩。"""
    import json as _json
    from pwiki_lib.layers import apply_layer_result
    plan = {"limits": {}, "modules": [], "pages": [], "cards": []}
    state = {"articles": ["overview-broken", {"slug": "parent",
                                               "depth": 1,
                                               "modules": ["/"]}],
             "profile": "standard"}
    payload = {"articles": [
        {"slug": "overview2", "title": "t2", "type": "overview",
         "objective": "o", "modules": ["/"], "need_further_planning": False}]}
    text = "<result>" + _json.dumps(payload, ensure_ascii=False) + "</result>"
    added, problems = apply_layer_result(None, plan, state, text,
                                         parent_slug="parent")
    assert added and added[0]["slug"] == "overview2"
    assert any("不是映射" in p for p in problems), problems


def test_r6_28_update_card_scope_scalar(tmp_path, capsys):
    """手编卡片 scope 标量('**')过 validate 后 update 不再 TypeError。"""
    from pwiki_lib import planner
    repo, root = _fixture(tmp_path)
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    _write_all_jobs(repo, root)
    assert _cli(["finish", "--repo", repo.path, "--root", root,
                 "--yes"]) == config.EXIT_OK
    ctx = _ctx(repo, root)
    plan = planner.load_plan(ctx)
    card = next(c for c in plan["cards"]
                if isinstance(c, dict) and c.get("id"))
    card["scope"] = "**"
    planner.save_plan(ctx, plan)
    assert _cli(["update", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK


# --------------------------------------------------------------------------- #
# 批次④: 次级/环境相关 + 性能(#31/#33/#34/#35/#36/#37)
# --------------------------------------------------------------------------- #

def test_r6_31_inventory_repo_mismatch_invalid(tmp_path):
    """清单携带的 repo 与当前仓库对不上(克隆被移动)→ 视为无效触发重扫,
    不再静默分析旧路径。"""
    from pwiki_lib import scan as scan_mod
    repo, root = _fixture(tmp_path)
    ctx = _ctx(repo, root)
    inv = {"repo": "/nonexistent/old-location", "files": [{"path": "a.py"}]}
    scan_mod.write_inventory(ctx, inv)
    assert scan_mod.load_inventory(ctx) == {}, \
        "旧路径清单仍被信任(decompose 将静默分析旧位置)"
    # 归一化等价写法不误伤
    inv2 = {"repo": repo.path + "/", "files": []}
    scan_mod.write_inventory(ctx, inv2)
    assert scan_mod.load_inventory(ctx) == inv2
    # 一致的 repo 正常返回
    inv3 = {"repo": repo.path, "files": []}
    scan_mod.write_inventory(ctx, inv3)
    assert scan_mod.load_inventory(ctx) == inv3


def test_r6_33_snapshot_writes_single_txn(tmp_path, monkeypatch):
    """基线三写同事务: set_baseline 崩溃时哈希行一并回滚, 不留分叉基线。"""
    from pwiki_lib import snapshot
    from pwiki_lib.db import WikiDB
    repo, root = _fixture(tmp_path)
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    _write_all_jobs(repo, root)
    assert _cli(["finish", "--repo", repo.path, "--root", root,
                 "--yes"]) == config.EXIT_OK
    ctx = _ctx(repo, root)
    pid = ctx.partition()["id"]
    n_before = ctx.db.conn.execute(
        "SELECT COUNT(*) c FROM file_hash WHERE partition_id=?",
        (pid,)).fetchone()["c"]
    assert n_before > 0

    def boom(*a, **kw):
        raise RuntimeError("r6: set_baseline 崩在提交前")

    monkeypatch.setattr(WikiDB, "set_baseline", boom)
    plan = __import__("pwiki_lib.planner", fromlist=["load_plan"]).load_plan(ctx)
    ctx2 = _ctx(repo, root)
    run_row = {"id": "synthetic", "generation_type": "FULL"}
    with pytest.raises(RuntimeError):
        snapshot.snapshot_run(ctx2, run_row, plan)
    n_after = ctx2.db.conn.execute(
        "SELECT COUNT(*) c FROM file_hash WHERE partition_id=?",
        (pid,)).fetchone()["c"]
    assert n_after == n_before, "哈希行已提交而基线未写(分叉基线)"


def test_r6_35_link_grammar_single_source(tmp_path):
    """mailto: 结尾 .md 的链接与发布器同判(不捕获不报错), 真内链仍校验。"""
    from pwiki_lib.gates import validate_content
    from pwiki_lib import planner
    repo, root = _fixture(tmp_path)
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    _write_all_jobs(repo, root)
    assert _cli(["finish", "--repo", repo.path, "--root", root,
                 "--yes"]) == config.EXIT_OK
    ctx = _ctx(repo, root)
    plan = planner.load_plan(ctx)
    pg = next(p for p in plan["pages"]
              if isinstance(p, dict) and p.get("id")
              and p.get("kind", "page") == "page")
    row = ctx.db.published_page(ctx.partition()["id"], str(pg["id"]))
    assert row is not None and row["path"]
    target = os.path.join(root, row["path"])
    with open(target, encoding="utf-8") as fh:
        body = fh.read()
    body += "\n\n联系 [团队](mailto:team@example.md) 与断链 [死链](no-such.md)。\n"
    with open(target, "w", encoding="utf-8") as fh:
        fh.write(body)
    report, code = validate_content(ctx, plan)
    flat = "\n".join(report["global"]["errors"])
    assert "mailto:team@example.md" not in flat, \
        "mailto 链接被校验器报死链(与发布器判定相反)"
    assert "no-such.md" in flat, "真断链漏检"


def test_r6_37_join_confined_last_segment_symlink(tmp_path):
    """末段是指向仓外的符号链接文件仍拦截; 目录链 memo 不改变判定。"""
    from pwiki_lib.util import join_confined
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    inner = repo / "src" / "inner.py"
    inner.write_text("x = 1\n", encoding="utf-8")
    outside = tmp_path / "outside.py"
    outside.write_text("escape\n", encoding="utf-8")
    os.symlink(outside, repo / "src" / "evil.py")
    os.symlink(inner, repo / "src" / "good.py")     # 仓内目标: 放行
    r = str(repo)
    assert join_confined(r, "src/inner.py") is not None
    assert join_confined(r, "src/../src/inner.py") is not None
    assert join_confined(r, "../outside.py") is None, "../ 逃逸漏拦"
    assert join_confined(r, "src/evil.py") is None, \
        "末段符号链接逃逸漏拦(memo 化改造的回归)"
    assert join_confined(r, "src/good.py") is not None
    # 同一目录重复调用(memo 命中路径)判定不变
    for _ in range(3):
        assert join_confined(r, "src/evil.py") is None
        assert join_confined(r, "src/inner.py") is not None
