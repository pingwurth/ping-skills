"""计划层: wiki_plan.yaml(v3) IO、校验门禁、种子目录、路径映射。

移植自 qwiki/scripts/qwiki.py 的 plan 半边(load/save/hash/validate/scaffold/
path mapping/progress), 适配:
  - schema v3(顶层 style/aliases; pages 增 scope/required_sections/pack_key/
    title_check; modules 增 module_type/skeleton/evidence)
  - 扫描器换 repowiki 口径(manifests 是 dict 列表、readme 单对象、
    entry_points 打分列表), qwiki 的 seed_modules 被 modules.py 的
    detect_modules 分解取代(D1 覆盖断言更强)
  - sheet 身份 slug 化(i18n), prompts 按风格取
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter

from . import config, i18n
from .config import Ctx, PROFILE_BUDGETS
from .util import posix, die, info, join_confined, safe_filename, slugify
from .yamlite import yaml_dump, yaml_load

PLAN_HEADER = """\
# pwiki 计划文件(v3) — 唯一人工编辑面
#
# 规则:
#   - 引擎绝不静默覆盖手工编辑; 需要重建时用 `pwiki plan --reset`
#     (themes 计划的重建是 `plan --themes --reset`)。
#   - `pwiki validate` 报错时 `pwiki begin` 被挡住(exit 2)。
#   - style: concise = Qoder 短页口径 / deep = repowiki 长文口径(门禁按此执行)。
#   - modules 的 path 是模块树路径(root 为 "/"); pages 的 parent 是页 id。
#   - 每个注册模块必须被至少一篇的 modules 覆盖(覆盖硬规)。
"""


# --------------------------------------------------------------------------- #
# Plan IO
# --------------------------------------------------------------------------- #

def load_plan(ctx: Ctx) -> dict:
    if not os.path.isfile(ctx.plan_path):
        die(f"plan not found: {ctx.plan_path} — 先跑 `pwiki modules` 或 `pwiki plan --seed`",
            config.EXIT_PRECOND)
    with open(ctx.plan_path, encoding="utf-8") as fh:
        text = fh.read()
    try:
        plan = yaml_load(text)
    except ValueError as e:
        die(f"wiki_plan.yaml 解析失败: {e}", config.EXIT_GATE)
    if not isinstance(plan, dict):
        die("wiki_plan.yaml: 顶层必须是映射", config.EXIT_GATE)
    return plan


def mod_titles_for(item, modules_list) -> list:
    """行/计划项 → 模块标题集(gates 模块提及检查的输入)。

    P32: submit/finish/validate --content/write/update 五处同源 —— 曾只有
    submit 传 mod_titles, gates 的 strict 升级层(`errors if strict`)是死
    代码, 模块分配规则在 LINING 从不强制。item 兼容 dict(计划项)与
    sqlite3.Row(job/page 行, 走 gates.rowget)。"""
    from .gates import rowget
    from .util import json_list
    wanted = {str(x).rstrip("/")
              for x in json_list(rowget(item, "modules", None))}
    single = rowget(item, "module", None)
    if single:
        wanted.add(str(single))
    titles = []
    for m in modules_list or []:
        keys = {str(m.get("id") or ""),
                str(m.get("path") or "").rstrip("/"),
                str(m.get("dir") or "").rstrip("/")}
        if keys & wanted and m.get("title"):
            titles.append(str(m["title"]))
    return titles


def save_plan(ctx: Ctx, plan: dict) -> None:
    """落盘(v3 头部注释 + 主体); 调用方负责"不覆盖手工编辑"的合并语义。

    c045: 临时文件 + os.replace 原子替换 —— 此前 open(path,'w') 先截断,
    进程在写入中途被杀/ENOSPC 会留下半份 wiki_plan.yaml(唯一人工编辑面
    的内容当场损毁, 且无备份)。"""
    from .util import atomic_write
    os.makedirs(ctx.root, exist_ok=True)
    atomic_write(ctx.plan_path, PLAN_HEADER + yaml_dump(plan))


def plan_modules(plan: dict) -> list:
    mods = plan.get("modules") or []
    return mods if isinstance(mods, list) else []


def plan_pages(plan: dict) -> list:
    pages = plan.get("pages") or []
    if not isinstance(pages, list):
        die("wiki_plan.yaml: 'pages' 必须是列表", config.EXIT_GATE)
    return pages


def plan_cards(plan: dict) -> list:
    cards = plan.get("cards") or []
    return cards if isinstance(cards, list) else []


def plan_hash(pages: list, cards: list) -> str:
    """内容相关字段的规范化哈希(prompt 漂移检测的基准)。"""
    canon = json.dumps(
        [{"kind": "page", **{k: p.get(k) for k in
                         ("id", "title", "parent", "ordinal", "kind", "module",
                          "sheet", "prompt", "source_files", "type", "slug",
                          "objective", "modules", "required_sections")}}
         for p in pages] +
        [{"kind": "card", **{k: c.get(k) for k in
                         ("id", "kind", "title", "scope", "prompt",
                          "source_files")}}
         for c in cards],
        ensure_ascii=False, sort_keys=True)
    return config.sha256_bytes(canon.encode())


def modules_hash(modules: list) -> str:
    canon = json.dumps(
        [{k: m.get(k) for k in ("id", "title", "dir", "scope", "depends_on",
                                "related_to", "module_type", "skeleton")}
         for m in modules],
        ensure_ascii=False, sort_keys=True)
    return config.sha256_bytes(canon.encode())


# --------------------------------------------------------------------------- #
# 路径映射(树 → 文件系统; qwiki bug4/11 修复随之)
# --------------------------------------------------------------------------- #

def modules_by_id(plan: dict) -> dict:
    return {str(m.get("id", "")): m for m in plan_modules(plan)}


def module_dir_name(mod: dict, by_id: dict) -> str:
    """知识层目录: 标题优先; 同名碰撞回退模块路径(qwiki bug11: ping 实跑
    80 张 sheet 塌成 8 个目录 —— Qoder 的知识树按 module_path 键控)。"""
    title = str(mod.get("title") or mod.get("id") or "module")
    same = [m for m in by_id.values() if str(m.get("title") or "") == title]
    if len(same) > 1:
        path = str(mod.get("path") or mod.get("dir") or mod.get("id") or title)
        return safe_filename(path)
    return safe_filename(title)


def page_path_for(pages: list, page: dict, locale: str, by_id: dict) -> str:
    """相对 markdown 路径: 页走 content 树(父页变同名目录), sheet 走 knowledge/。"""
    kind = page.get("kind", "page")
    if kind == "module_sheet":
        mod = by_id.get(page.get("module", "")) or {}
        mod_dir = module_dir_name(mod, by_id)
        display = i18n.sheet_display(i18n.sheet_slug(page.get("sheet") or ""), locale)
        return os.path.join("knowledge", locale, mod_dir, f"{safe_filename(display)}.md")
    by_page = {p.get("id"): p for p in pages}
    parts, cur, guard = [], page, 0
    while cur is not None and cur.get("parent") and guard < 10:
        parent = by_page.get(cur.get("parent"))
        if parent is None:
            break
        parts.append(safe_filename(parent.get("title", parent.get("id", "untitled"))))
        cur = parent
        guard += 1
    parts.reverse()  # 最远祖先在前(qwiki bug4: 嵌套父目录曾反序)
    parts.append(safe_filename(page.get("title", page.get("id", "untitled"))) + ".md")
    return os.path.join(locale, "content", *parts)


def card_path_for(card: dict, locale: str) -> str:
    name = safe_filename(card.get("title", card.get("id", "card")))
    return os.path.join("knowledge", locale, name, f"{name}.md")


def card_job(card: dict, locale: str) -> dict:
    """卡片 → job 字面量(source_files 回退 scope + knowledge 路径)。

    begin/regenerate/update/reconcile 四个排队入口曾各抄这三行, snapshot
    那份还漂移出遗留析取(伪页面事故); kind='card'/card_kind/ordinal 950
    的最终规范化仍统一落在 db.queue_jobs。"""
    item = dict(card)
    item["source_files"] = card.get("source_files") or card.get("scope") or []
    item["path"] = posix(card_path_for(card, locale))
    return item


# 人工编辑字段(c027 契约): 再规划合并时同 id 条目保留的手工调过的字段。
# themes 与 layers commit 两条再规划路由共用单一集合 —— 曾各自只保
# title/prompt, 手改的来源/目标/父级/序号被再生静默回退。
MANUAL_FIELDS = ("title", "prompt", "source_files", "scope", "objective",
                 "parent", "ordinal", "type", "slug")


def assign_paths(plan: dict, locale: str) -> None:
    """把派生 path 写回 pages/cards(发布与 job 都用同一映射)。"""
    by_id = modules_by_id(plan)
    pages = plan_pages(plan)
    for p in pages:
        p["path"] = posix(page_path_for(pages, p, locale, by_id))
    for c in plan_cards(plan):
        c["path"] = posix(card_path_for(c, locale))
    _die_on_path_collision(plan)


def _die_on_path_collision(plan: dict) -> None:
    """safe_filename 归一后的同路径碰撞(标题 '配置?' 与 '配置*' 归一到同名)。

    raw 同名兄弟 validate_plan 已拦, 但归一后才相同的标题从无人查 —— 发布
    循环逐行写文件, 后写者静默覆盖先写者。所有计划写入路由(seed/reset/
    themes/layers/reconcile)都过 assign_paths, 故在此统一拦截;
    validate 与 publish_run 各还有一道。"""
    seen: dict = {}
    for item in plan_pages(plan) + plan_cards(plan):
        pid = str(item.get("id") or "")
        path = str(item.get("path") or "")
        if not path:
            continue
        if path in seen and seen[path] != pid:
            die(f"路径冲突: {seen[path]!r} 与 {pid!r} 派生同一路径 {path!r} —"
                " 标题经文件名归一后同名, 改其一标题", config.EXIT_GATE)
        seen[path] = pid


# --------------------------------------------------------------------------- #
# 进度(evidence: uOl() percent mapping in workbench.desktop.main.js)
# --------------------------------------------------------------------------- #

def progress_pct(state: str, gen_type: str, total: int, completed: int) -> int:
    frac = (completed / total) if total else 0
    inc = gen_type == "INCREMENT"
    if state in ("COMPLETED", "PARTLY_COMPLETED"):
        return 100
    if state in ("WAITING", "LINING"):
        return 10
    if state == "INITIALIZING":
        return 20
    if state == "DIRECTORY_GENERATING":
        return int(30 + frac * (20 if inc else 10))
    if state == "DOCUMENT_GENERATING":
        return int((50 + frac * 50) if inc else (40 + frac * 60))
    return 0


# --------------------------------------------------------------------------- #
# Plan 校验(validatePlanConfig 门禁)
# --------------------------------------------------------------------------- #

MODULE_TYPES = ("real", "group", "container", "skeleton")


def _check_repo_path(ctx: Ctx, raw, what: str, errors: list, warnings: list,
                     warn_missing: bool, is_dir: bool = False) -> None:
    """c049 口径的 scope/源路径存在性检查(三处手抄块曾措辞与谓词漂移:
    module/card 用 exists、page 用 isdir —— 同一个坏路径在三个门上行为
    不一致)。越界一律错误(不给仓外存在性 oracle); 不存在在 warn_missing
    下降警告。"""
    sp = str(raw).rstrip("/")
    if not sp:
        return
    full = join_confined(ctx.repo, sp)
    if full is None:
        errors.append(f"{what} 路径越界(仅限仓内相对路径): {raw}")
        return
    ok = os.path.isdir(full) if is_dir else os.path.exists(full)
    if not ok:
        (warnings if warn_missing else errors).append(
            f"{what} 路径不存在: {raw}")


def validate_plan(ctx: Ctx, plan: dict, profile: str = "standard",
                  warn_missing: bool = False, strict: bool = True) -> tuple:
    """计划门禁。strict = begin/FULL 口径(形状下限为错误); update 路径传
    strict=False —— 已存在的瘦 wiki 永远不被新规则硬锁(qwiki 语义)。"""
    errors, warnings = [], []
    shape: list = errors if strict else warnings
    sv = plan.get("schema_version")
    if sv not in (1, 2, 3):
        errors.append(f"schema_version 必须是 1/2/3( got {sv!r})")
    if sv == 2:
        warnings.append("schema_version 2 可读; 建议升级 3(加 style/aliases 与"
                        " pages 新字段) — 编辑保存时自动完成")
    style = plan.get("style") or config.DEFAULT_STYLE
    if style not in config.STYLES:
        errors.append(f"style 必须是 {config.STYLES}(got {style!r})")
    elif style != ctx.style:
        warnings.append(f"plan style={style} 与 config.json style={ctx.style} "
                        "不一致; 以 plan 为准(begin 会按 plan 盖章)")
    pages = plan_pages(plan)
    cards = plan_cards(plan) if sv != 1 else []
    modules = plan_modules(plan) if sv != 1 else []

    mod_id_list = [str(m.get("id", "")).strip() for m in modules]
    mod_ids = set(mod_id_list)
    # 重复 module id 检查要在**原始列表**上数(曾数去重后的 set, 恒为 1,
    # 手改 plan 复制粘贴出两个 user-service 也静默 last-wins)
    dup_mod = {i for i in mod_ids if mod_id_list.count(i) > 1}
    if dup_mod:
        errors.append(f"重复 module id: {sorted(dup_mod)}")
    for m in modules:
        mid = str(m.get("id", "")).strip()
        if not mid:
            errors.append("module id 为空")
        mt = str(m.get("module_type") or "real")
        if mt not in MODULE_TYPES:
            errors.append(f"module {mid}: module_type 必须是 {MODULE_TYPES}(got {mt!r})")
        for s in m.get("scope") or []:
            # c049 同族: 计划 scope 不得逃出 repo(绝对/../ 一律拒)
            _check_repo_path(ctx, s, f"module {mid}: scope",
                             errors, warnings, warn_missing)
        for dep in (m.get("depends_on") or []) + (m.get("related_to") or []):
            dep = dep.get("path") if isinstance(dep, dict) else dep
            if dep and dep not in mod_ids:
                errors.append(f"module {mid}: 未知 depends_on/related_to: {dep}")

    # aliases 形状(依赖别名规则数据化 —— 修 qwiki yudao 硬编码)
    for a in plan.get("aliases") or []:
        if not isinstance(a, dict) or not a.get("patterns") or not a.get("module"):
            errors.append(f"aliases 条目需要 patterns+module: {a!r}")
        elif str(a.get("module")) not in mod_ids and str(a.get("module")) != "root":
            errors.append(f"aliases 指向未知模块: {a.get('module')!r}")

    if not pages and not cards:
        errors.append("pages 与 cards 均为空")
    ids = [str(p.get("id", "")).strip() for p in pages] + \
          [str(c.get("id", "")).strip() for c in cards]
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        errors.append(f"重复 page/card id: {sorted(dup)}")
    # 页的 parent 只能是**内容页** id: 曾把卡 id 并进合法集 —— parent 指卡的页
    # 过门后静默重根(评审树不显示/根数上限不计)。module_sheet 同理:
    # qoder-plan 的 by_id 过滤 sheet, 挂 sheet 下的页在那边悬空 parent
    # 直接 die, 而这里曾放行(且树 walk 把它当根, min_roots 误计)
    page_ids = {str(p.get("id", "")).strip() for p in pages}
    sheet_ids = {str(p.get("id", "")).strip() for p in pages
                 if p.get("kind", "page") == "module_sheet"}
    parents = {str(p.get("parent", "")).strip() for p in pages}
    unknown = parents - page_ids - {""}
    if unknown:
        errors.append(f"未知 parent id: {sorted(unknown)}")
    sheet_parented = sorted(
        {str(p.get("parent", "")).strip() for p in pages} & sheet_ids)
    if sheet_parented:
        errors.append(f"parent 指向 module_sheet(sheet 是知识层叶子, 不是树父): "
                      f"{sheet_parented}")
    graph = {str(p.get("id")): str(p.get("parent", "")) for p in pages}
    for start in graph:
        seen, cur = set(), start
        while cur and cur in graph:
            if cur in seen:
                errors.append(f"parent 环: {start}")
                break
            seen.add(cur)
            cur = graph[cur]
    # 预算只数内容页(kind != module_sheet) —— themes 的裁剪循环按内容页
    # 对齐预算, expand_sheets 再按模块数叠速查表; 曾把两者加总对一个
    # 预算, ≥11 模块的标准仓库跑完 SKILL.md 文档的三步序列直接被
    # validate 拒死(238 > 135), 无参数可解
    content_pages = [p for p in pages
                     if p.get("kind", "page") != "module_sheet"]
    budget = PROFILE_BUDGETS.get(profile, 135)
    if profile not in PROFILE_BUDGETS:
        warnings.append(f"config.json profile {profile!r} 未知, 预算按 {budget} "
                        f"处理(可选 {sorted(PROFILE_BUDGETS)})")
    # 硬预算与下方树形检查(:433)同源取 plan_limits —— 曾用裸 PROFILE_BUDGETS,
    # plan limits: max_total_articles 覆盖只能压低不能抬高, 140 页仓库
    # 配 160 上限时树检查过、这里拒死, 无 plan 侧解
    budget = plan_limits(plan, profile)["max_total_articles"]
    if len(content_pages) > budget:
        errors.append(f"{len(content_pages)} 内容页(不含 module_sheet) 超出 "
                      f"预算 {budget}(profile '{profile}' / plan limits 可覆盖)")
    if len(cards) > 60:
        warnings.append(f"{len(cards)} cards 偏多(>60); 考虑裁剪")
    for p in pages:
        pid = str(p.get("id", "")).strip()
        if not pid:
            errors.append("page id 为空")
            continue
        # c034: id 会进文件路径(孤儿稿保存/发布派生) —— 只查非空时, 含 '/'
        # 或 '../' 的手编 id 可把写落点挪到 .pwiki 之外; 只禁路径分隔符/
        # 穿越段/前导点, 中文 id 合法
        if config.ID_RE_UNSAFE.search(pid):
            errors.append(f"page {pid}: id 不得含路径分隔符/../ 穿越段或前导点")
        # None 标题曾被 str(None)='None' 洗成真值过门, begin 在
        # safe_filename(None) 裸崩(re.sub on None)
        ptitle = p.get("title")
        if not isinstance(ptitle, str) or not ptitle.strip():
            errors.append(f"page {pid}: 缺 title(或非字符串)")
        kind = p.get("kind", "page")
        if kind not in ("page", "module_sheet"):
            errors.append(f"page {pid}: kind 必须是 page|module_sheet(got {kind!r})")
        # `ordinal:` 空值是 None —— 键存在, .get 默认值不生效, 曾一路裸
        # int(None) 崩到 begin/update/finish/qoder-plan 四处
        if "ordinal" in p and (isinstance(p["ordinal"], bool)
                               or not isinstance(p["ordinal"], int)):
            errors.append(f"page {pid}: ordinal 必须是整数"
                          f"(got {p['ordinal']!r})")
        if kind == "module_sheet":
            if not modules:
                errors.append(f"page {pid}: module_sheet 需要 modules 注册表")
            elif str(p.get("module", "")).strip() not in mod_ids:
                errors.append(f"page {pid}: 未知 module: {p.get('module')!r}")
            sheet_slug = i18n.sheet_slug(str(p.get("sheet") or ""))
            if sheet_slug not in i18n.SHEET_SLUGS:
                errors.append(f"page {pid}: sheet 必须是 {i18n.SHEET_SLUGS}"
                              f"(got {p.get('sheet')!r})")
        elif str(p.get("module", "")).strip() and str(p["module"]).strip() not in mod_ids:
            errors.append(f"page {pid}: 未知 module: {p.get('module')!r}")
        srcs = p.get("source_files") or []
        if not isinstance(srcs, list):
            errors.append(f"page {pid}: source_files 必须是列表")
            srcs = []
        if not srcs and not warn_missing:
            warnings.append(f"page {pid}: 无 source_files(无证据页)")
        for s in srcs:
            s = str(s)
            if not s:
                continue
            # c049/c051: 计划声明的源不得逃出 repo(绝对/../ 一律拒)
            target = join_confined(ctx.repo, s.rstrip("/"))
            if target is None:
                errors.append(f"page {pid}: 源路径越界(仅限仓内相对路径): {s}")
            elif s.endswith("/"):
                if not os.path.isdir(target):
                    (warnings if warn_missing else errors).append(
                        f"page {pid}: 源目录不存在: {s}")
            elif not os.path.isfile(target):
                (warnings if warn_missing else errors).append(
                    f"page {pid}: 源文件不存在: {s}")
        for s in p.get("scope") or []:
            _check_repo_path(ctx, s, f"page {pid}: scope",
                             errors, warnings, warn_missing, is_dir=True)
        if not str(p.get("prompt", "")).strip():
            warnings.append(f"page {pid}: prompt 为空")
    for c in cards:
        cid = str(c.get("id", "")).strip()
        ctitle = c.get("title")
        if not cid or not isinstance(ctitle, str) or not ctitle.strip():
            errors.append(f"card 缺 id/title(或非字符串): {cid}")
        if c.get("kind") not in config.CARD_KINDS:
            errors.append(f"card {cid}: kind 必须是 {config.CARD_KINDS}")
        # Qoder 体系卡命名是「体系名+构成要素」事实短语, 不用裸类别名。
        if c.get("kind") in config.SYSTEM_CARD_KINDS and \
                str(c.get("title", "")).strip() == \
                i18n.card_kind_display(c.get("kind", ""), ctx.locale):
            warnings.append(f"card {cid}: 通用标题 {c.get('title')!r} — 在 plan 里"
                            " 改成本仓库的 体系名+构成要素 事实短语")
        srcs = c.get("source_files") or []
        if not srcs:
            warnings.append(f"card {cid}: 无 source_files")
        for s in srcs:
            _check_repo_path(ctx, s, f"card {cid}: 源",
                             errors, warnings, warn_missing)

    # 树形上限(证据: deepwiki catalogue config), 只作用于 content 树(kind=page)。
    limits = plan_limits(plan, profile)
    tree = [p for p in pages if p.get("kind", "page") == "page"]
    by_id_page = {str(p.get("id")): p for p in pages}

    def depth(p):
        d, cur, guard = 0, p, 0
        while cur.get("parent") and guard < 12:
            cur = by_id_page.get(str(cur.get("parent")), {})
            d += 1
            guard += 1
        return d

    max_depth_seen = max((depth(p) for p in tree), default=0)
    roots = [p for p in tree if not p.get("parent")]
    sub_counts = Counter(str(p.get("parent")) for p in tree if p.get("parent"))
    if len(tree) > limits["max_total_articles"]:
        errors.append(f"content 树 {len(tree)} 篇 > "
                      f"max_total_articles={limits['max_total_articles']}")
    if max_depth_seen > limits["max_depth"]:
        errors.append(f"content 树深度 {max_depth_seen} > "
                      f"max_depth={limits['max_depth']}")
    if len(roots) > limits["max_top_articles"]:
        errors.append(f"{len(roots)} 个顶层 > "
                      f"max_top_articles={limits['max_top_articles']}")
    over_sub = {p: n for p, n in sub_counts.items()
                if n > limits["max_sub_per_parent"]}
    if over_sub:
        errors.append(f"max_sub_per_parent={limits['max_sub_per_parent']} 超限: "
                      f"{over_sub}")

    # 形状下限: begin/FULL 为错误, update 为警告; 按注册表规模缩放
    # (3 模块的玩具仓库不该被垫到 60 篇)。
    if tree:
        top_mods = [m for m in modules
                    if m.get("id") != "root"
                    and (m.get("parent") or "") in ("", "root")]
        mod_count = len([m for m in modules if m.get("id") != "root"])
        need_roots = min(limits["min_roots"], 3 + len(top_mods))
        need_total = min(limits["min_total_articles"], mod_count)
        sev = shape if (mod_count >= 10 and top_mods) else warnings
        if len(roots) < need_roots:
            sev.append(f"{len(roots)} 个顶层 < min_roots={need_roots} — 单根是"
                       "链不是目录(Qoder 实测 9-17)")
        if len(tree) < need_total:
            sev.append(f"content 树 {len(tree)} 篇 < "
                       f"min_total_articles={need_total}(profile '{profile}')")
    typed = [p for p in tree if str(p.get("type") or "")]
    for p in typed:
        t = str(p.get("type") or "")
        pid = str(p.get("id"))
        if t not in config.ARTICLE_TYPES:
            errors.append(f"page {pid}: type 必须是 {config.ARTICLE_TYPES}(got {t!r})")
        if t in config.LEAF_TYPES and sub_counts.get(pid):
            errors.append(f"page {pid}: type '{t}' 按设计是叶子却有 "
                          f"{sub_counts[pid]} 个子篇")
    if typed:
        slugs = [str(p.get("slug") or "") for p in typed if p.get("slug")]
        dups = {s for s in slugs if slugs.count(s) > 1}
        if dups:
            errors.append(f"重复 article slug: {sorted(dups)}")
        for p in typed:
            if not str(p.get("slug") or ""):
                warnings.append(f"page {p.get('id')}: 有 type 无 slug")
            if not str(p.get("objective") or ""):
                warnings.append(f"page {p.get('id')}: 有 type 无 objective"
                                "(1-2 句 + 'Out of scope: ...')")
    # 覆盖硬规: 每个注册模块出现在至少一篇的 modules 里(跨层并集)。
    # 骨架/占位模块豁免(有 sheet 但不强制成文)。
    if typed:
        module_keys = {}
        for m in modules:
            mid = str(m.get("id", ""))
            keys = {mid, str(m.get("path") or ""), str(m.get("dir") or ""),
                    str(m.get("title") or "")}
            module_keys[mid] = {k.rstrip("/") for k in keys if k}
        covered = set()
        for p in tree:
            for mod in (p.get("modules") or []):
                covered.add(str(mod).rstrip("/"))
        for m in modules:
            mid = str(m.get("id", ""))
            if mid == "root":
                continue
            if m.get("skeleton") or str(m.get("module_type")) == "skeleton":
                continue
            keys = module_keys.get(mid) or {mid}
            if not (keys & covered):
                shape.append(f"module '{mid}' 未被任何文章的 modules 覆盖(覆盖硬规)")
    # 反模式: 兄弟同名 + 分层泄漏标题。同名兄弟是**错误** —— 路径由标题派生,
    # 同名即同路径, 发布会静默互相覆盖(曾只是警告, 照样 begin)。
    # 比对用 safe_filename 归一后的文件名: raw 不同('配置?' vs '配置*')但
    # 归一后同名的标题同样派生同一路径, raw 比对从无人拦。
    by_parent = {}
    for p in tree:
        by_parent.setdefault(str(p.get("parent") or ""), []).append(p)
    for parent, sibs in by_parent.items():
        names: dict = {}
        for p in sibs:
            names.setdefault(safe_filename(str(p.get("title") or "")), []).append(
                str(p.get("id") or "?"))
        dup = {k: ids for k, ids in names.items() if len(ids) > 1}
        if dup:
            detail = "; ".join(f"{'/'.join(ids)} → {k}.md"
                               for k, ids in sorted(dup.items()))
            errors.append(f"兄弟标题冲突(父 {parent or '<root>'}, "
                          f"文件名归一后同名): {detail}"
                          " — 派生同一路径, 会静默互相覆盖")
    # 卡片标题同理(knowledge/<标题>/<标题>.md), validate 从不查过; 同样按
    # 归一后文件名比对
    card_names: dict = {}
    for c in cards:
        key = safe_filename(str(c.get("title", "")).strip())
        if key:
            card_names.setdefault(key, []).append(str(c.get("id") or "?"))
    dup_ct = {k: ids for k, ids in card_names.items() if len(ids) > 1}
    if dup_ct:
        detail = "; ".join(f"{'/'.join(ids)} → {k}"
                           for k, ids in sorted(dup_ct.items()))
        errors.append(f"卡片标题冲突(文件名归一后同名, 派生同一路径): {detail}")
    for p in tree:
        low = str(p.get("title") or "").lower()
        if any(low.strip() == t or low.strip().endswith(" " + t)
               for t in config.ANTI_PATTERN_LEAKY_TITLE):
            warnings.append(f"page {p.get('id')}: 分层式标题 {p.get('title')!r} —"
                            " 改成读者意图的领域名")
    return errors, warnings


def plan_limits(plan: dict, profile: str) -> dict:
    """目录树上限: plan limits: 块覆盖 profile 默认。caps 来自 Qoder Config
    (engineConfig catalogue validation maxDepth=4); min_* 是 qwiki 按观测
    输出标定的形状下限(roots 9-17, articles 25-131)。"""
    defaults = {"max_depth": config.LIMIT_DEFAULTS["max_depth"],
                "max_total_articles": PROFILE_BUDGETS.get(profile, 135),
                "max_top_articles": config.LIMIT_DEFAULTS["max_top_articles"],
                "max_sub_per_parent": config.LIMIT_DEFAULTS["max_sub_per_parent"],
                "min_roots": config.LIMIT_DEFAULTS["min_roots"],
                "min_total_articles":
                    config.LIMIT_DEFAULTS["min_total_articles"].get(profile, 60)}
    raw = plan.get("limits") or {}
    limits = dict(defaults)
    if isinstance(raw, dict):
        for k in defaults:
            try:
                v = int(raw.get(k, defaults[k]))
            except (TypeError, ValueError):
                continue
            limits[k] = v
    return limits


# --------------------------------------------------------------------------- #
# 信号与种子(适配 repowiki 口径 inventory)
# --------------------------------------------------------------------------- #

def _manifest_paths(inv: dict) -> list:
    return [str(m.get("path")) for m in inv.get("manifests", []) if m.get("path")]


def _setup_signals(inv: dict) -> bool:
    names = {os.path.basename(p).lower() for p in _manifest_paths(inv)}
    readme = (inv.get("readme") or {}).get("path", "").lower()
    return bool(names & {"makefile", "docker-compose.yml", "docker-compose.yaml",
                         "dockerfile", "package.json", "pom.xml", "go.mod"}
                or inv.get("entry_points")
                or any(k in readme for k in ("getting", "quick", "start")))


def _dev_signals(inv: dict) -> bool:
    files = {f["path"].lower() for f in inv.get("files", [])}
    doc_hits = {p for p in files if os.path.basename(p) in
                ("contributing.md", "develop.md", "agents.md",
                 "development.md", "hacking.md")}
    return bool(inv.get("ci") or doc_hits)


def _unique_id(base: str, used: set) -> str:
    """全局唯一 plan id(Qoder slug 全局唯一; scripts 这类目录名跨父重复)。"""
    candidate, n = base, 2
    while candidate in used or not candidate:
        candidate = f"{base}-{n}"
        n += 1
    used.add(candidate)
    return candidate


def scaffold_pages(inv: dict, plan: dict) -> list:
    """保守多种子目录(qwiki scaffold_plan 的 pages 部分), 模块来自 plan 注册表
    (由 `pwiki modules` 写入)。"""
    modules = plan_modules(plan)
    if not modules:
        die("plan 没有 modules — 先跑 `pwiki modules`(D1 覆盖断言在那里)",
            config.EXIT_PRECOND)
    lang_note = "、".join(l["lang"] for l in inv.get("languages", [])[:3]) or "多语言"
    by_parent = {}
    for m in modules:
        by_parent.setdefault(m.get("parent") or "", []).append(m)
    top_mods = [m for m in by_parent.get("root", []) if m["id"] != "root"]
    pages = []
    used_ids = {"overview", "getting-started", "developer-guide"}
    mpaths = _manifest_paths(inv)
    readme_path = (inv.get("readme") or {}).get("path")
    ov_sources = ([readme_path] if readme_path else []) + \
        [p for p in mpaths if os.path.basename(p).lower()
         in ("package.json", "pom.xml", "go.mod", "cargo.toml",
             "pyproject.toml", "makefile")][:5]
    pages.append({
        "id": "overview", "slug": "overview", "title": "项目概览",
        "parent": "", "ordinal": 0, "kind": "page", "type": "overview",
        "module": "root", "modules": ["/"],
        "objective": (f"项目定位与核心价值、技术栈（{lang_note}）、模块地图与"
                      "阅读路径。Out of scope: 各领域内部细节。"),
        "prompt": (f"撰写项目概览：定位与核心价值、技术栈（{lang_note}）、模块划分"
                   "（见 digest 的模块表）、构建/运行方式。引用 README 与构建清单。"),
        "source_files": sorted(set(ov_sources)) or mpaths[:3],
    })
    ordinal = 1
    if _setup_signals(inv):
        pages.append({
            "id": "getting-started", "slug": "getting_started", "title": "快速开始",
            "parent": "", "ordinal": ordinal, "kind": "page",
            "type": "getting_started", "module": "root", "modules": ["/"],
            "objective": "环境准备、安装、首次运行与常见工作流。Out of scope: 各领域设计细节。",
            "prompt": "写安装、首次运行与常见工作流（命令来自真实清单文件）。",
            "source_files": sorted(set(ov_sources))[:6] or mpaths[:3],
        })
        ordinal += 1
    if _dev_signals(inv):
        pages.append({
            "id": "developer-guide", "slug": "developer_guide", "title": "开发指南",
            "parent": "", "ordinal": ordinal, "kind": "page",
            "type": "developer_guide", "module": "root", "modules": ["/"],
            "objective": "构建/测试/贡献流程与编码约定。Out of scope: 业务域行为。",
            "prompt": "写构建、测试、CI 与编码约定（只写代码里真实存在的做法）。",
            "source_files": sorted(set(ov_sources))[:6] or mpaths[:3],
        })
        ordinal += 1

    # 每个显著顶层模块一篇 domain; 溢出并入兄弟(不截断)。domain 名额为
    # 潜在的"其他模块"溢出页预留 1 个根位 —— 曾不留位, 溢出一触发就是
    # 21 个顶层 > max_top_articles=20, 自产计划过不了自家 validate
    limits = plan_limits(plan, "standard")
    max_domains = max(1, limits["max_top_articles"] - ordinal - 1)
    keep = top_mods[:max_domains]
    overflow = top_mods[max_domains:]
    ordinal = max(ordinal, 10)
    for m in keep:
        subs = by_parent.get(m["id"], [])
        dom_id = _unique_id(slugify(m.get("path") or m.get("title") or "domain",
                                    "domain"), used_ids)
        mod_paths = [str(m.get("path") or m["id"])] + \
                    [str(s.get("path") or s["id"]) for s in subs]
        pages.append({
            "id": dom_id, "slug": dom_id, "title": m["title"], "parent": "",
            "ordinal": ordinal, "kind": "page", "type": "domain",
            "module": m["id"], "modules": mod_paths,
            "objective": f"聚合 {m['title']} 领域的模块、边界与跨组件流程。"
                         "Out of scope: 单个子模块的实现细节。",
            "prompt": (f"为领域「{m['title']}」（{m.get('dir') or '根'}）撰写领域级"
                       "文档：该领域解决的问题、包含哪些子模块与边界、跨组件流程、"
                       "设计取舍。子模块细节交给子文章，不要复述。"),
            "source_files": m.get("scope") or [],
        })
        ordinal += 1
        for s in subs[:limits["max_sub_per_parent"]]:
            sid = _unique_id(slugify(s.get("path") or s.get("title") or "deep",
                                     f"deep-{ordinal}"), used_ids)
            pages.append({
                "id": sid, "slug": sid, "title": s["title"], "parent": dom_id,
                "ordinal": len([p for p in pages if p.get("parent") == dom_id]),
                "kind": "page", "type": "deep_dive", "module": s["id"],
                "modules": [str(s.get("path") or s["id"])],
                "objective": f"深入分析模块 {s['title']}：职责、关键抽象、"
                             "内部数据流与配置。",
                "prompt": (f"为模块「{s['title']}」（{s.get('dir') or '根'}）撰写"
                           "深入文档：职责、关键组成、内部结构与数据流、配置项、"
                           "关键文件地图。先通读该模块再下笔。"),
                "source_files": s.get("scope") or [],
            })
    if overflow:
        dom_id = _unique_id("other-domains", used_ids)
        mod_paths = [str(m.get("path") or m["id"]) for m in overflow]
        pages.append({
            "id": dom_id, "slug": dom_id, "title": "其他模块", "parent": "",
            "ordinal": ordinal, "kind": "page", "type": "domain",
            "module": overflow[0]["id"], "modules": mod_paths,
            "objective": "收纳未单独成篇的次级模块，给出定位与阅读指向。",
            "prompt": "简述这些模块各自的职责与边界，并链接到相关文章。",
            "source_files": [s for m in overflow for s in (m.get("scope") or [])][:8],
        })
    return pages


def seed_cards(inv: dict, locale: str) -> list:
    """知识卡种子(适配 repowiki inventory): 外部依赖 top5 + 有证据的体系卡。
    体系卡标题从通用名起步, 期望在 plan 里改成 体系名+构成要素 短语(validate 警告)。"""
    cards = []
    mpaths = _manifest_paths(inv)
    # 依赖计数(package.json/pyproject/pom 的依赖名并集, 去掉工具类噪音)
    dep_counter = Counter()
    for m in inv.get("manifests", []):
        deps = m.get("dependencies") or {}
        if isinstance(deps, dict):
            for name in deps:
                low = str(name).lower()
                if low.startswith(("@types/", "@babel/")) or low in (
                        "eslint", "prettier", "typescript"):
                    continue
                dep_counter[str(name)] += 1
    used_dep_ids = set()
    for i, (name, _) in enumerate(dep_counter.most_common(5)):
        # slug 会撞车("@foo/bar" 与 "foo-bar" 同 slug) —— id 去重,
        # 引擎自己生成的计划必须能过自己的 validate
        base = f"dep-{slugify(name, str(i))}"
        cid, n = base, 2
        while cid in used_dep_ids:
            cid = f"{base}-{n}"
            n += 1
        used_dep_ids.add(cid)
        cards.append({
            "id": cid,
            "kind": "external_dependency", "title": name, "scope": ["**"],
            "prompt": (f"记录外部依赖 {name}：用途、使用方、版本约束、升级注意点；"
                       "正文按 1.总体方案/2.关键文件/3.架构与约定/4.约定与约束/"
                       "5.局限 组织，路径用反引号。"),
            "source_files": mpaths[:3],
        })
    file_paths = [f["path"] for f in inv.get("files", [])]
    config_files = [p for p in file_paths if re.search(
        r"(^|/)(config|settings|conf)(/|\.)|\.env$|application[\w.]*\.(yml|yaml|properties)$",
        p)][:6]
    build_entries = [p for p in mpaths if os.path.basename(p).lower() in
                     ("makefile", "install.sh", "justfile", "taskfile.yml",
                      "taskfile.yaml", "build.sh", "dockerfile",
                      "docker-compose.yml", "docker-compose.yaml")]
    agent_docs = [p for p in file_paths if os.path.basename(p).upper() in
                  ("AGENTS.md", "CLAUDE.md", "CONTRIBUTING.md")][:4]
    frontend_files = [p for p in file_paths if p.endswith(
        (".css", ".scss", ".less", ".vue", ".svelte"))][:6]
    dep_names = [d for d, _ in dep_counter.most_common(30)]
    system_cards = [
        ("configuration_system", config_files,
         "总结配置系统：配置文件清单、加载顺序、环境变量（只写名字）、各配置项作用。"),
        ("build_system", build_entries,
         "总结构建与 CI：入口命令、流水线阶段、产物、本地复现方式。"),
        ("dependency_management", mpaths[:4],
         "总结依赖管理：清单文件、版本锁定方式、升级策略、多包/多语言协调。"),
        ("business_term", agent_docs,
         "术语表（词条式模板，不是五段式）：从仓库的 agent/贡献者文档与代码标识符里"
         "提炼新人最易误解的领域词汇，每个术语一个 `### 术语名` 小节，条目为"
         "「- 定义：…（一两句，带出处文件）」与「- 别名：…」。"),
        ("error_handling",
         [d for d in dep_names if any(k in d.lower() for k in
                                      ("sentry", "resilience", "hystrix", "retry"))],
         "总结错误处理：错误类型层次、统一映射/信封、重试与降级、日志关联。"),
        ("logging_system",
         [d for d in dep_names if any(k in d.lower() for k in
                                      ("log", "log4j", "slf4j", "zap", "pino",
                                       "winston", "logrus"))],
         "总结日志体系：框架、级别与格式、采集/汇聚链路、敏感信息处理。"),
        ("frontend_style", frontend_files,
         "总结前端样式体系：令牌/变量、组件库、按窗口/页面拆分方式。"),
    ]
    for kind, evidence, prompt in system_cards:
        srcs = [str(x) for x in evidence if x][:6]
        if not srcs:
            continue
        cards.append({
            "id": f"card-{slugify(kind, 'card')}",
            "kind": kind, "title": i18n.card_kind_display(kind, locale),
            "scope": ["**"],
            "prompt": (prompt + " 正文按 1.总体方案/2.关键文件/3.架构与约定/"
                       "4.约定与约束/5.局限 组织；路径用反引号。"),
            "source_files": srcs if kind != "build_system" else (mpaths[:4] or srcs),
        })
    return cards[:12]


def expand_sheets(plan: dict, profile: str, locale: str, style: str) -> int:
    """按 profile 展开模块 sheet 页(slug 身份 + i18n 显示名 + 风格化提示词)。"""
    sheets = i18n.SHEET_PROFILE.get(profile, [])
    if not sheets:
        return 0
    modules = plan_modules(plan)
    if not modules:
        return 0
    # pages 键缺失时手造列表: 曾硬索引 plan["pages"] —— 手工裁掉 pages: 行
    # 的 plan 在本路线 KeyError traceback(其他访问方都容忍缺键)
    pages = plan.setdefault("pages", [])
    existing = {str(p.get("id")) for p in pages}
    added = 0
    for m in modules:
        if m.get("id") == "root" and not (m.get("scope") or []):
            continue   # 空 scope 的 root 不强造 sheet
        for sheet in sheets:
            pid = f"{m.get('id')}-{sheet}"
            if pid in existing:
                continue
            pages.append({
                "id": pid,
                "title": i18n.sheet_display(sheet, locale),
                "parent": "", "ordinal": 900 + i18n.SHEET_SLUGS.index(sheet),
                "kind": "module_sheet", "module": m.get("id", ""), "sheet": sheet,
                "prompt": i18n.sheet_prompt(sheet, style, locale,
                                            str(m.get("title", ""))),
                "source_files": m.get("scope") or [],
            })
            added += 1
    return added


# --------------------------------------------------------------------------- #
# cmd: plan / validate
# --------------------------------------------------------------------------- #

def cmd_plan(args) -> int:
    ctx = Ctx(args).load()
    has_plan = os.path.isfile(ctx.plan_path)
    # 生效风格 = plan 级优先, 否则 config(与 begin/submit/finish 同口径)。
    # sheet 提示词曾按 ctx.style 取而 job 盖章按 plan.style —— 提示词与
    # 它自己的验收闸门用两种风格, 写得再对也被拒
    # 探测解析只做一次, 各分支复用(r4 #71: 每分支曾各自重读同一份
    # wiki_plan.yaml —— 纯重复 ~24-80ms/次)。探测吞掉的 SystemExit 在
    # 真正需要 plan 的分支由重读复现(原行为)
    plan = None
    plan_style = ""
    if has_plan:
        try:
            plan = load_plan(ctx)
            plan_style = str((plan or {}).get("style") or "")
        except SystemExit:
            plan = None
            plan_style = ""
    style = plan_style or ctx.style

    if getattr(args, "layers", None):
        from . import layers
        return layers.cmd_plan_layers(ctx, args)

    if getattr(args, "themes", False):
        from . import themes
        plan_t = plan if plan is not None else (
            load_plan(ctx) if has_plan else {
                "schema_version": 3, "locale": ctx.locale, "style": style,
                "title": "Repo Wiki", "repo_summary": "", "modules": [],
                "pages": [], "cards": [],
            })
        return themes.cmd_plan_themes(ctx, args, plan_t)

    if getattr(args, "seed", False) or getattr(args, "reset", False) or not has_plan:
        # 重建/新建: modules 保留(重置目录不重置注册表 —— `pwiki modules --reset`
        # 才重置注册表), pages/cards 重新种子; module_sheet 随注册表存活 ——
        # 曾只从 scaffold 出页, reset 一次 sheet 全部蒸发, 下次 finish 把
        # 已发布 knowledge/<模块>/*.md 当 moved-out 孤儿整树剪掉(themes
        # 路线的同款守卫)
        old = plan if plan is not None else (load_plan(ctx) if has_plan else {})
        old_pages = old.get("pages") if isinstance(old.get("pages"), list) \
            else []
        kept_sheets = [dict(p) for p in old_pages
                       if isinstance(p, dict) and p.get("kind") == "module_sheet"]
        inv = _require_inventory(ctx)
        plan = {
            "schema_version": 3,
            "locale": ctx.locale,
            "style": old.get("style") or style,
            "title": old.get("title") or "Repo Wiki",
            "repo_summary": old.get("repo_summary") or "",
            "export_style": old.get("export_style") or
            (ctx.config.get("export_style") or "legacy"),
            "aliases": old.get("aliases") or [],
            "modules": old.get("modules") or [],
            "pages": scaffold_pages(inv, {"modules": old.get("modules") or [],
                                          "limits": old.get("limits") or {}})
            + kept_sheets,
            "cards": seed_cards(inv, ctx.locale),
            "placeholders": old.get("placeholders") or [],
            # limits 被上方 scaffold 消费来定尺寸, 却没存回 —— 手编
            # max_sub_per_parent: 12 重建后凭空消失, validate 立刻按默认
            # 上限 8 报错。author_pages/author_notes 同为人工编辑面
            # (layers init 白名单 / qoder-plan 摘要来源)。
            "limits": old.get("limits") or {},
            "author_pages": old.get("author_pages") or [],
            "author_notes": old.get("author_notes") or [],
        }
        if not plan["modules"]:
            die("plan 没有 modules — 先跑 `pwiki modules`", config.EXIT_PRECOND)
        assign_paths(plan, ctx.locale)
        save_plan(ctx, plan)
        info(f"plan 已{'重置' if has_plan else '种子'}: {ctx.plan_path}"
             f" ({len(plan['pages'])} pages, {len(plan['cards'])} cards)")
        return config.EXIT_OK

    # 已存在且未 --reset: 只做增量操作, 绝不静默覆盖。
    if plan is None:
        plan = load_plan(ctx)
    changed = False
    sheets_profile = getattr(args, "sheets", None)
    if sheets_profile:
        n = expand_sheets(plan, sheets_profile, ctx.locale, style)
        info(f"sheets: +{n} (profile {sheets_profile})")
        changed = changed or bool(n)
    if getattr(args, "deps", False):
        from .modules import seed_depends_on
        n = seed_depends_on(ctx, plan)
        info(f"depends_on: 播种 {n} 个模块")
        changed = changed or bool(n)
    if getattr(args, "aliases", None):
        # c086 家族: 输入文件缺失/坏格式 = 前置条件不满足(3) —— 曾裸
        # FileNotFoundError/ValueError traceback, 破坏退出码契约
        from .yamlite import yaml_load as _yl
        try:
            with open(args.aliases, encoding="utf-8") as fh:
                loaded = _yl(fh.read())
        except OSError as e:
            die(f"别名规则文件读取失败: {args.aliases}({e})",
                config.EXIT_PRECOND)
        except ValueError as e:
            die(f"别名规则文件不是合法 YAML: {args.aliases}({e})",
                config.EXIT_GATE)
        if not isinstance(loaded, list):
            die(f"别名规则文件顶层必须是列表: {args.aliases}"
                f"(got {type(loaded).__name__})", config.EXIT_GATE)
        plan["aliases"] = loaded
        info(f"aliases: 从 {args.aliases} 载入 {len(plan['aliases'])} 条")
        changed = True
    if getattr(args, "review", False):
        from . import themes
        themes.render_plan_md(ctx, plan)
        changed = False   # review 只写 work/, 不动 plan
    if changed:
        assign_paths(plan, ctx.locale)
        plan["schema_version"] = max(3, int(plan.get("schema_version") or 2))
        save_plan(ctx, plan)
        info(f"plan 已更新: {ctx.plan_path}")
    else:
        info(f"plan 已存在({ctx.plan_path}); 增量操作用 --sheets/--deps/--aliases,"
             " 重建用 --reset — 引擎不静默覆盖手工编辑")
    return config.EXIT_OK


def _require_inventory(ctx: Ctx) -> dict:
    from .scan import load_inventory
    inv = load_inventory(ctx)
    if not inv:
        from .modules import _autoscan
        inv = _autoscan(ctx)
    return inv


def cmd_validate(args) -> int:
    ctx = Ctx(args).load()
    if getattr(args, "content", False):
        from .gates import cmd_validate_content
        return cmd_validate_content(ctx, args)
    if getattr(args, "fix", False):
        info("--fix 只配合 --content(D18 章节来源/D23 差一钳位); "
             "plan 校验没有机械修复项")
    plan = load_plan(ctx)
    profile = ctx.config.get("profile", "standard")
    errors, warnings = validate_plan(ctx, plan, profile=profile,
                                     warn_missing=getattr(args, "warn_missing", False))
    for w in warnings:
        info(f"warn: {w}")
    if errors:
        lenient = getattr(args, "lenient", False)
        for e in errors:
            print(("warn: " if lenient else "error: ") + e)
        if lenient:
            # lenient 只是报告口径: begin 的门禁不变(不给人"放宽已生效"的错觉)
            print(f"validate(--lenient): {len(errors)} 错误降级显示 — "
                  "`begin` 仍会按错误挡住; 修 plan 而不是绕门禁")
            return config.EXIT_OK
        print(f"validate: {len(errors)} 错误, {len(warnings)} 警告 — "
              "`begin` 被挡住; 修 plan 而不是绕门禁")
        return config.EXIT_GATE
    print(f"validate: OK ({len(plan_pages(plan))} pages, "
          f"{len(plan_cards(plan))} cards, {len(plan_modules(plan))} modules, "
          f"{len(warnings)} 警告)")
    return config.EXIT_OK


# --------------------------------------------------------------------------- #
# Module Tree 渲染(Qoder wiki_plan_user_module_tree 视图; --layers 载荷用)
# --------------------------------------------------------------------------- #

def render_module_tree(plan: dict, parent_modules=None) -> str:
    """`<path>` — _描述_ [ [N sub-modules] ] 视图。parent_modules 限定到
    父域 + 回溯到 `/` 的祖先链。"""
    modules = plan_modules(plan)
    by_parent = {}
    for m in modules:
        by_parent.setdefault(m.get("parent") or "", []).append(m)
    desc = {m.get("id"): str(m.get("notes") or m.get("title") or "")
            for m in modules}
    kids = {m.get("id"): len(by_parent.get(m.get("id"), [])) for m in modules}
    if parent_modules is not None:
        wanted = {str(x).rstrip("/") for x in parent_modules}
        by_id = {m.get("id"): m for m in modules}

        def keep(m):
            p = str(m.get("path") or m["id"]).rstrip("/")
            return p in wanted or any(p.startswith(w + "/") for w in wanted)

        keep_ids = {m.get("id") for m in modules if keep(m)}
        for mid in list(keep_ids):
            cur = by_id.get(mid)
            guard = 0
            while cur and cur.get("parent") and guard < 12:
                keep_ids.add(cur["parent"])
                cur = by_id.get(cur["parent"])
                guard += 1
    else:
        keep_ids = {m.get("id") for m in modules}
    lines = []

    def is_skeleton(m):
        return bool(m.get("skeleton")) or str(m.get("module_type")) == "skeleton"

    def walk(mid, prefix):
        m = next((x for x in modules if x.get("id") == mid), None)
        if m is None or mid not in keep_ids:
            return
        path = str(m.get("path") or ("/" if mid == "root" else mid))
        hint = f" [{kids[mid]} sub-modules]" if kids.get(mid) else ""
        mark = " [skeleton]" if is_skeleton(m) else ""
        lines.append(f"{prefix}`{path}` — _{desc.get(mid, '')}_{hint}{mark}")
        walk_children(mid, prefix)

    def walk_children(mid, prefix):
        children = [c for c in by_parent.get(mid, []) if c.get("id") in keep_ids]
        for i, c in enumerate(children):
            is_last = i == len(children) - 1
            cpath = str(c.get("path") or c.get("id"))
            chint = f" [{kids.get(c.get('id'), 0)} sub-modules]" \
                if kids.get(c.get("id")) else ""
            cmark = " [skeleton]" if is_skeleton(c) else ""
            lines.append(f"{prefix}{'└── ' if is_last else '├── '}"
                         f"`{cpath}` — _{desc.get(c.get('id'), '')}_{chint}{cmark}")
            walk_children(c.get("id"), prefix + ("    " if is_last else "│   "))

    walk("root", "")
    return "\n".join(lines)
