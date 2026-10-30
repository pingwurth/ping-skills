"""载体 1(Workflow 工具)结果摄取: 一切账目在 Python 侧从原始 agent 返回重算。

两条通道:
    1. transcript journal —— Workflow 工具原生持久化 <transcriptDir>/journal.jsonl
       (记录每个 agent 的真实返回值); 主上下文只搬运一个路径, 零逐字转录漂移。
    2. 压缩 journal —— journal 不可读时, deep-review.js 返回值携带的压缩台账
       (evidence 截断 400 字/条), LLM 一次 Write 落盘后经 --file 提交。

自描述返回(deep-review.js 的 schema 已改为):
    finder  {angle, candidates: [候选…]}
    refuter {file, line, summary, lens, verdict, evidence, severity}
摄取后 Python 重算去重/裁决/计数 —— 与 JS 内部计数分歧记入 parity_notes(可见不静默)。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator, Optional

from . import config, ledger, verify
from .logutil import module_logger
from .models import State

# journal.jsonl 记录中"agent 返回值"的候选键(按序探测)
_RESULT_KEYS = ("result", "output", "returnValue", "value", "data")
_COMPACT_EVIDENCE_LIMIT = 400


# --------------------------------------------------------------------------- #
# 记录源
# --------------------------------------------------------------------------- #
def records_from_transcript(transcript_dir: Path) -> Iterator[tuple[str, object]]:
    """逐行读 <transcriptDir>/journal.jsonl, 容错解析, 产出 (label, result)。"""
    path = Path(transcript_dir) / "journal.jsonl"
    with open(path, encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not isinstance(rec, dict):
                continue
            result = None
            for key in _RESULT_KEYS:
                if isinstance(rec.get(key), (dict, list)):
                    result = rec[key]
                    break
            if result is None and isinstance(rec.get("candidates"), list):
                result = rec  # 行本身即返回值
            yield rec.get("label") or rec.get("agentLabel") or "", result


def records_from_compact(data: dict) -> Iterator[tuple[str, object]]:
    """压缩 journal → 同构记录流: finders[i] 与 verdicts[j]。"""
    for f in data.get("finders") or []:
        yield f"find:{f.get('angle', '?')}", f
    for v in data.get("verdicts") or []:
        yield "verify", v


def _classify(result) -> str:
    """返回值分类: finder(含 angle+candidates) / refuter(verdict)。"""
    if isinstance(result, dict):
        if isinstance(result.get("candidates"), list) and result.get("angle"):
            return "finder"
        if result.get("verdict") in config.VERDICTS:
            return "refuter"
    return "unknown"


def _match_candidate(state: State, file, line, summary) -> Optional[dict]:
    """把 refuter 自描述返回匹配回台账候选(echo 的 file:line+summary)。"""
    nf = ledger.norm_file(file, (state.repo or {}).get("cwd", ""))
    exact = fallback = None
    for c in state.candidates:
        if c.get("file") != nf or c.get("status") == "dup":
            continue
        if (c.get("line") or 0) != (line or 0):
            continue
        if c.get("summary") == summary:
            exact = c
            break
        fallback = fallback or c
    return exact or fallback


# --------------------------------------------------------------------------- #
# 摄取主体
# --------------------------------------------------------------------------- #
def _ingest_records(state: State, records: Iterator[tuple[str, object]]) -> int:
    """候选入账 + 票据挂载; 返回 finder 记录数。"""
    finder_count = 0
    gap_seen = 0
    root = (state.repo or {}).get("cwd", "")
    for _label, result in records:
        kind = _classify(result)
        if kind == "finder":
            finder_count += 1
            angle = str(result.get("angle") or "?")
            round_no, origin = 1, "sweep"
            if angle == config.GAP_ANGLE_KEY:
                gap_seen += 1
                round_no, origin = 1 + gap_seen, "gap"
            items = {"items": [{
                "angle": angle,
                "coverage": {"ran": True},
                "candidates": [dict(c, file=ledger.norm_file(c.get("file"), root))
                               for c in (result.get("candidates") or [])],
            }]}
            errors = ledger.validate_candidates_file(items)
            if errors:
                module_logger().warning(f"journal finder(angle={angle}) 校验失败跳过: {errors}")
                state.coverage.setdefault("angles", {}).setdefault(
                    angle, {"ran": False, "raw": 0, "fresh": 0, "dropped": 0})
                continue
            ledger.ingest_candidates(state, items, origin=origin, round_no=round_no)
        elif kind == "refuter":
            cand = _match_candidate(state, result.get("file"), result.get("line"),
                                    result.get("summary"))
            if cand is None:
                module_logger().warning(
                    f"journal: refuter 返回无法匹配候选: {result.get('file')}:{result.get('line')}")
                continue
            cand.setdefault("votes", []).append({
                "lens": result.get("lens") or "correctness",
                "verdict": result.get("verdict"),
                "evidence": str(result.get("evidence") or "")[:_COMPACT_EVIDENCE_LIMIT],
                "severity": result.get("severity") or cand.get("severity"),
                "source": "agent",
            })
    state.cursor["round"] = 1 + gap_seen
    return finder_count


def _finalize_all(state: State) -> None:
    """终裁: 有票走票数法; 无票(workflow 预算耗尽保留为 PLAUSIBLE)显式落账。"""
    for c in state.candidates:
        if c.get("status") in ("dup", "verified", "unverified"):
            continue
        if not c.get("votes"):
            c["evidence"] = ("workflow 预算耗尽前未经反驳(deep 保留 PLAUSIBLE) —— "
                             "无引证可用, 见 journal")
        verify.finalize_candidate(state, c)


def _account_rounds(state: State) -> None:
    """轮次收敛记账(dry 重算自 round_history 的 fresh)。"""
    hist = state.accounting.setdefault("round_history", [])
    dry = 0
    for h in sorted(hist, key=lambda x: x.get("round", 0)):
        h["kept_after_verify"] = sum(1 for c in state.candidates if c.get("kept"))
        h["dry"] = h.get("fresh", 0) == 0
        dry = dry + 1 if h["dry"] else 0
    state.cursor["dry_streak"] = dry
    state.accounting["rounds"] = state.cursor["round"]
    state.cursor["pending_verify"] = []
    state.cursor["pending_angles"] = []
    state.phase, state.cursor["awaiting"] = "report", "details"


def ingest_transcript(state: State, transcript_dir: Path) -> str:
    """摄取 transcript journal: 候选入账 → 票据终裁 → 轮次/账目重算 → 进入报告。

    载体 1 的 gap 循环在 workflow 内部完成(maxRounds 已由扫描计划传入),
    摄取后引擎直接进入报告阶段 —— 不再开引擎侧 gap 轮。
    """
    count = _ingest_records(state, records_from_transcript(transcript_dir))
    if count == 0:
        raise ValueError(f"journal 中未发现 finder 返回: {transcript_dir}")
    _finalize_all(state)
    _account_rounds(state)
    return (f"journal 摄取: {count} finder 返回, "
            f"{state.accounting.get('total_seen', 0)} 入账, "
            f"{len(state.accounting.get('round_history') or [])} 轮")


def load_compact(path) -> dict:
    """读取压缩 journal(deep-review.js 返回值的 journal 字段落盘)。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "finders" not in data:
        raise ValueError("压缩 journal 缺少 finders 字段")
    return data


def apply_compact(state: State, data: dict) -> str:
    """应用压缩 journal(与 transcript 路径同构) + JS 计数奇偶注记。"""
    count = _ingest_records(state, records_from_compact(data))
    if count == 0:
        raise ValueError("压缩 journal 中无 finder 返回")
    _finalize_all(state)
    _account_rounds(state)
    js = data.get("counters") or {}
    ours = {"total_seen": state.accounting.get("total_seen", 0),
            "dup_dropped": state.accounting.get("dup_dropped", 0)}
    for key, value in ours.items():
        theirs = js.get(key)
        if theirs is not None and theirs != value:
            state.accounting.setdefault("parity_notes", []).append(
                f"{key}: JS={theirs} Python={value}(以 Python 台账为准)")
    return f"压缩 journal 摄取: {count} finder 返回"
