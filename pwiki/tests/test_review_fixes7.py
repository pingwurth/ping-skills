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
    PRECOND 放行契约, 也给 P8/P15 提供"LINING 窗口"现场)。

    P1 修复后 init/begin 会落发布骨架, 全新 workspace 不再触发 D19 ——
    这里拆掉骨架模拟根漂移(目录被手删/typo 的现场)。"""
    import shutil
    from pwiki_lib import engine
    repo, root = _fixture(tmp_path, style=style)
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    _write_all_jobs(repo, root, style=style)
    # 拆掉整个 locale 目录(两级缺失)才触发 D19; 只拆 content 只剩一级
    import contextlib
    with contextlib.suppress(OSError):
        shutil.rmtree(os.path.join(root, "zh"))
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


# =========================================================================== #
# 批次②: 样式戳/导出双渲染器(P1/P2/P3/P31/P32/P65)
# =========================================================================== #

def _publish_deep(tmp_path):
    """deep 全生命周期发布(短稿过不了 deep 分档 —— --force 全放, 同
    test_full_lifecycle_deep 口径)。返回 (repo, root, ctx, pid)。"""
    repo, root = _fixture(tmp_path, style="deep")
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    _write_all_jobs(repo, root, style="deep")
    assert _cli(["finish", "--force", "--repo", repo.path, "--root", root,
                 "--yes"]) == config.EXIT_OK
    ctx = _ctx(repo, root)
    return repo, root, ctx, ctx.partition()["id"]


def test_p01_fresh_finish_needs_no_yes(tmp_path):
    """init/begin 落发布骨架后, 全新 workspace 首次 finish 无需 --yes。"""
    repo, root = _fixture(tmp_path)
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    _write_all_jobs(repo, root)
    assert _cli(["finish", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK      # 无 --yes(D19 只对骨架外漂移触发)
    ctx = _ctx(repo, root)
    assert ctx.db.active_run(ctx.partition()["id"]) is None


def test_p02_db_to_file_respects_row_stamps(tmp_path):
    """deep 行撞 concise 计划: db_to_file 按行戳渲染(不注入), 注入层不
    被 file_to_db 吸收进 page.content(content_hash 不变)。"""
    from pwiki_lib import planner
    repo, root, ctx, pid = _publish_deep(tmp_path)
    deep = next(p for p in ctx.db.published_pages(pid)
                if p["kind"] == "page" and p["style"] == "deep")
    h0 = deep["content_hash"]
    plan = planner.load_plan(ctx)
    plan["style"] = "concise"
    planner.save_plan(ctx, plan)
    assert _cli(["export", "--direction", "db_to_file", "--repo", repo.path,
                 "--root", root]) == config.EXIT_OK
    with open(os.path.join(root, deep["path"]), encoding="utf-8") as fh:
        text = fh.read()
    assert "## 目录" not in text, "deep 行被按 concise 注入了编号目录"
    assert _cli(["export", "--direction", "file_to_db", "--repo", repo.path,
                 "--root", root]) == config.EXIT_OK
    after = ctx.db.published_page(pid, deep["page_id"])
    assert after["content_hash"] == h0, "注入层被吸收进 page.content"


def test_p03_rename_composes_row_style(tmp_path):
    """D22 改名落盘按行自己的盖章风格: deep 行撞 concise 计划不注入
    目录, 行戳保持 deep(file_to_db 剥除判定与文件一致)。"""
    from pwiki_lib import planner
    from pwiki_lib.reconcile import cmd_reconcile
    repo, root, ctx, pid = _publish_deep(tmp_path)
    deep = next(p for p in ctx.db.published_pages(pid)
                if p["kind"] == "page" and p["style"] == "deep")
    plan = planner.load_plan(ctx)
    plan["style"] = "concise"
    planner.save_plan(ctx, plan)
    ctx.db.reconcile_add(pid, "title_mismatch",
                         {"page_id": deep["page_id"],
                          "proposed": str(deep["title"]) + "改名"})
    assert cmd_reconcile(_ns(repo, root, all=True)) == config.EXIT_OK
    row = ctx.db.published_page(pid, deep["page_id"])
    assert row["style"] == "deep", "改名把行戳翻成了 plan 级风格"
    with open(os.path.join(root, row["path"]), encoding="utf-8") as fh:
        text = fh.read()
    assert "## 目录" not in text, "改名文件被按 concise 注入了目录"


def test_p31_unstamped_submit_follows_plan(tmp_path, capsys):
    """未盖章行的有效样式链与 finish 同序(job>run>plan>ctx)。"""
    from pwiki_lib import engine, planner
    repo, root = _fixture(tmp_path)          # ctx.style=concise
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    ctx = _ctx(repo, root)
    run = ctx.db.active_run(ctx.partition()["id"])
    plan = planner.load_plan(ctx)
    plan["style"] = "deep"                    # 与 ctx 相反
    planner.save_plan(ctx, plan)
    # 模拟手编/旧库的未盖章行: job 与 run 的 style 都抹掉
    ctx.db.conn.execute("UPDATE run SET style='' WHERE id=?", (run["id"],))
    claimed = ctx.db.claim_jobs(run["id"], count=9)
    job = next(j for j in claimed if j["kind"] == "page")
    ctx.db.conn.execute(
        "UPDATE page_job SET style='' WHERE run_id=? AND page_id=?",
        (run["id"], job["page_id"]))
    capsys.readouterr()
    assert engine.cmd_submit(_ns(
        repo, root, page_id=job["page_id"], file=_write_job_file(root, job),
        summary="", key_topics="", report=None, lenient=False, force=False,
        claim=job["claim_token"])) == config.EXIT_OK
    assert "style=deep" in capsys.readouterr().out, \
        "submit 仍按 ctx(concise)判样式 —— 与 finish 的 plan 层分叉"


def test_p32_module_mention_enforced_at_lining(tmp_path, capsys, monkeypatch):
    """指派模块未提及: 严重度走预设键 —— deep 的 LINING 升错误(曾只有
    submit 传 mod_titles, strict 升级层是死代码), concise 保持警告(种子页
    常挂根模块, 不强迫复述仓库名); finish 布线把 mod_titles 传进门禁。"""
    from pwiki_lib import gates
    ctx = _ctx(*_fixture(tmp_path)[:2])
    # 深度页措辞(不含模块名; 带一条真实引用避开 citation/none 硬错)
    content = ("# 页\n\n## 概述\n\n正文。\n\n"
               "- [README.md:1](file://README.md#L1-L1)\n")
    item = {"kind": "page", "title": "页", "modules": '["services"]'}
    # submit 阶段恒为警告
    errs, warns = gates.gate_item(content, item, ctx.repo, "deep",
                                  phase="submit", mod_titles=["后端服务"])
    assert not errs and any("后端服务" in w for w in warns)
    # LINING 按 preset 分档: deep=error, concise=warn
    errs, _ = gates.gate_item(content, item, ctx.repo, "deep",
                              phase="lining", mod_titles=["后端服务"])
    assert any("后端服务" in e for e in errs), "deep 的 strict 升级层未生效"
    errs2, warns2 = gates.gate_item(content, item, ctx.repo, "concise",
                                    phase="lining", mod_titles=["后端服务"])
    assert not errs2 and any("后端服务" in w for w in warns2)

    # finish 布线: LINING 循环确实把 mod_titles 喂给了门禁
    repo, root = _fixture(tmp_path)
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    _write_all_jobs(repo, root)
    seen = []
    orig = gates.gate_item

    def spy(content, item, repo_, style, phase="submit", mod_titles=None, **kw):
        seen.append((phase, mod_titles))
        return orig(content, item, repo_, style, phase=phase,
                    mod_titles=mod_titles, **kw)

    monkeypatch.setattr(gates, "gate_item", spy)
    assert _cli(["finish", "--repo", repo.path, "--root", root,
                 "--yes"]) == config.EXIT_OK
    lining_calls = [m for ph, m in seen if ph == "lining"]
    assert lining_calls and any(lining_calls), \
        "finish 的 LINING 门禁没传 mod_titles(strict 层无输入)"


def test_p65_write_save_and_restyle_atomic(tmp_path, monkeypatch):
    """write 的保存与重盖章同事务: 中途崩溃不留"新内容+旧样式戳"。"""
    from pwiki_lib import planner
    from pwiki_lib.db import WikiDB
    from pwiki_lib.publish import cmd_write
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
    # 计划切 deep → write 按新口径验收并把行重盖章(触发两段写)
    plan = planner.load_plan(ctx)
    plan["style"] = "deep"
    planner.save_plan(ctx, plan)
    new_file = _write_job_file(root, target)
    with open(new_file, "a", encoding="utf-8") as fh:
        fh.write("\n追加一段, 内容与原稿不同。\n")

    def boom(self, *a, **k):
        raise RuntimeError("crash after save")

    monkeypatch.setattr(WikiDB, "set_page_style", boom)
    with pytest.raises(RuntimeError, match="crash after save"):
        cmd_write(_ns(repo, root, page_id=target["page_id"], file=new_file,
                      lenient=False, force=True))
    row = ctx.db.published_page(pid, target["page_id"])
    assert row["style"] == "concise", "重盖章前崩溃, 行戳已被单独改写"
    assert "追加一段" not in row["content"], "内容保存未随回滚撤销"
    assert not ctx.db.conn.in_transaction
    monkeypatch.undo()
    assert cmd_write(_ns(repo, root, page_id=target["page_id"],
                         file=new_file, lenient=False, force=True)) == \
        config.EXIT_OK
    row = ctx.db.published_page(pid, target["page_id"])
    assert row["style"] == "deep" and "追加一段" in row["content"]


# =========================================================================== #
# 批次③: 围栏感知/门禁反馈/稿件不丢(P4/P6/P13/P14/P19/P20/P28)
# =========================================================================== #

def test_p04_gate_rejected_stdin_saved(tmp_path, monkeypatch):
    """门禁拒绝的 stdin 稿也保稿(cmd_submit 唯一曾不落 orphan 的路径)。"""
    import io
    from pwiki_lib import engine
    repo, root = _fixture(tmp_path)
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    ctx = _ctx(repo, root)
    run = ctx.db.active_run(ctx.partition()["id"])
    job = ctx.db.claim_jobs(run["id"], count=9)[0]
    bad = f"# {job['title']}\n\n- [nope.py:1](file://nope.py#L1-L1)\n"
    monkeypatch.setattr("sys.stdin", io.StringIO(bad))
    with pytest.raises(SystemExit) as ei:
        engine.cmd_submit(_ns(repo, root, page_id=job["page_id"], file="-",
                              summary="", key_topics="", report=None,
                              lenient=False, force=False,
                              claim=job["claim_token"]))
    assert ei.value.code == config.EXIT_GATE
    orphan_dir = os.path.join(root, "work", "orphan-submits")
    saved = [f for f in os.listdir(orphan_dir)
             if f.startswith(job["page_id"])] if os.path.isdir(orphan_dir) else []
    assert saved, "被拒的 stdin 稿件蒸发(未落 orphan)"
    rows = ctx.db.conn.execute(
        "SELECT detail FROM run_log WHERE run_id=? AND event='submit_rejected'",
        (run["id"],)).fetchall()
    assert rows and "orphan-submits" in rows[-1]["detail"]


def test_p06_strip_injected_layers_fence_aware(tmp_path):
    """file_to_db/validate 剥注入物不碰围栏示例 —— 带示例页不再被写坏。"""
    from pwiki_lib.gates import strip_injected_layers
    fenced = ("```markdown\n<cite>\n- [src/f.py](file://src/f.py)\n</cite>\n"
              "## 目录\n1. [x](#x)\n```\n")
    real = "<cite>\n**本文引用**\n- [README.md](file://README.md)\n</cite>\n"
    toc = "## 目录\n1. [概述](#概述)\n"
    content = f"# 页\n\n{real}\n{toc}\n正文。\n\n{fenced}"
    out = strip_injected_layers(content)
    assert "<cite>" not in out.split("```markdown")[0], "真注入块未剥净"
    assert "## 目录\n1. [概述]" not in out, "真目录节未剥净"
    assert "- [src/f.py](file://src/f.py)" in out, "围栏示例 cite 被误剥"
    assert "## 目录\n1. [x](#x)" in out, "围栏示例目录被误剥"
    assert out.count("```") == 2, "围栏被劈开(未闭合代码块)"


def test_p13_rename_h1_fence_aware(tmp_path):
    """D22 改名改真 H1, 不再误改页首代码栅栏里的 '#' 注释。"""
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
    fenced_first = ("```bash\n# 注释\nmake install\n```\n\n"
                    f"# {target['title']}\n\n正文。\n\n"
                    "- [README.md:1](file://README.md#L1-L1)\n")
    ctx.db.save_page_content(pid, target["page_id"], fenced_first)
    ctx.db.reconcile_add(pid, "title_mismatch",
                         {"page_id": target["page_id"],
                          "proposed": str(target["title"]) + "改名"})
    assert cmd_reconcile(_ns(repo, root, all=True)) == config.EXIT_OK
    row = ctx.db.published_page(pid, target["page_id"])
    assert "# 注释\nmake install" in row["content"], "栅栏内注释被误改"
    assert f"# {target['title']}改名" in row["content"], "真 H1 未改"


def test_p14_off_by_one_skips_empty_file(tmp_path):
    """空(0 行)文件的引用钳位放行原文 —— 不再造 '#L0-L0' 非法范围。"""
    from pwiki_lib.gates import fix_off_by_one
    repo, root = _fixture(tmp_path)
    ctx = _ctx(repo, root)
    empty = os.path.join(ctx.repo, "src", "empty.py")
    os.makedirs(os.path.dirname(empty), exist_ok=True)
    with open(empty, "w", encoding="utf-8") as fh:
        fh.write("")
    raw = "[src/empty.py:1-1](file://src/empty.py#L1-L1)"
    out, n = fix_off_by_one(f"# 页\n\n{raw}\n", ctx.repo)
    assert n == 0 and raw in out, "空文件被钳成非法 #L0-L0"


def test_p19_parse_cite_block_ignores_fenced_example(tmp_path):
    """deep 的 cite 块集合取真块 —— 围栏示例块不再被拿来比对。"""
    from pwiki_lib import gates
    ctx = _ctx(*_fixture(tmp_path)[:2])
    fenced = ("```markdown\n<cite>\n- [a.py](file://a.py)\n</cite>\n```\n\n")
    real = "<cite>\n- [README.md](file://README.md)\n</cite>\n"
    content = (f"# 页\n\n{fenced}{real}"
               "- [README.md:1](file://README.md#L1-L1)\n")
    assert gates.parse_cite_block(content) == {"README.md"}
    errs, _ = gates.gate_item(content, {"kind": "page", "title": "页"},
                              ctx.repo, "deep", phase="lining")
    assert not any(e.startswith("schema/cite") for e in errs), errs


def test_p20_cited_files_lower_bound_warns_at_submit(tmp_path):
    """引用文件数低于下限: 提交时即告警(不再憋到 LINING 才硬拒)。"""
    from pwiki_lib import gates
    ctx = _ctx(*_fixture(tmp_path)[:2])
    paths = ["README.md", "Makefile", "services/user/m0.py",
             "services/user/m1.py", "services/user/m2.py"]
    lines = []
    for i in range(65):
        p = paths[i % len(paths)]
        lines.append(f"- [r{i}](file://{p}#L1-L1)")
    content = "# 页\n\n## 架构\n\n" + "\n".join(lines) + "\n"
    errs, warns = gates.gate_item(content, {"kind": "page", "title": "页"},
                                  ctx.repo, "deep", phase="submit")
    assert not any(e.startswith("citation/") for e in errs), errs
    assert any("size/cited-files" in w and "≥6" in w for w in warns), warns


def test_p28_token_lost_orphan_audited(tmp_path, monkeypatch):
    """终写时令牌被回收的孤儿稿: 孤儿落盘 + run_log 补审计行。"""
    from pwiki_lib import engine
    from pwiki_lib.db import WikiDB
    repo, root = _fixture(tmp_path)
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    ctx = _ctx(repo, root)
    run = ctx.db.active_run(ctx.partition()["id"])
    job = ctx.db.claim_jobs(run["id"], count=9)[0]
    # 让前置令牌检查放行, 终写事务里的真检查如实翻车 → verdict=token_lost
    monkeypatch.setattr(WikiDB, "match_claim",
                        lambda self, *a, **k: True)
    with pytest.raises(SystemExit) as ei:
        engine.cmd_submit(_ns(repo, root, page_id=job["page_id"],
                              file=_write_job_file(root, job),
                              summary="", key_topics="", report=None,
                              lenient=False, force=False, claim="stale-tok"))
    assert ei.value.code == config.EXIT_PRECOND
    rows = ctx.db.conn.execute(
        "SELECT detail FROM run_log WHERE run_id=? AND event='orphan_submit'",
        (run["id"],)).fetchall()
    assert rows and "租约被回收" in rows[-1]["detail"], \
        "token_lost 孤儿从 run_log 审计通道消失"
    assert ctx.db.job(run["id"], job["page_id"])["state"] != "completed"


# =========================================================================== #
# 批次④: 解析面与输入健壮性(P16/P18/P21/P22/P26/P27/P29/P30/P33/P36/P39/
# P40/P41/P42/P43/P46/P47)
# =========================================================================== #

def test_p16_parse_pyproject_pep621(tmp_path):
    """[project] 只认 PEP 621 dependencies 数组 —— 标量键不再是依赖。"""
    from pwiki_lib.scan import parse_pyproject
    f = tmp_path / "pyproject.toml"
    f.write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n'
        'requires-python = ">=3.10"\n'
        'dependencies = [\n  "requests>=2.28",\n  "pydantic>=2",\n]\n\n'
        '[tool.poetry.dependencies]\npython = "^3.10"\nhttpx = "^0.27"\n',
        encoding="utf-8")
    out = parse_pyproject(str(f))
    assert out["name"] == "demo"
    assert out["dependencies"] == ["requests>=2.28", "pydantic>=2",
                                   "python", "httpx"], out["dependencies"]


def test_p42_ignore_dirs_cover_default_ignores():
    """inventory 的 walk 忽略集以 config.DEFAULT_IGNORES 为基(Pods 等
    不再进得了 inventory 却永不标 stale)。"""
    from pwiki_lib import config, scan
    assert config.DEFAULT_IGNORES <= scan.IGNORE_DIRS, \
        config.DEFAULT_IGNORES - scan.IGNORE_DIRS


def test_p43_setup_signals_reads_readme_excerpt():
    """src-layout 库(pyproject 无入口)靠 README 摘录判快速开始页。"""
    from pwiki_lib.planner import _setup_signals
    inv = {"manifests": [{"path": "pyproject.toml"}], "entry_points": [],
           "readme": {"path": "README.md", "excerpt": "# Demo\n\nGetting Started here"}}
    assert _setup_signals(inv) is True


def test_p27_review_keeps_prior_mutations(tmp_path):
    """`plan --sheets standard --review` 同调用: sheets 变更真的落盘。"""
    from pwiki_lib import planner
    repo, root = _fixture(tmp_path)
    ctx = _ctx(repo, root)
    n_before = len(planner.plan_pages(planner.load_plan(ctx)))
    assert _cli(["plan", "--sheets", "standard", "--review",
                 "--repo", repo.path, "--root", root]) == config.EXIT_OK
    n_after = len(planner.plan_pages(planner.load_plan(ctx)))
    assert n_after > n_before, "--review 把同调用的 --sheets 变也丢回内存"


def test_p29_platform_rules_both_shapes(tmp_path, capsys):
    """title_maps.platform_rules: dict/列表两形态都收, 坏形状警告+忽略。"""
    from pwiki_lib import themes
    repo, root = _fixture(tmp_path)
    ctx = _ctx(repo, root)
    ov = os.path.join(ctx.repo, "pwiki.yaml")
    with open(ov, "w", encoding="utf-8") as fh:
        fh.write("title_maps:\n  platform_rules:\n    ipc: IPC 层\n")
    m = themes.load_overrides(ctx)["title_maps"]
    assert m["platform_rules"] == [["ipc", "IPC 层"]], m
    with open(ov, "w", encoding="utf-8") as fh:
        fh.write("title_maps:\n  platform_rules:\n    - [web, Web 桥]\n")
    assert themes.load_overrides(ctx)["title_maps"]["platform_rules"] == \
        [["web", "Web 桥"]]
    with open(ov, "w", encoding="utf-8") as fh:
        fh.write("title_maps:\n  platform_rules: oops\n")
    capsys.readouterr()
    assert "platform_rules" not in themes.load_overrides(ctx)["title_maps"]
    assert "platform_rules" in capsys.readouterr().err


def test_p30_qoder_lint_nested_shapes(tmp_path, capsys):
    """嵌套坏形状按门禁错报(退出码 2), 不再裸 AttributeError。"""
    repo, root = _fixture(tmp_path)
    bad = tmp_path / "bad.yaml"
    bad.write_text("repowiki:\n  - a\n", encoding="utf-8")
    capsys.readouterr()
    assert _cli(["qoder-plan", "--lint", str(bad), "--repo", repo.path,
                 "--root", root]) == config.EXIT_GATE
    assert "必须是映射" in capsys.readouterr().out
    bad.write_text("documents: ok\nrepowiki:\n  documents: notalist\n",
                   encoding="utf-8")
    capsys.readouterr()
    assert _cli(["qoder-plan", "--lint", str(bad), "--repo", repo.path,
                 "--root", root]) == config.EXIT_GATE
    assert "documents 必须是列表" in capsys.readouterr().out


def test_p33_layers_non_dict_article(tmp_path):
    """模型输出的字符串列表条目按问题记账, 不再裸 AttributeError。"""
    from pwiki_lib import layers
    ctx = _ctx(*_fixture(tmp_path)[:2])
    added, problems = layers.apply_layer_result(
        ctx, {"limits": {}}, {"profile": "standard", "articles": []},
        '<result>{"articles": ["overview"]}</result>')
    assert added == []
    assert any("必须是映射" in p for p in problems), problems


def test_p36_emit_truncates_goal(tmp_path):
    """超限 goal 按 _trunc_bytes 截断发出 —— Qoder 加载器不再中切 CJK。"""
    from pwiki_lib import planner
    from pwiki_lib.qoderplan import CAP_GOAL_BYTES
    from pwiki_lib.yamlite import yaml_load
    repo, root = _fixture(tmp_path)
    ctx = _ctx(repo, root)
    plan = planner.load_plan(ctx)
    target = next(p for p in plan["pages"] if p.get("kind") == "page")
    target["objective"] = "目标。" * 6000      # ~36KB, 远超 10K 帽
    planner.save_plan(ctx, plan)
    out = os.path.join(root, "qoder-out.yaml")
    assert _cli(["qoder-plan", "--emit", out, "--repo", repo.path,
                 "--root", root]) == config.EXIT_OK
    doc = yaml_load(open(out, encoding="utf-8").read())
    goals = {str(d["title"]): str(d["goal"]) for d in doc["repowiki"]["documents"]}
    g = goals[str(target["title"])]
    assert len(g.encode("utf-8")) <= CAP_GOAL_BYTES, "goal 超限仍原样发出"


def test_p39_export_force_db_to_file_rejected(tmp_path):
    """db_to_file 不吃 --force —— 解析后静默丢弃的死开关改为显式拒绝。"""
    repo, root = _fixture(tmp_path)
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    _write_all_jobs(repo, root)
    assert _cli(["finish", "--repo", repo.path, "--root", root,
                 "--yes"]) == config.EXIT_OK
    with pytest.raises(SystemExit) as ei:
        _cli(["export", "--direction", "db_to_file", "--force",
              "--repo", repo.path, "--root", root])
    assert ei.value.code == config.EXIT_PRECOND


def test_p40_yamlite_colon_keys(tmp_path):
    """未加引号的冒号键按 YAML 语义成键, 不再静默重塑。"""
    from pwiki_lib.yamlite import yaml_dump, yaml_load
    assert yaml_load("go:generate: true") == {"go:generate": True}
    assert yaml_load("a:b: v") == {"a:b": "v"}
    assert yaml_load("url: http://x") == {"url": "http://x"}
    # 往返: 含冒号的键发射侧加引号, 精确回读
    data = {"go:generate": True, "url": "http://x", "plain": 1}
    assert yaml_load(yaml_dump(data)) == data


def test_p46_yamlite_inf_roundtrip(tmp_path):
    """非有限 float 与 'inf' 字符串各自精确往返。"""
    from pwiki_lib.yamlite import yaml_dump, yaml_load
    assert yaml_load(yaml_dump({"x": float("inf")}))["x"] == float("inf")
    assert yaml_load(yaml_dump({"y": float("nan")}))["y"] != \
        yaml_load(yaml_dump({"y": float("nan")}))["y"]   # nan != nan
    assert yaml_load(yaml_dump({"s": "inf"}))["s"] == "inf"


def test_p26_delete_all_clears_meta(tmp_path):
    """delete --all 连 meta/ 一起清 —— Qoder 导入面不再读幽灵条目。"""
    repo, root = _fixture(tmp_path)
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    _write_all_jobs(repo, root)
    assert _cli(["finish", "--repo", repo.path, "--root", root,
                 "--yes"]) == config.EXIT_OK
    meta = os.path.join(root, "zh", "meta")
    assert os.path.isfile(os.path.join(meta, "repowiki-metadata.json"))
    assert _cli(["delete", "--all", "--repo", repo.path,
                 "--root", root]) == config.EXIT_OK
    assert not os.path.isdir(meta), "meta/ 索引残留幽灵条目"


def test_p47_search_top_zero_returns_empty(tmp_path, capsys):
    """--top 0 尊重空集请求, 不再强塞一条结果。"""
    from pwiki_lib.search import cmd_search
    repo, root = _fixture(tmp_path)
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    _write_all_jobs(repo, root)
    assert _cli(["finish", "--repo", repo.path, "--root", root,
                 "--yes"]) == config.EXIT_OK
    capsys.readouterr()
    assert cmd_search(_ns(repo, root, query="服务", top=0,
                          json=True)) == config.EXIT_OK
    out_lines = capsys.readouterr().out.splitlines()
    start = next(i for i, l in enumerate(out_lines) if l.startswith("{"))
    payload = json.loads("\n".join(out_lines[start:]))
    assert payload["results"] == [], payload


def test_p21_p22_render_plan_md_robust(tmp_path):
    """骨架页说明整句成行(P21); 空 ordinal 排序容错(P22)。"""
    from pwiki_lib import themes
    repo, root = _fixture(tmp_path)
    ctx = _ctx(repo, root)
    plan = {"pages": [{"id": "p1", "title": "页一", "parent": "",
                       "ordinal": None, "kind": "page"},
                      {"id": "p2", "title": "页二", "parent": "",
                       "ordinal": 0, "kind": "page"}],
            "cards": []}
    work = os.path.join(root, "work")
    os.makedirs(work, exist_ok=True)
    with open(os.path.join(work, "themes-report.json"), "w",
              encoding="utf-8") as fh:
        json.dump({"check": {"skeleton_scope_pages": ["svc"]}}, fh)
    out = themes.render_plan_md(ctx, plan)          # P22: 不再 TypeError
    text = open(out, encoding="utf-8").read()
    assert "这些页面覆盖占位目录" in text
    assert "- svc" in text
    assert not [ln for ln in text.splitlines() if ln.strip() and len(ln) == 1], \
        "plan.md 仍有单字符行(P21)"


def test_p18_plan_cards_dedup_ids_and_titles(tmp_path, monkeypatch):
    """slug 撞车('@azure/storage' vs 'azure-storage')与 DEP_DISPLAY 撞标题
    都在 _plan_cards 内消解, 不再阻塞 begin / 打死 plan --themes。"""
    from collections import Counter
    from pwiki_lib import themes
    from pwiki_lib.corpus import Corpus
    repo, root = _fixture(tmp_path)
    ctx = _ctx(repo, root)
    inv = {"manifests": [{"path": "README.md",
                          "dependencies": {"tauri": "1",
                                           "@tauri-apps/api": "2",
                                           "@acme/util": "3",
                                           "acme-util": "4"}}]}
    monkeypatch.setattr(themes, "collect_dependencies",
                        lambda inv_: Counter({"tauri": 1, "@tauri-apps/api": 1,
                                              "@acme/util": 1,
                                              "acme-util": 1}))
    monkeypatch.setattr(themes, "DEP_SCORES", {})
    monkeypatch.setattr(themes, "ARCH_SHAPING_DEPS",
                        {"tauri", "@tauri-apps/api", "@acme/util",
                         "acme-util"})
    monkeypatch.setattr(themes, "DEP_DISPLAY",
                        {"tauri": "Tauri", "@tauri-apps/api": "Tauri"})
    corpus = Corpus(ctx.repo, [{"path": "README.md", "lang": "markdown",
                                "lines": 3}], {}, set())
    cards = themes._plan_cards(ctx, inv, corpus, [], "standard")
    ids = [c["id"] for c in cards]
    titles = [c["title"] for c in cards]
    assert len(ids) == len(set(ids)), f"重复卡片 id: {ids}"
    assert len(titles) == len(set(titles)), f"撞标题: {titles}"
    assert {"card-acme-util", "card-acme-util-2"} <= set(ids)
    assert {"Tauri", "Tauri(2)"} <= set(titles)


def test_p41_feature_leaf_excludes_test_paths(tmp_path):
    """feature 叶过滤与全库测试谓词同源(foo_test.go/__tests__ 不再漏进)。"""
    from pwiki_lib.corpus import match_any
    from pwiki_lib.themes import P_TEST
    for p in ("src/api/foo_test.go", "core/foo_test.rs",
              "src/tests/handler.ts", "src/pages/__tests__/Home.tsx"):
        assert match_any(p.lower(), P_TEST), p


# =========================================================================== #
# 批次⑤: 契约杂项(P10/P11/P35/P38/P44/P45)
# =========================================================================== #

def test_p35_init_merges_manual_config(tmp_path):
    """重跑 init 合并保留手工 config.json 键, 只应用显式选项。"""
    import json as _json
    repo, root = _fixture(tmp_path)
    cfg_path = os.path.join(root, "config.json")
    cfg = _json.load(open(cfg_path, encoding="utf-8"))
    cfg["lease_minutes"] = 99
    with open(cfg_path, "w", encoding="utf-8") as fh:
        _json.dump(cfg, fh, ensure_ascii=False, indent=2)
    # 无选项重跑: 手工键全保留
    assert _cli(["init", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    cfg2 = _json.load(open(cfg_path, encoding="utf-8"))
    assert cfg2["lease_minutes"] == 99, "手工 lease_minutes 被默认值覆盖"
    assert cfg2["style"] == "concise"
    # 显式 --style deep: 只改 style, 手工键仍在
    assert _cli(["init", "--style", "deep", "--repo", repo.path,
                 "--root", root]) == config.EXIT_OK
    cfg3 = _json.load(open(cfg_path, encoding="utf-8"))
    assert cfg3["style"] == "deep" and cfg3["lease_minutes"] == 99


def test_p38_relative_root_anchored_to_repo(tmp_path):
    """显式相对 --root 与默认根同基准(repo), 不再按进程 CWD 分裂。"""
    from pwiki_lib.config import Ctx
    repo, root = _fixture(tmp_path)
    ctx_rel = Ctx(types.SimpleNamespace(repo=repo.path, root=".pwiki"))
    ctx_def = Ctx(types.SimpleNamespace(repo=repo.path))
    assert ctx_rel.root == ctx_def.root, \
        "键入的 --root .pwiki 与省略指向了不同的树"
    assert ctx_rel.root.startswith(repo.path)


def test_p44_readonly_commands_do_not_materialize(tmp_path):
    """search 在未初始化仓库上不再顺手创建 wiki.db。"""
    import subprocess
    fresh = tmp_path / "fresh"
    (fresh / "src").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=str(fresh), check=True)
    from pwiki_lib.search import cmd_search
    with pytest.raises(SystemExit) as ei:
        cmd_search(types.SimpleNamespace(
            repo=str(fresh), root=str(tmp_path / "nope-wiki"), locale="zh",
            query="x", top=5, json=False))
    assert ei.value.code == config.EXIT_PRECOND
    assert not any(tmp_path.rglob("wiki.db")), "只读命令物化了工作区"


def test_p10_card_scope_changes_mark_stale(tmp_path):
    """卡片声明 scope+source_files 时, scope 文件变更也把卡片标 stale。"""
    from pwiki_lib import planner
    repo, root = _fixture(tmp_path)
    ctx = _ctx(repo, root)
    plan = planner.load_plan(ctx)
    card = plan["cards"][0]
    card["scope"] = ["services/user/"]
    card["source_files"] = ["Makefile"]
    planner.save_plan(ctx, plan)
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    _write_all_jobs(repo, root)
    assert _cli(["finish", "--repo", repo.path, "--root", root,
                 "--yes"]) == config.EXIT_OK
    repo.write("services/user/m0.py", "# changed\n" + "Y = 2\n" * 24)
    repo.commit("touch card scope file")
    assert _cli(["update", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    ctx2 = _ctx(repo, root)
    run = ctx2.db.active_run(ctx2.partition()["id"])
    assert run is not None
    queued = {j["page_id"] for j in ctx2.db.jobs(run["id"])}
    assert str(card["id"]) in queued, \
        f"卡片 scope 变更未标 stale(队列: {queued})"


def test_p11_update_does_not_revive_skipped(tmp_path):
    """显式 skip 过的页面不因 update 的 new_item 复活。"""
    repo, root = _fixture(tmp_path)
    assert _cli(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    ctx = _ctx(repo, root)
    run = ctx.db.active_run(ctx.partition()["id"])
    victim = next(j for j in ctx.db.jobs(run["id"])
                  if j["kind"] == "page" and j["state"] == "pending")
    assert _cli(["skip", victim["page_id"], "--reason", "不要这页",
                 "--repo", repo.path, "--root", root]) == config.EXIT_OK
    _write_all_jobs(repo, root)
    assert _cli(["finish", "--repo", repo.path, "--root", root,
                 "--yes"]) == config.EXIT_OK
    repo.write("Makefile", "build:\n\techo changed\n")
    repo.commit("touch tracked file")
    assert _cli(["update", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    ctx2 = _ctx(repo, root)
    run2 = ctx2.db.active_run(ctx2.partition()["id"])
    if run2 is not None:
        queued = {j["page_id"] for j in ctx2.db.jobs(run2["id"])}
        assert victim["page_id"] not in queued, \
            f"skip 决定被 update 翻案(队列: {queued})"


def test_p45_evidence_min_drop_recorded(tmp_path, monkeypatch):
    """scaled_min 证据门切掉的子项进 theme_declined(人审可见)。"""
    from pwiki_lib import themes
    from pwiki_lib.themes import Builder

    class _NoEvCorpus:
        by_path = {}
        modules = {}

        def collect(self, spec, pool=None):
            return []

        def grep(self, files, needles, limit=20):
            return []

    builder = Builder(_NoEvCorpus(), {"source_limit": 4, "pack_scale": 1.0}, "zh",
                      {"title_maps": {}, "title_aliases": {}}, style="deep")
    pack = {"key": "t", "title": "T", "kind": "cross", "patterns": ["src/"],
            "always": True, "children": [{"key": "c1", "title": "子项",
                                          "patterns": ["src/"]}]}
    before = len(builder.theme_declined)
    themes.instantiate_pack(builder, _NoEvCorpus(), pack,
                            {"source_limit": 4, "pack_scale": 1.0}, set())
    declined = builder.theme_declined[before:]
    assert any("evidence below minimum" in str(d.get("reason"))
               for d in declined), declined
