"""第八轮 max 审查回修回归测试。

批次①(发现 1-17): 静默损坏 5 + 不收敛 update 循环 4 + 状态机竞态 4 +
r7 自伤回归 4。r8 #3(read_published_utf8 OSError)的契约更新随
test_review_fixes8.test_r7_62 就地更新, 不在此重复。
"""
from __future__ import annotations

import json
import os
import sys

import pytest

from conftest import _cli, _ctx
from test_e2e import _fixture
from test_review_fixes8 import _published_fixture

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pwiki_lib import config  # noqa: E402


# --------------------------------------------------------------------------- #
# r8 #1 yamlite 引号键回退
# --------------------------------------------------------------------------- #

def test_r8_01_yamlite_quoted_colon_roundtrip():
    """含 ':' 的带引号列表项/键往返精确 —— pk 回退曾把 `"/api/:id"` 拆成
    单键 dict, 自家 dump→load 静默损毁计划。"""
    from pwiki_lib.yamlite import yaml_dump, yaml_load
    plan = {"aliases": [{"patterns": ["/api/:id", "v1"], "module": "gateway"}],
            "required_sections": ["Overview: why", "用法"]}
    assert yaml_load(yaml_dump(plan)) == plan
    v = yaml_load('list:\n  - "/api/:id"\n  - plain\n')
    assert v["list"] == ["/api/:id", "plain"], v
    # 引号键带冒号: 走 qk, 正常
    assert yaml_load('"/api/:id": v\n') == {"/api/:id": "v"}
    # 引号开头而引号键不成立: 序列条目里的半截引号串报错(不再当裸标量吞),
    # 映射行报错(不再 pk 回退拆键)
    with pytest.raises(ValueError):
        yaml_load('list:\n  - "a:b" oops\n')
    with pytest.raises(ValueError):
        yaml_load('m:\n  "/api/:id"\n')


def test_r8_01_yamlite_quoted_escape_still_scalar():
    """完整引号标量(含 '' 转义)不受 r8 #1 门影响, 仍是标量。"""
    from pwiki_lib.yamlite import yaml_load
    assert yaml_load("list:\n  - 'don''t # x'\n")["list"] == ["don't # x"]
    assert yaml_load('list:\n  - "a\\:b"\n')["list"] == ["a\\:b"]


# --------------------------------------------------------------------------- #
# r8 #2 merge_manual 不再就地改写引擎输出
# --------------------------------------------------------------------------- #

def test_r8_02_merge_manual_keeps_engine_output_pristine():
    """基线快照的原料(result)不被合并就地污染 —— 否则 manual-baseline 把
    手工值记成引擎输出, 二次生成手工编辑静默回退。"""
    from pwiki_lib.themes import merge_manual
    new = [{"id": "p1", "title": "E", "prompt": "e"}]
    old = [{"id": "p1", "title": "M", "prompt": "e", "kind": "page"}]
    prev = {"p1": {"title": "E", "prompt": "e", "kind": "page"}}
    merged = merge_manual(new, old, ("title", "prompt"), engine_prev=prev)
    assert merged[0]["title"] == "M"            # 手工编辑被保留
    assert new[0] == {"id": "p1", "title": "E", "prompt": "e"}, \
        "引擎输出 dict 被就地改写 —— save_manual_baseline 会把手改值当引擎值"


# --------------------------------------------------------------------------- #
# r8 #4/#5 目录剥除: CRLF 与条目间空行
# --------------------------------------------------------------------------- #

def test_r8_04_05_toc_strip_crlf_and_blank_lines():
    from pwiki_lib.gates import strip_injected_layers
    crlf = ("<cite>x</cite>\r\n\r\n## 目录\r\n\r\n1. [a](#a)\r\n"
            "2. [b](#b)\r\n\r\n正文\r\n")
    out = strip_injected_layers(crlf)
    assert "目录" not in out and "[a](#a)" not in out and "正文" in out, out
    blank = "导语\n\n## 目录\n\n1. a\n\n2. b\n\n3. c\n\n正文"
    out2 = strip_injected_layers(blank)
    assert "2. b" not in out2 and "3. c" not in out2 and "正文" in out2, out2
    plain = ("<cite>x</cite>\n\n## 目录\n\n1. [a](#a)\n2. [b](#b)\n\n正文\n")
    out3 = strip_injected_layers(plain)
    assert "目录" not in out3 and "正文" in out3, out3


# --------------------------------------------------------------------------- #
# r8 #6 清空声明收敛(kept 键存在语义)
# --------------------------------------------------------------------------- #

def test_r8_06_cleared_declaration_converges(tmp_path):
    """显式 `source_files: []` 经 kept(跳过的重排队)同步进 DB 行, 下轮
    update 不再 sources_redeclared —— `or` 回退旧行值曾重开死循环。"""
    from pwiki_lib import planner
    repo, root = _published_fixture(tmp_path)
    ctx = _ctx(repo, root)
    pid = ctx.partition()["id"]
    plan = planner.load_plan(ctx)
    item = next(p for p in plan["pages"]
                if p.get("source_files")
                and p.get("kind") not in ("module_sheet", "card"))
    pid_page = str(item["id"])
    item["source_files"] = []
    planner.save_plan(ctx, plan)
    assert _cli(["update", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    assert _cli(["skip", pid_page, "--reason", "r8e2e",
                 "--repo", repo.path, "--root", root]) == config.EXIT_OK
    assert _cli(["finish", "--yes", "--repo", repo.path,
                 "--root", root]) == config.EXIT_OK
    row = ctx.db.published_page(pid, pid_page)
    assert json.loads(row["source_files"]) == [], row["source_files"]
    # 收敛: 再 update 是 noop, 不再建 run
    assert _cli(["update", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    assert ctx.db.active_run(pid) is None


# --------------------------------------------------------------------------- #
# r8 #7 article_type 双拼写三口径一致
# --------------------------------------------------------------------------- #

def test_r8_07_article_type_dual_spelling_insert(tmp_path):
    from pwiki_lib.db import WikiDB
    db = WikiDB(str(tmp_path / "root"))
    rid = db.create_run(1, "generate", "start", "FULL",
                        "DOCUMENT_GENERATING", "c", "", {}, style="concise")
    db.queue_jobs(rid, [{"id": "p1", "title": "T", "kind": "page",
                         "article_type": "feature"}], style="concise")
    assert db.job(rid, "p1")["article_type"] == "feature"


# --------------------------------------------------------------------------- #
# r8 #8/#9 prompt 对账: str(None) 与 clip 尾换行
# --------------------------------------------------------------------------- #

def test_r8_08_09_prompt_compare_normalizes(tmp_path):
    """裸 `prompt:`(None)与升级行的 clip 尾换行差异都不再触发
    prompt_changed(整页 LLM 重写循环)。"""
    from pwiki_lib import planner
    repo, root = _published_fixture(tmp_path)
    ctx = _ctx(repo, root)
    pid = ctx.partition()["id"]
    plan = planner.load_plan(ctx)
    pages = [p for p in plan["pages"]
             if p.get("kind") not in ("module_sheet", "card")]
    # #9: 计划块标量 clip 值(尾 \n) vs 升级前落库的 rstrip 值
    pages[0]["prompt"] = "写一篇\n"
    ctx.db.conn.execute(
        "UPDATE page SET prompt='写一篇' WHERE partition_id=? AND page_id=?",
        (pid, str(pages[0]["id"])))
    # #8: 裸 prompt: 键(yamlite 保留 None) vs 行 ''
    pages[1]["prompt"] = None
    ctx.db.conn.execute(
        "UPDATE page SET prompt='' WHERE partition_id=? AND page_id=?",
        (pid, str(pages[1]["id"])))
    planner.save_plan(ctx, plan)
    assert _cli(["update", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    assert ctx.db.active_run(pid) is None, "prompt 对账误报 → 无谓重写循环"


# --------------------------------------------------------------------------- #
# r8 #10/#13 LINING/PAUSED 不派发
# --------------------------------------------------------------------------- #

def test_r8_10_claim_rejects_lining_and_paused(tmp_path):
    from pwiki_lib.db import WikiDB
    db = WikiDB(str(tmp_path / "root"))
    item = [{"id": "p1", "title": "T", "kind": "page"}]
    rid = db.create_run(1, "generate", "start", "FULL",
                        "DOCUMENT_GENERATING", "c", "", {}, style="concise")
    db.queue_jobs(rid, item, style="concise")
    assert db.update_run_guarded(rid, ("DOCUMENT_GENERATING",),
                                 state="LINING") is True
    assert db.claim_jobs(rid) == []          # finish 窗口不派发
    assert db.update_run_guarded(rid, ("LINING",),
                                 state="DOCUMENT_GENERATING") is True
    assert db.claim_jobs(rid) != []          # 回到派发态恢复
    db.update_run_guarded(rid, ("DOCUMENT_GENERATING",), state="PAUSED")
    assert db.claim_jobs(rid) == []          # 暂停即停派发


def test_r8_10_next_reports_lining(tmp_path, capsys):
    """next 对 LINING run 报 lining/EXIT_PRECOND, 不再误报 complete。"""
    repo, root = _fixture(tmp_path)
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    capsys.readouterr()          # begin 的输出不计入
    ctx = _ctx(repo, root)
    pid = ctx.partition()["id"]
    run = ctx.db.active_run(pid)
    assert ctx.db.update_run_guarded(run["id"],
                                     ("DOCUMENT_GENERATING",),
                                     state="LINING") is True
    rc = _cli(["next", "--json", "--repo", repo.path, "--root", root])
    assert rc == config.EXIT_PRECOND
    out = capsys.readouterr().out
    assert json.loads(out)["status"] == "lining", out


# --------------------------------------------------------------------------- #
# r8 #11/#17 排队窗口中断 → 取消三连属主
# --------------------------------------------------------------------------- #

def test_r8_17_begin_queue_interrupt_cancels_with_audit(tmp_path, monkeypatch):
    """begin 排队段中断: run 落 CANCELLED 且留 run_cancelled 审计行
    (手抄守卫写曾跳过 release/审计)。"""
    from pwiki_lib.db import WikiDB
    repo, root = _fixture(tmp_path)

    def boom(self, run_id, items, style=None):
        raise KeyboardInterrupt
    monkeypatch.setattr(WikiDB, "queue_jobs", boom)
    with pytest.raises(KeyboardInterrupt):
        _cli(["begin", "--repo", repo.path, "--root", root])
    ctx = _ctx(repo, root)
    pid = ctx.partition()["id"]
    assert ctx.db.active_run(pid) is None
    last = ctx.db.last_run(pid)
    assert last["state"] == "CANCELLED", last["state"]
    n = ctx.db.conn.execute(
        "SELECT COUNT(*) c FROM run_log WHERE run_id=? AND "
        "event='run_cancelled'", (last["id"],)).fetchone()["c"]
    assert n == 1


def test_r8_11_regenerate_queue_interrupt_cancels(tmp_path, monkeypatch):
    """regenerate 排队段中断: 新 run 落 CANCELLED —— 曾留 0-job
    DOCUMENT_GENERATING 活动态(next 假 complete, finish 空完成)。"""
    from pwiki_lib import planner
    from pwiki_lib.db import WikiDB
    repo, root = _fixture(tmp_path)
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    ctx = _ctx(repo, root)
    plan = planner.load_plan(ctx)
    some_page = str(plan["pages"][0]["id"])

    def boom(self, run_id, items, style=None):
        raise KeyboardInterrupt
    monkeypatch.setattr(WikiDB, "queue_jobs", boom)
    with pytest.raises(KeyboardInterrupt):
        _cli(["regenerate", some_page, "--repo", repo.path, "--root", root])
    pid = ctx.partition()["id"]
    assert ctx.db.active_run(pid) is None
    last = ctx.db.last_run(pid)
    assert last["kind"] == "regenerate"
    assert last["state"] == "CANCELLED", last["state"]


# --------------------------------------------------------------------------- #
# r8 #12 和解排队窗口并发取消 → retry(不消费冲突记录)
# --------------------------------------------------------------------------- #

def test_r8_12_reconcile_queue_window_cancel_returns_retry(
        tmp_path, monkeypatch):
    from pwiki_lib import planner, reconcile
    repo, root = _fixture(tmp_path)
    ctx = _ctx(repo, root)
    plan = planner.load_plan(ctx)
    card = planner.plan_cards(plan)[0]
    orig = ctx.db.queue_jobs

    def queue_then_cancel(run_id, items, style=None):
        r = orig(run_id, items, style=style)
        ctx.db.cancel_run(run_id, "模拟并发 cancel")
        return r
    monkeypatch.setattr(ctx.db, "queue_jobs", queue_then_cancel)
    status, msg = reconcile._apply_card_conflict(
        ctx, {"card": str(card.get("id"))}, dry_run=False)
    assert status == "retry", (status, msg)


# --------------------------------------------------------------------------- #
# r8 #14 模块注册表随 run 刷新
# --------------------------------------------------------------------------- #

def test_r8_14_begin_refreshes_module_registry(tmp_path):
    """begin 后 submit 用的 DB 模块表与当前 plan 同鲜度 —— 运行中途改名
    曾让 submit 按旧表要旧标题, finish 按新 plan 硬拒。"""
    from pwiki_lib import planner
    repo, root = _fixture(tmp_path)
    ctx = _ctx(repo, root)
    plan = planner.load_plan(ctx)
    plan["modules"][0]["title"] = "改名后的模块ZZ"
    planner.save_plan(ctx, plan)
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    titles = [m["title"] for m in ctx.db.modules(ctx.partition()["id"])]
    assert "改名后的模块ZZ" in titles, titles


# --------------------------------------------------------------------------- #
# r8 #15 gradle includeBuild 不再误收
# --------------------------------------------------------------------------- #

def test_r8_15_gradle_includebuild_excluded(tmp_path):
    from pwiki_lib.scan import parse_gradle_settings
    p = tmp_path / "settings.gradle"
    p.write_text('include(":core")\ninclude \':util\'\n'
                 'includeBuild("../platform-lib")\n', encoding="utf-8")
    assert parse_gradle_settings(str(p))["includes"] == ["core", "util"]


# --------------------------------------------------------------------------- #
# r8 #16 generation-state 基线整表只读一次
# --------------------------------------------------------------------------- #

def test_r8_16_generation_state_reads_baseline_once(tmp_path, monkeypatch):
    from pwiki_lib import publish
    repo, root = _fixture(tmp_path)
    ctx = _ctx(repo, root)
    calls = {"n": 0}
    orig = ctx.db.file_hashes

    def counted(pid):
        calls["n"] += 1
        return orig(pid)
    monkeypatch.setattr(ctx.db, "file_hashes", counted)
    pages = [{"page_id": f"p{i}", "path": f"p{i}.md", "source_files": "[]"}
             for i in range(3)]
    publish._write_generation_state(ctx, {"id": 1, "locale": "zh"},
                                    {"id": "r1"}, pages)
    assert calls["n"] == 1, calls


# --------------------------------------------------------------------------- #
# 批次②: 手编面 traceback 代门禁错误(r8 #18-#28)
# --------------------------------------------------------------------------- #

def _corrupt_plan(ctx, mutate):
    from pwiki_lib import planner
    plan = planner.load_plan(ctx)
    mutate(plan)
    planner.save_plan(ctx, plan)


def test_r8_18_themes_gate_before_dispatch(tmp_path):
    """`plan --themes` 对标量 pages 条目报 EXIT_GATE, 不再跑完全量规划后
    AttributeError exit 1。"""
    repo, root = _fixture(tmp_path)

    def bad(plan):
        plan["pages"].append("快速开始")
    _corrupt_plan(_ctx(repo, root), bad)
    with pytest.raises(SystemExit) as ei:
        _cli(["plan", "--themes", "--repo", repo.path, "--root", root])
    assert ei.value.code == config.EXIT_GATE


def test_r8_19_validate_plan_scalar_depends_on(tmp_path):
    repo, root = _fixture(tmp_path)
    ctx = _ctx(repo, root)
    from pwiki_lib import planner
    plan = planner.load_plan(ctx)
    plan["modules"][0]["depends_on"] = "user-service"
    errors, _w = planner.validate_plan(ctx, plan, strict=False)
    assert any("depends_on 必须是列表" in e for e in errors), errors


def test_r8_20_reset_gate_on_module_without_id(tmp_path):
    repo, root = _fixture(tmp_path)

    def bad(plan):
        plan["modules"].append({"parent": "root", "title": "X"})
    _corrupt_plan(_ctx(repo, root), bad)
    with pytest.raises(SystemExit) as ei:
        _cli(["plan", "--reset", "--repo", repo.path, "--root", root])
    assert ei.value.code == config.EXIT_GATE


def test_r8_21_validate_plan_scalar_page_scope_and_modules(tmp_path):
    repo, root = _fixture(tmp_path)
    ctx = _ctx(repo, root)
    from pwiki_lib import planner
    plan = planner.load_plan(ctx)
    page = next(p for p in plan["pages"]
                if p.get("kind") not in ("module_sheet", "card"))
    page["scope"] = "src/"
    page["modules"] = "services"
    errors, _w = planner.validate_plan(ctx, plan, strict=False)
    joined = "\n".join(errors)
    assert "scope 必须是列表" in joined and "modules 必须是列表" in joined, \
        errors


def test_r8_22_modules_cmd_shape_gate(tmp_path):
    """`pwiki modules` 对标量模块条目报 EXIT_GATE(decompose 之后不再
    AttributeError)。"""
    repo, root = _fixture(tmp_path)

    def bad(plan):
        plan["modules"].append("oops-scalar")
    _corrupt_plan(_ctx(repo, root), bad)
    with pytest.raises(SystemExit) as ei:
        _cli(["modules", "--repo", repo.path, "--root", root])
    assert ei.value.code == config.EXIT_GATE


def test_r8_23_modules_cmd_tolerates_bad_schema_version(tmp_path):
    """`pwiki modules` 对非数值 schema_version 容错抬升(bump_schema_version
    单一属主)—— 曾第三处裸 int() ValueError exit 1。"""
    from pwiki_lib import planner
    repo, root = _fixture(tmp_path)

    def bad(plan):
        plan["schema_version"] = "three"
    _corrupt_plan(_ctx(repo, root), bad)
    assert _cli(["modules", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    assert planner.load_plan(_ctx(repo, root))["schema_version"] == 3


def test_r8_24_qoderplan_notes_scalar_warns():
    from pwiki_lib.qoderplan import _check_caps
    warns = _check_caps([], ["oops-scalar"])
    assert any("必须是映射" in w for w in warns), warns


def test_r8_25_26_layers_state_shape_gate(tmp_path):
    """挽救的 plan-layers.json: 缺 articles 键 / 标量条目 → 干净 EXIT_GATE,
    apply/status/commit 不再 KeyError/AttributeError。"""
    import json
    from pwiki_lib import layers
    repo, root = _fixture(tmp_path)
    ctx = _ctx(repo, root)
    state_path = os.path.join(root, "work", "plan-layers.json")
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    for payload in ({"profile": "standard", "project": "T"},
                    {"articles": ["oops"]}):
        with open(state_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        with pytest.raises(SystemExit) as ei:
            layers._load_layers(ctx)
        assert ei.value.code == config.EXIT_GATE


def test_r8_28_layers_gate_on_scalar_plan_pages(tmp_path):
    repo, root = _fixture(tmp_path)

    def bad(plan):
        plan["pages"].append("裸条目")
    _corrupt_plan(_ctx(repo, root), bad)
    with pytest.raises(SystemExit) as ei:
        _cli(["plan", "--layers", "status", "--repo", repo.path,
              "--root", root])
    assert ei.value.code == config.EXIT_GATE


def test_r8_27_digest_tolerates_scalar_entries():
    """build_digest 对标量条目跳过(validate 负责报错), 写手共享摘要
    不再被打死。"""
    from pwiki_lib.digest import build_digest

    class _CtxLite:
        db = None

        def partition(self):
            return {"id": 1}
    out = build_digest(_CtxLite(),
                       {"modules": ["auth-core"], "pages": [], "cards": []},
                       {})
    assert "共享摘要" in out
