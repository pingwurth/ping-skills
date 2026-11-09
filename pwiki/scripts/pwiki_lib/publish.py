"""发布层: DB → 文件树 + 知识层 + 索引 + Qoder 形元数据。

移植 qwiki _write_page_files/_write_indexes/compose_export_content(3487-3763)
+ repowiki render_artifacts 的 uuid5 ID/wiki_overview/wiki_readme/
generation-state/D19 守卫, 并修复两个 qwiki 缺陷:
  - bug18: finish 期相对链接规范化(writer 两类系统性错误 + 双重编码空格)
  - bug e: file_to_db 导入时剥掉注入的 <cite>/## 目录(qwiki 曾原样吞进 DB)

注入物只存在于导出文件, 永不写回 page.content(bug10 自毒防线)。
"""

from __future__ import annotations

import json
import os
import re
import sys
import uuid

from . import config, i18n
from .config import Ctx, now_iso
from .planner import module_dir_name
from .util import (as_int, atomic_write, CJK_RE, json_list, posix, die,
                   info, join_confined)
from .yamlite import yaml_dump

NAMESPACE = uuid.UUID("6ba7b811-9dad-11d1-80b4-00c04fd430c8")  # uuid.NAMESPACE_URL


def _uuid5(path: str, repo: str) -> str:
    return str(uuid.uuid5(NAMESPACE, f"repowiki:{repo}#{path}"))


def _md_anchor(text: str) -> str:
    t = str(text).strip().lower()
    t = re.sub(rf"[^\w{CJK_RE.pattern[1:-1]}\s-]", "", t)
    return re.sub(r"\s+", "-", t)


# --------------------------------------------------------------------------- #
# 链接规范化(qwiki bug18: writer 两类系统性相对链接错 + 双重编码空格)
# --------------------------------------------------------------------------- #

LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")


def normalize_links(content: str, page_path: str, all_paths: set) -> tuple:
    from . import gates   # _md_href/split_fences(P61/P66 同源)
    """把内部 .md 相对链接改写为由页面真实位置出发的正确相对路径。

    writer 的两类系统错误(ping 实跑):
      1. 漏掉一级父目录(在 子页 写 父兄弟.md 应为 ../父兄弟.md)
      2. 深度 2 处漏 ../ 前缀
    另外处理 %2520 双重编码空格(qwiki bug18 第三案)与 `页.md#锚点` 形式
    (锚点保留, 路径部分照常解析改写 —— 曾整类不改写)。返回 (content, n_fixed)。

    代码栅栏内不改写(示例链接是内容, 不是导航); 重建从捕获组拼装 ——
    曾用 label.replace 搜原始串, %2520 修复计数+1 而内容纹丝不动。"""
    page_dir = os.path.dirname(page_path)
    # 裸文件名兜底只在**全树唯一**时启用: 跨子树同名(架构/部署.md 与
    # 运维/部署.md 合法共存 —— 只有同父同名被 validate 拦)曾折叠成一条,
    # 断链被静默改写到错误子树的页, validate 还查不出来(目标确实存在)
    by_name: dict = {}
    for p in all_paths:
        if p.endswith(".md"):
            by_name.setdefault(os.path.basename(p)[:-3], []).append(p)
    fixed = 0

    def repl(m):
        nonlocal fixed
        text, raw = m.group(1), m.group(2)
        if raw.startswith(("file://", "http", "#", "mailto:")):
            return m.group(0)
        orig = raw
        if "%2520" in raw:
            raw = raw.replace("%2520", "%20")
        path_part, _, anchor = raw.partition("#")
        if not path_part.endswith(".md"):
            if raw != orig:
                fixed += 1
                return f"[{text}]({raw})"
            return m.group(0)
        # 解引用 writer 的(可能错的)相对路径, 找目标标题
        cand = posix(os.path.normpath(os.path.join(page_dir, path_part)))
        name = os.path.basename(path_part)[:-3]
        target = None
        if cand in all_paths:
            target = cand
        elif len(by_name.get(name, [])) == 1:
            target = by_name[name][0]
        if not target:
            return f"[{text}]({raw})" if raw != orig else m.group(0)
        rel = posix(os.path.relpath(target, page_dir or "."))
        # P66: 空格编码走 gates._md_href(与全部 file:// 引用链接同源; 两套
        # 编码器 + 一次性 %2520 修补是三个必须同步的漂移点)
        rel = gates._md_href(rel) + (f"#{anchor}" if anchor else "")
        if rel == orig and raw == orig:
            return m.group(0)
        fixed += 1
        return f"[{text}]({rel})"

    # 只在代码栅栏之外改写(P61: 共用切分器)
    parts = gates.split_fences(content)
    out = "".join(p if p.startswith("```") else LINK_RE.sub(repl, p)
                  for p in parts)
    return out, fixed


# --------------------------------------------------------------------------- #
# 导出内容合成(legacy 注入; DB 恒存原文)
# --------------------------------------------------------------------------- #

def export_injects(ctx: Ctx, style: str, plan: dict = None) -> bool:
    """导出层是否注入 <cite>/编号目录 —— 注入侧与剥除侧共用的唯一判定。

    条件: preset 的 export=='legacy'(concise)且生效 export_style=='legacy'
    (plan 级 > config 级; current = 作者自管注入物)。file_to_db 吸收手改
    文件与 validate --content 复检前的剥除必须同判 —— 曾两侧各判一半
    (compose 看两键、file_to_db 只看 preset), concise + export_style:
    current 下注入层从不注入, 作者手写的 cite/目录被当注入物永久剥除。"""
    from .styles import preset
    export_style = ((plan or {}).get("export_style")
                    or ctx.config.get("export_style", "legacy"))
    return preset(style)["export"] == "legacy" and export_style == "legacy"


def _toc_insert_pos(content: str):
    """编号目录的注入位: 正文里首个 `</cite>`(cite 块之后), 否则正文首个
    H1 —— 栅栏感知(P61 同族): 栅栏内的示例 `</cite>` / '# 注释' 不是注入
    位, 曾把编号目录注进示例代码里把栅栏劈开出厂。无正文命中返回 None。"""
    from . import gates as _g
    for pattern in (r"</cite>\s*", r"(?m)^# .*$"):
        offset = 0
        for seg in _g.split_fences(content):
            if not seg.startswith("```"):
                m = re.search(pattern, seg)
                if m:
                    return offset + m.end()
            offset += len(seg)
    return None


def compose_export_content(ctx: Ctx, p, style: str, all_paths: set,
                           plan: dict = None) -> tuple:
    """(content, link_fixes): 页面按风格注入 <cite>/目录 并规范化内链。

    链接规范化与 D23 差一钳位对**所有 kind** 生效(卡/sheet 同样有引用与
    内链; 曾对非 page 早退, 而 finish 又对该类错误全抑制 —— 坏锚点照发,
    同一引用在页面却被钳位)。注入物(<cite>/目录)仍仅限 page。
    concise/legacy: 注入 cite 块 + 编号目录(Qoder 导出口径)。
    deep: 原文照发(writer 自带 cite 块)。
    export_style 优先级: plan 级 `export_style`(人工编辑面) > config.json。"""
    from . import gates
    content = p["content"]
    content, n_links = normalize_links(content, p["path"], all_paths)
    # D23 差一钳位落在发布层(lining 对 citation/off-by-one 静默, 由这里收口;
    # 只改导出文件, DB 仍存 writer 原稿)
    content, n_clamp = gates.fix_off_by_one(content, ctx.repo)
    n_links += n_clamp
    if p["kind"] != "page":
        return content, n_links
    if not export_injects(ctx, style, plan):
        # 判定收敛到 export_injects(注入侧与剥除侧共用)—— 曾 compose 看
        # preset+export_style 两键而 file_to_db 只看 preset, current 模式
        # 下作者手写的 cite 块被导回时整块剥掉
        return content, n_links
    content = gates.rebuild_cite_block(content, ctx.repo, ctx.locale)
    _toc_titles = set(i18n.TOC_TITLE.values()) | {"Table of Contents"}
    # 目录标题栅栏感知(iter_h2): 示例代码里的 '## 安装' 不是节 —— 曾生出
    # 指进栅栏的幻影目录锚点
    h2 = [m.group(1).strip() for m in gates.iter_h2(content)
          if m.group(1).strip() not in _toc_titles]
    if len(h2) >= 3:
        toc = ["## " + i18n.toc_title(ctx.locale)] + \
            [f"{i}. [{h}](#{_md_anchor(h)})" for i, h in enumerate(h2, 1)]
        # 旧目录节的剥除同样栅栏感知: 裸 TOC_SECTION_RE 在未剥栅栏的原文
        # 上跑, 示例代码里的 '## 目录' 连同栅栏闭合符到下一个真实 '## '
        # 整段被删 —— 发布文件带着未闭合栅栏出厂, 且 DB 存原文, 每次
        # finish 确定性重复损毁
        content = "".join(
            seg if seg.startswith("```") else i18n.TOC_SECTION_RE.sub("", seg)
            for seg in gates.split_fences(content))
        # 手写 '## 目录' 节内自带的 <cite>(concise 下仅警告, 合法)会随节
        # 一起被剥掉 —— 再补一次重建保证注入点存在(常态下 cite 未随节
        # 走, 这里是幂等 no-op)
        content = gates.rebuild_cite_block(content, ctx.repo, ctx.locale)
        pos = _toc_insert_pos(content)
        if pos is not None:
            content = (content[:pos].rstrip("\n") + "\n\n" + "\n".join(toc)
                       + "\n\n" + content[pos:].lstrip("\n"))
    return content, n_links


def _strip_injected(content: str) -> str:
    """file_to_db 导入前剥掉导出层注入物(qwiki bug e 修复)。
    剥除正则/范围与注入侧共用一份(gates.strip_injected_layers + i18n)。"""
    from .gates import strip_injected_layers
    return strip_injected_layers(content).strip() + "\n"


# --------------------------------------------------------------------------- #
# 发布主流程
# --------------------------------------------------------------------------- #

def _d19_guard(ctx: Ctx, yes: bool) -> None:
    """D19: 可疑的新根(一次要造 >1 级缺失目录)停下来确认 —— `clalude`
    typo 实跑曾凭空调出一棵 wiki 树。合法新根的目录骨架由 init/begin/
    update 落位(Ctx.ensure_publish_tree, P1): 全新 workspace 的首次 finish
    不再需要 --yes; 守卫只对骨架之外的多级缺失(根漂移/手删目录/typo)
    触发, 确认用 --yes。"""
    for sub in (os.path.join(ctx.locale, "content"), os.path.join("knowledge", ctx.locale)):
        target = os.path.join(ctx.root, sub)
        if os.path.exists(target):
            continue
        probe = target
        missing = 0
        while not os.path.exists(probe):
            missing += 1
            probe = os.path.dirname(probe)
        if missing > 1 and not yes:
            die(f"新发布根将创建 {missing} 级缺失目录({target}) —— 像路径 "
                "typo(D19); 确认无误加 --yes", config.EXIT_PRECOND)


def publish_run(ctx: Ctx, run, plan: dict, style: str, prune=False,
                yes=False, jobs=None) -> list:
    """发布: completed jobs + 计划内未动的已发布项(kept 规则), 落文件树。

    kept 语义(qwiki): 已发布 ∩ 计划 − 本次 run 重跑**完成**的项 → 原样重
    发布; 重跑失败/跳过的项保留上一版好稿(重写失败绝不连坐删除已发布内容);
    树位置在计划里变了的项(catalog 漂移, 含卡片改名)按新 plan 重算路径 ——
    零 LLM 移动。prune=False 只清理本引擎自己腾出的旧路径(移动/下架);
    True 才全量清扫计划外孤儿 .md(用户手放的笔记默认保留)。
    jobs= 调用方已读的整队行直传(finish 曾数秒内 SELECT * 整队读两遍)。
    返回 (needs_review, pages): needs_review=落盘失败条目; pages=发布后
    的全量行(调用方 finish 的计数/快照直接复用, 不再各自重查)。"""
    from .planner import (card_path_for, page_path_for, plan_cards,
                          plan_modules, plan_pages)
    part = ctx.partition()
    pid = part["id"]
    _d19_guard(ctx, yes)
    jobs = list(jobs) if jobs is not None else ctx.db.jobs(run["id"])

    pages_plan = plan_pages(plan)
    cards_plan = plan_cards(plan)
    modules_plan = plan_modules(plan)
    plan_ids = {str(p.get("id")) for p in pages_plan} | \
        {str(c.get("id")) for c in cards_plan}
    plan_by_id = {str(p.get("id")): p for p in pages_plan}
    # 卡片也进 plan_by_id: kept 重发布要按 plan 重算卡片路径(改名才能落地)
    plan_by_id.update({str(c.get("id")): c for c in cards_plan})
    mods_by_id = {m.get("id"): m for m in modules_plan}
    completed_ids = {j["page_id"] for j in jobs if j["state"] == "completed"}

    # 发布前已发布行只取一次(old_paths + kept 循环共用; 曾各自全表
    # SELECT *, 一次 finish 白拖整库稿体 5 遍)
    existing = list(ctx.db.published_pages(pid))
    # 旧路径集合(发布前) —— 默认只清理这些里不再保留的文件
    old_paths = {os.path.normpath(os.path.join(ctx.root, p["path"]))
                 for p in existing if p["path"]}

    kept_ids = []
    # F36: 两个重发布循环 + 下架 + 模块同步包进单个 BEGIN IMMEDIATE ——
    # 旧路在 autocommit 连接上每行一条 INSERT OR REPLACE, 稳态 finish(只
    # 触及几页)也对全库各行各发一个 fsync 写事务, 并发 next/submit 的写锁
    # 竞争成倍放大; 循环不触及 leases/claim token, 包事务零语义风险。
    # 未变行(落库列逐列相等)跳写 —— 与文件树侧"先比后写"的 r4 #72 修复
    # 同口径对齐。
    existing_by_id = {p["page_id"]: p for p in existing}
    ctx.db.begin()
    try:
        # 1) kept: 计划内、本次 run 未重跑完成的已发布项(目录漂移项重算路径)
        for p in existing:
            if p["page_id"] in completed_ids or p["page_id"] not in plan_ids:
                continue
            item = plan_by_id.get(p["page_id"])
            row = dict(p)
            if item is not None:
                merged = dict(item)
                merged["kind"] = item.get("kind", p["kind"])
                if config.is_card_kind(p["kind"]) or \
                        config.is_card_kind(merged.get("kind")):
                    row["path"] = posix(card_path_for(merged, ctx.locale))
                else:
                    row["path"] = posix(page_path_for(pages_plan, merged,
                                                      ctx.locale, mods_by_id))
                # kept 合并曾只搬 title/parent/ordinal/path —— plan 改了
                # type/slug/module/kind 而 DB 行保持旧值: snapshot.check_item
                # 按 type/slug 对账, update 每轮重标 catalog_changed 却每轮
                # 合并不进去, 0-job INCREMENT 死循环。目录字段随 plan 全量
                # 同步(kind 归一到行形态, 与 queue_jobs 同规)
                if config.is_card_kind(merged.get("kind")):
                    row["kind"] = "card"
                    row["card_kind"] = str(merged.get("card_kind")
                                           or merged.get("kind"))
                else:
                    row["kind"] = str(merged.get("kind") or "page")
                    row["card_kind"] = str(merged.get("card_kind") or "")
                row["parent_id"] = str(item.get("parent", p["parent_id"]))
                # 显式 `ordinal:` 空值(None)会击穿 .get 默认 —— begin 的
                # ordinal 守卫只在排队前跑, finish 重读手编 plan 后曾
                # int(None) 裸崩在 LINING 中途; as_int 是本 crash 类的统一
                # 容错(同族: lease_minutes/limbs 解码)
                row["ordinal"] = as_int(item.get("ordinal"), p["ordinal"])
                row["title"] = str(item.get("title", p["title"]))
                row["module"] = str(item.get("module") or "")
                row["sheet"] = str(item.get("sheet") or "")
                row["article_type"] = str(item.get("type")
                                          or item.get("article_type") or "")
                row["slug"] = str(item.get("slug") or "")
                # 对账字段全量同步(snapshot.check_item 比较的 9 字段一个
                # 不缺): objective/modules/required_sections/pack_key 曾
                # "比而不合" —— update 标 catalog_changed 排 0-job
                # INCREMENT, finish 的 kept 合并不写这四个字段, page 行保持
                # 旧值, 下轮 update 又 catalog_changed, 手编永不出循环
                row["objective"] = str(merged.get("objective") or "")
                row["modules"] = json.dumps(merged.get("modules") or [],
                                            ensure_ascii=False)
                row["required_sections"] = json.dumps(
                    merged.get("required_sections") or [], ensure_ascii=False)
                row["pack_key"] = str(merged.get("pack_key") or "")
            new_row = _row_like(row)
            if _republish_changed(p, new_row, p["content"]):
                ctx.db.publish_page(pid, new_row, p["content"], run["id"])
            kept_ids.append(p["page_id"])
        # 2) completed jobs → page 表
        for j in jobs:
            if j["state"] != "completed":
                continue
            old = existing_by_id.get(j["page_id"])
            if old is None or _republish_changed(old, j, j["content"]):
                ctx.db.publish_page(pid, j, j["content"], run["id"])
            kept_ids.append(j["page_id"])
        ctx.db.unpublish_pages(pid, kept_ids)
        ctx.db.publish_modules(pid, modules_plan)
        ctx.db.commit()
    except BaseException:
        ctx.db.rollback()
        raise

    modules = ctx.db.modules(pid)
    pages = list(ctx.db.published_pages(pid))
    # 同路径碰撞最后一道闸: 行路径汇合(手编 plan 未走 assign_paths 拦截 /
    # 卡片改名与已有页同名)时, 下方逐行写文件是后写静默覆盖先写 —— 宁可
    # 中止发布也不静默丢一页
    seen_path: dict = {}
    for p in pages:
        key = str(p["path"] or "")
        if not key:
            continue
        if key in seen_path and seen_path[key] != p["page_id"]:
            die(f"发布中止: 页 {seen_path[key]!r} 与 {p['page_id']!r} 同路径"
                f" {key!r} — 后写会静默覆盖先写, 改其一标题后再 finish",
                config.EXIT_GATE)
        seen_path[key] = p["page_id"]
    all_paths = {p["path"] for p in pages}

    written = link_fixes = 0
    # P71: 合成短路缓存 —— 合成输入(page_id/内容哈希/风格/路径/全路径集
    # 签名/commit/文件 mtime)与上次落盘完全一致时, 跳过 normalize+钳位+
    # 引用块重建的整段工作(每次 finish 曾对全部已发布页重组, 3 页增量也
    # 白付 ~132 页的正则与 stat)。任一输入变化(key 不匹配)即回退全量合成,
    # 引用钳位随仓库演化的自愈语义不变; 缓存本身走原子写。
    import hashlib as _hl
    _paths_sig = _hl.sha256(
        "\n".join(sorted(all_paths)).encode("utf-8")).hexdigest()[:16]
    _cache_path = os.path.join(ctx.root, "work", "compose-cache.json")
    try:
        with open(_cache_path, encoding="utf-8") as _fh:
            _cache = json.load(_fh)
        if not isinstance(_cache, dict):
            _cache = {}
    except (OSError, ValueError):
        _cache = {}
    _new_cache: dict = {}
    for p in pages:
        target = os.path.join(ctx.root, p["path"])
        # 合成按行自己的盖章风格(p['style'] > 调用方兜底)—— kept 行/旧 run
        # 行带着写入时的盖章, finish 曾按统一的当前风格合成, 换风格后旧页
        # 的注入层口径被翻转(行是 sqlite3.Row, 取键不用 .get)
        row_style = str(p["style"] or style)
        _key = [str(p["page_id"]), str(p["content_hash"] or ""), row_style,
                str(p["path"] or ""), _paths_sig, str(ctx.commit or "")]
        try:
            _st = os.stat(target)
            _ent = _cache.get(str(p["page_id"]))
            if isinstance(_ent, dict) and _ent.get("key") == _key \
                    and int(_st.st_mtime * 1000) == _ent.get("mtime"):
                _new_cache[str(p["page_id"])] = _ent
                continue
        except OSError:
            pass
        content, n = compose_export_content(ctx, p, row_style, all_paths,
                                            plan=plan)
        link_fixes += n
        # 先比后写(r4 #72): 3 页 INCREMENT 也曾对全树 ~180 份未变文件各做
        # 一遍 tmp+fsync+rename(每次 finish 数秒 + 写放大); 未变不落盘
        try:
            with open(target, encoding="utf-8") as fh:
                if fh.read() == content:
                    try:
                        _new_cache[str(p["page_id"])] = {
                            "key": _key,
                            "mtime": int(os.stat(target).st_mtime * 1000)}
                    except OSError:
                        pass
                    continue
        except (OSError, UnicodeDecodeError):
            pass
        atomic_write(target, content)
        written += 1
        try:
            _new_cache[str(p["page_id"])] = {
                "key": _key, "mtime": int(os.stat(target).st_mtime * 1000)}
        except OSError:
            pass
    atomic_write(_cache_path, json.dumps(_new_cache, ensure_ascii=False))
    keep_files = {os.path.normpath(os.path.join(ctx.root, p["path"]))
                  for p in pages if p["path"]}
    pruned = swept = 0
    # 默认: 只删本引擎自己腾出的旧路径(移动/下架), 计划外手放文件不动
    for fp in sorted(old_paths - keep_files):
        if os.path.isfile(fp):
            os.unlink(fp)
            pruned += 1
    if prune:
        for base in (ctx.content_dir(), ctx.knowledge_dir()):
            if not os.path.isdir(base):
                continue
            for dirpath, _dirnames, filenames in os.walk(base):
                for fn in filenames:
                    if not fn.endswith(".md"):
                        continue   # 索引文件单独重写
                    fp = os.path.normpath(os.path.join(dirpath, fn))
                    if fp not in keep_files:
                        os.unlink(fp)
                        swept += 1
    for base in (ctx.content_dir(), ctx.knowledge_dir()):
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, _fns in os.walk(base, topdown=False):
            for dn in dirnames:
                try:
                    os.rmdir(os.path.join(dirpath, dn))
                except OSError:
                    pass
    info(f"publish: {written} files ({pruned} moved-out pruned"
         + (f", {swept} orphans swept (--prune)" if prune else "")
         + f", {link_fixes} links normalized)")
    _write_indexes(ctx, part, pages, modules, run["id"], style)
    _write_generation_state(ctx, part, run, pages)
    needs_review = [p["page_id"] for p in pages if p["path"]
                    and not os.path.isfile(os.path.join(ctx.root, p["path"]))]
    return needs_review, pages


def _row_like(d: dict):
    """publish_page 读取 job 行的键; 从 page 行拷贝时补齐缺省。"""
    base = {
        "page_id": d.get("page_id"), "title": d.get("title"),
        "parent_id": d.get("parent_id", ""), "ordinal": d.get("ordinal", 0),
        "kind": d.get("kind", "page"), "module": d.get("module", ""),
        "sheet": d.get("sheet", ""), "card_kind": d.get("card_kind", ""),
        "path": d.get("path", ""), "prompt": d.get("prompt", ""),
        "source_files": d.get("source_files", "[]"),
        "article_type": d.get("article_type", ""), "slug": d.get("slug", ""),
        "objective": d.get("objective", ""), "modules": d.get("modules", "[]"),
        "summary": d.get("summary", ""), "key_topics": d.get("key_topics", "[]"),
        "style": d.get("style", ""), "scope": d.get("scope", "[]"),
        "required_sections": d.get("required_sections", "[]"),
        "title_check": d.get("title_check", 0), "pack_key": d.get("pack_key", ""),
    }
    return base


# F36: publish_page 落库列中参与"未变跳写"比对的键 —— run_id/updated_at
# 是过程性簿记(全库零读者), content_hash 由 content 推出, 均不参与比对
_REPUBLISH_CMP_COLS = ("title", "parent_id", "ordinal", "kind", "module",
                       "sheet", "card_kind", "path", "prompt", "source_files",
                       "article_type", "slug", "objective", "modules",
                       "summary", "key_topics", "style", "scope",
                       "required_sections", "title_check", "pack_key")


def _republish_changed(old_row, new_row, new_content: str) -> bool:
    """F36: 重发布行是否真的变化(未变行跳写, 与文件树侧"先比后写"对齐)。

    逐列比对 publish_page 会落库的元数据列 + 稿体; 任一不等即视为变化。
    比对从严: 类型/None 差异一律算变(多写一次无害, 漏写才是回归)。"""
    if new_content != (old_row["content"] or ""):
        return True
    for c in _REPUBLISH_CMP_COLS:
        try:
            old_v, new_v = old_row[c], new_row[c]
        except (KeyError, IndexError):
            return True     # 行形状异常 —— 保守写库
        if old_v != new_v:
            return True
    return False


def _write_indexes(ctx: Ctx, part, pages: list, modules: list, run_id: str,
                   style: str) -> None:
    from .planner import render_module_tree
    os.makedirs(ctx.meta_dir(), exist_ok=True)
    os.makedirs(ctx.content_dir(), exist_ok=True)
    os.makedirs(ctx.knowledge_dir(), exist_ok=True)

    # 台账 id 空间 = slug||page_id; WIKI_ITEM 边两端都必须换算到该空间
    # (曾页↔页边用 page_id、目录边用 slug, slug≠id 的子树导入即失联)
    cid_of = {p["page_id"]: (p["slug"] or p["page_id"])
              for p in pages if p["kind"] == "page"}
    relations = []
    for p in pages:
        # PARENT_CHILD 边只在下方 cat_relations 一处发(两个循环同条件各发
        # 一份, 合并后每条边在 repowiki-metadata 里逐字出现两次)
        if p["module"]:
            dst = cid_of.get(p["page_id"], p["page_id"])
            relations.append({
                "source_id": p["module"], "target_id": dst,
                "source_type": "MODULE", "target_type": "WIKI_ITEM",
                "relationship_type": "MODULE",
                "extra": f"Module membership: {p['module']} -> {dst}"})
    metadata = {
        "schema_version": 1,
        "generator": {"name": config.SKILL_NAME, "version": config.VERSION},
        "wiki_repo": {"last_commit_id": ctx.commit, "branch": part["branch"],
                      "locale": part["locale"]},
        "run_id": run_id,
        "generated_at": now_iso(),
        "items": [{"id": p["page_id"], "title": p["title"], "kind": p["kind"],
                   "parent_id": p["parent_id"], "ordinal": p["ordinal"],
                   "module": p["module"], "sheet": p["sheet"],
                   "card_kind": p["card_kind"], "path": p["path"],
                   "article_type": p["article_type"] or "page",
                   "slug": p["slug"] or p["page_id"],
                   "objective": p["objective"],
                   "modules": json_list(p["modules"]),
                   "summary": p["summary"],
                   "key_topics": json_list(p["key_topics"]),
                   "source_files": json_list(p["source_files"])}
                  for p in pages],
        "modules": [{"id": m.get("module_id"), "title": m.get("title"),
                     "dir": m.get("dir"), "path": m.get("path", ""),
                     "parent": m.get("parent_id", ""),
                     "scope": json_list(m.get("scope")),
                     "depends_on": json_list(m.get("depends_on")),
                     "related_to": json_list(m.get("related_to"))}
                    for m in modules],
        "knowledge_relations": relations,
    }
    # Qoder 可导入的目录台账(wiki_catalogs/wiki_items 1:1 + PARENT_CHILD 边)
    tree_pub = [p for p in pages if p["kind"] == "page"]
    catalogs, items, cat_relations = [], [], []
    by_page = {q["page_id"]: q for q in tree_pub}
    for i, p in enumerate(tree_pub):
        cid = p["slug"] or p["page_id"]
        depth, cur, guard = 0, p["parent_id"], 0
        while cur and guard < 12:
            depth += 1
            row = by_page.get(cur)
            cur = row["parent_id"] if row is not None else ""
            guard += 1
        catalogs.append({
            "id": cid, "repo_id": "", "name": p["title"],
            "description": cid, "prompt": p["prompt"],
            # parent 也换算到 slug 空间 —— 曾存原始 page_id, slug≠id 的
            # 子树在 Qoder 导入时找不到父目录, 整枝失联(cid_of 的初衷)
            "parent_id": cid_of.get(p["parent_id"], p["parent_id"]),
            "layer_level": depth,
            "order": i,
            "progress_status": "completed",
            "dependent_files": ",".join(
                json_list(p["source_files"])[:7]),
            "article_type": p["article_type"], "objective": p["objective"],
            "modules": json_list(p["modules"]),
        })
        items.append({"catalog_id": cid, "title": p["title"],
                      "description": cid, "extend": {},
                      "progress_status": "completed", "reference_count": 0,
                      "id": f"{cid}-item"})
        if p["parent_id"]:
            cat_relations.append({
                "source_id": cid_of.get(p["parent_id"], p["parent_id"]),
                "target_id": cid,
                "source_type": "WIKI_ITEM", "target_type": "WIKI_ITEM",
                "relationship_type": "PARENT_CHILD",
                "extra": f"Wiki parent-child relationship: "
                         f"{cid_of.get(p['parent_id'], p['parent_id'])} -> {cid}"})
    metadata["wiki_catalogs"] = catalogs
    metadata["wiki_items"] = items
    metadata["knowledge_relations"] = relations + cat_relations
    # repowiki 形态补充: uuid5 稳定 ID + wiki_overview + wiki_readme
    metadata["page_ids"] = {p["page_id"]: _uuid5(p["path"], ctx.repo)
                            for p in pages}
    ov = next((p for p in pages
               if p["kind"] == "page" and p["article_type"] == "overview"),
              None)
    if ov is not None:
        metadata["wiki_overview"] = {
            "content": "<blog>\n" + ov["content"] + "\n</blog>"}
    readme = os.path.join(ctx.repo, "README.md")
    if os.path.isfile(readme):
        try:
            with open(readme, encoding="utf-8", errors="ignore") as fh:
                metadata["wiki_readme"] = {"content": fh.read(200_000)}
        except OSError:
            pass
    try:
        metadata["wiki_repo"]["optimized_catalog"] = render_module_tree({
            "modules": [{"id": m.get("module_id"), "title": m.get("title"),
                         "dir": m.get("dir"), "path": m.get("path"),
                         "parent": m.get("parent_id"),
                         "notes": m.get("notes") or m.get("title")}
                        for m in modules]})
    except Exception as e:   # noqa: BLE001 —— 元数据尽力而为, 不挡发布
        info(f"note: optimized_catalog 渲染失败({e})")
    atomic_write(os.path.join(ctx.meta_dir(), "repowiki-metadata.json"),
                 json.dumps(metadata, ensure_ascii=False, indent=2))

    # content 索引
    index = {
        "schema_version": 1, "locale": part["locale"],
        "branch": part["branch"], "exported_at": now_iso(),
        "pages": [{"id": p["page_id"], "title": p["title"],
                   "parent": p["parent_id"], "ordinal": p["ordinal"],
                   "path": p["path"]}
                  for p in pages if p["kind"] == "page"],
    }
    atomic_write(os.path.join(ctx.content_dir(), "_index.yaml"),
                 "# repo wiki 目录索引(pwiki 生成)\n" + yaml_dump(index))

    # 知识层索引(模块树; root 键 "/")—— 模块身份键与树路径键单一推导
    # (三处各写一份且键形不同时, _index.yaml 记 'auth' 而 _module.yaml 记
    # 'auth/', Qoder 的两个注册表看到两个节点)
    cards = [p for p in pages if config.is_card_kind(p["kind"])]
    sheets = [p for p in pages if p["kind"] == "module_sheet"]

    def module_path_of(m) -> str:
        return "/" if m.get("module_id") == "root" \
            else (m.get("path") or m.get("dir") or m.get("module_id") or "")

    # P49: 索引 key 与 module_path 字段同源(曾两套派生, 手编计划空 path
    # + dir 时同一节点 key/module_path 互不一致)
    id_path = {m.get("module_id"): module_path_of(m) for m in modules}
    children = {}
    for m in modules:
        pid = m.get("parent_id") or ""
        children.setdefault(id_path.get(pid, pid), []).append(
            id_path.get(m.get("module_id"), ""))
    kindex = {
        "schema_version": 1, "locale": part["locale"],
        "branch": part["branch"], "exported_at": now_iso(),
        "nodes_managed": True,
        "modules": {
            module_path_of(m): {
                "module_path": module_path_of(m),
                "dir_name": m.get("title", ""),
                "title": m.get("title", ""),
                "scope": json_list(m.get("scope")),
                "source_files": [],
                "children": sorted(children.get(module_path_of(m), [])),
                "depends_on": json_list(m.get("depends_on")),
                "related_to": json_list(m.get("related_to")),
            } for m in modules},
        "cards": [{"id": c["page_id"], "kind": c["card_kind"],
                   "title": c["title"], "path": c["path"]} for c in cards],
        "module_sheets": [{"module": s["module"], "sheet": s["sheet"],
                           "path": s["path"]} for s in sheets],
    }
    atomic_write(os.path.join(ctx.knowledge_dir(), "_index.yaml"),
                 "# 知识层索引(pwiki 生成: 模块注册表 + 速查表 + 知识卡)\n"
                 + yaml_dump(kindex))
    # D5: 每 sheet 模块目录一份 _module.yaml —— 它是 Qoder 侧的导出元数据
    # (synchronizeWiki 吸收用); 本引擎的 validate 一律按 plan(kind 字段)
    # 区分 sheet 与卡, 不读此文件(c031: 原注释宣称 validate 靠它, 不实)
    mods_by_id = {m.get("module_id"): m for m in modules}
    for s in {s["module"] for s in sheets}:
        m = mods_by_id.get(s)
        if not m:
            continue
        mdir = os.path.join(ctx.knowledge_dir(),
                            module_dir_name(m, mods_by_id))
        os.makedirs(mdir, exist_ok=True)
        my = {
            "schema_version": 1,
            "module_path": module_path_of(m),
            "title": m.get("title", ""),
            "scope": json_list(m.get("scope")),
            "source_files": [],
            "depends_on": json_list(m.get("depends_on")),
            "related_to": json_list(m.get("related_to")),
        }
        atomic_write(os.path.join(mdir, "_module.yaml"), yaml_dump(my))
    # 剪孤儿 _module.yaml(模块不再在注册表 —— ping 实跑标题碰撞案的修复)
    keep_dirs = {module_dir_name(m, mods_by_id) for m in modules}
    for dp, _dn, fns in os.walk(ctx.knowledge_dir(), topdown=False):
        if "_module.yaml" in fns and os.path.basename(dp) not in keep_dirs:
            os.unlink(os.path.join(dp, "_module.yaml"))
        try:
            os.rmdir(dp)
        except OSError:
            pass


def _write_generation_state(ctx: Ctx, part, run, pages: list) -> None:
    state = {
        "schema_version": 2,
        "repo": ctx.repo,
        "commit": ctx.commit,
        "generated_at": now_iso(),
        "locale": part["locale"],
        "run_id": run["id"],
        "pages": {p["page_id"]: {"path": p["path"],
                                 "source_hashes": _source_hashes(ctx, p)}
                  for p in pages},
    }
    atomic_write(os.path.join(ctx.meta_dir(), "generation-state.json"),
                 json.dumps(state, ensure_ascii=False, indent=2))


def _source_hashes(ctx: Ctx, p) -> dict:
    out = {}
    # json_list 是该列解码的单一实现(本模块 :384/:417 同款)—— 裸
    # json.loads 对真值 'null' 返回 None, 'for s in None' 打死 finish 的
    # generation-state 写; 现行写库已归一, 此处是旧库/手改 DB 的兜底
    for s in json_list(p["source_files"]):
        # c049: 仓外路径不哈希(导出文件不得携带宿主文件指纹)
        full = join_confined(ctx.repo, str(s).rstrip("/"))
        if full and os.path.isfile(full):
            # ctx 级共享缓存: 同一源文件喂多页 + snapshot_run 复用同一份哈希
            sha = ctx.file_sha(full)
            if sha:
                out[str(s)] = sha
    return out


# --------------------------------------------------------------------------- #
# wiki 工具面: pages / read / write / delete / export
# --------------------------------------------------------------------------- #

def cmd_pages(args) -> int:
    ctx = Ctx(args).load(create=False)   # P44: 只读不物化工作区
    pages = ctx.db.published_pages(ctx.partition()["id"])
    if args.json:
        print(json.dumps([{"id": p["page_id"], "title": p["title"],
                           "kind": p["kind"], "parent": p["parent_id"],
                           "path": p["path"]} for p in pages],
                          ensure_ascii=False, indent=2))
        return config.EXIT_OK
    if args.tree:
        by_parent = {}
        for p in pages:
            by_parent.setdefault(p["parent_id"], []).append(p)

        def emit(parent, depth=0):
            for p in sorted(by_parent.get(parent, []),
                            key=lambda x: (x["ordinal"], x["page_id"])):
                kind = {"page": "", "module_sheet": " [速查表]",
                        "card": " [卡]"}[p["kind"]]
                print("  " * depth + f"- {p['title']}{kind} · {p['page_id']}")
                emit(p["page_id"], depth + 1)
        emit("")
        return config.EXIT_OK
    for p in pages:
        print(f"{p['page_id']}\t{p['kind']}\t{p['path']}")
    return config.EXIT_OK


def cmd_read(args) -> int:
    ctx = Ctx(args).load(create=False)   # P44: 只读不物化工作区
    p = ctx.db.published_page(ctx.partition()["id"], args.page_id)
    if not p:
        die(f"未发布条目: {args.page_id}", config.EXIT_PRECOND)
    if args.meta:
        print(json.dumps({k: p[k] for k in p.keys()
                          if k not in ("content",)},
                         ensure_ascii=False, indent=2, default=str))
        return config.EXIT_OK
    print(p["content"], end="")
    return config.EXIT_OK


def cmd_write(args) -> int:
    """edit_wiki: 直接发布修正(无 run); 同门禁; 刷新哈希基线(修 qwiki 缺陷 g)。"""
    from . import gates
    from .snapshot import refresh_baseline_for
    ctx = Ctx(args).load()
    pid = ctx.partition()["id"]
    # c039: write 的基线刷新是读-合-写 —— 与 finish/update 并发会互相覆盖
    # 哈希行; 与 update 的活动 run 守卫同款, write 也要求无活动 run
    if ctx.db.active_run(pid):
        die("存在活动 run — 先 finish/cancel 再手工 write(避免基线互相覆盖)",
            config.EXIT_PRECOND)
    p = ctx.db.published_page(pid, args.page_id)
    if not p:
        die(f"未发布条目: {args.page_id}", config.EXIT_PRECOND)
    # 读稿本体 = util.read_submission(submit/write 共用, r4 #60 收敛)
    from .util import read_submission
    content = read_submission(args.file).strip() + "\n"
    # 门禁/合成风格取当前 plan(与 validate --content / update / export 同
    # 口径 —— 行盖章只反映写入时的口径; plan 换风格后手工修正按新口径
    # 验收, 否则 write 放行的内容被自家 validator 拒), 并把行盖章刷新成
    # 生效值, 后续 finish 的逐行合成不再翻转
    from .planner import load_plan_optional, mod_titles_for, plan_modules
    plan = load_plan_optional(ctx)   # P60: 单一实现(坏计划留 warn)
    style = str((plan or {}).get("style") or p["style"] or ctx.style)
    # P32: mod_titles 与 submit/finish 同源 —— strict 升级层不再缺输入
    mod_titles = mod_titles_for(p, plan_modules(plan)) if plan else []
    errs, warns = gates.gate_item(content, p, ctx.repo, style,
                                  phase="lining", mod_titles=mod_titles)
    for w in warns:
        info(f"warn: {w}")
    # 差一引用是机械可修(compose 落盘时钳位) —— 与 finish 同口径排除,
    # 否则同一份稿 finish 能过而手工 write 被拒
    errs = [e for e in errs if not gates.is_auto_fixable(e)]
    if args.lenient and not args.force:
        # lenient 与 submit/finish 同一契约(只放宽 H1/无引用), 曾当
        # --force 全放 —— 引用真实性硬错也被手工发布
        errs = [e for e in errs if not gates.is_lenient_droppable(e)]
    if errs and not args.force:
        for e in errs:
            print(f"  rejected: {e}", file=sys.stderr)
        return config.EXIT_GATE
    # P65: 保存与重盖章同事务(两句裸自动提交之间中断会留"新内容+旧样式
    # 戳", 下次 finish 按陈旧风格组合该页); UPDATE 走 db.set_page_style
    # 单一入口(与 snapshot 的重盖章同源)
    ctx.db.begin()
    try:
        ctx.db.save_page_content(pid, args.page_id, content)
        if str(p["style"] or "") != style:
            ctx.db.set_page_style(pid, args.page_id, style)
        ctx.db.commit()
    except Exception:
        ctx.db.rollback()
        raise
    target = os.path.join(ctx.root, p["path"])
    os.makedirs(os.path.dirname(target), exist_ok=True)
    all_paths = ctx.db.published_paths(pid)
    exported, _ = compose_export_content(ctx, {**dict(p), "content": content},
                                         style, all_paths, plan=plan)
    atomic_write(target, exported)
    refresh_baseline_for(ctx, [args.page_id])   # 修 qwiki: write 后基线不再陈旧
    ctx.db.log("manual", "manual_edit", args.page_id)
    print(f"written {args.page_id} -> {p['path']} (baseline refreshed)")
    return config.EXIT_OK


def cmd_delete(args) -> int:
    ctx = Ctx(args).load()
    pid = ctx.partition()["id"]
    # P12: 与 write(c039)/export file_to_db(c028)同门 —— 活跃 run 的 finish
    # 会用旧 job 行把删掉的页整页复活(重插行+重写文件); run 期间的
    # delete --all 更是当场清光已发布行, 留下 DB/计划/树三方不一致
    if ctx.db.active_run(pid):
        die("存在活动 run — 先 finish/cancel 再 delete(避免发布回滚删除)",
            config.EXIT_PRECOND)
    if args.all:
        # 先清文件树再清 DB: 曾先提交 unpublish 再裸 os.unlink —— 一个
        # EBUSY/EACCES 文件让 DB 宣称"已全删"而树上挂着半堆 + 裸 traceback
        import shutil
        leftover = []
        # P26: meta/ 一并清 —— 单条目路径会重写 repowiki-metadata.json /
        # generation-state.json, --all 曾留着全量幽灵条目(Qoder 导入面读的
        # 正是这些索引)
        for base in (ctx.content_dir(), ctx.knowledge_dir(), ctx.meta_dir()):
            if os.path.isdir(base):
                shutil.rmtree(base, ignore_errors=True)
                if os.path.isdir(base):
                    leftover.append(base)
        ctx.db.unpublish_pages(pid, [])
        ctx.db.log("manual", "delete_index", "all")
        if leftover:
            info("note: 部分目录未能删除(文件被占用?) — " + ", ".join(leftover))
        print("deleted: 全部已发布条目与文件树(deleteIndex)")
        return config.EXIT_OK
    if not args.page_id:
        die("给出 page_id 或 --all", config.EXIT_PRECOND)
    p = ctx.db.published_page(pid, args.page_id)
    if not p:
        die(f"未发布条目: {args.page_id}", config.EXIT_PRECOND)
    ctx.db.unpublish_page(pid, args.page_id)
    target = os.path.join(ctx.root, p["path"])
    if os.path.isfile(target):
        os.unlink(target)
    # 索引同步重写: 曾只删行删文件 —— _index.yaml / knowledge/_index.yaml /
    # repowiki-metadata.json / generation-state.json 仍挂着幻影条目指向
    # 已删文件(Qoder 导入面读的正是这些索引), 直到某次 finish 才自愈
    from .planner import load_plan_optional
    plan = load_plan_optional(ctx)   # P60: 单一实现(坏计划留 warn)
    style = str((plan or {}).get("style") or ctx.style)
    try:
        pages_now = ctx.db.published_pages(pid)
        _write_indexes(ctx, ctx.partition(), pages_now,
                       ctx.db.modules(pid), "manual", style)
        _write_generation_state(ctx, ctx.partition(),
                                {"id": "manual"}, pages_now)
    except Exception as e:   # noqa: BLE001 — 索引尽力而为, 不挡删除
        info(f"note: 索引重算失败({e}) — 下次 finish 会重写")
    ctx.db.log("manual", "delete_wiki", args.page_id)
    print(f"deleted {args.page_id}")
    return config.EXIT_OK


def cmd_export(args) -> int:
    ctx = Ctx(args).load()
    pid = ctx.partition()["id"]
    if args.direction == "db_to_file":
        # P39: --force 是 file_to_db 的基线越权开关, db_to_file 无条件重
        # 渲染 —— 曾被解析后静默丢弃, 看起来像记录在案的强制语义
        if getattr(args, "force", False):
            die("--force 只用于 file_to_db(越过无基线守卫); "
                "db_to_file 本就无条件重渲染", config.EXIT_PRECOND)
        pages = ctx.db.published_pages(pid)
        if not pages:
            die("DB 无已发布条目", config.EXIT_PRECOND)
        all_paths = {p["path"] for p in pages}
        # 崩溃自愈路径与 finish/write/update 同口径: plan 级 style 与
        # export_style 都优先 —— 曾按 last_run 渲染, plan 改风格后的一次
        # 自愈把整库重渲染回旧风格
        from .planner import load_plan_optional
        plan = load_plan_optional(ctx)   # P60: 单一实现(坏计划留 warn)
        style = str((plan or {}).get("style")
                    or (ctx.db.last_run(pid) or {"style": ""})["style"]
                    or ctx.style)
        for p in pages:
            # P2: 每行按自己的盖章风格渲染(与 publish_run 逐行合成、
            # file_to_db 的剥除判定同口径) —— 曾整库用单一 plan/run 级
            # 风格重渲染: 行戳 deep 的页被按 concise 注入 TOC/cite, 随后
            # file_to_db 按行戳(deep→不剥)把注入层永久吸收进 page.content
            # (违反"注入物只存在于导出文件, 永不写回 page.content"防线)
            row_style = str(p["style"] or style)
            target = os.path.join(ctx.root, p["path"])
            exported, _ = compose_export_content(ctx, p, row_style, all_paths,
                                                 plan=plan)
            atomic_write(target, exported)
        print(f"db_to_file: 重写 {len(pages)} 个文件(崩溃自愈路径)")
        return config.EXIT_OK
    # file_to_db: 吸收手改文件(守卫 + 剥注入物 —— 只剥该风格真会注入的;
    # c028: 与 cmd_write 同款活动 run 守卫 —— 读树文件→写 DB 的循环可与
    # finish 的发布交错, 把 finish 刚发布的新内容用旧渲染盖回 DB)
    if ctx.db.active_run(pid):
        die("存在活动 run — 先 finish/cancel 再 export file_to_db"
            "(避免与发布互相覆盖)", config.EXIT_PRECOND)
    # deep 的 cite 块是 writer 正文, 曾被无差别剥掉再反手判 deep 页违规)
    part = ctx.partition()
    if not args.force:
        baseline_commit = part["baseline_commit"]
        head = ctx.commit
        if head and (not baseline_commit or "magic" in baseline_commit):
            die("拒绝把真实 HEAD 导入无基线(或 magic)的 wiki —— "
                "Qoder 同款守卫; 确认用 --force", config.EXIT_GATE)
    run_style = str((ctx.db.last_run(pid) or {"style": ""})["style"] or "")
    # 剥注入物与注入侧同判(export_injects, plan 级 export_style 优先)
    # —— 曾只看 preset, current 模式下注入层从不注入, 作者手写的
    # cite/目录被当注入物剥掉再写回 DB(永久丢稿)
    from .planner import load_plan_optional
    plan = load_plan_optional(ctx)   # P60: 单一实现(坏计划留 warn)
    # 空盖章行的兜底链与 db_to_file 同序(行戳 > plan > last_run > ctx)——
    # 曾只到 last_run: plan 改风格后 db_to_file 按 plan 注入 cite/目录,
    # file_to_db 按 last_run 判"该风格不注入"而不剥, 注入物被吸收进
    # page.content(bug10 防线在空盖章 + plan≠run 时重新打开)
    _fallback = str((plan or {}).get("style") or run_style or ctx.style)
    absorbed = skipped = unchanged = 0
    for p in ctx.db.published_pages(pid):
        full = os.path.join(ctx.root, p["path"])
        if not os.path.isfile(full):
            skipped += 1
            continue
        try:
            with open(full, encoding="utf-8") as fh:
                content = fh.read()
        except UnicodeDecodeError as e:
            # c048 同族: 手编文件的坏字节给可诊断的门禁错(指明停在哪一页),
            # 不是循环中途裸 traceback —— 早前页已吸收, 修好该文件重跑即可
            die(f"发布文件不是合法 UTF-8({full}: {e}) — 修好后重跑 file_to_db",
                config.EXIT_GATE)
        page_style = str(p["style"] or _fallback)
        if p["kind"] == "page" and export_injects(ctx, page_style, plan):
            content = _strip_injected(content)   # bug e: 剥注入物再入库
        # content_hash 的读者: 吸收前比对, 未变的行不再无脑重写
        if config.sha256_bytes(content.encode()) == (p["content_hash"] or ""):
            unchanged += 1
            continue
        ctx.db.save_page_content(pid, p["page_id"], content)
        absorbed += 1
    ctx.db.log("manual", "file_to_db", f"absorbed {absorbed}, skipped {skipped}")
    print(f"file_to_db: 吸收 {absorbed} 个手改文件(注入物按风格剥离), "
          f"跳过 {skipped}, 无变化 {unchanged}")
    return config.EXIT_OK
