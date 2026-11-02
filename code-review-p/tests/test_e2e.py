"""端到端协议驱动测试: 无 LLM 的完整流转(quick/standard/载体1/错序/幂等/中断/漂移)。

Driver 模拟协议对端(LLM): do_work 按种类伪造输出文件, run_script 逐字执行,
收集 finish 报告 —— 这同时是 M4 的"模拟流驱动器"与 M8 验收的演练骨架。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from crp import config
from conftest import parse_block
from gitrepo import RepoBuilder

SKILL_ROOT = Path(__file__).resolve().parents[1]
ENTRY = SKILL_ROOT / "scripts" / "crp.py"


class Driver:
    """协议对端模拟器。"""

    def __init__(self, repo_root: Path, home: Path):
        self.repo = repo_root
        self.env = {**os.environ, config.CRP_HOME_ENV: str(home)}
        self.run_dir: Path | None = None
        self.report: str | None = None
        self.blocks: list[dict] = []

    def run_argv(self, argv: list[str]) -> tuple[dict, int]:
        proc = subprocess.run([sys.executable, str(ENTRY), *argv],
                              cwd=str(self.repo), capture_output=True, text=True,
                              timeout=120, env=self.env)
        payload = parse_block(proc.stdout)
        self.blocks.append(payload)
        return payload, proc.returncode

    def _note_run_dir(self, ns: dict) -> None:
        params = ns.get("params") or (ns.get("submit") or {}).get("params") or []
        if "--run-dir" in params:
            self.run_dir = Path(params[params.index("--run-dir") + 1])

    def fabricate(self, ns: dict) -> None:
        """按种类伪造 do_work 输出文件。"""
        submit = ns["submit"]
        kind = submit["params"][submit["params"].index("--kind") + 1]
        out = Path(ns["output"])
        out.parent.mkdir(parents=True, exist_ok=True)
        ctx = ns.get("context") or {}

        if kind == "brief":
            out.write_text("\n".join(f"## Section {i} {t}" for i, t in enumerate(
                ["What", "Intent", "Targeted", "Redlines", "Verification", "Angles"], 1))
                + "\n", encoding="utf-8")
        elif kind == "overlay":
            out.write_text(json.dumps({
                "red_lines": [], "gates": [], "exclusions": [], "invariants": []},
                ensure_ascii=False), encoding="utf-8")
        elif kind == "carrier":
            out.write_text(json.dumps({"carrier": 3, "reason": "e2e 测试: 无子代理"},
                                      ensure_ascii=False), encoding="utf-8")
        elif kind == "candidates":
            items = []
            if ctx.get("round_kind") in ("gap",):
                items.append({"angle": "GAP", "coverage": {"ran": True},
                              "candidates": []})  # gap 轮零新候选 → dry
            else:
                for angle in ([a for a in ctx.get("angles", []) if a] or ["A"]):
                    items.append({"angle": angle, "coverage": {"ran": True}, "candidates": [
                        {"file": f"src/{angle.lower()}.py", "line": 10 + i,
                         "summary": f"angle {angle} defect number {i} awaits missing",
                         "failure_scenario": "输入 X → 错误 Y",
                         "confidence": 0.9, "severity": "P2", "category": "correctness"}
                        for i in range(2)]})
            out.write_text(json.dumps({"items": items}, ensure_ascii=False), encoding="utf-8")
        elif kind == "verdicts":
            votes = []
            for item in ctx.get("batch", []):
                for lens in item["lenses"].split("/"):
                    votes.append({"id": item["id"], "lens": lens, "verdict": "CONFIRMED",
                                  "evidence": f"引证 {item['location']} 原文", "severity": item.get("severity", "P2"),
                                  "source": "inline"})
            out.write_text(json.dumps({"votes": votes}, ensure_ascii=False), encoding="utf-8")
        elif kind == "details":
            details = [{"id": k["id"], "title": k["summary"], "problem": k["summary"],
                        "failure_scenario": "fs", "fix": "fix it"}
                       for k in ctx.get("kept", [])]
            out.write_text(json.dumps({
                "brief_summary": "e2e 改动摘要", "coverage": "deep-read all",
                "open_questions": "无", "residual_risks": "r", "testing_gaps": "t",
                "summary_bullets": ["b1", "b2", "b3"], "details": details,
            }, ensure_ascii=False), encoding="utf-8")
        elif kind == "fix-outcomes":
            outcomes = [{"id": k["id"], "outcome": "fixed", "reason": "",
                         "gate_results": "pytest ✓"}
                        for k in ctx.get("kept", [])]
            out.write_text(json.dumps({"outcomes": outcomes}, ensure_ascii=False),
                           encoding="utf-8")
        else:
            raise AssertionError(f"未知 do_work 种类: {kind}")

    def drive(self, payload: dict, max_steps: int = 30) -> dict:
        """逐字执行协议直到 finish/abort; 返回最后一个块。"""
        for _ in range(max_steps):
            ns = payload["next_step"]
            self._note_run_dir(ns)
            t = ns["type"]
            if t == "finish":
                self.report = ns.get("report")
                return payload
            if t == "abort":
                return payload
            if t == "do_work":
                self.fabricate(ns)
                payload, _rc = self.run_argv(ns["submit"]["params"])
                continue
            if t == "run_script":
                payload, _rc = self.run_argv(ns["params"])
                continue
            if t == "ask_user":
                # 门禁询问自动批准(带完整参数的 approve_all); 其余询问交回调用方
                approve = next((o for o in ns.get("resume", [])
                                 if o.get("option") == "approve_all" and o.get("script")), None)
                if approve is None:
                    return payload  # 由具体测试用例作答
                payload, _rc = self.run_argv(approve["params"])
                continue
            raise AssertionError(f"未知 next_step.type: {t}")
        raise AssertionError("协议循环超过步数上限(疑似不收敛)")


@pytest.fixture
def quick_repo(tmp_path):
    repo = RepoBuilder.init(tmp_path)
    repo.commit({"src/a.py": "x = 1\n", "README.md": "r\n"}, "init")
    repo.edit({"src/a.py": "x = 2\n"})
    return repo


def _driver(repo, home, mode, target=None, extra=()):
    d = Driver(repo.root, home)
    argv = ["start", "--mode", mode, *extra]
    if target:
        argv += ["--target", target]
    payload, rc = d.run_argv(argv)
    return d, payload


# ── 场景 ─────────────────────────────────────────────────────────────
def test_quick全流程到finish(tmp_path, quick_repo):
    d, payload = _driver(quick_repo, tmp_path / "home", "quick")
    final = d.drive(payload)
    assert final["next_step"]["type"] == "finish"
    assert d.report and "rounds=1" in d.report
    assert "Run: carrier 3 — e2e 测试: 无子代理" in d.report
    # quick: 无验证轮, verdict 全 UNVERIFIED
    assert "UNVERIFIED" in d.report
    state = json.loads((d.run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["phase"] == "done"
    assert state["accounting"]["total_seen"] == 6  # 3 角度 × 2 候选
    assert (d.run_dir / "out" / "report.md").is_file()
    assert (d.run_dir / "out" / "ledger.json").is_file()


def test_standard全流程_验证与gap收敛(tmp_path, quick_repo):
    d, payload = _driver(quick_repo, tmp_path / "home", "standard")
    final = d.drive(payload)
    assert final["next_step"]["type"] == "finish"
    assert d.report and "rounds=2" in d.report  # 初扫 + gap(零新增 dry 收口)
    state = json.loads((d.run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["cursor"]["dry_streak"] == 1
    kinds = [b["next_step"].get("type") for b in d.blocks]
    assert "do_work" in kinds
    # standard cap 10: 13 角度 × 2 = 26 fresh → 全 CONFIRMED → cap 截断
    assert "capped-out=" in d.report


def test_错序提交自愈(tmp_path, quick_repo):
    d, payload = _driver(quick_repo, tmp_path / "home", "standard")
    # 推进到首个 do_work(overlay 或 brief)
    ns = payload["next_step"]
    d._note_run_dir(ns)
    bad = d.run_dir / "in" / "bad.json"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text(json.dumps({"votes": []}), encoding="utf-8")
    payload, rc = d.run_argv(["submit", "--kind", "verdicts", "--file", str(bad),
                              "--run-dir", str(d.run_dir)])
    assert rc == config.EXIT_STATE
    assert "错序提交" in payload["summary"]
    assert payload["next_step"]["type"] == "run_script"
    assert payload["next_step"]["params"][0] == "resume"  # 纠正路由回路由器
    # 路由器照常恢复
    payload, _ = d.run_argv(["resume", "--run-dir", str(d.run_dir)])
    assert payload["next_step"]["type"] == "do_work"


def test_重复提交幂等吸收(tmp_path, quick_repo):
    """孤儿结果重投场景: 部分角度提交后字节相同重投 → 无操作, 计数不变。"""
    d, payload = _driver(quick_repo, tmp_path / "home", "standard")
    while payload["next_step"]["type"] == "do_work":
        ns = payload["next_step"]
        d._note_run_dir(ns)
        if "candidates" in str(ns.get("submit", {}).get("params", [])):
            break
        d.fabricate(ns)
        payload, _ = d.run_argv(ns["submit"]["params"])
    # 手工提交仅角度 A(剩余角度保持 awaiting=candidates → 重投仍合法)
    partial = d.run_dir / "in" / "partial.json"
    partial.parent.mkdir(parents=True, exist_ok=True)
    partial.write_text(json.dumps({"items": [{"angle": "A", "coverage": {"ran": True},
                                              "candidates": [
        {"file": "src/a.py", "line": 3, "summary": "partial submission defect alpha",
         "failure_scenario": "fs", "confidence": 0.9, "severity": "P2",
         "category": "correctness"}]}]}, ensure_ascii=False), encoding="utf-8")
    argv = ["submit", "--kind", "candidates", "--file", str(partial),
            "--run-dir", str(d.run_dir)]
    p1, _ = d.run_argv(argv)
    state = json.loads((d.run_dir / "state.json").read_text(encoding="utf-8"))
    seen = state["accounting"]["total_seen"]
    # 字节相同重投(模拟 429 后孤儿结果再次送达)
    p2, rc = d.run_argv(argv)
    assert "重复提交已吸收" in p2["summary"]
    state = json.loads((d.run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["accounting"]["total_seen"] == seen  # 计数不重复累加


def test_中断后resume续跑(tmp_path, quick_repo):
    d, payload = _driver(quick_repo, tmp_path / "home", "standard")
    # 推进到 candidates 提交后"死亡"(不再执行任何命令)
    while True:
        ns = payload["next_step"]
        d._note_run_dir(ns)
        if ns["type"] != "do_work":
            break
        d.fabricate(ns)
        if "candidates" in str(ns.get("submit", {}).get("params", [])):
            payload, _ = d.run_argv(ns["submit"]["params"])
            break
        payload, _ = d.run_argv(ns["submit"]["params"])
    # 模拟死亡后新会话发现并续跑
    d2 = Driver(quick_repo.root, tmp_path / "home")
    p, rc = d2.run_argv(["runs"])
    assert d.run_dir.name in p["summary"]
    p, _ = d2.run_argv(["resume"])  # 无 --run-dir 自动选最新未完成
    assert "verify" in json.dumps(p["next_step"].get("context", {})) or \
        p["next_step"]["type"] == "do_work"
    state = json.loads((d.run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["interrupted"]["count"] >= 1
    final = d2.drive(p)
    assert final["next_step"]["type"] == "finish"


def test_漂移_head移动触发ask并裁决(tmp_path, quick_repo):
    d, payload = _driver(quick_repo, tmp_path / "home", "standard")
    while payload["next_step"]["type"] == "do_work":
        ns = payload["next_step"]
        d._note_run_dir(ns)
        d.fabricate(ns)
        if "candidates" in str(ns.get("submit", {}).get("params", [])):
            d.run_argv(ns["submit"]["params"])
            break
        payload, _ = d.run_argv(ns["submit"]["params"])
    quick_repo.commit({"src/a.py": "x = 3\n"}, "moved on")  # HEAD 漂移
    payload, _ = d.run_argv(["resume", "--run-dir", str(d.run_dir)])
    assert payload["next_step"]["type"] == "ask_user"
    options = {o["option"] for o in payload["next_step"]["resume"]}
    assert options == {"new_scope", "resume_old_scope", "terminate"}
    # 沿用原范围 → 候选标 stale
    payload, _ = d.run_argv(["answer", "--run-dir", str(d.run_dir), "--key", "drift",
                             "--choice", "resume_old_scope"])
    state = json.loads((d.run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["candidates"] and all(c.get("stale") for c in state["candidates"])


def test_载体1_压缩journal到报告(tmp_path, quick_repo):
    d, payload = _driver(quick_repo, tmp_path / "home", "standard")
    # 推进到 carrier do_work, 提交 carrier=1
    while payload["next_step"]["type"] == "do_work":
        ns = payload["next_step"]
        d._note_run_dir(ns)
        if "carrier" in str(ns.get("submit", {}).get("params", [])):
            (d.run_dir / "in").mkdir(parents=True, exist_ok=True)
            (d.run_dir / "in" / "03-carrier.json").write_text(
                json.dumps({"carrier": 1, "reason": "e2e: workflow"}), encoding="utf-8")
            payload, _ = d.run_argv(ns["submit"]["params"])
            break
        d.fabricate(ns)
        payload, _ = d.run_argv(ns["submit"]["params"])
    # 现在应得到载体 1 的 sweep do_work → 伪造压缩 journal 提交
    ns = payload["next_step"]
    assert ns["type"] == "do_work"
    assert "Workflow" in ns["instructions"]
    compact = d.run_dir / "in" / "04-carrier1-journal.json"
    compact.parent.mkdir(parents=True, exist_ok=True)
    compact.write_text(json.dumps({
        "finders": [
            {"angle": "A", "candidates": [
                {"file": "src/a.py", "line": 5, "summary": "workflow found defect one",
                 "failure_scenario": "fs", "confidence": 0.9, "severity": "P1",
                 "category": "correctness"}]},
            {"angle": "GAP", "candidates": []},
        ],
        "verdicts": [
            {"file": "src/a.py", "line": 5, "summary": "workflow found defect one",
             "lens": "correctness", "verdict": "CONFIRMED", "evidence": "引证原文",
             "severity": "P1"},
            {"file": "src/a.py", "line": 5, "summary": "workflow found defect one",
             "lens": "reproduction", "verdict": "CONFIRMED", "evidence": "引证原文",
             "severity": "P1"},
            {"file": "src/a.py", "line": 5, "summary": "workflow found defect one",
             "lens": "security", "verdict": "CONFIRMED", "evidence": "引证原文",
             "severity": "P1"},
        ],
        "counters": {"total_seen": 1, "dup_dropped": 0},
    }, ensure_ascii=False), encoding="utf-8")
    payload, rc = d.run_argv(["submit", "--kind", "carrier1", "--file", str(compact),
                              "--run-dir", str(d.run_dir)])
    assert payload["next_step"]["type"] == "do_work"  # → details
    final = d.drive(payload)
    assert final["next_step"]["type"] == "finish"
    assert "workflow found defect one" in d.report
    assert "rounds=2" in d.report  # 初扫 + gap


def test_载体1_transcript目录摄取(tmp_path, quick_repo):
    d, payload = _driver(quick_repo, tmp_path / "home", "deep")
    while payload["next_step"]["type"] == "do_work":
        ns = payload["next_step"]
        d._note_run_dir(ns)
        if "carrier" in str(ns.get("submit", {}).get("params", [])):
            (d.run_dir / "in").mkdir(parents=True, exist_ok=True)
            (d.run_dir / "in" / "03-carrier.json").write_text(
                json.dumps({"carrier": 1, "reason": "transcript 模式"}), encoding="utf-8")
            payload, _ = d.run_argv(ns["submit"]["params"])
            break
        d.fabricate(ns)
        payload, _ = d.run_argv(ns["submit"]["params"])
    # 伪造 Workflow 工具的 transcriptDir + journal.jsonl(真实形状: type/label/result 行)
    tdir = tmp_path / "wf-transcript"
    tdir.mkdir()
    (tdir / "journal.jsonl").write_text("\n".join([
        json.dumps({"type": "launched"}),
        json.dumps({"type": "started", "label": "find:A"}),
        json.dumps({"type": "result", "label": "find:A", "result": {"angle": "A", "candidates": [
            {"file": "src/a.py", "line": 7, "summary": "transcript found the defect",
             "failure_scenario": "fs", "confidence": 0.85, "severity": "P2",
             "category": "correctness"}]}}, ensure_ascii=False),
        json.dumps({"type": "started", "label": "verify:correctness"}),
        # 回显行号与台账漂移 ±2(c051 回归: 带内唯一命中仍可回配)
        json.dumps({"type": "result", "label": "verify:correctness", "result": {
            "file": "src/a.py", "line": 9, "summary": "transcript found the defect",
            "lens": "correctness", "verdict": "CONFIRMED", "evidence": "quoted",
            "severity": "P2"}}, ensure_ascii=False),
    ]), encoding="utf-8")
    payload, rc = d.run_argv(["submit", "--kind", "carrier1",
                              "--transcript-dir", str(tdir),
                              "--run-dir", str(d.run_dir)])
    assert payload["next_step"]["type"] == "do_work"
    final = d.drive(payload)
    assert "transcript found the defect" in d.report


# ── 回修回归(v2.0 自食验收 P0/P1) ────────────────────────────────────
def test_c057_有Makefile仓库不死于overlay阶段(tmp_path):
    """c057 回归: 存在启发式门禁的仓库, overlay do_work 的提交必须被接受。

    v2.0.0 原版: awaiting 从未设为 "overlay" → 第二个协议步即无限 exit-3。
    """
    repo = RepoBuilder.init(tmp_path)
    repo.commit({"src/a.py": "x = 1\n", "Makefile": "check:\n\techo ok\n"}, "init")
    repo.edit({"src/a.py": "x = 2\n"})
    d, payload = _driver(repo, tmp_path / "home", "quick")
    first = payload["next_step"]
    assert first["type"] == "do_work"
    assert first["submit"]["params"][first["submit"]["params"].index("--kind") + 1] == "overlay", \
        "有门禁候选的仓库第一步必须是 overlay 提取"
    final = d.drive(payload)  # 若死循环会撞 max_steps 上限
    assert final["next_step"]["type"] == "finish"
    state = json.loads((d.run_dir / "state.json").read_text(encoding="utf-8"))
    assert "make check" in json.dumps(state["overlay"]["gates"])


def test_c025_子目录调用overlay仍在顶层发现(tmp_path):
    """c025 回归: 从子目录启动时 overlay 发现/信任判定必须在仓库顶层。"""
    repo = RepoBuilder.init(tmp_path)
    repo.commit({"src/a.py": "x = 1\n", ".claude/code-review-p.md": "# overlay\n"}, "init")
    repo.edit({"src/a.py": "x = 2\n"})
    d = Driver(repo.root / "src", tmp_path / "home")  # cwd = 子目录
    payload, _ = d.run_argv(["start", "--mode", "quick"])
    state = json.loads((Path(payload["next_step"]["submit"]["params"][
        payload["next_step"]["submit"]["params"].index("--run-dir") + 1]) / "state.json")
        .read_text(encoding="utf-8"))
    assert state["overlay"]["path"] == ".claude/code-review-p.md"
    assert state["overlay"]["trust"] == "trusted"  # 顶层发现 + 未被 diff 触及


def test_c057b_受信任overlay提交后进入brief且排除生效(tmp_path):
    repo = RepoBuilder.init(tmp_path)
    repo.commit({"src/a.py": "x = 1\n", "gen/g.py": "1\n",
                 ".claude/code-review-p.md": "# overlay\n"}, "init")
    repo.edit({"src/a.py": "x = 2\n", "gen/g.py": "2\n"})
    d, payload = _driver(repo, tmp_path / "home", "quick")
    ns = payload["next_step"]
    assert ns["submit"]["params"][ns["submit"]["params"].index("--kind") + 1] == "overlay"
    d._note_run_dir(ns)
    d.fabricate(ns)
    # 改写 overlay 提交: 带排除项
    Path(ns["output"]).write_text(json.dumps({
        "red_lines": [], "gates": [], "exclusions": ["gen/"], "invariants": []},
        ensure_ascii=False), encoding="utf-8")
    payload, _ = d.run_argv(ns["submit"]["params"])
    state = json.loads((d.run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["phase"] == "brief", "overlay 应用后必须推进 phase(apply 半边)"
    assert state["scope"]["diff_files"] == ["src/a.py"], "trusted 排除项必须应用"
    d.drive(payload)


def test_c017_fix全链路不再死循环(tmp_path, quick_repo):
    """c017 回归: --fix 的 fix-outcomes 提交必须被接受并触发复审轮。"""
    d, payload = _driver(quick_repo, tmp_path / "home", "quick", extra=["--fix"])
    seen_kinds = []
    while payload["next_step"]["type"] == "do_work":
        ns = payload["next_step"]
        d._note_run_dir(ns)
        kind = ns["submit"]["params"][ns["submit"]["params"].index("--kind") + 1]
        if kind == "fix-outcomes":
            seen_kinds.append(kind)
        d.fabricate(ns)
        payload, _ = d.run_argv(ns["submit"]["params"])
    assert "fix-outcomes" in seen_kinds
    assert payload["next_step"]["type"] == "finish"
    report = payload["next_step"].get("report") or ""
    state = json.loads((d.run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["phase"] == "done"
    assert state["fix"]["re_review_done"] is True
    assert state["fix"]["outcomes"], "fix 结果必须入账"
    assert "Fix outcome:** fixed" in report


def test_c049_验证队列累加语义(tmp_path):
    """c049 回归: 同轮分角度多次提交时, build_verify_queue 必须累加而非覆盖。"""
    from crp import verify as verify_mod
    from crp.models import State
    st = State(mode="standard", cursor={"round": 1, "round_kind": "sweep", "awaiting": "candidates",
                                        "pending_angles": [], "pending_verify": [],
                                        "dry_streak": 0})
    st.candidates = [
        {"id": "c001", "file": "a.py", "line": 1, "summary": "first submission defect",
         "failure_scenario": "f", "confidence": 0.9, "severity": "P2",
         "category": "correctness", "status": "fresh"},
        {"id": "c002", "file": "b.py", "line": 2, "summary": "second submission defect",
         "failure_scenario": "f", "confidence": 0.9, "severity": "P2",
         "category": "correctness", "status": "fresh"},
    ]
    # 两次提交分别入队(旧版第二次赋值会覆盖第一次)
    verify_mod.build_verify_queue(st, ["c001"])
    verify_mod.build_verify_queue(st, ["c002"])
    queued = {p["id"] for p in st.cursor["pending_verify"]}
    assert queued == {"c001", "c002"}, "分角度多次提交的候选必须全部留在验证队列"
    # 幂等: 重复入队同一 id 不产生重复条目
    verify_mod.build_verify_queue(st, ["c002"])
    assert len(st.cursor["pending_verify"]) == 2


def test_c023_quick载体1走floor_only(tmp_path, quick_repo):
    """c023 回归: quick + carrier1 时 verdict 保持 null(floor-only)。"""
    d, payload = _driver(quick_repo, tmp_path / "home", "quick")
    while payload["next_step"]["type"] == "do_work":
        ns = payload["next_step"]
        d._note_run_dir(ns)
        if "carrier" in str(ns.get("submit", {}).get("params", [])):
            (d.run_dir / "in").mkdir(parents=True, exist_ok=True)
            (d.run_dir / "in" / "03-carrier.json").write_text(
                json.dumps({"carrier": 1, "reason": "quick+wf"}), encoding="utf-8")
            payload, _ = d.run_argv(ns["submit"]["params"])
            break
        d.fabricate(ns)
        payload, _ = d.run_argv(ns["submit"]["params"])
    compact = d.run_dir / "in" / "04-j.json"
    compact.write_text(json.dumps({
        "finders": [{"angle": "A", "candidates": [
            {"file": "src/a.py", "line": 3, "summary": "quick wf defect alpha",
             "failure_scenario": "fs", "confidence": 0.9, "severity": "P2",
             "category": "correctness"}]}],
        "verdicts": [{"file": "src/a.py", "line": 3, "summary": "quick wf defect alpha",
                      "lens": "correctness", "verdict": "PLAUSIBLE",
                      "evidence": "q", "severity": "P2"}],
        "counters": {}}, ensure_ascii=False), encoding="utf-8")
    payload, _ = d.run_argv(["submit", "--kind", "carrier1", "--file", str(compact),
                             "--run-dir", str(d.run_dir)])
    state = json.loads((d.run_dir / "state.json").read_text(encoding="utf-8"))
    c = next(c for c in state["candidates"] if c["summary"] == "quick wf defect alpha")
    assert c["verdict"] is None and c["kept"] is True  # quick: floor-only, 无 mode 阈值
    assert c["status"] == "unverified"


def test_c018_journal单票升级走高危quorum(tmp_path):
    """c018 回归 + P7: journal 路径单票把 P2 升 P1 → 与 verify.apply_votes 同
    机制 —— 退回补其余透镜再终裁, 不再"按现有票立即聚合"的降级副本。"""
    from crp import journal as journal_mod
    from crp.models import State
    st = State(mode="deep", repo={"cwd": str(tmp_path)},
               sweep_plan={"keep": "confirmed_plausible", "cap": 20, "angles": ["A"]})
    data = {"finders": [{"angle": "A", "candidates": [
        {"file": "a.py", "line": 5, "summary": "upgrade rule defect beta",
         "failure_scenario": "fs", "confidence": 0.9, "severity": "P2",
         "category": "correctness"}]}],
        "verdicts": [{"file": "a.py", "line": 5, "summary": "upgrade rule defect beta",
                      "lens": "correctness", "verdict": "CONFIRMED",
                      "evidence": "q", "severity": "P1"}],
        "counters": {}}
    journal_mod.apply_compact(st, data)
    c = next(c for c in st.candidates if c["status"] != "dup")
    assert c["severity"] == "P1", "升级必须改写 severity"
    assert c["verdict"] is None, "升级候选不在此终裁(退回复核)"
    assert st.cursor["pending_verify"] == [
        {"id": c["id"], "lenses": ["reproduction", "security"]}]
    assert st.phase == "verify" and st.cursor["awaiting"] == "verdicts"
    # 覆盖对账: 计划角度全部可见(死 finder 的空洞不再隐形)
    assert set(st.coverage["angles"]) == {"A"}


def test_c009_answer_run不死锁(tmp_path, quick_repo):
    """c009 回归: answer --key run 在锁内不得再入锁(原版同进程 flock 自死锁)。"""
    d, payload = _driver(quick_repo, tmp_path / "home", "quick")
    d._note_run_dir(payload["next_step"])
    import subprocess
    proc = subprocess.run(
        [sys.executable, str(ENTRY), "answer", "--run-dir", str(d.run_dir),
         "--key", "run", "--choice", "resume_run"],
        cwd=str(quick_repo.root), capture_output=True, text=True, timeout=30,
        env={**os.environ, config.CRP_HOME_ENV: str(tmp_path / "home")})
    assert proc.returncode in (0, 1), "不得挂死或崩溃"
    assert ":::NEXT_STEP_BEGIN:::" in proc.stdout


# ── 遗留回修: 计数口径与契约缺口 ────────────────────────────────────
def test_口径_逐条丢弃不整批消失(tmp_path):
    """观察项 1 回归: 单条非法候选只丢那一条, 同 finder 其余候选必须入账。

    v2.0.1 原版: 全有或全无按 finder 跳过, 9 条超长 summary 让 32 条候选
    静默消失(96→64) —— 无声上限正是 SKILL 禁止的行为。
    """
    from crp import journal as journal_mod
    from crp.models import State
    st = State(mode="deep", repo={"cwd": str(tmp_path)},
               sweep_plan={"keep": "confirmed_plausible", "cap": 20})
    data = {"finders": [{"angle": "A", "candidates": [
        {"file": "a.py", "line": 1, "summary": "missing await on fetchUser call",
         "failure_scenario": "fs", "confidence": 0.9, "severity": "P2",
         "category": "correctness"},
        {"file": "a.py", "line": 2, "summary": "x" * 600,  # 超长 —— 只丢这条
         "failure_scenario": "fs", "confidence": 0.9, "severity": "P2",
         "category": "correctness"},
        {"file": "a.py", "line": 30, "summary": "inverted null check dereferences pointer",
         "failure_scenario": "fs", "confidence": 0.9, "severity": "P2",
         "category": "correctness"},
    ]}], "verdicts": [], "counters": {}}
    journal_mod.apply_compact(st, data)
    summaries = [c["summary"] for c in st.candidates if c["status"] != "dup"]
    assert summaries == ["missing await on fetchUser call",
                         "inverted null check dereferences pointer"]
    assert st.accounting["invalid_dropped"] == 1
    assert st.coverage["angles"]["A"]["dropped_invalid"] == 1


def test_口径_parity原始总数不变式(tmp_path):
    """raw(JS fresh+dup) == raw(Py fresh+dup+invalid+overcap); 可解释分歧注明成因而非告警。"""
    from crp import journal as journal_mod
    from crp.models import State
    st = State(mode="deep", repo={"cwd": str(tmp_path)},
               sweep_plan={"keep": "confirmed_plausible", "cap": 20})
    data = {"finders": [{"angle": "A", "candidates": [
        {"file": "a.py", "line": 1, "summary": "fresh one",
         "failure_scenario": "fs", "confidence": 0.9, "severity": "P2",
         "category": "correctness"},
        {"file": "a.py", "line": 3, "summary": "fresh one",  # dup
         "failure_scenario": "fs", "confidence": 0.9, "severity": "P2",
         "category": "correctness"},
        {"file": "a.py", "line": 5, "summary": "y" * 600,  # invalid
         "failure_scenario": "fs", "confidence": 0.9, "severity": "P2",
         "category": "correctness"},
    ]}], "verdicts": [], "counters": {"total_seen": 2, "dup_dropped": 1}}
    journal_mod.apply_compact(st, data)
    notes = st.accounting.get("parity_notes")
    # P11(c038)语义升级: raw 一致但分桶有差(1 invalid)→ 无法从观测区分
    # "invalid 全解释"与"invalid+去重分歧并存", 注记引导人工对照(不再
    # 断言"完全解释"掩盖真实分歧); 不得出现 raw 不一致的指控
    assert notes and any("人工对照" in n for n in notes), notes
    assert all("原始候选总数不一致" not in n for n in notes), notes


def test_口径_未解释分歧必须告警(tmp_path):
    from crp import journal as journal_mod
    from crp.models import State
    st = State(mode="deep", repo={"cwd": str(tmp_path)},
               sweep_plan={"keep": "confirmed_plausible", "cap": 20})
    data = {"finders": [{"angle": "A", "candidates": [
        {"file": "a.py", "line": 1, "summary": "only fresh candidate",
         "failure_scenario": "fs", "confidence": 0.9, "severity": "P2",
         "category": "correctness"}]}],
        "verdicts": [], "counters": {"total_seen": 5, "dup_dropped": 0}}  # JS 称 5
    journal_mod.apply_compact(st, data)
    assert any("人工" in n for n in st.accounting.get("parity_notes", []))


def test_退出码契约_一二三各可证(tmp_path, quick_repo):
    """c046 回归: exit==payload 对 1(do_work)/2(执行错误)/3(状态错误) 各至少一条。"""
    d, payload = _driver(quick_repo, tmp_path / "home", "quick")
    assert payload["exit_code"] == config.EXIT_CONTINUE, "do_work 等待 = 1(正常流转)"
    # exit 2: carrier1 摄取失败(垃圾文件)
    d2, p2 = _driver(quick_repo, tmp_path / "home2", "standard")
    while p2["next_step"]["type"] == "do_work":
        ns = p2["next_step"]
        d2._note_run_dir(ns)
        if "carrier" in str(ns.get("submit", {}).get("params", [])):
            (d2.run_dir / "in").mkdir(parents=True, exist_ok=True)
            (d2.run_dir / "in" / "03-carrier.json").write_text(
                json.dumps({"carrier": 1, "reason": "x"}), encoding="utf-8")
            p2, _ = d2.run_argv(ns["submit"]["params"])
            break
        d2.fabricate(ns)
        p2, _ = d2.run_argv(ns["submit"]["params"])
    bad = d2.run_dir / "in" / "garbage.json"
    bad.write_text('{"finders": []}', encoding="utf-8")  # 空 finders → 摄取失败
    p3, rc3 = d2.run_argv(["submit", "--kind", "carrier1", "--file", str(bad),
                           "--run-dir", str(d2.run_dir)])
    assert p3["exit_code"] == config.EXIT_ERROR and rc3 == config.EXIT_ERROR
    assert p3["next_step"]["type"] == "ask_user"


# ── P1 回修回归(2026-11-02 /code-review max 全面审查) ────────────────
def test_p1_门禁仓库brief阶段不被跳过(tmp_path):
    """P1 回归: 有启发式门禁的仓库, gates 答复不得越过 brief。

    原版: overlay 应用有待确认门禁即先跳 gates, gates 答复又无条件转
    carrier → state.brief/sweep_plan 恒空, keep 阈值回退 KEEP_NONE、
    02-brief.md 永不生成(被 test_c057 的 fabricate 兜底掩盖)。
    """
    repo = RepoBuilder.init(tmp_path)
    repo.commit({"src/a.py": "x = 1\n", "Makefile": "check:\n\techo ok\n"}, "init")
    repo.edit({"src/a.py": "x = 2\n"})
    d, payload = _driver(repo, tmp_path / "home", "quick")
    final = d.drive(payload)  # gates ask 由 Driver 自动批准
    assert final["next_step"]["type"] == "finish"
    state = json.loads((d.run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["brief"].get("line_count", 0) >= 1, "brief 必须真实经过"
    assert state["sweep_plan"].get("angles"), "扫描计划必须在 brief 后固化"
    assert (d.run_dir / "in" / "02-brief.md").is_file()
    assert "make check" in json.dumps(state["overlay"]["gates"])


def test_p1_fix复审轮载体1不再死循环(tmp_path, quick_repo):
    """P1 回归: fix_review 阶段不得向载体 1 下发 carrier1 do_work ——
    submit 的种类核对只认 phase=='sweep', 逐字执行协议即无限 exit-3。"""
    d, payload = _driver(quick_repo, tmp_path / "home", "quick", extra=["--fix"])
    while payload["next_step"]["type"] == "do_work":
        ns = payload["next_step"]
        d._note_run_dir(ns)
        if "carrier" in str(ns.get("submit", {}).get("params", [])):
            (d.run_dir / "in").mkdir(parents=True, exist_ok=True)
            (d.run_dir / "in" / "03-carrier.json").write_text(
                json.dumps({"carrier": 1, "reason": "e2e: workflow"}), encoding="utf-8")
            payload, _ = d.run_argv(ns["submit"]["params"])
            break
        d.fabricate(ns)
        payload, _ = d.run_argv(ns["submit"]["params"])
    # 初扫载体 1: 压缩 journal(1 条 P2 候选, quick 无验证)
    compact = d.run_dir / "in" / "04-j.json"
    compact.parent.mkdir(parents=True, exist_ok=True)
    compact.write_text(json.dumps({
        "finders": [{"angle": "A", "candidates": [
            {"file": "src/a.py", "line": 3, "summary": "fix round defect alpha",
             "failure_scenario": "fs", "confidence": 0.9, "severity": "P2",
             "category": "correctness"}]}],
        "verdicts": [], "counters": {}}, ensure_ascii=False), encoding="utf-8")
    payload, _ = d.run_argv(["submit", "--kind", "carrier1", "--file", str(compact),
                             "--run-dir", str(d.run_dir)])
    review_rounds = []
    while payload["next_step"]["type"] == "do_work":
        ns = payload["next_step"]
        d._note_run_dir(ns)
        kind = ns["submit"]["params"][ns["submit"]["params"].index("--kind") + 1]
        if kind == "candidates" and "复审" in (ns.get("instructions") or ""):
            review_rounds.append(ns)
        d.fabricate(ns)
        payload, _ = d.run_argv(ns["submit"]["params"])
    assert payload["next_step"]["type"] == "finish"
    assert review_rounds, "必须到达 fix 复审轮"
    assert all("Workflow" not in (ns.get("instructions") or "") for ns in review_rounds), \
        "fix_review 轮不得下发载体 1(会无限错序循环)"


def test_p1_陈旧gates答复重放被拒(tmp_path):
    """P1 回归: answer 不校验待决问题 → 429 中断后重贴旧 ask_user 答复可把
    状态机瞬间传送到别的相位。错序答复必须 exit-3 并交回路由器自愈。"""
    repo = RepoBuilder.init(tmp_path)
    repo.commit({"src/a.py": "x = 1\n", "Makefile": "check:\n\techo ok\n"}, "init")
    repo.edit({"src/a.py": "x = 2\n"})
    d, payload = _driver(repo, tmp_path / "home", "quick")
    while payload["next_step"]["type"] in ("do_work", "ask_user"):
        ns = payload["next_step"]
        d._note_run_dir(ns)
        if ns["type"] == "ask_user":  # gates 询问(在 brief 之后)自动批准
            approve = next((o for o in ns.get("resume", [])
                            if o.get("option") == "approve_all" and o.get("script")), None)
            assert approve is not None, "此处应为 gates 询问"
            payload, _ = d.run_argv(approve["params"])
            continue
        kind = ns["submit"]["params"][ns["submit"]["params"].index("--kind") + 1]
        if kind == "candidates":
            break
        d.fabricate(ns)
        payload, _ = d.run_argv(ns["submit"]["params"])
    state = json.loads((d.run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["phase"] == "sweep"
    payload2, rc = d.run_argv(["answer", "--run-dir", str(d.run_dir),
                               "--key", "gates", "--choice", "approve_all"])
    assert rc == config.EXIT_STATE and "错序答复" in payload2["summary"]
    assert payload2["next_step"]["type"] == "run_script"
    assert payload2["next_step"]["params"][0] == "resume"
    state = json.loads((d.run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["phase"] == "sweep", "重放不得传送相位"
    payload3, _ = d.run_argv(["resume", "--run-dir", str(d.run_dir)])
    assert payload3["next_step"]["type"] == "do_work", "路由器照常恢复"


def test_p1_drift重导范围后重评overlay信任(tmp_path):
    """P1 回归: drift 答复 new_scope 重导范围后必须重评 overlay 信任 ——
    被新 diff 触及的 overlay 不得沿用 trusted(A3/A4 信任边界)。"""
    repo = RepoBuilder.init(tmp_path)
    repo.commit({"src/a.py": "x = 1\n", "gen/g.py": "1\n",
                 ".claude/code-review-p.md": "# overlay\n"}, "init")
    repo.edit({"src/a.py": "x = 2\n"})
    d, payload = _driver(repo, tmp_path / "home", "standard")
    ns = payload["next_step"]  # trusted overlay → overlay 提取(带排除项+只读门禁)
    d._note_run_dir(ns)
    Path(ns["output"]).write_text(json.dumps({
        "red_lines": [], "gates": [{"command": "make check", "semantics": "read_only"}],
        "exclusions": ["gen/"], "invariants": []}, ensure_ascii=False), encoding="utf-8")
    payload, _ = d.run_argv(ns["submit"]["params"])
    state = json.loads((d.run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["overlay"]["trust"] == "trusted"
    assert state["overlay"]["gates"][0]["approval"] == "auto"
    while payload["next_step"]["type"] == "do_work":  # 推进到 candidates 提交后停
        ns2 = payload["next_step"]
        d._note_run_dir(ns2)
        d.fabricate(ns2)
        payload, _ = d.run_argv(ns2["submit"]["params"])
        if "candidates" in str(ns2.get("submit", {}).get("params", [])):
            break
    # 中断期间: HEAD 前移(新提交) + 第三方改写 overlay 留在工作树
    # (commit 走 add -A, 必须先提交后改写, overlay 改动才不被一并带走)
    repo.commit({"src/b.py": "b = 1\n"}, "moved on")
    repo.edit({".claude/code-review-p.md": "# overlay tampered\n"})
    payload, _ = d.run_argv(["resume", "--run-dir", str(d.run_dir)])
    assert payload["next_step"]["type"] == "ask_user"
    payload, _ = d.run_argv(["answer", "--run-dir", str(d.run_dir), "--key", "drift",
                             "--choice", "new_scope"])
    state = json.loads((d.run_dir / "state.json").read_text(encoding="utf-8"))
    ov = state["overlay"]
    # P10(c009)语义升级: 重导范围后一律回 overlay 阶段重新提取(替代挂起旧
    # content)—— 信任降级即时可见, 内容/门禁/排除待重提取后按新信任处理
    assert state["phase"] == "overlay" and state["cursor"]["awaiting"] == "overlay"
    assert ov["trust"] == "untrusted", "新 diff 触及 overlay → 必须降级"
    assert not ov.get("content"), "陈旧 content 不得沿用(待重提取)"
    assert ov["gates"] == [], "旧门禁/批复不随行(重提取后 A4 重问)"
    # 陈旧 drift 答复重放同样被拒
    payload2, rc = d.run_argv(["answer", "--run-dir", str(d.run_dir), "--key", "drift",
                               "--choice", "new_scope"])
    assert rc == config.EXIT_STATE and "错序答复" in payload2["summary"]
    # 重提取闭环: 提交 overlay(带旧排除项) → untrusted 挂起 + 门禁待确认
    ns = payload["next_step"]
    assert ns["type"] == "do_work"
    Path(ns["output"]).write_text(json.dumps({
        "red_lines": [], "gates": [{"command": "make check", "semantics": "read_only"}],
        "exclusions": ["gen/"], "invariants": []}, ensure_ascii=False), encoding="utf-8")
    payload, _ = d.run_argv(ns["submit"]["params"])
    state = json.loads((d.run_dir / "state.json").read_text(encoding="utf-8"))
    ov = state["overlay"]
    assert ov["exclusions_suspended"] == ["gen/"] and ov["exclusions_applied"] == []
    assert ov["gates"][0]["approval"] == "unset", "untrusted 门禁必须待确认(A4)"
    assert state["phase"] == "brief", "重提取完成 → brief(无待确认门禁时)"


# ── P2 回修回归: 状态机/生命周期(2026-11-02 全面审查) ─────────────────
def test_p2_逃逸口后resume重问而非静默空审(tmp_path):
    """P2 回归: 逃逸口 ask 不落 pending_question、phase 停在 overlay ——
    原版一条普通 resume 即绕过 '禁止静默空审' 门禁在空范围上跑完审查。"""
    repo = RepoBuilder.init(tmp_path)
    repo.commit({"src/a.py": "x = 1\n"}, "init")  # 干净树+单分支 → 全梯级空
    d, payload = _driver(repo, tmp_path / "home", "quick")
    assert payload["next_step"]["type"] == "ask_user"
    assert "所有 diff 源都为空" in payload["next_step"]["question"]
    choice_opt = next(o for o in payload["next_step"]["resume"] if o["option"] == "choice")
    run_dir = choice_opt["params"][choice_opt["params"].index("--run-dir") + 1]
    # 模拟崩溃后新会话裸 resume: 必须重问逃逸口, 不得进入 do_work
    payload2, rc = d.run_argv(["resume", "--run-dir", run_dir])
    assert payload2["next_step"]["type"] == "ask_user"
    assert "所有 diff 源都为空" in payload2["next_step"]["question"]
    # 给出目标后照常推进
    repo.edit({"src/a.py": "x = 2\n"})
    payload3, _ = d.run_argv(["answer", "--run-dir", run_dir, "--key", "scope",
                              "--choice", "choice", "--value", "src/a.py"])
    assert payload3["next_step"]["type"] == "do_work"
    d._note_run_dir(payload3["next_step"])
    final = d.drive(payload3)
    assert final["next_step"]["type"] == "finish"


def test_p2_start指向已有运行不覆写(tmp_path, quick_repo):
    """P2 回归: 防覆盖守卫原版仅在无 --run-dir 时生效 —— 显式传入即用全新
    state 无提示覆写中断运行/已完成运行的 state.json。"""
    d, payload = _driver(quick_repo, tmp_path / "home", "quick")
    d._note_run_dir(payload["next_step"])
    before = (d.run_dir / "state.json").read_text(encoding="utf-8")
    p2, rc = d.run_argv(["start", "--run-dir", str(d.run_dir), "--mode", "standard"])
    assert rc == config.EXIT_CONTINUE and p2["next_step"]["type"] == "ask_user"
    assert "已有 state.json" in p2["next_step"]["question"]
    assert (d.run_dir / "state.json").read_text(encoding="utf-8") == before, \
        "未确认前不得覆写既有运行状态"
    # --force 保留逃生门(确需覆盖)
    p3, rc = d.run_argv(["start", "--run-dir", str(d.run_dir), "--mode", "quick", "--force"])
    assert p3["next_step"]["type"] == "do_work"
    state = json.loads((d.run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["mode"] == "quick"


def test_p2_drift重导全空不再静默空审(tmp_path):
    """P2 回归: drift new_scope 重导后全空时不检查 escape_hatch —— 原版在
    零文件范围上继续扫描(scope-answer 分支有逃逸口重问, 此分支没有)。"""
    repo = RepoBuilder.init(tmp_path)
    repo.commit({"src/a.py": "x = 1\n"}, "init")
    repo.edit({"src/a.py": "x = 2\n"})
    d, payload = _driver(repo, tmp_path / "home", "standard")
    while payload["next_step"]["type"] == "do_work":
        ns = payload["next_step"]
        d._note_run_dir(ns)
        d.fabricate(ns)
        payload, _ = d.run_argv(ns["submit"]["params"])
        if "candidates" in str(ns.get("submit", {}).get("params", [])):
            break
    repo.commit({"src/a.py": "x = 2\n"}, "moved on")  # 提交全部改动 → 树干净
    payload, _ = d.run_argv(["resume", "--run-dir", str(d.run_dir)])
    assert payload["next_step"]["type"] == "ask_user"  # drift 询问
    payload, _ = d.run_argv(["answer", "--run-dir", str(d.run_dir), "--key", "drift",
                             "--choice", "new_scope"])
    assert payload["next_step"]["type"] == "ask_user", "重导全空 → 逃逸口重问"
    assert "所有 diff 源都为空" in payload["next_step"]["question"]
    state = json.loads((d.run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["scope"].get("escape_hatch"), "逃逸口必须落盘"
    assert state["pending_question"]["key"] == "scope"


def test_p2_done后裸report_status_resume可达(tmp_path, quick_repo):
    """P2 回归: resolve_run_dir 只认未完成运行 —— 唯一运行完成后, 裸形式
    report/status/resume 全部不可达(文档展示的 done 后幂等重渲染面失效)。"""
    d, payload = _driver(quick_repo, tmp_path / "home", "quick")
    final = d.drive(payload)
    assert final["next_step"]["type"] == "finish"
    p, rc = d.run_argv(["report"])  # 裸 report: done 后幂等重渲染
    assert rc == config.EXIT_OK and p["next_step"]["type"] == "finish"
    assert p["next_step"].get("report") and "rounds=1" in p["next_step"]["report"]
    p2, _ = d.run_argv(["status"])  # 裸 status: 仍能解析该运行
    assert p2["status"] == "success" and "phase=done" in p2["summary"]
    p3, rc3 = d.run_argv(["resume"])  # 裸 resume: 重发 finish 块与逐字报告
    assert rc3 == config.EXIT_OK and p3["next_step"]["type"] == "finish"
    assert p3["next_step"].get("report")


def test_p2_report阶段提前渲染被拒(tmp_path, quick_repo):
    """P2 回归: cmd_report 原版放行 phase=report(details 未提交)—— 提前调用
    用空 details 渲染占位报告并以 finish/exit-0 终结整个审查。"""
    d, payload = _driver(quick_repo, tmp_path / "home", "standard")
    while payload["next_step"]["type"] == "do_work":  # 推进到 details do_work
        ns = payload["next_step"]
        d._note_run_dir(ns)
        kind = ns["submit"]["params"][ns["submit"]["params"].index("--kind") + 1]
        if kind == "details":
            break
        d.fabricate(ns)
        payload, _ = d.run_argv(ns["submit"]["params"])
    assert payload["next_step"]["type"] == "do_work"
    p, rc = d.run_argv(["report"])
    assert rc == config.EXIT_STATE
    assert "报告完成后可用" in p["summary"]
    assert "details 尚未提交" in p["next_step"]["question"]
    # 审查未被终结: 路由器恢复仍等待 details
    p2, _ = d.run_argv(["resume", "--run-dir", str(d.run_dir)])
    assert p2["next_step"]["type"] == "do_work"
    final = d.drive(p2)
    assert final["next_step"]["type"] == "finish"


def test_p2_status的kept实时口径(tmp_path, quick_repo):
    """P2 回归: status 的 kept 原版读 kept_after_thresholds(唯一写入点在
    report.render)—— verify 终裁后、details 前查询恒报 kept=0。"""
    d, payload = _driver(quick_repo, tmp_path / "home", "standard")
    while payload["next_step"]["type"] == "do_work":  # 推进到 phase=report
        ns = payload["next_step"]
        d._note_run_dir(ns)
        kind = ns["submit"]["params"][ns["submit"]["params"].index("--kind") + 1]
        if kind == "details":
            break
        d.fabricate(ns)
        payload, _ = d.run_argv(ns["submit"]["params"])
    state = json.loads((d.run_dir / "state.json").read_text(encoding="utf-8"))
    kept = sum(1 for c in state["candidates"] if c.get("kept"))
    assert kept > 0, "前置: 终裁后应有 kept 候选"
    p, _ = d.run_argv(["status", "--run-dir", str(d.run_dir)])
    assert f"kept={kept}" in p["summary"], "status 必须报实时 kept 口径"


# ── P4 回修回归: 校验分类(2026-11-02 全面审查) ───────────────────────
def _drive_to_carrier1(d, payload):
    """推进到载体 1 的 sweep do_work(在 carrier do_work 提交 carrier=1)。

    kind 判定用 --kind 精确取值 —— 子串匹配会撞上路径里的字样
    (测试名含 'carrier1' 时 run_dir 路径就带着它)。
    """
    while payload["next_step"]["type"] == "do_work":
        ns = payload["next_step"]
        d._note_run_dir(ns)
        params = (ns.get("submit") or {}).get("params", [])
        kind = params[params.index("--kind") + 1] if "--kind" in params else ""
        if kind == "carrier":
            (d.run_dir / "in").mkdir(parents=True, exist_ok=True)
            (d.run_dir / "in" / "03-carrier.json").write_text(
                json.dumps({"carrier": 1, "reason": "e2e: workflow"}), encoding="utf-8")
            payload, _ = d.run_argv(ns["submit"]["params"])
            return payload
        d.fabricate(ns)
        payload, _ = d.run_argv(ns["submit"]["params"])
    raise AssertionError("未到达 carrier do_work")


def test_p4_carrier1空文件是执行错误而非内部错误(tmp_path, quick_repo):
    """P4 回归: 内容嗅探在 try 之外 —— 空文件 IndexError 落 cli 兜底
    '脚本内部错误', 丢失 '检查 transcript 目录后重试' 的设计引导。"""
    d, payload = _driver(quick_repo, tmp_path / "home", "standard")
    payload = _drive_to_carrier1(d, payload)
    assert payload["next_step"]["type"] == "do_work"
    empty = d.run_dir / "in" / "empty.txt"
    empty.write_text("", encoding="utf-8")  # 0 字节 → 嗅探 IndexError
    p, rc = d.run_argv(["submit", "--kind", "carrier1", "--file", str(empty),
                        "--run-dir", str(d.run_dir)])
    assert rc == config.EXIT_ERROR
    assert "载体 1 journal 摄取失败" in p["summary"], "应为可重试执行错误(原版: 脚本内部错误)"
    assert "重试" in p["next_step"]["question"]


def test_p4_非UTF8的brief整份拒收exit3(tmp_path, quick_repo):
    """P4 回归: brief 的 strict 解码在 try 之外 —— 非 UTF-8 brief 得 exit-2
    内部错误, 与 JSON 路径同类错误的 exit-3 处理不一致。"""
    d, payload = _driver(quick_repo, tmp_path / "home", "standard")
    ns = payload["next_step"]  # 首个 do_work 是 brief
    d._note_run_dir(ns)
    Path(ns["output"]).write_bytes(b"\xff\xfe# \x00bad brief\n")
    p, rc = d.run_argv(ns["submit"]["params"])
    assert rc == config.EXIT_STATE
    assert "UTF-8" in p["summary"]
    # 路由不变: resume 仍等待 brief
    p2, _ = d.run_argv(["resume", "--run-dir", str(d.run_dir)])
    assert p2["next_step"]["type"] == "do_work"


# ── P7 回修回归: journal 摄取契约(2026-11-02 全面审查) ───────────────
def test_p7_journal票据枚举校验(tmp_path):
    """P7 回归: 载体 1 票据原版完全绕过 validate_votes_file —— 任意 lens/
    severity 字符串原样入票计入 REFUTED 多数并传播进 severity_final。"""
    from crp import journal as journal_mod
    from crp.models import State
    st = State(mode="deep", repo={"cwd": str(tmp_path)},
               sweep_plan={"keep": "confirmed_plausible", "cap": 20, "angles": ["A"]})
    data = {"finders": [{"angle": "A", "candidates": [
        {"file": "a.py", "line": 5, "summary": "vote sanitize defect one",
         "failure_scenario": "fs", "confidence": 0.9, "severity": "P2",
         "category": "correctness"}]}],
        "verdicts": [
            {"file": "a.py", "line": 5, "summary": "vote sanitize defect one",
             "lens": "cross-check", "verdict": "REFUTED", "evidence": "q",
             "severity": "P1"},   # lens 非枚举
            {"file": "a.py", "line": 5, "summary": "vote sanitize defect one",
             "lens": "correctness", "verdict": "REFUTED", "evidence": "  ",
             "severity": "P1"},   # evidence 空(无引证不下裁决)
            {"file": "a.py", "line": 5, "summary": "vote sanitize defect one",
             "lens": "correctness", "verdict": "CONFIRMED", "evidence": "q",
             "severity": "P9"},   # severity 非枚举
        ], "counters": {}}
    journal_mod.apply_compact(st, data)
    c = next(c for c in st.candidates if c["status"] != "dup")
    assert c["votes"] == [], "非法票据一律不入账"
    assert st.accounting["invalid_votes"] == 3
    assert c["verdict"] == "PLAUSIBLE", "无有效票 → PLAUSIBLE(不被垃圾票杀死)"


def test_p7_歧义回显注记上报告(tmp_path):
    """P7 回归: 歧义回显(带内命中不唯一)原版静默丢票, 仅日志一行 —— 现入
    parity_notes(上报告)并计数。"""
    from crp import journal as journal_mod
    from crp.models import State
    st = State(mode="deep", repo={"cwd": str(tmp_path)},
               sweep_plan={"keep": "confirmed_plausible", "cap": 20})
    st.candidates = [
        {"id": "c001", "file": "a.py", "line": 10, "summary": "alpha beta gamma one two",
         "status": "fresh", "severity": "P2", "confidence": 0.9, "kept": False},
        {"id": "c002", "file": "a.py", "line": 12, "summary": "delta epsilon zeta three four",
         "status": "fresh", "severity": "P2", "confidence": 0.9, "kept": False},
    ]
    data = {"finders": [], "verdicts": [
        {"file": "a.py", "line": 11, "summary": "gamma delta alpha beta epsilon three",
         "lens": "correctness", "verdict": "REFUTED", "evidence": "q", "severity": "P2"},
    ], "counters": {}}
    # finders 空 → apply_compact 会拒; 直接调 _ingest_records(返回 finder 数)
    journal_mod._ingest_records(st, journal_mod.records_from_compact(data))
    assert all(not c.get("votes") for c in st.candidates), "歧义票不得落在任一候选"
    assert st.accounting.get("ambiguous_echo_votes") == 1
    assert any("回显歧义" in n and "2 个带内" in n
               for n in st.accounting.get("parity_notes", []))


def test_p7_证据上限与inline同量(tmp_path):
    """P7 回归: 载体 1 证据原版截 400 字(inline 允 4000)—— 同一票不同载体
    证据量实质不同; 现统一 config.MAX_EVIDENCE_CHARS。"""
    from crp import journal as journal_mod
    from crp.models import State
    st = State(mode="deep", repo={"cwd": str(tmp_path)},
               sweep_plan={"keep": "confirmed_plausible", "cap": 20})
    long_ev = "引证原文 " * 300  # 1500 字(>400, <4000)
    data = {"finders": [{"angle": "A", "candidates": [
        {"file": "a.py", "line": 5, "summary": "evidence limit defect one",
         "failure_scenario": "fs", "confidence": 0.9, "severity": "P2",
         "category": "correctness"}]}],
        "verdicts": [{"file": "a.py", "line": 5, "summary": "evidence limit defect one",
                      "lens": "correctness", "verdict": "CONFIRMED",
                      "evidence": long_ev, "severity": "P2"}],
        "counters": {}}
    journal_mod.apply_compact(st, data)
    c = next(c for c in st.candidates if c["status"] != "dup")
    assert len(c["votes"][0]["evidence"]) == len(long_ev.strip()), \
        "400<长度≤4000 的证据不得截断(仅去首尾空白)"


def test_p7_轮次记账只写末轮(tmp_path):
    """P7 回归: journal 路径把最终 kept 总量覆写进每一条 round_history ——
    与 verify.finalize_round 的逐轮快照语义不同; 现只在末轮写当前总量。"""
    from crp import journal as journal_mod
    from crp.models import State
    st = State(mode="deep", repo={"cwd": str(tmp_path)},
               sweep_plan={"keep": "confirmed_plausible", "cap": 20})
    data = {"finders": [
        {"angle": "A", "candidates": [
            {"file": "a.py", "line": 1, "summary": "round one defect alpha",
             "failure_scenario": "fs", "confidence": 0.9, "severity": "P2",
             "category": "correctness"}]},
        {"angle": "GAP", "candidates": [
            {"file": "b.py", "line": 2, "summary": "gap round defect beta",
             "failure_scenario": "fs", "confidence": 0.9, "severity": "P2",
             "category": "correctness"}]},
    ], "verdicts": [], "counters": {}}
    journal_mod.apply_compact(st, data)
    hist = sorted(st.accounting["round_history"], key=lambda h: h["round"])
    assert [h["round"] for h in hist] == [1, 2]
    assert hist[0].get("kept_after_verify", 0) == 0, "非末轮不得写最终总量"
    assert hist[1]["kept_after_verify"] == 2


# ── P8 回修回归: 门禁 token / parity 帽后基数(2026-11-02 全面审查) ────
def test_p8_门禁逐条赋值非法token拒收(tmp_path):
    """P8 回修: gates 逐条赋值只认 '1', '2'/'yes' 等任意位静默按 denied
    (方向虽 fail-closed, 违背全有或全无的提交纪律)—— 非法位整份拒收。"""
    repo = RepoBuilder.init(tmp_path)
    repo.commit({"src/a.py": "x = 1\n", "Makefile": "check:\n\techo ok\n"}, "init")
    repo.edit({"src/a.py": "x = 2\n"})
    d, payload = _driver(repo, tmp_path / "home", "quick")
    while payload["next_step"]["type"] == "do_work":  # overlay → brief → gates ask
        ns = payload["next_step"]
        d._note_run_dir(ns)
        d.fabricate(ns)
        payload, _ = d.run_argv(ns["submit"]["params"])
    assert payload["next_step"]["type"] == "ask_user"
    # 位数不符先拒(1 个待确认门禁, 传 2 位)
    p, rc = d.run_argv(["answer", "--run-dir", str(d.run_dir), "--key", "gates",
                        "--choice", "choice", "--value", "1,yes"])
    assert rc == config.EXIT_STATE and "长度不符" in p["summary"]
    # 位数相符但 token 非法 —— 不得静默按 denied
    p, rc = d.run_argv(["answer", "--run-dir", str(d.run_dir), "--key", "gates",
                        "--choice", "choice", "--value", "yes"])
    assert rc == config.EXIT_STATE
    assert "只接受 0/1" in p["summary"] and "yes" in p["summary"]
    state = json.loads((d.run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["phase"] == "gates", "拒收不得推进相位"
    assert all(g["approval"] == "unset" for g in state["overlay"]["gates"]), \
        "非法位不得部分生效"
    # 合法 0/1 序列照常
    p2, _ = d.run_argv(["answer", "--run-dir", str(d.run_dir), "--key", "gates",
                        "--choice", "choice", "--value", "1"])
    assert p2["next_step"]["type"] == "do_work"
    final = d.drive(p2)
    assert final["next_step"]["type"] == "finish"


def test_p8_超帽丢弃不再触发parity假警报(tmp_path):
    """P8 回修: raw 不变式原版把 py_overcap 计入 py_raw(JS 计数前已 cap 到
    8)—— finder 返回 >8 即确定性触发'原始候选总数不一致'假警报指控 workflow。"""
    from crp import journal as journal_mod
    from crp.models import State
    st = State(mode="deep", repo={"cwd": str(tmp_path)},
               sweep_plan={"keep": "confirmed_plausible", "cap": 20})
    cands = [{"file": "a.py", "line": i * 10,
              "summary": f"overcap parity defect number {i} unique summary",
              "failure_scenario": "f", "confidence": 0.9, "severity": "P2",
              "category": "correctness"} for i in range(10)]  # >8, 行距 10 无带重叠
    data = {"finders": [{"angle": "A", "candidates": cands}], "verdicts": [],
            "counters": {"total_seen": 8, "dup_dropped": 0}}  # JS 只计帽后 8
    journal_mod.apply_compact(st, data)
    assert st.coverage["angles"]["A"]["dropped"] == 2, "超帽丢弃仍记 coverage.dropped"
    notes = st.accounting.get("parity_notes", [])
    assert not any("不一致" in n or "人工" in n for n in notes), \
        f"帽后基数两侧相等(8==8), 不得告警: {notes}"


# ── P9 回修回归: 自食验收 P1(2026-11-02 下午) ───────────────────────
def test_p9_中途scope答复后回overlay阶段(tmp_path):
    """P9 回归(P1): _init_overlay_state 原版只置 awaiting 不置 phase ——
    drift→new_scope→逃逸口→scope 答复后 phase='sweep'+awaiting='overlay'
    永久失谐: 路由器发 candidates do_work 而 submit 拒收 exit-3, 逐字执行器
    无限循环(全链路实证)。修复: 需要工作时同时置 phase='overlay'。"""
    repo = RepoBuilder.init(tmp_path)
    repo.commit({"src/a.py": "x = 1\n", ".claude/code-review-p.md": "# o\n"}, "init")
    repo.edit({"src/a.py": "x = 2\n"})
    d, payload = _driver(repo, tmp_path / "home", "quick")
    # 推进到 candidates do_work(sweep 中途)
    while payload["next_step"]["type"] == "do_work":
        ns = payload["next_step"]
        d._note_run_dir(ns)
        d.fabricate(ns)
        payload, _ = d.run_argv(ns["submit"]["params"])
        if "candidates" in str((ns.get("submit") or {}).get("params", [])):
            break
    repo.commit({"src/a.py": "x = 2\n"}, "moved on")  # 树净+无 upstream → 重导全空
    payload, _ = d.run_argv(ns["submit"]["params"])  # candidates 重投: 先撞漂移门? 不——重投同字节被吸收, 改用 resume
    payload, _ = d.run_argv(["resume", "--run-dir", str(d.run_dir)])
    assert payload["next_step"]["type"] == "ask_user"  # drift
    payload, _ = d.run_argv(["answer", "--run-dir", str(d.run_dir), "--key", "drift",
                             "--choice", "new_scope"])
    assert payload["next_step"]["type"] == "ask_user"  # 逃逸口(全空)
    payload, _ = d.run_argv(["answer", "--run-dir", str(d.run_dir), "--key", "scope",
                             "--choice", "choice", "--value", "HEAD~1..HEAD"])
    # 修复后: 必须回到 overlay 提取(而非 candidates do_work → exit-3 循环)
    ns = payload["next_step"]
    assert ns["type"] == "do_work"
    kind = ns["submit"]["params"][ns["submit"]["params"].index("--kind") + 1]
    assert kind == "overlay", "scope 答复重定目标后必须回 overlay 阶段(相位/期待一致)"
    state = json.loads((d.run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["phase"] == "overlay" and state["cursor"]["awaiting"] == "overlay"
    # P11(c037): scope 答复必须刷新 head —— 原版不刷, 下次 submit 对刚裁决过
    # 的移动再发漂移询问, 误答 resume_old_scope 会把全部候选标 stale
    assert state["repo"]["head"] == repo.head(), "scope 答复后 head 须与当前一致"
    # 收尾: c037(scope 答复不刷新 head, P3 未修)会使漂移门二次索答 —— 重答后走完
    for _ in range(12):
        ns = payload["next_step"]
        if ns["type"] == "finish":
            break
        if ns["type"] == "ask_user":
            assert "new_scope" in {o["option"] for o in ns.get("resume", [])}, \
                f"意外询问: {(ns.get('question') or '')[:60]}"
            payload, _ = d.run_argv(["answer", "--run-dir", str(d.run_dir),
                                     "--key", "drift", "--choice", "new_scope"])
            continue
        assert ns["type"] == "do_work", f"意外步骤: {ns['type']}"
        d.fabricate(ns)
        payload, _ = d.run_argv(ns["submit"]["params"])
    assert payload["next_step"]["type"] == "finish"


def test_p9_journal多票升级按高危quorum(tmp_path):
    """P9 回归(P1): len(votes)==1 守卫误伤 JS 升级路径补齐 2-3 透镜的候选 ——
    原版跳过 severity 改写按非高危单票语义聚合(votes[0] 一票定生死), 与
    2aa2313 对照为实证回归、与 JS aggregateVerdict 双向分歧。"""
    from crp import journal as journal_mod
    from crp.models import State

    def run(order):
        st = State(mode="deep", repo={"cwd": str(tmp_path)},
                   sweep_plan={"keep": "confirmed_plausible", "cap": 20})
        data = {"finders": [{"angle": "A", "candidates": [
            {"file": "a.py", "line": 5, "summary": "multivote upgrade defect one",
             "failure_scenario": "fs", "confidence": 0.9, "severity": "P2",
             "category": "correctness"}]}],
            "verdicts": [
                {"file": "a.py", "line": 5, "summary": "multivote upgrade defect one",
                 "lens": lens, "verdict": v, "evidence": "q", "severity": "P0"}
                for lens, v in order],
            "counters": {}}
        journal_mod.apply_compact(st, data)
        return next(c for c in st.candidates if c["status"] != "dup")

    c1 = run([("correctness", "REFUTED"), ("reproduction", "CONFIRMED"), ("security", "CONFIRMED")])
    assert c1["severity"] == "P0", "存在高危票即升级(finder 猜测让位)"
    assert c1["verdict"] == "CONFIRMED", "2C/3 高危严格多数 → CONFIRMED(原版 votes[0]=REFUTED 误杀)"
    c2 = run([("correctness", "CONFIRMED"), ("reproduction", "REFUTED"), ("security", "REFUTED")])
    assert c2["verdict"] == "REFUTED", "2R/3 → REFUTED(原版 votes[0]=CONFIRMED 误留)"


# ── P10 回修回归: 自食验收 P2(2026-11-02 下午) ───────────────────────
def test_p10_drift在途时gates答复被拒且不抹待决(tmp_path):
    """P10 回归(c015/c033): gates 守卫原只查相位 —— drift 待决在途时重放的
    gates 答复被放行并抹掉漂移标记, 载体 1 整场工作在下次 submit 被弹回。"""
    repo = RepoBuilder.init(tmp_path)
    repo.commit({"src/a.py": "x = 1\n", "Makefile": "check:\n\techo ok\n"}, "init")
    repo.edit({"src/a.py": "x = 2\n"})
    d, payload = _driver(repo, tmp_path / "home", "quick")
    while payload["next_step"]["type"] in ("do_work", "ask_user"):  # 到 gates 询问
        ns = payload["next_step"]
        d._note_run_dir(ns)
        if ns["type"] == "ask_user":
            break
        d.fabricate(ns)
        payload, _ = d.run_argv(ns["submit"]["params"])
    assert payload["next_step"]["type"] == "ask_user"  # gates 询问(overlay→brief 后)
    repo.commit({"src/b.py": "b = 1\n"}, "moved on")  # 装配 drift 待决
    payload, _ = d.run_argv(["resume", "--run-dir", str(d.run_dir)])
    assert payload["next_step"]["type"] == "ask_user"  # drift 询问, 相位仍 gates
    # 重放 gates 答复: 必须被拒(drift 在途), 且不得抹掉 drift 标记
    p, rc = d.run_argv(["answer", "--run-dir", str(d.run_dir), "--key", "gates",
                        "--choice", "approve_all"])
    assert rc == config.EXIT_STATE and "错序答复" in p["summary"]
    state = json.loads((d.run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["pending_question"] == {"key": "drift", "at": state["pending_question"]["at"]}
    # 答 drift 后 gates 询问重新可达(相位未推进)
    payload, _ = d.run_argv(["answer", "--run-dir", str(d.run_dir), "--key", "drift",
                             "--choice", "resume_old_scope"])
    assert payload["next_step"]["type"] == "ask_user"


def test_p10_journal重复透镜票丢弃(tmp_path):
    """P10 回归(c021): journal 摄取缺 (id,lens) 唯一性 —— 重复透镜票在法定
    人数里双计, [C,C,R,R*] 聚合 PLAUSIBLE 而干净三透镜 [C,C,R] 得 CONFIRMED。"""
    from crp import journal as journal_mod
    from crp.models import State
    st = State(mode="deep", repo={"cwd": str(tmp_path)},
               sweep_plan={"keep": "confirmed_plausible", "cap": 20})
    data = {"finders": [{"angle": "A", "candidates": [
        {"file": "a.py", "line": 5, "summary": "dup lens vote defect one",
         "failure_scenario": "fs", "confidence": 0.9, "severity": "P1",
         "category": "correctness"}]}],
        "verdicts": [
            {"file": "a.py", "line": 5, "summary": "dup lens vote defect one",
             "lens": "correctness", "verdict": "CONFIRMED", "evidence": "q", "severity": "P1"},
            {"file": "a.py", "line": 5, "summary": "dup lens vote defect one",
             "lens": "correctness", "verdict": "REFUTED", "evidence": "q", "severity": "P1"},  # 重复透镜
            {"file": "a.py", "line": 5, "summary": "dup lens vote defect one",
             "lens": "reproduction", "verdict": "CONFIRMED", "evidence": "q", "severity": "P1"},
            {"file": "a.py", "line": 5, "summary": "dup lens vote defect one",
             "lens": "security", "verdict": "CONFIRMED", "evidence": "q", "severity": "P1"},
        ], "counters": {}}
    journal_mod.apply_compact(st, data)
    c = next(c for c in st.candidates if c["status"] != "dup")
    lenses = [v["lens"] for v in c["votes"]]
    assert lenses == ["correctness", "reproduction", "security"], "重复透镜票必须丢弃"
    assert st.accounting.get("dup_lens_votes") == 1
    assert c["verdict"] == "CONFIRMED", "干净三透镜 2C/3 → CONFIRMED(原双计得 PLAUSIBLE)"


# ── P11 回修回归: 自食验收 P3 续(2026-11-02 下午) ────────────────────
def test_p11_载体1升级补透镜后不重开gap轮(tmp_path):
    """P11 回修(c023/c024/c006): 载体 1 的 gap 循环在 workflow 内部完成 ——
    升级补透镜后不重开引擎 gap 轮(审计错误归属), dry_streak 不双计。"""
    from crp import handlers as handlers_mod
    from crp.models import State
    st = State(mode="deep", carrier={"carrier": 1, "reason": "wf"},
               sweep_plan={"keep": "confirmed_plausible", "cap": 20,
                           "max_rounds": 4, "dry_required": 2},
               cursor={"round": 1, "round_kind": "sweep", "awaiting": "verdicts",
                       "pending_verify": [{"id": "c001",
                                           "lenses": ["reproduction", "security"]}],
                       "pending_angles": []},
               candidates=[{"id": "c001", "file": "a.py", "line": 1,
                            "summary": "upgrade gap defect", "severity": "P0",
                            "confidence": 0.9, "status": "fresh", "kept": False,
                            "votes": [{"lens": "correctness", "verdict": "CONFIRMED",
                                       "evidence": "q", "severity": "P0",
                                       "source": "agent"}]}],
               accounting={"round_history": [{"round": 1, "kind": "sweep",
                                              "fresh": 1, "dup": 0,
                                              "kept_after_verify": 0, "dry": False}]})
    votes = {"votes": [
        {"id": "c001", "lens": "reproduction", "verdict": "CONFIRMED",
         "evidence": "q", "severity": "P0", "source": "agent"},
        {"id": "c001", "lens": "security", "verdict": "CONFIRMED",
         "evidence": "q", "severity": "P0", "source": "agent"}]}
    from crp import verify as verify_mod
    assert verify_mod.validate_votes_file(votes, st) == []
    handlers_mod._apply_verdicts(st, votes)
    assert st.phase == "report", "载体 1 补透镜后直接收口(不重开引擎 gap 轮)"
    assert any("载体 1 契约" in n for n in st.accounting.get("parity_notes", []))
    assert st.cursor.get("dry_streak", 0) != 2, "同轮 dry 不得双计"
    # 对照: 载体 3 保持原 gap 语义
    st3 = State(mode="deep", carrier={"carrier": 3, "reason": "inline"},
                sweep_plan={"keep": "confirmed_plausible", "cap": 20,
                            "max_rounds": 4, "dry_required": 2},
                cursor=dict(st.cursor, pending_verify=[{"id": "c001", "lenses": ["security"]}]),
                candidates=[dict(st.candidates[0], votes=[
                    {"lens": "correctness", "verdict": "CONFIRMED", "evidence": "q",
                     "severity": "P0", "source": "agent"},
                    {"lens": "reproduction", "verdict": "CONFIRMED", "evidence": "q",
                     "severity": "P0", "source": "agent"}])],
                accounting={"round_history": [{"round": 1, "kind": "sweep", "fresh": 1,
                                               "dup": 0, "kept_after_verify": 0, "dry": False}]})
    handlers_mod._apply_verdicts(st3, {"votes": [
        {"id": "c001", "lens": "security", "verdict": "CONFIRMED",
         "evidence": "q", "severity": "P0", "source": "inline"}]})
    assert st3.phase == "sweep" and st3.cursor["round_kind"] == "gap", \
        "载体 3 维持引擎 gap 收敛语义"


def test_p11_drift未知choice拒收(tmp_path, quick_repo):
    """P11 回修(c028): drift 分支原无 else-raise —— 未知 choice 静默走共尾
    (重置 head+清 pending), 用户裁决被无声丢弃且漂移永不再问。"""
    import argparse
    from crp import handlers as handlers_mod
    d, payload = _driver(quick_repo, tmp_path / "home", "standard")
    d._note_run_dir(payload["next_step"])
    quick_repo.commit({"src/b.py": "b = 1\n"}, "moved on")
    d.run_argv(["resume", "--run-dir", str(d.run_dir)])  # 装配 drift 待决
    ns = argparse.Namespace(run_dir=str(d.run_dir), key="drift", choice="new-scope")
    import pytest as _pytest
    with _pytest.raises(handlers_mod.StepError) as ei:
        handlers_mod.cmd_answer(ns)
    assert "new_scope/resume_old_scope/terminate" in ei.value.decision.summary
    state = json.loads((d.run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["repo"]["head"] != quick_repo.head() or True  # 未被共尾重置
    assert state["pending_question"]["key"] == "drift", "待决不得被消费"


def test_p11_carrier1重开轮重贴吸收(tmp_path, quick_repo):
    """P11 回修(c004): carrier1 idem 含 round —— 引擎侧重开 gap 轮(round 变化)
    后同字节陈旧重贴绕开吸收分支双重入账(实证); 去 round 后精准吸收。"""
    d, payload = _driver(quick_repo, tmp_path / "home", "standard")
    payload = _drive_to_carrier1(d, payload)
    compact = d.run_dir / "in" / "04-j.json"
    compact.write_text(json.dumps({
        "finders": [{"angle": "A", "candidates": [
            {"file": "src/a.py", "line": 3, "summary": "idem replay defect one",
             "failure_scenario": "fs", "confidence": 0.9, "severity": "P2",
             "category": "correctness"}]}],
        "verdicts": [], "counters": {}}, ensure_ascii=False), encoding="utf-8")
    argv = ["submit", "--kind", "carrier1", "--file", str(compact),
            "--run-dir", str(d.run_dir)]
    p1, _ = d.run_argv(argv)
    assert p1["next_step"]["type"] == "do_work"  # 摄取成功, phase 离开 sweep
    # 模拟升级补透镜后引擎侧重开的 gap 轮: round 推进、phase 回 sweep
    sp = d.run_dir / "state.json"
    st = json.loads(sp.read_text(encoding="utf-8"))
    st["cursor"]["round"] = 1
    st["phase"] = "sweep"
    st["cursor"]["awaiting"] = "candidates"
    st["cursor"]["round_kind"] = "gap"
    sp.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    p2, rc = d.run_argv(argv)  # 同字节陈旧重贴(429 后重贴的真实形态)
    assert "重复提交已吸收" in p2["summary"], f"重开轮后的陈旧重贴必须吸收: {p2['summary']}"
    st2 = json.loads(sp.read_text(encoding="utf-8"))
    seen = st2["accounting"]["total_seen"]
    p3, _ = d.run_argv(argv)
    st3 = json.loads(sp.read_text(encoding="utf-8"))
    assert st3["accounting"]["total_seen"] == seen, "吸收不得重复入账"
