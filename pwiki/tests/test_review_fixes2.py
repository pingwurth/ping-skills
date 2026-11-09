"""2026-11-08 第二轮 max 审查回修的回归测试(B1 生命周期/数据丢失族)。

对应报告: pwiki/docs/code-review/2026-11-08-code-review-max-round2.md(报告文件已清理)
  #1  D22 改名只改 DB 不落盘(旧位置取自 plan 陈旧 path 键)
  #14 改名落盘绕过导出合成层(丢 <cite>/目录)
  #2  快照卡片 scope 是 JSON 字符串却被逐字符遍历
  #3  regenerate 静默取消整个 run(搁置已完成未发布稿)
  #4  reconcile 拒绝项也标 applied(rejected/retry 从未写入)
  #23 reconcile --apply 非数字裸 traceback
  #10 submit 漏 --claim 顶掉在写 writer(重认领后必须亮令牌)
  #16 next 在全部 in-flight 时谎报 complete
  #22 begin --missing 跳过集只看 last_run(INCREMENT 后跳过页复活)
  #25 fail/skip 对 completed 无守卫
"""

from __future__ import annotations

import json
import os
import re
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from pwiki_lib import config

from test_e2e import _fixture, _write_all_jobs
from conftest import _cli, _ctx
from test_review_fixes import _published_full, _write_job_file


def _ns(repo, root, **kw):
    base = {"repo": repo.path, "root": root, "locale": "zh"}
    base.update(kw)
    return types.SimpleNamespace(**base)


def _expire_lease(ctx, run_id, page_id):
    ctx.db.conn.execute(
        "UPDATE page_job SET lease_expires_at='2000-01-01T00:00:00.000Z' "
        "WHERE run_id=? AND page_id=?", (run_id, page_id))


def _submit(ctx, repo, root, job, claim=None):
    from pwiki_lib import engine
    args = _ns(repo, root, page_id=job["page_id"],
               file=_write_job_file(root, job), summary="", key_topics="",
               report=None, lenient=False, force=False,
               claim=claim if claim is not None else job["claim_token"])
    assert engine.cmd_submit(args) == config.EXIT_OK
    return job["page_id"]


# --------------------------------------------------------------------------- #
# #1/#14 D22 改名: 文件真的搬走, 落盘走导出合成层, 索引指向新路径
# --------------------------------------------------------------------------- #

def test_d22_rename_moves_published_file(tmp_path):
    repo, root = _published_full(tmp_path)          # concise(legacy 注入)
    ctx = _ctx(repo, root)
    pid = ctx.partition()["id"]
    page = next(p for p in ctx.db.published_pages(pid) if p["kind"] == "page")
    old_title = page["title"]
    new_title = old_title + "修订"
    ctx.db.reconcile_add(pid, "title_mismatch", {
        "page_id": page["page_id"], "old": old_title,
        "proposed": new_title, "evidence": "回归测试"})
    assert _cli(["reconcile", "--all", "--repo", repo.path,
                 "--root", root]) == config.EXIT_OK

    row = ctx.db.published_page(pid, page["page_id"])
    assert row["title"] == new_title
    assert row["path"] != page["path"]
    # 文件真搬家: 旧位置清空(DB 指向的)新位置落盘 —— 曾只改 DB,
    # 旧文件留在原地随后被孤儿清扫, 新路径是幻影
    assert not os.path.isfile(os.path.join(root, page["path"])), \
        "旧路径文件未搬走"
    new_full = os.path.join(root, row["path"])
    assert os.path.isfile(new_full), "新路径没有落盘"
    body = open(new_full, encoding="utf-8").read()
    assert body.startswith(f"# {new_title}")
    # 与 finish 同口径: legacy 导出注入 <cite> —— 曾直写 DB 原文丢注入层
    assert "<cite>" in body, "改名落盘绕过了导出合成层"
    # 索引指向新路径, 不再索引旧路径
    idx = open(os.path.join(root, "zh", "content", "_index.yaml"),
               encoding="utf-8").read()
    assert row["path"].replace(os.sep, "/") in idx
    meta = json.load(open(os.path.join(root, "zh", "meta",
                                       "repowiki-metadata.json"),
                          encoding="utf-8"))
    assert row["path"] in {i["path"] for i in meta["items"]}


# --------------------------------------------------------------------------- #
# #4/#23 reconcile 结局: rejected / retry 如实入账; --apply 非数字干净报错
# --------------------------------------------------------------------------- #

def _reconcile_state(ctx, rid):
    return ctx.db.conn.execute(
        "SELECT state FROM reconcile WHERE id=?", (rid,)).fetchone()["state"]


def test_reconcile_rejected_and_retry_states(tmp_path):
    from pwiki_lib import planner
    repo, root = _fixture(tmp_path)
    ctx = _ctx(repo, root)
    pid = ctx.partition()["id"]
    # 结构性拒绝: 计划里没有的页/卡 → rejected, 不复活
    rid1 = ctx.db.reconcile_add(pid, "title_mismatch",
                                {"page_id": "ghost", "old": "a",
                                 "proposed": "b"})
    rid2 = ctx.db.reconcile_add(pid, "card_conflict", {"card": "不存在的卡"})
    assert _cli(["reconcile", "--all", "--repo", repo.path,
                 "--root", root]) == config.EXIT_OK
    assert _reconcile_state(ctx, rid1) == "rejected"
    assert _reconcile_state(ctx, rid2) == "rejected"

    # 瞬态拒绝: 存在活动 run 时的卡片冲突 → 保留 pending 等下次
    plan = planner.load_plan(ctx)
    card = planner.plan_cards(plan)[0]
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    rid3 = ctx.db.reconcile_add(pid, "card_conflict", {"card": card["id"]})
    assert _cli(["reconcile", "--all", "--repo", repo.path,
                 "--root", root]) == config.EXIT_OK
    assert _reconcile_state(ctx, rid3) == "pending", \
        "有活动 run 的冲突被静默消化(曾一律 applied)"

    # run 收尾后再和解 → applied(排进 regenerate run)
    assert _cli(["cancel", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    assert _cli(["reconcile", "--apply", str(rid3), "--repo", repo.path,
                 "--root", root]) == config.EXIT_OK
    assert _reconcile_state(ctx, rid3) == "applied"


def test_reconcile_apply_nonnumeric_is_clean_error(tmp_path):
    repo, root = _fixture(tmp_path)
    ctx = _ctx(repo, root)
    ctx.db.reconcile_add(ctx.partition()["id"], "title_mismatch",
                         {"page_id": "x", "old": "a", "proposed": "b"})
    with pytest.raises(SystemExit) as ei:
        _cli(["reconcile", "--apply", "xyz", "--repo", repo.path,
              "--root", root])
    # r4 #36: argparse 用法错从 2(撞 EXIT_GATE)归 3(前置不满足);
    # 本断言的初衷 —— 不是裸 ValueError traceback —— 保持成立
    assert ei.value.code in (config.EXIT_PRECOND,)


# --------------------------------------------------------------------------- #
# #10 重认领后 submit 漏 --claim: 顶号被拒, 稿件走孤儿稿
# --------------------------------------------------------------------------- #

def test_submit_after_reclaim_requires_claim(tmp_path):
    from pwiki_lib import engine
    repo, root = _fixture(tmp_path)
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    ctx = _ctx(repo, root)
    pid = ctx.partition()["id"]
    run = ctx.db.active_run(pid)
    a = ctx.db.claim_jobs(run["id"], count=1)[0]      # writer A 认领
    # A 租约过期, 兄弟 writer B 重认领(attempts=2, 新令牌)
    _expire_lease(ctx, run["id"], a["page_id"])
    b = ctx.db.claim_jobs(run["id"], count=1)[0]
    assert b["page_id"] == a["page_id"] and b["claim_token"] != a["claim_token"]

    # A 无 --claim 重交(过期稿) —— 曾直接采纳 B 的行内令牌让老稿获胜
    stale_args = _ns(repo, root, page_id=a["page_id"],
                     file=_write_job_file(root, a), summary="", key_topics="",
                     report=None, lenient=False, force=False, claim=None)
    with pytest.raises(SystemExit) as ei:
        engine.cmd_submit(stale_args)
    assert ei.value.code == config.EXIT_PRECOND
    orphan_dir = os.path.join(root, "work", config.ORPHAN_SUBMIT_DIR)
    assert os.path.isdir(orphan_dir) and os.listdir(orphan_dir), \
        "被拒的过期稿必须存孤儿稿"

    # B 持令牌正常交稿
    _submit(ctx, repo, root, b, claim=b["claim_token"])
    assert ctx.db.job(run["id"], b["page_id"])["state"] == "completed"

    # A 再来一次(无令牌, job 已 completed)—— completed 守卫也要保稿
    with pytest.raises(SystemExit) as ei2:
        engine.cmd_submit(stale_args)
    assert ei2.value.code == config.EXIT_PRECOND
    assert len(os.listdir(orphan_dir)) == 2, \
        "completed 守卫拒绝时稿件不落孤儿目录(曾静默丢)"


# --------------------------------------------------------------------------- #
# #16 next: pending=0 但有租约在写 → in_flight, 不再谎报 complete
# --------------------------------------------------------------------------- #

def test_next_in_flight_vs_complete(tmp_path, capsys):
    repo, root = _fixture(tmp_path)
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    ctx = _ctx(repo, root)
    pid = ctx.partition()["id"]
    run = ctx.db.active_run(pid)
    ctx.db.claim_jobs(run["id"], count=100)    # 全部 processing, pending=0
    capsys.readouterr()
    assert _cli(["next", "--json", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    import json as _json
    cap = capsys.readouterr()
    # --json 契约(第三轮 P1 #24): stdout 只准是 JSON, 人类状态行走
    # stderr —— 编排方 json.loads(stdout) 曾恰好在 in_flight/complete
    # 切换点(轮询要检测的时刻)炸掉
    payload = _json.loads(cap.out)
    assert payload["status"] == "in_flight", cap.out
    assert "在写" in cap.err and "在写" not in cap.out

    for j in ctx.db.jobs(run["id"]):
        ctx.db.set_job_state(run["id"], j["page_id"], "failed", error="x")
    capsys.readouterr()
    assert _cli(["next", "--json", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    cap2 = capsys.readouterr()
    assert _json.loads(cap2.out)["status"] == "complete", cap2.out


# --------------------------------------------------------------------------- #
# #25 fail/skip: completed 不许无 --force 撤销; 重认领后同样要 --claim
# --------------------------------------------------------------------------- #

def test_fail_skip_guards(tmp_path):
    from pwiki_lib import engine
    repo, root = _fixture(tmp_path)
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    ctx = _ctx(repo, root)
    pid = ctx.partition()["id"]
    run = ctx.db.active_run(pid)
    done = ctx.db.claim_jobs(run["id"], count=1)[0]
    _submit(ctx, repo, root, done)

    # completed 无 --force → 拒绝, 状态不动
    with pytest.raises(SystemExit) as ei:
        engine.cmd_fail(_ns(repo, root, page_id=done["page_id"],
                            error="手滑"))
    assert ei.value.code == config.EXIT_PRECOND
    assert ctx.db.job(run["id"], done["page_id"])["state"] == "completed"
    # --force 明确撤销
    assert engine.cmd_fail(_ns(repo, root, page_id=done["page_id"],
                               error="确认重做", force=True)) == config.EXIT_OK
    assert ctx.db.job(run["id"], done["page_id"])["state"] == "failed"

    # 重认领后 skip 无 --claim → 拒绝(令牌可能属于其他 writer)
    nxt = ctx.db.claim_jobs(run["id"], count=1)[0]
    _expire_lease(ctx, run["id"], nxt["page_id"])
    reclaimed = ctx.db.claim_jobs(run["id"], count=1)[0]
    with pytest.raises(SystemExit) as ei2:
        engine.cmd_skip(_ns(repo, root, page_id=reclaimed["page_id"],
                            reason="跳过"))
    assert ei2.value.code == config.EXIT_PRECOND


# --------------------------------------------------------------------------- #
# #3 regenerate: 搁置已完成未发布稿要挡; 取消时如实播报
# --------------------------------------------------------------------------- #

def test_regenerate_strands_completed_needs_force(tmp_path, capsys):
    repo, root = _published_full(tmp_path)
    ctx = _ctx(repo, root)
    pid = ctx.partition()["id"]
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    run = ctx.db.active_run(pid)
    done = ctx.db.claim_jobs(run["id"], count=1)[0]
    _submit(ctx, repo, root, done)
    target = next(j["page_id"] for j in ctx.db.jobs(run["id"])
                  if j["kind"] == "page" and j["page_id"] != done["page_id"])

    # 活动 run 里有 1 份已完成未发布的稿 → 静默取消 = 搁置它, 拒绝
    with pytest.raises(SystemExit) as ei:
        _cli(["regenerate", target, "--repo", repo.path, "--root", root])
    assert ei.value.code == config.EXIT_PRECOND

    # --force 放行, 且取消被播报+入账(曾只打印新 run 一行)
    capsys.readouterr()
    assert _cli(["regenerate", target, "--force", "--repo", repo.path,
                 "--root", root]) == config.EXIT_OK
    captured = capsys.readouterr()
    assert "已取消 run" in captured.out + captured.err, \
        captured.out + captured.err
    events = {r[0] for r in ctx.db.conn.execute(
        "SELECT event FROM run_log WHERE run_id=?", (run["id"],)).fetchall()}
    assert "run_cancelled_by_regenerate" in events
    assert ctx.db.job(run["id"], done["page_id"])["state"] == "completed", \
        "被搁置的完成稿必须仍在旧 run 行内(可追溯)"


# --------------------------------------------------------------------------- #
# #22 begin --missing: 跳过集看全部 run, INCREMENT 之后跳过页不复活
# --------------------------------------------------------------------------- #

def _drive_all_but_first_page(repo, root, ctx):
    """交完全部 job, 但跳过第一个认领到的 page(留下永久的 skip 决定)。"""
    from pwiki_lib import engine
    run = ctx.db.active_run(ctx.partition()["id"])
    skipped = None
    while True:
        claimed = ctx.db.claim_jobs(run["id"], count=1)
        if not claimed:
            break
        j = claimed[0]
        if skipped is None and j["kind"] == "page":
            assert engine.cmd_skip(_ns(repo, root, page_id=j["page_id"],
                                       reason="人工决定跳过")) == config.EXIT_OK
            skipped = j["page_id"]
        else:
            _submit(ctx, repo, root, j)
    return skipped


def test_begin_missing_skipped_survives_increment_run(tmp_path, capsys):
    repo, root = _fixture(tmp_path)
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    ctx = _ctx(repo, root)
    pid = ctx.partition()["id"]
    skipped = _drive_all_but_first_page(repo, root, ctx)
    assert skipped
    assert _cli(["finish", "--repo", repo.path, "--root", root, "--yes"]) == \
        config.EXIT_OK

    # 制造一次真实的 INCREMENT run 并收尾 —— 它成为 last_run
    repo.write("Makefile", "build:\n\techo changed\n")
    repo.commit("touch makefile")
    assert _cli(["update", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    _write_all_jobs(repo, root, style="concise")
    assert _cli(["finish", "--repo", repo.path, "--root", root, "--yes"]) == \
        config.EXIT_OK

    # 恢复跑不得复活被跳过的页(跳过集曾只看 last_run = INCREMENT)
    capsys.readouterr()
    assert _cli(["begin", "--missing", "--repo", repo.path,
                 "--root", root]) == config.EXIT_OK
    out = capsys.readouterr().out
    assert "nothing missing" in out, out
    assert ctx.db.active_run(pid) is None, "跳过的页被复活重排"


# --------------------------------------------------------------------------- #
# #2 快照: 发布行卡片 scope/source_files(JSON 字符串)真实进基线
# --------------------------------------------------------------------------- #

def test_snapshot_hashes_card_json_scope(tmp_path):
    from pwiki_lib import snapshot
    repo, root = _published_full(tmp_path)
    ctx = _ctx(repo, root)
    pid = ctx.partition()["id"]
    card = next(p for p in ctx.db.published_pages(pid)
                if p["kind"] == "card")
    # 行侧字段确是 JSON 字符串(逐字符遍历的触发前提)
    assert isinstance(dict(card)["source_files"], str)
    entries = snapshot.snapshot_hashes(ctx, [], [], [dict(card)])
    assert "Makefile" in entries, \
        "卡片 scope/source_files 没有进跟踪集(被当字符串逐字符遍历)"


# --------------------------------------------------------------------------- #
# B2 yamlite 解析器族(块标量 '#' 行 / 错缩进续行 / 撇号注释 / 同缩进块列表
# / 跨行流式列表 / '' 转义) + themes overrides 与扩展名单一事实源
# --------------------------------------------------------------------------- #

def test_yamlite_block_scalar_keeps_hash_lines():
    from pwiki_lib.yamlite import yaml_load
    # '#' 开头的行在 '|' 块标量里是正文(prompt 要求的标题结构曾静默蒸发)
    v = yaml_load("prompt: |\n  # 必须包含的标题\n  ## 性能指标\n  正文\n")
    assert v["prompt"] == "# 必须包含的标题\n## 性能指标\n正文"


def test_yamlite_misindented_continuation_raises():
    from pwiki_lib.yamlite import yaml_load
    # 错缩进的续行曾绕过 unparsed 守卫被静默吞掉(title 消失无报错)
    with pytest.raises(ValueError):
        yaml_load("pages:\n - id: a\n  title: A\n")


def test_yamlite_apostrophe_not_quote():
    from pwiki_lib.yamlite import yaml_load
    v = yaml_load("title: don't worry # TODO\nnext: 1\n")
    assert v["title"] == "don't worry", "行尾注释被焊进值"
    # 引号值里的 '#' 仍保留
    assert yaml_load('title: "a # b"\n')["title"] == "a # b"
    assert yaml_load("title: 'a' # c\n")["title"] == "a"


def test_yamlite_same_indent_block_list():
    from pwiki_lib.yamlite import yaml_load
    # PyYAML 默认块列表风格 'key:\n- a' 曾 unparsed line / frontmatter 误报
    v = yaml_load("kind: build_system\nsource_files:\n- README.md\n- x.py\n"
                  "name: 卡\n")
    assert v["source_files"] == ["README.md", "x.py"]
    assert v["name"] == "卡"


def test_yamlite_multiline_flow_list_raises():
    from pwiki_lib.yamlite import yaml_load
    # 跨行行内列表曾静默解析成字符串 '[' —— 现在明确报错
    with pytest.raises(ValueError):
        yaml_load('pages:\n - id: a\n   scope: [\n       "src/x.py",\n   ]')


def test_yamlite_quote_escape_pair():
    from pwiki_lib.yamlite import yaml_load
    assert yaml_load("list:\n  - 'don''t # x'\n")["list"] == ["don't # x"]


def test_yamlite_comments_tolerated_in_maps():
    from pwiki_lib.yamlite import yaml_load
    v = yaml_load("a:\n  # 注释\n  b: 1\n# 顶层注释\nd: 3\n")
    assert v == {"a": {"b": 1}, "d": 3}
    # 块标量后跟顶层注释: 注释不进块体
    v2 = yaml_load("prompt: |\n  正文\n# 顶层注释\ntitle: x\n")
    assert v2 == {"prompt": "正文", "title": "x"}


def test_card_frontmatter_block_list_accepted(tmp_path):
    """#35 的端到端口径: 合法 frontmatter(块列表)不再误报"必须带 frontmatter"。"""
    from pwiki_lib import gates
    content = ("---\nkind: build_system\nname: 构建\nsource_files:\n"
               "- Makefile\n---\n\n## 1. 总体方案\n\n事实。\n")
    item = {"kind": "card", "title": "构建", "card_kind": "build_system"}
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "Makefile").write_text("build:\n\techo b\n", encoding="utf-8")
    errs, _warns = gates.gate_item(content, item, str(repo), "deep",
                                   phase="submit")
    assert not [e for e in errs if "frontmatter" in e], errs


def test_load_overrides_warns_on_bad_yaml(tmp_path, capsys):
    """#38: 解析失败/Tab 缩进必须出声, 不再静默吞掉整批映射。"""
    from pwiki_lib.config import Ctx
    from pwiki_lib.themes import load_overrides
    root = tmp_path / "wiki"
    root.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "Makefile").write_text("b:\n", encoding="utf-8")
    import subprocess
    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)
    ov = repo / "pwiki.yaml"
    ov.write_text("title_maps:\n\t服务: 后端\n", encoding="utf-8")
    ctx = Ctx(types.SimpleNamespace(repo=str(repo), root=str(root),
                                    locale="zh"))
    got = load_overrides(ctx)
    assert got["title_maps"] == {}
    err = capsys.readouterr().err
    assert "Tab" in err, "Tab 缩进没有警告"
    ov.write_text("title_maps: [broken\n", encoding="utf-8")
    got2 = load_overrides(ctx)
    assert got2["title_maps"] == {}
    assert "解析失败" in capsys.readouterr().err


def test_strip_exts_single_source():
    """#41: .mjs/.cpp/.php 等不再漏剥; humanize 与 stem_of 同源。"""
    from pwiki_lib import config
    from pwiki_lib.corpus import stem_of
    from pwiki_lib.themes import humanize
    for name, stem in (("utils.mjs", "utils"), ("a.cpp", "a"),
                       ("b.php", "b"), ("x.mts", "x")):
        assert stem_of(name) == stem, name
        assert not humanize(name).endswith(name[name.index("."):]), name
    assert ".mts" in config.SOURCE_EXTS


# --------------------------------------------------------------------------- #
# B3 gate/文档契约族: compose 全 kind 钳位 / --fix 复查严重度 / 词上限 /
# D16 行级 / 栅栏感知章节 / 内链(卡片+锚点) / catalog 边 id 空间
# --------------------------------------------------------------------------- #

def _ctx_raw(tmp_path):
    from pwiki_lib.config import Ctx
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "src" / "a.py").write_text("x = 1\n" * 20, encoding="utf-8")
    (repo / "src" / "b.py").write_text("y = 2\n" * 200, encoding="utf-8")
    import subprocess
    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)
    root = tmp_path / "wiki"
    root.mkdir()
    ctx = Ctx(types.SimpleNamespace(repo=str(repo), root=str(root),
                                    locale="zh"))
    ctx.config = {"export_style": "legacy"}
    return ctx


def test_compose_clamps_and_normalizes_cards(tmp_path):
    """#7: 卡片/module_sheet 走 fix_off_by_one + normalize_links。"""
    from pwiki_lib import publish
    ctx = _ctx_raw(tmp_path)
    card = {"kind": "card", "path": "knowledge/zh/构建/构建.md",
            "content": ("引用 [b.py:1-201](file://src/b.py#L1-L201) 与"
                        " [邻居](其他.md)。\n")}
    out, _ = publish.compose_export_content(
        ctx, card, "deep",
        {"knowledge/zh/构建/构建.md", "knowledge/zh/其他/其他.md"})
    assert "#L1-L200" in out, "卡片引用差一未被钳位(曾只有页面钳)"
    # 相对链接按卡片真实位置改写(卡片目录出发要回一级)
    assert "(../其他/其他.md)" in out, out


def test_validate_fix_keeps_lining_severity(tmp_path):
    """#17: --fix 复查按 lining —— 一处可修引用不再把其余错误降成警告。"""
    from pwiki_lib.gates import validate_content
    ctx = _ctx_raw(tmp_path)
    page = {"id": "p1", "title": "测试页", "path": "zh/content/测试页.md",
            "required_sections": ["架构", "组件", "数据流"]}
    path = os.path.join(ctx.root, page["path"])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("# 测试页\n\n## 唯一节\n\nx "
                    "[a.py:1-3](file://src/a.py#L1-L3) "
                    "[b.py:1-201](file://src/b.py#L1-L201)\n")
    plan = {"style": "deep", "pages": [page], "cards": []}
    _rep, code = validate_content(ctx, plan, fix=False)
    assert code == config.EXIT_GATE
    _rep2, code2 = validate_content(ctx, plan, fix=True)
    assert code2 == config.EXIT_GATE, \
        "--fix 后复查曾降回 submit 口径, 风格错误静默清零"
    assert "#L1-L200" in open(path, encoding="utf-8").read(), "差一未钳位"


def test_words_cap_no_double_margin(tmp_path):
    """#19: 词上限不再二次 ×1.1, concise 恒为警告。"""
    from pwiki_lib import gates
    ctx = _ctx_raw(tmp_path)
    body = " ".join(["字"] * 3400)     # 3300 < 3400 < 3300×1.1=3630 死区
    content = ("# 测试页\n\n## 正文\n\n" + body + "\n\n**章节来源**\n"
               "- [src/a.py:1-3](file://src/a.py#L1-L3)\n")
    errs, warns = gates.gate_item(content, {"kind": "page", "title": "测试页"},
                                  ctx.repo, "concise", phase="lining")
    assert any("size/words" in w for w in warns), "死区 3400 词未触发"
    assert not any("size/words" in e for e in errs), \
        "concise 词超限在 LINING 被硬拒(曾不认 size_hard)"


def test_d16_counts_line_level_only(tmp_path):
    """#34: D16 按行级引用计数, 整文件链接不算数。"""
    from pwiki_lib import gates
    ctx = _ctx_raw(tmp_path)
    content = ("---\nkind: build_system\nname: 构建\n---\n\n"
               "事实 [a](file://src/a.py) [b](file://src/a.py) "
               "[c](file://src/a.py)\n")
    item = {"kind": "card", "title": "构建", "card_kind": "build_system"}
    errs, _ = gates.gate_item(content, item, ctx.repo, "deep", phase="lining")
    assert any("knowledge/citations" in e and "行级" in e for e in errs), \
        "3 条无行号引用曾凑满 ≥3 行级下限"


def test_fenced_headings_are_not_sections(tmp_path):
    """#27: 栅栏里的 '## ' 不是节; 栅栏内的来源块不满足要求; --fix 不注栅栏。"""
    from pwiki_lib import gates
    ctx = _ctx_raw(tmp_path)
    sec = "\n**章节来源**\n- [src/a.py:1-3](file://src/a.py#L1-L3)\n"
    fenced = ("```markdown\n## 安装示例\n示例\n## 用法示例\n示例\n**章节来源**\n"
              "- [src/a.py:1-3](file://src/a.py#L1-L3)\n```\n")
    good = ("# 测试页\n\n## 架构\n\nx\n" + sec + "## 组件\n\nx\n" + sec
            + fenced)
    errs, warns = gates.gate_item(good, {"kind": "page", "title": "测试页"},
                                  ctx.repo, "deep", phase="lining")
    assert not any("sources/section" in x for x in errs + warns), \
        f"幻影节改变了计数: {errs + warns}"
    # 反向: 真节缺来源, 栅栏里的示例来源块救不了
    bad = ("# 测试页\n\n## 架构\n\nx\n## 组件\n\nx\n## 数据流\n\nx\n" + fenced)
    errs2, _ = gates.gate_item(bad, {"kind": "page", "title": "测试页"},
                               ctx.repo, "deep", phase="lining")
    assert any("sources/section" in e for e in errs2)
    # --fix 不把来源块注进栅栏(第三轮 #52: 旧断言的切片恰好重新找到开
    # 栅栏、replace 删掉的就是搜索串, 且本夹具 n 恒为 0 —— 恒真空转,
    # 回归不可能被抓住)。真实不变量: 真节(带可收割引用)各注入一块、
    # 栅栏逐字节未动、注入块都在栅栏之外
    import re as _re
    cite = "[src/a.py:1-3](file://src/a.py#L1-L3)"
    bad2 = ("# 测试页\n\n## 架构\n\nx " + cite +
            "\n## 组件\n\nx " + cite + "\n## 数据流\n\nx " + cite + "\n"
            + fenced)
    fixed, n = gates.fix_section_sources(bad2)
    assert n == 3, n
    segs = _re.split(r"(```.*?```)", fixed, flags=_re.S)
    fences = [s for s in segs if s.startswith("```")]
    assert fences and all(f.strip() == fenced.strip() for f in fences), \
        "栅栏内容被 --fix 改写"
    outside = "".join(s for s in segs if not s.startswith("```"))
    assert outside.count("**章节来源**") == 3, "注入块落进了栅栏或数量不对"


def test_internal_links_cover_cards_and_anchors(tmp_path):
    """#28/#29: 卡片路径在内链目标集内; 带锚点的断链照常报错。"""
    from pwiki_lib.gates import validate_content
    ctx = _ctx_raw(tmp_path)
    page = {"id": "p1", "title": "页", "path": "zh/content/页.md"}
    card = {"id": "c1", "title": "构建", "path": "knowledge/zh/构建/构建.md"}
    for item in (page, card):
        p = os.path.join(ctx.root, item["path"])
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("正文\n")
    with open(os.path.join(ctx.root, page["path"]), "w",
              encoding="utf-8") as fh:
        fh.write("# 页\n\n[构建卡](../../knowledge/zh/构建/构建.md) 与 "
                 "[断链](../不存在.md#核心机制)\n")
    plan = {"style": "concise", "pages": [page], "cards": [card]}
    _rep, code = validate_content(ctx, plan)
    errs = _rep["global"]["errors"]
    assert code == config.EXIT_GATE
    assert any("不存在" in e for e in errs), "锚点断链曾整类漏检"
    assert not any("links/internal" in e and "构建" in e for e in errs), \
        "指向卡片的正确内链被误判死链(引擎自己会重写出这种链接)"


def test_metadata_edges_use_catalog_ids(tmp_path):
    """#31: slug != page_id 时, cat_relations 两端同在 catalog id 空间。"""
    import json as _json
    from pwiki_lib import publish
    ctx = _ctx_raw(tmp_path)
    parent = {"page_id": "svc", "title": "服务", "parent_id": "", "ordinal": 0,
              "kind": "page", "module": "", "sheet": "", "card_kind": "",
              "path": "zh/content/服务.md", "prompt": "", "source_files": "[]",
              "article_type": "", "slug": "services-slug", "objective": "",
              "modules": "[]", "summary": "", "key_topics": "[]"}
    child = dict(parent, page_id="ch", title="子", parent_id="svc", ordinal=1,
                 slug="", path="zh/content/子.md")
    publish._write_indexes(ctx, {"id": 1, "branch": "main", "locale": "zh"},
                           [parent, child], [], "manual")
    meta = _json.load(open(
        os.path.join(ctx.root, "zh", "meta", "repowiki-metadata.json"),
        encoding="utf-8"))
    cids = {c["id"] for c in meta["wiki_catalogs"]}
    assert "services-slug" in cids and "ch" in cids
    for r in meta["knowledge_relations"]:
        if r["source_type"] == "WIKI_ITEM":
            assert r["source_id"] in cids and r["target_id"] in cids, \
                f"父子边跨了 id 空间(导入即失联): {r}"


def test_write_honors_plan_export_style(tmp_path):
    """#30: 手工 write 与 finish 同口径取 plan 级 export_style。"""
    from pwiki_lib import planner
    repo, root = _published_full(tmp_path)          # concise legacy: 有 <cite>
    ctx = _ctx(repo, root)
    pid = ctx.partition()["id"]
    plan = planner.load_plan(ctx)
    plan["export_style"] = "current"
    planner.save_plan(ctx, plan)
    page = next(p for p in ctx.db.published_pages(pid) if p["kind"] == "page")
    raw = page["content"]
    f = os.path.join(root, "work", "manual.md")
    os.makedirs(os.path.dirname(f), exist_ok=True)
    with open(f, "w", encoding="utf-8") as fh:
        fh.write(raw)
    assert _cli(["write", page["page_id"], "--file", f,
                 "--repo", repo.path, "--root", root]) == config.EXIT_OK
    body = open(os.path.join(root, page["path"]), encoding="utf-8").read()
    assert "<cite>" not in body, "write 按 config(legacy) 重注入, 与 finish 翻转"


# --------------------------------------------------------------------------- #
# B4 scan / qoder-plan / layers / util 杂项
# --------------------------------------------------------------------------- #

def test_detect_commands_reads_makefile_from_repo(tmp_path, monkeypatch):
    """#9: Makefile 解析按 repo 解析, 不按进程 CWD。"""
    from pwiki_lib import scan
    ctx = _ctx_raw(tmp_path)
    repo = ctx.repo
    with open(os.path.join(repo, "Makefile"), "w", encoding="utf-8") as fh:
        fh.write("build-stable:\n\techo ok\n")
    elsewhere = tmp_path / "cwd"
    elsewhere.mkdir()
    (elsewhere / "Makefile").write_text("wrongbuild:\n\techo w\n",
                                        encoding="utf-8")
    monkeypatch.chdir(elsewhere)
    inv = scan.scan_repo(repo)
    joined = " ".join(inv["commands"]["build"] + inv["commands"]["dev"])
    assert "build-stable" in joined, joined
    assert "wrongbuild" not in joined, "读到了 CWD 的错误 Makefile"


def test_scan_symlinks_and_ignore_paths(tmp_path):
    """#42/#43/#44: 符号链接一律跳过并计数; --ignore 按 repo 相对路径;
    max-files 上限对单个平铺目录也生效。"""
    from pwiki_lib import scan
    ctx = _ctx_raw(tmp_path)
    repo = ctx.repo
    os.makedirs(os.path.join(repo, "gen", "deep"), exist_ok=True)
    for i in range(8):
        with open(os.path.join(repo, "gen", f"f{i}.py"), "w") as fh:
            fh.write("x\n")
    with open(os.path.join(repo, "gen", "deep", "d.py"), "w") as fh:
        fh.write("y\n")
    os.symlink(os.path.join(repo, "src", "a.py"),
               os.path.join(repo, "alias.py"))
    os.symlink(os.path.join(repo, "gen"), os.path.join(repo, "genlink"))
    inv = scan.scan_repo(repo, max_files=5,
                         extra_ignore=("gen",))
    paths = {f["path"] for f in inv["files"]}
    assert not any(p.startswith("gen") for p in paths), paths
    assert "alias.py" not in paths and "genlink" not in paths
    # 目录链接在 walk 的 dirs 过滤里剪掉(不进 names); 文件链接计入跳过数
    assert inv["stats"]["skipped_symlinks"] == 1
    n_repo_files = len(paths)
    assert n_repo_files <= 5, f"max_files=5 被平铺目录绕过: {n_repo_files}"


def test_expand_scope_excludes_binaries(tmp_path):
    """#39: scope 目录展开不收二进制(资产重导出曾触发整模块全量重写)。"""
    from pwiki_lib import scan
    ctx = _ctx_raw(tmp_path)
    repo = ctx.repo
    os.makedirs(os.path.join(repo, "assets"), exist_ok=True)
    with open(os.path.join(repo, "assets", "logo.png"), "wb") as fh:
        fh.write(b"\x89PNG\r\n")
    with open(os.path.join(repo, "assets", "data.sqlite"), "wb") as fh:
        fh.write(b"sqlite")
    with open(os.path.join(repo, "assets", "a.py"), "w") as fh:
        fh.write("x = 1\n")
    entries, seen = {}, set()
    scan.expand_scope(repo, entries, seen, "assets/")
    assert set(entries) == {"assets/a.py"}, entries


def test_git_survives_c_locale_non_ascii_branch(tmp_path, monkeypatch):
    """#20: LC_ALL=C 下非 ASCII 分支名不再裸 UnicodeDecodeError。"""
    from pwiki_lib import util
    import subprocess
    ctx = _ctx_raw(tmp_path)
    env = dict(os.environ, LC_ALL="C", LANG="C")
    subprocess.run(["git", "-C", ctx.repo, "add", "-A"], check=True, env=env)
    subprocess.run(["git", "-C", ctx.repo, "-c", "user.email=t@t",
                    "-c", "user.name=t", "commit", "-q", "-m", "i"],
                   check=True, env=env)
    subprocess.run(["git", "-C", ctx.repo, "checkout", "-q", "-b", "功能分支"],
                   check=True, env=env)
    monkeypatch.setenv("LC_ALL", "C")
    branch = util.git(ctx.repo, "rev-parse", "--abbrev-ref", "HEAD")
    assert branch == "功能分支", branch


def test_qoder_emit_parent_and_untyped_pages(tmp_path):
    """#11/#12: 父标题按同口径截断; 无 type 的页不再静默消失。"""
    import io
    import contextlib
    from pwiki_lib import cli
    from pwiki_lib.qoderplan import _emit
    from pwiki_lib.yamlite import yaml_dump, yaml_load
    ctx = _ctx_raw(tmp_path)
    long_title = "功能" * 20                       # 40 CJK ≈ 120 字节 > 100
    plan = {"schema_version": 3, "style": "concise", "title": "T", "pages": [
        {"id": "r1", "title": long_title, "kind": "page", "type": "domain",
         "objective": "覆盖", "ordinal": 0},
        {"id": "m1", "title": "中间层", "kind": "page", "ordinal": 1,
         "parent": "r1"},                          # 手改掉 type 的页
        {"id": "c1", "title": "子页", "kind": "page", "type": "deep_dive",
         "parent": "m1", "ordinal": 0, "objective": "深挖"},
    ], "cards": []}
    os.makedirs(os.path.dirname(ctx.plan_path), exist_ok=True)
    with open(ctx.plan_path, "w", encoding="utf-8") as fh:
        fh.write(yaml_dump(plan))
    out = os.path.join(ctx.root, "qplan.yaml")
    with contextlib.redirect_stdout(io.StringIO()):
        assert _emit(ctx, out) == config.EXIT_OK
    gen = yaml_load(open(out, encoding="utf-8").read())
    docs = gen["repowiki"]["documents"]
    ids_titles = [d["title"] for d in docs]
    assert len(docs) == 3, f"无 type 页/子树被静默丢弃: {ids_titles}"
    by_title = {d["title"]: d for d in docs}
    child = by_title["子页"]
    mid = by_title.get("中间层")
    assert mid is not None, "无 type 的中间页消失了"
    parent_title = next(t for t in ids_titles if t.startswith("功能"))
    assert len(parent_title.encode("utf-8")) <= 100, "父标题未截断"
    assert mid["parent"] == parent_title, \
        f"parent 引用了未截断的标题: {mid.get('parent')!r}"
    assert child.get("parent") == "中间层"
    # lint: 列表顶层文件干净报错(不 AttributeError)
    bad = os.path.join(ctx.root, "bad.yaml")
    with open(bad, "w", encoding="utf-8") as fh:
        fh.write("- a\n- b\n")
    code = cli.main(["qoder-plan", "--lint", bad,
                     "--repo", ctx.repo, "--root", ctx.root])
    assert code == config.EXIT_GATE


def test_layers_parse_result_prefers_valid_fence(tmp_path):
    """#26: 多栅栏时逐个尝试; 解析失败在 apply 层变干净门禁错。"""
    from pwiki_lib.layers import parse_result_block
    text = ("先看示例(残缺):\n```json\n{\"name\": \"repo\", \"scripts\":\n```\n"
            "结论:\n```json\n{\"articles\": [{\"slug\": \"a\"}]}\n```\n")
    v = parse_result_block(text)
    assert "articles" in v
    with pytest.raises(ValueError):
        parse_result_block("没有结果块, 只有散文")


def test_layers_commit_honors_plan_depth(tmp_path):
    """#21: commit 的深度循环用 plan_limits(plan 可覆盖), 不再硬编码 4。"""
    from pwiki_lib import layers
    ctx = _ctx_raw(tmp_path)
    plan = {"modules": [{"id": "root", "path": "/", "scope": ["src/"]}],
            "limits": {"max_depth": 5}}
    state = {"profile": "standard", "articles": [
        {"slug": "ov", "title": "总览", "type": "overview", "modules": ["/"],
         "objective": "o", "need_further_planning": False, "depth": 0,
         "parent": ""},
        {"slug": "d5", "title": "深层", "type": "deep_dive",
         "modules": ["src/"], "objective": "x",
         "need_further_planning": False, "depth": 5, "parent": "ov"},
    ]}
    pages = layers.materialize_layers(ctx, plan, state)
    ids = {p["id"] for p in pages}
    assert "d5" in ids, f"depth-5 文章被硬编码深度丢弃: {ids}"


def test_sheet_prompt_english():
    """#40: 英文 locale 的 sheet 提示词不再是中文。"""
    from pwiki_lib.i18n import sheet_prompt
    p = sheet_prompt("overview", "concise", "en", "Payment Gateway")
    assert "sentences" in p and "1-3" in p
    for style in ("concise", "deep"):
        for slug in ("overview", "stack"):
            text = sheet_prompt(slug, style, "en", "M")
            assert not re.search(r"[一-鿿]", text), (style, slug, text)


def test_budget_counts_content_pages_only(tmp_path):
    """#8: 预算只数内容页 —— sheets 叠加不再把三步序列锁死。"""
    from pwiki_lib.planner import validate_plan
    ctx = _ctx_raw(tmp_path)
    sheets = [{"id": f"s{i}", "title": f"表{i}", "kind": "module_sheet",
               "module": "src", "sheet": "overview", "parent": "",
               "ordinal": 900, "source_files": ["src/"], "prompt": "p"}
              for i in range(200)]
    plan = {"schema_version": 3, "style": "concise", "pages": sheets,
            "cards": [], "modules": [{"id": "src", "title": "S",
                                      "scope": ["src/"]}]}
    errors, _ = validate_plan(ctx, plan, profile="standard", strict=False)
    assert not any("预算" in e for e in errors), errors
    content = [{"id": f"p{i}", "title": f"页{i}", "kind": "page",
                "type": "deep_dive", "parent": "", "ordinal": i,
                "source_files": ["src/a.py"], "prompt": "p"}
               for i in range(140)]
    plan["pages"] = content
    errors2, _ = validate_plan(ctx, plan, profile="standard", strict=False)
    assert any("内容页" in e and "预算" in e for e in errors2), errors2


# --------------------------------------------------------------------------- #
# B5 性能与清理族的关键回归
# --------------------------------------------------------------------------- #

def test_queue_jobs_normalizes_cards(tmp_path):
    """卡片 job 规范化收敛在 queue_jobs 咽喉点(四处分拷曾漂移)。"""
    from pwiki_lib.db import WikiDB
    wdb = WikiDB(str(tmp_path / "db"))
    pid = wdb.partition("main", "zh")["id"]
    run_id = wdb.create_run(pid, "generate", "start", "FULL",
                            "DOCUMENT_GENERATING", "c" * 40, "h" * 8, {})
    wdb.queue_jobs(run_id, [
        {"id": "c1", "title": "构建", "kind": "build_system", "prompt": "p",
         "source_files": ["Makefile"]},
        {"id": "p1", "title": "页", "kind": "page", "prompt": "p"},
        {"id": "s1", "title": "表", "kind": "module_sheet", "prompt": "p"},
    ], style="concise")
    rows = {r["page_id"]: r for r in wdb.jobs(run_id)}
    assert rows["c1"]["kind"] == "card"
    assert rows["c1"]["card_kind"] == "build_system"
    assert rows["c1"]["ordinal"] == 950
    assert rows["p1"]["kind"] == "page" and rows["s1"]["kind"] == "module_sheet"


def test_jobs_meta_omits_blobs(tmp_path):
    """jobs_meta 与 jobs 行数一致但不含 content/report。"""
    from pwiki_lib.db import WikiDB
    wdb = WikiDB(str(tmp_path / "db"))
    pid = wdb.partition("main", "zh")["id"]
    run_id = wdb.create_run(pid, "generate", "start", "FULL",
                            "DOCUMENT_GENERATING", "c" * 40, "h" * 8, {})
    wdb.queue_jobs(run_id, [{"id": "p1", "title": "页", "kind": "page",
                             "prompt": "p"}])
    wdb.claim_jobs(run_id)
    wdb.set_job_state(run_id, "p1", "completed", content="# 页\n\n正文\n")
    full = wdb.jobs(run_id)
    meta = wdb.jobs_meta(run_id)
    assert len(full) == len(meta) == 1
    assert full[0]["content"] == "# 页\n\n正文\n"
    assert "content" not in meta[0].keys()
    assert "report" not in meta[0].keys()
    assert meta[0]["state"] == "completed"


def test_snapshot_run_incremental_hash_rows(tmp_path):
    """快照基线增量写: 离集行删除, 不再全表重写(delete+insert)。"""
    from pwiki_lib import snapshot
    repo, root = _published_full(tmp_path)
    ctx = _ctx(repo, root)
    pid = ctx.partition()["id"]
    plan = __import__("pwiki_lib.planner", fromlist=["load_plan"]).load_plan(ctx)
    run = {"id": "manual-snap"}
    snapshot.snapshot_run(ctx, run, plan)
    before = ctx.db.file_hashes(pid)
    assert before
    # 下架一张卡 → 其跟踪文件离集, 再快照后行要消失
    ctx.db.unpublish_pages(pid, [p["page_id"] for p in ctx.db.published_pages(pid)
                                 if p["kind"] != "card"])
    snapshot.snapshot_run(ctx, {"id": "manual-snap2"}, plan)
    after = ctx.db.file_hashes(pid)
    # 卡片源(Makefile 属卡 scope/source_files)不再被任何条目跟踪
    assert set(after) < set(before) or after, "基线表未按当前跟踪集收敛"


def test_lenient_prefixes_single_source():
    from pwiki_lib.gates import is_lenient_droppable
    assert is_lenient_droppable("schema/h1: H1 不匹配")
    assert is_lenient_droppable("citation/none: 页面至少 1 条")
    assert not is_lenient_droppable("citation/path: 引用路径不存在")
    assert not is_lenient_droppable("knowledge/frontmatter: 缺 frontmatter")


def test_corpus_collect_memoized(tmp_path, monkeypatch):
    """collect 对同 spec 全池调用记忆化(instantiate_pack 曾反复全仓扫)。"""
    from pwiki_lib import corpus as cz
    ctx = _ctx_raw(tmp_path)
    files = [{"path": "src/a.py", "lang": "python", "lines": 5, "bytes": 20},
             {"path": "README.md", "lang": "markdown", "lines": 2, "bytes": 8}]
    c = cz.Corpus(ctx.repo, files, {}, set())
    calls = []
    orig = cz.Corpus._collect
    monkeypatch.setattr(cz.Corpus, "_collect",
                        lambda self, spec, pool=None: calls.append(spec)
                        or orig(self, spec, pool))
    spec = {"patterns": ["src/**"]}
    r1 = c.collect(spec)
    r2 = c.collect(spec)
    assert r1 == r2
    assert len(calls) == 1, f"同 spec 重复扫描: {len(calls)}"


def test_doc_hits_pool_uses_path_set(tmp_path):
    from pwiki_lib import corpus as cz
    ctx = _ctx_raw(tmp_path)
    doc = {"path": "docs/guide.md", "lang": "markdown", "lines": 1, "bytes": 1}
    code = {"path": "src/a.py", "lang": "python", "lines": 1, "bytes": 1}
    c = cz.Corpus(ctx.repo, [doc, code], {}, set())
    assert "docs/guide.md" in c._doc_paths
    # pool 里的文档按路径集合命中; 非文档不进 grep 基池
    base_pool = [dict(doc), dict(code), dict(doc, path="other/x.md")]
    c.read = lambda p: "guide" if "guide" in p else ""
    hits = c.doc_hits(["guide"], pool=base_pool)
    assert {h["path"] for h in hits} == {"docs/guide.md"}
