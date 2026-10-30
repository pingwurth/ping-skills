"""Phase 0 阶梯矩阵测试: 各级首个非空、失败下降、untracked、path/repo 语义、漂移。"""

from __future__ import annotations

from pathlib import Path

from crp import gitops, scope as scope_mod
from gitrepo import RepoBuilder


def test_工作树改动命中第5级(tmp_path):
    repo = RepoBuilder.init(tmp_path)
    repo.commit({"src/a.py": "x = 1\n"}, "init")
    repo.edit({"src/a.py": "x = 2\n"})
    sc = scope_mod.resolve_scope(repo.root)
    assert sc["kind"] == "worktree" and sc["rung"] == 5
    assert sc["scope_command"] == "git diff HEAD"
    assert sc["diff_files"] == ["src/a.py"]
    assert sc["stats"]["files"] == 1


def test_未推送提交命中第3级(tmp_path):
    origin = RepoBuilder.init(tmp_path, name="origin")
    origin.commit({"README.md": "hi\n"}, "init")
    clone = origin.clone(tmp_path)
    clone.commit({"src/new.py": "print(1)\n"}, "local work")
    sc = scope_mod.resolve_scope(clone.root)
    assert sc["kind"] == "upstream" and sc["rung"] == 3
    assert "src/new.py" in sc["diff_files"]


def test_已推送分支命中第4级(tmp_path):
    origin = RepoBuilder.init(tmp_path, name="origin")
    origin.commit({"README.md": "hi\n"}, "init")
    clone = origin.clone(tmp_path)
    clone.branch("feature")  # 未设 upstream → 第 3 级失败, 第 4 级命中
    clone.commit({"src/feat.py": "pass\n"}, "feature work")
    sc = scope_mod.resolve_scope(clone.root)
    assert sc["kind"] == "default" and sc["rung"] == 4
    assert "src/feat.py" in sc["diff_files"]
    assert sc["scope_command"].startswith("git diff origin/main...HEAD")


def test_第4级叠加工作树改动(tmp_path):
    origin = RepoBuilder.init(tmp_path, name="origin")
    origin.commit({"README.md": "hi\n"}, "init")
    clone = origin.clone(tmp_path)
    clone.branch("feature")
    clone.commit({"src/feat.py": "pass\n"}, "feature work")
    clone.edit({"README.md": "uncommitted change\n"})  # 已跟踪文件的工作树改动 → 叠加
    sc = scope_mod.resolve_scope(clone.root)
    assert sc["secondary_commands"] == ["git diff HEAD"]
    assert set(sc["diff_files"]) >= {"src/feat.py", "README.md"}


def test_仅untracked不是空diff(tmp_path):
    repo = RepoBuilder.init(tmp_path)
    repo.commit({"README.md": "hi\n"}, "init")
    repo.untracked({"src/brand_new.py": "x = 1\n"})
    sc = scope_mod.resolve_scope(repo.root)
    assert sc["kind"] == "worktree" and sc["untracked"] == ["src/brand_new.py"]
    assert sc["diff_files"] == ["src/brand_new.py"]
    assert sc["stats"]["files"] == 1


def test_干净仓库进逃逸口(tmp_path):
    repo = RepoBuilder.init(tmp_path)
    repo.commit({"README.md": "hi\n"}, "init")
    sc = scope_mod.resolve_scope(repo.root)
    assert sc["kind"] == "empty"
    assert sc["escape_hatch"] and sc["escape_hatch"]["probes"]


def test_路径目标_有改动走受限阶梯(tmp_path):
    repo = RepoBuilder.init(tmp_path)
    repo.commit({"src/a.py": "x=1\n", "other/b.py": "y=2\n"}, "init")
    repo.edit({"src/a.py": "x=2\n", "other/b.py": "y=3\n"})
    sc = scope_mod.resolve_scope(repo.root, "src")
    assert "src/a.py" in sc["diff_files"] and "other/b.py" not in sc["diff_files"]
    assert "-- src" in sc["scope_command"]


def test_路径目标_HEAD先行于范围级(tmp_path):
    """c052 回归: 路径受限时 `git diff HEAD -- <path>` 先行(scope.md 契约),
    即使 upstream 范围也触及该路径 —— 工作树是路径目标的主场景。
    注意 `git diff HEAD` 看不见 untracked, 所以用已跟踪文件的工作树改动。"""
    origin = RepoBuilder.init(tmp_path, name="origin")
    origin.commit({"README.md": "hi\n", "src/a.py": "x=1\n"}, "init")
    clone = origin.clone(tmp_path)
    clone.commit({"src/pushed.py": "pass\n"}, "pushed path work")  # upstream 范围也触及 src
    clone.edit({"src/a.py": "x=2\n"})                             # 已跟踪文件的工作树改动
    sc = scope_mod.resolve_scope(clone.root, "src")
    assert sc["scope_command"] == "git diff HEAD -- src", "路径受限必须 HEAD 先行"
    assert "src/a.py" in sc["diff_files"] and "src/pushed.py" not in sc["diff_files"]


def test_路径目标_干净文件回退whole_file(tmp_path):
    repo = RepoBuilder.init(tmp_path)
    repo.commit({"src/a.py": "x=1\n"}, "init")
    sc = scope_mod.resolve_scope(repo.root, "src/a.py")
    assert sc["kind"] == "whole_file" and sc["whole_file_mode"] is True
    assert sc["diff_files"] == ["src/a.py"]
    assert "git log -p -- src/a.py" == sc["commit_log_cmd"]


def test_repo目标空树基线(tmp_path):
    repo = RepoBuilder.init(tmp_path)
    repo.commit({"a.py": "1\n", "b/c.py": "2\n"}, "init")
    sc = scope_mod.resolve_scope(repo.root, "repo")
    assert sc["repo_mode"] is True and sc["rung"] == 1
    assert set(sc["diff_files"]) == {"a.py", "b/c.py"}


def test_区间与分支目标(tmp_path):
    repo = RepoBuilder.init(tmp_path)
    repo.commit({"a.py": "1\n"}, "init")
    repo.branch("feature")
    repo.commit({"b.py": "2\n"}, "feat")
    sc = scope_mod.resolve_scope(repo.root, "main..feature")
    assert sc["kind"] == "range" and sc["diff_files"] == ["b.py"]

    sc2 = scope_mod.resolve_scope(repo.root, "feature")
    assert sc2["kind"] == "branch" and "b.py" in sc2["diff_files"]
    assert sc2["scope_command"] == "git diff main...feature"


def test_目标即默认分支且无upstream进逃逸口(tmp_path):
    """默认分支对自身 diff 恒空 —— 无 upstream 参照时走逃逸口而非空审。"""
    repo = RepoBuilder.init(tmp_path)
    repo.commit({"a.py": "1\n"}, "init")
    sc = scope_mod.resolve_scope(repo.root, "main")
    assert sc.get("escape_hatch") is not None


def test_坏区间进逃逸口(tmp_path):
    repo = RepoBuilder.init(tmp_path)
    repo.commit({"a.py": "1\n"}, "init")
    sc = scope_mod.resolve_scope(repo.root, "nosuch..alsonosuch")
    assert sc.get("escape_hatch")


def test_子目录调用归一到仓库顶层(tmp_path):
    repo = RepoBuilder.init(tmp_path)
    repo.commit({"src/a.py": "x=1\n"}, "init")
    repo.edit({"src/a.py": "x=2\n"})
    (repo.root / "src").mkdir(exist_ok=True)
    sc = scope_mod.resolve_scope(repo.root / "src")
    assert sc["diff_files"] == ["src/a.py"]  # 路径相对顶层而非子目录


def test_cjk与空格路径的untracked解析(tmp_path):
    repo = RepoBuilder.init(tmp_path)
    repo.commit({"README.md": "hi\n"}, "init")
    repo.untracked({"src/中文 目录/文件 名.py": "x = 1\n"})
    files = gitops.untracked_files(repo.root)
    assert files == ["src/中文 目录/文件 名.py"]


def test_排除项应用(tmp_path):
    repo = RepoBuilder.init(tmp_path)
    repo.commit({"a.py": "1\n"}, "init")
    repo.edit({"a.py": "2\n"})
    sc = scope_mod.resolve_scope(repo.root)
    scope_mod.apply_exclusions(sc, ["src/generated/", "vendor"], "overlay")
    assert sc["diff_files"] == ["a.py"]
    repo.untracked({"src/generated/g.py": "x\n", "vendor/v.py": "y\n", "keep.py": "z\n"})
    sc2 = scope_mod.resolve_scope(repo.root)
    scope_mod.apply_exclusions(sc2, ["src/generated/", "vendor"], "overlay")
    assert set(sc2["diff_files"]) == {"a.py", "keep.py"}
    assert len(sc2["excluded"]) == 2


def test_漂移检测(tmp_path):
    repo = RepoBuilder.init(tmp_path)
    repo.commit({"a.py": "1\n"}, "init")
    before = scope_mod.current_signature(repo.root)
    assert scope_mod.drift_between(before, scope_mod.current_signature(repo.root)) == "none"
    repo.edit({"a.py": "2\n"})
    assert scope_mod.drift_between(before, scope_mod.current_signature(repo.root)) == "working_tree_changed"
    repo.commit({"a.py": "2\n"}, "bump")
    after = scope_mod.current_signature(repo.root)
    assert scope_mod.drift_between(before, after) == "head_moved"


def test_gh缺失即下降不报错(tmp_path, monkeypatch):
    """PATH 上没有 gh —— 第 2 级失败应静默下降(不抛异常)。"""
    repo = RepoBuilder.init(tmp_path)
    repo.commit({"README.md": "hi\n"}, "init")
    repo.edit({"a.py": "1\n"})
    monkeypatch.setenv("PATH", "/nonexistent")  # 洗掉 gh(顺带 git)后构建于前
    sc = scope_mod.resolve_scope(repo.root)
    # git 也不可用时一切 rung 失败 → 逃逸口而非异常
    assert isinstance(sc, dict) and sc.get("escape_hatch") is not None
