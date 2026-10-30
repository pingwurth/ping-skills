"""候选台账: 路径归一、schema 校验、去重、ID 分配、coverage 与计数。

去重语义与 workflows/deep-review.js 的 pure helpers 逐点对齐
(±3 行带 + 摘要 token 重叠 + CJK shingle); tests/fixtures/dedup_cases.json
由 Python 与 node 两侧共同断言, 锁死跨实现漂移。

台账 = 曾见一切(含 dup/refuted) —— "对曾见一切去重"是收敛的前提
(被拒绝的 finding 不得以新措辞重新入账)。
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from typing import Optional

from . import config


# --------------------------------------------------------------------------- #
# 路径归一(与 JS normFile 对齐; v1.4.1 #9 回归)
# --------------------------------------------------------------------------- #
def norm_file(f: object, root: str = "") -> str:
    p = str(f or "").replace("\\", "/")
    r = str(root or "").replace("\\", "/").rstrip("/")
    if r and p.startswith(r + "/"):
        p = p[len(r) + 1:]
    if p.startswith("./"):
        p = p[2:]
    return p


# --------------------------------------------------------------------------- #
# 摘要归一与 token 化(与 JS normSummary/tokensOf 对齐)
# --------------------------------------------------------------------------- #
def _is_letter_or_num(ch: str) -> bool:
    r"""\p{L}|\p{N} 的精确判定(与 JS [^\p{L}\p{N}] 剥离语义一致)。"""
    cat = unicodedata.category(ch)
    return cat[0] in ("L", "N")


def norm_summary(s: str) -> str:
    out = []
    for ch in str(s or "").lower():
        out.append(ch if _is_letter_or_num(ch) else " ")
    # 连续分隔折叠为单空格并去首尾(与 JS 的 [^\p{L}\p{N}]+ 语义一致)
    return " ".join("".join(out).split())


# CJK 实用区段(Han/Hiragana/Katakana/Hangul; 与 JS \p{Script=…} 在摘要
# 场景下等价 —— 兼容区与部首区在摘要中不出现)
_CJK_RANGES = (
    (0x2E80, 0x2FDF),   # 部首/康熙(防御性)
    (0x3005, 0x3005),   # 々
    (0x3040, 0x30FF),   # 平假名 + 片假名
    (0x3105, 0x312F),   # 注音(防御性)
    (0x3400, 0x4DBF),   # Han Ext A
    (0x4E00, 0x9FFF),   # Han 统一表意
    (0xAC00, 0xD7AF),   # 谚文音节
    (0xF900, 0xFAFF),   # Han 兼容
)


def _is_cjk(ch: str) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in _CJK_RANGES)


def tokens_of(s: str) -> set[str]:
    """词 token(len>1) + CJK 连续段 bigram shingle(CJK 无空格可分)。"""
    norm = norm_summary(s)
    toks = {t for t in norm.split(" ") if len(t) > 1}
    # 找 CJK 连续段(归一后非 L/N 已成空格, 段内全是 CJK 字符)
    run: list[str] = []
    runs: list[list[str]] = []
    for ch in norm:
        if _is_cjk(ch):
            run.append(ch)
        else:
            if len(run) >= 2:
                runs.append(run)
            run = []
    if len(run) >= 2:
        runs.append(run)
    for seg in runs:
        for i in range(len(seg) - 1):
            toks.add(seg[i] + seg[i + 1])
    return toks


def overlap_toks(a: set[str], b: set[str]) -> bool:
    """较小 token 集的半数(上限 3)以上共享 → 视为同一缺陷。"""
    if not a or not b:
        return False  # 空摘要永不合并
    hit = sum(1 for t in a if t in b)
    return hit >= max(1, min(config.OVERLAP_CAP, (min(len(a), len(b)) + 1) // 2))


def find_dup(seen: list[dict], cand: dict) -> Optional[dict]:
    """在曾见台账中找同缺陷条目: 同 file + 行号 ±DEDUP_LINE_BAND + 摘要重叠。"""
    toks = tokens_of(cand.get("summary", ""))
    cline = cand.get("line") or 0
    for p in seen:
        if p.get("file") != cand.get("file"):
            continue
        if abs((p.get("line") or 0) - cline) > config.DEDUP_LINE_BAND:
            continue
        if overlap_toks(toks, tokens_of(p.get("summary", ""))):
            return p
    return None


# --------------------------------------------------------------------------- #
# 候选 schema 校验(全有或全无; 拒收不截断)
# --------------------------------------------------------------------------- #
def validate_candidate(c: object, where: str) -> list[str]:
    """单条候选校验; 返回错误列表(空 = 通过)。"""
    errs: list[str] = []
    if not isinstance(c, dict):
        return [f"{where}: 候选必须是对象"]
    file = c.get("file")
    if not isinstance(file, str) or not file.strip():
        errs.append(f"{where}: file 必须是非空字符串(仓库相对路径)")
    line = c.get("line")
    if not isinstance(line, int) or isinstance(line, bool) or line < 0:
        errs.append(f"{where}: line 必须是 ≥0 的整数")
    summary = c.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        errs.append(f"{where}: summary 必须是非空字符串")
    elif len(summary) > config.MAX_SUMMARY_CHARS:
        errs.append(f"{where}: summary 长度 {len(summary)} 超上限 {config.MAX_SUMMARY_CHARS}")
    fs = c.get("failure_scenario")
    if not isinstance(fs, str) or not fs.strip():
        errs.append(f"{where}: failure_scenario 必须非空(无失败场景即无 finding)")
    elif len(fs) > config.MAX_SCENARIO_CHARS:
        errs.append(f"{where}: failure_scenario 长度超上限 {config.MAX_SCENARIO_CHARS}")
    conf = c.get("confidence")
    if not isinstance(conf, (int, float)) or isinstance(conf, bool) or not (0.0 <= float(conf) <= 1.0):
        errs.append(f"{where}: confidence 必须在 [0,1]")
    sev = c.get("severity")
    if sev not in config.SEVERITIES:
        errs.append(f"{where}: severity 必须是 {'/'.join(config.SEVERITIES)}(得到 {sev!r})")
    cat = c.get("category")
    if not isinstance(cat, str) or not cat.strip():
        errs.append(f"{where}: category 必须是非空字符串")
    return errs


def validate_candidates_file(data: object) -> list[str]:
    """candidates 提交文件整体校验; 返回错误列表(空 = 通过)。"""
    if not isinstance(data, dict):
        return ["提交文件必须是 JSON 对象 {items: [...]}"]
    items = data.get("items")
    if not isinstance(items, list) or not items:
        return ["items 必须是非空数组(每元素一个角度批次)"]
    errs: list[str] = []
    total = 0
    for i, item in enumerate(items):
        w = f"items[{i}]"
        if not isinstance(item, dict):
            errs.append(f"{w}: 必须是对象")
            continue
        angle = item.get("angle")
        if not isinstance(angle, str) or not angle.strip():
            errs.append(f"{w}: angle 必须是非空字符串")
        cands = item.get("candidates")
        if not isinstance(cands, list):
            errs.append(f"{w}: candidates 必须是数组")
            continue
        total += len(cands)
        for j, c in enumerate(cands):
            errs.extend(validate_candidate(c, f"{w}.candidates[{j}]"))
    if total > config.MAX_CANDIDATES_PER_FILE:
        errs.append(f"候选总数 {total} 超单文件上限 {config.MAX_CANDIDATES_PER_FILE}")
    return errs


# --------------------------------------------------------------------------- #
# 入账
# --------------------------------------------------------------------------- #
@dataclass
class IngestResult:
    fresh_ids: list[str] = field(default_factory=list)
    dup_count: int = 0
    dropped_over_cap: int = 0
    errors: list[str] = field(default_factory=list)


def next_id(state) -> str:
    return f"c{len(state.candidates) + 1:03d}"


def ingest_candidates(state, data: dict, *, origin: str, round_no: int) -> IngestResult:
    """把 candidates 提交并入台账(调用方已通过 validate_candidates_file)。

    每角度批次: 路径归一 → 超 8 条丢弃计数 → 逐条去重 → 分配 id 入账;
    coverage/账目随之更新。已通过 candidate_by_id 幂等保护由 submit 处理器负责。
    """
    result = IngestResult()
    root = (state.repo or {}).get("cwd", "")
    acc = state.accounting
    fresh_for_round = 0
    dup_for_round = 0

    for item in data.get("items", []):
        angle = item.get("angle", "?")
        cov = state.coverage.setdefault("angles", {}).setdefault(
            angle, {"ran": False, "raw": 0, "fresh": 0, "dropped": 0})
        cov["ran"] = cov.get("ran") or bool(item.get("coverage", {}).get("ran", True))
        cands = item.get("candidates") or []
        if len(cands) > config.MAX_CANDIDATES_PER_ANGLE:
            result.dropped_over_cap += len(cands) - config.MAX_CANDIDATES_PER_ANGLE
            cov["dropped"] = cov.get("dropped", 0) + len(cands) - config.MAX_CANDIDATES_PER_ANGLE
            cands = cands[:config.MAX_CANDIDATES_PER_ANGLE]
        cov["raw"] = cov.get("raw", 0) + len(cands)

        for c in cands:
            c = dict(c)
            c["file"] = norm_file(c.get("file"), root)
            dup = find_dup(state.candidates, c)
            if dup is not None:
                result.dup_count += 1
                dup_for_round += 1
                state.candidates.append({
                    "id": next_id(state), "file": c["file"], "line": c.get("line", 0),
                    "summary": c.get("summary", ""),
                    "failure_scenario": c.get("failure_scenario", ""),
                    "confidence": c.get("confidence", 0.0),
                    "severity": c.get("severity", "P3"), "category": c.get("category", "correctness"),
                    "angle": angle, "round": round_no, "origin": origin,
                    "status": "dup", "dup_of": dup.get("id"),
                })
                continue
            cid = next_id(state)
            entry = {
                "id": cid, "file": c["file"], "line": c.get("line", 0),
                "summary": c.get("summary", ""),
                "failure_scenario": c.get("failure_scenario", ""),
                "confidence": float(c.get("confidence", 0.0)),
                "severity": c.get("severity", "P3"), "category": c.get("category", "correctness"),
                "angle": angle, "round": round_no, "origin": origin,
                "status": "fresh", "dup_of": None,
                "votes": [], "verdict": None, "evidence": "",
                "severity_final": c.get("severity", "P3"),
                "kept": False, "drop_reason": None, "stale": False, "rank": None,
            }
            state.candidates.append(entry)
            result.fresh_ids.append(cid)
            fresh_for_round += 1
            cov["fresh"] = cov.get("fresh", 0) + 1

    acc["total_seen"] = len(state.candidates)
    acc["dup_dropped"] = acc.get("dup_dropped", 0) + result.dup_count
    # 轮内累计: 同轮多次提交累入同一条目; finalize_round 落 kept/dry 后轮号推进
    hist = acc.setdefault("round_history", [])
    if hist and hist[-1].get("round") == round_no:
        hist[-1]["fresh"] = hist[-1].get("fresh", 0) + fresh_for_round
        hist[-1]["dup"] = hist[-1].get("dup", 0) + dup_for_round
    else:
        hist.append({"round": round_no, "kind": state.cursor.get("round_kind", "sweep"),
                     "fresh": fresh_for_round, "dup": dup_for_round,
                     "kept_after_verify": 0, "dry": False})
    return result
