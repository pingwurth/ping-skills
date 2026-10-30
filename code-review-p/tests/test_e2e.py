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
from gitrepo import RepoBuilder

SKILL_ROOT = Path(__file__).resolve().parents[1]
ENTRY = SKILL_ROOT / "scripts" / "crp.py"


def parse_block(stdout: str) -> dict:
    assert stdout.count(config.NEXT_STEP_BEGIN_MARKER) == 1
    begin = stdout.index(config.NEXT_STEP_BEGIN_MARKER) + len(config.NEXT_STEP_BEGIN_MARKER)
    end = stdout.index(config.NEXT_STEP_END_MARKER)
    return json.loads(stdout[begin:end])


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
                                        "lens_requests": [], "dry_streak": 0})
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
    """c018 回归: journal 路径单票把 P2 升 P1 → 不得单票 CONFIRMED 终裁。"""
    from crp import journal as journal_mod
    from crp.models import State
    st = State(mode="deep", repo={"cwd": str(tmp_path)},
               sweep_plan={"keep": "confirmed_plausible", "cap": 20})
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
    assert c["verdict"] == "PLAUSIBLE", "单票不足高危 quorum → PLAUSIBLE"


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
