"""端到端: 双风格全生命周期、发布树、检索、增量更新、崩溃自愈。"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from gitrepo import GitRepo

from pwiki_lib import config


def _fixture(tmp_path, style="concise"):
    repo = GitRepo(tmp_path / "repo")
    for svc in ("user", "order"):
        for i in range(4):
            repo.write(f"services/{svc}/m{i}.py",
                       f"# 模块 {i}\n" + "X = 1\n" * 24)
    repo.write("packages/shared/util.py", "def f():\n    pass\n" * 8)
    repo.write("README.md", "# 全周期仓库\n\n两个服务与一个共享包。\n")
    repo.write("Makefile", "build:\n\techo b\n")
    repo.commit("init")
    root = str(tmp_path / "wiki")
    from pwiki_lib import cli
    for argv in (["init", "--style", style, "--repo", repo.path,
                  "--root", root],           # init 最先: plan 创建时盖章风格
                 ["scan", "--repo", repo.path, "--root", root],
                 ["modules", "--repo", repo.path, "--root", root],
                 ["plan", "--seed", "--repo", repo.path, "--root", root],
                 ["plan", "--sheets", "lite", "--repo", repo.path,
                  "--root", root],
                 ["digest", "--repo", repo.path, "--root", root]):
        assert cli.main(argv) in (config.EXIT_OK,), argv
    return repo, root


def _write_all_jobs(repo, root, style="concise"):
    """驱动整个 DOCUMENT 循环: 逐个认领并交合格稿。"""
    import types
    from pwiki_lib import cli, engine
    from pwiki_lib.config import Ctx
    ctx = Ctx(types.SimpleNamespace(repo=repo.path, root=root,
                                    locale="zh")).load()
    run = ctx.db.active_run(ctx.partition()["id"])
    assert run is not None
    while True:
        claimed = ctx.db.claim_jobs(run["id"], count=1)
        if not claimed:
            break
        j = claimed[0]
        if j["kind"] == "card":
            content = (f"---\nkind: {j['card_kind']}\nname: {j['title']}\n---\n\n"
                       "## 1. 总体方案\n\n事实。\n")
        elif j["kind"] == "module_sheet":
            if style == "concise":
                content = f"# {j['title']}\n\n模块负责 X；位于 Y；对外提供 Z。\n"
            else:
                content = (f"# {j['title']}\n\n模块负责 X。\n\n**章节来源**\n"
                           "- [README.md:1](file://README.md#L1)\n")
        else:
            sec = "\n**章节来源**\n- [README.md:1](file://README.md#L1)\n"
            content = (f"# {j['title']}\n\n## 概述\n\n页面摘要。\n"
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
               "lenient": False, "force": False,
               "claim": j["claim_token"]})
        assert engine.cmd_submit(args) == config.EXIT_OK, j["page_id"]


def test_full_lifecycle_concise(tmp_path, capsys):
    """concise 全周期: init→scan→modules→plan→digest→begin→循环→finish→search。"""
    from pwiki_lib import cli
    repo, root = _fixture(tmp_path, style="concise")
    assert cli.main(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    _write_all_jobs(repo, root, style="concise")
    code = cli.main(["finish", "--repo", repo.path, "--root", root, "--yes"])
    assert code == config.EXIT_OK, code
    # 发布树存在
    content_dir = os.path.join(root, "zh", "content")
    assert os.path.isdir(content_dir)
    mds = [p for p in _walk_md(content_dir)]
    assert mds
    knowledge = os.path.join(root, "knowledge", "zh")
    assert os.path.isdir(knowledge)
    # sheet 落盘 + _module.yaml(D5)
    assert any(os.path.basename(p) == "_module.yaml"
               for p in _walk_all(knowledge))
    # 元数据(Qoder 形)
    meta = json.load(open(os.path.join(root, "zh", "meta",
                                       "repowiki-metadata.json"),
                          encoding="utf-8"))
    assert "wiki_catalogs" in meta and "wiki_items" in meta
    assert meta["wiki_repo"]["last_commit_id"]
    assert os.path.isfile(os.path.join(root, "zh", "meta",
                                       "generation-state.json"))
    # 状态 COMPLETED
    import types
    from pwiki_lib.config import Ctx
    ctx = Ctx(types.SimpleNamespace(repo=repo.path, root=root,
                                    locale="zh")).load()
    run = ctx.db.last_run(ctx.partition()["id"])
    assert run["state"] == "COMPLETED", run["state"]
    # 检索命中(BM25): 结果里要真的看到期望页面(标题/路径), 不止退出码
    capsys.readouterr()   # 清掉此前命令的 stdout, 只看 search 的
    code = cli.main(["search", "服务", "--repo", repo.path,
                     "--root", root, "-n", "3"])
    assert code == config.EXIT_OK
    out = capsys.readouterr().out
    assert out.startswith("top "), out
    assert "后端服务" in out and "(page, services)" in out, out
    assert os.path.join(root, "zh", "content", "后端服务.md") in out, out
    # 零命中输出形状: 'no match for …' + exit 0(锁死, 不许悄悄变非零)
    code = cli.main(["search", "zzqqxx", "--repo", repo.path, "--root", root])
    assert code == config.EXIT_OK
    assert "no match for 'zzqqxx'" in capsys.readouterr().out


def test_full_lifecycle_deep(tmp_path):
    """deep 全周期(LINING 硬门禁按 deep 预设)。"""
    from pwiki_lib import cli
    repo, root = _fixture(tmp_path, style="deep")
    assert cli.main(["begin", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    _write_all_jobs(repo, root, style="deep")
    code = cli.main(["finish", "--force", "--repo", repo.path,
                     "--root", root, "--yes"])
    # deep 的行数/引用分档在测试短稿上会失败 —— --force 全放发布为
    # PARTLY_COMPLETED 并记录 needs_review; 不隐藏。(r4 #43: --lenient
    # 不再是 --force 的别名 —— 只放宽 H1/无引用两类。)
    assert code == config.EXIT_OK
    import types
    from pwiki_lib.config import Ctx
    ctx = Ctx(types.SimpleNamespace(repo=repo.path, root=root,
                                    locale="zh")).load()
    run = ctx.db.last_run(ctx.partition()["id"])
    assert run["state"] in ("COMPLETED", "PARTLY_COMPLETED")


def test_incremental_update_after_edit(tmp_path):
    """改一个源文件 → update 只排队引用它的项。"""
    from pwiki_lib import cli
    import types
    from pwiki_lib.config import Ctx
    repo, root = _fixture(tmp_path, style="concise")
    cli.main(["begin", "--repo", repo.path, "--root", root])
    _write_all_jobs(repo, root, style="concise")
    assert cli.main(["finish", "--repo", repo.path, "--root", root,
                     "--yes"]) == config.EXIT_OK
    ctx = Ctx(types.SimpleNamespace(repo=repo.path, root=root,
                                    locale="zh")).load()
    pid = ctx.partition()["id"]
    before_hashes = set(ctx.db.file_hashes(pid))
    assert before_hashes, "快照跟踪集非空"
    # 改 README(几乎每个页面都引用它 → 多页 stale); 再改一个只被少数页
    # 引用的文件
    repo.write("services/user/m0.py", "# 改动\nX = 2\n" + "Y = 3\n" * 24)
    repo.write("README.md", "# 全周期仓库\n\n更新后的描述。\n")
    repo.commit("edit")
    code = cli.main(["update", "--repo", repo.path, "--root", root])
    assert code == config.EXIT_OK
    run2 = ctx.db.active_run(pid)
    assert run2 is not None and run2["generation_type"] == "INCREMENT"
    queued = {j["page_id"] for j in ctx.db.jobs(run2["id"])}
    assert queued, "改动后有 stale 项"
    # noop 路径: 不改任何东西再 update 应报 noop(run 先取消)
    ctx.db.update_run(run2["id"], state="CANCELLED",
                      finished_at=config.now_iso())
    ctx.db.release_all_claims(run2["id"])
    assert cli.main(["update", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK   # noop 也是 0


def test_write_edits_and_refreshes_baseline(tmp_path):
    """write 直接修正已发布页 + 刷新基线(修 qwiki 缺陷 g)。"""
    from pwiki_lib import cli
    import types
    from pwiki_lib.config import Ctx
    repo, root = _fixture(tmp_path, style="concise")
    cli.main(["begin", "--repo", repo.path, "--root", root])
    _write_all_jobs(repo, root, style="concise")
    cli.main(["finish", "--repo", repo.path, "--root", root, "--yes"])
    ctx = Ctx(types.SimpleNamespace(repo=repo.path, root=root,
                                    locale="zh")).load()
    pid = ctx.partition()["id"]
    pages = ctx.db.published_pages(pid)
    # concise 速查表无节下限, 适合做直接修正的对象
    target = next(p for p in pages if p["kind"] == "module_sheet")
    fixed = f"# {target['title']}\n\n修正后的模块速查内容。\n"
    fixed_file = str(tmp_path / "fixed.md")
    with open(fixed_file, "w", encoding="utf-8") as fh:
        fh.write(fixed)
    code = cli.main(["write", target["page_id"], "--file", fixed_file,
                     "--repo", repo.path, "--root", root])
    assert code == config.EXIT_OK
    assert ctx.db.published_page(pid, target["page_id"])["content"] == fixed
    # 手工改文件后 update 应 noop(基线已被 write 刷新)
    assert cli.main(["update", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK


def test_export_db_to_file_heals(tmp_path):
    """删掉发布树的一半 → export db_to_file 完整恢复。"""
    from pwiki_lib import cli
    repo, root = _fixture(tmp_path, style="concise")
    cli.main(["begin", "--repo", repo.path, "--root", root])
    _write_all_jobs(repo, root, style="concise")
    cli.main(["finish", "--repo", repo.path, "--root", root, "--yes"])
    base = os.path.join(root, "zh", "content")
    before = sorted(_walk_md(base))
    assert before
    for p in before[: max(1, len(before) // 2)]:
        os.unlink(os.path.join(base, p))
    assert cli.main(["export", "--direction", "db_to_file",
                     "--repo", repo.path, "--root", root]) == config.EXIT_OK
    after = sorted(_walk_md(base))
    assert after == before


def test_parallel_writers_e2e(tmp_path):
    """两个"writer"交替认领/提交, 互不干扰(线程级)。"""
    import threading
    import types
    from pwiki_lib import cli
    from pwiki_lib.config import Ctx
    from pwiki_lib import engine
    repo, root = _fixture(tmp_path, style="concise")
    cli.main(["begin", "--repo", repo.path, "--root", root])
    ctx = Ctx(types.SimpleNamespace(repo=repo.path, root=root,
                                    locale="zh")).load()
    run = ctx.db.active_run(ctx.partition()["id"])
    errors = []

    def writer():
        try:
            my_ctx = Ctx(types.SimpleNamespace(repo=repo.path, root=root,
                                               locale="zh")).load()
            my_run = my_ctx.db.active_run(my_ctx.partition()["id"])
            while True:
                claimed = my_ctx.db.claim_jobs(my_run["id"], count=1)
                if not claimed:
                    return
                j = claimed[0]
                sec = "\n**章节来源**\n- [README.md:1](file://README.md#L1)\n"
                if j["kind"] == "card":
                    content = (f"---\nkind: {j['card_kind']}\nname: "
                               f"{j['title']}\n---\n\n## 1. 总体方案\n\nx\n")
                elif j["kind"] == "module_sheet":
                    content = f"# {j['title']}\n\n模块负责 X。\n"
                else:
                    content = (f"# {j['title']}\n\n## 概述\n\ns\n"
                               f"## 架构\n\nx\n{sec}## 组件\n\nx\n{sec}"
                               f"## 数据流\n\nx\n{sec}## 配置\n\nx\n{sec}")
                f = os.path.join(root, "work",
                                 f"{j['page_id']}-{threading.get_ident()}.md")
                with open(f, "w", encoding="utf-8") as fh:
                    fh.write(content)
                args = types.SimpleNamespace(
                    **{"repo": repo.path, "root": root, "locale": "zh",
                       "page_id": j["page_id"], "file": f, "summary": "",
                       "key_topics": "", "report": None, "lenient": False,
                       "force": False, "claim": j["claim_token"]})
                code = engine.cmd_submit(args)
                if code != config.EXIT_OK:
                    errors.append((j["page_id"], code))
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=writer) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    counts = ctx.db.pending_counts(run["id"])
    assert counts["pending"] == 0 and counts["processing"] == 0
    assert counts["completed"] == sum(counts.values())


def _walk_md(base):
    for dirpath, _dirs, files in os.walk(base):
        for fn in files:
            if fn.endswith(".md"):
                yield os.path.relpath(os.path.join(dirpath, fn), base)


def _walk_all(base):
    for dirpath, _dirs, files in os.walk(base):
        for fn in files:
            yield os.path.join(dirpath, fn)
