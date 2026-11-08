#!/usr/bin/env python3
"""dd.py — design-doc-writer 唯一 CLI 入口。

职责：问题台账（ledger.json，LLM 禁手编）、frontier 轮次计算、
文档门禁（章节完整性 / mermaid 规约 / 未决标记 / ADR 对账）、
Draft→Confirmed 状态机、原子落盘、断点续跑。

轻协议（对齐 sensors-analyze）：stdout = 人类可读摘要 + 末行唯一
`next_step: <指令>`；详细日志走 --log；退出码契约——
  0 = 完成 / 门禁全 PASS
  1 = 正常流转信号（frontier 有待办 / check 有 FAIL / confirm 被拒 / finish 未双确认）
  2 = 执行异常（参数错 / 台账缺失或损坏 / 非法状态流转 / IO 错误）

纯 stdlib。
"""
from __future__ import annotations

import argparse
import atexit
import json
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path

# ==================== 常量（单一事实源） ====================

SKILL_NAME = "design-doc-writer"
LEDGER_VERSION = 1
ARTIFACT_DIR = ".agent/design-doc-writer"
LEDGER_NAME = "ledger.json"
LOG_NAME = "dd.log"

PHASES = ["intake", "analyzing", "grilling", "analysis-doc", "design-doc", "done"]
# 合法阶段边（done 只能经 finish 进入，不在此表）
PHASE_EDGES = {
    "intake": {"analyzing"},
    "analyzing": {"grilling"},
    "grilling": {"analysis-doc", "analyzing"},
    "analysis-doc": {"design-doc", "grilling"},
    "design-doc": {"grilling"},
    "done": set(),
}

DIMENSIONS = [
    "goal", "scope", "technical", "edge",
    "ui", "data-model", "api", "compat", "cache", "nonfunc", "release", "ops",
]

# (doc, section_id, H2 标题, 默认启用) —— H2 标题与 references/doc-templates.md 严格同文
MANIFEST_DEFAULTS = [
    ("analysis", "background", "背景与目标", True),
    ("analysis", "scope", "范围：IN 与 OUT", True),
    ("analysis", "findings", "现状与事实调研", True),
    ("analysis", "glossary", "术语表", True),
    ("analysis", "decisions", "决策表", True),
    ("analysis", "open-questions", "未决与延期", True),
    ("design", "overview", "功能概述", True),
    ("design", "ui", "UI 与页面交互", True),
    ("design", "core-logic", "核心逻辑说明", True),
    ("design", "api", "接口设计", True),
    ("design", "db", "数据库设计", True),
    ("design", "cache", "缓存设计", False),
    ("design", "nonfunc", "非功能性设计", True),
    ("design", "release", "灰度与回滚", False),
    ("design", "tdd", "TDD 测试用例", True),
    ("design", "adr-index", "决策与 ADR 索引", True),
]
ALL_SECTION_IDS = {sid for _, sid, _, _ in MANIFEST_DEFAULTS}

# shape → 对默认清单的覆盖（未列出者保持 default）
SHAPE_PRESETS = {
    "web": {"cache": True},
    "api": {"ui": False},
    "backend": {"ui": False, "cache": True, "release": True},
    "library": {"ui": False, "db": False},
}

# 知识库探测（按优先级排序）：(kind, 目录名, 检索指引)
KB_SOURCES = [
    ("codegraph", ".codegraph", "codegraph MCP：codegraph_explore 一次拿到相关符号源码+调用路径"),
    ("repowise", ".repowise", "repowise MCP：search_codebase / get_answer / get_context"),
    ("pwiki", ".pwiki", "pwiki：用 pwiki 技能检索（wiki.db / published wiki）；无检索入口时直接读发布目录 markdown"),
    ("wiki", ".wiki", "openwiki MCP 可用时 openwiki_search / openwiki_read；否则直接读目录内 markdown"),
    ("graphify", "graphify-out", "graphify：图查询（god nodes / query / path）"),
]

MERMAID_ALLOWED = {"flowchart", "graph", "sequencediagram", "classdiagram",
                   "statediagram", "erdiagram"}  # 图型首词小写并剥 `-v2` 等版本后缀后比对
MERMAID_ALIAS = {"graph": "flowchart"}  # 归一化报告用
MAX_NODES = 15   # 单图复杂度 WARN 阈值（与 references/mermaid-uml.md 一致）
MAX_EDGES = 25

PLACEHOLDERS = ["TODO", "TBD", "FIXME", "待补充", "待定", "<占位", "<动态"]
UNRESOLVED_RE = re.compile(r"\[未决:Q\d+\]")
DEFERRED_RE = re.compile(r"\[延期:(Q\d+)\]")
ADR_REF_RE = re.compile(r"ADR-(\d{4})")
STATUS_LINE_RE = re.compile(r"^状态[:：]\s*(Draft|Confirmed)\s*$", re.M)
ADR_FILE_RE = re.compile(r"^(\d{4})-.*\.md$")

EXIT_OK, EXIT_FLOW, EXIT_ERR = 0, 1, 2

# ==================== 日志（移植自 sensors-analyze/scripts/utils.py） ====================

_ORIGINAL_STDERR = sys.stderr
_STDERR_REDIRECT = None
_ATEXIT_REGISTERED = False
LOG = logging.getLogger(SKILL_NAME)


def _restore_stderr() -> None:
    global _STDERR_REDIRECT
    sys.stderr = _ORIGINAL_STDERR
    if _STDERR_REDIRECT is not None:
        try:
            _STDERR_REDIRECT.close()
        except OSError:
            pass
        _STDERR_REDIRECT = None


def setup_logging(log_path: Path) -> None:
    """全部输出写日志文件；stderr 一并重定向（进程退出时恢复）。

    对齐 sensors-analyze 现行为：每次调用 mode="w" 重建日志文件，
    命令头由本函数写入。
    """
    global _STDERR_REDIRECT, _ATEXIT_REGISTERED
    LOG.setLevel(logging.DEBUG)
    for h in list(LOG.handlers):  # 幂等：摘掉上次配置的 handler
        LOG.removeHandler(h)
        h.close()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(str(log_path), encoding="utf-8", mode="w")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    LOG.addHandler(fh)
    try:
        f = open(str(log_path), "a", encoding="utf-8")
    except OSError:
        pass  # 保留原始 stderr 作回退
    else:
        if _STDERR_REDIRECT is not None:
            try:
                _STDERR_REDIRECT.close()
            except OSError:
                pass
        _STDERR_REDIRECT = f
        sys.stderr = f
        if not _ATEXIT_REGISTERED:
            atexit.register(_restore_stderr)
            _ATEXIT_REGISTERED = True


# ==================== 错误类型 ====================


class DDError(Exception):
    """执行异常（exit 2）。next_ 可携带修复指引。"""

    def __init__(self, msg: str, next_: str | None = None):
        super().__init__(msg)
        self.msg = msg
        self.next_ = next_


# ==================== 台账 ====================


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def artifact_dir(repo: Path) -> Path:
    return repo / ARTIFACT_DIR


def ledger_path(repo: Path) -> Path:
    return artifact_dir(repo) / LEDGER_NAME


def default_log_path(repo: Path) -> Path:
    return artifact_dir(repo) / LOG_NAME


def validate_ledger(data: object) -> list[str]:
    """结构校验，返回错误清单（空 = 合法）。"""
    errs: list[str] = []
    if not isinstance(data, dict):
        return ["台账不是 JSON 对象"]
    if data.get("version") != LEDGER_VERSION:
        errs.append(f"version 应为 {LEDGER_VERSION}，实际 {data.get('version')!r}")
    if data.get("phase") not in PHASES:
        errs.append(f"phase 非法：{data.get('phase')!r}")
    proj = data.get("project")
    if not isinstance(proj, dict) or not isinstance(proj.get("feature"), str) or not proj.get("feature"):
        errs.append("project.feature 缺失或非非空字符串")
    qs = data.get("questions")
    if not isinstance(qs, list):
        errs.append("questions 不是列表")
    else:
        for i, q in enumerate(qs):
            if not isinstance(q, dict):
                errs.append(f"questions[{i}] 不是对象")
                continue
            for k in ("id", "title", "status"):
                if not isinstance(q.get(k), str) or not q.get(k):
                    errs.append(f"questions[{i}].{k} 缺失")
            if q.get("status") not in ("open", "answered", "deferred"):
                errs.append(f"questions[{i}].status 非法：{q.get('status')!r}")
            if q.get("dimension") not in DIMENSIONS:
                errs.append(f"questions[{i}].dimension 非法：{q.get('dimension')!r}")
    docs = data.get("docs")
    if not isinstance(docs, dict):
        errs.append("docs 缺失")
    else:
        for d in ("analysis", "design"):
            dd = docs.get(d)
            if not isinstance(dd, dict) or dd.get("status") not in ("draft", "confirmed"):
                errs.append(f"docs.{d}.status 非法：{docs.get(d)!r}")
    man = data.get("manifest")
    if not isinstance(man, dict) or not isinstance(man.get("sections"), list):
        errs.append("manifest.sections 缺失或非列表")
    if not isinstance(data.get("adrs_dir"), str) or not data.get("adrs_dir"):
        errs.append("adrs_dir 缺失")
    if not isinstance(data.get("history"), list):
        errs.append("history 不是列表")
    return errs


def load_ledger(repo: Path) -> dict:
    p = ledger_path(repo)
    if not p.is_file():
        raise DDError(
            f"台账不存在：{p}（先在目标库 init）",
            next_="收集需求/知识库/代码库信息后执行 dd.py init --feature <slug>",
        )
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise DDError(f"台账损坏，无法解析：{p}（{e}）", next_="台账只能经 dd.py 子命令修改；损坏时备份后重新 init") from e
    errs = validate_ledger(data)
    if errs:
        raise DDError("台账结构非法（" + "；".join(errs) + "）",
                      next_="台账禁止手编；只能用 dd.py 子命令修复，损坏时备份后重新 init")
    return data


def save_ledger(repo: Path, ledger: dict) -> None:
    """原子写：同目录临时文件 + fsync + os.replace；失败清理临时文件。"""
    ledger["project"]["updated_at"] = now_iso()
    d = artifact_dir(repo)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f"{LEDGER_NAME}.tmp-{os.getpid()}"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(ledger, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, ledger_path(repo))
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def add_history(ledger: dict, event: str, detail: str = "") -> None:
    ledger.setdefault("history", []).append({"at": now_iso(), "event": event, "detail": detail})


def q_by_id(ledger: dict) -> dict:
    return {q["id"]: q for q in ledger["questions"]}


def next_qid(ledger: dict) -> str:
    n = 0
    for q in ledger["questions"]:
        m = re.match(r"^Q(\d+)$", q["id"])
        if m:
            n = max(n, int(m.group(1)))
    return f"Q{n + 1:03d}"


def adrs_dir_path(repo: Path, ledger: dict) -> Path:
    return repo / ledger["adrs_dir"]


def next_adr_number(repo: Path, ledger: dict) -> int:
    n = 0
    d = adrs_dir_path(repo, ledger)
    if d.is_dir():
        for p in d.iterdir():
            m = ADR_FILE_RE.match(p.name)
            if m:
                n = max(n, int(m.group(1)))
    return n + 1


# ==================== frontier（纯图算法） ====================


def compute_frontier(questions: list[dict]):
    """返回 (frontier, blocked)。

    frontier：status==open 且全部依赖 answered 的问题（按 round、id 排序）。
    blocked ：open 且存在未 answered 依赖的 (问题, [未 answered 依赖])。
    """
    by_id = {q["id"]: q for q in questions}
    frontier: list[dict] = []
    blocked: list[tuple[dict, list[dict]]] = []
    for q in sorted(questions, key=lambda x: (x.get("round", 1), x["id"])):
        if q["status"] != "open":
            continue
        deps = [by_id[d] for d in q.get("depends_on", []) if d in by_id]
        if all(d["status"] == "answered" for d in deps):
            frontier.append(q)
        else:
            blocked.append((q, [d for d in deps if d["status"] != "answered"]))
    return frontier, blocked


def question_stats(ledger: dict) -> dict:
    stats = {"total": 0, "answered": 0, "open": 0, "deferred": 0, "adr": 0}
    for q in ledger["questions"]:
        stats["total"] += 1
        stats[q["status"]] += 1
        if (q.get("adr") or {}).get("needed"):
            stats["adr"] += 1
    return stats


# ==================== 门禁（check/confirm 共用） ====================


class Item:
    """一条门禁结果。level: PASS/FAIL/WARN。"""

    def __init__(self, level: str, code: str, msg: str, hint: str = ""):
        self.level, self.code, self.msg, self.hint = level, code, msg, hint


def _mermaid_blocks(text: str) -> list[list[str]]:
    """提取 ```mermaid 围栏块的内容行；悬开块以已读行返回。"""
    blocks: list[list[str]] = []
    cur: list[str] | None = None
    for line in text.splitlines():
        s = line.strip()
        if s == "```mermaid":
            cur = []
        elif cur is not None and s == "```":
            blocks.append(cur)
            cur = None
        elif cur is not None:
            cur.append(line)
    if cur is not None:
        blocks.append(cur)
    return blocks


def _mermaid_type(block: list[str]) -> str | None:
    for line in block:
        s = line.strip()
        if s:
            return s.split()[0].lower().split("-")[0]  # stateDiagram-v2 → statediagram
    return None


ARROW_RE = re.compile(r"-->>|-->|->>|-\.->|==>|--\|>|\|\|--")  # 各图型常见边的粗集


def _complexity(block: list[str], mtype: str) -> tuple[int, int]:
    """粗估 (节点数, 边数)。仅用于 WARN 阈值，不追求精确。"""
    if mtype == "sequencediagram":
        actors, edges = set(), 0
        for line in block:
            s = line.strip()
            m = re.match(r"^(?:participant|actor)\s+(\S+)", s)
            if m:
                actors.add(m.group(1))
            edges += len(ARROW_RE.findall(s))
            m2 = re.match(r"^(\S+)\s*(?:-->>|->>|-->|->)", s)
            if m2:
                actors.add(m2.group(1))
        return len(actors), edges
    nodes, edges = set(), 0
    for line in block:
        s = line.strip()
        if not s or s.startswith(("%%", "classDef", "style", "class ")):
            continue
        hits = ARROW_RE.findall(s)
        edges += len(hits)
        if hits:
            for seg in ARROW_RE.split(s):
                tok = seg.strip().split("[")[0].split("(")[0].strip()
                for piece in tok.split():
                    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", piece):
                        nodes.add(piece)
    if not edges:  # 无边的图（纯节点清单）按非空行估
        body = [l for l in block if l.strip() and not l.strip().startswith("%%")]
        return max(len(body), 1), 0
    return len(nodes), edges


def check_doc(repo: Path, ledger: dict, doc_key: str, refs_union: set[str]) -> list[Item]:
    """对单份文档跑检查项 ①~⑧（⑨⑩ 由 run_checks 汇总层处理）。"""
    items: list[Item] = []
    doc = ledger["docs"][doc_key]
    path = repo / doc["path"]

    if not path.is_file():
        items.append(Item("FAIL", "doc-missing", f"文档不存在：{doc['path']}",
                          "按 references/doc-templates.md 骨架创建文档（H1 下第二行写 状态: Draft）"))
        return items
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        items.append(Item("FAIL", "doc-read", f"文档读取失败：{e}"))
        return items
    lines = text.splitlines()

    # ① 章节完整性（H2 精确标题，且非空）
    headings = {}
    for i, line in enumerate(lines):
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            headings[m.group(1)] = i
    for s in ledger["manifest"]["sections"]:
        if s["doc"] != doc_key or not s["enabled"]:
            continue
        if s["title"] not in headings:
            items.append(Item("FAIL", "section-missing",
                              f"缺少章节「## {s['title']}」",
                              "章节标题须与 manifest 完全一致（dd.py MANIFEST_DEFAULTS 为单一事实源）"))
            continue
        j = headings[s["title"]]
        # 章节边界只认 H1/H2：H3+ 子标题及其内容都算本章节实体
        nxt = len(lines)
        for k in range(j + 1, len(lines)):
            if re.match(r"^#{1,2}\s", lines[k]):
                nxt = k
                break
        if not "\n".join(lines[j + 1:nxt]).strip():
            items.append(Item("FAIL", "section-empty", f"章节「{s['title']}」为空",
                              "补充内容；确不需要该章节时先 dd.py manifest --disable <id>"))

    # ② mermaid 围栏配平
    fences = sum(1 for l in lines if l.strip().startswith("```"))
    if fences % 2 != 0:
        items.append(Item("FAIL", "fence-imbalance",
                          f"代码围栏不配平（``` 共 {fences} 个）", "补齐缺失的闭合围栏"))

    # ③④ 图类型白名单 + 复杂度
    mermaid_n = 0
    for bi, block in enumerate(_mermaid_blocks(text), 1):
        mermaid_n += 1
        t = _mermaid_type(block)
        if t is None:
            items.append(Item("FAIL", "mermaid-empty", f"第 {bi} 张 mermaid 图为空"))
            continue
        if t not in MERMAID_ALLOWED:
            items.append(Item("FAIL", "mermaid-type",
                              f"第 {bi} 张图类型 `{t}` 不在白名单（flowchart/sequenceDiagram/classDiagram/stateDiagram/erDiagram）",
                              "逻辑设计不用 mindmap/gantt/pie 等，按 references/mermaid-uml.md 改型"))
            continue
        shown = MERMAID_ALIAS.get(t, t)
        nodes, edges = _complexity(block, t)
        if nodes > MAX_NODES or edges > MAX_EDGES:
            items.append(Item("WARN", "mermaid-complex",
                              f"第 {bi} 张图（{shown}）粗估 {nodes} 节点/{edges} 边，超上限 {MAX_NODES}/{MAX_EDGES}",
                              "拆分为多张图，每图只回答一个问题"))

    # ⑤ 未决/延期标记
    unresolved = UNRESOLVED_RE.findall(text)
    if unresolved:
        items.append(Item("FAIL", "unresolved-marker",
                          f"存在未决标记 {'、'.join(sorted(set(unresolved)))}",
                          "未决问题必须先 q-answer 或裁剪出范围（写进 IN/OUT）"))
    doc_deferred = set(DEFERRED_RE.findall(text))
    if doc_key == "design":
        if doc_deferred:
            items.append(Item("FAIL", "deferred-marker",
                              f"设计文档不允许延期标记：{'、'.join(sorted(doc_deferred))}",
                              "详细设计确认前延期问题必须答掉或裁剪出范围"))
    else:
        ledger_deferred = {q["id"] for q in ledger["questions"] if q["status"] == "deferred"}
        missing = ledger_deferred - doc_deferred
        dangling = doc_deferred - ledger_deferred
        if missing:
            items.append(Item("FAIL", "deferred-reconcile",
                              f"台账延期但文档未标注：{'、'.join(sorted(missing))}",
                              "在「未决与延期」节逐条 [延期:Qxxx] 标注"))
        if dangling:
            items.append(Item("FAIL", "deferred-reconcile",
                              f"文档延期标记在台账中不存在或非延期：{'、'.join(sorted(dangling))}",
                              "标记必须与台账 deferred 集合一一对应"))

    # ⑥ 占位符
    ph_hits = [p for p in PLACEHOLDERS if p in text]
    if ph_hits:
        items.append(Item("FAIL", "placeholder",
                          f"存在占位符残留：{'、'.join(ph_hits)}",
                          "所有占位符替换为具体内容后才能确认"))

    # ⑦ 状态行与台账一致
    m = STATUS_LINE_RE.search(text)
    expect = "Confirmed" if doc["status"] == "confirmed" else "Draft"
    if not m:
        items.append(Item("FAIL", "status-line", f"缺少状态行（H1 下应有 `状态: {expect}`）"))
    elif m.group(1) != expect:
        items.append(Item("FAIL", "status-line",
                          f"状态行为 {m.group(1)}，台账为 {doc['status']}（期望 {expect}）",
                          "状态行只能随 confirm 流转，勿手改漂移"))

    # ⑧ ADR 对账（b：文档引用→文件存在；a/c 在 run_checks 汇总层做全局对账）
    adir = adrs_dir_path(repo, ledger)
    adr_files = {}
    if adir.is_dir():
        for p in adir.iterdir():
            mm = ADR_FILE_RE.match(p.name)
            if mm:
                adr_files[mm.group(1)] = p
    for num in sorted(ADR_REF_RE.findall(text)):
        if num not in adr_files:
            items.append(Item("FAIL", "adr-ref",
                              f"文档引用 ADR-{num} 但 {ledger['adrs_dir']}/{num}-*.md 不存在",
                              "按分配路径补写 ADR 文件（references/doc-templates.md 的 ADR 模板）"))

    if doc_key == "design" and mermaid_n == 0 and any(
            s["doc"] == "design" and s["id"] == "core-logic" and s["enabled"]
            for s in ledger["manifest"]["sections"]):
        items.append(Item("WARN", "mermaid-none",
                          "设计文档没有任何 mermaid 图", "复杂逻辑应以图表达（硬约束 5）"))
    return items


def run_checks(repo: Path, ledger: dict, which: str) -> tuple[list[Item], bool]:
    """跑门禁。which: analysis|design|all。返回 (items, has_fail)。"""
    items: list[Item] = []
    docs = ["analysis", "design"] if which == "all" else [which]

    # 两份已存在文档的引用并集（ADR 全局对账 c 用）
    refs_union: set[str] = set()
    for d in ("analysis", "design"):
        p = repo / ledger["docs"][d]["path"]
        if p.is_file():
            try:
                refs_union |= set(ADR_REF_RE.findall(p.read_text(encoding="utf-8")))
            except OSError:
                pass

    for dk in docs:
        items.extend(check_doc(repo, ledger, dk, refs_union))
        # ⑨⑩ 前置
        stats = question_stats(ledger)
        if dk == "analysis" and stats["open"] > 0:
            items.append(Item("FAIL", "open-questions",
                              f"台账还有 {stats['open']} 个 open 问题",
                              "frontier 清零（全部 q-answer 或 --defer）后才能确认分析文档"))
        if dk == "design":
            if ledger["docs"]["analysis"]["status"] != "confirmed":
                items.append(Item("FAIL", "design-prereq",
                                  "分析文档尚未 confirmed",
                                  "先 check+confirm analysis，再 phase design-doc"))
            if stats["open"] > 0 or stats["deferred"] > 0:
                items.append(Item("FAIL", "design-prereq",
                                  f"设计确认要求台账零 open 零 deferred（当前 open={stats['open']} deferred={stats['deferred']}）",
                                  "延期问题要么答掉，要么裁剪进范围（更新 IN/OUT）"))

    # ⑧ 全局对账（不依赖 which，对台账里分配过的 ADR 负责）
    adir = adrs_dir_path(repo, ledger)
    for q in ledger["questions"]:
        adr = q.get("adr") or {}
        if adr.get("needed") and q["status"] == "answered":
            p = repo / adr.get("path", "")
            num_m = re.search(r"(\d{4})-", adr.get("path", ""))
            if not p.is_file():
                items.append(Item("FAIL", "adr-file",
                                  f"{q['id']} 分配了 ADR 但文件不存在：{adr.get('path')}",
                                  "按 references/doc-templates.md 的 ADR 模板立即补写"))
            elif num_m and num_m.group(1) not in refs_union:
                items.append(Item("FAIL", "adr-ref",
                                  f"ADR-{num_m.group(1)}（{q['id']}）未被任何决策表引用",
                                  "在分析或设计文档的决策表/ADR 索引中补 ADR-NNNN 引用"))
    has_fail = any(i.level == "FAIL" for i in items)
    return items, has_fail


def render_items(items: list[Item]) -> None:
    for i in items:
        line = f"[{i.level}] {i.code}: {i.msg}"
        if i.hint:
            line += f"\n       ↳ {i.hint}"
        print(line)


# ==================== 各子命令（返回 (exit_code, next_step)） ====================


def _detect_docs_root(repo: Path) -> str:
    if (repo / "docs").is_dir():
        return "docs"
    if (repo / "doc").is_dir():
        return "doc"
    return "docs"


def _agent_ignored(repo: Path) -> bool:
    gi = repo / ".gitignore"
    if not (repo / ".git").exists():
        return True  # 非 git 仓库，不做提醒
    if not gi.is_file():
        return False
    for line in gi.read_text(encoding="utf-8").splitlines():
        s = line.strip().rstrip("/")
        if s == ".agent":
            return True
    return False


def _brief_status(ledger: dict) -> str:
    stats = question_stats(ledger)
    return (f"phase: {ledger['phase']}  feature: {ledger['project']['feature']}  "
            f"问题 {stats['total']}：answered {stats['answered']} / open {stats['open']} / "
            f"deferred {stats['deferred']}  文档 analysis={ledger['docs']['analysis']['status']} "
            f"design={ledger['docs']['design']['status']}")


PHASE_GUIDE = {
    "intake": "输入已收集则 dd.py phase analyzing 进入分析阶段，随后 dd.py kb-scan",
    "analyzing": "dd.py kb-scan 后按 primary 知识库做事实调研，对照 references/question-dimensions.md 逐维度 dd.py q-add",
    "grilling": "dd.py frontier 取本轮问题，按 grilling 格式一轮全问（编号+推荐答案+---），用户答复后逐条 dd.py q-answer",
    "analysis-doc": "按 references/doc-templates.md 写需求分析文档（状态: Draft），dd.py check --doc analysis 迭代到 PASS，用户明确确认后 dd.py confirm --doc analysis --because \"<原话摘要>\"",
    "design-doc": "按模板写详细设计文档（接口表格化、复杂逻辑 mermaid、TDD 用例表），dd.py check --doc design 迭代到 PASS",
    "done": "流程已完成：向用户逐文件汇报两份文档、ADR 清单与台账位置",
}


def cmd_init(args, repo: Path) -> tuple[int, str]:
    if not re.fullmatch(r"[A-Za-z0-9_\-一-龥]+", args.feature):
        raise DDError(f"--feature 非法：{args.feature!r}（只允许字母/数字/_/-/中文，不含空格与斜杠）",
                      next_="换一个合法 slug 重新 init")
    lp = ledger_path(repo)
    if lp.is_file():
        ledger = load_ledger(repo)
        print("== 台账已存在（恢复模式，绝不重置） ==")
        print(_brief_status(ledger))
        print(f"台账：{lp}")
        return EXIT_OK, PHASE_GUIDE[ledger["phase"]]

    root = _detect_docs_root(repo)
    analysis = args.analysis_doc or f"{root}/design/{args.feature}/requirements-analysis.md"
    design = args.design_doc or f"{root}/design/{args.feature}/design.md"
    adrs_dir = args.adrs_dir or f"{root}/adr"

    preset = SHAPE_PRESETS[args.shape]
    sections = []
    for doc, sid, title, default in MANIFEST_DEFAULTS:
        sections.append({"doc": doc, "id": sid, "title": title,
                         "enabled": bool(preset.get(sid, default))})
    reason = f"shape={args.shape} 预置（可用 dd.py manifest --enable/--disable 调整）"
    ledger = {
        "version": LEDGER_VERSION,
        "note": "本文件只允许经 scripts/dd.py 子命令修改，禁止手编",
        "project": {"repo_root": str(repo.resolve()), "feature": args.feature,
                    "created_at": now_iso(), "updated_at": now_iso()},
        "phase": "intake",
        "kb": {"scanned_at": None, "detected": [], "openwiki_mcp": False,
               "primary": None, "note": "尚未 kb-scan"},
        "questions": [],
        "docs": {
            "analysis": {"path": analysis, "status": "draft", "confirmed_at": None, "because": None},
            "design": {"path": design, "status": "draft", "confirmed_at": None, "because": None},
        },
        "manifest": {"updated_at": now_iso(), "sections": sections, "reason": reason},
        "adrs_dir": adrs_dir,
        "history": [],
    }
    add_history(ledger, "init", f"feature={args.feature} shape={args.shape}")
    save_ledger(repo, ledger)

    print(f"== init 完成 ==  feature: {args.feature}  shape: {args.shape}")
    print(f"分析文档（建议路径）：{analysis}")
    print(f"设计文档（建议路径）：{design}")
    print(f"ADR 目录：{adrs_dir}")
    print(f"台账：{lp}")
    if not _agent_ignored(repo):
        print("提醒：.agent/ 尚未被 .gitignore 覆盖，建议添加（台账与日志不随仓库提交）")
    return EXIT_OK, ("dd.py phase analyzing 进入分析阶段，随后 dd.py kb-scan 探测知识库"
                     "（有 openwiki MCP 时加 --openwiki）")


def cmd_kb_scan(args, repo: Path) -> tuple[int, str]:
    ledger = load_ledger(repo)
    detected = []
    primary = None
    for kind, dirname, guide in KB_SOURCES:
        d = repo / dirname
        usable = d.is_dir() and any(d.iterdir())
        if d.is_dir():
            detected.append({"kind": kind, "detail": dirname + ("/" if usable else "（空目录）"),
                             "usable": usable})
        if usable and primary is None:
            primary = kind
    ledger["kb"] = {
        "scanned_at": now_iso(),
        "detected": detected,
        "openwiki_mcp": bool(args.openwiki),
        "primary": primary,
        "note": ("事实调研优先用 primary 对应工具；无知识库则降级直接读码"
                 if primary else "未探测到知识库，降级直接读码"),
    }
    add_history(ledger, "kb-scan", f"primary={primary} openwiki={bool(args.openwiki)}")
    save_ledger(repo, ledger)

    print("== kb-scan ==")
    guides = {k: g for k, _, g in KB_SOURCES}
    if detected:
        for d in detected:
            mark = "可用" if d["usable"] else "空目录（不可用）"
            print(f"  [{d['kind']}] {d['detail']} —— {mark}")
            if d["usable"]:
                print(f"      检索：{guides[d['kind']]}")
    else:
        print("  未探测到任何知识库目录（.codegraph/.repowise/.pwiki/.wiki/graphify-out）")
    if args.openwiki:
        print("  [openwiki] MCP 自报可用：openwiki_search / openwiki_read")
    print(f"primary：{primary or '（无 → 降级直接读码）'}（默认建议，可按问题性质改用任一可用源）")
    nxt = ("按 primary 做事实调研，对照 references/question-dimensions.md 逐维度 dd.py q-add"
           if primary else "直接读码做事实调研，对照 references/question-dimensions.md 逐维度 dd.py q-add")
    return EXIT_OK, nxt


def _add_one_question(ledger: dict, spec: dict) -> str:
    dim = spec.get("dimension", "")
    if dim not in DIMENSIONS:
        raise DDError(f"dimension 非法：{dim!r}（枚举见 SKILL.md / references/question-dimensions.md）",
                      next_="修正 dimension 后重试")
    for k in ("title", "body", "recommendation"):
        if not isinstance(spec.get(k), str) or not spec.get(k):
            raise DDError(f"问题缺少必填字段 {k}",
                          next_="每个问题都要有 title/body/recommendation（grilling：每题必附推荐答案）")
    by_id = q_by_id(ledger)
    deps_raw = spec.get("depends_on", []) or []
    if isinstance(deps_raw, str):
        deps_raw = [d.strip() for d in deps_raw.split(",") if d.strip()]
    for d in deps_raw:
        if d not in by_id:
            raise DDError(f"depends_on 引用不存在的问题：{d}", next_="依赖只能指向已存在的问题 id")
    qid = next_qid(ledger)
    deps = list(dict.fromkeys(deps_raw))
    if qid in deps:
        raise DDError("问题不能依赖自身")
    q = {
        "id": qid,
        "round": 1 + max([by_id[d]["round"] for d in deps], default=0),
        "dimension": dim,
        "title": spec["title"],
        "body": spec["body"],
        "recommendation": spec["recommendation"],
        "depends_on": deps,
        "status": "open",
        "answer": None,
        "answered_at": None,
        "adr": None,
        "facts": spec.get("facts") or None,
    }
    ledger["questions"].append(q)
    # 防御性环检查（新节点无入边，理论上不成环；批量乱序时兜底）
    _assert_acyclic(ledger)
    return qid


def _assert_acyclic(ledger: dict) -> None:
    by_id = q_by_id(ledger)
    color: dict[str, int] = {}

    def visit(qid: str) -> None:
        color[qid] = 1
        for d in by_id[qid].get("depends_on", []):
            if d not in by_id:
                continue
            if color.get(d) == 1:
                raise DDError(f"依赖成环：{d} ↔ {qid}", next_="检查 depends_on，依赖必须无环")
            if color.get(d) is None:
                visit(d)
        color[qid] = 2

    for qid in by_id:
        if color.get(qid) is None:
            visit(qid)


def cmd_q_add(args, repo: Path) -> tuple[int, str]:
    ledger = load_ledger(repo)
    if ledger["phase"] == "intake":
        raise DDError("phase=intake，先完成输入收集（kb-scan 与事实调研）再提问",
                      next_="先 dd.py kb-scan 并做事实调研")
    if ledger["phase"] == "done":
        raise DDError("phase=done 已终态", next_="流程已完成；如需修订请开启新特性")
    auto = False
    if ledger["phase"] == "analyzing":
        ledger["phase"] = "grilling"
        auto = True
        add_history(ledger, "phase", "analyzing→grilling（q-add 自动推进）")

    ids = []
    if args.batch:
        try:
            batch = json.loads(Path(args.batch).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise DDError(f"--batch 文件读取失败：{e}", next_="检查 questions.json 路径与格式（对象数组，按依赖顺序排列）") from e
        if not isinstance(batch, list) or not batch:
            raise DDError("--batch 须为非空 JSON 数组", next_="检查 questions.json 格式")
        for spec in batch:
            ids.append(_add_one_question(ledger, spec))
    else:
        spec = {"dimension": args.dimension, "title": args.title, "body": args.body,
                "recommendation": args.recommendation, "depends_on": args.depends_on,
                "facts": args.facts}
        ids.append(_add_one_question(ledger, spec))
    add_history(ledger, "q-add", "、".join(ids) + (f"（共 {len(ids)} 条）" if len(ids) > 1 else ""))
    save_ledger(repo, ledger)
    print(f"== q-add ==  新增：{'、'.join(ids)}" + ("（analyzing→grilling 自动推进）" if auto else ""))
    return EXIT_OK, "继续 q-add 直到问题识别完毕，然后 dd.py frontier 取本轮问题"


def cmd_q_answer(args, repo: Path) -> tuple[int, str]:
    ledger = load_ledger(repo)
    if ledger["phase"] in ("intake", "analyzing"):
        raise DDError(f"phase={ledger['phase']}，问题作答发生在 grilling 及之后",
                      next_="先 q-add 识别问题进入 grilling")
    if ledger["phase"] == "done":
        raise DDError("phase=done 已终态")
    if not args.answer and not args.defer:
        raise DDError("须提供 --answer <决策> 或 --defer --reason <原因>")
    if args.answer and args.defer:
        raise DDError("--answer 与 --defer 互斥")
    if args.defer and not args.reason:
        raise DDError("--defer 须同时给 --reason")
    if args.adr and not args.answer:
        raise DDError("--adr 只能搭配 --answer（先有决策才有 ADR）")

    q = q_by_id(ledger).get(args.id)
    if q is None:
        raise DDError(f"问题不存在：{args.id}", next_="dd.py status 查看台账中的问题 id")

    if args.defer:
        re_defer = q["status"] == "deferred"
        q["status"] = "deferred"
        q["answer"] = None
        q["answered_at"] = None
        add_history(ledger, "q-answer", f"{args.id} 延期{'（重新延期）' if re_defer else ''}：{args.reason}")
        save_ledger(repo, ledger)
        print(f"== q-answer ==  {args.id} → deferred（{args.reason}）")
        return EXIT_OK, "继续 dd.py frontier；延期问题会阻塞其下游，分析文档需 [延期:Qxxx] 标注"

    re_answer = q["status"] == "answered"
    adr_note = ""
    if args.adr:
        if not re.fullmatch(r"[a-z0-9\-]+", args.adr_slug or ""):
            raise DDError("--adr-slug 须为小写 ascii（[a-z0-9-]）",
                          next_="例如 async-export-job")
        n = next_adr_number(repo, ledger)
        adr_path = f"{ledger['adrs_dir']}/{n:04d}-{args.adr_slug}.md"
        q["adr"] = {"needed": True, "path": adr_path}
        adr_note = f"  ADR 分配：{adr_path}"
    q["status"] = "answered"
    q["answer"] = args.answer
    q["answered_at"] = now_iso()
    add_history(ledger, "q-answer",
                f"{args.id} {'重答' if re_answer else '决策'}：{args.answer}"
                + (f"  adr={q['adr']['path']}" if q.get("adr") else ""))
    save_ledger(repo, ledger)
    print(f"== q-answer ==  {args.id} → answered（{'重答，' if re_answer else ''}{args.answer}）{adr_note}")
    if adr_note:
        return EXIT_OK, (f"立即按 references/doc-templates.md 的 ADR 模板写 {q['adr']['path']}（一段即可），"
                         "再继续 dd.py frontier")
    return EXIT_OK, "继续处理 frontier：dd.py frontier"


def cmd_frontier(args, repo: Path) -> tuple[int, str]:
    ledger = load_ledger(repo)
    stats = question_stats(ledger)
    frontier, blocked = compute_frontier(ledger["questions"])
    print(f"== design-doc-writer 台账 ==  phase: {ledger['phase']}  feature: {ledger['project']['feature']}")
    print(f"问题 {stats['total']}：answered {stats['answered']} / open {stats['open']} / "
          f"deferred {stats['deferred']}   ADR 已分配 {stats['adr']}")
    if frontier:
        print(f"本轮 frontier（{len(frontier)}）：")
        for q in frontier:
            print(f"  {q['id']} [{q['dimension']}] {q['title']}")
    if blocked:
        print(f"被阻塞（{len(blocked)}）：" + "、".join(
            f"{q['id']}（等 {'、'.join(d['id'] + ('延期中' if d['status'] == 'deferred' else '') for d in deps)}）"
            for q, deps in blocked))
    if frontier:
        return EXIT_FLOW, (f"把上述 {len(frontier)} 题按 grilling 格式一轮全问（❓ {frontier[0]['id']} - **标题**: 题干 + ➡️ 推荐答案，"
                           "题间 --- 分隔），用户答复后逐条 dd.py q-answer")
    if blocked:
        return EXIT_FLOW, ("无新问题可问但有被阻塞问题：请用户处理被延期依赖（q-answer 答掉或维持延期），"
                           "或裁剪掉下游问题")
    if stats["open"] > 0:  # 理论上不会走到（open 必在 frontier/blocked 之一）
        return EXIT_FLOW, "仍有 open 问题：dd.py status 检查台账"
    return EXIT_OK, ("frontier 已清零：向用户复述完整理解求 shared-understanding 确认，"
                     "确认后 dd.py phase analysis-doc")


def cmd_phase(args, repo: Path) -> tuple[int, str]:
    ledger = load_ledger(repo)
    cur, tgt = ledger["phase"], args.name
    if tgt not in PHASES:
        raise DDError(f"未知阶段：{tgt!r}（合法：{'|'.join(PHASES)}）")
    if tgt == cur:
        print(f"== phase ==  已在 {cur}（no-op）")
        return EXIT_OK, PHASE_GUIDE[cur]
    if tgt not in PHASE_EDGES[cur]:
        raise DDError(f"非法阶段转移：{cur} → {tgt}",
                      next_="合法边：intake→analyzing→grilling→analysis-doc→design-doc；回流边 →grilling/→analyzing；done 只经 finish")
    if tgt == "design-doc" and ledger["docs"]["analysis"]["status"] != "confirmed":
        raise DDError("分析文档尚未 confirmed，不能进入 design-doc",
                      next_="先 dd.py check --doc analysis 到 PASS，用户确认后 dd.py confirm --doc analysis")
    ledger["phase"] = tgt
    add_history(ledger, "phase", f"{cur}→{tgt}")
    save_ledger(repo, ledger)
    print(f"== phase ==  {cur} → {tgt}")
    return EXIT_OK, PHASE_GUIDE[tgt]


def cmd_manifest(args, repo: Path) -> tuple[int, str]:
    ledger = load_ledger(repo)
    if not args.enable and not args.disable:
        raise DDError("须提供 --enable <ids> 和/或 --disable <ids>", next_="例如 --enable cache,release")
    changed = []
    for flag, ids_str, want in (("enable", args.enable, True), ("disable", args.disable, False)):
        if not ids_str:
            continue
        for sid in [s.strip() for s in ids_str.split(",") if s.strip()]:
            if sid not in ALL_SECTION_IDS:
                raise DDError(f"未知章节 id：{sid!r}（合法集合见 dd.py MANIFEST_DEFAULTS / SKILL.md 命令速查）")
            for s in ledger["manifest"]["sections"]:
                if s["id"] == sid:
                    if s["enabled"] != want:
                        s["enabled"] = want
                        changed.append(f"{sid}→{'on' if want else 'off'}")
                    break
    if not args.reason:
        raise DDError("--reason 必填（记录裁剪理由，供审计）")
    if changed:
        ledger["manifest"]["updated_at"] = now_iso()
        ledger["manifest"]["reason"] = args.reason
        add_history(ledger, "manifest", f"{','.join(changed)}  reason={args.reason}")
        save_ledger(repo, ledger)
        print(f"== manifest ==  {', '.join(changed)}  reason: {args.reason}")
    else:
        print("== manifest ==  无变化（目标状态已是现状）")
    on = [s["id"] for s in ledger["manifest"]["sections"] if s["enabled"]]
    off = [s["id"] for s in ledger["manifest"]["sections"] if not s["enabled"]]
    print(f"启用：{'、'.join(on) or '（无）'}")
    print(f"停用：{'、'.join(off) or '（无）'}")
    return EXIT_OK, "章节清单已更新；check 门禁将只校验启用章节"


def cmd_status(args, repo: Path) -> tuple[int, str]:
    ledger = load_ledger(repo)
    stats = question_stats(ledger)
    frontier, blocked = compute_frontier(ledger["questions"])
    print(f"== design-doc-writer 台账 ==  phase: {ledger['phase']}  feature: {ledger['project']['feature']}")
    for dk in ("analysis", "design"):
        d = ledger["docs"][dk]
        extra = f"  confirmed_at={d['confirmed_at']}" if d["status"] == "confirmed" else ""
        print(f"  [{dk}] {d['status']:<9} {d['path']}{extra}")
    print(f"问题 {stats['total']}：answered {stats['answered']} / open {stats['open']} / deferred {stats['deferred']}"
          f"   ADR 已分配 {stats['adr']}")
    by_dim: dict[str, int] = {}
    for q in ledger["questions"]:
        if q["status"] == "open":
            by_dim[q["dimension"]] = by_dim.get(q["dimension"], 0) + 1
    if by_dim:
        print("open 按维度：" + "、".join(f"{k}×{v}" for k, v in sorted(by_dim.items())))
    if frontier:
        print(f"frontier（{len(frontier)}）：" + "、".join(q["id"] for q in frontier))
    if blocked:
        print(f"被阻塞（{len(blocked)}）：" + "、".join(q["id"] for q, _ in blocked))
    kb = ledger["kb"]
    print(f"知识库：primary={kb.get('primary') or '（无，降级读码）'}"
          + (f"  openwiki_mcp={kb.get('openwiki_mcp')}" if kb.get("openwiki_mcp") else ""))
    for h in ledger["history"][-5:]:
        print(f"  · {h['at']} {h['event']} {h['detail']}")
    print(f"台账：{ledger_path(repo)}")
    return EXIT_OK, PHASE_GUIDE[ledger["phase"]]


def cmd_check(args, repo: Path) -> tuple[int, str]:
    ledger = load_ledger(repo)
    which = args.doc
    if which is None:
        which = "all" if ledger["phase"] in ("design-doc", "done") else "analysis"
        print(f"（未指定 --doc，按 phase={ledger['phase']} 推断为 {which}）")
    items, has_fail = run_checks(repo, ledger, which)
    print(f"== check（{which}） ==")
    if not items:
        print("（无检查项）")
    render_items(items)
    n_fail = sum(1 for i in items if i.level == "FAIL")
    n_warn = sum(1 for i in items if i.level == "WARN")
    n_pass = sum(1 for i in items if i.level == "PASS")
    print(f"合计：PASS {n_pass} / WARN {n_warn} / FAIL {n_fail}")
    if has_fail:
        return EXIT_FLOW, f"按上述 FAIL 项修复文档/台账后重跑 dd.py check --doc {which}"
    if which == "analysis":
        return EXIT_OK, ("门禁全过：请用户审阅需求分析文档并明确确认，"
                         "确认后 dd.py confirm --doc analysis --because \"<用户确认原话摘要>\"")
    return EXIT_OK, ("门禁全过：请用户审阅设计文档并明确确认，"
                     "确认后 dd.py confirm --doc design --because \"<用户确认原话摘要>\"，随后 dd.py finish")


def cmd_confirm(args, repo: Path) -> tuple[int, str]:
    ledger = load_ledger(repo)
    if not args.because or not args.because.strip():
        raise DDError("--because 必填（记录用户确认原话摘要，审计留痕）")
    doc = args.doc
    if doc not in ("analysis", "design"):
        raise DDError("--doc 只能是 analysis 或 design")
    items, has_fail = run_checks(repo, ledger, doc)
    if has_fail:
        print(f"== confirm（{doc}）==  门禁未过，文档保持 draft ==")
        render_items(items)
        return EXIT_FLOW, "先修复上述 FAIL 项（dd.py check --doc {d}），用户确认后再 confirm".format(d=doc)
    d = ledger["docs"][doc]
    re_confirm = d["status"] == "confirmed"
    d["status"] = "confirmed"
    d["confirmed_at"] = now_iso()
    d["because"] = args.because.strip()
    # 阶段自动补齐：确认 analysis 时若仍在 grilling → analysis-doc；确认 design 时若仍在 analysis-doc → design-doc
    auto = ""
    if doc == "analysis" and ledger["phase"] == "grilling":
        ledger["phase"] = "analysis-doc"
        auto = "grilling→analysis-doc（confirm 自动推进）"
    elif doc == "design" and ledger["phase"] == "analysis-doc":
        ledger["phase"] = "design-doc"
        auto = "analysis-doc→design-doc（confirm 自动推进）"
    if auto:
        add_history(ledger, "phase", auto)
    # 同步文档状态行（脚本改，杜绝手改漂移；门禁已保证状态行存在）
    doc_file = repo / d["path"]
    try:
        raw = doc_file.read_text(encoding="utf-8")
        raw2 = STATUS_LINE_RE.sub("状态: Confirmed", raw, count=1)
        if raw2 != raw:
            doc_file.write_text(raw2, encoding="utf-8")
    except OSError as e:
        LOG.warning(f"confirm 同步状态行失败（不阻断）：{e}")
    add_history(ledger, "confirm", f"{doc}{'重确认' if re_confirm else ''} because={args.because.strip()}")
    save_ledger(repo, ledger)
    print(f"== confirm ==  {doc} → confirmed（because: {args.because.strip()}；状态行已同步为 Confirmed）")
    if doc == "analysis":
        return EXIT_OK, "dd.py phase design-doc 进入详细设计编写"
    return EXIT_OK, "dd.py finish 收尾并输出交付摘要"


def cmd_finish(args, repo: Path) -> tuple[int, str]:
    ledger = load_ledger(repo)
    missing = [dk for dk in ("analysis", "design") if ledger["docs"][dk]["status"] != "confirmed"]
    if missing:
        print("== finish ==  未完成双确认：" + "、".join(missing))
        return EXIT_FLOW, ("先完成 " + "、".join(missing) + " 的 check+confirm（用户明确确认后）")
    if ledger["phase"] != "done":
        ledger["phase"] = "done"
        add_history(ledger, "phase", "→done（finish）")
        save_ledger(repo, ledger)
    stats = question_stats(ledger)
    print("== finish ==  交付摘要")
    for dk in ("analysis", "design"):
        d = ledger["docs"][dk]
        print(f"  [{dk}] Confirmed  {d['path']}")
    adrs = [q["adr"]["path"] for q in ledger["questions"] if (q.get("adr") or {}).get("path")]
    for a in adrs:
        mark = "存在" if (repo / a).is_file() else "缺失！"
        print(f"  [adr] {mark}  {a}")
    print(f"  问题 {stats['total']}：answered {stats['answered']} / deferred {stats['deferred']}")
    print(f"  台账：{ledger_path(repo)}（在 .agent/ 下，不随仓库提交）")
    return EXIT_OK, "向用户逐文件汇报以上产出路径与状态，流程结束"


# ==================== CLI ====================


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--repo", default=argparse.SUPPRESS, help="目标库根目录（默认 CWD）")
    common.add_argument("--log", default=argparse.SUPPRESS, help="日志文件（默认 <repo>/.agent/design-doc-writer/dd.log）")

    p = argparse.ArgumentParser(prog="dd.py", description="design-doc-writer 台账/门禁 CLI（退出码：0 完成/1 流转信号/2 异常）")
    p.add_argument("--repo", default=".", help="目标库根目录（默认 CWD；子命令前后均可传）")
    p.add_argument("--log", default=None, help="日志文件（默认 <repo>/.agent/design-doc-writer/dd.log）")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", parents=[common], help="建台账（已存在则恢复模式）")
    s.add_argument("--feature", required=True, help="特性 slug（用于文档目录命名）")
    s.add_argument("--shape", choices=list(SHAPE_PRESETS), default="api",
                   help="项目形态，预置章节清单（默认 api）")
    s.add_argument("--analysis-doc", default=None, help="分析文档路径（默认 <docs>/design/<feature>/requirements-analysis.md）")
    s.add_argument("--design-doc", default=None, help="设计文档路径（默认 <docs>/design/<feature>/design.md）")
    s.add_argument("--adrs-dir", default=None, help="ADR 目录（默认 <docs>/adr）")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("kb-scan", parents=[common], help="探测目标库知识库")
    s.add_argument("--openwiki", action="store_true", help="环境有 openwiki MCP 时自报")
    s.set_defaults(func=cmd_kb_scan)

    s = sub.add_parser("q-add", parents=[common], help="追加问题（LLM 判断，脚本记账）")
    s.add_argument("--dimension", default=None, help="维度枚举（见 SKILL.md）")
    s.add_argument("--title", default=None)
    s.add_argument("--body", default=None)
    s.add_argument("--recommendation", default=None, help="推荐答案（每题必附）")
    s.add_argument("--depends-on", default=None, help="依赖的问题 id，逗号分隔")
    s.add_argument("--facts", default=None, help="自查所得事实（非问用户）")
    s.add_argument("--batch", default=None, help="批量 JSON 文件（对象数组，按依赖顺序）")
    s.set_defaults(func=cmd_q_add)

    s = sub.add_parser("q-answer", parents=[common], help="记录用户决策或延期")
    s.add_argument("id", help="问题 id（Qxxx）")
    s.add_argument("--answer", default=None, help="用户决策文本")
    s.add_argument("--defer", action="store_true", help="延期该问题")
    s.add_argument("--reason", default=None, help="延期原因（--defer 必填）")
    s.add_argument("--adr", action="store_true", help="该决策过 ADR 三重门，分配 ADR 文件路径")
    s.add_argument("--adr-slug", default=None, help="ADR 文件名 slug（小写 ascii）")
    s.set_defaults(func=cmd_q_answer)

    s = sub.add_parser("frontier", parents=[common], help="计算本轮可问问题（exit 1=还有待办）")
    s.set_defaults(func=cmd_frontier)

    s = sub.add_parser("phase", parents=[common], help="阶段转移（合法边校验）")
    s.add_argument("name", help="目标阶段")
    s.set_defaults(func=cmd_phase)

    s = sub.add_parser("manifest", parents=[common], help="裁剪设计文档章节清单")
    s.add_argument("--enable", default=None, help="启用章节 id，逗号分隔")
    s.add_argument("--disable", default=None, help="停用章节 id，逗号分隔")
    s.add_argument("--reason", default=None, help="裁剪理由（必填）")
    s.set_defaults(func=cmd_manifest)

    s = sub.add_parser("status", parents=[common], help="台账状态（中断恢复入口）")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("check", parents=[common], help="文档门禁")
    s.add_argument("--doc", choices=("analysis", "design", "all"), default=None,
                   help="检查对象（默认按 phase 推断）")
    s.set_defaults(func=cmd_check)

    s = sub.add_parser("confirm", parents=[common], help="Draft→Confirmed（唯一通道，先跑门禁）")
    s.add_argument("--doc", required=True, choices=("analysis", "design"))
    s.add_argument("--because", required=True, help="用户确认原话摘要（审计留痕）")
    s.set_defaults(func=cmd_confirm)

    s = sub.add_parser("finish", parents=[common], help="终态：双确认校验+交付摘要")
    s.set_defaults(func=cmd_finish)
    return p


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # argparse 错误发生在日志重定向前，保证 usage 可见
    parser = build_parser()
    args = parser.parse_args(argv)

    repo = Path(getattr(args, "repo", ".")).resolve()
    log = Path(getattr(args, "log", None) or default_log_path(repo))
    setup_logging(log)
    LOG.info(f"[cmd] dd.py {' '.join(argv)}  repo={repo}")

    try:
        code, nxt = args.func(args, repo)
    except DDError as e:
        print(f"错误: {e.msg}")
        nxt = e.next_ or "按 SKILL.md 工作流修正后重试"
        code = EXIT_ERR
    except Exception as e:  # noqa: BLE001 - 顶层兜底
        LOG.exception("未预期异常")
        print(f"错误: 未预期异常：{e}（详见日志 {log}）")
        nxt = "查看日志定位；这是 bug 请回报"
        code = EXIT_ERR
    if nxt:
        print(f"next_step: {nxt}")
    return code


if __name__ == "__main__":
    sys.exit(main())
