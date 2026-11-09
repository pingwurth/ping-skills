"""分层目录规划(Qoder deepwiki-catalogue-plan → generate 递归的镜像)。

一次只出一层; 服务对标 need_further_planning 的子篇再问一轮。
移植 qwiki cmd_plan_layers/layer_payload/apply_layer_result/materialize_layers
(2390-2693)。状态在 work/plan-layers.json(可再生缓存之外的会话态)。
"""

from __future__ import annotations

import json
import os
import re
import sys

from . import config
from .config import Ctx, now_iso
from .planner import (plan_limits, plan_modules, plan_pages, render_module_tree,
                      save_plan)
from .styles import preset
from .util import as_int, die, info

RESULT_RE = re.compile(r"(?s)<result>\s*(\{.*?\})\s*</result>")
FENCE_JSON_RE = re.compile(r"(?s)```(?:json)?\s*(\{.*\})\s*```")
SLUG_RE = re.compile(r"^[a-z0-9_-]{1,40}$")


def _layers_path(ctx: Ctx) -> str:
    return os.path.join(ctx.root, "work", "plan-layers.json")


def _load_layers(ctx: Ctx):
    p = _layers_path(ctx)
    if not os.path.isfile(p):
        return None
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except ValueError:
        # 半份文件(截断/ENOSPC)是有价值的手编会话状态 —— 曾吞成"无状态",
        # 把用户引进 init 一键覆盖可挽救的大纲
        die(f"{p} 已损坏(截断的 JSON?) — 手工挽救或删除后再 "
            "`pwiki plan --layers init`", config.EXIT_PRECOND)
    except OSError as e:
        die(f"{p} 读取失败({e})", config.EXIT_ENV)
        return None


def _save_layers(ctx: Ctx, state) -> None:
    """work/plan-layers.json 是**会话状态**(模块 docstring: 非可再生缓存)
    —— 落盘走原子写(截断即损毁整个分层规划对话)。"""
    from .util import atomic_write
    p = _layers_path(ctx)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    atomic_write(p, json.dumps(state, ensure_ascii=False, indent=2) + "\n")


def parse_result_block(text: str) -> dict:
    """单个 <result>{…}</result> 块(Qoder 应答契约; 代码栅栏 JSON 兜底)。

    栅栏兜底逐个栅栏尝试解析 —— 曾用贪婪 `\{.*\}` 把多个栅栏(如正文里
    先引用的 package.json 示例)合并成一段不可解析的跨度, json.loads 在
    调用方裸 traceback。"""
    m = RESULT_RE.search(text)
    if m:
        return json.loads(m.group(1))
    for fm in re.finditer(r"(?s)```(?:json)?\s*(.*?)\s*```", text):
        try:
            v = json.loads(fm.group(1))
        except ValueError:
            continue
        if isinstance(v, dict):
            return v
    raise ValueError("no <result>{...}</result> block found")


def _layer_rules(plan: dict, profile: str) -> str:
    limits = plan_limits(plan, profile)
    max_words = config.LIMIT_DEFAULTS.get("max_article_words", 3000)
    return f"""# 编目规则(与 Qoder 的 catalogue planner 同构; 一次只出一层)

## 顶层四步
1. `/`(root) → 一篇 `overview`; Makefile/docker-compose/package.json scripts/
“Getting Started”类文档 → 一篇 `getting_started`; contributing/developer
相关模块 → 一篇 `developer_guide`。
2. 每个显著的 depth-1 模块 → 一个候选顶层; 共享功能域的紧耦合模块(如
`auth/*`)**合并**成一篇 `domain`; Module Tree 的 `[N sub-modules]` 是主要
聚合信号。
3. 候选数超过 max_top_articles 时按合并文件数降序保留, **溢出候选并入兄弟
顶层**(不要截断、不要丢弃模块)。
4. `need_further_planning: true` ⟺ 子树包含 **≥2 个可独立成篇的子域**
(不同职责/关注点, `[N sub-modules]` 只是参考 —— 布局目录不成模块)**且**
预估内容超过约 {max_words} 词。宁可多拆: Qoder 在同等仓库产出的篇数约为
模块数的 5 倍。

## 子层
- 只产出**直接子篇**, 不要嵌套; 服务会对你标 true 的子篇再问一轮。
- 子篇 `modules` 必须是父篇 `modules` 的子集; 子篇合起来干净划分父域,
  避免与同层兄弟重叠。
- 再入 ⟺ 该子篇覆盖 ≥2 个可独立成篇的子域 **且** 当前深度+1 ≤ max_depth。

## 硬规
- Module Tree 中**每个模块路径**必须至少出现在一篇的 `modules` 里(跨递归
  层并集)—— 这是硬门禁; **标注 [skeleton] 的占位模块除外**(不要为它规划
  文章, 可在相关页 Out of scope 提及)。
- 总篇数 ≤ {limits['max_total_articles']}; 顶层 ≤ {limits['max_top_articles']};
  每父 ≤ {limits['max_sub_per_parent']}; 深度 ≤ {limits['max_depth']}。
- `objective` 只写 1–2 句范围 + `Out of scope: …`; **禁止规定内容结构**。

## 文章类型(5 型)
| type | 何时用 | 可有子篇 |
|---|---|---|
| `overview` | 项目级综述 | 可选(子通常 domain) |
| `getting_started` | 安装/首次运行/常见工作流 | 否 |
| `domain` | 聚合 2+ 紧耦合模块, 自身不做深挖 | 通常有(子为 deep_dive) |
| `deep_dive` | 1–3 个模块的深入分析 | 否(恒叶) |
| `developer_guide` | 构建/测试/贡献/编码约定 | 否 |

## 反模式(NEVER)
一个模块一篇; 按技术分层命名(“Controllers”“Repositories”“Util”); 跳过
模块; 空 `modules`; 文章套文章; 子篇越父域; 超总篇数; objective 规定结构;
非法 type; 给叶子型标 `need_further_planning`。

## 输出
只输出一个 `<result>` 块, 无其它散文:
<result>{{"articles":[{{"slug":"overview","title":"项目概览","type":"overview","objective":"… Out of scope: …","modules":["/"],"need_further_planning":false}}]}}</result>
`slug` 必须匹配 `[a-z0-9_-]{{1,40}}` 且全局唯一; `title` 1–6 个词的读者
意图标题; `modules` 原样复制 Module Tree 里的路径(root 为 `/`), 不要翻译。"""


def layer_payload(ctx: Ctx, plan: dict, state: dict, node: dict) -> str:
    profile = state.get("profile", "standard")
    limits = plan_limits(plan, profile)
    modules = plan_modules(plan)
    if node["parent"] is None:
        tree = render_module_tree(plan)
        header = (f"# Project: {state.get('project', '')}\n\n"
                  f"# Module Tree ({len(modules)} modules)")
    else:
        tree = render_module_tree(plan, node["modules"])
        header = (f"# Project: {state.get('project', '')}\n\n"
                  f"# Module Tree (filtered to parent's domain — parent "
                  f"`{node['title']}`)")
    sibs = [a for a in state["articles"]
            if a.get("parent") == (node.get("parent") or "")
            and a.get("slug") != node.get("slug")]
    planned = [a for a in state["articles"]
               if a.get("parent") == node.get("slug")]
    sib_txt = "\n".join(f"- {a['title']} (`{a['slug']}`) — {a.get('objective', '')}"
                        for a in sibs[:12]) or "- (无)"
    child_txt = "\n".join(f"- {a['title']} (`{a['slug']}`) — {a.get('objective', '')}"
                          for a in planned[:12]) or "- (无)"
    return f"""{header}

```
{tree}
```

# Configuration
- max_depth: {limits['max_depth']}
- max_total_articles: {limits['max_total_articles']}
- max_top_articles: {limits['max_top_articles']}
- max_sub_per_parent: {limits['max_sub_per_parent']}
- output_language: {ctx.locale}
- current_depth: {node['depth']} ({'top-level planning' if node['parent'] is None else 'planning sub-articles'})

# 已规划的同层兄弟
{sib_txt}

# 该父篇已规划的子篇
{child_txt}

{_layer_rules(plan, profile)}"""


def apply_layer_result(ctx, plan: dict, state: dict, text: str,
                       parent_slug: str = "") -> tuple:
    """把一个 <result> 大纲规范化进 state; 返回 (added, problems)。"""
    data = parse_result_block(text)
    arts = data.get("articles")
    problems, added = [], []
    if not isinstance(arts, list) or not arts:
        return [], ["result has no non-empty 'articles' list"]
    limits = plan_limits(plan, state.get("profile", "standard"))
    depth = 0
    parent_node = None
    # 存量条目形状守卫(P33 家族): 文档明示可"手工挽救"的
    # work/plan-layers.json 里一条标量曾让 a["slug"] TypeError 裸崩
    # (exit 1, 打破本命令的退出码契约)—— 坏条目跳过并计入 problems
    bad_articles = [a for a in state.get("articles", [])
                    if not isinstance(a, dict)]
    problems += [f"存量条目不是映射(跳过): {a!r}" for a in bad_articles]
    articles = [a for a in state.get("articles", [])
                if isinstance(a, dict)]
    if parent_slug:
        parent_node = next((a for a in articles
                            if a.get("slug") == parent_slug), None)
        if parent_node is None:
            return [], [f"unknown parent slug: {parent_slug}"]
        depth = as_int(parent_node.get("depth"), 0) + 1
        if depth > limits["max_depth"]:
            return [], [f"parent {parent_slug} already at max_depth"]
    existing = {a.get("slug") for a in articles}
    # Qoder: 子篇 modules ⊆ 父篇 modules(或 Module Tree 里的后代);
    # 父模块自身与回溯到 "/" 的祖先链保留在视图里。
    parent_modules = set(x.rstrip("/") or "/" for x in
                         ((parent_node or {}).get("modules") or ["/"]))
    if parent_node is not None:
        allowed = set(parent_modules)
        if "/" in parent_modules:
            allowed |= {str(m.get("path") or m.get("id") or "").rstrip("/")
                        for m in plan_modules(plan)}
        for m in plan_modules(plan):
            p = str(m.get("path") or m.get("id") or "").rstrip("/")
            for pm in parent_modules:
                if pm and pm != "/" and p.startswith(pm + "/"):
                    allowed.add(p)
    else:
        allowed = {str(m.get("path") or m.get("id") or "").rstrip("/")
                   for m in plan_modules(plan)} | {"/"}
    for i, a in enumerate(arts):
        # P33: 模型输出字符串列表/截断 JSON 时不再裸 AttributeError
        # ('str' 对象无属性 'get') 打穿退出码契约 —— 按门禁错记问题项
        if not isinstance(a, dict):
            problems.append(f"article {i}: 条目必须是映射"
                            f"(得到 {type(a).__name__})")
            continue
        slug = str(a.get("slug") or "").strip()
        title = str(a.get("title") or "").strip()
        atype = str(a.get("type") or "").strip()
        modules = [str(x).rstrip("/") or "/" for x in (a.get("modules") or [])]
        objective = str(a.get("objective") or "").strip()
        if not SLUG_RE.match(slug):
            problems.append(f"article {i}: bad slug {slug!r}")
            continue
        if slug in existing:
            problems.append(f"article {i}: duplicate slug {slug!r} (dropped)")
            continue
        if atype not in config.ARTICLE_TYPES:
            problems.append(f"article {slug}: bad type {atype!r}")
            continue
        if not title:
            problems.append(f"article {slug}: empty title")
            continue
        if not modules:
            problems.append(f"article {slug}: empty modules")
            continue
        if parent_node is not None and not all(m in allowed for m in modules):
            problems.append(f"article {slug}: modules outside parent's domain")
            continue
        need = bool(a.get("need_further_planning"))
        if atype in config.LEAF_TYPES:
            need = False
        if need and depth + 1 > limits["max_depth"]:
            need = False
        rec = {"slug": slug, "title": title, "type": atype,
               "objective": objective, "modules": modules,
               "need_further_planning": need, "depth": depth,
               "parent": parent_slug or ""}
        state["articles"].append(rec)
        existing.add(slug)
        added.append(rec)
    if parent_node is not None and added:
        # 展开应答已落地: 父节点的 need_further_planning 就此了结。
        # (原版从不清除 —— pending 永远含父节点, 下一层 payload 反复
        # 重指同一父, "planning complete → commit" 提示不可达, 引擎
        # 对同一父无限重问)
        parent_node["need_further_planning"] = False
    return added, problems


def materialize_layers(ctx: Ctx, plan: dict, state: dict) -> list:
    """把收集好的大纲物化成 plan pages(kind=page; sheet 页保留)。"""
    # 键控规范化: root 的 plan path 是 "/", rstrip 后成 '' —— 与
    # apply_layer_result 归一出的 "/" 曾对不上, overview(modules:["/"])
    # 查不到 root 模块, 源列表落空
    modules = {(str(m.get("path") or "/").rstrip("/") or "/"): m
               for m in plan_modules(plan)}
    root = next((m for m in plan_modules(plan) if m.get("id") == "root"), {})
    pages = []
    order = {"overview": 0, "getting_started": 1, "developer_guide": 2,
             "domain": 10, "deep_dive": 20}
    # 深度上限取 plan 可覆盖的 plan_limits —— 曾硬编码 LIMIT_DEFAULTS,
    # plan 声明 max_depth: 5 时 apply 门放行的 depth-5 文章在 commit 整层
    # 静默丢弃(模块覆盖断言随之失守)
    _limits = plan_limits(plan, state.get("profile", "standard"))
    # 提示词按风格分口径(themes.prompt_for 同源问题): 曾整段禁"顶部引用
    # 清单", 而 deep(DEFAULT_STYLE)的 cite_block_required=True —— 默认风格
    # 下整 run 的页全缺 <cite>, finish LINING 整批拒绝。
    # F30: 分档事实不再手写 —— gates 按 STYLE_PRESETS 执行, 提示词与门禁
    # 同一来源(预设里收紧 mermaid band 后, 手写的"5-8 张"会让 writers 按
    # 旧数字产出、门禁整批拒); 分支键也走预设而非 style 字面比较, 第三种
    # 风格不再静默继承 deep 散文(同 gates.py 的预设化口径)。
    style = str(plan.get("style") or ctx.style)
    _preset = preset(style)
    if _preset.get("cite_block_required"):
        _lo, _hi = _preset["mermaid"]
        style_tail = (f"深文口径：结尾自带 <cite> 引用块（逐文件列出正文引用的"
                      f"源码路径，与正文引用集合一致），全文配 {_lo}-{_hi} 张 "
                      f"mermaid 图，本节分析到具体文件时在节末给章节来源。")
    else:
        style_tail = ("短页口径：不要写目录、结论或顶部引用清单，本节分析到"
                      "具体文件时在节末给章节来源。")
    for depth in range(0, int(_limits["max_depth"]) + 1):
        level = [a for a in state["articles"] if a["depth"] == depth]
        level.sort(key=lambda a: (order.get(a["type"], 30),
                                  state["articles"].index(a)))
        for a in level:
            srcs = []
            for path in a["modules"]:
                if path == "/":
                    srcs.extend(root.get("scope") or [])
                else:
                    m = modules.get(path) or {}
                    srcs.extend(m.get("scope") or [f"{path}/"])
            sibling_ord = len([p for p in pages
                               if p.get("parent") == (a["parent"] or "")])
            prompt = (f"{a['objective']}\n\n"
                      f"文章类型：{a['type']}；按该类型的建议结构组织，"
                      "标题自拟（不要照抄英文说明）。" + style_tail)
            pages.append({
                "id": a["slug"], "slug": a["slug"], "title": a["title"],
                "parent": a["parent"] or "", "ordinal": sibling_ord,
                "kind": "page", "type": a["type"],
                "module": (modules.get(a["modules"][0])
                           or (root if a["modules"][0] == "/" else {})
                           or {}).get("id", ""),
                "modules": a["modules"], "objective": a["objective"],
                "prompt": prompt,
                "source_files": sorted(set(s for s in srcs if s)),
            })
    return pages


def layer_status_text(state: dict) -> str:
    done = [a for a in state["articles"] if not a.get("need_further_planning")]
    pending = [a for a in state["articles"] if a.get("need_further_planning")]
    return (f"articles: {len(state['articles'])} (leaves {len(done)}, "
            f"expandable {len(pending)}); next: "
            f"{pending[0]['slug'] if pending else 'commit'}")


def cmd_plan_layers(ctx: Ctx, args) -> int:
    from .planner import load_plan
    plan = load_plan(ctx)
    profile = ctx.config.get("profile", "standard")
    ac = (args.layers or "").strip()
    state = _load_layers(ctx)
    if ac == "init":
        author_pages = plan.get("author_pages") or []
        if author_pages:
            print("author_pages 白名单存在 — 跳过分层规划; 直接用声明的页面"
                  "作目录", file=sys.stderr)
            return config.EXIT_OK
        if state is not None and state.get("articles"):
            # 会话状态非空: 习惯性重跑 init 曾把累积的应用层大纲一键清零
            # —— 要重来需显式 `pwiki plan --reset --layers init`
            if not getattr(args, "reset", False):
                die(f"layer state 已有 {len(state['articles'])} 篇大纲"
                    "(交互会话状态) — 继续 apply/commit; 确要重来加 --reset",
                    config.EXIT_PRECOND)
            info("--reset: 丢弃既有分层大纲, 重新开始")
        state = {"profile": profile, "project": plan.get("title", ""),
                 "articles": [], "created_at": now_iso()}
        _save_layers(ctx, state)
        node = {"parent": None, "slug": "", "title": "", "depth": 0,
                "modules": ["/"]}
        print(layer_payload(ctx, plan, state, node))
        return config.EXIT_OK
    if state is None:
        die("no layer state — 先跑 `pwiki plan --layers init`",
            config.EXIT_PRECOND)
    if ac == "status":
        print(layer_status_text(state))
        return config.EXIT_OK
    if ac == "apply":
        if not args.file:
            # 前置条件不满足走 3(engine/qoder-plan 同契约), 不是内容门禁 2
            die("--layers apply 需要 --file", config.EXIT_PRECOND)
        try:
            with open(os.path.abspath(args.file), encoding="utf-8") as fh:
                text = fh.read()
        except OSError as e:
            die(f"result 文件读取失败: {args.file}({e}) — 确认 writer 输出"
                "路径", config.EXIT_PRECOND)
        try:
            added, problems = apply_layer_result(ctx, plan, state, text,
                                                 args.of or "")
        except ValueError as e:
            die(f"result 块无法解析({e}) — 只接受一个 <result>{{…}}</result> "
                "块或一个合法的 json 栅栏", config.EXIT_GATE)
        for p in problems:
            print(f"  warn: {p}", file=sys.stderr)
        if not added and problems:
            die("没有可接受的 article — 修 <result> 块重试", config.EXIT_GATE)
        _save_layers(ctx, state)
        pending = [a for a in state["articles"]
                   if a.get("need_further_planning")]
        print(f"added {len(added)} article(s); {layer_status_text(state)}")
        if pending:
            node = {"parent": pending[0].get("parent") or "",
                    "slug": pending[0]["slug"], "title": pending[0]["title"],
                    "depth": pending[0]["depth"], "modules": pending[0]["modules"]}
            print("\n---- next layer payload ----\n")
            print(layer_payload(ctx, plan, state, node))
        else:
            print("planning complete — `pwiki plan --layers commit`")
        return config.EXIT_OK
    if ac == "commit":
        new_tree = materialize_layers(ctx, plan, state)
        if not new_tree:
            die("layer state 里没有 article — 无从 commit", config.EXIT_PRECOND)
        old_pages = plan_pages(plan)
        kept_sheets = [p for p in old_pages if p.get("kind") == "module_sheet"]
        # 整树重建曾把 hand-added 页与手改字段静默抹掉(PLAN_HEADER 承诺
        # "重建只经 --reset"; themes 路线 c027 同款契约): 同 id 保留手工
        # 字段, 计划里有而本轮分层没再输出的内容页原样保留, 只有
        # `pwiki plan --reset` 才整体重建
        from .planner import MANUAL_FIELDS, assign_paths, validate_plan
        from .themes import (load_manual_baseline, merge_manual,
                             save_manual_baseline)
        # 三路合并基线(themes 路线同款): 显式清空/删键也是人工编辑
        engine_prev = load_manual_baseline(ctx)
        merged = merge_manual(new_tree, old_pages, MANUAL_FIELDS,
                              keep=lambda p: p.get("kind") != "module_sheet",
                              engine_prev=engine_prev)
        kept_manual = len(merged) - len(new_tree)
        if kept_manual:
            info(f"保留计划内已有、本轮分层未覆盖的手编页 {kept_manual} 个"
                 "(整体重建用 `pwiki plan --reset`)")
        plan["pages"] = merged + kept_sheets
        plan["schema_version"] = max(3, int(plan.get("schema_version") or 2))
        assign_paths(plan, ctx.locale)
        # 保存前过自家 validate(themes 路线同款闸): 预算/深度只作为 payload
        # 建议曾无门 —— commit 落盘的 plan 自己都过不了 begin, 规划全废
        errors, _w = validate_plan(
            ctx, plan, profile=str(ctx.config.get("profile", "standard")))
        if errors:
            for e in errors:
                print(f"  error: {e}", file=sys.stderr)
            die("commit 后的 plan 未过校验 — 调整 work/plan-layers.json "
                "的层级(裁文章数/深度)再 commit", config.EXIT_GATE)
        save_plan(ctx, plan)
        # 基线刷新为本次分层输出(按 id 并入, 卡片的既有基线保留)
        save_manual_baseline(ctx, new_tree)
        print(f"plan rewritten: {len(new_tree)} article pages + "
              f"{kept_manual} kept + "
              f"{len(kept_sheets)} module sheets -> {ctx.plan_path}")
        print("next: `pwiki begin`")
        return config.EXIT_OK
    die("--layers 必须是 init|apply|commit|status", config.EXIT_GATE)
    return config.EXIT_GATE
