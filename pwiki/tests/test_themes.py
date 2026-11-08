"""themes.py: 主题包门控、D12 declined、D13 裁剪、check gate、合并与评审门。"""

from __future__ import annotations

import json
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from gitrepo import GitRepo

from pwiki_lib import config, corpus, themes
from pwiki_lib.config import Ctx


def _svc_fixture(tmp_path):
    """services/* 多服务仓库(触发 D2 微服务包 + architecture + devguide)。"""
    repo = GitRepo(tmp_path / "repo")
    for svc, n in (("gateway", 40), ("user-api", 35), ("order-api", 35),
                   ("ai-llm", 30), ("pay-server", 30)):
        repo.write(f"services/{svc}/pom.xml",
                   f"<project><artifactId>{svc}</artifactId></project>\n")
        for i in range(n // 10):
            repo.write(f"services/{svc}/src/main/java/Api{i}.java",
                       "public class Api%d {\n}\n" % i)
        repo.write(f"services/{svc}/README.md", f"# {svc}\n")
    repo.write("README.md", "# 服务演示仓库\n\n五个后端服务。\n")
    repo.write("Makefile", "build:\n\techo b\n\ntest:\n\techo t\n")
    repo.commit("init")
    return repo


def _ctx(repo, root) -> Ctx:
    return Ctx(types.SimpleNamespace(repo=repo.path if hasattr(repo, "path")
                                     else str(repo), root=str(root),
                                     locale="zh")).load()


def _decomposed(repo):
    from pwiki_lib import modules as mods
    from pwiki_lib.scan import scan_repo
    inv = scan_repo(repo.path if hasattr(repo, "path") else str(repo))
    return inv, mods.decompose(inv)


def test_pack_gating_and_d2_services(tmp_path):
    repo = _svc_fixture(tmp_path)
    inv, dec = _decomposed(repo)
    ctx = _ctx(repo, tmp_path / "root")
    result = themes.run_themes(ctx, inv, dec, "standard", framework_mods=set())
    titles = [p["title"] for p in result["pages"]]
    assert "架构设计" in titles            # always pack
    assert "微服务集群" in titles           # D2: ≥3 services 模块
    assert "开发指南" in titles             # always pack
    ids = [p["id"] for p in result["pages"]]
    assert len(ids) == len(set(ids))
    # D22: 主题子项带 title_check
    assert any(p.get("title_check") for p in result["pages"])
    # 卡片: 依赖管理/构建/术语表至少出现
    card_kinds = {c["kind"] for c in result["cards"]}
    assert "business_term" in card_kinds
    assert "dependency_management" in card_kinds


def test_d12_decline_recorded(tmp_path):
    """弱证据运维主题被拒并记录原因(theme_declined)。

    子项必须先过 min 门(有文档证据)才会走到关键词门 —— 所以部署文档要
    真的提到 pm2/systemd/nginx/prometheus 这些话题, 才能证明"只写文档、
    没有实现"的拒绝路径。"""
    repo = GitRepo(tmp_path / "repo2")
    for i in range(6):
        repo.write(f"services/s{i}/main.py", "print(1)\n" * 20)
    repo.write("README.md", "# small\n")
    # infra 强证据 ×2 让 deploy 包过 min 门; 文档 ×2 让运维子项过各自 min 门
    repo.write("Dockerfile", "FROM python:3.12\n")
    repo.write(".github/workflows/ci.yml", "jobs:\n  build:\n")
    repo.write("docs/deploy.md",
               "# 部署\n\n## 进程管理\n使用 pm2 与 systemd 守护进程。\n\n"
               "## 网络配置\nnginx 反向代理, 端口映射。\n\n"
               "## 监控\nprometheus 采集指标, grafana 看板。\n\n"
               "## 备份\nbackup 脚本每日 dump。\n")
    repo.write("docs/ops.md",
               "# 运维\n\npm2 进程守护; systemd 单元; nginx 反向代理;\n"
               "prometheus 监控; backup 每日恢复演练。\n")
    repo.commit("init")
    inv, dec = _decomposed(repo)
    ctx = _ctx(repo, tmp_path / "root2")
    result = themes.run_themes(ctx, inv, dec, "standard", set())
    declined = result["report"]["theme_declined"]
    assert isinstance(declined, list) and declined, "弱证据运维主题应被拒绝并记录"
    declined_children = {d.get("child") for d in declined}
    assert {"process", "network", "monitor", "backup"} & declined_children
    # 每条拒绝都有原因(D12 可评审)
    for d in declined:
        assert d.get("reason")


def test_check_gate_scope_hit_and_d6(tmp_path, monkeypatch):
    repo = _svc_fixture(tmp_path)
    # 给 D6 一个真实的近邻文件: 盘上是 deploy-guide.md(连字符形态)
    repo.write("docs/deploy-guide.md", "# 部署\n\npm2 与 systemd 守护。\n")
    repo.commit("ops docs")
    inv, dec = _decomposed(repo)
    ctx = _ctx(repo, tmp_path / "root3")
    # D6 语义: 手改计划(与规划器)会拼出不存在的路径 —— 这里注入下划线形态
    # deploy_guide.md 模拟手改(规划器自身只引用清单内路径, 造不出坏路径),
    # 让 check gate 的 _/- 近邻修复真实地跑一遍。
    _orig_add = themes.Builder.add

    def _add_with_hand_edited_source(self, page_id, *a, **kw):
        page = _orig_add(self, page_id, *a, **kw)
        if page_id == "project-overview":
            page["source_files"].insert(0, "docs/deploy_guide.md")
        return page

    monkeypatch.setattr(themes.Builder, "add", _add_with_hand_edited_source)
    result = themes.run_themes(ctx, inv, dec, "standard", set())
    # 正常仓库: 源都在各自 scope 内 → check errors 必须为零(不是"极少")
    report = result["report"]
    assert "check" in report
    assert report["check"]["errors"] == [], report["check"]["errors"]
    # D6: 不存在的 _ 形态被修成盘上真实存在的 - 形态, 并留账(name-fixed)
    fixed = [w for w in report["check"]["warnings"]
             if w["code"] == "source/name-fixed"]
    assert any("docs/deploy_guide.md -> docs/deploy-guide.md" in w["detail"]
               for w in fixed), report["check"]["warnings"]
    ov = next(p for p in result["pages"] if p["id"] == "project-overview")
    assert "docs/deploy_guide.md" not in ov["source_files"]
    assert "docs/deploy-guide.md" in ov["source_files"]
    # 源文件全部真实存在
    for p in result["pages"]:
        for s in p.get("source_files") or []:
            assert os.path.exists(os.path.join(repo.path, s)), f"{s} 不存在"


def test_cmd_plan_themes_merge_and_review(tmp_path):
    """plan --themes 全链路: 合并进 plan + diff 报告 + plan.md 评审门。"""
    from pwiki_lib import cli
    repo = _svc_fixture(tmp_path)
    root = str(tmp_path / "wiki")
    assert cli.main(["scan", "--repo", repo.path, "--root", root]) == 0
    assert cli.main(["modules", "--repo", repo.path, "--root", root]) == 0
    code = cli.main(["plan", "--themes", "--repo", repo.path, "--root", root])
    assert code == config.EXIT_OK
    # plan 文件 v3 + 主题字段
    from pwiki_lib.planner import load_plan
    ctx = _ctx(repo, root)
    plan = load_plan(ctx)
    assert plan["schema_version"] == 3
    assert plan["pages"]
    assert any(p.get("pack_key") for p in plan["pages"])
    assert plan["cards"]
    # 手工字段保留
    plan["repo_summary"] = "手工定位"
    from pwiki_lib.planner import save_plan
    save_plan(ctx, plan)
    code = cli.main(["plan", "--themes", "--repo", repo.path, "--root", root])
    assert code == config.EXIT_OK
    plan2 = load_plan(ctx)
    assert plan2["repo_summary"] == "手工定位"
    # 报告与评审门文件
    assert os.path.isfile(os.path.join(root, "work", "themes-report.json"))
    plan_md = os.path.join(root, "work", "plan.md")
    assert os.path.isfile(plan_md)
    body = open(plan_md, encoding="utf-8").read()
    assert "Page tree" in body
    # validate 仍绿(覆盖硬规: services 模块被微服务包覆盖)
    assert cli.main(["validate", "--repo", repo.path, "--root", root]) == 0
    # digest 跑通
    assert cli.main(["digest", "--repo", repo.path, "--root", root]) == 0
    digest = open(os.path.join(root, "work", "repo-notes.md"), encoding="utf-8").read()
    assert "模块表" in digest


def test_overrides_loaded_from_pwiki_yaml(tmp_path):
    """试点标题映射从仓库级 pwiki.yaml 注入(去试点化)。"""
    repo = _svc_fixture(tmp_path)
    repo.write("pwiki.yaml",
               "title_maps:\n"
               "  feature:\n"
               "    library: 材料库页面\n"
               "title_aliases:\n"
               "  '功能指南|设置页面': 设置功能\n")
    repo.commit("overrides")
    ctx = _ctx(repo, tmp_path / "root4")
    ov = themes.load_overrides(ctx)
    assert ov["title_maps"]["feature"]["library"] == "材料库页面"
    assert ov["title_aliases"][("功能指南", "设置页面")] == "设置功能"
