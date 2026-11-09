"""检索: 已发布 wiki 的 BM25(字段加权 + 中文单字 + 短语加成)。

移植 qwiki _tokenize/cmd_search(4120-4214)原样 —— 刻意的离线替代:
无向量索引、无持久化, 每次查询对全语料现算(qwiki 的既定取舍)。
"""

from __future__ import annotations

import json
import math
import os
import re

from . import config
from .util import CJK_RE as _CJK_RE   # 单一来源(r4 #58)
from .config import Ctx

_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with",
    "is", "are", "be", "this", "that", "it", "as", "at", "by", "from",
    "的", "了", "在", "是", "和", "与", "对", "中", "或", "及", "把", "被",
    "如何", "什么", "怎么", "哪些",
}


def tokenize(text: str) -> list:
    out = []
    for w in _WORD_RE.findall(text):
        low = w.lower()
        if low not in _STOP:
            out.append(low)
    for ch in _CJK_RE.findall(text):
        if ch not in _STOP:
            out.append(ch)
    return out


def _fields(content: str):
    """(title_tokens, heading_tokens, body_tokens) — title×3, headings×2。"""
    lines = content.splitlines()
    title, headings, body = [], [], []
    for line in lines:
        if line.startswith("#"):
            toks = tokenize(line.lstrip("# "))
            if line.startswith("# ") and not title:
                title = toks
            else:
                headings.extend(toks)
        else:
            body.extend(tokenize(line))
    return title, headings, body


def cmd_search(args) -> int:
    ctx = Ctx(args).load()
    pages = ctx.db.published_pages(ctx.partition()["id"])
    if not pages:
        print("没有已发布条目 — 先跑完一次 begin→…→finish")
        return config.EXIT_PRECOND
    query_raw = args.query
    q_tokens = tokenize(query_raw)
    if not q_tokens:
        print("query 没有可检索 token")
        return config.EXIT_OK

    docs = []
    df = {}
    for p in pages:
        title, headings, body = _fields(p["content"])
        tokens = title * 3 + headings * 2 + body
        if not tokens:
            continue
        tf = {}
        for t in tokens:
            tf[t] = tf.get(t, 0) + 1
        doc = {"page": p, "tf": tf, "length": len(tokens),
               "title_text": p["title"], "content": p["content"]}
        docs.append(doc)
        for t in tf:
            df[t] = df.get(t, 0) + 1

    n = len(docs)
    avg_len = (sum(d["length"] for d in docs) / n) if n else 1.0
    k1, b = 1.5, 0.75
    results = []
    q_low = query_raw.lower()
    for d in docs:
        score = 0.0
        for t in q_tokens:
            f = d["tf"].get(t, 0)
            if not f:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            score += idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * d["length"] / avg_len))
        if score <= 0:
            continue
        if q_low in d["title_text"].lower():
            score += 10.0
        if q_low in d["content"].lower():
            score += 5.0
        results.append((score, d))
    results.sort(key=lambda x: -x[0])
    # P47: 尊重调用者的空集请求 —— max(1, ·) 曾把 --top 0/负值静默翻成
    # "至少一条", 编排方对被要求抑制的页面执行了动作
    n_top = args.top if isinstance(args.top, int) else 10
    top = results[:n_top] if n_top > 0 else []

    if args.json:
        payload = []
        for score, d in top:
            excerpt = ""
            for line in d["content"].splitlines():
                if any(t in line.lower() for t in q_tokens):
                    excerpt = line.strip()[:200]
                    break
            from .gates import extract_citations
            payload.append({
                "score": round(score, 3), "page_id": d["page"]["page_id"],
                "kind": d["page"]["kind"], "title": d["page"]["title"],
                "path": d["page"]["path"], "excerpt": excerpt,
                "cited_files": sorted({c["path"] for c
                                       in extract_citations(d["content"])}),
            })
        print(json.dumps({"query": query_raw, "results": payload},
                         ensure_ascii=False, indent=2))
        return config.EXIT_OK

    if not top:
        print(f"no match for {query_raw!r}")
        return config.EXIT_OK
    print(f"top {len(top)} for {query_raw!r}:")
    for i, (score, d) in enumerate(top, 1):
        print(f"{i}. [{score:.1f}] {d['page']['title']} "
              f"({d['page']['kind']}, {d['page']['page_id']})")
        print(f"   {os.path.join(ctx.root, d['page']['path'])}")
    return config.EXIT_OK
