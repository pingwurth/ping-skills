"""载体 1(Workflow 工具)结果摄取: 一切账目在 Python 侧从原始 agent 返回重算。

两条通道:
    1. transcript journal —— Workflow 工具原生持久化 <transcriptDir>/journal.jsonl
       (记录每个 agent 的真实返回值); 主上下文只搬运一个路径, 零逐字转录漂移。
    2. 压缩 journal —— journal 不可读时, deep-review.js 返回值携带的压缩台账
       (evidence 截断 MAX_EVIDENCE_CHARS/条), LLM 一次 Write 落盘后经 --file 提交。

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
# P7 回修: 证据截断统一 config.MAX_EVIDENCE_CHARS(与 inline/载体 2 同限)——
# 原版两条摄取路径都截 400 字, 同一票不同载体证据量实质不同; JS 侧
# JOURNAL_EVIDENCE_CHARS 已同步, schema maxLength 本就允许 4000。
_EVIDENCE_LIMIT = config.MAX_EVIDENCE_CHARS


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


def _match_pool(state: State, file, line, summary,
                 file_index: Optional[dict] = None,
                 token_cache: Optional[dict] = None) -> tuple[Optional[dict], int]:
    """把 refuter 自描述返回匹配回台账候选; 返回 (候选, 带内命中数)。

    回修 c051: echo 的 file:line 可能与台账条目有小漂移(多 finder 对同一缺陷
    的行号取点不同), 只做精确匹配会让票据落在 dup 副本或完全丢失 —— 与
    find_dup 同语义: 同 file + 行号 ±DEDUP_LINE_BAND + 摘要重叠; 精确命中
    优先, 带内命中唯一才算(歧义返回 None, 计数交调用方注记 —— P7 回修,
    原版静默丢票只在日志一行)。

    P6 回修: file_index/token_cache 由调用方预建(按归一文件索引 + 摘要
    token 缓存)。匹配语义逐字不变(JS isDup 是 parity 锁定孪生)。
    """
    from . import config as _config
    nf = ledger.norm_file(file, ledger.norm_root(state))
    exact = None
    banded = []
    toks = ledger.tokens_of(summary or "")
    pool = file_index.get(nf) if file_index is not None else None
    if pool is None:
        pool = [c for c in state.candidates
                if c.get("file") == nf and c.get("status") != "dup"]
    for c in pool:
        if c.get("status") == "dup":
            continue
        if (c.get("line") or 0) == (line or 0) and c.get("summary") == summary:
            exact = c
            break
        if abs((c.get("line") or 0) - (line or 0)) <= _config.DEDUP_LINE_BAND:
            if token_cache is not None:
                ctoks = token_cache.get(c.get("id"))
                if ctoks is None:
                    ctoks = ledger.tokens_of(c.get("summary", ""))
                    token_cache[c.get("id")] = ctoks
            else:
                ctoks = ledger.tokens_of(c.get("summary", ""))
            if ledger.overlap_toks(toks, ctoks):
                banded.append(c)
    if exact is not None:
        return exact, 0
    if len(banded) == 1:
        return banded[0], 1
    return None, len(banded)


def _match_candidate(state: State, file, line, summary,
                     file_index: Optional[dict] = None,
                     token_cache: Optional[dict] = None) -> Optional[dict]:
    """兼容外壳(测试/旧行为): 只返回候选。"""
    return _match_pool(state, file, line, summary,
                       file_index=file_index, token_cache=token_cache)[0]


# --------------------------------------------------------------------------- #
# 摄取主体
# --------------------------------------------------------------------------- #
def _ingest_records(state: State, records: Iterator[tuple[str, object]]) -> int:
    """候选入账 + 票据挂载; 返回 finder 记录数。

    逐条校验逐条丢弃(计数可见) —— 全有或全无的整批跳过曾让 96 条原始候选
    静默变 64(自食验收观察项 1 的根因): 单条非法(如超长 summary)只丢那一条,
    计入 accounting.invalid_dropped 与 coverage.dropped_invalid。
    """
    finder_count = 0
    gap_seen = 0
    root = ledger.norm_root(state)
    # P6: 回配索引(按归一文件)+ 摘要 token 缓存; 有新候选入账后置脏重建
    file_index: dict = {}
    token_cache: dict = {}
    index_dirty = True
    for _label, result in records:
        kind = _classify(result)
        if kind == "finder":
            finder_count += 1
            angle = str(result.get("angle") or "?")
            round_no, origin = 1, "sweep"
            if angle == config.GAP_ANGLE_KEY:
                gap_seen += 1
                round_no, origin = 1 + gap_seen, "gap"
            valid: list[dict] = []
            cov = state.coverage.setdefault("angles", {}).setdefault(
                angle, ledger.default_angle_coverage(True))
            for c in (result.get("candidates") or []):
                if not isinstance(c, dict):
                    c = {}
                c = dict(c, file=ledger.norm_file(c.get("file"), root))
                errs = ledger.validate_candidate(c, f"journal:{angle}")
                if errs:
                    module_logger().warning(f"journal 候选非法丢弃: {errs}")
                    state.accounting["invalid_dropped"] = \
                        state.accounting.get("invalid_dropped", 0) + 1
                    cov["dropped_invalid"] = cov.get("dropped_invalid", 0) + 1
                    continue
                valid.append(c)
            items = {"items": [{"angle": angle, "coverage": {"ran": True},
                                "candidates": valid}]}
            if valid:
                ledger.ingest_candidates(state, items, origin=origin, round_no=round_no)
                index_dirty = True  # 新候选入账 → 回配索引下次 refuter 记录前重建
        elif kind == "refuter":
            if index_dirty:
                file_index = {}
                for c in state.candidates:
                    if c.get("status") != "dup":
                        file_index.setdefault(c.get("file"), []).append(c)
                index_dirty = False
            cand, n_banded = _match_pool(state, result.get("file"), result.get("line"),
                                         result.get("summary"),
                                         file_index=file_index, token_cache=token_cache)
            if cand is None:
                # P7 回修: 歧义回显不再静默 —— parity 注记上报告 + 计数
                if n_banded > 1:
                    state.accounting.setdefault("parity_notes", []).append(
                        f"refuter 回显歧义: {result.get('file')}:{result.get('line')} "
                        f"同时命中 {n_banded} 个带内候选, 票丢弃(人工对照)")
                    state.accounting["ambiguous_echo_votes"] = \
                        state.accounting.get("ambiguous_echo_votes", 0) + 1
                module_logger().warning(
                    f"journal: refuter 返回无法匹配候选: {result.get('file')}:{result.get('line')}")
                continue
            # P7 回修: 票据过枚举校验(原版完全绕过 validate_votes_file, 任意
            # lens/severity 字符串原样入票计入多数)—— lens/severity 非枚举或
            # evidence 缺失/超长者丢弃计数, 不再污染裁决与 severity_final
            lens = result.get("lens") or "correctness"
            sev = result.get("severity") or cand.get("severity")
            ev = str(result.get("evidence") or "").strip()
            if (lens not in config.LENSES or sev not in config.SEVERITIES
                    or not ev or len(ev) > config.MAX_EVIDENCE_CHARS):
                module_logger().warning(
                    f"journal 票据非法丢弃({result.get('file')}:{result.get('line')}): "
                    f"lens={lens!r} severity={sev!r} evidence_len={len(ev)}")
                state.accounting["invalid_votes"] = \
                    state.accounting.get("invalid_votes", 0) + 1
                continue
            # P10 回修(c021): (id,lens) 唯一性 —— 与 validate_votes_file 对齐,
            # 重复透镜票在法定人数里双计可翻转裁决(实证 [C,C,R,R*]→PLAUSIBLE)
            if lens in {v.get("lens") for v in (cand.get("votes") or [])}:
                state.accounting["dup_lens_votes"] = \
                    state.accounting.get("dup_lens_votes", 0) + 1
                module_logger().warning(
                    f"journal 重复透镜票丢弃: {cand.get('id')} lens={lens}")
                continue
            cand.setdefault("votes", []).append({
                "lens": lens,
                "verdict": result.get("verdict"),
                "evidence": ev[:_EVIDENCE_LIMIT],
                "severity": sev,
                "source": "agent",
            })
    state.cursor["round"] = 1 + gap_seen
    return finder_count


def _finalize_all(state: State) -> list[dict]:
    """终裁; 返回需补透镜的升级候选 [{id, lenses}](调用方据此进入 verify 相位)。

    回修 c023: quick 模式走 floor-only 语义(verdict 保持 null), 不得套用
    mode keep 阈值; 回修 c018: 单票把 P2/P3 升为 P0/P1 时改写 severity,
    使聚合按高危法定票数走。
    P7 回修: 升级不再"改写后立即按现有票聚合"的降级副本 —— 与
    verify.apply_votes 同机制: 退回复核其余透镜(升级候选经 pending_verify
    由常规验证轮补票终裁, P0/P1 永不躺在单票上, SKILL.md §7 契约)。
    """
    if state.mode == "quick":
        verify.finalize_without_verify(state)
        return []
    pending: list[dict] = []
    for c in state.candidates:
        if c.get("status") in ("dup", "verified", "unverified"):
            continue
        votes = c.get("votes") or []
        if not votes:
            c["evidence"] = ("workflow 预算耗尽前未经反驳(deep 保留 PLAUSIBLE) —— "
                             "无引证可用, 见 journal")
        elif not verify.is_high(c.get("severity")):
            highs = [v for v in votes if verify.is_high(v.get("severity"))]
            if highs:
                # 升级: finder 的 severity 猜测让位于 refuter 判定
                c["severity"] = highs[0].get("severity")
                if len(votes) == 1:
                    # 单票: 退回复核补其余透镜再终裁(P0/P1 永不躺在单票上)
                    rest = [l for l in config.LENSES
                            if l not in {v.get("lens") for v in votes}]
                    pending.append({"id": c["id"], "lenses": rest})
                    continue
                # P9 回修(自食验收 P1): 多票(JS 升级路径已补齐 2-3 透镜,
                # 或带内回配附加)不再被 len(votes)==1 守卫跳过 —— 原版对
                # 这类候选按非高危单票语义聚合(votes[0] 一票定生死), JS 侧
                # 三透镜 quorum 与 Python 双向分歧(实证回归); 改写后按高危
                # quorum 直接终裁, 与 deep-review.js aggregateVerdict 对齐。
        verify.finalize_candidate(state, c)
    return pending


def _account_rounds(state: State, to_report: bool = True) -> None:
    """轮次收敛记账(dry 重算自 round_history 的 fresh)。

    P7 回修: kept_after_verify 只写末轮 —— 原版把最终 kept 总量覆写进每一条
    round_history, 与 verify.finalize_round 的逐轮快照语义不同(同一字段两
    载体含义不同); journal 一次性摄取无法回溯逐轮快照, 末轮即当前总量。
    to_report=False(存在升级补透镜候选)时不清队列不推进相位。
    """
    hist = state.accounting.setdefault("round_history", [])
    dry = 0
    for h in sorted(hist, key=lambda x: x.get("round", 0)):
        h["dry"] = h.get("fresh", 0) == 0
        dry = dry + 1 if h["dry"] else 0
    if hist:
        last = max(hist, key=lambda x: x.get("round", 0))
        last["kept_after_verify"] = sum(1 for c in state.candidates if c.get("kept"))
    if to_report:
        # P11(c024/c031): 有升级候选时(to_report=False)不预置 dry_streak ——
        # 补透镜后的 verify.finalize_round 会对同末轮再计, 原版双计使 deep
        # 提前一轮误读收敛(实证); 直接收口路径才写全史 streak
        state.cursor["dry_streak"] = dry
    state.accounting["rounds"] = state.cursor["round"]
    if to_report:
        state.cursor["pending_verify"] = []
        state.cursor["pending_angles"] = []
        state.phase, state.cursor["awaiting"] = "report", "details"


def _reconcile_coverage(state: State) -> None:
    """P7 回修: 计划角度对账 —— 死 finder 的覆盖空洞在台账可见。

    JS 侧 {ran:false} 注册(v1.4.1 #10)只活在被丢弃的 workflow 返回值里;
    引擎侧按 sweep_plan 角度补齐缺省条目, coverage 空洞在 ledger.json 可见
    ('no silent caps/coverage holes' 契约)。
    """
    for a in (state.sweep_plan or {}).get("angles") or []:
        state.coverage.setdefault("angles", {}).setdefault(
            a, ledger.default_angle_coverage(False))


def _parity_notes(state: State, js: dict) -> None:
    """JS 内部计数 vs Python 重算的可见对照(分歧记入 parity_notes, 不静默)。

    口径(P8 回修): 两侧同为**帽后基数** —— JS 在计数前已把单 finder 截到
    8(raw.slice(0,8)), Python 摄取同样先截后算; 超帽丢弃只在
    coverage.dropped 可见, 不进 raw 不变式(原版把 py_overcap 计入 py_raw,
    finder 返回 >8 即确定性触发"原始候选总数不一致"假警报指控 workflow)。
    不变式 = 帽后总数两侧相等; 分桶差异若能被 invalid 丢弃解释, 注明成因
    而非告警。
    """
    acc = state.accounting
    fresh = sum(1 for c in state.candidates if c["status"] != "dup")
    py_fresh, py_dup = fresh, acc.get("dup_dropped", 0)
    py_invalid = acc.get("invalid_dropped", 0)
    js_fresh = js.get("total_seen", js.get("totalSeen"))
    js_dup = js.get("dup_dropped", js.get("dupDropped"))
    if js_fresh is None or js_dup is None:
        return
    js_raw = int(js_fresh) + int(js_dup)
    py_raw = py_fresh + py_dup + py_invalid
    notes = acc.setdefault("parity_notes", [])
    if js_raw != py_raw:
        notes.append(f"raw: JS={js_raw} Python={py_raw} —— 原始候选总数不一致, "
                     "存在未记账的丢弃(人工查 coverage 与 journal)")
        return
    diff = abs(int(js_fresh) - py_fresh) + abs(int(js_dup) - py_dup)
    if diff == 0:
        return
    if diff <= py_invalid:
        # P11(c038): 分桶差与 invalid 数在 raw 门下恒相等 —— 无法从观测区分
        # "invalid 全解释"与"invalid+去重语义分歧并存", 原版断言"完全解释/
        # 以后不再发生"会掩盖真实跨引擎分歧(实证); 一律引导人工对照
        notes.append(f"分桶分歧 fresh(JS={js_fresh}/Py={py_fresh}) dup(JS={js_dup}/Py={py_dup}) "
                     f"与 {py_invalid} 条 invalid 丢弃并存 —— 无法排除去重语义分歧, "
                     "人工对照 journal 与 coverage")
    else:
        notes.append(f"分桶分歧 fresh(JS={js_fresh}/Py={py_fresh}) dup(JS={js_dup}/Py={py_dup}) "
                     "超出 invalid 丢弃可解释范围 —— 去重语义需人工对照")


def ingest_transcript(state: State, transcript_dir: Path,
                      js_counters: Optional[dict] = None) -> str:
    """摄取 transcript journal: 候选入账 → 票据终裁 → 轮次/账目重算 → 进入报告。

    js_counters(可选): workflow 返回值里的计数(rounds/totalSeen/dupDropped),
    用于跨引擎 parity 对照(分歧记入 parity_notes 上报告)。

    载体 1 的 gap 循环在 workflow 内部完成(maxRounds 已由扫描计划传入),
    摄取后引擎直接进入报告阶段 —— 不再开引擎侧 gap 轮。
    """
    count = _ingest_records(state, records_from_transcript(transcript_dir))
    if count == 0:
        raise ValueError(f"journal 中未发现 finder 返回: {transcript_dir}")
    _reconcile_coverage(state)
    upgrades = _finalize_all(state)
    _account_rounds(state, to_report=not upgrades)
    if upgrades:
        # 单票升级的候选退回验证相位补其余透镜(P7: 与 verify.apply_votes 同机制)
        state.cursor["pending_verify"] = upgrades
        state.phase, state.cursor["awaiting"] = "verify", "verdicts"
    if js_counters:
        _parity_notes(state, js_counters)
    return (f"journal 摄取: {count} finder 返回, "
            f"{state.accounting.get('total_seen', 0)} 入账, "
            f"{len(state.accounting.get('round_history') or [])} 轮"
            + (f", {len(upgrades)} 条升级待补透镜" if upgrades else ""))


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
    _reconcile_coverage(state)
    upgrades = _finalize_all(state)
    _account_rounds(state, to_report=not upgrades)
    if upgrades:
        state.cursor["pending_verify"] = upgrades
        state.phase, state.cursor["awaiting"] = "verify", "verdicts"
    js = data.get("counters") or {}
    if js:
        _parity_notes(state, js)
    return (f"压缩 journal 摄取: {count} finder 返回"
            + (f", {len(upgrades)} 条升级待补透镜" if upgrades else ""))
