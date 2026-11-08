"""2026-11-08 deep 审查(crp deep 载体1)回修的回归测试。

对应报告: ~/.local/state/code-review-p/ping-skills-d53ce8a1/
crp-20261108-203727-68ec/out/report.md (47 条, 5 P1)。
编号 cXXX 与该报告的 finding id 一致。
"""

from __future__ import annotations

import json
import os
import sys
import threading
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from gitrepo import GitRepo

from pwiki_lib import config
from test_e2e import _fixture, _write_all_jobs


from conftest import _cli, _ctx   # 单一来源(r4 #63)


def _cli_exit(argv):
    """把 die() 的 sys.exit 翻成退出码(测试断言用)。"""
    from pwiki_lib import cli
    try:
        return cli.main(argv)
    except SystemExit as e:
        return int(e.code)


# --------------------------------------------------------------------------- #
# c036: begin 活动检查与建 run 必须同一事务 —— 并发 begin 只出一个活动 run
# --------------------------------------------------------------------------- #

def test_create_run_guarded_returns_existing(tmp_path):
    repo, root = _fixture(tmp_path, style="concise")
    ctx = _ctx(repo, root)
    pid = ctx.partition()["id"]
    from pwiki_lib import planner
    plan = planner.load_plan(ctx)
    ph = planner.plan_hash(planner.plan_pages(plan), planner.plan_cards(plan))
    run_id, raced = ctx.db.create_run_guarded(
        pid, "generate", "start", "FULL", "DIRECTORY_GENERATING",
        "deadbeef", ph, {"locale": "zh"})
    assert run_id and raced is None
    # 第二个"并发"begin: 同一守卫路径必须吸收进已有活动 run
    run_id2, raced2 = ctx.db.create_run_guarded(
        pid, "generate", "start", "FULL", "DIRECTORY_GENERATING",
        "deadbeef", ph, {"locale": "zh"})
    assert run_id2 is None and raced2 is not None \
        and raced2["id"] == run_id


def test_concurrent_begin_yields_single_active_run(tmp_path):
    """线程级双 begin: 两个都 EXIT_OK, 但 run 表只有一个活动 run。"""
    repo, root = _fixture(tmp_path, style="concise")
    results = []

    def _begin():
        from pwiki_lib import cli
        results.append(cli.main(["begin", "--repo", repo.path,
                                 "--root", root]))

    t1 = threading.Thread(target=_begin)
    t2 = threading.Thread(target=_begin)
    t1.start(); t2.start(); t1.join(); t2.join()
    assert all(r == config.EXIT_OK for r in results), results
    ctx = _ctx(repo, root)
    pid = ctx.partition()["id"]
    rows = ctx.db.conn.execute(
        f"SELECT id FROM run WHERE partition_id=? AND state IN "
        f"('WAITING','INITIALIZING','DIRECTORY_GENERATING',"
        f"'DOCUMENT_GENERATING','LINING','PAUSED','DOCUMENT_FAILED')",
        (pid,)).fetchall()
    assert len(rows) == 1, [tuple(r) for r in rows]


# --------------------------------------------------------------------------- #
# c027: plan --themes 不再整体替换 —— 手工 prompt/title 与手加页默认保留
# --------------------------------------------------------------------------- #

def test_themes_replan_preserves_manual_page_edits(tmp_path):
    from pwiki_lib import cli
    from pwiki_lib.planner import load_plan, save_plan
    from test_themes import _svc_fixture
    repo = _svc_fixture(tmp_path)
    root = str(tmp_path / "wiki")
    for argv in (["scan"], ["modules"]):
        assert cli.main([*argv, "--repo", repo.path, "--root", root]) == 0
    assert cli.main(["plan", "--themes", "--repo", repo.path,
                     "--root", root]) == config.EXIT_OK
    ctx = _ctx(repo, root)
    plan = load_plan(ctx)
    target = next(p for p in plan["pages"] if not p.get("kind") == "module_sheet")
    engine_prompt = str(target.get("prompt") or "")
    target["prompt"] = "HANDTUNED-PROMPT-c027"
    target["title"] = "手工标题 c027"
    plan["pages"].append({"id": "manual-page-c027", "title": "手加页",
                          "kind": "page", "parent": "", "ordinal": 999,
                          "prompt": "手加页 prompt"})
    save_plan(ctx, plan)

    assert cli.main(["plan", "--themes", "--repo", repo.path,
                     "--root", root]) == config.EXIT_OK
    plan2 = load_plan(ctx)
    by_id = {str(p.get("id")): p for p in plan2["pages"]}
    assert str(target["id"]) in by_id
    assert by_id[str(target["id"])]["prompt"] == "HANDTUNED-PROMPT-c027"
    assert by_id[str(target["id"])]["title"] == "手工标题 c027"
    assert "manual-page-c027" in by_id, "手加页不得被静默删除"

    # --reset 才整体重建: 手加页消失, 引擎 prompt 恢复
    assert cli.main(["plan", "--themes", "--reset", "--repo", repo.path,
                     "--root", root]) == config.EXIT_OK
    plan3 = load_plan(ctx)
    ids3 = {str(p.get("id")) for p in plan3["pages"]}
    assert "manual-page-c027" not in ids3
    p3 = next(p for p in plan3["pages"]
              if str(p.get("id")) == str(target["id"]))
    assert p3["prompt"] != "HANDTUNED-PROMPT-c027"


# --------------------------------------------------------------------------- #
# c049/c051: file:// 引用与计划源路径不得逃出 repo
# --------------------------------------------------------------------------- #

def test_citations_outside_repo_rejected_uniformly(tmp_path):
    from pwiki_lib.gates import validate_citations
    repo = GitRepo(tmp_path / "r")
    repo.write("a.py", "x = 1\n" * 10)
    repo.commit("init")
    content = ("[abs](file:///etc/passwd#L1-L2)\n"
               "[up](file://../../../etc/passwd#L1-L2)\n"
               "[ok](file://a.py#L1-L2)\n")
    errs, cited = validate_citations(content, repo.path)
    esc = [e for e in errs if "越界" in e]
    assert len(esc) == 2, errs          # 绝对 + ../ 都拦, 同一措辞(无 oracle)
    assert not [e for e in errs if "passwd" in e and "行" in e], errs
    assert cited == {"a.py"}            # 越界路径不进引用集


def test_plan_source_files_outside_repo_rejected(tmp_path):
    from pwiki_lib.planner import validate_plan
    repo, root = _fixture(tmp_path, style="concise")
    ctx = _ctx(repo, root)
    from pwiki_lib.planner import load_plan
    plan = load_plan(ctx)
    plan["pages"][0]["source_files"] = ["/etc/shadow"]
    errors, _ = validate_plan(ctx, plan)
    assert any("越界" in e for e in errors), errors


def test_snapshot_hashes_skip_outside_repo(tmp_path):
    from pwiki_lib.snapshot import snapshot_hashes
    repo, root = _fixture(tmp_path, style="concise")
    ctx = _ctx(repo, root)
    page = {"content": "[x](file://../../../etc/hostname#L1)\n",
            "path": "zh/content/x.md"}
    hashes = snapshot_hashes(ctx, [page], [], [])
    assert not [k for k in hashes if "hostname" in k], hashes


# --------------------------------------------------------------------------- #
# c086/c022/c034/c037/c040/c039: 契约与守卫族
# --------------------------------------------------------------------------- #

def test_next_no_run_returns_precond(tmp_path):
    repo, root = _fixture(tmp_path, style="concise")
    assert _cli(["next", "--repo", repo.path, "--root", root]) \
        == config.EXIT_PRECOND


def test_fail_unknown_id_is_rejected(tmp_path):
    repo, root = _fixture(tmp_path, style="concise")
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == 0
    code = _cli_exit(["fail", "no-such-page", "--error", "boom",
                 "--repo", repo.path, "--root", root])
    assert code == config.EXIT_PRECOND
    ctx = _ctx(repo, root)
    run = ctx.db.active_run(ctx.partition()["id"])
    logs = ctx.db.conn.execute(
        "SELECT event FROM run_log WHERE run_id=? AND event='page_failed'",
        (run["id"],)).fetchall()
    assert not logs, "幽灵 id 不得写进 run_log"


def test_fail_with_wrong_claim_token_rejected(tmp_path):
    repo, root = _fixture(tmp_path, style="concise")
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == 0
    ctx = _ctx(repo, root)
    run = ctx.db.active_run(ctx.partition()["id"])
    job = ctx.db.claim_jobs(run["id"], count=1)[0]
    code = _cli_exit(["fail", job["page_id"], "--error", "x",
                 "--claim", "not-the-token",
                 "--repo", repo.path, "--root", root])
    assert code == config.EXIT_PRECOND
    # 正确令牌(c034: fail 也能断言自己的租约)则成功
    code2 = _cli(["fail", job["page_id"], "--error", "x",
                  "--claim", job["claim_token"],
                  "--repo", repo.path, "--root", root])
    assert code2 == config.EXIT_OK


def test_submit_blocked_while_lining(tmp_path):
    repo, root = _fixture(tmp_path, style="concise")
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == 0
    ctx = _ctx(repo, root)
    run = ctx.db.active_run(ctx.partition()["id"])
    job = ctx.db.claim_jobs(run["id"], count=1)[0]
    ctx.db.update_run(run["id"], state="LINING")
    page_file = os.path.join(root, "work", "lining.md")
    with open(page_file, "w", encoding="utf-8") as fh:
        fh.write("# t\n\n正文\n")
    code = _cli_exit(["submit", job["page_id"], "--file", page_file,
                 "--force", "--repo", repo.path, "--root", root])
    assert code == config.EXIT_PRECOND


def test_regenerate_with_live_lease_needs_force(tmp_path):
    repo, root = _fixture(tmp_path, style="concise")
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == 0
    ctx = _ctx(repo, root)
    run = ctx.db.active_run(ctx.partition()["id"])
    ctx.db.claim_jobs(run["id"], count=1)     # 在制租约
    from pwiki_lib.planner import load_plan, plan_pages
    pid = str(plan_pages(load_plan(ctx))[0]["id"])
    code = _cli_exit(["regenerate", pid, "--repo", repo.path, "--root", root])
    assert code == config.EXIT_PRECOND
    assert _cli(["regenerate", pid, "--force", "--repo", repo.path,
                 "--root", root]) == config.EXIT_OK


def test_write_refused_while_run_active(tmp_path):
    repo, root = _fixture(tmp_path, style="concise")
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == 0
    ctx = _ctx(repo, root)
    pages = ctx.db.published_pages(ctx.partition()["id"])
    assert not pages          # 尚未发布
    code = _cli_exit(["write", "whatever", "--file", "-", "--repo", repo.path,
                 "--root", root])
    assert code == config.EXIT_PRECOND


# --------------------------------------------------------------------------- #
# c046/c048: 可诊断失败族
# --------------------------------------------------------------------------- #

def test_update_bad_from_commit_rejected(tmp_path):
    repo, root = _fixture(tmp_path, style="concise")
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == 0
    _write_all_jobs(repo, root, style="concise")
    assert _cli(["finish", "--yes", "--repo", repo.path,
                 "--root", root]) == config.EXIT_OK
    code = _cli_exit(["update", "--from-commit", "0" * 40,
                 "--repo", repo.path, "--root", root])
    assert code == config.EXIT_PRECOND


def test_submit_non_utf8_file_clean_gate_error(tmp_path):
    repo, root = _fixture(tmp_path, style="concise")
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == 0
    ctx = _ctx(repo, root)
    run = ctx.db.active_run(ctx.partition()["id"])
    job = ctx.db.claim_jobs(run["id"], count=1)[0]
    bad = os.path.join(root, "work", "bad.md")
    os.makedirs(os.path.dirname(bad), exist_ok=True)
    with open(bad, "wb") as fh:
        fh.write(b"# t\n\n\xff\xfe\xfd\n")
    code = _cli_exit(["submit", job["page_id"], "--file", bad,
                 "--repo", repo.path, "--root", root])
    assert code == config.EXIT_GATE


# --------------------------------------------------------------------------- #
# c003/c006/c044: 纯函数与配置族
# --------------------------------------------------------------------------- #

def test_yamlite_roundtrip_underscore_and_special_floats(tmp_path):
    from pwiki_lib.yamlite import yaml_dump, yaml_load
    d = {"k": "2026_11_08", "v": "1_000", "n": "nan", "i": "inf",
         "neg": "-inf", "ok": 123, "f": 1.5, "s": "普通"}
    back = yaml_load(yaml_dump(d))
    assert back == d, (back, d)          # 字符串身份不被偷换成数值


def test_qoderplan_trunc_bytes_safe():
    from pwiki_lib.qoderplan import CAP_TITLE_BYTES, _trunc_bytes
    mixed = "架构设計📐" * 30            # CJK + 4 字节 emoji 混排
    out = _trunc_bytes(mixed, CAP_TITLE_BYTES)
    assert len(out.encode("utf-8")) <= CAP_TITLE_BYTES
    out.encode("utf-8").decode("utf-8")  # 不切半个字符(可无损解码)
    assert _trunc_bytes("short", CAP_TITLE_BYTES) == "short"


def test_config_corrupt_json_falls_back_with_defaults(tmp_path):
    from pwiki_lib.config import Ctx
    root = tmp_path / "w"
    root.mkdir()
    (root / "config.json").write_text("{not json", encoding="utf-8")
    ctx = Ctx(types.SimpleNamespace(repo=str(tmp_path), root=str(root),
                                    locale="zh"))
    ctx.load()                            # 不崩, 且回默认
    assert ctx.config.get("style", "concise")


def test_save_plan_leaves_no_tmp(tmp_path):
    from pwiki_lib.planner import save_plan, load_plan
    repo, root = _fixture(tmp_path, style="concise")
    ctx = _ctx(repo, root)
    plan = load_plan(ctx)
    plan["repo_summary"] = "atomic"
    save_plan(ctx, plan)
    assert not [f for f in os.listdir(root) if f.endswith(".tmp")]
    assert load_plan(ctx)["repo_summary"] == "atomic"


# --------------------------------------------------------------------------- #
# c026: writer 报告全通道入账(files_read_beyond / uncertainties)
# --------------------------------------------------------------------------- #

def test_submit_report_channels_recorded(tmp_path):
    repo, root = _fixture(tmp_path, style="concise")
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == 0
    ctx = _ctx(repo, root)
    run = ctx.db.active_run(ctx.partition()["id"])
    job = ctx.db.claim_jobs(run["id"], count=1)[0]
    if job["kind"] == "card":
        content = (f"---\nkind: {job['card_kind']}\nname: {job['title']}\n---\n\n"
                   "## 1. 总体方案\n\n事实。\n")
    elif job["kind"] == "module_sheet":
        content = f"# {job['title']}\n\n模块负责 X；位于 Y；对外提供 Z。\n"
    else:
        sec = "\n**章节来源**\n- [README.md:1](file://README.md#L1)\n"
        content = (f"# {job['title']}\n\n## 概述\n\n页面摘要。\n"
                   f"## 架构\n\nx\n{sec}\n## 组件\n\nx\n{sec}"
                   f"## 数据流\n\nx\n{sec}## 配置\n\nx\n{sec}")
    page_file = os.path.join(root, "work", f"{job['page_id']}.md")
    os.makedirs(os.path.dirname(page_file), exist_ok=True)
    with open(page_file, "w", encoding="utf-8") as fh:
        fh.write(content)
    report = {"files_read_beyond_sources": ["extra/a.py", "extra/b.py"],
              "uncertainties": ["foo 的返回形状未确认"]}
    rep_file = os.path.join(root, "work", "report.json")
    with open(rep_file, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False)
    code = _cli(["submit", job["page_id"], "--file", page_file,
                 "--claim", job["claim_token"], "--summary", "s",
                 "--key-topics", "a", "--report", rep_file,
                 "--repo", repo.path, "--root", root])
    assert code == config.EXIT_OK, job["page_id"]
    events = {row[0] for row in ctx.db.conn.execute(
        "SELECT event FROM run_log WHERE run_id=?", (run["id"],)).fetchall()}
    assert "files_read_beyond" in events
    assert "uncertainties" in events


# --------------------------------------------------------------------------- #
# 残留收口: 同族 join(planner scope ×3 / expand_scope / workspace / 基线键)
# 与 c086 同族退出码(qoderplan lint / submit / write 的输入文件缺失)
# --------------------------------------------------------------------------- #

def test_plan_scope_outside_repo_rejected(tmp_path):
    from pwiki_lib.planner import validate_plan
    repo, root = _fixture(tmp_path, style="concise")
    ctx = _ctx(repo, root)
    from pwiki_lib.planner import load_plan
    plan = load_plan(ctx)
    plan["pages"][0]["scope"] = ["../../../etc"]
    if plan.get("cards"):
        plan["cards"][0]["source_files"] = ["/etc/hostname"]
    if plan.get("modules"):
        plan["modules"][0]["scope"] = ["../outside"]
    errors, _ = validate_plan(ctx, plan)
    esc = [e for e in errors if "越界" in e]
    # page scope + card source(+module scope 若存在)各自拦截
    assert len(esc) >= 2, (esc, errors)
    assert not [e for e in errors if "路径不存在" in e and "etc" in e], errors


def test_expand_scope_drops_outside_entries(tmp_path):
    from pwiki_lib.scan import expand_scope
    repo = GitRepo(tmp_path / "r")
    repo.write("a/b.py", "x = 1\n")
    repo.commit("init")
    entries, seen = {}, set()
    expand_scope(repo.path, entries, seen, "a/")
    expand_scope(repo.path, entries, seen, "../../../etc/")
    expand_scope(repo.path, entries, seen, "/etc/")
    expand_scope(repo.path, entries, seen, "no-such-dir/")
    assert set(entries) == {"a/b.py"}, entries


def test_qoderplan_lint_missing_file_is_precond(tmp_path):
    from pwiki_lib.qoderplan import _lint
    from pwiki_lib.config import Ctx
    ctx = Ctx(types.SimpleNamespace(repo=str(tmp_path), root=str(tmp_path),
                                    locale="zh"))
    ctx.load()
    assert _lint(ctx, str(tmp_path / "nope.yaml")) == config.EXIT_PRECOND


def test_submit_missing_input_file_is_precond(tmp_path):
    repo, root = _fixture(tmp_path, style="concise")
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == 0
    ctx = _ctx(repo, root)
    run = ctx.db.active_run(ctx.partition()["id"])
    job = ctx.db.claim_jobs(run["id"], count=1)[0]
    code = _cli_exit(["submit", job["page_id"], "--file",
                      os.path.join(root, "work", "nope.md"),
                      "--repo", repo.path, "--root", root])
    assert code == config.EXIT_PRECOND
