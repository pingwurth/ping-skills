"""项目配置层: overlay 发现、信任判定、启发式门禁扫描、提交校验与应用。

信任边界(references/project-config.md):
    - overlay 出现在被审范围内(新增/修改/untracked)或 repo 模式(无基线) → untrusted;
    - untrusted overlay 的红线不覆盖严重度、门禁需用户确认(A4 批量 ask_user);
    - **排除项挂起**(v2.0 A3): untrusted overlay 的排除项不应用 —— 攻击者的 overlay
      不得把恶意文件排除出审查; 挂起项记录在案, 交由扫描审查。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from . import config, scope as scope_mod
from .logutil import module_logger
from .models import State


def discover(cwd: str | Path) -> Optional[str]:
    """overlay 发现顺序: .claude/code-review-p.md → 仓库根 code-review-p.md。"""
    root = Path(cwd)
    for rel in config.OVERLAY_CANDIDATE_PATHS:
        if (root / rel).is_file():
            return rel
    return None


def trust_of(scope: dict, overlay_path: Optional[str]) -> tuple[str, str]:
    """信任判定(确定性): 是否被被审范围触及 / repo 模式无基线。"""
    if not overlay_path:
        return "none", "未发现 overlay"
    touched = (overlay_path in (scope.get("diff_files") or [])
               or overlay_path in (scope.get("untracked") or []))
    if scope.get("repo_mode"):
        return "untrusted", "repo 模式无合并基线 —— 一切仓库内容按不可信处理"
    if touched:
        return "untrusted", "被审 diff 新增/修改了 overlay"
    return "trusted", "overlay 不在被审范围内(合并基线内既存且未被触及)"


# --------------------------------------------------------------------------- #
# 启发式门禁扫描(仅无 overlay 时; 提出候选, 永不自动执行)
# --------------------------------------------------------------------------- #
def heuristic_gates(cwd: str | Path) -> list[str]:
    """扫描 Makefile/justfile/package.json/CI 名中的门禁候选(≤12 条)。"""
    root = Path(cwd)
    found: list[str] = []
    pattern = re.compile(config.HEURISTIC_GATE_PATTERN, re.IGNORECASE)

    def add(cmd: str) -> None:
        if len(found) < 12 and cmd not in found and pattern.search(cmd):
            found.append(cmd)

    for name in config.HEURISTIC_GATE_FILES:
        f = root / name
        if not f.is_file():
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if name == "package.json":
            try:
                parsed = json.loads(text)
            except ValueError:
                parsed = {}
            scripts = parsed.get("scripts") if isinstance(parsed, dict) else None
            if not isinstance(scripts, dict):  # c015: scripts 非对象(畸形 json)不迭代
                scripts = {}
            for key in scripts:
                add(f"npm run {key}")
        else:
            for m in re.finditer(r"^([A-Za-z0-9_.-]+):", text, re.MULTILINE):
                add(f"make {m.group(1)}")

    wf = root / config.HEURISTIC_GATE_DIR
    if wf.is_dir():
        for f in sorted(wf.glob("*.yml")) + sorted(wf.glob("*.yaml")):
            add(f"ci:{f.name}")
    return found


# --------------------------------------------------------------------------- #
# 提交校验与应用
# --------------------------------------------------------------------------- #
def validate_overlay_file(data: object) -> list[str]:
    if not isinstance(data, dict):
        return ["overlay 提交必须是 JSON 对象"]
    errs: list[str] = []
    rl = data.get("red_lines", [])
    if not isinstance(rl, list):
        errs.append("red_lines 必须是数组")
    else:
        for i, r in enumerate(rl):
            if not isinstance(r, dict):
                errs.append(f"red_lines[{i}] 必须是对象")
                continue
            if not str(r.get("rule", "")).strip():
                errs.append(f"red_lines[{i}].rule 必须非空")
            if r.get("severity") not in config.SEVERITIES:
                errs.append(f"red_lines[{i}].severity 必须是 P0-P3")
    gates = data.get("gates", [])
    if not isinstance(gates, list):
        errs.append("gates 必须是数组")
    else:
        for i, g in enumerate(gates):
            if not isinstance(g, dict) or not str(g.get("command", "")).strip():
                errs.append(f"gates[{i}].command 必须非空")
            elif g.get("semantics") not in ("read_only", "write"):
                errs.append(f"gates[{i}].semantics 必须是 read_only|write")
    excl = data.get("exclusions", [])
    if not isinstance(excl, list) or any(not isinstance(x, str) for x in excl):
        errs.append("exclusions 必须是字符串数组")
    inv = data.get("invariants", [])
    if not isinstance(inv, list) or any(not isinstance(x, str) for x in inv):
        errs.append("invariants 必须是字符串数组")
    return errs


def apply_overlay(state: State, data: dict) -> dict:
    """应用 overlay 提交: 存内容、合并门禁、应用/挂起排除项。返回应用摘要。

    门禁审批规则(A4):
        trusted overlay 的 read_only 门禁 → auto(Phase 3 可直接运行取证);
        一切 write 语义 → denied(永不运行, finding 记 unverified by gate);
        untrusted overlay / 启发式门禁 → unset(等待唯一一次批量 ask_user)。
    """
    ov = state.overlay
    ov["content"] = {
        "red_lines": data.get("red_lines", []),
        "gates": data.get("gates", []),
        "exclusions": data.get("exclusions", []),
        "invariants": data.get("invariants", []),
    }
    trust = ov.get("trust", "none")

    # ── 门禁合并 ──
    merged: list[dict] = []
    for g in data.get("gates", []):
        entry = {"command": g.get("command", ""), "semantics": g.get("semantics", "read_only"),
                 "source": "overlay" if trust != "none" else "heuristic"}
        if entry["semantics"] == "write":
            entry["approval"] = "denied"
        elif trust == "trusted" and entry["source"] == "overlay":
            entry["approval"] = "auto"
        else:
            entry["approval"] = "unset"
        merged.append(entry)
    # LLM 漏分类的启发式候选原样保留(未知语义, 一律待确认)
    seen_cmds = {g["command"] for g in merged}
    for cmd in ov.get("heuristic_gates") or []:
        if cmd not in seen_cmds:
            merged.append({"command": cmd, "semantics": "unknown",
                           "source": "heuristic", "approval": "unset"})
    ov["gates"] = merged

    # ── 排除项: trusted 应用 / untrusted|none 挂起(A3) ──
    summary: dict = {"exclusions_applied": [], "exclusions_suspended": [], "gates_pending": 0}
    patterns = data.get("exclusions", [])
    if patterns and trust == "trusted":
        scope_mod.apply_exclusions(state.scope, patterns, "overlay")
        ov["exclusions_applied"] = patterns
        summary["exclusions_applied"] = patterns
    elif patterns:
        ov["exclusions_suspended"] = patterns
        summary["exclusions_suspended"] = patterns
        module_logger().warning(f"不可信/无 overlay: 排除项挂起未应用: {patterns}")
    summary["gates_pending"] = sum(1 for g in merged if g.get("approval") == "unset")
    return summary


def pending_gate_question_count(state: State) -> int:
    return sum(1 for g in (state.overlay.get("gates") or [])
               if g.get("approval") == "unset" and g.get("semantics") != "write")
