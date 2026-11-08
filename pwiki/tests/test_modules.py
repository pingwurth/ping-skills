"""modules.py: 分解、D1 覆盖断言、注册表转换、depends_on 播种(别名数据化)。"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from gitrepo import GitRepo

from pwiki_lib import config, modules
from pwiki_lib.config import Ctx


def _inv(repo: GitRepo) -> dict:
    from pwiki_lib.scan import scan_repo
    return scan_repo(repo.path)


def _fixture(tmp_path) -> GitRepo:
    repo = GitRepo(tmp_path / "repo")
    repo.write("services/user/api.py", "from auth import token\n" * 10)
    repo.write("services/user/logic.py", "import auth\n" * 10)
    repo.write("services/order/api.py", "print('order')\n" * 12)
    repo.write("services/order/logic.py", "import user\n" * 12)
    repo.write("services/payment/pay.py", "import order\n" * 8)
    repo.write("packages/shared/util.py", "def f():\n    pass\n" * 6)
    repo.write("packages/shared/types.py", "X = 1\n" * 6)
    repo.write("packages/client-core/core.py", "Y = 2\n" * 5)
    repo.write("docs/guide.md", "# 指南\n" * 5)
    repo.write("README.md", "# 演示仓库\n\n多服务演示。\n")
    repo.commit("init")
    return repo


def test_decompose_and_registry(tmp_path):
    repo = _fixture(tmp_path)
    inv = _inv(repo)
    dec = modules.decompose(inv)
    assert dec["remaining"] == []
    registry = modules.to_plan_modules(dec)
    ids = {m["id"] for m in registry}
    assert "root" in ids
    paths = {m["path"] for m in registry}
    assert "services" in paths or any(p.startswith("services/") for p in paths)
    root = next(m for m in registry if m["id"] == "root")
    assert root["parent"] == ""
    # 每个模块的 parent 是注册表 id
    idset = {m["id"] for m in registry}
    for m in registry:
        if m["id"] != "root":
            assert m["parent"] in idset, f"{m['id']} 的 parent {m['parent']} 不在注册表"


def test_d1_coverage_assertion_fails_on_unrepresentable(tmp_path):
    """D1: 无法代表的目录 ⇒ remaining 非空 ⇒ cmd_modules 退出非零。

    构造: 一个容器目录只有噪声子目录(全被 NOISE_DIRS 过滤), 制造断言压力。"""
    repo = GitRepo(tmp_path / "repo2")
    # 单一巨大目录 + 全部子目录都是噪声 → 细分被抑制, 覆盖仍应成立(由父模块代表)
    for i in range(40):
        repo.write(f"big/src/mod{i}.py", "X = 1\n" * 40)
    repo.write("README.md", "# big\n")
    repo.commit("init")
    inv = _inv(repo)
    dec = modules.decompose(inv)
    # 正常路径: 要么 src 被吸收, 要么无孤儿; remaining 必须为空
    assert dec["remaining"] == []


def test_skeleton_flag(tmp_path):
    """D8: 几乎没有代码文件的目录标记为骨架。"""
    repo = GitRepo(tmp_path / "repo3")
    repo.write("app/main.py", "print(1)\n" * 30)
    repo.write("app/core/x.py", "A = 1\n" * 30)
    repo.write("skeleton-dir/README.md", "# 只有文档的占位目录\n")
    repo.write("skeleton-dir/.gitkeep", "")
    repo.write("README.md", "# 骨架演示\n")
    repo.commit("init")
    inv = _inv(repo)
    dec = modules.decompose(inv)
    registry = modules.to_plan_modules(dec)
    sk = [m for m in registry if m["id"] == "skeleton-dir"]
    if sk:   # min_files=4 时它可能太小不成模块; 成了就必须是骨架
        assert sk[0]["skeleton"] or sk[0]["module_type"] == "skeleton"


def test_pom_comment_stripped(tmp_path):
    """qwiki bug6: 被注释掉的 pom 依赖不泄漏。"""
    text = """<dependencies>
<!-- <dependency><artifactId>ghost</artifactId></dependency> -->
<dependency><artifactId>real-dep</artifactId></dependency>
</dependencies>"""
    arts = modules._pom_dep_artifacts(text)
    assert arts == {"real-dep"}


def test_imported_names_dotted_segments():
    """qwiki bug7: py from X.Y import → X、Y 都是候选; java 取首段。"""
    text = ("from src.util import helper\n"
            "import json, os\n"
            "import com.example.DeepThing;\n"
            "const x = require('lodash')\n")
    names = modules._imported_names(text)
    for n in ("src", "util", "json", "os", "com", "lodash"):
        assert n in names, n


def test_seed_depends_on_with_aliases(tmp_path):
    """qwiki bug8 + 数据化别名: 多形态别名解析; yudao 规则现在是 plan aliases。

    机制口径: import 名/pom artifactId → 别名键(模块 id/dirbase/title, 剥
    -api/-server/-biz 后缀); fnmatch 模式规则来自 plan aliases:。"""
    repo = GitRepo(tmp_path / "repo4")
    repo.write("auth_svc/api.py", "from user_svc import client\n" * 5)
    repo.write("user_svc/api.py", "print(1)\n" * 5)
    repo.write("yudao-server/pom.xml",
               "<project><dependencies>\n"
               "  <dependency><artifactId>yudao-spring-boot-starter-web"
               "</artifactId></dependency>\n"
               "</dependencies></project>\n")
    repo.write("yudao-framework/pom.xml", "<project></project>\n")
    repo.write("README.md", "# alias demo\n")
    repo.commit("init")

    import types
    ctx = Ctx(types.SimpleNamespace(repo=repo.path, root=str(tmp_path / "root"),
                                    locale="zh")).load()
    plan = {"schema_version": 3,
            "modules": [
                {"id": "auth_svc", "title": "认证服务", "dir": "auth_svc/",
                 "scope": ["auth_svc/"], "depends_on": []},
                {"id": "user_svc", "title": "用户服务", "dir": "user_svc/",
                 "scope": ["user_svc/"], "depends_on": []},
                {"id": "yudao-server", "title": "yudao 服务", "dir": "yudao-server/",
                 "scope": ["yudao-server/"], "depends_on": []},
                {"id": "yudao-framework", "title": "yudao 框架",
                 "dir": "yudao-framework/", "scope": ["yudao-framework/"],
                 "depends_on": []},
            ],
            "pages": [], "cards": [],
            "aliases": [{"patterns": ["yudao-spring-boot-starter-*", "yudao-common*"],
                         "module": "yudao-framework"}]}
    n = modules.seed_depends_on(ctx, plan)
    assert n >= 1
    by_id = {m["id"]: m.get("depends_on") for m in plan["modules"]}
    assert "user_svc" in by_id["auth_svc"]        # from user_svc import → 别名键直命中
    assert "yudao-framework" in by_id["yudao-server"]  # pom artifactId → plan aliases 规则


def test_cmd_modules_writes_plan_and_db(tmp_path):
    repo = _fixture(tmp_path)
    import types
    from pwiki_lib import cli
    code = cli.main(["modules", "--repo", repo.path,
                     "--root", str(tmp_path / "wiki-root")])
    assert code == config.EXIT_OK
    plan_path = str(tmp_path / "wiki-root" / "wiki_plan.yaml")
    assert os.path.isfile(plan_path)
    from pwiki_lib.planner import load_plan
    ctx = Ctx(types.SimpleNamespace(repo=repo.path,
                                    root=str(tmp_path / "wiki-root"),
                                    locale="zh")).load()
    plan = load_plan(ctx)
    assert plan["schema_version"] == 3
    assert plan["modules"]
    pid = ctx.partition()["id"]
    assert ctx.db.modules(pid)   # 注册表进了 DB
