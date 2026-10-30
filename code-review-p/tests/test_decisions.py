"""路由矩阵: 模式×phase → 期望 next_step.type; 扫描计划; do_work 装配; 轮次收敛。"""

from __future__ import annotations

from pathlib import Path

from crp import config, decisions, verify
from crp.models import State

CTX = decisions.RouteCtx(run_dir=Path("/tmp/fake-run"))


def _state(mode="standard", phase="brief", **kw) -> State:
    params = config.mode_params(mode)
    st = State(mode=mode, phase=phase,
               sweep_plan={"angles": decisions.sweep_angles(mode, {}),
                           "keep": params["keep"], "cap": params["cap"],
                           "dry_required": params["dry_required"],
                           "max_rounds": params["max_rounds"],
                           "max_agents": 60},
               cursor={"round": 0, "round_kind": "sweep", "awaiting": "brief",
                       "pending_angles": [], "pending_verify": [], "lens_requests": [],
                       "dry_streak": 0},
               **kw)
    return st


def test_扫描计划_模式角度集():
    plan = decisions.build_sweep_plan("quick", {}, {"stats": {"files": 2, "insertions": 10, "deletions": 0}})
    assert plan["angles"] == ["A", "B", "C"]
    assert plan["cap"] == 4 and plan["keep"] == config.KEEP_NONE

    plan = decisions.build_sweep_plan("quick", {"content": {"red_lines": [{"id": "R1"}]}}, {"stats": {}})
    assert "K" in plan["angles"]  # 红线非空追加 K

    plan = decisions.build_sweep_plan("deep", {}, {"stats": {}})
    assert plan["angles"] == list(config.FULL_ANGLE_KEYS) and plan["cap"] == 20

    plan = decisions.build_sweep_plan("standard", {}, {"stats": {"files": 20, "insertions": 0, "deletions": 0}})
    assert plan["angles"] == list(config.STANDARD_FANOUT_ANGLES)  # 大 diff 收敛热点角度
    assert plan["fanout_recommended"] is True


def test_路由矩阵_各phase():
    cases = [
        ("overlay", "do_work"), ("brief", "do_work"), ("carrier", "do_work"),
        ("sweep", "do_work"), ("verify", "do_work"), ("report", "do_work"),
        ("done", "finish"), ("aborted", "abort"), ("scope", "abort"),
    ]
    for phase, expected in cases:
        st = _state(phase=phase)
        if phase == "verify":  # verify 需有待验证批次; 空批次是自愈回路由器的防御路径
            st.candidates = [{"id": "c001", "file": "a.py", "line": 1, "summary": "s",
                              "failure_scenario": "f", "severity": "P2"}]
            st.cursor["pending_verify"] = [{"id": "c001", "lenses": ["correctness"]}]
        if phase == "sweep":
            st.cursor["pending_angles"] = ["A"]
        d = decisions.decide_next(st, CTX)
        assert d.next_type == expected, f"phase={phase} -> {d.next_type}, 期望 {expected}"


def test_verify空批次自愈回路由器():
    d = decisions.decide_next(_state(phase="verify"), CTX)
    assert d.next_type == "run_script" and d.exit_code == config.EXIT_STATE


def test_do_work_装配_output与submit():
    d = decisions.decide_next(_state(phase="brief"), CTX)
    ns = {"type": d.next_type}
    assert d.output and "02-brief.md" in d.output
    assert d.submit is not None and d.submit.script == config.ENTRY_FILENAME
    assert d.submit.params[:2] == ["submit", "--kind"]
    assert d.submit.params[2] == "brief"
    assert d.submit.params[-2:] == ["--run-dir", str(CTX.run_dir)]


def test_gates_无待确认直接过carrier():
    st = _state(phase="gates")
    st.overlay = {"gates": [{"command": "make check", "source": "overlay",
                             "semantics": "read_only", "approval": "auto"}]}
    d = decisions.decide_next(st, CTX)
    assert d.next_type == "do_work"  # 无 unset 门禁 → carrier do_work


def test_gates_有待确认发出ask_user():
    st = _state(phase="gates")
    st.overlay = {"gates": [
        {"command": "make check", "source": "heuristic", "semantics": "read_only", "approval": "unset"},
        {"command": "make gen", "source": "overlay", "semantics": "write", "approval": "denied"},
    ]}
    d = decisions.decide_next(st, CTX)
    assert d.next_type == "ask_user"
    assert d.question and "make check" in d.question and "make gen" not in d.question
    options = {o.option for o in d.resume}
    assert options == {"approve_all", "skip_all", "choice"}


def test_gap_digest取自台账而非记忆():
    st = _state(phase="sweep")
    st.cursor.update(round_kind="gap", round=1)
    st.candidates = [
        {"id": "c001", "file": "a.py", "line": 1, "summary": "s1", "kept": True},
        {"id": "c002", "file": "b.py", "line": 2, "summary": "s2", "kept": False},
        {"id": "c003", "file": "c.py", "line": 3, "summary": "s3", "kept": True},
    ]
    d = decisions.decide_next(st, CTX)
    assert d.next_type == "do_work"
    assert "a.py:1 s1" in d.context["digest"]
    assert "b.py:2" not in d.context["digest"]  # 非 kept 不进 digest
    assert "c.py:3 s3" in d.context["digest"]


def test_verify批次上下文():
    st = _state(phase="verify")
    st.candidates = [{"id": "c001", "file": "a.py", "line": 9, "summary": "s",
                      "failure_scenario": "f", "severity": "P1"}]
    st.cursor["pending_verify"] = [{"id": "c001", "lenses": list(config.LENSES)}]
    d = decisions.decide_next(st, CTX)
    assert d.context["batch"][0]["lenses"] == "correctness/reproduction/security"
    assert d.context["remaining"] == 0


def test_轮次收敛_深模式两连dry收口():
    st = _state(mode="deep")
    st.cursor["round"] = 2
    st.accounting["round_history"] = [{"round": 2, "kind": "gap", "fresh": 0, "dup": 0,
                                       "kept_after_verify": 0, "dry": True}]
    st.cursor["dry_streak"] = 2
    assert verify.finalize_round(st) == "report"


def test_轮次达上限强制收口():
    st = _state(mode="deep")
    st.cursor["round"] = 4
    st.cursor["dry_streak"] = 0
    assert verify.finalize_round(st) == "report"


def test_未收敛继续gap():
    st = _state(mode="deep")
    st.cursor["round"] = 1
    st.accounting["round_history"] = [{"round": 1, "kind": "sweep", "fresh": 5, "dup": 0,
                                       "kept_after_verify": 3, "dry": False}]
    st.cursor["dry_streak"] = 0
    assert verify.finalize_round(st) == "gap"


def test_quick无gap诉求():
    st = _state(mode="quick")
    st.cursor["round"] = 1
    st.accounting["round_history"] = [{"round": 1, "kind": "sweep", "fresh": 3, "dup": 0,
                                       "kept_after_verify": 3, "dry": False}]
    assert verify.finalize_round(st) == "report"
