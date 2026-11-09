"""2026-11-09 第三轮 max 审查 P0(13 条)回修的回归测试。

对应报告: pwiki/docs/code-review/2026-11-09-code-review-max-round3.md(#1-#13, 报告文件已清理)。
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
    """无 config.json 的裸 ctx(test_review_fixes2 同款)。"""
    import subprocess
    from pwiki_lib.config import Ctx
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "src" / "a.py").write_text("x = 1\n" * 20, encoding="utf-8")
    import subprocess as _sp
    _sp.run(["git", "init", "-q"], cwd=str(repo), check=True)
    root = tmp_path / "wiki"
    root.mkdir()
    ctx = Ctx(types.SimpleNamespace(repo=str(repo), root=str(root),
                                    locale="zh"))
    ctx.config = {"export_style": "legacy"}
    return ctx


def _published_custom(tmp_path, style="concise"):
    """标准夹具上加 支付(父)+结算/对账(双子) 三页, 走完整发布。"""
    from pwiki_lib import planner
    repo, root = _fixture(tmp_path, style=style)
    ctx = _ctx(repo, root)
    plan = planner.load_plan(ctx)
    plan["pages"].extend([
        {"id": "pay", "title": "支付", "parent": "", "ordinal": 80,
         "kind": "page", "prompt": "写支付领域。", "source_files": ["README.md"]},
        {"id": "settle", "title": "结算", "parent": "pay", "ordinal": 0,
         "kind": "page", "prompt": "写结算。", "source_files": ["README.md"]},
        {"id": "recon", "title": "对账", "parent": "pay", "ordinal": 1,
         "kind": "page", "prompt": "写对账。", "source_files": ["README.md"]},
    ])
    planner.assign_paths(plan, ctx.locale)
    planner.save_plan(ctx, plan)
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    _write_all_jobs(repo, root, style=style)
    assert _cli(["finish", "--repo", repo.path, "--root", root, "--yes"]) == \
        config.EXIT_OK
    return repo, root


# --------------------------------------------------------------------------- #
# #1 纯 file:// 链接(无 #L)曾 int(None) 崩掉每条导出路径
# --------------------------------------------------------------------------- #

def test_p0_1_plain_file_link_no_crash(tmp_path):
    from pwiki_lib import gates, publish
    ctx = _ctx_raw(tmp_path)
    # rebuild_cite_block 自己就产出纯链接 —— compose 全链路曾 TypeError
    page = {"kind": "page", "path": "zh/content/T.md",
            "content": "# T\n\n## 正文\n\n见 [src/a.py](file://src/a.py)。\n"}
    out, _ = publish.compose_export_content(ctx, page, "concise",
                                            {"zh/content/T.md"}, plan={})
    assert "<cite>" in out and "[src/a.py](file://src/a.py)" in out
    fixed, n = gates.fix_off_by_one("[x](file://src/a.py)\n", ctx.repo)
    assert n == 0 and fixed == "[x](file://src/a.py)\n"


# --------------------------------------------------------------------------- #
# #11 a==b==n+1 曾被 a<=n 守卫跳过 —— 分类器说可自动修, 钳位器永不钳
# --------------------------------------------------------------------------- #

def test_p0_11_range_off_by_one_clamped(tmp_path):
    from pwiki_lib import gates
    ctx = _ctx_raw(tmp_path)          # src/a.py = 20 行
    content = "[x](file://src/a.py#L21-L21)\n"
    errs, _ = gates.validate_citations(content, ctx.repo)
    assert any(e.startswith("citation/off-by-one") for e in errs), errs
    fixed, n = gates.fix_off_by_one(content, ctx.repo)
    assert n == 1
    assert "#L20-L20" in fixed and "#L21" not in fixed


# --------------------------------------------------------------------------- #
# #5 --fix 曾先 D18 收割再 D23 钳位 —— 未钳行号进来源块, 永不收敛
# --------------------------------------------------------------------------- #

def test_p0_5_fix_clamps_before_harvesting_sources(tmp_path):
    from pwiki_lib import gates
    ctx = _ctx_raw(tmp_path)
    page = {"id": "p1", "title": "测试页", "path": "zh/content/测试页.md"}
    path = os.path.join(ctx.root, page["path"])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("# 测试页\n\n## 正文\n\nx "
                 "[src/a.py:21-21](file://src/a.py#L21-L21)\n")
    plan = {"style": "concise", "pages": [page], "cards": []}
    gates.validate_content(ctx, plan, fix=True)
    text = open(path, encoding="utf-8").read()
    assert "#L21" not in text, "差一行号未被钳位"
    # 收割进 **章节来源** 的行号也必须是钳位后的(曾抄进 21 → 永不收敛)
    assert "**章节来源**" in text and "src/a.py:20-20" in text, text
    rep2, _c = gates.validate_content(ctx, plan, fix=True)
    assert not any("off-by-one" in x for x in
                   rep2["global"]["errors"] + rep2["global"]["warnings"]), \
        "第二轮 --fix 仍在报差一(不收敛)"


# --------------------------------------------------------------------------- #
# #2 validate --content --fix 曾把剥掉注入物的原稿写回 → 发布树永久丢 cite/目录
# --------------------------------------------------------------------------- #

def test_p0_2_validate_fix_keeps_injected_layers(tmp_path):
    from pwiki_lib import gates
    ctx = _ctx_raw(tmp_path)
    page = {"id": "p1", "title": "测试页", "path": "zh/content/测试页.md"}
    path = os.path.join(ctx.root, page["path"])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("# 测试页\n\n<cite>\n引用文件\n"
                 "- [src/a.py](file://src/a.py)\n</cite>\n\n"
                 "## 概述\n\nintro。\n\n"
                 "## 架构\n\nx [src/a.py:1-3](file://src/a.py#L1-L3)\n\n"
                 "## 组件\n\ny [src/a.py:1-2](file://src/a.py#L1-L2)\n\n"
                 "## 目录\n\n1. [概述](#概述)\n2. [架构](#架构)\n"
                 "3. [组件](#组件)\n")
    plan = {"style": "concise", "pages": [page], "cards": []}
    gates.validate_content(ctx, plan, fix=True)
    text = open(path, encoding="utf-8").read()
    assert "<cite>" in text, "--fix 把注入的 cite 块永久剥掉了"
    assert "## 目录" in text, "--fix 把注入的编号目录永久剥掉了"
    assert "**章节来源**" in text, "缺来源的节未被补上"


# --------------------------------------------------------------------------- #
# #9 内链校验曾不解码 %20(publish 自己编码)且栅栏盲区 —— 自家产出被判死链
# --------------------------------------------------------------------------- #

def test_p0_9_internal_link_percent_decoded_and_fence_blind(tmp_path):
    from pwiki_lib import gates
    ctx = _ctx_raw(tmp_path)
    p1 = {"id": "p1", "title": "Data Layer",
          "path": "zh/content/Data Layer.md"}
    p2 = {"id": "p2", "title": "State Management",
          "path": "zh/content/State Management.md"}
    for item in (p1, p2):
        fp = os.path.join(ctx.root, item["path"])
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        with open(fp, "w", encoding="utf-8") as fh:
            fh.write(f"# {item['title']}\n\n正文。\n")
    with open(os.path.join(ctx.root, p1["path"]), "w",
              encoding="utf-8") as fh:
        fh.write("# Data Layer\n\n[go](State%20Management.md) 与断链 "
                 "[bad](Nope.md)。\n\n```markdown\n"
                 "[示例](Fence%20Demo.md)\n```\n")
    plan = {"style": "concise", "pages": [p1, p2], "cards": []}
    rep, _code = gates.validate_content(ctx, plan)
    link_errs = [e for e in rep["global"]["errors"]
                 if "links/internal" in e]
    assert any("Nope.md" in e for e in link_errs), "真断链必须仍然报错"
    assert not any("%20" in e or "State Management" in e for e in link_errs), \
        "发布层自己编码的 %20 链接被误判死链"
    assert not any("Fence" in e for e in link_errs), "栅栏内示例链接被当导航"


# --------------------------------------------------------------------------- #
# #10 cite_lines 曾只数前 1MB —— 大文件后段的正确引用被误判越界硬错
# --------------------------------------------------------------------------- #

def test_p0_10_cite_lines_counts_beyond_1mb(tmp_path):
    from pwiki_lib import gates
    from pwiki_lib.scan import cite_lines
    ctx = _ctx_raw(tmp_path)
    big = os.path.join(ctx.repo, "big.py")
    with open(big, "wb") as fh:
        fh.write(b"x = 1\n" * 200_000)      # 1.4MB / 200000 行
    assert cite_lines(big) == 200_000
    errs, _ = gates.validate_citations(
        "[f](file://big.py#L199990-L199995)", ctx.repo)
    assert errs == [], errs
    fixed, n = gates.fix_off_by_one("[f](file://big.py#L200001-L200001)",
                                     ctx.repo)
    assert n == 1 and "#L200000-L200000" in fixed


# --------------------------------------------------------------------------- #
# #13 块标量里的裸 '---' 曾被全局过滤 —— 手写 prompt 静默丢行
# --------------------------------------------------------------------------- #

def test_p0_13_yaml_block_scalar_keeps_separator_lines():
    from pwiki_lib.yamlite import yaml_load
    v = yaml_load("prompt: |\n  step one\n  ---\n  step two\n")
    assert v["prompt"] == "step one\n---\nstep two"
    # 列表项内的块标量同样保留(virt 行重组路径)
    v2 = yaml_load("pages:\n  - id: a\n    prompt: |\n      step\n"
                   "      ---\n      more\n")
    assert v2["pages"][0]["prompt"] == "step\n---\nmore"
    # 顶层文档分隔符: 首/尾跳过; 中途出现显式报错(多文档不支持)
    assert yaml_load("---\na: 1\n") == {"a": 1}
    assert yaml_load("a: 1\n---\n") == {"a": 1}
    with pytest.raises(ValueError):
        yaml_load("a: 1\n---\nb: 2\n")


# --------------------------------------------------------------------------- #
# #12 plan --reset 曾丢全部 module_sheet → 下次 finish 孤儿剪除已发布知识页
# --------------------------------------------------------------------------- #

def test_p0_12_plan_reset_preserves_module_sheets(tmp_path):
    from pwiki_lib import planner
    repo, root = _fixture(tmp_path, style="concise")
    ctx = _ctx(repo, root)
    before = {str(p["id"]) for p in
              planner.plan_pages(planner.load_plan(ctx))
              if p.get("kind") == "module_sheet"}
    assert before, "夹具应含 module_sheet"
    assert _cli(["plan", "--reset", "--repo", repo.path,
                 "--root", root]) == config.EXIT_OK
    after = {str(p["id"]) for p in
             planner.plan_pages(planner.load_plan(ctx))
             if p.get("kind") == "module_sheet"}
    assert after == before, "reset 丢失 module_sheet(下次 finish 会剪掉知识树)"


# --------------------------------------------------------------------------- #
# #4 file_to_db 剥注入物曾只看 preset —— current 模式下作者 cite 块被永久剥掉
# --------------------------------------------------------------------------- #

def test_p0_4_file_to_db_keeps_author_cite_under_export_style_current(tmp_path):
    from pwiki_lib import planner
    repo, root = _fixture(tmp_path, style="concise")
    ctx = _ctx(repo, root)
    plan = planner.load_plan(ctx)
    plan["export_style"] = "current"
    planner.save_plan(ctx, plan)
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    _write_all_jobs(repo, root, style="concise")
    assert _cli(["finish", "--repo", repo.path, "--root", root,
                 "--yes"]) == config.EXIT_OK
    pid = ctx.partition()["id"]
    row = next(p for p in ctx.db.published_pages(pid)
               if p["kind"] == "page")
    fp = os.path.join(root, row["path"])
    text = open(fp, encoding="utf-8").read()
    assert "<cite>" not in text, "current 模式发布文件不应含注入物"
    with open(fp, "w", encoding="utf-8") as fh:
        fh.write(text + "\n<cite>\n作者注\n"
                 "- [README.md](file://README.md)\n</cite>\n")
    assert _cli(["export", "--direction", "file_to_db",
                 "--repo", repo.path, "--root", root]) == config.EXIT_OK
    fresh = ctx.db.published_page(pid, row["page_id"])
    assert "<cite>" in (fresh["content"] or ""), \
        "作者手写 cite 块被当注入物从 DB 剥掉(永久丢稿)"


# --------------------------------------------------------------------------- #
# #3 D22 改名无兄弟冲突守卫 —— 同名改写覆盖兄弟发布文件 + 持久化非法 plan
# --------------------------------------------------------------------------- #

def test_p0_3_rename_rejected_on_sibling_title_collision(tmp_path, capsys):
    repo, root = _published_custom(tmp_path)
    ctx = _ctx(repo, root)
    pid = ctx.partition()["id"]
    ctx.db.reconcile_add(pid, "title_mismatch",
                         {"page_id": "settle", "old": "结算",
                          "proposed": "对账", "evidence": "标题勘误"})
    assert _cli(["reconcile", "--all", "--repo", repo.path,
                 "--root", root]) == config.EXIT_OK
    out = capsys.readouterr().out
    assert "兄弟标题冲突" in out, out
    # 兄弟文件未被覆盖, 双方都健在
    assert os.path.isfile(os.path.join(root, "zh", "content",
                                       "支付", "对账.md"))
    assert os.path.isfile(os.path.join(root, "zh", "content",
                                       "支付", "结算.md"))
    row = ctx.db.published_page(pid, "settle")
    assert row["title"] == "结算" and row["path"].endswith("结算.md")


# --------------------------------------------------------------------------- #
# #7 D22 改名无活跃 run 守卫 —— 执行中被 finish 静默回滚
# --------------------------------------------------------------------------- #

def test_p0_7_rename_waits_for_active_run(tmp_path, capsys):
    repo, root = _published_custom(tmp_path)
    ctx = _ctx(repo, root)
    pid = ctx.partition()["id"]
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    ctx.db.reconcile_add(pid, "title_mismatch",
                         {"page_id": "pay", "old": "支付",
                          "proposed": "收款", "evidence": "标题勘误"})
    assert _cli(["reconcile", "--all", "--repo", repo.path,
                 "--root", root]) == config.EXIT_OK
    out = capsys.readouterr().out
    assert "存在活动 run" in out, out
    pend = ctx.db.reconcile_pending(pid)
    assert pend and pend[0]["kind"] == "title_mismatch", \
        "retry 项应保留 pending"
    assert os.path.isfile(os.path.join(root, "zh", "content", "支付.md"))
    assert _cli(["cancel", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    assert _cli(["reconcile", "--all", "--repo", repo.path,
                 "--root", root]) == config.EXIT_OK
    assert os.path.isfile(os.path.join(root, "zh", "content", "收款.md")), \
        "run 取消后改名应正常落地"


# --------------------------------------------------------------------------- #
# #6 改名级联曾对着半更新的 DB 逐个合成 —— 父页死链, 自家 validator 报错
# --------------------------------------------------------------------------- #

def test_p0_6_rename_cascade_keeps_internal_links(tmp_path):
    from pwiki_lib import gates, planner
    repo, root = _published_custom(tmp_path)
    ctx = _ctx(repo, root)
    pid = ctx.partition()["id"]
    # 父页内容带指向子页的相对内链(发布树里的真实导航形态)
    content = ("# 支付\n\n## 概述\n\n见 [结算](支付/结算.md)。\n\n"
               "**章节来源**\n- [README.md:1](file://README.md#L1)\n")
    ctx.db.save_page_content(pid, "pay", content)
    ctx.db.reconcile_add(pid, "title_mismatch",
                         {"page_id": "pay", "old": "支付",
                          "proposed": "收款", "evidence": "标题勘误"})
    assert _cli(["reconcile", "--all", "--repo", repo.path,
                 "--root", root]) == config.EXIT_OK
    assert os.path.isfile(os.path.join(root, "zh", "content", "收款.md"))
    assert not os.path.isfile(os.path.join(root, "zh", "content", "支付.md"))
    assert os.path.isfile(os.path.join(root, "zh", "content",
                                       "收款", "结算.md"))
    rep, _code = gates.validate_content(ctx, planner.load_plan(ctx))
    dead = [e for e in rep["global"]["errors"] if "links/internal" in e]
    assert not dead, f"改名级联留死链: {dead}"


# --------------------------------------------------------------------------- #
# #8 kept 合并曾只搬 title/parent/ordinal/path —— update 0-job 死循环
# --------------------------------------------------------------------------- #

def test_p0_8_kept_merge_syncs_catalog_fields(tmp_path, capsys):
    from pwiki_lib import planner
    repo, root = _published_custom(tmp_path)
    ctx = _ctx(repo, root)
    pid = ctx.partition()["id"]
    plan = planner.load_plan(ctx)
    page = next(p for p in planner.plan_pages(plan)
                if p.get("id") == "settle")
    page["slug"] = "settle-v2"
    page["type"] = "deep_dive"
    planner.save_plan(ctx, plan)
    assert _cli(["update", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    assert ctx.db.active_run(pid) is not None, "catalog 漂移应建 INCREMENT run"
    assert _cli(["finish", "--repo", repo.path, "--root", root,
                 "--yes"]) == config.EXIT_OK
    row = ctx.db.published_page(pid, "settle")
    assert row["slug"] == "settle-v2", "kept 合并未同步 slug"
    assert row["article_type"] == "deep_dive", "kept 合并未同步 type"
    # 收敛: 字段已同步, 第二轮 update 不再重标 catalog_changed
    capsys.readouterr()
    assert _cli(["update", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    out = capsys.readouterr().out
    assert "no update required" in out, \
        f"update 仍每轮重标 catalog_changed(0-job 死循环): {out}"


# =========================================================================== #
# P1(#14-#30): validate 放行后崩溃簇 / 协议与稿件丢失 / 编排契约
# =========================================================================== #

def test_p1_14_15_ordinal_and_null_lists_gated_and_normalized(tmp_path):
    """#14/#15: `ordinal:`/`source_files:` 空值(None)曾穿过 validate,
    入库为 None/'null' 后在 begin/next/submit 裸 int(None)/TypeError。"""
    from pwiki_lib import engine, planner
    from pwiki_lib.db import WikiDB
    ctx = _ctx_raw(tmp_path)
    plan = {"schema_version": 3, "style": "concise",
            "modules": [{"id": "root", "title": "/", "parent": ""}],
            "pages": [{"id": "a", "title": "甲", "parent": "",
                       "kind": "page", "ordinal": None,
                       "source_files": ["src/a.py"], "prompt": "x"}],
            "cards": []}
    errs, _ = planner.validate_plan(ctx, plan)
    assert any("ordinal" in e for e in errs), errs
    # 入库侧归一: 页 ordinal=None → 0; 卡 ordinal=None → 950;
    # source_files=None → '[]'(不再存 'null' 绕过消费方守卫)
    db = WikiDB(str(tmp_path / "dbroot"))
    db.queue_jobs("r1", [
        {"id": "a", "title": "甲", "kind": "page", "ordinal": None,
         "parent": "", "path": "zh/content/甲.md"},
        {"id": "c", "title": "卡", "kind": "external_dependency",
         "source_files": None, "modules": None, "ordinal": None,
         "path": "knowledge/zh/卡/卡.md"},
    ])
    ja, jc = db.job("r1", "a"), db.job("r1", "c")
    assert ja["ordinal"] == 0
    assert jc["ordinal"] == 950 and jc["kind"] == "card"
    assert jc["source_files"] == "[]" and jc["modules"] == "[]"
    # 旧库残存 'null' 行的容错解码(next 的 join / submit 的迭代不再崩)
    payload = engine._job_payload(
        jc, ctx, {"id": "r1", "state": "DOCUMENT_GENERATING",
                  "generation_type": "FULL"}, [], {"total": 2})
    assert payload["job"]["source_files"] == [] \
        and payload["job"]["modules"] == []


def test_p1_16_null_title_rejected_at_gate(tmp_path):
    """#16: None 标题曾被 str(None)='None' 洗成真值过门 → safe_filename 崩。"""
    from pwiki_lib import planner
    from pwiki_lib.yamlite import yaml_load
    ctx = _ctx_raw(tmp_path)
    plan = yaml_load(
        "style: concise\n"
        "modules:\n  - id: root\n    title: /\n"
        "pages:\n  - id: a\n    title:\n"
        "    source_files:\n      - src/a.py\n"
        "cards:\n  - id: c1\n    kind: external_dependency\n    title:\n"
        "    source_files:\n      - src/a.py\n")
    errs, _ = planner.validate_plan(ctx, plan)
    assert any("缺 title" in e for e in errs), errs
    assert any("card 缺 id/title" in e for e in errs), errs


def test_p1_17_digest_accepts_dict_depends_on(tmp_path):
    """#17: depends_on 的 {path: ...} 形态(validate 收)曾让 digest join 崩。"""
    from pwiki_lib.digest import build_digest
    ctx = _ctx_raw(tmp_path)
    plan = {"modules": [{"id": "m1", "title": "M", "dir": "src",
                         "module_type": "real",
                         "depends_on": [{"path": "user-service"}]}],
            "pages": [], "cards": []}
    inv = {"repo": "r", "stats": {"files": 1, "lines": 2}, "languages": [],
           "entry_points": [], "ci": [], "commands": {}, "files": [],
           "manifests": [], "git": {}}
    body = build_digest(ctx, plan, inv)
    assert "user-service" in body


def test_p1_18_expand_sheets_tolerates_missing_pages_key(tmp_path):
    """#18: 手工裁掉 pages: 行的 plan 曾在 plan --sheets 直接 KeyError。"""
    from pwiki_lib.planner import expand_sheets
    plan = {"modules": [{"id": "m1", "title": "M", "scope": ["src/"],
                         "parent": "root", "path": "/src"}]}
    n = expand_sheets(plan, "lite", "zh", "concise")
    assert n > 0 and plan["pages"], "pages 键缺失曾 KeyError traceback"


def test_p1_19_overrides_list_valued_key_ignored_with_warning(tmp_path,
                                                              capsys):
    """#19: title_aliases 写成列表(自然 YAML 形态)曾 AttributeError 打死
    整条 themes 路线, 而非文档承诺的"大声警告 + 忽略"。"""
    from pwiki_lib import themes
    ctx = _ctx_raw(tmp_path)
    with open(ctx.overrides_path, "w", encoding="utf-8") as fh:
        fh.write("title_aliases:\n  - foo\n")
    ov = themes.load_overrides(ctx)
    assert ov["title_aliases"] == {}
    assert "title_aliases 不是映射" in capsys.readouterr().err


def test_p1_20_config_profile_typo_dies_loudly(tmp_path, capsys):
    """#20: config.json profile 拼错曾在 run_themes 裸 KeyError, 而
    validate/layers 又静默按默认预算跑。"""
    from test_themes import _ctx as _tctx, _svc_fixture
    from pwiki_lib.themes import cmd_plan_themes
    repo = _svc_fixture(tmp_path)
    ctx = _tctx(repo, tmp_path / "root")
    ctx.config["profile"] = "standrd"
    with pytest.raises(SystemExit) as ei:
        cmd_plan_themes(ctx, types.SimpleNamespace(reset=False),
                        {"schema_version": 3, "style": "concise",
                         "modules": [], "pages": [], "cards": []})
    assert ei.value.code == config.EXIT_GATE
    assert "不在" in capsys.readouterr().err


def test_p1_21_stale_resubmit_does_not_clobber_fresh_claim(tmp_path):
    """#21: 门禁 I/O 期间被重认领的 job, 无 token 重交曾直接顶掉在写
    writer 的租约(其 submit 随即孤儿化)—— 租约系统要防的交叉覆盖。"""
    from pwiki_lib import engine, gates
    repo, root = _fixture(tmp_path, style="concise")
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    ctx = _ctx(repo, root)
    pid = ctx.partition()["id"]
    run = ctx.db.active_run(pid)
    job = next(j for j in ctx.db.jobs(run["id"]) if j["kind"] == "page")
    x = job["page_id"]
    # A 首次认领(attempts=1)后交稿被拒 → 回 pending(令牌释放)
    ctx.db.conn.execute(
        "UPDATE page_job SET state='processing', attempts=1, "
        "claim_token='a-tok' WHERE run_id=? AND page_id=?", (run["id"], x))
    ctx.db.set_job_state(run["id"], x, "pending", error="test",
                         claim_token="a-tok")
    # A 修好稿重交(省略 --claim); 门禁期间 B 认领(attempts=2, b-tok)
    orig = gates.gate_item

    def fake(content, item, repo_, style, phase="submit", mod_titles=None):
        ctx.db.conn.execute(
            "UPDATE page_job SET state='processing', attempts=attempts+1, "
            "claim_token='b-tok' WHERE run_id=? AND page_id=?",
            (run["id"], x))
        return orig(content, item, repo_, style, phase=phase,
                    mod_titles=mod_titles)

    sec = "\n**章节来源**\n- [README.md:1](file://README.md#L1)\n"
    draft = os.path.join(root, "work", f"{x}-resubmit.md")
    os.makedirs(os.path.dirname(draft), exist_ok=True)
    with open(draft, "w", encoding="utf-8") as fh:
        fh.write(f"# {job['title']}\n\n## 概述\n\n页面摘要。\n## 架构\n\nx\n"
                 f"{sec}\n## 组件\n\nx\n{sec}## 数据流\n\nx\n{sec}"
                 f"## 配置\n\nx\n{sec}")
    gates.gate_item = fake
    try:
        with pytest.raises(SystemExit) as ei:
            engine.cmd_submit(types.SimpleNamespace(
                **{"repo": repo.path, "root": root, "locale": "zh",
                   "page_id": x, "file": draft, "summary": "",
                   "key_topics": "", "report": None, "lenient": False,
                   "force": False, "claim": None}))
    finally:
        gates.gate_item = orig
    assert ei.value.code == config.EXIT_PRECOND
    row = ctx.db.job(run["id"], x)
    assert row["state"] == "processing" and row["claim_token"] == "b-tok", \
        "在写 writer 的租约被无 token 重交顶掉"
    orphan_dir = os.path.join(root, "work", "orphan-submits")
    assert os.listdir(orphan_dir), "A 的稿件应进孤儿稿目录(不丢)"


def test_p1_22_23_submit_reads_draft_before_state_guards(tmp_path):
    """#22/#23: 无活动 run(begin --force 已取消)/LINING 的守卫曾发生在
    读稿前 —— stdin 交稿被消费后整篇蒸发, 孤儿目录都进不去。"""
    from pwiki_lib import engine
    repo, root = _fixture(tmp_path, style="concise")
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    ctx = _ctx(repo, root)
    pid = ctx.partition()["id"]
    draft = os.path.join(root, "work", "d.md")
    os.makedirs(os.path.dirname(draft), exist_ok=True)
    with open(draft, "w", encoding="utf-8") as fh:
        fh.write("# 稿\n\n## 概述\n\nx\n")
    orphan_dir = os.path.join(root, "work", "orphan-submits")

    def _submit(page_id):
        with pytest.raises(SystemExit) as ei:
            engine.cmd_submit(types.SimpleNamespace(
                **{"repo": repo.path, "root": root, "locale": "zh",
                   "page_id": page_id, "file": draft, "summary": "",
                   "key_topics": "", "report": None, "lenient": False,
                   "force": False, "claim": None}))
        return ei.value.code

    # run 被 begin --force 同款取消(无活动 run) → 稿件先保再挡
    run = ctx.db.active_run(pid)
    ctx.db.update_run(run["id"], state="CANCELLED", finished_at="x")
    assert _submit("overview") == config.EXIT_PRECOND
    assert os.listdir(orphan_dir), "无活动 run 的稿件未进孤儿目录"
    # LINING 窗口 → 同样保稿再挡
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    run = ctx.db.active_run(pid)
    ctx.db.update_run(run["id"], state="LINING", stage="lining")
    n_before = len(os.listdir(orphan_dir))
    assert _submit("overview") == config.EXIT_PRECOND
    assert len(os.listdir(orphan_dir)) == n_before + 1, \
        "LINING 守卫的稿件未进孤儿目录"


def test_p1_24_next_json_stdout_pure_on_no_run(tmp_path, capsys):
    """#24: next --json 的 stdout 必须纯 JSON(人类状态行走 stderr)——
    in_flight/complete 路径已由 test_next_in_flight_vs_complete 锁定。"""
    repo, root = _fixture(tmp_path, style="concise")
    capsys.readouterr()
    assert _cli(["next", "--json", "--repo", repo.path,
                 "--root", root]) == config.EXIT_PRECOND
    cap = capsys.readouterr()
    assert cap.out == "", f"stdout 混入人类可读行: {cap.out!r}"
    assert "status:" in cap.err


def test_p1_25_payload_reports_real_run_state(tmp_path, capsys):
    """#25: next --json 曾硬编码 DOCUMENT_GENERATING —— DOCUMENT_FAILED
    恢复轮询拿到的正是错误状态。"""
    repo, root = _fixture(tmp_path, style="concise")
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    ctx = _ctx(repo, root)
    run = ctx.db.active_run(ctx.partition()["id"])
    ctx.db.update_run(run["id"], state="DOCUMENT_FAILED")
    capsys.readouterr()
    assert _cli(["next", "--json", "--repo", repo.path,
                 "--root", root]) == config.EXIT_OK
    import json as _json
    payload = _json.loads(capsys.readouterr().out)
    assert payload["run"]["state"] == "DOCUMENT_FAILED"


def test_p1_26_delete_all_sweeps_files_before_db(tmp_path, capsys):
    """#26: 曾先提交 unpublish 再裸 unlink —— 一个删不掉的文件留下
    "DB 称已全删 + 树上半堆 + 裸 traceback"。"""
    repo, root = _published_custom(tmp_path)
    ctx = _ctx(repo, root)
    pid = ctx.partition()["id"]
    kbase = os.path.join(root, "knowledge", "zh")
    sub = next(os.path.join(kbase, d) for d in os.listdir(kbase)
               if os.path.isdir(os.path.join(kbase, d)))
    # c041: chmod 0o500 在 root/忽略 DAC 的文件系统上拦不住删除(测试假红)
    # —— 改为注入失败: rmtree 删掉其余子项但保留该子目录(等价于
    # "一个删不掉的子目录让 base 幸存"), 不依赖文件系统 DAC 行为
    import shutil as _shutil
    real_rmtree = _shutil.rmtree
    _sub_abs = os.path.abspath(sub)

    def _partial_rmtree(path, *a, **kw):
        p_abs = os.path.abspath(str(path))
        if os.path.isdir(p_abs) and p_abs != _sub_abs \
                and _sub_abs.startswith(p_abs + os.sep):
            for entry in os.listdir(p_abs):
                full = os.path.join(p_abs, entry)
                if os.path.abspath(full) == _sub_abs:
                    continue
                if os.path.isdir(full) and not os.path.islink(full):
                    real_rmtree(full, ignore_errors=True)
                else:
                    try:
                        os.unlink(full)
                    except OSError:
                        pass
            return
        return real_rmtree(path, *a, **kw)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(_shutil, "rmtree", _partial_rmtree)
    try:
        capsys.readouterr()
        assert _cli(["delete", "--all", "--repo", repo.path,
                     "--root", root]) == config.EXIT_OK
        assert not list(ctx.db.published_pages(pid)), "DB 应已清空"
        assert "未能删除" in capsys.readouterr().err
    finally:
        monkeypatch.undo()


def test_p1_27_bad_utf8_hand_edit_dies_with_page(tmp_path, capsys):
    """#27: 手编发布文件的坏字节给可诊断门禁错(c048 曾只覆盖 submit/write)。"""
    repo, root = _published_custom(tmp_path)
    ctx = _ctx(repo, root)
    pid = ctx.partition()["id"]
    row = next(p for p in ctx.db.published_pages(pid) if p["kind"] == "page")
    with open(os.path.join(root, row["path"]), "ab") as fh:
        fh.write(b"\xd6\xd0\xca\xd4")
    with pytest.raises(SystemExit) as ei:
        _cli(["validate", "--content", "--repo", repo.path, "--root", root])
    assert ei.value.code == config.EXIT_GATE
    assert "不是合法 UTF-8" in capsys.readouterr().err


def test_p1_28_atomic_write_leaves_no_tmp(tmp_path):
    """#28: 发布工件统一原子写 —— 无 .tmp 残留, 深目录可用, 覆写原子替换。"""
    from pwiki_lib.util import atomic_write
    p = tmp_path / "a" / "f.md"
    p.parent.mkdir()
    atomic_write(str(p), "v1\n")
    atomic_write(str(p), "v2\n")
    assert p.read_text(encoding="utf-8") == "v2\n"
    assert list(p.parent.iterdir()) == [p], "留下 .tmp 残留"
    deep = tmp_path / "b" / "c" / "f.md"
    atomic_write(str(deep), "x\n")
    assert deep.read_text(encoding="utf-8") == "x\n"


def test_p1_30_inner_import_error_not_disguised(tmp_path):
    """#30: 模块内部 ImportError 曾被吞成"命令尚未实现"exit 3。"""
    # c042: 曾把 _tmp_badmod_p1.py 写进活源码树(中断的 pytest 会把杂模块
    # 留在 scripts/pwiki_lib/ 里随 install.sh 随船)—— 改写 tmp_path 并
    # 临时扩展包搜索路径, 源码树零接触
    import pwiki_lib.cli as cli_mod
    import pwiki_lib as _pkg
    bad = tmp_path / "_tmp_badmod_p1.py"
    bad.write_text("from . import definitely_missing_mod_xyz\n", encoding="utf-8")
    old_pkg_path = list(_pkg.__path__)
    _pkg.__path__.append(str(tmp_path))
    try:
        cli_mod.COMMAND_OWNERS["_p1probe"] = ("_tmp_badmod_p1", "fn")
        with pytest.raises(ImportError):
            cli_mod._resolve_handler("_p1probe")   # 原样抛, 不再伪装
        cli_mod.COMMAND_OWNERS["_p1probe2"] = ("_no_such_mod", "fn")
        assert cli_mod._resolve_handler("_p1probe2") is None  # 真未落地
    finally:
        _pkg.__path__[:] = old_pkg_path
        cli_mod.COMMAND_OWNERS.pop("_p1probe", None)
        cli_mod.COMMAND_OWNERS.pop("_p1probe2", None)
        sys.modules.pop("pwiki_lib._tmp_badmod_p1", None)


# =========================================================================== #
# P2(#31-#52): 静默错误行为簇
# =========================================================================== #

def test_p2_31_themes_ordinal_by_priority_desc(tmp_path):
    """#31: to_plan_pages 曾按优先级升序发号 —— overview(1000) 拿最大
    ordinal, 树视图/认领顺序全部倒挂。"""
    from test_themes import _ctx as _tctx, _decomposed, _svc_fixture
    from pwiki_lib import themes
    repo = _svc_fixture(tmp_path)
    inv, dec = _decomposed(repo)
    ctx = _tctx(repo, tmp_path / "root")
    result = themes.run_themes(ctx, inv, dec, "standard",
                               framework_mods=set())
    roots = [p for p in result["pages"] if not p.get("parent")]
    assert roots, "夹具应产出根页"
    ov = next(p for p in roots if p.get("type") == "overview")
    assert ov["ordinal"] == min(p["ordinal"] for p in roots), \
        "overview 应是 0 号(最重要优先), 曾倒挂到最大"


def test_p2_32_scaffold_reserves_overflow_root_slot(tmp_path):
    """#32: domain 名额曾不为"其他模块"溢出页留位 —— 溢出一触发就 21 个
    顶层, 自产计划过不了自家 validate。"""
    from pwiki_lib import planner
    modules = [{"id": f"m{i}", "title": f"模块{i}", "parent": "root",
                "path": f"/mod{i}", "dir": f"mod{i}/", "scope": [f"mod{i}/"]}
               for i in range(25)]
    inv = {"manifests": [{"path": "package.json", "dependencies": {}}],
           "readme": {"path": "README.md"}, "files": [], "languages": [],
           "entry_points": [], "ci": []}
    pages = planner.scaffold_pages(inv, {"modules": modules, "limits": {}})
    roots = [p for p in pages if not p.get("parent")]
    limits = planner.plan_limits({"limits": {}}, "standard")
    assert len(roots) <= limits["max_top_articles"], \
        f"{len(roots)} 个顶层 > max_top_articles={limits['max_top_articles']}"
    assert any(p.get("type") == "domain" and p["title"] == "其他模块"
               for p in roots), "溢出页应在场(触发即验证)"


def test_p2_33_provider_hits_boundary_and_noise():
    """#33: provider 键曾按裸子串匹配 —— __mocks__ 与 pathfinder(含
    'hf')都被当成集成证据, 造出不存在的依赖卡。"""
    from pwiki_lib.themes import provider_hits
    files = [{"path": p} for p in (
        "src/__mocks__/api.ts", "src/order.spec.ts",
        "src/pathfinder.ts",           # 含 'hf', 非词边界
        "src/hf/client.ts",            # 真目录段
        "src/openai/proxy.go",
        "services/mock/store.prod.ts")]
    assert provider_hits(files, "hf") == [files[3]]
    assert provider_hits(files, "mock") == [files[5]], \
        "__mocks__/.spec. 噪音应被排除, 生产的 mock 目录保留"
    assert provider_hits(files, "openai") == [files[4]]


def test_p2_34_parent_card_id_rejected(tmp_path):
    """#34: 页的 parent 指向卡片 id 曾过门后静默重根。"""
    from pwiki_lib import planner
    ctx = _ctx_raw(tmp_path)
    plan = {"schema_version": 3, "style": "concise",
            "modules": [{"id": "root", "title": "/", "parent": ""}],
            "pages": [{"id": "a", "title": "甲", "kind": "page",
                       "parent": "card-x", "source_files": ["src/a.py"],
                       "prompt": "x"}],
            "cards": [{"id": "card-x", "kind": "external_dependency",
                       "title": "卡", "source_files": ["src/a.py"]}]}
    errs, _ = planner.validate_plan(ctx, plan)
    assert any("未知 parent id" in e for e in errs), errs


def test_p2_35_catalog_parent_in_slug_space(tmp_path):
    """#35: wiki_catalogs 的 parent_id 曾存原始 page_id —— slug≠id 的
    子树在 Qoder 导入时失联。"""
    import json as _json
    from pwiki_lib import planner
    repo, root = _published_custom(tmp_path)
    ctx = _ctx(repo, root)
    plan = planner.load_plan(ctx)
    pay = next(p for p in planner.plan_pages(plan) if p.get("id") == "pay")
    pay["type"] = "domain"
    pay["slug"] = "pay-slug"
    planner.save_plan(ctx, plan)
    assert _cli(["update", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    assert _cli(["finish", "--repo", repo.path, "--root", root,
                 "--yes"]) == config.EXIT_OK
    meta = _json.load(open(os.path.join(root, "zh", "meta",
                                        "repowiki-metadata.json"),
                           encoding="utf-8"))
    catalogs = meta["wiki_catalogs"]
    cids = {c["id"] for c in catalogs}
    assert "pay-slug" in cids
    settle = next(c for c in catalogs if c["id"] == "settle")
    assert settle["parent_id"] == "pay-slug", \
        f"parent_id 留在 page_id 空间({settle['parent_id']!r}), 无对应目录"


def test_p2_36_qoder_emit_dangling_parent_dies(tmp_path, capsys):
    """#36: 悬空 parent 的子树曾静默不出现在导出计划里还 exit 0。"""
    from pwiki_lib import planner
    repo, root = _published_custom(tmp_path)
    ctx = _ctx(repo, root)
    plan = planner.load_plan(ctx)
    # 拼错 parent: 子树不再可达
    target = next(p for p in planner.plan_pages(plan)
                  if p.get("id") == "settle")
    target["parent"] = "pa"
    planner.save_plan(ctx, plan)
    with pytest.raises(SystemExit) as ei:
        _cli(["qoder-plan", "--emit", str(tmp_path / "out.yaml"),
              "--repo", repo.path, "--root", root])
    assert ei.value.code == config.EXIT_GATE
    assert "悬空" in capsys.readouterr().err


def test_p2_37_scope_only_card_goes_stale(tmp_path, capsys):
    """#37: 目录型 scope 曾"哈希按展开文件、对账按目录串" —— 卡永不 stale。"""
    from pwiki_lib import planner
    repo, root = _published_custom(tmp_path)
    ctx = _ctx(repo, root)
    plan = planner.load_plan(ctx)
    # scope-only 卡(source_files 留空 —— 非空会按优先级盖掉 scope 对账)
    plan["cards"].append({"id": "c-dir", "kind": "external_dependency",
                          "title": "目录卡", "scope": ["services/"],
                          "prompt": "x", "source_files": []})
    planner.assign_paths(plan, ctx.locale)
    planner.save_plan(ctx, plan)
    assert _cli(["update", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    _write_all_jobs(repo, root, style="concise")
    assert _cli(["finish", "--repo", repo.path, "--root", root,
                 "--yes"]) == config.EXIT_OK
    # scope 目录内的真实变更 → 卡必须 stale(曾只比 'services' 串本身)
    repo.write("services/user/m0.py", "# changed\n" + "X = 2\n" * 24)
    repo.commit("touch scope dir")
    capsys.readouterr()
    assert _cli(["update", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    out = capsys.readouterr().out
    assert "c-dir" in out, f"scope-only 卡未随目录内变更标 stale: {out}"


def test_p2_38_kindless_scoped_page_stays_in_content_tree(tmp_path):
    """#38: 无 kind 带 scope 的页曾被 update 的遗留析取路由进 knowledge
    树 —— 同一条目按入口在两棵树间漂移。"""
    from pwiki_lib import planner
    repo, root = _published_custom(tmp_path)
    ctx = _ctx(repo, root)
    plan = planner.load_plan(ctx)
    plan["pages"].append({"id": "custom", "title": "自定义页", "parent": "",
                          "ordinal": 99, "prompt": "x",
                          "scope": ["services/"], "source_files": []})
    planner.assign_paths(plan, ctx.locale)
    planner.save_plan(ctx, plan)
    assert _cli(["update", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    _write_all_jobs(repo, root, style="concise")
    assert _cli(["finish", "--repo", repo.path, "--root", root,
                 "--yes"]) == config.EXIT_OK
    assert os.path.isfile(os.path.join(root, "zh", "content",
                                       "自定义页.md")), \
        "begin 口径的 content 树位置"
    # 页的陈旧由引用对账驱动(_write_all_jobs 模板引用 README.md)
    repo.write("README.md", "# 全周期仓库\n\nchanged。\n两个服务与一个共享包。\n")
    repo.commit("touch cited readme")
    assert _cli(["update", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    run = ctx.db.active_run(ctx.partition()["id"])
    jobs = ctx.db.jobs(run["id"])
    cust = next(j for j in jobs if j["page_id"] == "custom")
    assert cust["path"].replace(os.sep, "/").startswith("zh/content/"), \
        f"update 把无 kind 页排进了 knowledge 树: {cust['path']}"


def test_p2_39_style_drift_requeues_cards(tmp_path):
    """#39: 风格漂移复检曾按字面元组跳过卡片 —— concise→deep 后旧卡
    永不再排。"""
    from pwiki_lib import planner
    repo, root = _published_custom(tmp_path)
    ctx = _ctx(repo, root)
    plan = planner.load_plan(ctx)
    plan["style"] = "deep"
    planner.save_plan(ctx, plan)
    assert _cli(["update", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    run = ctx.db.active_run(ctx.partition()["id"])
    assert run is not None, "风格漂移应建 run"
    kinds = {j["kind"] for j in ctx.db.jobs(run["id"])}
    assert "card" in kinds, f"卡片未被重排(只排了 {kinds})"


def test_p2_40_db_to_file_uses_plan_style(tmp_path):
    """#40(r5 P2 收敛): 崩溃自愈渲染按**每行盖章风格**(与 publish_run
    逐行合成、file_to_db 剥除判定同口径) —— 行戳是行的真值; plan 改风格
    由 update 的漂移复检重盖章后再渲染, 自愈路径不把整库翻成行还没过检
    的风格(曾按 last_run 渲染 + 曾按 plan 渲染, 都与逐行戳分叉)。"""
    from pwiki_lib import planner
    repo, root = _published_custom(tmp_path)
    ctx = _ctx(repo, root)
    pid = ctx.partition()["id"]
    pay_file = os.path.join(root, "zh", "content", "支付.md")
    assert "<cite>" in open(pay_file, encoding="utf-8").read(), \
        "前置: concise 发布文件含注入 cite"
    plan = planner.load_plan(ctx)
    plan["style"] = "deep"
    planner.save_plan(ctx, plan)
    # 行戳仍是 concise → 自愈渲染保持注入(与 finish 的逐行合成逐字节同)
    assert _cli(["export", "--direction", "db_to_file",
                 "--repo", repo.path, "--root", root]) == config.EXIT_OK
    text = open(pay_file, encoding="utf-8").read()
    assert "<cite>" in text, "行戳 concise 的页被按 plan(deep) 漏注 cite"
    # 行被重盖章为 deep(update 漂移复检过检后的口径) → 自愈不再注入
    ctx.db.set_page_style(pid, "pay", "deep")
    assert _cli(["export", "--direction", "db_to_file",
                 "--repo", repo.path, "--root", root]) == config.EXIT_OK
    text = open(pay_file, encoding="utf-8").read()
    assert "<cite>" not in text, "deep 行戳仍被注入 cite"


def test_p2_41_spaced_path_citations(tmp_path):
    """#41: 带空格的仓内文件曾无法被合法引用(%20 被判不存在, 裸空格
    对全部门禁不可见)。"""
    from pwiki_lib import gates
    ctx = _ctx_raw(tmp_path)
    os.makedirs(os.path.join(ctx.repo, "docs"), exist_ok=True)
    with open(os.path.join(ctx.repo, "docs", "my notes.md"), "w",
              encoding="utf-8") as fh:
        fh.write("l1\nl2\n")
    c1 = "[n](file://docs/my%20notes.md#L1-L2)"
    c2 = "[n](file://docs/my notes.md#L1-L2)"
    errs, cited = gates.validate_citations(c1 + "\n" + c2 + "\n", ctx.repo)
    assert errs == [], errs
    assert cited == {"docs/my notes.md"}, cited
    assert gates.extract_citations(c1)[0]["path"] == "docs/my notes.md"
    # 发射侧: 重建的 cite 块用 %20 编码, 且能被 CITE_RE 认回
    rebuilt = gates.rebuild_cite_block("# T\n\n" + c1 + "\n", ctx.repo)
    assert "file://docs/my%20notes.md" in rebuilt
    errs2, cited2 = gates.validate_citations(rebuilt, ctx.repo)
    assert errs2 == [] and cited2 == {"docs/my notes.md"}


def test_p2_42_fenced_citations_are_not_evidence(tmp_path):
    """#42: 栅栏内的示例引用曾既被当真硬拒, 又能凑 D16 行级下限。"""
    from pwiki_lib import gates
    ctx = _ctx_raw(tmp_path)
    content = ("# T\n\n## 概述\n\nx [src/a.py:1-2](file://src/a.py#L1-L2)\n\n"
               "```markdown\n[e](file://no/such/file.py#L1-L2)\n```\n")
    errs, _ = gates.gate_item(content, {"kind": "page", "title": "T"},
                              ctx.repo, "concise", phase="submit")
    assert not any("citation/path" in e for e in errs), \
        "栅栏里的示例引用被当真硬拒"
    card = ("---\nkind: external_dependency\nname: 卡\n---\n\n"
            "## 1. 总体方案\n\n事实。\n\n```markdown\n"
            "[a](file://src/a.py#L1)\n[b](file://src/a.py#L2)\n"
            "[c](file://src/a.py#L3)\n```\n")
    errs2, _ = gates.gate_item(card, {"kind": "card", "title": "卡",
                                      "card_kind": "external_dependency"},
                               ctx.repo, "deep", phase="lining")
    assert any("knowledge/citations" in e for e in errs2), \
        "栅栏-only 引用凑满了 D16 行级下限"


def test_p2_43_contents_heading_warned(tmp_path):
    """#43: '## Contents' 曾过得了门, 发布层却把它整节删掉。"""
    from pwiki_lib import gates
    ctx = _ctx_raw(tmp_path)
    content = ("# T\n\n## Contents\n\n- [x](#x)\n\n## 正文\n\nx "
               "[src/a.py:1-2](file://src/a.py#L1-L2)\n")
    _errs, warns = gates.gate_item(content, {"kind": "page", "title": "T"},
                                   ctx.repo, "concise", phase="submit")
    assert any("schema/toc" in w for w in warns), \
        "英文目录节未被门禁提示(与剥除侧 TOC_SECTION_RE 漂移)"


def test_p2_44_summary_strips_fenced_code(tmp_path):
    """#44: 摘要提取的手搓栅栏跳过曾让示例代码漏进 240 字摘要, 随 prompt
    注入后续 writer。"""
    from pwiki_lib import engine
    repo, root = _fixture(tmp_path, style="concise")
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    ctx = _ctx(repo, root)
    run = ctx.db.active_run(ctx.partition()["id"])
    job = None
    while job is None or job["kind"] != "page":
        claimed = ctx.db.claim_jobs(run["id"], count=1)
        if not claimed:
            raise AssertionError("没有 page job")
        job = claimed[0]
    sec = "\n**章节来源**\n- [README.md:1](file://README.md#L1)\n"
    draft = os.path.join(root, "work", f"{job['page_id']}.md")
    os.makedirs(os.path.dirname(draft), exist_ok=True)
    with open(draft, "w", encoding="utf-8") as fh:
        fh.write(f"# {job['title']}\n\n## 概述\n\n示例：\n```python\n"
                 f"def f(): pass\n```\n{sec}\n## 架构\n\nx\n{sec}")
    args = types.SimpleNamespace(
        **{"repo": repo.path, "root": root, "locale": "zh",
           "page_id": job["page_id"], "file": draft, "summary": "",
           "key_topics": "", "report": None, "lenient": False,
           "force": False, "claim": job["claim_token"]})
    assert engine.cmd_submit(args) == config.EXIT_OK
    row = ctx.db.job(run["id"], job["page_id"])
    assert row["summary"] == "示例：", \
        f"栅栏内容漏进摘要: {row['summary']!r}"
    assert "def f" not in (row["summary"] or "")


def test_p2_45_templates_use_title_placeholder():
    """#45: 五张速查表模板曾硬编码中文 H1 —— 非 zh wiki 照模板写必被
    schema/h1 硬拒(与 page.md 的 {{TITLE}} 占位对齐)。"""
    tdir = os.path.join(os.path.dirname(__file__), "..", "templates")
    for name in ("module-architecture.md", "module-commands.md",
                 "module-conventions.md", "module-overview.md",
                 "module-stack.md"):
        with open(os.path.join(tdir, name), encoding="utf-8") as fh:
            first = fh.read().splitlines()[0]
        assert first.strip() == "# {{TITLE}}", (name, first)


def test_p2_46_47_49_yamlite_loud_failures():
    """#46/#47/#49: Tab 缩进静默重排 / 重复键静默 last-wins / 锚点别名
    降级成假字符串 —— 人工编辑面一律显式报错。"""
    from pwiki_lib.yamlite import yaml_load
    with pytest.raises(ValueError):
        yaml_load("wiki:\n\ttitle: Repo\n")
    with pytest.raises(ValueError):
        yaml_load("a: 1\na: 2\n")
    with pytest.raises(ValueError):
        yaml_load("scope: *default_scope\n")


def test_p2_48_empty_dict_roundtrip():
    """#48: 空 dict 曾发射并解析回字符串 "{}" —— 静默类型翻转。"""
    from pwiki_lib.yamlite import yaml_dump, yaml_load
    v = yaml_load(yaml_dump({"a": {}, "mods": [{"e": {}}]}))
    assert v["a"] == {} and v["mods"][0]["e"] == {}


def test_p2_50_indexes_tolerate_corrupt_json_rows(tmp_path):
    """#50: 同一坏行 gate 侧吞成 [], _write_indexes 却裸 JSONDecodeError
    —— 发布容错(gate 已挡在先, 这里兜旧库/手改 DB)。"""
    from pwiki_lib import publish
    ctx = _ctx_raw(tmp_path)
    part = {"id": 1, "branch": "main", "locale": "zh"}
    rows = [{
        "page_id": "p1", "title": "P", "parent_id": "", "ordinal": 0,
        "kind": "page", "module": "", "sheet": "", "card_kind": "",
        "path": "zh/content/P.md", "prompt": "", "source_files": "not-json",
        "article_type": "", "slug": "p1", "objective": "", "modules": "null",
        "summary": "", "key_topics": "{bad", "style": "", "scope": "[]",
        "required_sections": "[]", "title_check": 0, "pack_key": "",
        "content": "# P\n",
    }]
    publish._write_indexes(ctx, part, rows, [], "manual", "concise")
    assert os.path.isfile(os.path.join(ctx.root, "zh", "content",
                                       "_index.yaml"))


# =========================================================================== #
# P3(#53-#73): 重复收敛 / 死代码 / 性能 / 文档契约
# =========================================================================== #

def test_p3_53_card_job_helper():
    """#53: 卡片 job 字面量统一 planner.card_job(四个排队入口曾各抄一份,
    snapshot 那份漂移出遗留析取)。"""
    from pwiki_lib.planner import card_job
    job = card_job({"id": "c", "kind": "external_dependency", "title": "构建",
                    "scope": ["src/"]}, "zh")
    assert job["source_files"] == ["src/"], "source_files 应回退 scope"
    assert job["path"].replace(os.sep, "/").startswith("knowledge/zh/")
    assert card_job({"id": "c2", "kind": "external_dependency", "title": "卡",
                     "source_files": ["a.py"]}, "zh")["source_files"] == ["a.py"]


def test_p3_54_report_alias_channel_persisted(tmp_path):
    """#54: --report 曾双解析且键枚举各持一份 —— 别名拼写进了 run_log
    却漏出 job 行报告。"""
    import json as _json
    from pwiki_lib import engine
    repo, root = _fixture(tmp_path, style="concise")
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    ctx = _ctx(repo, root)
    run = ctx.db.active_run(ctx.partition()["id"])
    job = None
    while job is None or job["kind"] != "page":
        claimed = ctx.db.claim_jobs(run["id"], count=1)
        assert claimed
        job = claimed[0]
    sec = "\n**章节来源**\n- [README.md:1](file://README.md#L1)\n"
    draft = os.path.join(root, "work", f"{job['page_id']}.md")
    os.makedirs(os.path.dirname(draft), exist_ok=True)
    with open(draft, "w", encoding="utf-8") as fh:
        fh.write(f"# {job['title']}\n\n## 概述\n\nx\n{sec}\n## 架构\n\n"
                 f"x\n{sec}## 组件\n\nx\n{sec}## 数据流\n\nx\n{sec}"
                 f"## 配置\n\nx\n{sec}")
    report = os.path.join(root, "work", "report.json")
    with open(report, "w", encoding="utf-8") as fh:
        # 别名拼写(单数形 files_read_beyond)
        _json.dump({"files_read_beyond": ["services/user/m0.py"],
                    "uncertainties": ["示例不确定点"]}, fh, ensure_ascii=False)
    args = types.SimpleNamespace(
        **{"repo": repo.path, "root": root, "locale": "zh",
           "page_id": job["page_id"], "file": draft, "summary": "",
           "key_topics": "", "report": report, "lenient": False,
           "force": False, "claim": job["claim_token"]})
    assert engine.cmd_submit(args) == config.EXIT_OK
    row = ctx.db.job(run["id"], job["page_id"])
    persisted = _json.loads(row["report"])
    assert persisted.get("files_read_beyond_sources") == \
        ["services/user/m0.py"], persisted


def test_p3_55_no_duplicate_edges(tmp_path):
    """#55: PARENT_CHILD 边曾在两个循环各发一份 —— 每条边在
    repowiki-metadata 里逐字出现两次。"""
    import json as _json
    repo, root = _published_custom(tmp_path)
    meta = _json.load(open(os.path.join(root, "zh", "meta",
                                        "repowiki-metadata.json"),
                           encoding="utf-8"))
    edges = [(e["source_id"], e["target_id"], e["relationship_type"])
             for e in meta["knowledge_relations"]]
    assert edges and len(edges) == len(set(edges)), \
        f"重复边: {[e for e in edges if edges.count(e) > 1][:3]}"


def test_p3_56_humanize_single_source():
    """#56: 两份同名 humanize 曾各自漂移(modules 认表不剥扩展名,
    themes 剥扩展名不认表)—— 同一目录两条路由两个标题。"""
    from pwiki_lib import modules, themes
    assert modules.humanize("main.py") == "Main" == themes.humanize("main.py")
    assert themes.humanize("client") == modules.humanize("client") \
        == modules.TITLE_MAP["client"]


def test_p3_59_module_path_parity(tmp_path):
    """#59: 模块身份键曾三种推导 —— _index.yaml 记 'auth' 而 _module.yaml
    记 'auth/', Qoder 两个注册表看到两个节点。"""
    from pwiki_lib.yamlite import yaml_load
    repo, root = _published_custom(tmp_path)
    kbase = os.path.join(root, "knowledge", "zh")
    with open(os.path.join(kbase, "_index.yaml"), encoding="utf-8") as fh:
        kindex = yaml_load(fh.read())
    index_paths = {m.get("module_path")
                   for m in (kindex.get("modules") or {}).values()}
    module_yamls = []
    for dp, _dn, fns in os.walk(kbase):
        if "_module.yaml" in fns:
            with open(os.path.join(dp, "_module.yaml"),
                      encoding="utf-8") as fh:
                module_yamls.append(yaml_load(fh.read()))
    assert module_yamls, "夹具应产出 _module.yaml"
    for my in module_yamls:
        assert my.get("module_path") in index_paths, \
            f"_module.yaml 记 {my.get('module_path')!r} 不在 _index.yaml 的" \
            f" {sorted(index_paths)} 里 —— 两注册表键形分叉"
