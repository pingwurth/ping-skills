"""共享摘要(work/repo-notes.md) — 所有 writer 动笔前必读的唯一上下文。

融合 qwiki build_digest(模块表/页面目录/知识卡)与 repowiki make_digest
(技术栈/命令/相邻页规则/--extra)。

**活术语表**(修 repowiki 静态 digest): submit --key-topics 把术语写进 DB
glossary 表, digest 从 DB 再生 —— 后到的 writer 也能看到先到 writer 的
术语; 术语定义由 business_term 卡承载(引擎暂无人工补定义的 CLI 入口)。

D11: digest 覆写自身输出 —— 前期笔记(arch-notes 等)必须走 --extra。
D24: --extra 里的声明是 writer 上下文, writer 会信任它 —— 版本/存在性
断言必须先对源验证, 不要照抄 README。
"""

from __future__ import annotations

import os

from . import config
from .config import Ctx
from .planner import plan_cards, plan_modules, plan_pages
from .util import die


def build_digest(ctx: Ctx, plan: dict, inv: dict) -> str:
    lines = [f"# 共享摘要（{config.SKILL_NAME} repo-notes）", "",
             "所有写手在动笔前必读本文。本文由 `pwiki digest` 确定性生成，"
             "禁止手改（要补充内容用 --extra 或让术语进 glossary）。", ""]
    summary = str(plan.get("repo_summary") or "").strip()
    if summary:
        lines += ["## 仓库摘要", "", summary, ""]
    stats = inv.get("stats", {})
    langs = "、".join(f"{l['lang']}({l['files']})"
                      for l in inv.get("languages", [])[:8])
    lines += ["## 基本事实", "",
              f"- 仓库：{inv.get('repo', '?')}（commit "
              f"{(inv.get('git') or {}).get('short_head') or '?'}）",
              f"- 文件总数：{stats.get('files', '?')}（约 "
              f"{stats.get('lines', '?')} 行）",
              f"- 语言分布：{langs or '未知'}",
              f"- 入口文件：{'、'.join(e['path'] for e in inv.get('entry_points', [])[:8]) or '未检出'}",
              f"- CI：{'、'.join(inv.get('ci', [])) or '未检出'}", ""]
    commands = inv.get("commands") or {}
    for bucket, label in (("build", "构建"), ("test", "测试"),
                          ("lint", "静态检查"), ("dev", "运行")):
        entries = commands.get(bucket) or []
        if entries:
            shown = "；".join(str(e) for e in entries[:3])
            lines.append(f"- {label}命令：{shown}")
    if len(lines) and lines[-1] != "":
        lines.append("")
    mods = plan_modules(plan)
    if mods:
        lines += ["## 模块表", "",
                  "| id | 标题 | 目录 | 类型 | 依赖 |", "|---|---|---|---|---|"]
        for m in mods:
            # depends_on 的合法形态是 str 或 {path: ...}(validate_plan 两态
            # 都收)—— 曾假定 str, dict 条目让 join 直接 TypeError
            dep_list = []
            for d in m.get("depends_on") or []:
                v = d.get("path") if isinstance(d, dict) else d
                if v:
                    dep_list.append(str(v))
            deps = ",".join(dep_list) or "-"
            lines.append(f"| {m.get('id')} | {m.get('title')} | "
                         f"{m.get('dir') or '.'} | {m.get('module_type', 'real')} "
                         f"| {deps} |")
        lines.append("")
    pages = plan_pages(plan)
    if pages:
        lines += ["## 页面目录（标题 · id · 父）", ""]
        for p in pages:
            kind = p.get("kind", "page")
            tag = ""
            if kind == "module_sheet":
                tag = f" [速查表:{p.get('sheet', '')}]"
            elif p.get("pack_key"):
                tag = f" [主题:{p['pack_key']}]"
            lines.append(f"- {p.get('title')}{tag} · {p.get('id')} · "
                         f"父: {p.get('parent') or '-'}")
        lines += ["", "相邻页面互相链接而不是重复内容。写页面前先看本表，"
                  "避免与兄弟页重叠。", ""]
    cards = plan_cards(plan)
    if cards:
        lines += ["## 知识卡", ""]
        for c in cards:
            lines.append(f"- [{c.get('kind')}] {c.get('title')} · {c.get('id')}")
        lines.append("")
    # 活术语表(从 DB 再生)
    if ctx.db is not None:
        pid = ctx.partition()["id"]
        terms = ctx.db.glossary_all(pid)
        if terms:
            lines += ["## 术语表（活 —— writer 提交的 key_topics 自动累积）", ""]
            for t in terms:
                if t.get("definition"):
                    lines.append(f"- **{t['term']}**：{t['definition']}")
                else:
                    lines.append(f"- {t['term']}（待 business_term 卡补定义）")
            lines.append("")
    lines += ["## 证据规则", "",
              "- 本摘要只是定向, 不是证据；引用必须来自你实际读过的文件行。",
              "- 负面断言（「未发现 X」）需要 ls AND grep 双重验证（D14）。",
              "- 不要改页面的 slug/id/标题；标题勘误走 submit --report（D22）。",
              "- 与知识卡冲突时: 术语以卡为准, 事实以行级引用的源码为准（D7）。", ""]
    return "\n".join(lines).rstrip() + "\n"


def cmd_digest(args) -> int:
    from .planner import load_plan
    from .scan import load_inventory

    ctx = Ctx(args).load()
    plan = load_plan(ctx)
    inv = load_inventory(ctx)
    if not inv:
        from .modules import _autoscan
        inv = _autoscan(ctx)
    body = build_digest(ctx, plan, inv)
    # D11: --extra 在覆写后追加(前期笔记不丢)
    for extra in getattr(args, "extra", []) or []:
        if not os.path.isfile(extra):
            # D11 的前期笔记入口: 拼错路径曾静默跳过 —— 摘要里无声缺了
            # 架构笔记, 所有后续 writer 拿到缺上下文的定向还无人知晓
            die(f"--extra 文件不存在: {extra}", config.EXIT_PRECOND)
        try:
            with open(extra, encoding="utf-8") as fh:
                extra_text = fh.read()
        except UnicodeDecodeError as e:
            # c048 同族: 手编文件坏字节给可诊断的门禁错, 不是裸 traceback
            die(f"--extra 文件不是合法 UTF-8({extra}: {e})",
                config.EXIT_GATE)
        body = body.rstrip() + "\n\n---\n\n" + extra_text
    out = ctx.digest_path
    os.makedirs(os.path.dirname(out), exist_ok=True)
    # 原子写: repo-notes.md 是每个 next 都指向 writer 的共享上下文 ——
    # 运行中重跑 digest 被截断, 后续页面全程对着半份摘要规划
    from .util import atomic_write
    atomic_write(out, body)
    print(f"digest: {out} ({len(body)} chars)")
    return config.EXIT_OK
