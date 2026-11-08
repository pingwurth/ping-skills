"""engine.py: 生命周期、认领、拒绝保稿、报告摄入、--missing、pause。"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from gitrepo import GitRepo

from pwiki_lib import config


def _fixture(tmp_path):
    repo = GitRepo(tmp_path / "repo")
    for svc in ("user", "order"):
        for i in range(4):
            repo.write(f"services/{svc}/m{i}.py", "X = 1\n" * 25)
    repo.write("packages/shared/util.py", "def f():\n    pass\n" * 8)
    repo.write("README.md", "# 引擎测试仓库\n")
    repo.write("Makefile", "build:\n\techo b\n")
    repo.commit("init")
    root = str(tmp_path / "wiki")
    return repo, root


def _pipeline(repo, root, style="concise", sheets="standard"):
    """scan→modules→plan --seed→plan --sheets→digest→begin(公共底座)。"""
    from pwiki_lib import cli
    for argv in (["scan", "--repo", repo.path, "--root", root],
                 ["modules", "--repo", repo.path, "--root", root],
                 ["plan", "--seed", "--repo", repo.path, "--root", root],
                 ["plan", "--sheets", sheets, "--repo", repo.path, "--root", root],
                 ["digest", "--repo", repo.path, "--root", root]):
        assert cli.main(argv) == config.EXIT_OK, argv
    return root


def _begin(repo, root):
    from pwiki_lib import cli
    assert cli.main(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    import types
    from pwiki_lib.config import Ctx
    ctx = Ctx(types.SimpleNamespace(repo=repo.path, root=root,
                                    locale="zh")).load()
    run = ctx.db.active_run(ctx.partition()["id"])
    return ctx, run


def test_knowledge_first_bottom_up_claims(tmp_path):
    repo, root = _fixture(tmp_path)
    _pipeline(repo, root)
    ctx, run = _begin(repo, root)
    order = []
    while True:
        claimed = ctx.db.claim_jobs(run["id"], count=1)
        if not claimed:
            break
        order.append((claimed[0]["kind"], claimed[0]["page_id"]))
        ctx.db.set_job_state(run["id"], claimed[0]["page_id"], "completed",
                             content="x", claim_token=claimed[0]["claim_token"])
    kinds = [k for k, _ in order]
    assert kinds[0] == "module_sheet"
    assert "card" not in kinds or kinds.index("card") < kinds.index("page") \
        if "card" in kinds else True
    pages = [(k, p) for k, p in order if k == "page"]
    # 自底向上: 深页先于浅页(父页聚合)
    depths = {}
    jobs = {j["page_id"]: j for j in ctx.db.jobs(run["id"])}
    for _, pid in pages:
        d, cur, guard = 0, jobs[pid]["parent_id"], 0
        while cur and guard < 12:
            d += 1
            cur = jobs[cur]["parent_id"] if cur in jobs else ""
            guard += 1
        depths[pid] = d
    seq = [depths[pid] for _, pid in pages]
    assert seq == sorted(seq, reverse=True), f"页应自底向上, 实得 {seq}"


def test_submit_rejection_keeps_checkpoint(tmp_path):
    """拒绝 → pending 保 checkpoint; 修稿重交成功。"""
    from pwiki_lib import cli, engine
    repo, root = _fixture(tmp_path)
    _pipeline(repo, root)
    ctx, run = _begin(repo, root)
    claimed = ctx.db.claim_jobs(run["id"], count=1)
    job = claimed[0]
    bad_page = f"# 错标题\n\n## 正文\n\nx\n"   # H1 不匹配 + 无引用
    bad_file = str(tmp_path / "bad.md")
    with open(bad_file, "w", encoding="utf-8") as fh:
        fh.write(bad_page)
    import types as _t
    args = _t.SimpleNamespace(
        **{"repo": repo.path, "root": root, "locale": "zh",
           "page_id": job["page_id"], "file": bad_file, "summary": "",
           "key_topics": "", "report": None, "lenient": False, "force": False})
    with pytest.raises(SystemExit) as ei:
        engine.cmd_submit(args)
    assert ei.value.code == config.EXIT_GATE
    j2 = ctx.db.job(run["id"], job["page_id"])
    assert j2["state"] == "pending"
    assert j2["last_error"]           # 拒绝原因可见

    # 重新认领并交合格稿
    claimed2 = ctx.db.claim_jobs(run["id"], count=1)
    assert claimed2[0]["page_id"] == job["page_id"]
    j = claimed2[0]
    good = (f"# {j['title']}\n\n## 概述\n\n模块职责。\n\n**章节来源**\n"
            "- [README.md:1](file://README.md#L1)\n")
    good_file = str(tmp_path / "good.md")
    with open(good_file, "w", encoding="utf-8") as fh:
        fh.write(good)
    args2 = _t.SimpleNamespace(
        **{"repo": repo.path, "root": root, "locale": "zh",
           "page_id": j["page_id"], "file": good_file, "summary": "一句话",
           "key_topics": "BFF,RunSnapshot", "report": None,
           "lenient": False, "force": False, "claim": j["claim_token"]})
    code = engine.cmd_submit(args2)
    assert code == config.EXIT_OK
    j3 = ctx.db.job(run["id"], j["page_id"])
    assert j3["state"] == "completed"
    assert j3["content"] == good
    # 活术语表
    terms = [t["term"] for t in
             ctx.db.glossary_all(ctx.partition()["id"])]
    assert "BFF" in terms and "RunSnapshot" in terms


def test_report_ingestion_reconcile(tmp_path):
    """--report 的 D22/D7 进 reconcile 队列。"""
    from pwiki_lib import engine
    import types as _t
    repo, root = _fixture(tmp_path)
    _pipeline(repo, root)
    ctx, run = _begin(repo, root)
    claimed = ctx.db.claim_jobs(run["id"], count=1)
    j = claimed[0]
    good = (f"# {j['title']}\n\n## 概述\n\nx\n\n**章节来源**\n"
            "- [README.md:1](file://README.md#L1)\n")
    page_file = str(tmp_path / "p.md")
    with open(page_file, "w", encoding="utf-8") as fh:
        fh.write(good)
    report = {"title_mismatch": [{"proposed": "新标题", "evidence": "grep 证明"}],
              "card_conflict": [{"card": "错误处理体系",
                                 "card_line": "统一重试",
                                 "evidence": "src/a.py:1-5"}]}
    report_file = str(tmp_path / "report.json")
    with open(report_file, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False)
    args = _t.SimpleNamespace(
        **{"repo": repo.path, "root": root, "locale": "zh",
           "page_id": j["page_id"], "file": page_file, "summary": "",
           "key_topics": "", "report": report_file, "lenient": False,
           "force": False})
    assert engine.cmd_submit(args) == config.EXIT_OK
    pending = ctx.db.reconcile_pending(ctx.partition()["id"])
    kinds = {r["kind"] for r in pending}
    assert kinds == {"title_mismatch", "card_conflict"}
    tm = next(r for r in pending if r["kind"] == "title_mismatch")
    assert json.loads(tm["payload"])["proposed"] == "新标题"


def test_pause_refuses_with_lease(tmp_path):
    repo, root = _fixture(tmp_path)
    _pipeline(repo, root)
    ctx, run = _begin(repo, root)
    ctx.db.claim_jobs(run["id"], count=2)
    from pwiki_lib import cli
    with pytest.raises(SystemExit) as ei:   # die() 走 SystemExit
        cli.main(["pause", "--repo", repo.path, "--root", root])
    assert ei.value.code == config.EXIT_PRECOND   # 有租约拒绝
    # 释放后可暂停/恢复
    ctx.db.release_all_claims(run["id"])
    assert cli.main(["pause", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    assert cli.main(["resume", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK


def test_orphan_submit_on_token_mismatch(tmp_path):
    """租约被回收后 submit → exit 3 + 孤儿稿保存。"""
    from pwiki_lib import engine
    import types as _t
    repo, root = _fixture(tmp_path)
    _pipeline(repo, root)
    ctx, run = _begin(repo, root)
    claimed = ctx.db.claim_jobs(run["id"], count=1)
    j = claimed[0]
    ctx.db.conn.execute(
        "UPDATE page_job SET lease_expires_at='2000-01-01T00:00:00.000Z' "
        "WHERE run_id=? AND page_id=?", (run["id"], j["page_id"]))
    ctx.db.claim_jobs(run["id"], count=1)   # 回收+被"别人"认领
    page_file = str(tmp_path / "orphan.md")
    with open(page_file, "w", encoding="utf-8") as fh:
        fh.write("# 内容\n\n正文\n")
    args = _t.SimpleNamespace(
        **{"repo": repo.path, "root": root, "locale": "zh",
           "page_id": j["page_id"], "file": page_file, "summary": "",
           "key_topics": "", "report": None, "lenient": False, "force": False,
           "claim": j["claim_token"]})   # 过期旧令牌
    with pytest.raises(SystemExit) as ei:
        engine.cmd_submit(args)
    assert ei.value.code == config.EXIT_PRECOND
    orphan_dir = os.path.join(root, "work", config.ORPHAN_SUBMIT_DIR)
    assert os.path.isdir(orphan_dir) and os.listdir(orphan_dir)


def test_begin_missing_recovery(tmp_path):
    """--missing: 只排队未发布且未跳过的项。"""
    from pwiki_lib import cli
    repo, root = _fixture(tmp_path)
    _pipeline(repo, root)
    ctx, run = _begin(repo, root)
    # 完成 1 个、跳过 1 个, 其余 fail 掉使 run 可开新程
    claimed = ctx.db.claim_jobs(run["id"], count=2)
    ctx.db.set_job_state(run["id"], claimed[0]["page_id"], "completed",
                         content="done", claim_token=claimed[0]["claim_token"])
    ctx.db.set_job_state(run["id"], claimed[1]["page_id"], "skipped",
                         notes="不想写", claim_token=claimed[1]["claim_token"])
    ctx.db.update_run(run["id"], state="CANCELLED", finished_at=config.now_iso())
    # 手工发布 completed 那个, 模拟旧 wiki
    job = ctx.db.job(run["id"], claimed[0]["page_id"])
    ctx.db.publish_page(ctx.partition()["id"], job, job["content"], run["id"])
    ctx.db.unpublish_pages(ctx.partition()["id"],
                           [claimed[0]["page_id"]])
    code = cli.main(["begin", "--missing", "--repo", repo.path, "--root", root])
    assert code == config.EXIT_OK
    run2 = ctx.db.active_run(ctx.partition()["id"])
    ids = {j["page_id"] for j in ctx.db.jobs(run2["id"])}
    assert claimed[0]["page_id"] not in ids   # 已发布
    assert claimed[1]["page_id"] not in ids   # 已跳过
    assert ids


def test_status_json(tmp_path):
    from pwiki_lib import cli
    import types
    from pwiki_lib.config import Ctx
    repo, root = _fixture(tmp_path)
    _pipeline(repo, root)
    _begin(repo, root)
    code = cli.main(["status", "--json", "--repo", repo.path, "--root", root])
    assert code == config.EXIT_OK
    ctx = Ctx(types.SimpleNamespace(repo=repo.path, root=root,
                                    locale="zh")).load()
    run = ctx.db.active_run(ctx.partition()["id"])
    # leased 视图存在(空列表)
    assert ctx.db.leased_jobs(run["id"]) == []
