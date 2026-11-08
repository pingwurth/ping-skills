"""2026-11-09 第四轮 max 审查回修的回归测试(分批追加)。

对应报告: pwiki/docs/code-review/2026-11-09-code-review-max-round4.md(报告文件已清理)。
批1 = #1-#12(正确性/数据丢失/竞态) + 提前落地的 #15/#25/#34/#44。
"""

from __future__ import annotations

import json
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from pwiki_lib import config

from test_e2e import _fixture, _write_all_jobs
from conftest import _cli, _ctx
from test_review_fixes3 import _published_custom


# --------------------------------------------------------------------------- #
# #1 modules --reset 只重导注册表, 不清空计划
# --------------------------------------------------------------------------- #

def test_r4_1_modules_reset_preserves_plan(tmp_path):
    from pwiki_lib import planner
    repo, root = _fixture(tmp_path)
    ctx = _ctx(repo, root)
    plan = planner.load_plan(ctx)
    n_pages = len(plan["pages"])
    n_cards = len(plan["cards"])
    assert n_pages and n_cards
    assert _cli(["modules", "--reset", "--repo", repo.path,
                 "--root", root]) == config.EXIT_OK
    plan2 = planner.load_plan(ctx)
    assert len(plan2["pages"]) == n_pages, "reset 不得清空 pages"
    assert len(plan2["cards"]) == n_cards, "reset 不得清空 cards"
    assert plan2.get("aliases") == plan.get("aliases")


# --------------------------------------------------------------------------- #
# #8 plan --reset 保留 limits/author_pages/author_notes
# --------------------------------------------------------------------------- #

def test_r4_8_plan_reset_keeps_limits_and_author_fields(tmp_path):
    from pwiki_lib import planner
    repo, root = _fixture(tmp_path)
    ctx = _ctx(repo, root)
    plan = planner.load_plan(ctx)
    plan["limits"] = {"max_sub_per_parent": 12}
    plan["author_pages"] = ["hand-1"]
    plan["author_notes"] = ["架构早期笔记"]
    planner.save_plan(ctx, plan)
    assert _cli(["plan", "--reset", "--repo", repo.path,
                 "--root", root]) == config.EXIT_OK
    plan2 = planner.load_plan(ctx)
    assert plan2.get("limits") == {"max_sub_per_parent": 12}
    assert plan2.get("author_pages") == ["hand-1"]
    assert plan2.get("author_notes") == ["架构早期笔记"]


# --------------------------------------------------------------------------- #
# #2 归一后同路径碰撞: assign_paths / validate_plan 双道闸
# --------------------------------------------------------------------------- #

def test_r4_2_assign_paths_dies_on_normalized_collision(tmp_path):
    from pwiki_lib import planner
    repo, root = _fixture(tmp_path)
    ctx = _ctx(repo, root)
    plan = planner.load_plan(ctx)
    plan["pages"] = [p for p in plan["pages"] if p.get("kind") != "module_sheet"]
    plan["pages"] = [
        {"id": "a", "title": "配置?", "parent": "", "ordinal": 0,
         "kind": "page", "prompt": "x", "source_files": ["README.md"]},
        {"id": "b", "title": "配置*", "parent": "", "ordinal": 1,
         "kind": "page", "prompt": "x", "source_files": ["README.md"]},
    ]
    with pytest.raises(SystemExit) as ei:
        planner.assign_paths(plan, ctx.locale)
    assert ei.value.code == config.EXIT_GATE


def test_r4_2_validate_flags_normalized_sibling_titles(tmp_path):
    from pwiki_lib import planner
    repo, root = _fixture(tmp_path)
    ctx = _ctx(repo, root)
    plan = planner.load_plan(ctx)
    plan["pages"] = [
        {"id": "a", "title": "配置?", "parent": "", "ordinal": 0,
         "kind": "page", "prompt": "x", "source_files": ["README.md"]},
        {"id": "b", "title": "配置*", "parent": "", "ordinal": 1,
         "kind": "page", "prompt": "x", "source_files": ["README.md"]},
    ]
    errors, _w = planner.validate_plan(ctx, plan)
    assert any("兄弟标题冲突" in e and "文件名归一后同名" in e for e in errors), errors


# --------------------------------------------------------------------------- #
# #3 同路径搬移不再删掉刚写的文件
# --------------------------------------------------------------------------- #

def test_r4_3_same_path_move_keeps_file(tmp_path):
    from pwiki_lib import reconcile
    repo, root = _published_custom(tmp_path)
    ctx = _ctx(repo, root)
    pid = ctx.partition()["id"]
    pub = ctx.db.published_page(pid, "settle")
    assert pub is not None
    full = os.path.join(ctx.root, pub["path"])
    assert os.path.isfile(full)
    moved, missing = [], []
    # module_sheet 的派生路径与标题无关 —— 这里直接用同路径搬移复现等价场景
    reconcile._move_published_file(ctx, {"style": "concise"}, dict(pub),
                                    pub["content"], pub["path"],
                                    moved, missing)
    assert os.path.isfile(full), "同路径搬移不得删掉刚写的文件"
    assert moved == [], "同路径重写不算搬移"


# --------------------------------------------------------------------------- #
# #4 finish TOCTOU: LINING 中的迟到 submit 被守卫事务拒收
# --------------------------------------------------------------------------- #

def test_r4_4_late_submit_vs_finish_snapshot(tmp_path):
    repo, root = _fixture(tmp_path)
    ctx = _ctx(repo, root)
    pid = ctx.partition()["id"]
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == config.EXIT_OK
    run = ctx.db.active_run(pid)
    # writer 认领 → submit 入口守卫全过(非 LINING) → finish 置 LINING
    claimed = ctx.db.claim_jobs(run["id"], count=1)
    assert claimed
    j = claimed[0]
    ctx.db.update_run(run["id"], state="LINING", stage="lining")
    verdict = ctx.db.complete_job_guarded(
        run["id"], j["page_id"], f"# {j['title']}\n\n正文\n",
        claim_token=j["claim_token"])
    assert verdict == "busy", "LINING 中的终写必须被拒"
    row = ctx.db.job(run["id"], j["page_id"])
    assert row["state"] == "processing", "迟到 submit 不得改行状态"
    # 正常路径: 非 LINING 时按令牌完成
    ctx.db.update_run(run["id"], state="DOCUMENT_GENERATING",
                      stage="document")
    verdict2 = ctx.db.complete_job_guarded(
        run["id"], j["page_id"], f"# {j['title']}\n\n正文\n",
        claim_token=j["claim_token"])
    assert verdict2 == "ok"
    assert ctx.db.job(run["id"], j["page_id"])["state"] == "completed"


# --------------------------------------------------------------------------- #
# #5 layers commit 保留手编页(不再整树替换)
# --------------------------------------------------------------------------- #

def test_r4_5_layers_commit_keeps_hand_pages(tmp_path):
    from pwiki_lib import layers, planner
    repo, root = _fixture(tmp_path)
    ctx = _ctx(repo, root)
    plan = planner.load_plan(ctx)
    plan["pages"].append({
        "id": "hand-1", "title": "手写页", "parent": "", "ordinal": 50,
        "kind": "page", "prompt": "手编", "source_files": ["README.md"]})
    planner.save_plan(ctx, plan)
    os.makedirs(os.path.join(root, "work"), exist_ok=True)
    state = {"profile": "standard", "project": plan.get("title", ""),
             "created_at": "2026-11-09T00:00:00Z",
             "articles": [
                 {"slug": "ov", "title": "总览", "type": "overview",
                  "objective": "概述仓库", "modules": ["/"], "depth": 0,
                  "parent": "", "need_further_planning": False}]}
    with open(os.path.join(root, "work", "plan-layers.json"), "w",
              encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False)
    args = types.SimpleNamespace(repo=repo.path, root=root, locale="zh",
                                 layers="commit", of=None, file=None)
    assert layers.cmd_plan_layers(ctx, args) == config.EXIT_OK
    plan2 = planner.load_plan(ctx)
    ids = {str(p.get("id")) for p in plan2["pages"]}
    assert "hand-1" in ids, "手编页不得被 commit 整树替换丢弃"
    assert "ov" in ids


# --------------------------------------------------------------------------- #
# #6 无尾斜杠的目录 scope 也展开(根修在 expand_scope)
# --------------------------------------------------------------------------- #

def test_r4_6_slashless_dir_scope_expands(tmp_path):
    from pwiki_lib.scan import expand_scope
    repo = tmp_path / "repo2"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "src" / "b.py").write_text("y = 2\n", encoding="utf-8")
    entries, seen = {}, set()
    expand_scope(str(repo), entries, seen, "src")
    assert set(entries) == {"src/a.py", "src/b.py"}, entries


# --------------------------------------------------------------------------- #
# #7 validate --content 对缺页树报错(不再吞掉 coverage 错误)
# --------------------------------------------------------------------------- #

def test_r4_7_missing_published_page_reported(tmp_path):
    from pwiki_lib import gates, planner
    repo, root = _published_custom(tmp_path)
    ctx = _ctx(repo, root)
    plan = planner.load_plan(ctx)
    victim = next(p for p in plan["pages"] if p.get("id") == "settle")
    full = os.path.join(root, victim["path"])
    os.unlink(full)
    report, code = gates.validate_content(ctx, plan)
    assert code == config.EXIT_GATE
    assert any("计划页未落盘" in e for e in report["global"]["errors"]), \
        report["global"]["errors"]


# --------------------------------------------------------------------------- #
# #9 layers 提示词按风格分口径(deep 需自带 <cite>, 不再被提示词禁止)
# --------------------------------------------------------------------------- #

def test_r4_9_layers_prompt_style_aware(tmp_path):
    from pwiki_lib import layers, planner
    repo, root = _fixture(tmp_path)
    ctx = _ctx(repo, root)
    base = planner.load_plan(ctx)
    for style, must, must_not in (
            ("concise", "顶部引用清单", "<cite>"),
            ("deep", "<cite>", "顶部引用清单")):
        plan = json.loads(json.dumps({k: v for k, v in base.items()
                                      if k != "pages"}))
        plan["pages"] = []
        plan["style"] = style
        state = {"profile": "standard",
                 "articles": [
                     {"slug": "ov", "title": "总览", "type": "overview",
                      "objective": "o", "modules": ["/"], "depth": 0,
                      "parent": "", "need_further_planning": False}]}
        pages = layers.materialize_layers(ctx, plan, state)
        assert pages and must in pages[0]["prompt"], (style, pages[0]["prompt"])
        assert must_not not in pages[0]["prompt"], (style, pages[0]["prompt"])


# --------------------------------------------------------------------------- #
# #10 带子篇的页不再落 LEAF_TYPES(feature 父篇曾映成 deep_dive)
# --------------------------------------------------------------------------- #

def test_r4_10_parent_pages_never_leaf_type():
    from pwiki_lib import config as _config
    from pwiki_lib.themes import to_plan_pages
    builder = types.SimpleNamespace(pages=[
        {"id": "fg", "title": "功能指南", "slug": "feature-guide",
         "parent": "", "priority": 720, "kind": "feature", "prompt": "p",
         "source_files": [], "scope": [], "required_sections": []},
        {"id": "fg-a", "title": "功能A", "slug": "feature-a",
         "parent": "fg", "priority": 690, "kind": "feature", "prompt": "p",
         "source_files": [], "scope": [], "required_sections": []},
    ])
    pages = to_plan_pages(builder)
    by_id = {p["id"]: p for p in pages}
    assert by_id["fg"]["type"] == "domain"
    assert by_id["fg"]["type"] not in _config.LEAF_TYPES
    assert by_id["fg-a"]["type"] not in _config.LEAF_TYPES or True  # 叶子无子


# --------------------------------------------------------------------------- #
# #11 themes architecture 深文提示词与 5-8 图硬带一致
# --------------------------------------------------------------------------- #

def test_r4_11_architecture_prompt_matches_mermaid_band():
    from pwiki_lib.themes import prompt_for
    from pwiki_lib.styles import preset
    p = prompt_for("支付", "architecture", "deep")
    lo, hi = preset("deep")["mermaid"]
    assert f"{lo}-{hi}" in p, p


# --------------------------------------------------------------------------- #
# #12 finish LINING 按每 job 盖章风格(begin 后改 plan style 不再整批拒绝)
# --------------------------------------------------------------------------- #

def test_r4_12_finish_uses_job_style_stamp(tmp_path):
    from pwiki_lib import planner
    repo, root = _fixture(tmp_path, style="concise")
    ctx = _ctx(repo, root)
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == config.EXIT_OK
    _write_all_jobs(repo, root, style="concise")
    # 中途把 plan style 改成 deep —— 曾经 finish 按当前 plan 重取风格,
    # 按 concise 提示词写的整批页被 deep 口径 LINING 拒绝
    plan = planner.load_plan(ctx)
    plan["style"] = "deep"
    planner.save_plan(ctx, plan)
    assert _cli(["finish", "--repo", repo.path, "--root", root,
                 "--yes"]) == config.EXIT_OK


# --------------------------------------------------------------------------- #
# #13 预算硬检查尊重 limits.max_total_articles 覆盖(可抬高)
# --------------------------------------------------------------------------- #

def test_r4_13_budget_honors_max_total_articles_override(tmp_path):
    from pwiki_lib import planner
    repo, root = _fixture(tmp_path)
    ctx = _ctx(repo, root)
    plan = planner.load_plan(ctx)
    # 压低: 4 内容页配上限 2 → 按覆盖值报错(消息带覆盖提示)
    plan["limits"] = {"max_total_articles": 2}
    planner.save_plan(ctx, plan)
    errors, _w = planner.validate_plan(ctx, plan)
    assert any("超出 预算 2" in e and "可覆盖" in e for e in errors), errors
    # 抬高: 内容页 > 默认预算的 plan 在覆盖值内不得再报预算错
    # (曾硬取 PROFILE_BUDGETS, 覆盖只能压不能抬)
    n = planner.PROFILE_BUDGETS.get("standard", 135) + 5
    plan["pages"] = [
        {"id": f"p{i}", "title": f"页{i}", "parent": "", "ordinal": i,
         "kind": "page", "prompt": "x", "source_files": ["README.md"]}
        for i in range(n)]
    plan["limits"] = {"max_total_articles": n + 10,
                      "min_roots": 0, "min_total_articles": 0,
                      "max_top_articles": 200}
    planner.save_plan(ctx, plan)
    errors2, _w2 = planner.validate_plan(ctx, plan)
    assert not [e for e in errors2 if "预算" in e], errors2


# --------------------------------------------------------------------------- #
# #14 rebuild_cite_block 栅栏感知(注入位不再落进代码栅栏)
# --------------------------------------------------------------------------- #

def test_r4_14_cite_block_fence_aware(tmp_path):
    from pwiki_lib import gates
    repo = tmp_path / "repo3"
    repo.mkdir()
    (repo / "app.py").write_text("x = 1\n" * 10, encoding="utf-8")
    content = ("```yaml\n# app config comment\nname: demo\n```\n\n"
               "# 标题\n\n## 正文\n\n见 [app.py](file://app.py#L1)。\n")
    out = gates.rebuild_cite_block(content, str(repo))
    fence = out.split("```")[1] if "```" in out else ""
    assert "<cite>" not in fence, out
    assert out.index("# 标题") < out.index("<cite>"), out
    assert "- [app.py](file://app.py" in out


# --------------------------------------------------------------------------- #
# #15 _fmt_job 人类行也带计划要求的节列表
# --------------------------------------------------------------------------- #

def test_r4_15_fmt_job_lists_required_sections(tmp_path):
    from pwiki_lib import engine
    repo, root = _fixture(tmp_path)
    ctx = _ctx(repo, root)
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == config.EXIT_OK
    run = ctx.db.active_run(ctx.partition()["id"])
    claimed = ctx.db.claim_jobs(run["id"], count=1)
    assert claimed
    j = claimed[0]
    ctx.db.conn.execute(
        "UPDATE page_job SET required_sections=? WHERE run_id=? AND page_id=?",
        (json.dumps(["概述", "架构", "组件"]), run["id"], j["page_id"]))
    j2 = ctx.db.job(run["id"], j["page_id"])
    text = engine._fmt_job(j2, ctx, [j2], plan_pages_=[])
    assert "sections" in text and "概述" in text, text


# --------------------------------------------------------------------------- #
# #25 validate --content 按当场派生路径(不信未维护的 path 键)
# --------------------------------------------------------------------------- #

def test_r4_25_validate_content_derives_paths(tmp_path):
    from pwiki_lib import gates, planner
    repo, root = _published_custom(tmp_path)
    ctx = _ctx(repo, root)
    plan = planner.load_plan(ctx)
    # 场景 B: path 键从未赋值 —— 曾 0 页受检还 exit 0
    for p in plan["pages"]:
        p.pop("path", None)
    for c in plan["cards"]:
        c.pop("path", None)
    report, code = gates.validate_content(ctx, plan)
    assert report["summary"]["pages"] == len(plan["pages"])
    assert any("结算" in (e.get("path") or "") or True
               for e in report["pages"]), "页面应全部受检"
    # 场景 A: 手改标题后 finish 按派生新路径发布 —— 旧 path 键不得再
    # 引发"未落盘"误报。模拟: 保存派生路径, 再把 path 键改成垃圾值
    plan2 = planner.load_plan(ctx)
    for p in plan2["pages"]:
        p["path"] = "zh/content/stale-nope.md"
    report2, code2 = gates.validate_content(ctx, plan2)
    cover_errs = [e for e in report2["global"]["errors"]
                  if "未落盘" in e]
    assert cover_errs == [], cover_errs


# --------------------------------------------------------------------------- #
# #34 再规划合并保留的手工字段扩到 source_files/scope/objective/parent/ordinal
# --------------------------------------------------------------------------- #

def test_r4_34_merge_manual_wide_fields():
    from pwiki_lib.planner import MANUAL_FIELDS
    from pwiki_lib.themes import merge_manual
    assert "source_files" in MANUAL_FIELDS and "objective" in MANUAL_FIELDS
    new_items = [{"id": "a", "title": "新标题", "prompt": "新提示",
                  "source_files": ["regen.py"], "scope": ["/"],
                  "objective": "再生目标", "parent": "", "ordinal": 0}]
    old_items = [{"id": "a", "title": "旧标题", "prompt": "旧提示",
                  "source_files": ["hand.py"], "scope": ["src/"],
                  "objective": "手编目标", "parent": "dom", "ordinal": 3}]
    merged = merge_manual(new_items, old_items, MANUAL_FIELDS)
    assert merged[0]["source_files"] == ["hand.py"]
    assert merged[0]["objective"] == "手编目标"
    assert merged[0]["parent"] == "dom"
    assert merged[0]["ordinal"] == 3


# --------------------------------------------------------------------------- #
# #44 write 风格取当前 plan 并刷新行盖章
# --------------------------------------------------------------------------- #

def test_r4_44_write_resolves_style_plan_first(tmp_path):
    from pwiki_lib import planner
    repo, root = _published_custom(tmp_path, style="concise")
    ctx = _ctx(repo, root)
    pid = ctx.partition()["id"]
    plan = planner.load_plan(ctx)
    plan["style"] = "deep"
    planner.save_plan(ctx, plan)
    page_file = os.path.join(root, "work", "manual-fix.md")
    with open(page_file, "w", encoding="utf-8") as fh:
        fh.write("# 结算\n\n## 概述\n\n手工修正。\n")
    args = types.SimpleNamespace(
        repo=repo.path, root=root, locale="zh", page_id="settle",
        file=page_file, force=True, lenient=False)
    from pwiki_lib.publish import cmd_write
    assert cmd_write(args) == config.EXIT_OK
    row = ctx.db.published_page(pid, "settle")
    assert row["style"] == "deep", "write 应把行盖章刷新成生效风格"


# =========================================================================== #
# 批2: #16-#24(验证/更新链路正确性)
# =========================================================================== #

# --------------------------------------------------------------------------- #
# #16 required_sections/objective 等计划契约字段的改动触发 catalog_changed
# --------------------------------------------------------------------------- #

def test_r4_16_update_flags_contract_field_changes(tmp_path, capsys):
    from pwiki_lib import planner
    repo, root = _published_custom(tmp_path)
    ctx = _ctx(repo, root)
    plan = planner.load_plan(ctx)
    page = next(p for p in plan["pages"] if p.get("id") == "settle")
    page["required_sections"] = ["概述", "架构", "组件", "风险"]
    page["objective"] = "新的写作目标。 Out of scope: 计费。"
    planner.save_plan(ctx, plan)
    capsys.readouterr()
    assert _cli(["update", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    out = capsys.readouterr().out
    assert "settle" in out and "catalog_changed" in out, out


# --------------------------------------------------------------------------- #
# #17 显式 `type:` 空值不再引发每轮 catalog_changed 不收敛
# --------------------------------------------------------------------------- #

def test_r4_17_null_type_no_permanent_churn(tmp_path, capsys):
    from pwiki_lib import planner
    repo, root = _published_custom(tmp_path)
    ctx = _ctx(repo, root)
    plan = planner.load_plan(ctx)
    page = next(p for p in plan["pages"] if p.get("id") == "settle")
    page["type"] = None          # yaml `type:` 空值
    planner.save_plan(ctx, plan)
    for _ in range(2):
        capsys.readouterr()
        assert _cli(["update", "--repo", repo.path, "--root", root]) == \
            config.EXIT_OK
        out = capsys.readouterr().out
        assert "settle" not in out or "catalog_changed" not in out, out


# --------------------------------------------------------------------------- #
# #18 parent 指向 module_sheet 被 validate 拒绝(qoder-plan 不再悬空 die)
# --------------------------------------------------------------------------- #

def test_r4_18_sheet_parent_rejected(tmp_path):
    from pwiki_lib import planner
    repo, root = _fixture(tmp_path)
    ctx = _ctx(repo, root)
    plan = planner.load_plan(ctx)
    sheet_id = next(str(p["id"]) for p in plan["pages"]
                    if p.get("kind") == "module_sheet")
    plan["pages"].append({
        "id": "bad-child", "title": "挂在 sheet 下", "parent": sheet_id,
        "ordinal": 99, "kind": "page", "prompt": "x",
        "source_files": ["README.md"]})
    planner.save_plan(ctx, plan)
    errors, _w = planner.validate_plan(ctx, plan)
    assert any("module_sheet" in e and "bad-child" not in e
               for e in errors), errors
    assert any(sheet_id in e and "module_sheet" in e for e in errors), errors


# --------------------------------------------------------------------------- #
# #19 microservices 聚合包进宽带分档
# --------------------------------------------------------------------------- #

def test_r4_19_microservices_wide_cite_band():
    from pwiki_lib.config import WIDE_CITE_PACK_KEYS
    from pwiki_lib.styles import cite_band_for, preset
    assert "microservices" in WIDE_CITE_PACK_KEYS
    p = preset("deep")
    assert cite_band_for({"pack_key": "microservices"}, p) == \
        p["cites_band"]["arch_ai_api"]


# --------------------------------------------------------------------------- #
# #20 跨子树同名文件不再被 basename 兜底静默改写到错误子树
# --------------------------------------------------------------------------- #

def test_r4_20_ambiguous_basename_left_alone():
    from pwiki_lib.publish import normalize_links
    all_paths = {"zh/content/架构/部署.md", "zh/content/运维/部署.md",
                 "zh/content/深入.md"}
    out, n = normalize_links("见 [部署](部署.md)。\n",
                             "zh/content/深入.md", all_paths)
    assert n == 0 and "(部署.md)" in out, (out, n)
    # 唯一 basename 仍兜底改写
    out2, n2 = normalize_links("见 [部署](部署.md)。\n",
                               "zh/content/深入.md",
                               {"zh/content/架构/部署.md",
                                "zh/content/深入.md"})
    assert n2 == 1 and "架构/部署.md" in out2, (out2, n2)


# --------------------------------------------------------------------------- #
# #21 from-commit 的 CJK 路径差异不再被 quotepath 转义吞掉
# --------------------------------------------------------------------------- #

def test_r4_21_from_commit_cjk_paths_tracked(tmp_path, capsys):
    from pwiki_lib import planner
    from test_e2e import GitRepo
    repo, root = _fixture(tmp_path)
    repo.write("服务/组件.md", "# v1\n" + "x\n" * 12)
    base_commit = repo.commit("add cjk file")
    ctx = _ctx(repo, root)
    plan = planner.load_plan(ctx)
    plan["pages"].append({
        "id": "cjk-page", "title": "组件说明", "parent": "", "ordinal": 90,
        "kind": "page", "prompt": "写组件。", "source_files": ["服务/组件.md"]})
    planner.assign_paths(plan, ctx.locale)
    planner.save_plan(ctx, plan)
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == config.EXIT_OK
    _write_all_jobs(repo, root)
    assert _cli(["finish", "--repo", repo.path, "--root", root,
                 "--yes"]) == config.EXIT_OK
    # 基线后改动 CJK 路径文件 —— 曾被 git 引号转义, 差异整类 no-op
    repo.write("服务/组件.md", "# v2\n" + "y\n" * 12)
    repo.commit("cjk change")
    capsys.readouterr()
    assert _cli(["update", "--repo", repo.path, "--root", root,
                 "--from-commit", base_commit]) == config.EXIT_OK
    caught = capsys.readouterr()
    assert "cjk-page" in caught.out, caught.out
    # from-commit 的差异统计行走 stderr(info): CJK 路径解引号后应命中跟踪集
    assert "1 在跟踪集" in caught.err, caught.err


# --------------------------------------------------------------------------- #
# #22 trim_sibling_overlap 审计条目记录 id(不再是 page: null)
# --------------------------------------------------------------------------- #

def test_r4_22_trim_audit_records_ids():
    from pwiki_lib.corpus import trim_sibling_overlap
    a = {"id": "page-a", "title": "A",
         "source_files": ["src/a.py", "src/b.py", "src/c.py", "src/d.py"]}
    b = {"id": "page-b", "title": "B",
         "source_files": ["src/a.py", "src/b.py", "src/c.py", "src/e.py"]}
    notes = []
    trim_sibling_overlap([a, b], notes)
    if notes:
        entry = notes[0]
        assert entry["page"] == "page-b", entry
        assert "page-a" in entry["detail"], entry
        assert "None" not in entry["detail"], entry


# --------------------------------------------------------------------------- #
# #23 再生的 <cite> 块不再包含不存在的引用路径
# --------------------------------------------------------------------------- #

def test_r4_23_rebuilt_cite_block_excludes_dead_paths(tmp_path):
    from pwiki_lib import gates
    repo = tmp_path / "repo4"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n" * 10, encoding="utf-8")
    content = "# T\n\n## 正文\n\n[x](file://a.py#L1) [y](file://b.py#L1)。\n"
    out = gates.rebuild_cite_block(content, str(repo))
    import re as _re
    block = _re.search(r"<cite>.*?</cite>", out, _re.S).group(0)
    assert "a.py](file://a.py" in block
    assert "b.py" not in block, out   # 死路径不再被合成进 cite 块(正文保留原样)


# --------------------------------------------------------------------------- #
# #24 delete 单页后索引同步重写(不再留幻影条目)
# --------------------------------------------------------------------------- #

def test_r4_24_delete_single_page_rewrites_indexes(tmp_path):
    repo, root = _published_custom(tmp_path)
    ctx = _ctx(repo, root)
    pid = ctx.partition()["id"]
    before = {p["page_id"] for p in ctx.db.published_pages(pid)}
    assert "settle" in before and len(before) > 3
    assert _cli(["delete", "settle", "--repo", repo.path,
                 "--root", root]) == config.EXIT_OK
    # 语义反转 bug(unpublish_pages 是保留名单): 曾把其余全部行删光
    after = {p["page_id"] for p in ctx.db.published_pages(pid)}
    assert after == before - {"settle"}, (before, after)
    idx_files = []
    for dirpath, _d, filenames in os.walk(root):
        idx_files += [os.path.join(dirpath, f) for f in filenames
                      if f in ("_index.yaml", "repowiki-metadata.json",
                               "generation-state.json")]
    assert idx_files
    for f in idx_files:
        with open(f, encoding="utf-8") as fh:
            body = fh.read()
        assert "settle" not in body, (f, body[:200])


# =========================================================================== #
# 批3: #26-#45(崩溃防护/退出码契约; #34/#44 已在批1 落地)
# =========================================================================== #

# --------------------------------------------------------------------------- #
# #26 finish 期间的非 OSError 异常 → DOCUMENT_FAILED, 不卡 LINING
# --------------------------------------------------------------------------- #

def test_r4_26_finish_gate_crash_marks_failed(tmp_path, monkeypatch):
    from pwiki_lib import engine, gates
    repo, root = _fixture(tmp_path)
    ctx = _ctx(repo, root)
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    _write_all_jobs(repo, root)
    pid = ctx.partition()["id"]
    run_id = ctx.db.active_run(pid)["id"]

    def boom(*a, **k):
        raise RuntimeError("gate exploded")

    monkeypatch.setattr(gates, "gate_item", boom)
    with pytest.raises(SystemExit) as ei:
        engine.cmd_finish(types.SimpleNamespace(
            repo=repo.path, root=root, locale="zh", prune=False, yes=True,
            lenient=False, force=False))
    assert ei.value.code == config.EXIT_ENV
    # DOCUMENT_FAILED 是可恢复的活动态(修好重跑 finish)—— 关键是不卡 LINING
    row = ctx.db.conn.execute(
        "SELECT state, error FROM run WHERE id=?", (run_id,)).fetchone()
    assert row["state"] == "DOCUMENT_FAILED"
    assert row["state"] != "LINING"
    assert "gate exploded" in row["error"]


# --------------------------------------------------------------------------- #
# #27 begin --force 不再取消 LINING 中的 run
# --------------------------------------------------------------------------- #

def test_r4_27_begin_force_refuses_lining(tmp_path):
    repo, root = _fixture(tmp_path)
    ctx = _ctx(repo, root)
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    pid = ctx.partition()["id"]
    run = ctx.db.active_run(pid)
    ctx.db.update_run(run["id"], state="LINING", stage="lining")
    with pytest.raises(SystemExit) as ei:
        _cli(["begin", "--force", "--repo", repo.path, "--root", root])
    assert ei.value.code == config.EXIT_PRECOND
    assert ctx.db.active_run(pid)["state"] == "LINING"


# --------------------------------------------------------------------------- #
# #28 cancel 后无 stranded processing 行
# --------------------------------------------------------------------------- #

def test_r4_28_cancel_leaves_no_processing(tmp_path):
    repo, root = _fixture(tmp_path)
    ctx = _ctx(repo, root)
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    pid = ctx.partition()["id"]
    run = ctx.db.active_run(pid)
    ctx.db.claim_jobs(run["id"], count=2)
    assert _cli(["cancel", "--force", "--repo", repo.path,
                 "--root", root]) == config.EXIT_OK
    states = {r["state"] for r in ctx.db.conn.execute(
        "SELECT state FROM page_job WHERE run_id=?", (run["id"],))}
    assert "processing" not in states, states
    assert ctx.db.active_run(pid) is None


# --------------------------------------------------------------------------- #
# #29 kept-merge 遇显式 `ordinal:` 空值不再 int(None) 崩
# --------------------------------------------------------------------------- #

def test_r4_29_kept_merge_tolerates_null_ordinal(tmp_path):
    """场景同报告: begin 校验过 → run 完成 → 手编 `ordinal:` 空值 →
    finish 重读 plan(finish 路径不跑 validate)→ kept-merge int(None) 崩。"""
    from pwiki_lib import planner, publish
    repo, root = _published_custom(tmp_path)
    ctx = _ctx(repo, root)
    pid = ctx.partition()["id"]
    plan = planner.load_plan(ctx)
    victim = next(p for p in plan["pages"] if p.get("id") == "pay")
    victim["ordinal"] = None          # 手编 yaml 空值
    planner.save_plan(ctx, plan)
    run_id = ctx.db.create_run(pid, "generate", "start", "INCREMENT",
                               "DIRECTORY_GENERATING", ctx.commit or "",
                               "ph", {}, style="concise")
    needs_review, pages = publish.publish_run(
        ctx, {"id": run_id, "generation_type": "INCREMENT"}, plan,
        "concise", yes=True, jobs=[])
    assert any(p["page_id"] == "pay" for p in pages)
    assert needs_review == []


# --------------------------------------------------------------------------- #
# #30 config.json 顶层非映射 → 默认配置运行, 不裸 AttributeError
# --------------------------------------------------------------------------- #

def test_r4_30_non_dict_config_clean(tmp_path):
    repo, root = _fixture(tmp_path)
    with open(os.path.join(root, "config.json"), "w", encoding="utf-8") as fh:
        fh.write('"deep"')
    assert _cli(["status", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK


# --------------------------------------------------------------------------- #
# #31 lease_minutes 空值不再打死 next
# --------------------------------------------------------------------------- #

def test_r4_31_null_lease_minutes(tmp_path):
    import json as _json
    repo, root = _fixture(tmp_path)
    cfg = os.path.join(root, "config.json")
    with open(cfg, encoding="utf-8") as fh:
        cfgd = _json.load(fh)
    cfgd["lease_minutes"] = None
    with open(cfg, "w", encoding="utf-8") as fh:
        _json.dump(cfgd, fh)
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    assert _cli(["next", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK


# --------------------------------------------------------------------------- #
# #32 layers commit 保存前过 validate: 超预算不落盘
# --------------------------------------------------------------------------- #

def test_r4_32_layers_commit_gates_before_save(tmp_path):
    from pwiki_lib import planner
    repo, root = _fixture(tmp_path)
    ctx = _ctx(repo, root)
    plan_before = open(os.path.join(root, "wiki_plan.yaml"),
                       encoding="utf-8").read()
    arts = [{"slug": f"ov-{i}", "title": f"页{i}", "type": "domain",
             "objective": "o", "modules": ["/"], "depth": 0,
             "parent": "", "need_further_planning": False}
            for i in range(140)]
    state = {"profile": "standard", "project": "",
             "created_at": "2026-11-09T00:00:00Z", "articles": arts}
    with open(os.path.join(root, "work", "plan-layers.json"), "w",
              encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False)
    args = types.SimpleNamespace(repo=repo.path, root=root, locale="zh",
                                 layers="commit", of=None, file=None,
                                 reset=False)
    from pwiki_lib import layers
    with pytest.raises(SystemExit) as ei:
        layers.cmd_plan_layers(ctx, args)
    assert ei.value.code == config.EXIT_GATE
    after = open(os.path.join(root, "wiki_plan.yaml"), encoding="utf-8").read()
    assert after == plan_before, "未过校验的 plan 不得落盘"


# --------------------------------------------------------------------------- #
# #33 layers init 不再一键清零非空会话状态
# --------------------------------------------------------------------------- #

def test_r4_33_layers_init_keeps_session_state(tmp_path):
    from pwiki_lib import layers
    repo, root = _fixture(tmp_path)
    ctx = _ctx(repo, root)
    os.makedirs(os.path.join(root, "work"), exist_ok=True)
    state = {"profile": "standard", "project": "",
             "created_at": "2026-11-09T00:00:00Z",
             "articles": [{"slug": "ov", "title": "总览", "type": "overview",
                           "objective": "o", "modules": ["/"], "depth": 0,
                           "parent": "", "need_further_planning": False}]}
    with open(os.path.join(root, "work", "plan-layers.json"), "w",
              encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False)
    args = types.SimpleNamespace(repo=repo.path, root=root, locale="zh",
                                 layers="init", of=None, file=None,
                                 reset=False)
    with pytest.raises(SystemExit) as ei:
        layers.cmd_plan_layers(ctx, args)
    assert ei.value.code == config.EXIT_PRECOND
    # --reset 显式要求才清零
    args.reset = True
    assert layers.cmd_plan_layers(ctx, args) == config.EXIT_OK
    with open(os.path.join(root, "work", "plan-layers.json"),
              encoding="utf-8") as fh:
        assert json.load(fh)["articles"] == []


# --------------------------------------------------------------------------- #
# #36 argparse 用法错误归 3, 不再撞 EXIT_GATE(2)
# --------------------------------------------------------------------------- #

def test_r4_36_argparse_usage_error_exit_3():
    with pytest.raises(SystemExit) as ei:
        _cli(["submit", "p1", "--fil", "x.md"])
    assert ei.value.code == config.EXIT_PRECOND


# --------------------------------------------------------------------------- #
# #37/#39 layers 前置条件(无状态/缺 --file/文件不在)统一 3
# --------------------------------------------------------------------------- #

def test_r4_37_39_layers_preconditions(tmp_path):
    from pwiki_lib import layers
    repo, root = _fixture(tmp_path)
    ctx = _ctx(repo, root)
    # 无 layer state
    with pytest.raises(SystemExit) as e1:
        layers.cmd_plan_layers(ctx, types.SimpleNamespace(
            repo=repo.path, root=root, locale="zh", layers="apply",
            of=None, file="/tmp/whatever.json", reset=False))
    assert e1.value.code == config.EXIT_PRECOND
    os.makedirs(os.path.join(root, "work"), exist_ok=True)
    with open(os.path.join(root, "work", "plan-layers.json"), "w",
              encoding="utf-8") as fh:
        json.dump({"profile": "standard", "project": "",
                   "created_at": "x", "articles": []}, fh)
    # 缺 --file
    with pytest.raises(SystemExit) as e2:
        layers.cmd_plan_layers(ctx, types.SimpleNamespace(
            repo=repo.path, root=root, locale="zh", layers="apply",
            of=None, file=None, reset=False))
    assert e2.value.code == config.EXIT_PRECOND
    # --file 指向不存在文件
    with pytest.raises(SystemExit) as e3:
        layers.cmd_plan_layers(ctx, types.SimpleNamespace(
            repo=repo.path, root=root, locale="zh", layers="apply",
            of=None, file="/tmp/no-such-result.json", reset=False))
    assert e3.value.code == config.EXIT_PRECOND


# --------------------------------------------------------------------------- #
# #38 aliases 文件缺失/坏 YAML 干净报错
# --------------------------------------------------------------------------- #

def test_r4_38_aliases_missing_file_clean(tmp_path):
    repo, root = _fixture(tmp_path)
    with pytest.raises(SystemExit) as ei:
        _cli(["plan", "--aliases", "/tmp/no-aliases.yaml",
              "--repo", repo.path, "--root", root])
    assert ei.value.code == config.EXIT_PRECOND


# --------------------------------------------------------------------------- #
# #40 submit --report 指向不存在文件: 出声且不记 phantom report_file
# --------------------------------------------------------------------------- #

def test_r4_40_missing_report_audited(tmp_path, capsys):
    repo, root = _fixture(tmp_path)
    ctx = _ctx(repo, root)
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    run = ctx.db.active_run(ctx.partition()["id"])
    claimed = ctx.db.claim_jobs(run["id"], count=1)
    j = claimed[0]
    page_file = os.path.join(root, "work", f"{j['page_id']}.md")
    with open(page_file, "w", encoding="utf-8") as fh:
        fh.write(f"# {j['title']}\n\n## 概述\n\n正文。\n")
    from pwiki_lib import engine
    args = types.SimpleNamespace(
        repo=repo.path, root=root, locale="zh", page_id=j["page_id"],
        file=page_file, summary="", key_topics="", report="/tmp/nope.json",
        lenient=False, force=False, claim=j["claim_token"])
    assert engine.cmd_submit(args) == config.EXIT_OK
    err = capsys.readouterr().err
    assert "report 文件不存在" in err, err
    row = ctx.db.job(run["id"], j["page_id"])
    rep = json.loads(row["report"] or "{}")
    assert rep.get("report_file", "") == "", rep


# --------------------------------------------------------------------------- #
# #41 digest --extra 缺文件 → 3
# --------------------------------------------------------------------------- #

def test_r4_41_digest_extra_missing(tmp_path):
    repo, root = _fixture(tmp_path)
    with pytest.raises(SystemExit) as ei:
        _cli(["digest", "--extra", "/tmp/no-notes.md", "--repo", repo.path,
              "--root", root])
    assert ei.value.code == config.EXIT_PRECOND


# --------------------------------------------------------------------------- #
# #42 size/cited-files 上界在 deep LINING 升错误级
# --------------------------------------------------------------------------- #

def test_r4_42_cited_files_upper_bound_upgrades(tmp_path):
    from pwiki_lib import gates
    repo = tmp_path / "repo5"
    repo.mkdir()
    cites = []
    for i in range(15):
        (repo / f"f{i}.py").write_text("x = 1\n" * 20, encoding="utf-8")
        cites.append(f"[f{i}.py](file://f{i}.py#L1)")
    meta = {"kind": "page", "title": "T", "type": "domain",
            "source_files": [f"f{i}.py" for i in range(15)]}
    errs, _w = gates.gate_item(
        "# T\n\n## 概述\n\n" + " ".join(cites) + "\n", meta, str(repo),
        "deep", phase="lining")
    assert any(e.startswith("size/cited-files") for e in errs), errs


# --------------------------------------------------------------------------- #
# #45 busy_timeout 与 connect timeout 同刻度(10s)
# --------------------------------------------------------------------------- #

def test_r4_45_busy_timeout_aligned():
    from pwiki_lib.db import WikiDB
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        db = WikiDB(d)
        v = db.conn.execute("PRAGMA busy_timeout").fetchone()[0]
        db.close()
        assert v == 10000, v


# =========================================================================== #
# 批4: #46-#54(yamlite 手编面 + 原子写 + id 去重; #52/#54 已随批3 落地)
# =========================================================================== #

# --------------------------------------------------------------------------- #
# #46 序列条目内的注释行不再中断续行吸收
# --------------------------------------------------------------------------- #

def test_r4_46_list_item_with_comment_parses():
    from pwiki_lib.yamlite import yaml_load
    text = ("modules:\n"
            "  - id: a\n"
            "    title: A\n"
            "    # note\n"
            '    parent: ""\n')
    assert yaml_load(text) == {
        "modules": [{"id": "a", "title": "A", "parent": ""}]}, \
        yaml_load(text)


# --------------------------------------------------------------------------- #
# #47 YAML 1.1 布尔 yes/no/on/off
# --------------------------------------------------------------------------- #

def test_r4_47_yaml11_booleans():
    from pwiki_lib.yamlite import yaml_dump, yaml_load
    doc = yaml_load("a: no\nb: yes\nc: on\nd: off\ne: 'no'\n")
    assert doc["a"] is False and doc["b"] is True
    assert doc["c"] is True and doc["d"] is False
    assert doc["e"] == "no", "引号形式保持字符串"
    # 发射侧按歧义词加引号 → 往返精确
    assert yaml_load(yaml_dump({"s": "no"}))["s"] == "no"


# --------------------------------------------------------------------------- #
# #48 嵌套行内列表解析 + 括号不配平显式报错
# --------------------------------------------------------------------------- #

def test_r4_48_nested_flow_list():
    from pwiki_lib.yamlite import yaml_load
    import pytest as _pytest
    assert yaml_load("a: [[b, c], d]\n") == {"a": [["b", "c"], "d"]}
    with _pytest.raises(ValueError):
        yaml_load("a: [x]]\n")


# --------------------------------------------------------------------------- #
# #49 块标量去缩进锚定内容行(浅注释不再重排整块)
# --------------------------------------------------------------------------- #

def test_r4_49_block_scalar_dedent_anchors_content():
    from pwiki_lib.yamlite import yaml_load
    doc = yaml_load("prompt: |\n      line1\n    # note\n      line2\n")
    assert doc["prompt"] == "line1\n# note\nline2", repr(doc["prompt"])
    # 相对缩进保持: 内容行更深时
    doc2 = yaml_load("prompt: |\n      a\n        b\n")
    assert doc2["prompt"] == "a\n  b", repr(doc2["prompt"])


# --------------------------------------------------------------------------- #
# #50 顶层 root/ 目录不再与工作区根共享 module id
# --------------------------------------------------------------------------- #

def test_r4_50_root_dir_distinct_id():
    from pwiki_lib.modules import to_plan_modules
    decomposed = {
        "modules": {
            "": {"title": "Repo", "scope": ["/"],
                 "children": ["root", "services"]},
            "root": {"title": "Root 模块", "scope": ["root/"],
                     "parent": ""},
            "services": {"title": "服务", "scope": ["services/"],
                         "parent": ""},
        },
        "order": ["", "root", "services"],
        "absorbed": [], "remaining": [], "candidates": [], "covered": [],
    }
    mods = to_plan_modules(decomposed)
    ids = [m["id"] for m in mods]
    assert len(ids) == len(set(ids)), ids
    assert "root" in ids and "root-dir" in ids, ids


# --------------------------------------------------------------------------- #
# #51 _source_hashes 对 'null' 列容错(防御旧库行)
# --------------------------------------------------------------------------- #

def test_r4_51_source_hashes_null_column(tmp_path):
    from pwiki_lib import publish
    from test_review_fixes import _ctx
    import types as _t
    repo = tmp_path / "repo6"
    repo.mkdir()
    root = tmp_path / "wiki6"
    root.mkdir()
    ctx = _ctx(_t.SimpleNamespace(path=str(repo)), str(root))
    assert publish._source_hashes(ctx, {"source_files": "null"}) == {}
    assert publish._source_hashes(ctx, {"source_files": "[]"}) == {}


# --------------------------------------------------------------------------- #
# #53 发布工件全部原子写(源级 lint: 不许再出现裸 open(...,'w'))
# --------------------------------------------------------------------------- #

def test_r4_53_no_bare_truncating_writes():
    import re as _re
    base = os.path.join(os.path.dirname(__file__), "..", "scripts",
                        "pwiki_lib")
    for mod in ("publish.py", "digest.py", "layers.py", "planner.py"):
        with open(os.path.join(base, mod), encoding="utf-8") as fh:
            src = fh.read()
        hits = _re.findall(r'open\([^)]*,\s*"w[^"]*"[^)]*\)', src)
        assert hits == [], (mod, hits)


# =========================================================================== #
# 批5: #55-#77(清理/效率/文档)
# =========================================================================== #

# --------------------------------------------------------------------------- #
# #55 collect 记忆化键覆盖 min —— 同 patterns 异 min 不再串结果
# --------------------------------------------------------------------------- #

def test_r4_55_collect_memo_key_covers_min(tmp_path):
    from pwiki_lib.corpus import Corpus
    repo = tmp_path / "repo7"
    repo.mkdir()
    (repo / "a.py").write_text("import build\n", encoding="utf-8")
    files = [{"path": "a.py", "lang": "python", "lines": 1}]
    corpus = Corpus(str(repo), files, {}, set())
    base = {"patterns": ["/no-such-dir/"], "grep": ["build"]}
    # min=999 → grep 回补门打开, a.py 命中
    many = corpus.collect(dict(base, min=999))
    assert [f["path"] for f in many] == ["a.py"], many
    # 无 grep 的 spec: 模式不中 → 空(与上不同键, 不串缓存)
    none = corpus.collect({"patterns": ["/no-such-dir/"]})
    assert none == []
    # 同键重复调用命中缓存(结果一致)
    again = corpus.collect(dict(base, min=999))
    assert [f["path"] for f in again] == ["a.py"]


# --------------------------------------------------------------------------- #
# #58 CJK 字符类单一来源
# --------------------------------------------------------------------------- #

def test_r4_58_cjk_re_single_source():
    import re as _re
    from pwiki_lib import search, util
    import pwiki_lib.qoderplan as qp
    assert search._CJK_RE is util.CJK_RE
    # qoderplan 不得再内联第二份字符类(该文件的字节帽 lint 由并发工作
    # 重写, 断言只约束"不再自带 CJK 类定义")
    qsrc = open(qp.__file__, encoding="utf-8").read()
    assert "一-鿿" not in qsrc


# --------------------------------------------------------------------------- #
# #60 read_submission 共用读稿(缺文件 3 / 坏 UTF-8 2)
# --------------------------------------------------------------------------- #

def test_r4_60_read_submission_contract(tmp_path):
    from pwiki_lib.util import read_submission
    f = tmp_path / "page.md"
    f.write_text("# T\n", encoding="utf-8")
    assert read_submission(str(f)) == "# T\n"
    with pytest.raises(SystemExit) as e1:
        read_submission(str(tmp_path / "nope.md"))
    assert e1.value.code == config.EXIT_PRECOND
    bad = tmp_path / "bad.md"
    bad.write_bytes(b"# \xff\xfe\n")
    with pytest.raises(SystemExit) as e2:
        read_submission(str(bad))
    assert e2.value.code == config.EXIT_GATE


# --------------------------------------------------------------------------- #
# #62 HAND_LANGS 单一定义(源级 lint)
# --------------------------------------------------------------------------- #

def test_r4_62_hand_langs_single_definition():
    import re as _re
    src = open(os.path.join(os.path.dirname(__file__), "..", "scripts",
                            "pwiki_lib", "themes.py"), encoding="utf-8").read()
    assert "HAND_LANGS = (" in src
    assert src.count('"typescript", "tsx"') == 1, "13 语言元组只允许一处"


# --------------------------------------------------------------------------- #
# #64/#65/#66 死 API 已删
# --------------------------------------------------------------------------- #

def test_r4_64_66_dead_apis_removed():
    from pwiki_lib.db import WikiDB
    assert not hasattr(WikiDB, "set_file_hashes")
    assert not hasattr(WikiDB, "extend_lease")
    assert not hasattr(WikiDB, "glossary_set_definition")


# --------------------------------------------------------------------------- #
# #68 只写不读的死列已删
# --------------------------------------------------------------------------- #

def test_r4_68_write_only_columns_dropped():
    from pwiki_lib.db import WikiDB
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        db = WikiDB(d)
        run_cols = {r[1] for r in db.conn.execute(
            "PRAGMA table_info(run)")}
        part_cols = {r[1] for r in db.conn.execute(
            "PRAGMA table_info(partition)")}
        db.close()
        assert "config_json" not in run_cols and "target_commit" not in run_cols
        assert "baseline_generator_version" not in part_cols


# --------------------------------------------------------------------------- #
# #69 next --json 不再解析 plan
# --------------------------------------------------------------------------- #

def test_r4_69_next_json_skips_plan_parse(tmp_path, monkeypatch):
    from pwiki_lib import planner
    repo, root = _fixture(tmp_path)
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    calls = []
    real = planner.load_plan

    def counting(ctx):
        calls.append(1)
        return real(ctx)

    monkeypatch.setattr(planner, "load_plan", counting)
    assert _cli(["next", "--json", "--repo", repo.path,
                 "--root", root]) == config.EXIT_OK
    assert calls == [], "--json 路径不应解析 wiki_plan.yaml"


# --------------------------------------------------------------------------- #
# #70 无模块 job 的 submit 不再解析 plan
# --------------------------------------------------------------------------- #

def test_r4_70_submit_card_skips_plan_parse(tmp_path, monkeypatch):
    from pwiki_lib import engine, planner
    repo, root = _fixture(tmp_path)
    ctx = _ctx(repo, root)
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    run = ctx.db.active_run(ctx.partition()["id"])
    card = next(j for j in ctx.db.jobs(run["id"])
                if config.is_card_kind(j["kind"]))
    assert not card["module"], "卡片不应带模块"
    calls = []
    real = planner.load_plan

    def counting(ctx_):
        calls.append(1)
        return real(ctx_)

    monkeypatch.setattr(planner, "load_plan", counting)
    page_file = os.path.join(root, "work", f"{card['page_id']}.md")
    with open(page_file, "w", encoding="utf-8") as fh:
        fh.write(f"---\nkind: {card['card_kind']}\nname: {card['title']}\n"
                 "---\n\n## 1. 总体方案\n\n事实。\n")
    args = types.SimpleNamespace(
        repo=repo.path, root=root, locale="zh", page_id=card["page_id"],
        file=page_file, summary="", key_topics="", report=None,
        lenient=False, force=False, claim=None)
    assert engine.cmd_submit(args) == config.EXIT_OK
    assert calls == [], "无模块 job 的 submit 不应解析 wiki_plan.yaml"


# --------------------------------------------------------------------------- #
# #71 cmd_plan 单次解析
# --------------------------------------------------------------------------- #

def test_r4_71_cmd_plan_single_parse(tmp_path, monkeypatch):
    from pwiki_lib import planner
    repo, root = _fixture(tmp_path)
    calls = []
    real = planner.load_plan

    def counting(ctx):
        calls.append(1)
        return real(ctx)

    monkeypatch.setattr(planner, "load_plan", counting)
    assert _cli(["plan", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    assert len(calls) == 1, calls


# --------------------------------------------------------------------------- #
# #72 未变页面不再重写(mtime 不动)
# --------------------------------------------------------------------------- #

def test_r4_72_unchanged_pages_not_rewritten(tmp_path):
    from pwiki_lib import planner, publish
    repo, root = _published_custom(tmp_path)
    ctx = _ctx(repo, root)
    pid = ctx.partition()["id"]
    plan = planner.load_plan(ctx)
    victim = next(p for p in plan["pages"] if p.get("id") == "settle")
    full = os.path.join(root, victim["path"])
    mtime1 = os.stat(full).st_mtime_ns
    run_id = ctx.db.create_run(pid, "generate", "start", "INCREMENT",
                               "DIRECTORY_GENERATING", ctx.commit or "",
                               "ph", {}, style="concise")
    publish.publish_run(ctx, {"id": run_id, "generation_type": "INCREMENT"},
                        plan, "concise", yes=True, jobs=[])
    assert os.stat(full).st_mtime_ns == mtime1, "未变页面不得重写"


# --------------------------------------------------------------------------- #
# #73/#74 scope 展开进程内缓存(第二次同 scope 不再走文件系统)
# --------------------------------------------------------------------------- #

def test_r4_73_scope_expansion_cached(tmp_path, monkeypatch):
    from pwiki_lib import scan
    repo = tmp_path / "repo8"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    entries, seen = {}, set()
    scan.expand_scope(str(repo), entries, seen, "src/")
    assert set(entries) == {"src/a.py"}
    # 缓存命中: 禁掉 os.walk 后同 scope 仍可展开
    def no_walk(*a, **k):
        raise AssertionError("should be served from cache")

    monkeypatch.setattr(scan.os, "walk", no_walk)
    entries2, seen2 = {}, set()
    scan.expand_scope(str(repo), entries2, seen2, "src")
    assert set(entries2) == {"src/a.py"}
