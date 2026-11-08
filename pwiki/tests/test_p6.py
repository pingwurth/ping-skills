"""P6: reconcile(D7/D22 落为代码)与 qoder-plan(wiki-plan-gen 契约)。"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from gitrepo import GitRepo

from pwiki_lib import config, qoderplan


def _fixture(tmp_path):
    repo = GitRepo(tmp_path / "repo")
    for svc in ("user", "order"):
        for i in range(4):
            repo.write(f"services/{svc}/m{i}.py", "X = 1\n" * 25)
    repo.write("README.md", "# 和解仓库\n")
    repo.write("Makefile", "build:\n\techo b\n")
    repo.commit("init")
    return repo, str(tmp_path / "wiki")


def _ready_wiki(repo, root):
    from pwiki_lib import cli
    from tests.test_e2e import _write_all_jobs
    for argv in (["init", "--style", "concise", "--repo", repo.path,
                  "--root", root],
                 ["scan", "--repo", repo.path, "--root", root],
                 ["modules", "--repo", repo.path, "--root", root],
                 ["plan", "--seed", "--repo", repo.path, "--root", root],
                 ["digest", "--repo", repo.path, "--root", root],
                 ["begin", "--repo", repo.path, "--root", root]):
        assert cli.main(argv) == config.EXIT_OK, argv
    _write_all_jobs(repo, root, style="concise")
    assert cli.main(["finish", "--repo", repo.path, "--root", root,
                     "--yes"]) == config.EXIT_OK


def test_d22_rename_zero_llm(tmp_path):
    """改名 = 文件移动 + plan 更新 + H1 替换; 引用不动。"""
    import types
    from pwiki_lib import cli
    from pwiki_lib.config import Ctx
    repo, root = _fixture(tmp_path)
    _ready_wiki(repo, root)
    ctx = Ctx(types.SimpleNamespace(repo=repo.path, root=root,
                                    locale="zh")).load()
    pid = ctx.partition()["id"]
    pages = [p for p in ctx.db.published_pages(pid) if p["kind"] == "page"]
    target = next((p for p in pages if p["parent_id"]), pages[0])
    old_path = os.path.join(root, target["path"])
    assert os.path.isfile(old_path)
    old_content = open(old_path, encoding="utf-8").read()

    rid = ctx.db.reconcile_add(pid, "title_mismatch",
                               {"page_id": target["page_id"],
                                "old": target["title"],
                                "proposed": "用户服务全解析",
                                "evidence": "grep services/user"})
    assert cli.main(["reconcile", "--apply", str(rid), "--repo", repo.path,
                     "--root", root]) == config.EXIT_OK
    # 旧文件没了, 新文件在, H1 已换
    assert not os.path.isfile(old_path)
    from pwiki_lib.planner import load_plan
    plan = load_plan(ctx)
    page = next(p for p in plan["pages"]
                if p["id"] == target["page_id"])
    assert page["title"] == "用户服务全解析"
    new_full = os.path.join(root, page["path"])
    assert os.path.isfile(new_full)
    body = open(new_full, encoding="utf-8").read()
    assert body.startswith("# 用户服务全解析")
    assert "file://README.md" in body          # 引用保留
    # 已 applied
    assert ctx.db.reconcile_pending(pid) == []


def test_d7_card_conflict_queues_regenerate(tmp_path):
    import types
    from pwiki_lib import cli
    from pwiki_lib.config import Ctx
    repo, root = _fixture(tmp_path)
    _ready_wiki(repo, root)
    ctx = Ctx(types.SimpleNamespace(repo=repo.path, root=root,
                                    locale="zh")).load()
    pid = ctx.partition()["id"]
    cards = [p for p in ctx.db.published_pages(pid) if p["kind"] == "card"]
    if not cards:
        # 该 fixture 可能没有卡; 造一条计划卡再测
        from pwiki_lib.planner import load_plan, save_plan
        plan = load_plan(ctx)
        plan["cards"].append({"id": "card-test", "kind": "error_handling",
                              "title": "错误处理体系", "scope": ["**"],
                              "prompt": "总结错误处理。",
                              "source_files": ["README.md"]})
        save_plan(ctx, plan)
        cards = [{"page_id": "card-test", "title": "错误处理体系"}]
    card = cards[0]
    rid = ctx.db.reconcile_add(pid, "card_conflict",
                               {"card": card["title"],
                                "card_line": "统一重试三次",
                                "evidence": "services/user/m0.py:1-5 无重试"})
    assert cli.main(["reconcile", "--apply", str(rid), "--repo", repo.path,
                     "--root", root]) == config.EXIT_OK
    run = ctx.db.active_run(pid)
    assert run is not None and run["kind"] == "regenerate"
    job_ids = {j["page_id"] for j in ctx.db.jobs(run["id"])}
    assert card["page_id"] in job_ids


def test_reconcile_dry_run_and_listing(tmp_path):
    import types
    from pwiki_lib import cli
    from pwiki_lib.config import Ctx
    repo, root = _fixture(tmp_path)
    _ready_wiki(repo, root)
    ctx = Ctx(types.SimpleNamespace(repo=repo.path, root=root,
                                    locale="zh")).load()
    pid = ctx.partition()["id"]
    ctx.db.reconcile_add(pid, "title_mismatch",
                         {"page_id": "x", "old": "旧", "proposed": "新",
                          "evidence": "e"})
    # 列表模式
    assert cli.main(["reconcile", "--repo", repo.path, "--root", root]) == \
        config.EXIT_OK
    # dry-run 不落盘
    assert cli.main(["reconcile", "--all", "--dry-run", "--repo", repo.path,
                     "--root", root]) == config.EXIT_OK
    assert ctx.db.reconcile_pending(pid)          # 仍在 pending


# --------------------------------------------------------------------------- #
# qoder-plan(wiki-plan-gen 契约)
# --------------------------------------------------------------------------- #

def test_qoder_plan_emit_and_caps(tmp_path):
    import types
    from pwiki_lib import cli
    from pwiki_lib.config import Ctx
    repo, root = _fixture(tmp_path)
    _ready_wiki(repo, root)
    out = os.path.join(repo.path, ".qoder", "repowiki", "wiki_plan.yaml")
    assert cli.main(["qoder-plan", "--emit", out, "--repo", repo.path,
                     "--root", root]) == config.EXIT_OK
    assert os.path.isfile(out)
    from pwiki_lib.yamlite import yaml_load
    doc = yaml_load(open(out, encoding="utf-8").read())
    assert doc["version"] == 1
    assert "scope" in doc and "repowiki" in doc and "knowledgecard" in doc
    docs = doc["repowiki"]["documents"]
    assert docs and all(d.get("title") and d.get("goal") for d in docs)
    # 字节口径预检: 33 个 CJK 字符的 title 会触发警告(100 字节 ≈ 33 CJK)
    problems = qoderplan._check_caps(
        [{"title": "字" * 40, "goal": "目标"}], [])
    assert any("字节" in p for p in problems)


def test_qoder_plan_lint_silent_repairs(tmp_path):
    """lint 抓出 Qoder 会静默施加的全部修复。"""
    path = str(tmp_path / "poison.yaml")
    long_cjk_title = "标" * 40     # 120 字节 > 100
    doc = f"""version: 1
scope:
  include: []
  exclude: []
repowiki:
  template: bogus-preset
  notes:
    - text: ""
    - text: "{'注' * 4000}"
  documents:
    - title: "总览"
      goal: "目标"
    - title: "{long_cjk_title}"
      goal: "另一个目标"
      parent: "总览"
    - title: "总览"
      goal: "重复标题"
      parent: "孤儿父"
    - title: "自指"
      goal: ""
      parent: "自指"
knowledgecard:
  notes: []
"""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(doc)
    import types
    from pwiki_lib import cli
    repo, root = _fixture(tmp_path)
    code = cli.main(["qoder-plan", "--lint", path, "--repo", repo.path,
                     "--root", root])
    assert code == config.EXIT_GATE        # 有问题非零
    # 各修复路径都被点名
    out = _capture(lambda: cli.main(["qoder-plan", "--lint", path,
                                     "--repo", repo.path, "--root", root]))
    for kw in ("字节", "重复 title", "降级顶层", "空 goal", "空文本",
               "预设名"):
        assert kw in out, f"lint 应点名 {kw!r}\n{out}"


def test_qoder_plan_clean_file_passes(tmp_path):
    from pwiki_lib import cli
    repo, root = _fixture(tmp_path)
    _ready_wiki(repo, root)
    out = os.path.join(str(tmp_path), "clean-plan.yaml")
    assert cli.main(["qoder-plan", "--emit", out, "--repo", repo.path,
                     "--root", root]) == config.EXIT_OK
    # emit 产物再 lint: 不应有错误级问题(干净文件)
    code = cli.main(["qoder-plan", "--lint", out, "--repo", repo.path,
                     "--root", root])
    assert code in (config.EXIT_OK,) or "warn" in _capture(
        lambda: cli.main(["qoder-plan", "--lint", out, "--repo", repo.path,
                          "--root", root]))


def _capture(fn):
    import io
    import contextlib
    buf, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
        try:
            fn()
        except SystemExit:
            pass
    return buf.getvalue() + err.getvalue()
