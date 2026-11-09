"""补覆盖(2026-11-08 深度审查缺口): finish/LINING 硬门禁失败路径、
layers 分层规划路由(此前零测试)、五条保留教训的不变量回归
(bug5/bug10/bug15/bug17/D19)。

约定: 代码冻结, 只加测试; 复用 test_e2e 的 fixture 驱动器与
test_review_fixes 的回归测试习惯(_cli/_ctx/_published_full/_write_job_file)。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from gitrepo import GitRepo

from pwiki_lib import config
from test_e2e import _fixture, _write_all_jobs
from conftest import _cli, _ctx
from test_review_fixes import _published_full, _write_job_file


# --------------------------------------------------------------------------- #
# c055: finish/LINING 硬门禁失败路径
# --------------------------------------------------------------------------- #

def test_finish_lining_hard_gate_rejects_corrupt_job(tmp_path):
    """LINING 硬错(引用路径不存在)→ exit 2, 坏 job 打回 pending,
    run 落 DOCUMENT_FAILED(可恢复); --lenient 强过 → exit 0。

    坏稿直接经 set_job_state 落库 —— submit 阶段对 citation/path 同样
    是硬错, 无法从正常入口造出"已 completed 的坏稿"。"""
    from pwiki_lib import cli
    repo, root = _fixture(tmp_path, style="concise")
    assert cli.main(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    _write_all_jobs(repo, root, style="concise")
    ctx = _ctx(repo, root)
    pid = ctx.partition()["id"]
    run = ctx.db.active_run(pid)
    victim = next(j for j in ctx.db.jobs(run["id"]) if j["kind"] == "page")
    bad = (f"# {victim['title']}\n\n## 概述\n\n正文。\n\n"
           "[x](file://no/such/file.py#L1-L2)\n")
    assert ctx.db.set_job_state(run["id"], victim["page_id"], "completed",
                                content=bad)
    with pytest.raises(SystemExit) as ei:
        cli.main(["finish", "--repo", repo.path, "--root", root, "--yes"])
    assert ei.value.code == config.EXIT_GATE
    job = ctx.db.job(run["id"], victim["page_id"])
    assert job["state"] == "pending", "LINING 拒绝后应打回 pending 保 checkpoint"
    assert "citation/path" in (job["last_error"] or "")
    assert ctx.db.active_run(pid)["state"] == "DOCUMENT_FAILED"
    # r4 #43: --lenient 不再放行 citation/path(引用真实性永不放宽) ——
    # 重新置 completed 后 lenient finish 仍拒绝; --force 才全放
    ctx.db.set_job_state(run["id"], victim["page_id"], "completed",
                         content=bad)
    with pytest.raises(SystemExit) as ei2:
        cli.main(["finish", "--lenient", "--repo", repo.path,
                  "--root", root, "--yes"])
    assert ei2.value.code == config.EXIT_GATE
    ctx.db.set_job_state(run["id"], victim["page_id"], "completed",
                         content=bad)
    assert cli.main(["finish", "--force", "--repo", repo.path,
                     "--root", root, "--yes"]) == config.EXIT_OK


# --------------------------------------------------------------------------- #
# c056: plan --layers init|apply|commit|status(此前零测试)
# --------------------------------------------------------------------------- #

_LAYERS_TOP = {"articles": [
    {"slug": "overview", "title": "项目概览", "type": "overview",
     "objective": "定位与模块地图。Out of scope: 各域内部细节。",
     "modules": ["/"], "need_further_planning": False},
    {"slug": "services-domain", "title": "后端服务领域", "type": "domain",
     "objective": "服务域聚合。Out of scope: 共享包。",
     "modules": ["services"], "need_further_planning": True},
    {"slug": "packages-domain", "title": "共享包领域", "type": "domain",
     "objective": "共享域。Out of scope: 服务实现。",
     "modules": ["packages"], "need_further_planning": False},
    {"slug": "dev-guide", "title": "开发指南", "type": "developer_guide",
     "objective": "构建与约定。Out of scope: 业务行为。",
     "modules": ["/"], "need_further_planning": False},
]}

_LAYERS_SUB = {"articles": [
    {"slug": "user-service-dive", "title": "用户服务深入", "type": "deep_dive",
     "objective": "用户服务实现。Out of scope: 订单服务。",
     "modules": ["services"], "need_further_planning": False},
    {"slug": "order-service-dive", "title": "订单服务深入", "type": "deep_dive",
     "objective": "订单服务实现。Out of scope: 用户服务。",
     "modules": ["services"], "need_further_planning": False},
]}


def _session_path(root):
    return os.path.join(root, "work", "plan-layers.json")


def _result_file(tmp_path, name, payload, fenced=False):
    """手造 parse_result_block 期望的应答: 单个 <result>{…}</result> 块
    (或其代码栅栏 JSON 兜底形态)。"""
    body = json.dumps(payload, ensure_ascii=False)
    text = f"```json\n{body}\n```" if fenced else f"<result>{body}</result>"
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


def _layers_apply(repo, root, file, of=None):
    argv = ["plan", "--layers", "apply", "--file", file,
            "--repo", repo.path, "--root", root]
    if of:
        argv += ["--of", of]
    assert _cli(argv) == config.EXIT_OK


def _load_session(root):
    with open(_session_path(root), encoding="utf-8") as fh:
        return json.load(fh)


def test_layers_init_writes_session_and_first_question(tmp_path, capsys):
    """init: 建 work/plan-layers.json 会话 + 输出第一层(顶层)问询载荷。"""
    repo, root = _fixture(tmp_path, style="concise")
    assert _cli(["plan", "--layers", "init", "--repo", repo.path,
                 "--root", root]) == config.EXIT_OK
    assert os.path.isfile(_session_path(root))
    state = _load_session(root)
    assert state["articles"] == []
    out = capsys.readouterr().out
    assert "# Module Tree (3 modules)" in out      # 第一问带全量模块树
    assert "编目规则" in out                         # 规则随载荷下发
    assert "current_depth: 0 (top-level planning)" in out


def test_layers_apply_advances_layer_and_recurses(tmp_path, capsys):
    """apply: <result> 应答入账; need_further_planning 的子篇触发递归
    (立即给出过滤到父域的下一层载荷); --of 落子层, 叶子型 need 恒 False。"""
    repo, root = _fixture(tmp_path, style="concise")
    assert _cli(["plan", "--layers", "init", "--repo", repo.path,
                 "--root", root]) == config.EXIT_OK
    capsys.readouterr()
    _layers_apply(repo, root, _result_file(tmp_path, "layer1.json",
                                            _LAYERS_TOP))
    out = capsys.readouterr().out
    assert "added 4 article(s)" in out
    state = _load_session(root)
    assert [a["slug"] for a in state["articles"]] == \
        ["overview", "services-domain", "packages-domain", "dev-guide"]
    assert next(a for a in state["articles"]
                if a["slug"] == "services-domain")["need_further_planning"]
    # 递归: 对标 true 的子篇马上问下一层(模块树过滤到父域)
    assert "next layer payload" in out
    assert "filtered to parent's domain" in out and "后端服务领域" in out
    # 子层应答(--of 指定父 slug); 栅栏 JSON 是 parse_result_block 的兜底形态
    _layers_apply(repo, root, _result_file(tmp_path, "layer2.json",
                                            _LAYERS_SUB, fenced=True),
                  of="services-domain")
    out = capsys.readouterr().out
    assert "added 2 article(s)" in out
    state = _load_session(root)
    kids = [a for a in state["articles"] if a["parent"] == "services-domain"]
    assert {a["slug"] for a in kids} == \
        {"user-service-dive", "order-service-dive"}
    assert all(a["depth"] == 1 and not a["need_further_planning"]
               for a in kids), "deep_dive 是叶子型, need 必须被压成 False"
    # status: 会话台账可查
    assert _cli(["plan", "--layers", "status", "--repo", repo.path,
                 "--root", root]) == config.EXIT_OK
    assert "articles: 6" in capsys.readouterr().out


def test_layers_commit_merges_tree_into_plan(tmp_path, capsys):
    """commit: 决定的目录树物化进 wiki_plan.yaml —— 页落位、sheet 保留、
    计划仍过 validate(begin 同口径)。"""
    from pwiki_lib import planner
    repo, root = _fixture(tmp_path, style="concise")
    assert _cli(["plan", "--layers", "init", "--repo", repo.path,
                 "--root", root]) == config.EXIT_OK
    _layers_apply(repo, root, _result_file(tmp_path, "l1.json", _LAYERS_TOP))
    _layers_apply(repo, root, _result_file(tmp_path, "l2.json", _LAYERS_SUB),
                  of="services-domain")
    capsys.readouterr()
    assert _cli(["plan", "--layers", "commit", "--repo", repo.path,
                 "--root", root]) == config.EXIT_OK
    # r4 #5: commit 不再整树替换 —— seed 阶段的内容页(手编面)保留,
    # 只有 `pwiki plan --reset` 才整体重建
    out = capsys.readouterr().out
    assert "plan rewritten: 6 article pages + 3 kept + 3 module sheets" in out, out
    ctx = _ctx(repo, root)
    plan = planner.load_plan(ctx)
    by_id = {p["id"]: p for p in planner.plan_pages(plan)}
    assert "overview" in by_id and by_id["overview"]["type"] == "overview"
    assert by_id["user-service-dive"]["parent"] == "services-domain"
    assert by_id["services-domain"]["source_files"] == ["services/"]
    # sheet 页保留(commit 只换目录树, 与 --themes 合并同口径)
    assert {p["id"] for p in planner.plan_pages(plan)
            if p["kind"] == "module_sheet"} == \
        {"root-overview", "packages-overview", "services-overview"}
    errors, _ = planner.validate_plan(
        ctx, plan, profile=ctx.config.get("profile", "standard"))
    assert errors == [], errors


# --------------------------------------------------------------------------- #
# c028: 五条保留教训的不变量
# --------------------------------------------------------------------------- #

def _republish_one(repo, root):
    """最小发布路径: begin → regenerate 单页 → submit → finish(刷新发布树)。"""
    from pwiki_lib import engine
    ctx = _ctx(repo, root)
    pid = ctx.partition()["id"]
    page_id = next(p["page_id"] for p in ctx.db.published_pages(pid)
                   if p["kind"] == "page")
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
    assert _cli(["finish", "--repo", repo.path, "--root", root, "--yes"]) == \
        config.EXIT_OK


def test_bug10_injection_never_written_back_to_db(tmp_path):
    """bug10: <cite>/目录注入只活在导出层(compose_export_content),
    DB 的 page.content 永存 writer 原稿 —— 否则下一轮 finish 自我拒绝。"""
    repo, root = _published_full(tmp_path)
    ctx = _ctx(repo, root)
    pid = ctx.partition()["id"]
    pages = [p for p in ctx.db.published_pages(pid) if p["kind"] == "page"]
    assert pages
    for p in pages:
        with open(os.path.join(root, p["path"]), encoding="utf-8") as fh:
            exported = fh.read()
        assert "<cite>" in exported and "## 目录" in exported, p["path"]
        db_content = ctx.db.published_page(pid, p["page_id"])["content"]
        assert "<cite>" not in db_content, \
            f"{p['page_id']}: 注入物写回了 DB(bug10 自毒)"
        assert "## 目录" not in db_content, p["page_id"]


def test_bug15_orphan_module_yaml_pruned_on_republish(tmp_path):
    """bug15: 模块不再在注册表 → 其 knowledge/_module.yaml 在下一次发布
    被剪掉; 注册表内的 _module.yaml 一个不少。"""
    repo, root = _published_full(tmp_path)
    knowledge = os.path.join(root, "knowledge", "zh")

    def module_yaml_dirs():
        return {os.path.basename(os.path.dirname(os.path.join(dp, f)))
                for dp, _dn, fns in os.walk(knowledge)
                for f in fns if f == "_module.yaml"}

    orphan = os.path.join(knowledge, "幽灵模块")
    os.makedirs(orphan, exist_ok=True)
    with open(os.path.join(orphan, "_module.yaml"), "w",
              encoding="utf-8") as fh:
        fh.write("title: 幽灵模块\n")
    before = module_yaml_dirs()
    assert "幽灵模块" in before
    _republish_one(repo, root)
    after = module_yaml_dirs()
    assert "幽灵模块" not in after, "孤儿 _module.yaml 没被剪(bug15)"
    assert after == before - {"幽灵模块"}, \
        f"注册表内的 _module.yaml 被误剪: {after}"


def test_d19_guard_blocks_deep_missing_root_without_yes(tmp_path):
    """D19: 发布根出现骨架之外的多级缺失(locale 目录被手删/根漂移),
    不加 --yes 即拒(exit 3, 且先于任何建树); run 留在 LINING 可恢复,
    --yes 放行。r5 P1 后 init/begin 落发布骨架 —— 全新 workspace 的首次
    finish 不再触发 D19, 守卫只剩漂移检测职责。"""
    repo, root = _fixture(tmp_path, style="concise")
    nested_parent = tmp_path / "deep" / "nest"     # 两级目录都不存在
    nested_parent.mkdir(parents=True)
    nested_root = str(nested_parent / "wiki")
    shutil.move(root, nested_root)
    root = nested_root
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == config.EXIT_OK
    _write_all_jobs(repo, root, style="concise")
    # 模拟漂移: begin 落好的 locale 骨架被手删(content+zh 两级缺失)
    shutil.rmtree(os.path.join(root, "zh"))
    with pytest.raises(SystemExit) as ei:
        _cli(["finish", "--repo", repo.path, "--root", root])
    assert ei.value.code == config.EXIT_PRECOND
    assert not os.path.isdir(os.path.join(root, "zh")), \
        "守卫必须先于任何建树动作"
    ctx = _ctx(repo, root)
    assert ctx.db.active_run(ctx.partition()["id"])["state"] == "LINING"
    assert _cli(["finish", "--repo", repo.path, "--root", root,
                 "--yes"]) == config.EXIT_OK
    assert os.path.isdir(os.path.join(root, "zh", "content"))


def test_bug17_scan_keeps_gitkeep_marker_visible(tmp_path):
    """bug17: .gitkeep 以 lang=marker 留在扫描清单里 —— 占位目录检测
    (模块分解的骨架判定)需要看到标记文件, 目录树视图也才可见。"""
    repo = GitRepo(tmp_path / "repo")
    repo.write("src/a.py", "x = 1\n")
    repo.write("docs/legacy-notes/.gitkeep", "")
    repo.write("placeholder-only/.gitkeep", "")
    repo.commit("init")
    root = str(tmp_path / "root")
    assert _cli(["scan", "--repo", repo.path, "--root", root]) == config.EXIT_OK
    with open(os.path.join(root, "work", "inventory.json"),
              encoding="utf-8") as fh:
        inv = json.load(fh)
    markers = {f["path"]: f["lang"] for f in inv["files"]
               if f["path"].endswith(".gitkeep")}
    assert markers == {"docs/legacy-notes/.gitkeep": "marker",
                       "placeholder-only/.gitkeep": "marker"}, markers
    assert "placeholder-only" in inv["tree_text"] \
        and "legacy-notes" in inv["tree_text"]


def test_bug5_untrack_is_not_delete(tmp_path, capsys):
    """bug5: 条目离开计划 → 其源文件退出跟踪集, 但 removed 只数盘上
    真没了的文件; 源文件在盘 → 0 removed, DB 行与发布文件都还在, 不崩。"""
    from pwiki_lib import planner
    repo, root = _published_full(tmp_path)
    ctx = _ctx(repo, root)
    pid = ctx.partition()["id"]
    plan = planner.load_plan(ctx)
    target = next(p for p in planner.plan_pages(plan)
                  if p["kind"] == "page" and p["id"] == "packages")
    src = str(target["source_files"][0])           # 'packages/'
    plan["pages"] = [p for p in planner.plan_pages(plan)
                     if p["id"] != target["id"]]
    planner.save_plan(ctx, plan)
    assert _cli(["update", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    out = capsys.readouterr().out
    # 源目录还在盘上 → 不得按 removed 计
    assert "/ 0 removed tracked" in out, out
    assert os.path.isdir(os.path.join(repo.path, src)), "源文件被误删?"
    # 页本身离开计划被如实记账(剪除推迟到 finish), 与文件删除是两回事
    assert "removed_items: 1" in out and "离开计划" in out, out
    assert ctx.db.published_page(pid, "packages") is not None
    assert os.path.isfile(os.path.join(root, "zh", "content", "共享包集合.md"))
    # 收尾: 不把挂着的 0-job INCREMENT run 留给后续命令
    assert _cli(["cancel", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK


def test_layers_expand_clears_parent_need_flag_to_converge(tmp_path, capsys):
    """收敛回归(--of 展开后父节点 need 必须清除):

    原版 apply_layer_result 从不清除被展开父节点的 need_further_planning
    —— pending 永远含父节点, 下一层 payload 反复重指同一父,
    "planning complete → commit" 提示不可达, 引擎对同一父无限重问。"""
    repo, root = _fixture(tmp_path, style="concise")
    assert _cli(["plan", "--layers", "init", "--repo", repo.path,
                 "--root", root]) == config.EXIT_OK
    capsys.readouterr()
    _layers_apply(repo, root, _result_file(tmp_path, "l1.json", _LAYERS_TOP))
    capsys.readouterr()
    # 展开 services-domain(其 need=true 触发的正是这次 --of 应答)
    _layers_apply(repo, root,
                  _result_file(tmp_path, "l2.json", _LAYERS_SUB, fenced=True),
                  of="services-domain")
    out = capsys.readouterr().out
    state = _load_session(root)
    parent = next(a for a in state["articles"]
                  if a["slug"] == "services-domain")
    assert not parent["need_further_planning"], \
        "父节点被展开后 need 旗标必须清除"
    pending = [a["slug"] for a in state["articles"]
               if a.get("need_further_planning")]
    assert not pending, pending
    assert "planning complete" in out, \
        "全部展开落地后必须给出 commit 提示(原版不可达)"
