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


# ── P3 回修回归: 范围/门禁(2026-11-02 全面审查) ──────────────────────
def test_p3_子目录相对路径目标不再误判(tmp_path):
    """P3 回归: 顶层重定根后相对路径目标按顶层判定 —— (root/'mod.py') 不存在
    且 ls-files 列的是 src/deep/mod.py → 误判 branch → git diff main...mod.py
    失败落逃逸口, 用户点名的文件审查意图丢失。"""
    repo = RepoBuilder.init(tmp_path)
    repo.commit({"src/deep/mod.py": "x = 1\n"}, "init")
    repo.edit({"src/deep/mod.py": "x = 2\n"})
    sc = scope_mod.resolve_scope(repo.root / "src" / "deep", "mod.py")
    assert not sc.get("escape_hatch"), "相对路径目标不得落逃逸口"
    assert sc["diff_files"] == ["src/deep/mod.py"], "path 目标须归一为顶层相对"
    assert sc["stats"]["files"] == 1


def test_p3_点斜杠路径目标的untracked命中(tmp_path):
    """P3 回归: untracked_in_scope 用 target 原文比对 porcelain 路径 ——
    './src' 形式零命中, 显式累积要覆盖的洞被重新打开(只剩目录 whole-file)。"""
    repo = RepoBuilder.init(tmp_path)
    repo.commit({"README.md": "r\n"}, "init")
    repo.untracked({"src/new.py": "n = 1\n"})
    sc = scope_mod.resolve_scope(repo.root, "./src")
    assert sc["untracked"] == ["src/new.py"]
    assert "src/new.py" in sc["diff_files"]
    assert not sc.get("escape_hatch")


def test_p3_排除项同步过滤untracked(tmp_path):
    """P3 回归: apply_exclusions 原版只滤 diff_files —— trusted overlay 排除
    'gen/' 后 scope['untracked'] 仍把 gen/g_new.py 全量交给 brief 当审查目标,
    'out of scope entirely' 承诺对未跟踪文件不成立。"""
    repo = RepoBuilder.init(tmp_path)
    repo.commit({"src/a.py": "x = 1\n", "gen/g.py": "1\n"}, "init")
    repo.edit({"src/a.py": "x = 2\n"})
    repo.untracked({"gen/g_new.py": "n = 1\n"})
    sc = scope_mod.resolve_scope(repo.root)
    assert set(sc["diff_files"]) == {"src/a.py", "gen/g_new.py"}
    scope_mod.apply_exclusions(sc, ["gen/"], "overlay")
    assert sc["diff_files"] == ["src/a.py"]
    assert sc["untracked"] == [], "被排除的未跟踪路径不得再作为审查目标"
    assert sc["raw_files"] == ["src/a.py"]
    assert [e["path"] for e in sc["excluded"]] == ["gen/g_new.py"]


def test_p3_二进制与巨型untracked行数计0(tmp_path):
    """P3 回归: _file_insertions 承诺 '二进制/超大文件计 0' 但无任何检查 ——
    50MB 构建产物把原始行数灌进 stats.insertions, 顶穿 FANOUT 阈值。"""
    from crp import config
    (tmp_path / "text.py").write_bytes(b"a\nb\nc\n")
    (tmp_path / "blob.bin").write_bytes(b"\x00\x01\n" * 10)
    (tmp_path / "huge.min.js").write_bytes(b"x" * (config.UNTRACKED_INSERTIONS_MAX_BYTES + 1))
    assert scope_mod._file_insertions(tmp_path / "text.py") == 3
    assert scope_mod._file_insertions(tmp_path / "blob.bin") == 0
    assert scope_mod._file_insertions(tmp_path / "huge.min.js") == 0
    # 集成: untracked 二进制不得抬高范围统计
    repo = RepoBuilder.init(tmp_path)
    repo.commit({"README.md": "r\n"}, "init")
    (repo.root / "out.bin").write_bytes(b"\x00" * 100)
    sc = scope_mod.resolve_scope(repo.root)
    assert sc["untracked"] == ["out.bin"]
    assert sc["stats"]["insertions"] == 0


def test_p3_区间三点目标base_head拆分(tmp_path):
    """P3 回归: 'main...feature' 按 partition('..') 拆出 head='.feature' ——
    三点对称差须优先, 无 '...' 才按 '..'。"""
    repo = RepoBuilder.init(tmp_path)
    repo.commit({"a.py": "1\n"}, "c1")
    repo.commit({"b.py": "2\n"}, "c2")
    sc = scope_mod.resolve_scope(repo.root, "HEAD~1...HEAD")
    assert sc["kind"] == "range"
    assert sc["base"] == "HEAD~1" and sc["head"] == "HEAD"
    sc2 = scope_mod.resolve_scope(repo.root, "HEAD~1..HEAD")
    assert sc2["base"] == "HEAD~1" and sc2["head"] == "HEAD"


# ── P6 回修回归: Phase 0 子进程开销(2026-11-02 全面审查) ────────────
def test_p6_命中rung只跑一次git_diff(tmp_path, monkeypatch):
    """P6 回归: 每个命中 rung 把同一 git diff 跑两遍(--name-only 取文件 +
    --numstat 取统计, 完全相同参数)—— 大树上最贵的 Phase 0 操作纯双倍功。"""
    from crp import gitops as gitops_mod
    repo = RepoBuilder.init(tmp_path)
    repo.commit({"src/a.py": "x = 1\n"}, "init")
    repo.edit({"src/a.py": "x = 2\n"})
    calls: list[tuple] = []
    orig = gitops_mod.run

    def counting(args, cwd):
        if args and args[0] == "diff":
            calls.append(tuple(args))
        return orig(args, cwd)

    monkeypatch.setattr(scope_mod.gitops, "run", counting)
    sc = scope_mod.resolve_scope(repo.root)
    assert sc["kind"] == "worktree" and sc["stats"]["files"] == 1
    # rung3 探测 + rung4 探测 + rung5 命中, 每级恰一条(原版命中级 = name-only+numstat 两条)
    assert len(calls) == 3, f"三级各一条(实际 {calls})"
    assert len(set(calls)) == len(calls), "同参数 git diff 不得重复执行"
    assert all(c[:2] == ("diff", "--numstat") for c in calls)

    # 路径受限同样一次(且 numstat 带 -- target, 统计口径不再漏限制)
    calls.clear()
    sc2 = scope_mod.resolve_scope(repo.root, "src/a.py")
    assert sc2["kind"] == "worktree"
    assert len(calls) == 1 and "--" in calls[0], "路径受限的统计必须带 -- target"


def test_p6_start全流程一份porcelain快照(tmp_path, monkeypatch):
    """P6 回修: 单次 start 把同一 git status --porcelain -z -uall 跑 2-3 次
    (阶梯入口 untracked / 重导 / 签名)—— 10 万文件 monorepo 每次 1-3 秒。"""
    import argparse
    from crp import gitops as gitops_mod
    from crp import handlers
    repo = RepoBuilder.init(tmp_path)
    repo.commit({"src/a.py": "x = 1\n"}, "init")
    repo.edit({"src/a.py": "x = 2\n"})
    calls: list[str] = []
    orig = gitops_mod.porcelain_z

    def counting(cwd):
        calls.append(str(cwd))
        return orig(cwd)

    monkeypatch.setattr(gitops_mod, "porcelain_z", counting)
    monkeypatch.chdir(repo.root)
    monkeypatch.setenv("CRP_HOME", str(tmp_path / "home"))
    args = argparse.Namespace(run_dir=None, mode="quick", target=None,
                              fix=False, force=True)
    handlers.cmd_start(args)
    assert len(calls) == 1, f"start 全程应恰一次 porcelain(实际 {len(calls)})"


def test_p6_已删除tracked文件仍判为path(tmp_path):
    """P6 回修: classify_target 改 --error-unmatch 后语义保持 —— 已删除
    未暂存的 tracked 文件(exists() False)仍应判 path(whole-file/diff 可见)。"""
    repo = RepoBuilder.init(tmp_path)
    repo.commit({"src/a.py": "x = 1\n", "README.md": "r\n"}, "init")
    (repo.root / "src" / "a.py").unlink()
    assert scope_mod.classify_target(repo.root, "src/a.py") == "path"
    assert scope_mod.classify_target(repo.root, "no-such-ref") == "branch"


# ── P9 回修回归: 自食验收 3×P1(2026-11-02 下午) ─────────────────────
def test_p9_重命名伪路径归一为新路径(tmp_path):
    """P9 回归(P1): diff.renames 默认开, numstat 重命名行是 '{old => new}'/
    'old => new' 伪路径 —— 原样进 diff_files 使 brief 打不开、排除项失配、
    trust 精确成员失配(A3 绕过面, 实证)。"""
    repo = RepoBuilder.init(tmp_path)
    repo.commit({"src/a.py": "x = 1\n", "src/old_name.txt": "old\n"}, "init")
    repo.edit({"src/a.py": "x = 2\n"})
    (repo.root / "src" / "old_name.txt").rename(repo.root / "src" / "new_name.txt")
    repo.stage("src/old_name.txt"); repo.stage("src/new_name.txt")
    sc = scope_mod.resolve_scope(repo.root)
    assert sc["diff_files"] == ["src/a.py", "src/new_name.txt"], \
        "重命名必须归一为新路径, 不得出现 '=> ' 伪路径"
    assert sc["stats"]["files"] == 2
    assert all("=>" not in f and "{" not in f for f in sc["raw_files"])


def test_p9_改名进overlay路径被判untrusted(tmp_path):
    """P9 回归(P1 security 面): 把普通文件改名为 overlay 路径进被审 diff ——
    伪路径形使 trust_of 精确成员匹配失配, diff 引入的 overlay 被当 trusted
    (A3 挂起被绕过, 实证); 归一后必须判 untrusted。"""
    from crp import overlay
    repo = RepoBuilder.init(tmp_path)
    # 同目录纯改名(内容随行)—— git 配对为 rename, numstat 打花括号伪路径
    repo.commit({"src/a.py": "x = 1\n", ".claude/attacker.md": "evil overlay\n"}, "init")
    repo.edit({"src/a.py": "x = 2\n"})
    (repo.root / ".claude" / "attacker.md").rename(repo.root / ".claude" / "code-review-p.md")
    repo.stage(".claude/attacker.md"); repo.stage(".claude/code-review-p.md")
    sc = scope_mod.resolve_scope(repo.root)
    assert ".claude/code-review-p.md" in sc["diff_files"], "改名目标即真实新路径"
    trust, _ = overlay.trust_of(sc, ".claude/code-review-p.md")
    assert trust == "untrusted", "被审 diff 引入/触及 overlay 必须 untrusted"
