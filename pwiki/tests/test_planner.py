"""planner.py: plan v3 校验、种子回环、路径映射、hash。"""

from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from gitrepo import GitRepo

from pwiki_lib import config, planner
from pwiki_lib.config import Ctx


def _fixture(tmp_path, root_name="root") -> tuple:
    repo = GitRepo(tmp_path / "repo")
    repo.write("services/user/api.py", "X = 1\n" * 30)
    repo.write("services/user/logic.py", "Y = 2\n" * 30)
    repo.write("services/order/api.py", "Z = 3\n" * 30)
    repo.write("services/order/logic.py", "W = 4\n" * 30)
    repo.write("services/payment/pay.py", "Q = 5\n" * 20)
    repo.write("packages/shared/util.py", "def f():\n    pass\n" * 8)
    repo.write("packages/shared/types.py", "T = 1\n" * 8)
    repo.write("packages/client-core/core.py", "C = 1\n" * 10)
    repo.write("docs/guide.md", "# 指南\n内容\n" * 4)
    repo.write("README.md", "# 演示仓库\n\n多服务演示。\n")
    repo.write("Makefile", "build:\n\techo build\n\ntest:\n\techo test\n")
    repo.commit("init")
    root = str(tmp_path / root_name)
    return repo, root


def _ctx(repo_path, root) -> Ctx:
    return Ctx(types.SimpleNamespace(repo=repo_path, root=root,
                                     locale="zh")).load()


def _full_pipeline(repo, root) -> dict:
    from pwiki_lib import cli
    assert cli.main(["modules", "--repo", repo.path, "--root", root]) == 0
    assert cli.main(["plan", "--seed", "--repo", repo.path, "--root", root]) == 0
    assert cli.main(["validate", "--repo", repo.path, "--root", root]) == 0
    return planner.load_plan(_ctx(repo.path, root))


def test_seed_validate_roundtrip(tmp_path):
    """P2 里程碑: scan→modules→plan --seed→validate 回环全绿。"""
    repo, root = _fixture(tmp_path)
    plan = _full_pipeline(repo, root)
    assert plan["schema_version"] == 3
    assert plan["style"] in config.STYLES
    pages = planner.plan_pages(plan)
    kinds = {p.get("kind") for p in pages}
    assert "page" in kinds
    titles = [p["title"] for p in pages]
    assert "项目概览" in titles
    assert "快速开始" in titles          # Makefile 触发 setup 信号
    ids = [p["id"] for p in pages]
    assert len(ids) == len(set(ids))
    # 覆盖硬规在 validate 里通过(每个模块被 domain 覆盖)


def test_path_mapping_three_levels(tmp_path):
    """qwiki bug4: 三层嵌套父目录顺序(最远祖先在前)。"""
    pages = [
        {"id": "a", "title": "一级", "parent": "", "kind": "page"},
        {"id": "b", "title": "二级", "parent": "a", "kind": "page"},
        {"id": "c", "title": "三级", "parent": "b", "kind": "page"},
    ]
    path = planner.page_path_for(pages, pages[2], "zh", {})
    assert path.replace(os.sep, "/") == "zh/content/一级/二级/三级.md"


def test_path_mapping_sheet_uses_display_name(tmp_path):
    """sheet 路径用 i18n 显示名(zh: 概述.md), 身份仍是 slug。"""
    by_id = {"src": {"id": "src", "title": "核心", "dir": "src/"}}
    page = {"id": "src-overview", "title": "概述", "parent": "",
            "kind": "module_sheet", "module": "src", "sheet": "overview"}
    path = planner.page_path_for([], page, "zh", by_id)
    assert path.replace(os.sep, "/") == "knowledge/zh/核心/概述.md"


def test_module_dir_name_collision_fallback(tmp_path):
    """qwiki bug11: 同名模块目录回退模块路径(80 sheet 塌 8 目录的修复)。"""
    by_id = {
        "a": {"id": "a", "title": "脚本", "dir": "tools/scripts/"},
        "b": {"id": "b", "title": "脚本", "dir": "app/scripts/"},
    }
    assert planner.module_dir_name(by_id["a"], by_id) == "tools-scripts"
    assert planner.module_dir_name(by_id["b"], by_id) == "app-scripts"


def test_validate_catches_errors(tmp_path):
    repo, root = _fixture(tmp_path)
    _full_pipeline(repo, root)
    ctx = _ctx(repo.path, root)
    plan = planner.load_plan(ctx)
    plan["pages"].append({"id": "orphan-child", "title": "孤儿", "parent": "nope",
                          "kind": "page", "type": "deep_dive", "slug": "orphan",
                          "objective": "x", "modules": ["/"],
                          "prompt": "p", "source_files": ["README.md"]})
    errors, warnings = planner.validate_plan(ctx, plan)
    assert any("未知 parent" in e for e in errors)
    # 重复 id
    dup = dict(plan["pages"][0])
    plan["pages"].append(dup)
    errors, _ = planner.validate_plan(ctx, plan)
    assert any("重复" in e for e in errors)
    # 叶子型带子篇
    gs = next(p for p in plan["pages"] if p.get("type") == "getting_started")
    gs["id"] = "gs-unique"
    plan["pages"].append({"id": "gs-child", "title": "子", "parent": "gs-unique",
                          "kind": "page", "modules": ["/"], "prompt": "p",
                          "source_files": ["README.md"]})
    errors, _ = planner.validate_plan(ctx, plan)
    assert any("叶子" in e for e in errors)


def test_validate_style_and_sheet_slug(tmp_path):
    repo, root = _fixture(tmp_path)
    _full_pipeline(repo, root)
    ctx = _ctx(repo.path, root)
    plan = planner.load_plan(ctx)
    plan["style"] = "bogus"
    errors, _ = planner.validate_plan(ctx, plan)
    assert any("style" in e for e in errors)
    plan["style"] = "deep"
    plan["pages"].append({"id": "bad-sheet", "title": "x", "kind": "module_sheet",
                          "module": "root", "sheet": "概述", "prompt": "p",
                          "source_files": []})
    errors, _ = planner.validate_plan(ctx, plan)   # 中文显示名也能解析(slug 化)
    assert not any("sheet" in e for e in errors)
    plan["pages"][-1]["sheet"] = "不存在的表"
    errors, _ = planner.validate_plan(ctx, plan)
    assert any("sheet" in e for e in errors)


def test_plan_hash_sensitivity():
    p1 = [{"id": "a", "title": "t", "prompt": "写 A"}]
    p2 = [{"id": "a", "title": "t", "prompt": "写 B"}]
    assert planner.plan_hash(p1, []) != planner.plan_hash(p2, [])
    m1 = [{"id": "a", "scope": ["a/"], "depends_on": []}]
    m2 = [{"id": "a", "scope": ["a/", "b/"], "depends_on": []}]
    assert planner.modules_hash(m1) != planner.modules_hash(m2)


def test_plan_never_silently_overwrites(tmp_path):
    """plan 已存在且未 --reset: 只做增量, 手工编辑保留。"""
    repo, root = _fixture(tmp_path)
    _full_pipeline(repo, root)
    ctx = _ctx(repo.path, root)
    plan_path = ctx.plan_path
    with open(plan_path, encoding="utf-8") as fh:
        text = fh.read()
    # 注入手工字段
    plan = planner.load_plan(ctx)
    plan["repo_summary"] = "手工写的一句话定位"
    planner.save_plan(ctx, plan)
    from pwiki_lib import cli
    assert cli.main(["plan", "--repo", repo.path, "--root", root]) == 0
    plan2 = planner.load_plan(ctx)
    assert plan2["repo_summary"] == "手工写的一句话定位"
    assert planner.plan_hash(planner.plan_pages(plan2), []) == \
        planner.plan_hash(planner.plan_pages(plan), [])   # pages 未被重置


def test_sheets_expansion(tmp_path):
    repo, root = _fixture(tmp_path)
    _full_pipeline(repo, root)
    from pwiki_lib import cli
    assert cli.main(["plan", "--sheets", "standard", "--repo", repo.path,
                     "--root", root]) == 0
    ctx = _ctx(repo.path, root)
    plan = planner.load_plan(ctx)
    sheets = [p for p in planner.plan_pages(plan) if p.get("kind") == "module_sheet"]
    assert sheets
    slugs = {p["sheet"] for p in sheets}
    assert slugs <= {"overview", "architecture", "stack"}
    # validate 仍绿(sheet 走 i18n 身份)
    assert cli.main(["validate", "--repo", repo.path, "--root", root]) == 0
