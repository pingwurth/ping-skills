"""overlay 信任判定、A3 排除挂起、门禁合并与审批规则、启发式扫描。"""

from __future__ import annotations

from crp import overlay
from crp.models import State
from gitrepo import RepoBuilder


def _state(scope=None, trust="trusted") -> State:
    return State(mode="standard",
                 scope=scope or {"diff_files": ["src/a.py"], "untracked": [],
                                 "stats": {"files": 1, "insertions": 1, "deletions": 0}},
                 overlay={"path": ".claude/code-review-p.md", "trust": trust,
                          "trust_basis": "", "heuristic_gates": []})


def test_信任判定_被审diff触及overlay即untrusted():
    trust, basis = overlay.trust_of(
        {"diff_files": ["src/a.py", ".claude/code-review-p.md"], "untracked": []},
        ".claude/code-review-p.md")
    assert trust == "untrusted"
    trust, _ = overlay.trust_of({"diff_files": ["src/a.py"], "untracked": []},
                                ".claude/code-review-p.md")
    assert trust == "trusted"
    trust, _ = overlay.trust_of({"diff_files": [], "untracked": [".claude/code-review-p.md"]},
                                ".claude/code-review-p.md")
    assert trust == "untrusted"
    assert overlay.trust_of({"diff_files": []}, None)[0] == "none"


def test_信任判定_repo模式一律untrusted():
    trust, _ = overlay.trust_of({"diff_files": ["a.py"], "repo_mode": True},
                                ".claude/code-review-p.md")
    assert trust == "untrusted"


def test_应用_trusted排除项生效(tmp_path):
    from crp import scope as scope_mod
    repo = RepoBuilder.init(tmp_path)
    repo.commit({"a.py": "1\n", "gen/x.py": "2\n", "keep.py": "3\n"}, "init")
    repo.edit({"a.py": "2\n", "gen/x.py": "3\n", "keep.py": "4\n"})
    sc = scope_mod.resolve_scope(repo.root)
    st = _state(scope=sc, trust="trusted")
    data = {"red_lines": [{"id": "R1", "rule": "r", "severity": "P2"}],
            "gates": [{"command": "make check", "semantics": "read_only"}],
            "exclusions": ["gen/"], "invariants": ["inv"]}
    assert overlay.validate_overlay_file(data) == []
    summary = overlay.apply_overlay(st, data)
    assert summary["exclusions_applied"] == ["gen/"]
    assert "gen/x.py" not in st.scope["diff_files"] and "keep.py" in st.scope["diff_files"]
    # c041: trusted overlay 的只读门禁也不再 auto —— 'trusted' 是位置事实
    # 不是授权, 一律 unset 待 A4 批量确认(semantics 标签由 overlay 作者自述)
    assert st.overlay["gates"][0]["approval"] == "unset"


def test_应用_untrusted排除项挂起A3(tmp_path):
    from crp import scope as scope_mod
    repo = RepoBuilder.init(tmp_path)
    repo.commit({"a.py": "1\n", "evil.py": "2\n"}, "init")
    repo.edit({"a.py": "2\n", "evil.py": "3\n"})
    sc = scope_mod.resolve_scope(repo.root)
    st = _state(scope=sc, trust="untrusted")
    data = {"red_lines": [], "gates": [{"command": "make check", "semantics": "read_only"}],
            "exclusions": ["evil.py"], "invariants": []}
    summary = overlay.apply_overlay(st, data)
    assert summary["exclusions_suspended"] == ["evil.py"]
    assert "evil.py" in st.scope["diff_files"]  # 未被排除 —— 恶意文件仍在审查范围
    # untrusted overlay 门禁待用户确认
    assert st.overlay["gates"][0]["approval"] == "unset"
    assert overlay.pending_gate_question_count(st) == 1


def test_应用_写语义门禁一律denied():
    st = _state(trust="trusted")
    data = {"red_lines": [], "gates": [{"command": "make gen", "semantics": "write"}],
            "exclusions": [], "invariants": []}
    overlay.apply_overlay(st, data)
    assert st.overlay["gates"][0]["approval"] == "denied"
    assert overlay.pending_gate_question_count(st) == 0


def test_应用_漏分类的启发式候选补回待确认():
    st = _state(trust="none")
    st.overlay["path"] = None
    st.overlay["heuristic_gates"] = ["make lint", "make test"]
    data = {"red_lines": [], "gates": [{"command": "make lint", "semantics": "read_only"}],
            "exclusions": [], "invariants": []}
    overlay.apply_overlay(st, data)
    by_cmd = {g["command"]: g for g in st.overlay["gates"]}
    assert by_cmd["make lint"]["source"] == "heuristic" and by_cmd["make lint"]["approval"] == "unset"
    assert by_cmd["make test"]["semantics"] == "unknown" and by_cmd["make test"]["approval"] == "unset"


def test_校验_红线严重度与门禁语义枚举():
    bad = {"red_lines": [{"id": "R1", "rule": "r", "severity": "P9"}],
           "gates": [{"command": "x", "semantics": "sometimes"}],
           "exclusions": "not-a-list", "invariants": []}
    errs = overlay.validate_overlay_file(bad)
    assert any("severity" in e for e in errs)
    assert any("semantics" in e for e in errs)
    assert any("exclusions" in e for e in errs)


def test_启发式门禁扫描(tmp_path):
    repo = RepoBuilder.init(tmp_path)
    repo.commit({"Makefile": "check:\n\techo ok\n\nlint:\n\teslint .\n\ndeploy:\n\techo up\n",
                 "package.json": '{"scripts": {"test:unit": "jest", "build": "vite"}}'},
                "init")
    repo.edit({"a.py": "1\n"})
    gates = overlay.heuristic_gates(repo.root)
    assert "make check" in gates and "make lint" in gates and "npm run test:unit" in gates
    assert not any("deploy" in g or "build" in g for g in gates)  # 非门禁名不进


# ── P3 回修回归: 启发式门禁扫描(2026-11-02 全面审查) ─────────────────
def test_p3_Makefile变量赋值不产门禁(tmp_path):
    """P3 回归: '^([A-Za-z0-9_.-]+):' 同样命中 'TESTS:= …' 变量赋值行 ——
    'make TESTS' 假门禁进入 A4 询问, 批准后 verify 执行必失败。"""
    (tmp_path / "Makefile").write_text(
        "TESTS:= test_a test_b\nCHECK_FLAGS:= -Wall\n"
        "check:\n\techo ok\nlint: build\n\techo lint\n", encoding="utf-8")
    gates = overlay.heuristic_gates(tmp_path)
    assert "make check" in gates and "make lint" in gates
    assert "make TESTS" not in gates and "make CHECK_FLAGS" not in gates


def test_p3_justfile产just命令而非make(tmp_path):
    """P3 回归: justfile 套 Makefile 正则并加 'make ' 前缀 —— just 仓库登记的
    'make check' 门禁根本不存在, 依赖门禁的 finding 永远 'unverified by gate'。"""
    (tmp_path / "justfile").write_text(
        "check:\n echo ok\nlint:\n echo lint\n", encoding="utf-8")
    gates = overlay.heuristic_gates(tmp_path)
    assert gates == ["just check", "just lint"]
