"""2026-11-09 全仓 max 审查 pwiki 分派回修(F06/F30/F36/F47)的回归测试。

对应报告: docs/code-review/2026-11-09-full-repo-max.md(JSON 同目录)。
F37(qwiki-port 检索无缓存)为记录在案的有意权衡, skipped-intentional。
"""

from __future__ import annotations

import os
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from pwiki_lib import config

from test_e2e import _fixture, _write_all_jobs
from conftest import _cli, _ctx


def _ctx_raw(tmp_path):
    """无 config.json 的裸 ctx(test_review_fixes3 同款)。"""
    import subprocess
    from pwiki_lib.config import Ctx
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "src" / "a.py").write_text("x = 1\n" * 20, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)
    root = tmp_path / "wiki"
    root.mkdir()
    ctx = Ctx(types.SimpleNamespace(repo=str(repo), root=str(root),
                                    locale="zh"))
    ctx.config = {"export_style": "legacy"}
    return ctx


# --------------------------------------------------------------------------- #
# F06: cmd_update 样式漂移"过新门禁 → 重盖章"分支曾引用未绑定 pid → NameError
# --------------------------------------------------------------------------- #

def test_f06_style_drift_passing_rows_restamp(tmp_path, capsys):
    """deep 发布 → 改 concise → update: 过 concise 门禁的行重盖章而非崩溃。

    修复前该分支(测试从未覆盖的 else 臂)引用未绑定的 pid, 一次 NameError
    让 update 在建 run 之前当场崩, 样式漂移永不收敛。"""
    from pwiki_lib import planner
    repo, root = _fixture(tmp_path, style="deep")
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    _write_all_jobs(repo, root, style="deep")
    # deep 的行数/引用分档在短稿上会失败 —— --force 全放发布(同
    # test_full_lifecycle_deep 的口径)
    assert _cli(["finish", "--force", "--repo", repo.path, "--root", root,
                 "--yes"]) == config.EXIT_OK
    ctx = _ctx(repo, root)
    pid = ctx.partition()["id"]
    assert {p["style"] for p in ctx.db.published_pages(pid)} == {"deep"}

    plan = planner.load_plan(ctx)
    plan["style"] = "concise"
    planner.save_plan(ctx, plan)
    assert _cli(["update", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    # 全部行过 concise 门禁(concise 的尺寸/注入物类均为警告级) → 全部
    # 重盖章, 漂移一轮收敛
    rows = list(ctx.db.published_pages(pid))
    assert rows and {p["style"] for p in rows} == {"concise"}, \
        "过新门禁的行未被重盖章(或被误重排)"
    # 二轮 update: 无漂移、无差异 → noop, 不再对全库重跑门禁
    capsys.readouterr()
    assert _cli(["update", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    assert "no update required" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# F30: 图层提示词的 deep 预设事实(mermaid 区间/cite 块)曾手写, 与门禁漂移
# --------------------------------------------------------------------------- #

def _layers_state():
    return {"profile": "standard", "articles": [
        {"slug": "ov", "title": "总览", "type": "overview",
         "objective": "o", "modules": ["/"], "depth": 0, "parent": "",
         "need_further_planning": False},
    ]}


def test_f30_layer_prompt_mermaid_band_from_preset(tmp_path, monkeypatch):
    """改预设 mermaid band → 生成的提示包含新值(gates 与提示同一来源)。"""
    from pwiki_lib import layers
    from pwiki_lib.styles import STYLE_PRESETS
    ctx = _ctx_raw(tmp_path)
    plan = {"style": "deep",
            "modules": [{"id": "root", "path": "/", "scope": ["src/"]}],
            "limits": {"max_depth": 1}}
    monkeypatch.setitem(STYLE_PRESETS["deep"], "mermaid", (7, 10))
    pages = layers.materialize_layers(ctx, plan, _layers_state())
    assert pages, "大纲未物化出页面"
    assert "7-10" in pages[0]["prompt"], pages[0]["prompt"]
    assert "5-8" not in pages[0]["prompt"], \
        f"提示词仍带旧预设数字: {pages[0]['prompt']}"


def test_f30_layer_prompt_arm_follows_preset_keys(tmp_path, monkeypatch):
    """分支键走预设而非 style 字面 —— cite_block_required=False 时 deep
    也不再要求自带 cite 块(第三个风格不静默继承 deep 散文)。"""
    from pwiki_lib import layers
    from pwiki_lib.styles import STYLE_PRESETS
    ctx = _ctx_raw(tmp_path)
    plan = {"style": "deep",
            "modules": [{"id": "root", "path": "/", "scope": ["src/"]}],
            "limits": {"max_depth": 1}}
    monkeypatch.setitem(STYLE_PRESETS["deep"], "cite_block_required", False)
    pages = layers.materialize_layers(ctx, plan, _layers_state())
    assert "<cite>" not in pages[0]["prompt"], pages[0]["prompt"]
    assert "顶部引用清单" in pages[0]["prompt"], pages[0]["prompt"]


# --------------------------------------------------------------------------- #
# F36: publish_run 逐行 INSERT OR REPLACE(含字节相同行)→ 单事务 + 未变跳写
# --------------------------------------------------------------------------- #

def _count_publish_page(monkeypatch):
    """统计 WikiDB.publish_page 实际落库次数(仍转发真实写)。"""
    from pwiki_lib.db import WikiDB
    calls = []
    orig = WikiDB.publish_page

    def counting(self, pid, job_row, content, run_id):
        calls.append(job_row["page_id"])
        return orig(self, pid, job_row, content, run_id)

    monkeypatch.setattr(WikiDB, "publish_page", counting)
    return calls


def test_f36_byte_identical_rerun_writes_nothing(tmp_path, monkeypatch):
    """增量 update 重排的页提交字节相同稿 → publish_page 零调用
    (kept 行与完成行都未变不落库; 文件树侧"先比后写"的 DB 对齐)。"""
    repo, root = _fixture(tmp_path, style="concise")
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    _write_all_jobs(repo, root, style="concise")
    assert _cli(["finish", "--repo", repo.path, "--root", root,
                 "--yes"]) == config.EXIT_OK
    ctx = _ctx(repo, root)
    pid = ctx.partition()["id"]
    n_before = len(list(ctx.db.published_pages(pid)))
    assert n_before > 1

    repo.write("README.md", "# 全周期仓库\n\nchanged。\n两个服务与一个共享包。\n")
    repo.commit("touch cited readme")
    assert _cli(["update", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    run = ctx.db.active_run(pid)
    assert run is not None
    _write_all_jobs(repo, root, style="concise")   # 与首发逐字节相同的模板稿

    calls = _count_publish_page(monkeypatch)
    assert _cli(["finish", "--repo", repo.path, "--root", root,
                 "--yes"]) == config.EXIT_OK
    assert calls == [], f"字节相同的重发布仍写库: {calls}"
    assert len(list(ctx.db.published_pages(pid))) == n_before, \
        "零写路径弄丢了已发布行"


def test_f36_changed_content_still_lands(tmp_path, monkeypatch):
    """内容真变的完成行仍落库 —— 跳写只挡字节相同, 不挡真更新。"""
    from pwiki_lib import engine
    repo, root = _fixture(tmp_path, style="concise")
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    _write_all_jobs(repo, root, style="concise")
    assert _cli(["finish", "--repo", repo.path, "--root", root,
                 "--yes"]) == config.EXIT_OK
    ctx = _ctx(repo, root)
    pid = ctx.partition()["id"]

    repo.write("README.md", "# 全周期仓库\n\nchanged2。\n两个服务与一个共享包。\n")
    repo.commit("touch cited readme again")
    assert _cli(["update", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    run = ctx.db.active_run(pid)
    assert run is not None

    # 逐个认领, 按种类提交带 v2 标记的合格稿(与 _write_all_jobs 同模板)
    markers = {"card": "事实 v2。", "module_sheet": "（v2）",
               "page": "页面摘要 v2。"}
    completed = []
    while True:
        claimed = ctx.db.claim_jobs(run["id"], count=1)
        if not claimed:
            break
        j = claimed[0]
        if j["kind"] == "card":
            content = (f"---\nkind: {j['card_kind']}\nname: {j['title']}\n"
                       f"---\n\n## 1. 总体方案\n\n事实 v2。\n")
        elif j["kind"] == "module_sheet":
            content = f"# {j['title']}\n\n模块负责 X；位于 Y；对外提供 Z（v2）。\n"
        else:
            sec = "\n**章节来源**\n- [README.md:1](file://README.md#L1)\n"
            content = (f"# {j['title']}\n\n## 概述\n\n页面摘要 v2。\n"
                       f"## 架构\n\nx\n{sec}\n## 组件\n\nx\n{sec}"
                       f"## 数据流\n\nx\n{sec}## 配置\n\nx\n{sec}")
        page_file = os.path.join(root, "work", f"{j['page_id']}.md")
        os.makedirs(os.path.dirname(page_file), exist_ok=True)
        with open(page_file, "w", encoding="utf-8") as fh:
            fh.write(content)
        args = types.SimpleNamespace(
            **{"repo": repo.path, "root": root, "locale": "zh",
               "page_id": j["page_id"], "file": page_file, "summary": "",
               "key_topics": f"{j['page_id']}-主题", "report": None,
               "lenient": False, "force": False, "claim": j["claim_token"]})
        assert engine.cmd_submit(args) == config.EXIT_OK, j["page_id"]
        completed.append((j["page_id"], j["kind"]))

    calls = _count_publish_page(monkeypatch)
    assert _cli(["finish", "--repo", repo.path, "--root", root,
                 "--yes"]) == config.EXIT_OK
    assert set(calls) == {pid_ for pid_, _ in completed}, \
        f"变化行未全量落库: {calls} vs {completed}"
    by_id = {p["page_id"]: p for p in ctx.db.published_pages(pid)}
    for page_id, kind in completed:
        assert markers[kind] in (by_id[page_id]["content"] or ""), \
            f"{page_id} 的新稿未落库"


def test_f36_republish_batch_is_atomic(tmp_path, monkeypatch):
    """发布批单事务: 中途失败整体回滚, 不留半发布的库。"""
    from pwiki_lib import planner, publish
    from pwiki_lib.db import WikiDB
    repo, root = _fixture(tmp_path, style="concise")
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    _write_all_jobs(repo, root, style="concise")
    assert _cli(["finish", "--repo", repo.path, "--root", root,
                 "--yes"]) == config.EXIT_OK
    ctx = _ctx(repo, root)
    pid = ctx.partition()["id"]

    # 目录漂移(改名) → kept 循环有真写; 在 publish_modules 处炸掉
    plan = planner.load_plan(ctx)
    target = next(p for p in plan["pages"]
                  if p.get("kind") != "module_sheet")
    old_title = str(target["title"])
    target["title"] = old_title + "总"
    planner.save_plan(ctx, plan)
    assert _cli(["update", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    run = ctx.db.active_run(pid)
    assert run is not None

    def boom(self, pid_, modules):
        raise RuntimeError("boom")

    monkeypatch.setattr(WikiDB, "publish_modules", boom)
    with pytest.raises(RuntimeError, match="boom"):
        publish.publish_run(ctx, run, planner.load_plan(ctx), "concise")
    assert not ctx.db.conn.in_transaction, "失败后连接仍挂着未决事务"
    assert ctx.db._txn_depth == 0
    titles = {p["title"] for p in ctx.db.published_pages(pid)}
    assert old_title in titles and (old_title + "总") not in titles, \
        "半发布的改名未随整批回滚"

    # 回滚没留残局: 撤掉故障注入, 真 finish 仍可完成目录移动
    monkeypatch.undo()
    assert _cli(["finish", "--repo", repo.path, "--root", root,
                 "--yes"]) == config.EXIT_OK
    titles = {p["title"] for p in ctx.db.published_pages(pid)}
    assert (old_title + "总") in titles


def test_f36_nested_txn_helpers(tmp_path):
    """begin/commit/rollback 嵌套感知: 外层 BEGIN IMMEDIATE, 内层
    SAVEPOINT; 嵌套 rollback 整笔回滚并清零深度。"""
    from pwiki_lib.db import WikiDB
    wdb = WikiDB(str(tmp_path / "root"))
    # 内层 commit 只 RELEASE —— 外层事务仍在, 内层写随外层回滚消失
    wdb.begin()
    wdb.begin()
    assert wdb.conn.in_transaction
    wdb.meta_set("k", "v")
    wdb.commit()
    assert wdb.conn.in_transaction, "内层 commit 误提交了外层事务"
    wdb.rollback()
    assert not wdb.conn.in_transaction
    assert wdb.meta_get("k") == ""
    # 嵌套 rollback: 全部回滚, 深度清零, 之后事务仍可用
    wdb.begin()
    wdb.begin()
    wdb.meta_set("k2", "v2")
    wdb.rollback()
    assert not wdb.conn.in_transaction
    assert wdb.meta_get("k2") == ""
    wdb.begin()
    wdb.meta_set("k3", "v3")
    wdb.commit()
    assert wdb.meta_get("k3") == "v3"
    wdb.close()


# --------------------------------------------------------------------------- #
# F47: db_meta.schema_version 只写不读 → 接成真开库门禁(选 a: 轻量接线)
# --------------------------------------------------------------------------- #

def test_f47_reopen_current_version_quiet(tmp_path):
    from pwiki_lib import db as dbmod
    root = str(tmp_path / "root")
    dbmod.WikiDB(root).close()
    wdb = dbmod.WikiDB(root)     # 同版本重开: 安静放行
    assert wdb.meta_get("schema_version") == dbmod.SCHEMA_VERSION
    wdb.close()


def test_f47_legacy_db_without_stamp_restamped(tmp_path):
    """db_meta 之前的老库(无戳)按可隐式迁移放行并补戳。"""
    from pwiki_lib import db as dbmod
    root = str(tmp_path / "root")
    w = dbmod.WikiDB(root)
    w.conn.execute("DELETE FROM db_meta WHERE key='schema_version'")
    w.close()
    wdb = dbmod.WikiDB(root)
    assert wdb.meta_get("schema_version") == dbmod.SCHEMA_VERSION
    wdb.close()


def test_f47_unknown_version_rejected(tmp_path):
    """未来/未知版本明确报错, 不静默误读别代 schema。"""
    from pwiki_lib import db as dbmod
    root = str(tmp_path / "root")
    w = dbmod.WikiDB(root)
    w.meta_set("schema_version", "v99")
    w.close()
    with pytest.raises(dbmod.SchemaVersionError) as ei:
        dbmod.WikiDB(root)
    assert "v99" in str(ei.value)


def test_f47_ctx_load_dies_with_precond(tmp_path):
    """CLI 面: 开库版本门禁给一行干净报错 + EXIT_PRECOND, 不留裸 traceback。"""
    from pwiki_lib import db as dbmod
    from pwiki_lib.config import Ctx
    root = tmp_path / "root"
    w = dbmod.WikiDB(str(root))
    w.meta_set("schema_version", "v99")
    w.close()
    ctx = Ctx(types.SimpleNamespace(repo=str(tmp_path), root=str(root),
                                    locale="zh"))
    with pytest.raises(SystemExit) as ei:
        ctx.load()
    assert ei.value.code == config.EXIT_PRECOND
