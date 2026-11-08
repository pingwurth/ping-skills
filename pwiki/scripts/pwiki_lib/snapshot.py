"""快照与增量: 跟踪集哈希、陈旧矩阵、目录漂移零重写、风格漂移。

移植 qwiki _expand_scope/_snapshot_hashes/cmd_update(3766-4113), 修复:
  - 卡片 scope 参与跟踪集(修 qwiki 缺陷 g 的 TODO; '**' 全仓 glob 除外)
  - `write` 后刷新基线(refresh_baseline_for —— qwiki 曾留下陈旧基线)
  - 风格漂移: plan 改风格后对已发布项按新预设复检, 只排队不过关项
    (过关页零 LLM 保留)。

陈旧理由(new_item/sources_changed/sources_removed/prompt_changed/
catalog_changed/modules_changed/style_changed)与 qwiki 口径一致;
untrack≠delete: 离开跟踪集不等于删除, removed 要求文件真的没了。
"""

from __future__ import annotations

import json
import os
import sys
import uuid

from . import config
from .config import Ctx
from .gates import extract_citations, strip_code_fences
from .planner import (card_job, load_plan, modules_hash, page_path_for,
                      plan_hash, plan_cards, plan_modules, plan_pages,
                      validate_plan)
from .scan import expand_scope
from .util import as_int, posix, die, info, join_confined


def _as_list(v) -> list:
    """scope/source_files 字段容错读取: 发布行上是 JSON 字符串, 计划条目
    上是真 list。曾是"谁是字符串谁被逐字符遍历"—— 卡片行整列 JSON
    字符串, expand_scope 收到的是 '[' '"' 's' 'r' 'c'…, 卡片 scope 从
    不进基线; 含 '/' 的字符串更会把整仓当目录展开。
    (r4 #57: 解码本体统一 util.json_list —— 这是别名, 保留历史注释)"""
    from .util import json_list
    return json_list(v)


def snapshot_hashes(ctx: Ctx, pages: list, modules: list, cards=None,
                    baseline: dict = None) -> dict:
    """哈希全部跟踪文件: 模块 scope ∪ 条目源 ∪ 正文引用 ∪ 卡片 scope(非全仓)。

    (证据: Qoder mtree rhash —— 发布时对跟踪集逐文件哈希。)
    给 baseline 时先比 (size, mtime): 命中即复用旧哈希 —— 10k 文件的
    no-change update 曾整读 ~200MB, 现在只剩 stat()。"""
    entries, seen = {}, set()
    pages = [dict(p) if not isinstance(p, dict) else p for p in pages]
    modules = [dict(m) if not isinstance(m, dict) else m for m in modules]
    cards = [dict(c) if not isinstance(c, dict) else c for c in (cards or [])]

    def add_file(rel: str):
        if rel in entries:
            return
        # c049: 越界路径(绝对/../)不进基线 —— 不哈希仓外文件
        full = join_confined(ctx.repo, rel)
        if full and os.path.isfile(full):
            try:
                st = os.stat(full)
                mtime = int(st.st_mtime * 1000)
                old = (baseline or {}).get(rel)
                if old and old.get("size") == st.st_size \
                        and old.get("mtime") == mtime and old.get("sha256"):
                    entries[rel] = {"sha256": old["sha256"],
                                    "size": st.st_size, "mtime": mtime}
                    return
                sha = ctx.file_sha(full)
                if sha:
                    entries[rel] = {"sha256": sha,
                                    "size": st.st_size, "mtime": mtime}
            except OSError:
                pass

    def add_scope(rel: str):
        rel = str(rel).strip()
        if not rel or "*" in rel:
            # glob 全仓 scope('**')不整仓哈希; 依赖具体文件的条目仍会被
            # source_files/引用路径覆盖。
            return
        tmp, tmp_seen = {}, set()
        expand_scope(ctx.repo, tmp, tmp_seen, rel)
        for p in tmp:
            add_file(p)

    for m in modules:
        for s in _as_list(m.get("scope")):
            add_scope(s)
    for p in pages:
        for s in _as_list(p.get("source_files")):
            add_scope(str(s))
        # 栅栏内的示例引用不是证据, 不进跟踪集(与 gates 口径一致)
        for c in extract_citations(strip_code_fences(p.get("content") or "")):
            add_file(c["path"])
    for c in (cards or []):
        for s in _as_list(c.get("scope")):
            add_scope(s)
        for s in _as_list(c.get("source_files")):
            add_scope(str(s))
    return entries


def snapshot_run(ctx: Ctx, run, plan: dict, pages=None) -> str:
    part = ctx.partition()
    pid = part["id"]
    pages = list(pages) if pages is not None \
        else ctx.db.published_pages(pid)
    modules = ctx.db.modules(pid)
    cards = [p for p in pages if config.is_card_kind(p["kind"])]
    # 基线传入: 未变文件走 size+mtime 短路(cmd_update 同款)—— finish 曾
    # 每次全量重读重哈希跟踪集(万文件仓库的 3 页增量也是整轮 ~200MB)
    old = ctx.db.file_hashes(pid)
    hashes = snapshot_hashes(ctx, pages, modules, cards, baseline=old)
    snapshot_id = uuid.uuid4().hex[:16]
    # 增量写: 只 upsert 变化行 + 删除离集行(全表删写曾让 3 页增量 finish
    # 重写 10k 跟踪行; upsert_file_hashes 本就是为此而设)
    changed = {p: e for p, e in hashes.items()
               if p not in old or old[p]["sha256"] != e["sha256"]}
    ctx.db.upsert_file_hashes(pid, changed)
    ctx.db.delete_file_hashes(pid, set(old) - set(hashes))
    ctx.db.set_baseline(pid, snapshot_id, ctx.commit or "",
                        plan_hash(plan_pages(plan), plan_cards(plan)),
                        modules_hash(plan_modules(plan)), run["id"])
    return snapshot_id


def refresh_baseline_for(ctx: Ctx, page_ids: list) -> None:
    """`write`(手工修正)后刷新这些条目跟踪集的哈希基线(只 UPSERT 触及的行
    —— 曾全表删写, 10k 跟踪文件的单页修正要白 churn 1000 倍行数)。"""
    part = ctx.partition()
    pid = part["id"]
    baseline = ctx.db.file_hashes(pid)
    rows = []
    for page_id in page_ids:
        p = ctx.db.published_page(pid, page_id)
        if p is None:
            continue
        rows.append(p)
    fresh = snapshot_hashes(ctx, rows, [], [], baseline=baseline)
    ctx.db.upsert_file_hashes(pid, fresh)


# --------------------------------------------------------------------------- #
# update: 快照差异 → INCREMENT run
# --------------------------------------------------------------------------- #

def cmd_update(args) -> int:
    ctx = Ctx(args).load()
    part = ctx.partition()
    if not part["published_snapshot_id"]:
        die("还没有已发布 wiki — 先跑全量(`pwiki begin`)", config.EXIT_PRECOND)
    if ctx.db.active_run(part["id"]):
        die("存在活动 run — 先 finish 或 cancel", config.EXIT_PRECOND)
    plan = load_plan(ctx)
    errors, _ = validate_plan(ctx, plan,
                              profile=ctx.config.get("profile", "standard"),
                              strict=False)
    if errors:
        for e in errors:
            print(f"  error: {e}", file=sys.stderr)
        die("plan 校验失败 — 先修 wiki_plan.yaml", config.EXIT_GATE)

    pages, cards, modules = plan_pages(plan), plan_cards(plan), plan_modules(plan)
    published = {p["page_id"]: p for p in ctx.db.published_pages(part["id"])}

    modules_pub = [{"module_id": m["id"], "title": m.get("title", ""),
                    "dir": m.get("dir", ""),
                    "scope": json.dumps(m.get("scope", []), ensure_ascii=False),
                    "depends_on": json.dumps(m.get("depends_on", []),
                                             ensure_ascii=False),
                    "related_to": json.dumps(m.get("related_to", []),
                                             ensure_ascii=False)}
                   for m in modules]
    baseline = ctx.db.file_hashes(part["id"])
    current = snapshot_hashes(ctx, list(published.values()),
                              modules_pub, cards, baseline=baseline)
    changed = {p for p in current
               if p not in baseline or baseline[p]["sha256"] != current[p]["sha256"]}
    # removed = 文件真的没了; 只是离开跟踪集(条目被删)不算删除。
    # c049 同族: 越界基线键(修复前旧库可能带入)视同已消失 —— 顺带出跟踪集
    removed = {p for p in baseline if p not in current
               and (join_confined(ctx.repo, p) is None
                    or not os.path.isfile(join_confined(ctx.repo, p)))}
    if getattr(args, "from_commit", None):
        from .util import git
        # c046: git() 把一切失败吞成 '' —— 坏 commit 号曾静默变成"零差异
        # 全 noop"。先 rev-parse 验真, 验不过直接拒
        verified = git(ctx.repo, "rev-parse", "--verify",
                       f"{args.from_commit}^{{commit}}")
        if not verified:
            die(f"from-commit {args.from_commit} 不是本仓库的有效提交 — "
                "增量基线无法建立", config.EXIT_PRECOND)
        # -c core.quotepath=off: CJK 路径默认被 git 引成八进制转义
        # ("src/\347\273...md"), 永远撞不上 UTF-8 跟踪集键 —— from-commit
        # 差异里的中文路径曾整类静默 no-op
        diff_out = git(ctx.repo, "-c", "core.quotepath=off", "diff",
                       "--name-only", f"{args.from_commit}..HEAD")
        git_changed = {ln.strip() for ln in diff_out.split("\n") if ln.strip()}
        changed |= {p for p in git_changed if p in current}
        info(f"from-commit 差异 {args.from_commit[:10]}..HEAD: "
             f"{len(git_changed)} 文件, {len(git_changed & set(current))} 在跟踪集")
    modules_changed = part["baseline_modules_hash"] != modules_hash(modules)

    # 模块 scope 文件集(成员驱动陈旧)
    mod_files = {}
    for m in modules:
        tmp, tmp_seen = {}, set()
        for s in m.get("scope") or []:
            expand_scope(ctx.repo, tmp, tmp_seen, str(s))
        mod_files[m.get("id", "")] = set(tmp)

    # 风格回退口径与 begin/submit/finish 一致(plan 优先, 否则 config):
    # 曾在这里退到 DEFAULT_STYLE(deep)而 run 用 ctx.style(concise),
    # 一次 update 就把整库按 deep 重排。空 style 的旧行视为漂移 → 复检。
    plan_style = str(plan.get("style") or ctx.style)
    style_drift = any(str(p["style"] or "") != plan_style
                      for p in published.values())

    stale = []   # (item, reason)

    def expand_dirs(strs) -> set:
        """目录型 source/scope 展开成文件集: 基线按展开文件哈希, 陈旧
        对账也必须同口径 —— 曾只比目录串本身, scope-only 条目的跟踪集
        永远撞不上"变更文件", 源码全改了还报 0 stale。"""
        out = set()
        for s in strs:
            s2 = str(s).rstrip("/")
            if not s2:
                continue
            out.add(s2)
            full = join_confined(ctx.repo, s2)
            if full and os.path.isdir(full):
                tmp, tmp_seen = {}, set()
                # expand_scope 的目录分支认尾斜杠(无斜杠落进文件分支静默
                # 不展开)
                expand_scope(ctx.repo, tmp, tmp_seen, s2 + "/")
                out.update(tmp)
        return out

    # 每页引用集只算一次(check_item 与旧值对账复用; 曾与 snapshot_hashes
    # 各算一遍)。栅栏内的示例引用不是证据(与 gates 口径一致)
    cited_by_page = {}
    for _pid, _pub in published.items():
        cited_by_page[_pid] = {
            c["path"] for c in
            extract_citations(strip_code_fences(_pub["content"] or ""))}

    def check_item(item, pub, sources):
        pid = item.get("id")
        if pub is None:
            stale.append((item, "new_item"))
            return
        srcs = expand_dirs(sources)
        cited = cited_by_page.get(str(pid), set())
        old_srcs = expand_dirs(_as_list(pub["source_files"]))
        scope = set()
        mid = item.get("module") or (pub["module"] if pub else "")
        if mid in mod_files:
            scope = mod_files[mid]
        watch = srcs | cited | old_srcs | scope
        if watch & changed:
            stale.append((item, "sources_changed"))
        elif watch & removed:
            stale.append((item, "sources_removed"))
        elif str(item.get("prompt", "")) != (pub["prompt"] or ""):
            stale.append((item, "prompt_changed"))
        else:
            # 目录形状漂移: 重挂/改名/改型 —— 即使源干净也要重建路径与树位。
            # ordinal: 计划没写序数的条目(卡片从来不带)视为不变 ——
            # 曾拿 pub 的 950 对计划的 0, 每次 update 都全量误报 catalog_changed。
            # 显式 None(yaml `type:`/`slug:` 空值)经 or "" 归一 —— 曾
            # str(None)='None' 对 pub 的 '', catalog_changed 永不收敛,
            # 每轮 update 0-job INCREMENT 重写全树。
            # 对账字段涵盖计划侧全部写作契约字段: required_sections/modules/
            # pack_key/objective 的手编改动曾不触发任何 stale reason,
            # 新要求只作为 validate --content 的永久错误浮出。
            item_ordinal = as_int(item.get("ordinal"), pub["ordinal"])

            def _jjoin(v):
                return "|".join(str(x) for x in
                                (v if isinstance(v, list) else _as_list(v)))

            before = (pub["title"], pub["parent_id"], pub["ordinal"],
                      pub["article_type"] or "", pub["slug"] or "",
                      _jjoin(pub["modules"]), str(pub["objective"] or ""),
                      _jjoin(pub["required_sections"]),
                      str(pub["pack_key"] or ""))
            after = (str(item.get("title") or ""), str(item.get("parent") or ""),
                     item_ordinal, str(item.get("type") or ""),
                     str(item.get("slug") or ""),
                     _jjoin(item.get("modules") or []),
                     str(item.get("objective") or ""),
                     _jjoin(item.get("required_sections") or []),
                     str(item.get("pack_key") or ""))
            if before != after:
                stale.append((item, "catalog_changed"))

    for p in pages:
        if p.get("kind") == "module_sheet" and modules_changed:
            stale.append((p, "modules_changed"))
        else:
            check_item(p, published.get(str(p.get("id"))),
                       p.get("source_files") or [])
    for c in cards:
        check_item(c, published.get(str(c.get("id"))),
                   c.get("source_files") or c.get("scope") or [])
    removed_items = [pid for pid in published if pid not in
                     ({str(p.get("id")) for p in pages} |
                      {str(c.get("id")) for c in cards})]

    # 风格漂移: 按新预设复检已发布项, 只排队不过关的(过关页零 LLM 保留)
    style_stale = []
    if style_drift:
        from . import gates
        for pub in published.values():
            # 卡片也按风格分档(concise 0 行级 / deep ≥3)—— 曾按字面元组
            # 跳过, concise→deep 切换后旧卡永不再排, validate --content
            # 长期报错无修复路径
            if pub["kind"] not in ("page", "module_sheet", "card"):
                continue
            if pub["style"] == plan_style:
                continue
            errs, _ = gates.gate_item(pub["content"], pub, ctx.repo,
                                      plan_style, phase="lining")
            errs = [e for e in errs if not gates.is_auto_fixable(e)]
            if errs:
                item = next((x for x in pages + cards
                             if str(x.get("id")) == pub["page_id"]), None)
                if item is not None:
                    style_stale.append((item, "style_changed"))
            else:
                # c005: 过得了新门禁的行在此重盖章(章是门禁挣来的) —— 曾只
                # requeue FAILING 行, PASSING 行永远带旧戳: style_drift 永不
                # 收敛, 每次 update 对全库重跑一遍门禁
                ctx.db.set_page_style(pid, pub["page_id"], plan_style)

    print(f"diff vs baseline {part['published_snapshot_id']}: "
          f"{len(changed)} changed / {len(removed)} removed tracked; "
          f"modules_changed={modules_changed} style_drift={style_drift}")
    moved = [(i, w) for i, w in stale if w == "catalog_changed"]
    stale = [(i, w) for i, w in stale if w != "catalog_changed"] + style_stale
    print(f"stale items: {len(stale)}  moved: {len(moved)}  "
          f"removed_items: {len(removed_items)}")
    for item, why in stale[:25]:
        print(f"  - {item.get('id')} ({why})")
    for item, _why in moved[:25]:
        print(f"  - {item.get('id')} (catalog_changed → finish 时零重写移动)")
    if not stale and not moved and not removed_items and not modules_changed:
        print("no update required (noop)")
        return config.EXIT_OK

    # c036: 前置检查(:143)与建 run 之间的窗口可被并发 begin/reconcile 钻入 —
    # 守卫式创建兜底, 竞态输家如实退出
    run_id, raced = ctx.db.create_run_guarded(
        part["id"], "update", "increment", "INCREMENT",
        "DIRECTORY_GENERATING", ctx.commit,
        plan_hash(pages, cards),
        {"locale": ctx.locale}, style=plan_style)
    if run_id is None:
        die(f"建 run 时发现并发活动 run {raced['id']}({raced['state']}) — "
            "重试 `pwiki update`", config.EXIT_PRECOND)
    modules_by_id = {m.get("id"): m for m in modules}
    items = []
    for item, _why in stale:
        # 卡片字面量统一走 planner.card_job(四个排队入口曾各抄一份)
        if config.is_card_kind(item.get("kind")):
            items.append(card_job(item, ctx.locale))
            continue
        job = dict(item)
        job["path"] = posix(page_path_for(pages, job, ctx.locale,
                                         modules_by_id))
        items.append(job)
    ctx.db.queue_jobs(run_id, items, style=plan_style)
    ctx.db.update_run(run_id, state="DOCUMENT_GENERATING", stage="document")
    ctx.db.log(run_id, "increment_planned",
               f"{len(stale)} stale, {len(removed_items)} removed")
    print(f"run {run_id}: INCREMENT over {len(stale)} items — `pwiki next`")
    if removed_items:
        print(f"note: {len(removed_items)} 个已发布项离开计划, finish 时剪除")
    return config.EXIT_OK
