"""2026-11-09 第五轮 max 审查(pwiki 专项)批次①回修的回归测试 —— 生命
周期/并发簇 P5/P7/P8/P9/P12/P15/P23/P24/P25/P37。

对应报告: docs/code-review/2026-11-09-pwiki-max-r5.md(JSON 同目录)。
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
from test_review_fixes import _write_job_file


def _ns(repo, root, **kw):
    base = {"repo": repo.path, "root": root, "locale": "zh"}
    base.update(kw)
    return types.SimpleNamespace(**base)


def _drive_to_lining(tmp_path, style="concise"):
    """全生命周期推到 finish 的 D19 拒绝点 —— run 停在 LINING(既测 P7 的
    PRECOND 放行契约, 也给 P8/P15 提供"LINING 窗口"现场)。"""
    from pwiki_lib import engine
    repo, root = _fixture(tmp_path, style=style)
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    _write_all_jobs(repo, root, style=style)
    with pytest.raises(SystemExit) as ei:
        _cli(["finish", "--repo", repo.path, "--root", root])   # 无 --yes
    assert ei.value.code == config.EXIT_PRECOND
    ctx = _ctx(repo, root)
    run = ctx.db.active_run(ctx.partition()["id"])
    assert run is not None and run["state"] == "LINING"
    return repo, root, ctx, run


# --------------------------------------------------------------------------- #
# P5: complete_job_guarded 的 busy 集合曾漏 CANCELLED —— 死 run 上照收
# --------------------------------------------------------------------------- #

def test_p05_complete_guarded_rejects_cancelled_run(tmp_path):
    """无令牌提交撞上并发 force 取消: verdict=busy(走孤儿稿), 不在死 run
    上落 completed 还报 ok。"""
    repo, root = _fixture(tmp_path)
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    ctx = _ctx(repo, root)
    pid = ctx.partition()["id"]
    run = ctx.db.active_run(pid)
    job = ctx.db.claim_jobs(run["id"], count=1)[0]   # attempts=1, 无令牌可交
    # 协调者并发 begin/cancel --force: CANCELLED + 释放认领(回 pending/清 token)
    ctx.db.update_run(run["id"], state="CANCELLED", finished_at=now_iso())
    ctx.db.release_all_claims(run["id"])
    verdict = ctx.db.complete_job_guarded(run["id"], job["page_id"],
                                          "# 稿\n\n正文。\n", claim_token="")
    assert verdict == "busy", "死 run 上的提交未被拒收(曾返回 ok)"
    assert ctx.db.job(run["id"], job["page_id"])["state"] != "completed"


# --------------------------------------------------------------------------- #
# P9: summary/key_topics 曾是终写之后的裸自动提交 —— 竞争 finish 基线化空摘要
# --------------------------------------------------------------------------- #

def test_p09_summary_topics_land_with_completion(tmp_path):
    """摘要/关键话题随 completed 终写同一事务落行(手工与自动摘要两路)。"""
    from pwiki_lib import engine
    repo, root = _fixture(tmp_path)
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    ctx = _ctx(repo, root)
    run = ctx.db.active_run(ctx.partition()["id"])
    jobs = ctx.db.claim_jobs(run["id"], count=99)
    pages = [j for j in jobs if j["kind"] == "page"]
    assert len(pages) >= 2
    # 手工 summary + key_topics
    a = pages[0]
    assert engine.cmd_submit(_ns(
        repo, root, page_id=a["page_id"], file=_write_job_file(root, a),
        summary="手工摘要", key_topics="主题甲, 主题乙", report=None,
        lenient=False, force=False, claim=a["claim_token"])) == config.EXIT_OK
    row = ctx.db.job(run["id"], a["page_id"])
    assert row["summary"] == "手工摘要"
    assert json.loads(row["key_topics"]) == ["主题甲", "主题乙"]
    # 省略时自动摘首段(page 类)
    b = pages[1]
    assert engine.cmd_submit(_ns(
        repo, root, page_id=b["page_id"], file=_write_job_file(root, b),
        summary="", key_topics="", report=None,
        lenient=False, force=False, claim=b["claim_token"])) == config.EXIT_OK
    assert ctx.db.job(run["id"], b["page_id"])["summary"].strip(), \
        "自动摘要未随终写落行"


# --------------------------------------------------------------------------- #
# P7: finish 安全网只捕 Exception —— publish/snapshot 的 die() 带着已提交的
#     页面表残局逃逸, run 卡 LINING
# --------------------------------------------------------------------------- #

def test_p07_publish_die_marks_document_failed(tmp_path, monkeypatch):
    """发布/快照里的非 PRECOND die 落 DOCUMENT_FAILED(可恢复重跑 finish)。"""
    from pwiki_lib import snapshot
    repo, root = _fixture(tmp_path)
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    _write_all_jobs(repo, root)
    # 模拟 publish.py 同路径冲突类的 die(EXIT_GATE): 页面表已提交之后
    monkeypatch.setattr(
        snapshot, "snapshot_run",
        lambda *a, **k: (_ for _ in ()).throw(SystemExit(config.EXIT_GATE)))
    with pytest.raises(SystemExit) as ei:
        _cli(["finish", "--repo", repo.path, "--root", root, "--yes"])
    assert ei.value.code == config.EXIT_ENV
    ctx = _ctx(repo, root)
    run = ctx.db.active_run(ctx.partition()["id"])
    assert run["state"] == "DOCUMENT_FAILED", \
        f"die() 逃过安全网, run 卡在 {run['state']}"
    # 可恢复: 撤掉故障注入后重跑 finish 走完
    monkeypatch.undo()
    assert _cli(["finish", "--repo", repo.path, "--root", root,
                 "--yes"]) == config.EXIT_OK
    run = ctx.db.active_run(ctx.partition()["id"])
    assert run is None   # 已终态


def test_p07_precond_die_keeps_lining(tmp_path):
    """PRECOND 类 die(D19 要 --yes)按既定契约留在 LINING 等重跑。"""
    repo, root, ctx, run = _drive_to_lining(tmp_path)
    assert run["state"] == "LINING"
    # 补 --yes 重跑即通过
    assert _cli(["finish", "--repo", repo.path, "--root", root,
                 "--yes"]) == config.EXIT_OK
    assert ctx.db.active_run(ctx.partition()["id"]) is None


# --------------------------------------------------------------------------- #
# P8: cancel/regenerate --force/pause 缺 LINING 守卫 —— 发布窗口内取消让
#     publish 在 CANCELLED run 下完整跑完
# --------------------------------------------------------------------------- #

def test_p08_state_ops_blocked_during_lining(tmp_path):
    """LINING 窗口内 cancel(含 --force)/pause/regenerate 全部拒绝, run 不动;
    恢复路径是重跑 finish。"""
    from pwiki_lib import engine
    repo, root, ctx, run = _drive_to_lining(tmp_path)
    rid = run["id"]
    for argv, kwargs in (
            (["cancel", "--repo", repo.path, "--root", root], {}),
            (["cancel", "--force", "--repo", repo.path, "--root", root],
             {"force": True}),
            (["pause", "--repo", repo.path, "--root", root], {}),
            (["regenerate", "overview", "--repo", repo.path, "--root", root],
             {"page_id": "overview"})):
        with pytest.raises(SystemExit) as ei:
            if argv[0] == "regenerate":
                engine.cmd_regenerate(_ns(repo, root, **kwargs))
            elif argv[0] == "cancel":
                engine.cmd_cancel(_ns(repo, root, **kwargs))
            else:
                engine.cmd_pause(_ns(repo, root, **kwargs))
        assert ei.value.code == config.EXIT_PRECOND, argv
        assert ctx.db.active_run(ctx.partition()["id"])["state"] == "LINING"
    assert ctx.db.active_run(ctx.partition()["id"])["id"] == rid
    # 恢复: 重跑 finish(带 --yes)
    assert _cli(["finish", "--repo", repo.path, "--root", root,
                 "--yes"]) == config.EXIT_OK


def test_p08_cancel_write_is_state_guarded(tmp_path):
    """取消写带状态前置: run 已被并发方改走(如 CANCELLED)时不再裸覆盖。"""
    repo, root = _fixture(tmp_path)
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    ctx = _ctx(repo, root)
    pid = ctx.partition()["id"]
    run = ctx.db.active_run(pid)
    # 模拟并发方已把 run 置 CANCELLED —— 无守卫 UPDATE 会"成功"复活/重复写
    ctx.db.update_run(run["id"], state="CANCELLED", finished_at=now_iso())
    from pwiki_lib import engine
    with pytest.raises(SystemExit) as ei:
        engine.cmd_cancel(_ns(repo, root, force=True))
    assert ei.value.code == config.EXIT_PRECOND
    assert ctx.db.active_run(pid) is None   # CANCELLED 不被覆盖成活动态


# --------------------------------------------------------------------------- #
# P15: fail/skip 无 LINING 守卫 —— finish 的结论用 LINING 前计数快照,
#      此间改终局态会让重处理从结果里静默消失
# --------------------------------------------------------------------------- #

def test_p15_fail_skip_blocked_during_lining(tmp_path):
    from pwiki_lib import engine
    repo, root, ctx, run = _drive_to_lining(tmp_path)
    done = next(j for j in ctx.db.jobs(run["id"])
                if j["state"] == "completed")
    with pytest.raises(SystemExit) as ei:
        engine.cmd_fail(_ns(repo, root, page_id=done["page_id"],
                            error="手滑", force=True))
    assert ei.value.code == config.EXIT_PRECOND
    with pytest.raises(SystemExit) as ei2:
        engine.cmd_skip(_ns(repo, root, page_id=done["page_id"], reason="x"))
    assert ei2.value.code == config.EXIT_PRECOND
    assert ctx.db.job(run["id"], done["page_id"])["state"] == "completed"


# --------------------------------------------------------------------------- #
# P23: create_run_guarded 与 queue_jobs 之间的 DIRECTORY_GENERATING 窗口,
#      cmd_next 曾对空 job 表报 complete(退出码 0)
# --------------------------------------------------------------------------- #

def test_p23_next_reports_queueing_window(tmp_path, capsys):
    from pwiki_lib import engine
    repo, root = _fixture(tmp_path)
    ctx = _ctx(repo, root)
    pid = ctx.partition()["id"]
    ctx.db.create_run(pid, "begin", "full", "FULL",
                      "DIRECTORY_GENERATING", "c0ffee", "ph", {})
    capsys.readouterr()   # 排掉 fixture 阶段的 stdout, 只留 next 的 JSON
    rc = engine.cmd_next(_ns(repo, root, json=True, batch=1))
    assert rc == config.EXIT_PRECOND, "排队窗口被当成了可行动状态"
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "queueing", payload
    assert payload["state"] == "DIRECTORY_GENERATING"


# --------------------------------------------------------------------------- #
# P24: submit 的完成提示只看 pending —— 剩在写被报成全终态, 协调者提前
#      finish 又被 in-flight 前置弹回
# --------------------------------------------------------------------------- #

def test_p24_finish_hint_waits_for_inflight(tmp_path, capsys):
    from pwiki_lib import engine
    repo, root = _fixture(tmp_path)
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    ctx = _ctx(repo, root)
    run = ctx.db.active_run(ctx.partition()["id"])
    claimed = ctx.db.claim_jobs(run["id"], count=99)
    assert len(claimed) >= 2          # pending=0, 全部 processing
    j = claimed[0]
    capsys.readouterr()
    assert engine.cmd_submit(_ns(
        repo, root, page_id=j["page_id"], file=_write_job_file(root, j),
        summary="", key_topics="", report=None, lenient=False, force=False,
        claim=j["claim_token"])) == config.EXIT_OK
    out = capsys.readouterr().out
    assert "in-flight" in out, out
    assert "全部 job 已终态" not in out, out


# --------------------------------------------------------------------------- #
# P25: upsert_file_hashes 裸 executemany 逐句自动提交 —— 刷新中途崩溃留
#      半更新基线
# --------------------------------------------------------------------------- #

def test_p25_upsert_file_hashes_atomic(tmp_path, monkeypatch):
    from pwiki_lib.db import WikiDB
    wdb = WikiDB(str(tmp_path / "root"))
    entries = {f"f{i}.py": {"sha256": f"s{i}", "size": i, "mtime": i}
               for i in range(3)}
    # 写入与提交之间崩溃: commit 抛错 → 整笔回滚, 不留半份基线
    def boom():
        raise RuntimeError("crash before commit")
    monkeypatch.setattr(wdb, "commit", boom)
    with pytest.raises(RuntimeError, match="crash"):
        wdb.upsert_file_hashes(1, entries)
    assert not wdb.conn.in_transaction
    assert wdb._txn_depth == 0
    assert wdb.file_hashes(1) == {}, "半更新基线残留"
    # 正常路径: 整批落齐
    monkeypatch.undo()
    wdb.upsert_file_hashes(1, entries)
    assert set(wdb.file_hashes(1)) == set(entries)


# --------------------------------------------------------------------------- #
# P12: cmd_delete 无活跃 run 守卫 —— writer 期间的删除被 finish 静默复活
# --------------------------------------------------------------------------- #

def test_p12_delete_blocked_during_active_run(tmp_path):
    from pwiki_lib import engine
    from pwiki_lib.publish import cmd_delete
    repo, root = _fixture(tmp_path)
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    with pytest.raises(SystemExit) as ei:
        cmd_delete(_ns(repo, root, all=True))
    assert ei.value.code == config.EXIT_PRECOND
    with pytest.raises(SystemExit) as ei2:
        cmd_delete(_ns(repo, root, all=False, page_id="services"))
    assert ei2.value.code == config.EXIT_PRECOND
    # run 终结后放行(守卫按状态收窄, 不是一刀切)
    assert engine.cmd_cancel(_ns(repo, root, force=True)) == config.EXIT_OK
    assert cmd_delete(_ns(repo, root, all=True)) == config.EXIT_OK


# --------------------------------------------------------------------------- #
# P37: D22 改名是一串裸自动提交 —— UPDATE 与 save 之间中断留下
#      行已改/内容仍旧 H1 的分歧
# --------------------------------------------------------------------------- #

def test_p37_rename_db_mutations_rollback_together(tmp_path, monkeypatch):
    from pwiki_lib import planner
    from pwiki_lib.db import WikiDB
    from pwiki_lib.reconcile import cmd_reconcile
    repo, root = _fixture(tmp_path)
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    _write_all_jobs(repo, root)
    assert _cli(["finish", "--repo", repo.path, "--root", root,
                 "--yes"]) == config.EXIT_OK
    ctx = _ctx(repo, root)
    pid = ctx.partition()["id"]
    target = next(p for p in ctx.db.published_pages(pid)
                  if p["kind"] == "page")
    before = (target["title"], target["path"], target["content"])
    ctx.db.reconcile_add(pid, "title_mismatch",
                         {"page_id": target["page_id"],
                          "proposed": str(target["title"]) + "改名"})
    # 行 UPDATE 与内容保存之间崩溃(曾各自自动提交, 先写的留在库里)
    def boom(self, *a, **k):
        raise RuntimeError("crash between UPDATE and save")
    monkeypatch.setattr(WikiDB, "save_page_content", boom)
    with pytest.raises(RuntimeError, match="crash"):
        cmd_reconcile(_ns(repo, root, all=True))
    after = ctx.db.published_page(pid, target["page_id"])
    assert (after["title"], after["path"], after["content"]) == before, \
        "改名中途崩溃留下半改的行(未整体回滚)"
    assert not ctx.db.conn.in_transaction
    assert ctx.db._txn_depth == 0
    # 撤掉故障注入: 和解项仍在队列, 重跑完整落位
    monkeypatch.undo()
    assert cmd_reconcile(_ns(repo, root, all=True)) == config.EXIT_OK
    row = ctx.db.published_page(pid, target["page_id"])
    assert row["title"] == before[0] + "改名"
