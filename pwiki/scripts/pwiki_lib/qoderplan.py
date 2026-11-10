"""Qoder IDE wiki_plan.yaml 契约(wiki-plan-gen 的全部功能)。

emit: 从 pwiki plan v3 生成 Qoder v1 格式的 wiki_plan.yaml(默认落
`.pwiki/qoder/repowiki/`, 显式 --emit 可写到
供 IDE 的 /knowledge 消费)。lint: 按反编译契约(MECHANICS.md)预检 ——
7 个截断上限(字节 vs 字符精确口径)与 loader 的静默修复规则。

契约要点(全部有反编译证据, 见 wiki-plan-gen/MECHANICS.md):
  - title/goal/note.text 按【字节】截断(CJK 约按 1/3 折算);
    hints/template 按【字符】截断。
  - 空 title/goal 的文档被丢弃; parent 按【修剪后的 title】只能回溯解析
    (指向更早的文档); parent 拼错 → 静默降级顶层; parent==self → 降级;
    重复 title 在 parent 解析【之后】裁决(后者被静默丢弃)。
  - planner 自身预算: depth 2 / 500 篇 / 120 顶层 / 80 每父 / 6000 词 /
    15000 dir tokens —— 约束生成而非文件(手写文件不受检)。
"""

from __future__ import annotations

import os
import re

from . import config
from .config import Ctx
from .util import as_int, die, info

# 上限(字节 vs 字符口径 —— 两条路径都来自反编译比较指令)
CAP_NOTES_COUNT = 50          # *.notes 条数
CAP_NOTE_TEXT_BYTES = 10_000  # 单条 note.text(字节)
CAP_DOCS_COUNT = 500          # documents 总数
CAP_TITLE_BYTES = 100         # title(字节; ≈33 个 CJK 字符)
CAP_GOAL_BYTES = 10_000       # goal(字节)
CAP_HINTS_CHARS = 5_000       # hints(字符)
CAP_TEMPLATE_CHARS = 5_000    # 单文档 template(字符; 全局 template 无上限)

PRESET_TEMPLATES = ("architecture", "product_requirement")

_PLANNER_BUDGET = {"max_depth": 2, "max_total": 500, "max_top": 120,
                   "max_sub_per_parent": 80, "max_words": 6000,
                   "dir_tree_tokens": 15000}


def cmd_qoder_plan(args) -> int:
    ctx = Ctx(args).load()
    lint_path = getattr(args, "lint", None)
    if lint_path:
        return _lint(ctx, lint_path)
    # c060: 默认 emit 落 ctx.root(.pwiki 工作区)内 —— 曾默认写
    # <repo>/.qoder/repowiki/, 在被分析仓库工作树里植入未忽略的文件
    # (违反"pwiki 只写 .pwiki"红线); 需要导入 Qoder IDE 时显式 --emit
    return _emit(ctx, getattr(args, "emit", None) or
                 os.path.join(ctx.root, "qoder", "repowiki", "wiki_plan.yaml"))


# --------------------------------------------------------------------------- #
# emit: plan v3 → Qoder v1 格式
# --------------------------------------------------------------------------- #

def _emit(ctx: Ctx, out_path: str) -> int:
    from .planner import load_plan, plan_pages
    plan = load_plan(ctx)

    # documents: content 树(kind=page)。不要求 type —— 曾按 `p.get("type")`
    # 过滤, 手改掉 type 的页连同整棵子树静默消失(exit 0, 只报"(1 documents)")
    pages = [p for p in plan_pages(plan)
             if p.get("kind", "page") == "page"]
    by_id = {str(p.get("id")): p for p in pages}
    order = []          # Qoder parent 只能回溯: 父必须先于子出现
    seen = set()

    def emit_level(parent_id):
        kids = [p for p in pages
                if str(p.get("parent") or "") == parent_id
                and str(p.get("id")) not in seen]
        kids.sort(key=lambda p: (as_int(p.get("ordinal"), 0), str(p.get("id"))))
        for p in kids:
            pid = str(p.get("id"))
            seen.add(pid)
            order.append(p)
            emit_level(pid)

    roots = [p for p in pages if not p.get("parent")]
    roots.sort(key=lambda p: (as_int(p.get("ordinal"), 0), str(p.get("id"))))
    for p in roots:
        pid = str(p.get("id"))
        if pid in seen:
            continue
        seen.add(pid)
        order.append(p)
        emit_level(pid)
    leftover = [p for p in pages if str(p.get("id")) not in seen]
    if leftover:
        # 悬空 parent(parent id 不在计划/拼写错)整棵子树够不着 DFS —— 曾
        # 静默少导出还 exit 0, IDE 导入缺树无任何警告(validate 本可拦住)
        ids = ", ".join(str(p.get("id")) for p in leftover[:5])
        die(f"plan 有 {len(leftover)} 页 parent 悬空、子树未导出(如 {ids}) — "
            "先 `pwiki validate` 修 parent", config.EXIT_GATE)

    documents = []
    for p in order:
        title = str(p.get("title") or "").strip()
        goal = str(p.get("objective") or p.get("prompt") or "").strip() \
            or f"撰写{title}。"       # 空 goal 会被 Qoder 静默丢弃
        parent_title = ""
        if p.get("parent"):
            parent = by_id.get(str(p.get("parent")))
            if parent is not None:
                parent_title = str(parent.get("title") or "").strip()
        # P36: goal 也过字节帽(_trunc_bytes 不切多字节字符) —— 曾只截
        # title/note/hints, 超限 goal 仅告警仍原样发出, Qoder 的加载器
        # 会在 CJK 字符中间硬字节截断(乱码)
        doc = {"title": _trunc_bytes(title, CAP_TITLE_BYTES),
               "goal": _trunc_bytes(goal, CAP_GOAL_BYTES)}
        if parent_title and str(p.get("parent")) != str(p.get("id")):
            # parent 必须写**截断后**的父标题 —— loader 拿修剪后的 title
            # 回溯匹配, 全量父标题对超限父档永不命中 → 子档静默降级顶层。
            # 自引用守卫按 id(parent==自己)而不是标题: 父子同名是合法树
            # (validate 的重名检查按 parent 分组)—— 曾按标题相等丢 parent
            # 键, 子档被 IDE 按"重复 title 丢后者"契约整篇静默蒸发
            doc["parent"] = _trunc_bytes(parent_title, CAP_TITLE_BYTES)
        hints = str(p.get("prompt") or "").strip()
        if hints:
            doc["hints"] = hints[:CAP_HINTS_CHARS]
        documents.append(doc)

    yaml_doc = {
        "version": 1,
        "scope": {"include": [], "exclude": []},
        "repowiki": {
            "template": "",
            "notes": _notes_from_plan(plan),
            "documents": documents[:CAP_DOCS_COUNT],
        },
        "knowledgecard": {"notes": []},
    }
    from .util import atomic_write   # P56: 中断不留半份导入计划
    from .yamlite import yaml_dump
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    atomic_write(out_path, _HEADER + yaml_dump(yaml_doc))

    problems = _check_caps(documents, yaml_doc["repowiki"]["notes"])
    for w in problems:
        info(f"warn: {w}")
    print(f"emit: {out_path} ({len(documents)} documents; "
          f"parent 回溯序已保证)")
    if problems:
        print(f"  上限预检: {len(problems)} 条警告(见上)")
    return config.EXIT_OK


def _notes_from_plan(plan: dict) -> list:
    notes = []
    for n in (plan.get("author_notes") or [])[:CAP_NOTES_COUNT]:
        if not isinstance(n, dict):
            continue   # r7 #26: 手编标量条目不崩 --emit(其余条目照常)
        text = str(n.get("text") or "").strip()
        if not text:
            continue    # 空文本 note 被 normalizeNotes 静默丢弃
        notes.append({"text": _trunc_bytes(text, CAP_NOTE_TEXT_BYTES),
                      "author": str(n.get("author") or "author")})
    return notes


def _trunc_bytes(text: str, cap: int) -> str:
    """按 UTF-8 字节安全截断, 不切半个多字节字符(c006)。

    原字符启发式(CAP//3 若含 CJK)对 CJK+4 字节字符混排、以及任何非 CJK
    多字节文字都会超出字节上限, 由 Qoder 侧再硬按字节截 → 乱码。"""
    raw = text.encode("utf-8")
    if len(raw) <= cap:
        return text
    return raw[:cap].decode("utf-8", errors="ignore")


_HEADER = """\
# Qoder IDE wiki_plan.yaml — 由 pwiki qoder-plan 生成
#
# 契约(反编译证据): title/goal/note 按字节截断(CJK≈1/3), hints/template 按字符;
# 空 title/goal 丢弃; parent 只能指向更早出现的文档; 重复 title 丢后者。
# planner 预算: depth 2 / 500 篇 / 120 顶层 / 80 每父。
"""


# --------------------------------------------------------------------------- #
# lint: 对现有文件做上限与静默修复预检
# --------------------------------------------------------------------------- #

def _check_caps(documents: list, notes: list) -> list:
    warns = []
    titles_seen = {}
    for i, doc in enumerate(documents):
        if not isinstance(doc, dict):
            # r7 #26: lint 存在的意义就是预检手写文件 —— 标量条目曾让
            # 专职预检的自己 AttributeError 裸崩 exit 1
            warns.append(f"documents[{i}]: 条目必须是映射(got "
                         f"{type(doc).__name__}): {doc!r}")
            continue
        title = str(doc.get("title") or "")
        goal = str(doc.get("goal") or "")
        tb = len(title.encode("utf-8"))
        gb = len(goal.encode("utf-8"))
        if not title.strip():
            warns.append(f"documents[{i}]: 空 title — Qoder 会静默丢弃该文档")
        elif tb > CAP_TITLE_BYTES:
            warns.append(f"documents[{i}]: title {tb} 字节 > {CAP_TITLE_BYTES}"
                         f"(≈{tb // 3} CJK 字符超限) — 会被字节截断, 可能切成乱码")
        if not goal.strip():
            warns.append(f"documents[{i}] ({title!r}): 空 goal — 会被静默丢弃")
        elif gb > CAP_GOAL_BYTES:
            warns.append(f"documents[{i}] ({title!r}): goal {gb} 字节 > "
                         f"{CAP_GOAL_BYTES} — 会被截断")
        hints = str(doc.get("hints") or "")
        if len(hints) > CAP_HINTS_CHARS:
            warns.append(f"documents[{i}] ({title!r}): hints {len(hints)} 字符 > "
                         f"{CAP_HINTS_CHARS} — 会被截断(字符口径, 不会切乱码)")
        tmpl = str(doc.get("template") or "")
        if len(tmpl) > CAP_TEMPLATE_CHARS:
            warns.append(f"documents[{i}] ({title!r}): template {len(tmpl)} 字符 — "
                         f"会被截断")
        parent = str(doc.get("parent") or "").strip()
        if parent == title.strip():
            warns.append(f"documents[{i}] ({title!r}): parent 指向自身 — "
                         "静默降级为顶层")
        elif parent:
            if parent not in titles_seen:
                # Qoder 只能回溯解析: 目标若出现在后面, parent 落空 → 降顶层
                later = any(str(d.get("title") or "").strip() == parent
                            for d in documents[i + 1:])
                if later:
                    warns.append(f"documents[{i}] ({title!r}): parent "
                                 f"{parent!r} 定义在其后 — 无法回溯, 静默降级顶层")
                else:
                    warns.append(f"documents[{i}] ({title!r}): parent {parent!r} "
                                 "不匹配任何 title — 静默降级顶层")
        titles_seen.setdefault(title.strip(), i)
    dup = {}
    for i, doc in enumerate(documents):
        if not isinstance(doc, dict):   # r7 #26: 标量条目(上面已 warn)
            continue
        t = str(doc.get("title") or "").strip()
        if t in titles_seen and titles_seen[t] != i:
            dup.setdefault(t, [titles_seen[t]]).append(i)
    for t, idxs in dup.items():
        warns.append(f"重复 title {t!r}(位置 {idxs}) — parent 解析后静默丢后者, "
                     "已指向它的链接会解析到第一个")
    if len(documents) > CAP_DOCS_COUNT:
        warns.append(f"documents {len(documents)} > {CAP_DOCS_COUNT} — 截断")
    if len(notes) > CAP_NOTES_COUNT:
        warns.append(f"notes {len(notes)} 条 > {CAP_NOTES_COUNT} — 截断")
    for i, n in enumerate(notes):
        nb = len(str(n.get("text") or "").encode("utf-8"))
        if not str(n.get("text") or "").strip():
            warns.append(f"notes[{i}]: 空文本 — 静默丢弃")
        elif nb > CAP_NOTE_TEXT_BYTES:
            warns.append(f"notes[{i}]: text {nb} 字节 > {CAP_NOTE_TEXT_BYTES} — "
                         "字节截断")
    return warns


def _lint(ctx: Ctx, path: str) -> int:
    if not os.path.isfile(path):
        print(f"error: 文件不存在: {path}")
        # c086 同族: 目标文件缺失是前置条件不满足(3), 不是环境错误(1)
        # —— 契约表(config)明示编排方按 3 识别"先补输入"
        return config.EXIT_PRECOND
    from .yamlite import yaml_load
    with open(path, encoding="utf-8") as fh:
        try:
            doc = yaml_load(fh.read())
        except ValueError as e:
            print(f"error: YAML 解析失败: {e}")
            return config.EXIT_GATE
    if not isinstance(doc, dict):
        # yamlite 放行列表顶层的文档 —— 这里 .get 会裸 AttributeError
        print(f"error: YAML 顶层必须是映射, 得到 {type(doc).__name__}")
        return config.EXIT_GATE
    # P30: 嵌套形状守卫(与顶层守卫同类) —— repowiki:/knowledgecard: 非映射
    # 或 documents:/notes: 非列表曾裸 AttributeError traceback(退出码 1,
    # 非契约 2); lint 的本职就是验形状, 坏形状按门禁错报
    for key in ("repowiki", "knowledgecard"):
        v = doc.get(key)
        if v is not None and not isinstance(v, dict):
            print(f"error: {key} 必须是映射, 得到 {type(v).__name__}")
            return config.EXIT_GATE
    rw = doc.get("repowiki") or {}
    kc = doc.get("knowledgecard") or {}
    for name, v in (("repowiki.documents", rw.get("documents")),
                    ("repowiki.notes", rw.get("notes")),
                    ("knowledgecard.notes", kc.get("notes"))):
        if v is not None and not isinstance(v, list):
            print(f"error: {name} 必须是列表, 得到 {type(v).__name__}")
            return config.EXIT_GATE
    version = doc.get("version")
    if version not in (0, 1):
        info(f"warn: version {version!r} 非 0/1 — 按警告处理后按 1 继续")
    template = str(rw.get("template") or "")
    if template and template not in PRESET_TEMPLATES:
        info(f"warn: repowiki.template {template!r} 不是预设名"
             f"({PRESET_TEMPLATES}) — 被忽略, 全局模板置空")
    docs = rw.get("documents") or []
    notes = rw.get("notes") or []
    knotes = kc.get("notes") or []
    warns = _check_caps(docs, notes) + _check_caps([], knotes)
    for w in warns:
        print(f"warn: {w}")
    print(f"lint: {len(docs)} documents, {len(notes)}+{len(knotes)} notes; "
          f"{len(warns)} 条会触发 Qoder 静默修复/截断的问题")
    return config.EXIT_OK if not warns else config.EXIT_GATE
