"""2026-11-08 max 审查回修的回归测试(P0×3 / 卡片 kind 根因族 / 竞态 / 门禁族)。

对应报告: pwiki/docs/code-review/2026-11-08-code-review-max.md(报告文件已清理)。
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from gitrepo import GitRepo

from pwiki_lib import config
from test_e2e import _fixture, _write_all_jobs


from conftest import _cli, _ctx   # 单一来源(r4 #63)


def _published_full(tmp_path, style="concise"):
    from pwiki_lib import cli
    repo, root = _fixture(tmp_path, style=style)
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == config.EXIT_OK
    _write_all_jobs(repo, root, style=style)
    assert _cli(["finish", "--repo", repo.path, "--root", root, "--yes"]) == \
        config.EXIT_OK
    return repo, root


# --------------------------------------------------------------------------- #
# #1 regenerate: page/card 两个分支都必须带派生 path
# --------------------------------------------------------------------------- #

def test_regenerate_page_and_card_carry_paths(tmp_path):
    repo, root = _fixture(tmp_path, style="concise")
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == config.EXIT_OK
    ctx = _ctx(repo, root)
    pid = ctx.partition()["id"]
    from pwiki_lib import planner
    plan = planner.load_plan(ctx)
    page_id = str(planner.plan_pages(plan)[0]["id"])
    card_id = str(planner.plan_cards(plan)[0]["id"])

    for target, prefix in ((page_id, os.path.join("zh", "content")),
                           (card_id, os.path.join("knowledge", "zh"))):
        # regenerate 需要活动 run(它会取消旧 run 另起 regenerate run)
        assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
            config.EXIT_OK
        assert _cli(["regenerate", target, "--repo", repo.path,
                     "--root", root]) == config.EXIT_OK
        run = ctx.db.active_run(pid)
        jobs = ctx.db.jobs(run["id"])
        assert len(jobs) == 1
        assert jobs[0]["path"], f"{target} 入队 path 为空"
        assert jobs[0]["path"].replace(os.sep, "/").startswith(prefix + "/")
        # 收尾, 释放活动 run
        assert _cli(["cancel", "--repo", repo.path, "--root", root]) == \
            config.EXIT_OK


# --------------------------------------------------------------------------- #
# #2 update 的卡片路由: 陈旧卡片按 card 入队(kind='card', knowledge 路径)
# --------------------------------------------------------------------------- #

def test_update_card_stale_queues_card_job(tmp_path):
    repo, root = _published_full(tmp_path)
    # 卡片(build_system)的 source_files 是 [Makefile] —— 动它
    repo.write("Makefile", "build:\n\techo changed\n")
    repo.commit("touch makefile")
    assert _cli(["update", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    ctx = _ctx(repo, root)
    pid = ctx.partition()["id"]
    run = ctx.db.active_run(pid)
    assert run is not None
    jobs = ctx.db.jobs(run["id"])
    card_jobs = [j for j in jobs if j["kind"] == "card"]
    assert card_jobs, f"陈旧卡片未按 card 入队: {[(j['page_id'], j['kind']) for j in jobs]}"
    j = card_jobs[0]
    assert j["card_kind"] in config.CARD_KINDS
    assert j["path"].replace(os.sep, "/").startswith("knowledge/zh/")
    # 裸 next(文本模式)不再 KeyError
    assert _cli(["next", "--repo", repo.path, "--root", root]) == config.EXIT_OK


# --------------------------------------------------------------------------- #
# #3 重跑失败绝不连坐删除已发布页(数据丢失)
# --------------------------------------------------------------------------- #

def test_failed_rerun_keeps_published_page(tmp_path):
    from pwiki_lib import engine
    repo, root = _published_full(tmp_path)
    ctx = _ctx(repo, root)
    pid = ctx.partition()["id"]

    repo.write("services/user/m0.py", "# 改动\nX = 9\n" + "Z = 1\n" * 24)
    repo.commit("edit m0")
    assert _cli(["update", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    run = ctx.db.active_run(pid)
    page_jobs = [j for j in ctx.db.jobs(run["id"]) if j["kind"] == "page"]
    assert page_jobs, "改动 services/user 后应有页面被排队为 stale"
    victim_id = page_jobs[0]["page_id"]
    victim = next(p for p in ctx.db.published_pages(pid)
                  if p["page_id"] == victim_id)
    old_content = victim["content"]
    old_file = os.path.join(root, victim["path"])
    # 该 run 的全部 job 都标记失败(受害者第一个) —— finish 才放行
    for j in ctx.db.jobs(run["id"]):
        args = types.SimpleNamespace(**{
            "repo": repo.path, "root": root, "locale": "zh",
            "page_id": j["page_id"], "error": "writer gave up"})
        assert engine.cmd_fail(args) == config.EXIT_OK
    assert _cli(["finish", "--repo", repo.path, "--root", root, "--yes"]) == \
        config.EXIT_OK
    # 上一版好稿仍在: DB 行在、内容未变、文件在
    row = ctx.db.published_page(pid, victim_id)
    assert row is not None, "重跑失败把已发布行删了(数据丢失)"
    assert row["content"] == old_content
    assert os.path.isfile(old_file), "重跑失败把已发布文件删了(数据丢失)"


# --------------------------------------------------------------------------- #
# #9 卡片改名经 update→finish 落地; 无变化时不再每次空跑
# --------------------------------------------------------------------------- #

def test_card_rename_lands_and_noop_update_is_clean(tmp_path):
    from pwiki_lib import planner
    repo, root = _published_full(tmp_path)
    ctx = _ctx(repo, root)
    pid = ctx.partition()["id"]
    card = next(p for p in ctx.db.published_pages(pid) if p["kind"] == "card")
    old_path = os.path.join(root, card["path"])

    plan = planner.load_plan(ctx)
    pc = next(c for c in planner.plan_cards(plan)
              if str(c.get("id")) == card["page_id"])
    new_title = "构建交付体系改名版"
    pc["title"] = new_title
    planner.save_plan(ctx, plan)

    assert _cli(["update", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    run = ctx.db.active_run(pid)
    assert run is not None, "catalog 漂移要开 run 让 finish 落移动"
    assert _cli(["finish", "--repo", repo.path, "--root", root, "--yes"]) == \
        config.EXIT_OK
    row = ctx.db.published_page(pid, card["page_id"])
    assert row["title"] == new_title, "卡片改名没有落地(kept 重发布曾忽略卡片)"
    assert row["path"].replace(os.sep, "/") == \
        f"knowledge/zh/{new_title}/{new_title}.md"
    assert os.path.isfile(os.path.join(root, row["path"]))
    assert not os.path.isfile(old_path)

    # 收尾: 挂着的 0-job run 已 finish; 此后再无变化 → noop, 不再每次空跑
    assert _cli(["update", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    assert ctx.db.active_run(pid) is None, "无变化 update 留下了空 INCREMENT run"


# --------------------------------------------------------------------------- #
# #11 begin --missing 不再留下 0 任务僵尸 run
# --------------------------------------------------------------------------- #

def test_begin_missing_leaves_no_stray_run(tmp_path):
    from pwiki_lib import cli
    repo, root = _fixture(tmp_path, style="concise")
    ctx = _ctx(repo, root)
    pid = ctx.partition()["id"]
    # 无旧 run: die 退出 3(c086 前置不满足; 曾误用 2 让编排方读成门禁拒绝),
    # 且不留活动 run
    with pytest.raises(SystemExit) as ei:
        _cli(["begin", "--missing", "--repo", repo.path, "--root", root])
    assert ei.value.code == config.EXIT_PRECOND
    assert ctx.db.active_run(pid) is None, "失败的 --missing 留下了僵尸 run"
    # 全量发布后: nothing missing 早退同样不留 run
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == config.EXIT_OK
    _write_all_jobs(repo, root, style="concise")
    assert _cli(["finish", "--repo", repo.path, "--root", root, "--yes"]) == \
        config.EXIT_OK
    assert _cli(["begin", "--missing", "--repo", repo.path,
                 "--root", root]) == config.EXIT_OK
    assert ctx.db.active_run(pid) is None, "nothing missing 留下了僵尸 run"


# --------------------------------------------------------------------------- #
# #12 --prune 才清扫计划外孤儿; 默认手放笔记保留
# --------------------------------------------------------------------------- #

def test_prune_flag_controls_orphan_sweep(tmp_path):
    repo, root = _published_full(tmp_path)
    ctx = _ctx(repo, root)
    pid = ctx.partition()["id"]
    note = os.path.join(root, "zh", "content", "我的部署备注.md")
    os.makedirs(os.path.dirname(note), exist_ok=True)
    with open(note, "w", encoding="utf-8") as fh:
        fh.write("手放笔记, 不在计划里\n")
    page_id = next(p["page_id"] for p in ctx.db.published_pages(pid)
                   if p["kind"] == "page")

    # 默认 finish(经 regenerate 驱动): 笔记保留
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == config.EXIT_OK
    assert _cli(["regenerate", page_id, "--repo", repo.path,
                 "--root", root]) == config.EXIT_OK
    run = ctx.db.active_run(pid)
    claimed = ctx.db.claim_jobs(run["id"], count=1)[0]
    args = types.SimpleNamespace(**{
        "repo": repo.path, "root": root, "locale": "zh",
        "page_id": claimed["page_id"], "file": _write_job_file(root, claimed),
        "summary": "", "key_topics": "", "report": None,
        "lenient": False, "force": False, "claim": claimed["claim_token"]})
    from pwiki_lib import engine
    assert engine.cmd_submit(args) == config.EXIT_OK
    assert _cli(["finish", "--repo", repo.path, "--root", root, "--yes"]) == \
        config.EXIT_OK
    assert os.path.isfile(note), "默认 finish 删了计划外手放文件"

    # --prune: 全量清扫
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == config.EXIT_OK
    assert _cli(["regenerate", page_id, "--repo", repo.path,
                 "--root", root]) == config.EXIT_OK
    run = ctx.db.active_run(pid)
    claimed = ctx.db.claim_jobs(run["id"], count=1)[0]
    args = types.SimpleNamespace(**{
        "repo": repo.path, "root": root, "locale": "zh",
        "page_id": claimed["page_id"], "file": _write_job_file(root, claimed),
        "summary": "", "key_topics": "", "report": None,
        "lenient": False, "force": False, "claim": claimed["claim_token"]})
    assert engine.cmd_submit(args) == config.EXIT_OK
    assert _cli(["finish", "--prune", "--repo", repo.path, "--root", root,
                 "--yes"]) == config.EXIT_OK
    assert not os.path.isfile(note), "--prune 没有清扫孤儿"


def _write_job_file(root, job):
    """给 regenerate 的页/sheet/卡造一份能过门的稿。"""
    if job["kind"] == "card":
        content = (f"---\nkind: {job['card_kind']}\nname: {job['title']}\n---\n\n"
                   "## 1. 总体方案\n\n事实。\n")
    elif job["kind"] == "module_sheet":
        content = f"# {job['title']}\n\n模块负责 X；位于 Y；对外提供 Z。\n"
    else:
        sec = "\n**章节来源**\n- [README.md:1](file://README.md#L1)\n"
        content = (f"# {job['title']}\n\n## 概述\n\n页面摘要。\n"
                   f"## 架构\n\nx\n{sec}## 组件\n\nx\n{sec}"
                   f"## 数据流\n\nx\n{sec}## 配置\n\nx\n{sec}")
    page_file = os.path.join(root, "work", f"regen-{job['page_id']}.md")
    os.makedirs(os.path.dirname(page_file), exist_ok=True)
    with open(page_file, "w", encoding="utf-8") as fh:
        fh.write(content)
    return page_file


# --------------------------------------------------------------------------- #
# #5 卡片 kind 根因: plan 卡片(kind=<种类>)走卡片门禁, 不再按页面门禁误报
# --------------------------------------------------------------------------- #

def test_gate_dispatches_plan_card_by_card_kind(tmp_path):
    from pwiki_lib import gates
    repo = GitRepo(tmp_path / "repo")
    repo.write("Makefile", "all:\n\techo hi\n")
    repo.write("README.md", "# r\n")
    repo.commit("init")
    card = {"id": "card-build-system", "kind": "build_system",
            "title": "构建体系", "prompt": "", "source_files": ["Makefile"],
            "scope": ["**"]}
    good = ("---\nkind: build_system\nname: 构建体系\nsource_files:"
            " [Makefile]\n---\n\n## 1. 总体方案\n\n事实。\n")
    errs, warns = gates.gate_item(good, card, repo.path, "concise",
                                  phase="lining")
    assert not [e for e in errs if e.startswith(("schema/h1", "schema/frontmatter",
                                                "knowledge/"))], errs
    # 卡片带 H1 → 报; frontmatter 声明不存在的源 → 报(#34 保证回补)
    bad = ("---\nkind: build_system\nname: 构建体系\nsource_files:"
           " [nope/deleted.py]\n---\n\n# 我是不该有的 H1\n\nx\n")
    errs, _ = gates.gate_item(bad, card, repo.path, "concise", phase="lining")
    assert any(e.startswith("knowledge/h1") for e in errs), errs
    assert any("source_files 越界或不存在" in e for e in errs), errs


# --------------------------------------------------------------------------- #
# #4 sqlite Row 任务的 D15 分档(source_files 真值参与, 不再恒 standard)
# --------------------------------------------------------------------------- #

def test_gate_band_uses_row_source_files(tmp_path):
    from pwiki_lib import gates
    repo = GitRepo(tmp_path / "repo")
    for i in range(6):
        repo.write(f"src/f{i}.py", "\n".join(f"X{i} = {n}" for n in range(30))
                   + "\n")
    repo.commit("init")

    def row_for(n_sources):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        cols = ("kind TEXT, module TEXT, sheet TEXT, card_kind TEXT, "
                "path TEXT, prompt TEXT, source_files TEXT, article_type TEXT, "
                "slug TEXT, title TEXT, objective TEXT, modules TEXT, "
                "summary TEXT, key_topics TEXT, scope TEXT, "
                "required_sections TEXT, title_check INTEGER, pack_key TEXT")
        conn.execute(f"CREATE TABLE page_job ({cols})")
        srcs = json.dumps([f"src/f{i}.py" for i in range(n_sources)])
        conn.execute("INSERT INTO page_job(kind,title,source_files,"
                     "article_type,pack_key) VALUES('page','T',?,'','')", (srcs,))
        return conn.execute("SELECT * FROM page_job").fetchone()

    def content(n_cites):
        parts = [f"# T\n\n## 概述\n\nx\n"]
        for i in range(n_cites):
            parts.append(f"- [f{i % 6}](file://src/f{i % 6}.py#L1)\n")
        return "".join(parts)

    # thin_leaf(2 源, 分档 40-70): 50 条引用过门
    errs, _ = gates.gate_item(content(50), row_for(2), repo.path, "deep",
                              phase="lining")
    assert not any(e.startswith("size/citations") for e in errs), errs
    # standard(5 源, 分档 60-90): 同样 50 条引用现在要报
    errs, _ = gates.gate_item(content(50), row_for(5), repo.path, "deep",
                              phase="lining")
    assert any("分档 60-90" in e for e in errs), errs


# --------------------------------------------------------------------------- #
# #6/#35 validate --content: concise 自家产出不再误报; 断链是错误
# --------------------------------------------------------------------------- #

def test_validate_content_clean_on_published_concise(tmp_path):
    repo, root = _published_full(tmp_path)
    assert _cli(["validate", "--content", "--repo", repo.path,
                 "--root", root]) == config.EXIT_OK


def test_validate_content_broken_link_is_error(tmp_path):
    from pwiki_lib import gates
    repo, root = _published_full(tmp_path)
    ctx = _ctx(repo, root)
    pid = ctx.partition()["id"]
    page = next(p for p in ctx.db.published_pages(pid) if p["kind"] == "page")
    target = os.path.join(root, page["path"])
    with open(target, encoding="utf-8") as fh:
        text = fh.read()
    with open(target, "w", encoding="utf-8") as fh:
        fh.write(text + "\n[断链](../不存在的页.md)\n")
    from pwiki_lib import planner
    code = _cli(["validate", "--content", "--repo", repo.path, "--root", root])
    assert code == config.EXIT_GATE, "断链应使 validate --content 失败"


# --------------------------------------------------------------------------- #
# #19 单行差一钳位(修 + 发布层)
# --------------------------------------------------------------------------- #

def test_off_by_one_single_line_clamped(tmp_path):
    from pwiki_lib import gates
    repo = GitRepo(tmp_path / "repo")
    repo.write("a.py", "a = 1\nb = 2\nc = 3\nd = 4\n")   # 4 行
    repo.commit("init")
    content = "[x](file://a.py#L5)\n"
    errs, _ = gates.validate_citations(content, repo.path)
    assert any(e.startswith("citation/off-by-one") for e in errs), errs
    fixed, n = gates.fix_off_by_one(content, repo.path)
    assert n == 1
    assert "file://a.py#L4" in fixed and "#L5" not in fixed


# --------------------------------------------------------------------------- #
# #20/#29 %2520 真改写; 代码栅栏内的链接不动; '[EOF' 不再关停归一化
# --------------------------------------------------------------------------- #

def test_normalize_links_percent2520_fences_eof(tmp_path):
    from pwiki_lib.publish import normalize_links
    all_paths = {"dir/a b.md", "dir/ok.md", "zh/content/父.md"}
    content = ("[see](dir/a%2520b.md)\n[ok](dir/ok.md)\n\n"
               "```markdown\n[示例](dir/ok.md)\n```\n"
               "提及 [EOF 标记]\n[父](zh/content/父.md)\n")
    out, n = normalize_links(content, "zh/content/x.md", all_paths)
    assert "dir/a%20b.md" in out and "a%2520b.md" not in out, out
    assert n >= 1
    assert out.count("```markdown\n[示例](dir/ok.md)\n```") == 1, "栅栏内被改写"
    assert "[EOF 标记]" in out


# --------------------------------------------------------------------------- #
# #13/#21 planner: 重复 module id / 兄弟同名 / 卡片同名都是错误
# --------------------------------------------------------------------------- #

def test_validate_plan_rejects_collisions(tmp_path):
    from pwiki_lib import planner
    repo = GitRepo(tmp_path / "repo")
    repo.write("a/one.py", "x = 1\n")
    repo.write("b/two.py", "y = 2\n")
    repo.write("README.md", "# r\n")
    repo.commit("init")
    ctx = _ctx(repo, tmp_path / "root")
    plan = {
        "schema_version": 3, "style": "concise",
        "modules": [
            {"id": "user-service", "title": "A", "dir": "a/", "path": "/a",
             "scope": ["a/"], "depends_on": [], "related_to": []},
            {"id": "user-service", "title": "B", "dir": "b/", "path": "/b",
             "scope": ["b/"], "depends_on": [], "related_to": []},
        ],
        "pages": [
            {"id": "p1", "title": "配置管理", "parent": "", "kind": "page"},
            {"id": "p2", "title": "配置管理", "parent": "", "kind": "page"},
        ],
        "cards": [
            {"id": "c1", "kind": "build_system", "title": "构建体系",
             "scope": ["**"], "source_files": ["README.md"]},
            {"id": "c2", "kind": "logging_system", "title": "构建体系",
             "scope": ["**"], "source_files": ["README.md"]},
        ],
    }
    errors, _ = planner.validate_plan(ctx, plan, profile="lite", strict=False)
    assert any("重复 module id" in e for e in errors), errors
    assert any("兄弟标题冲突" in e for e in errors), errors
    assert any("卡片标题冲突" in e for e in errors), errors


# --------------------------------------------------------------------------- #
# #14 依赖卡 slug 撞车("@foo/bar" vs "foo-bar")也要唯一 id
# --------------------------------------------------------------------------- #

def test_seed_cards_dep_slug_collision(tmp_path):
    from pwiki_lib import planner
    inv = {
        "repo": str(tmp_path), "files": [
            {"path": "package.json", "lang": "json", "lines": 5, "bytes": 50},
            {"path": "README.md", "lang": "markdown", "lines": 2, "bytes": 20},
        ],
        "manifests": [{
            "path": "package.json", "type": "npm",
            "dependencies": {"@foo/bar": "1", "foo-bar": "2", "react": "3"},
        }],
    }
    cards = planner.seed_cards(inv, "zh")
    ids = [c["id"] for c in cards if c["id"].startswith("dep-")]
    assert len(ids) == len(set(ids)) and len(ids) >= 2, ids


# --------------------------------------------------------------------------- #
# #15/#16 db: 认领竞态如实报失配; partition 并发首建幂等
# --------------------------------------------------------------------------- #

def test_set_job_state_stale_token_returns_false(tmp_path):
    from pwiki_lib.db import WikiDB
    db = WikiDB(str(tmp_path / "root"))
    rid = db.create_run(1, "generate", "start", "FULL", "WAITING", "c", "",
                        {}, style="concise")
    db.queue_jobs(rid, [{"id": "p1", "title": "T", "kind": "page"}],
                  style="concise")
    job = db.claim_jobs(rid, count=1)[0]
    token = job["claim_token"]
    # 租约被回收并重认领 → 旧 token 作废
    db.conn.execute("UPDATE page_job SET claim_token='other' "
                    "WHERE run_id=? AND page_id=?", (rid, "p1"))
    ok = db.set_job_state(rid, "p1", "completed", content="x\n",
                          claim_token=token)
    assert ok is False, "过期令牌的 UPDATE 命中 0 行却返回了 True"
    # 稿件没有静默写进别人的行
    row = db.job(rid, "p1")
    assert row["state"] == "processing" and row["claim_token"] == "other"


def test_partition_idempotent_under_repeat(tmp_path):
    from pwiki_lib.db import WikiDB
    db = WikiDB(str(tmp_path / "root"))
    a = db.partition("main", "zh")
    b = db.partition("main", "zh")
    assert a["id"] == b["id"]
    # 直接预插一行(模拟并发赢家)再走 partition → OR IGNORE 回读同一行
    db.conn.execute(
        "INSERT OR IGNORE INTO partition(branch,locale,created_at,"
        "last_accessed_at) VALUES('main','en',?,?)", ("t", "t"))
    c = db.partition("main", "en")
    d = db.partition("main", "en")
    assert c["id"] == d["id"]


# --------------------------------------------------------------------------- #
# #10 重跑 plan --themes 不再丢 module_sheet; #61 concise 不盖章九节模板
# --------------------------------------------------------------------------- #

def test_themes_replan_preserves_sheets(tmp_path):
    from test_themes import _svc_fixture, _ctx as _themes_ctx
    repo = _svc_fixture(tmp_path)
    root = str(tmp_path / "root")
    for argv in (["init", "--style", "concise", "--repo", repo.path,
                  "--root", root],
                 ["scan", "--repo", repo.path, "--root", root],
                 ["modules", "--repo", repo.path, "--root", root],
                 ["plan", "--sheets", "lite", "--repo", repo.path,
                  "--root", root],
                 ["plan", "--themes", "--repo", repo.path, "--root", root]):
        assert _cli(argv) in (config.EXIT_OK, config.EXIT_GATE), argv
    ctx = _themes_ctx(repo, root)
    from pwiki_lib import planner
    plan = planner.load_plan(ctx)
    sheets = [p for p in planner.plan_pages(plan)
              if p.get("kind") == "module_sheet"]
    assert sheets, "重跑 plan --themes 把 module_sheet 页弄丢了"
    # concise: 主题页不盖九节深文模板
    theme_pages = [p for p in planner.plan_pages(plan)
                   if p.get("pack_key")]
    assert all(not p.get("required_sections") for p in theme_pages), \
        "concise 主题页不应携带 deep 九节模板"
