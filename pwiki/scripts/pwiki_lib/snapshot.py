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
from .planner import (load_plan, modules_hash, plan_hash, plan_cards,
                      plan_modules, plan_pages, queue_item, validate_plan)
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
    no-change update 曾整读 ~200MB, 现在只剩 stat()。
    取舍(文档化的失配窗口, r6 #34): 同尺寸编辑落在同一 mtime tick 内会
    复用旧哈希 —— 本机 ext4 是 ns 级 st_mtime, 人类节奏下概率可忽略;
    NFS/SMB 等 1s 粒度挂载或紧凑脚本循环写入可命中, 陈旧直到文件再被
    触碰。兜底: `update --from-commit` 走 git diff 不受此影响
    (Ctx.file_sha 同款缓存同款取舍)。"""
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
    # 重写 10k 跟踪行; upsert_file_hashes 本就是为此而设)。
    # 三写同装一个事务(P25 只修了 upsert 内部批次): 哈希行与分区基线
    # 曾横跨三个独立提交 —— 崩在中间留下分叉基线, 异常路径落
    # DOCUMENT_FAILED 后 update 看到一次性虚假 modules_changed
    changed = {p: e for p, e in hashes.items()
               if p not in old or old[p]["sha256"] != e["sha256"]}
    ctx.db.begin()
    try:
        ctx.db.upsert_file_hashes(pid, changed)
        ctx.db.delete_file_hashes(pid, set(old) - set(hashes))
        ctx.db.set_baseline(pid, snapshot_id, ctx.commit or "",
                            plan_hash(plan_pages(plan), plan_cards(plan)),
                            modules_hash(plan_modules(plan)), run["id"])
        ctx.db.commit()
    except BaseException:
        ctx.db.rollback()
        raise
    return snapshot_id


def refresh_baseline_for(ctx: Ctx, page_ids: list) -> None:
    """`write`(手工修正)后刷新这些条目跟踪集的哈希基线(只 UPSERT 触及的行
    —— 曾全表删写, 10k 跟踪文件的单页修正要白 churn 1000 倍行数)。

    r7 #15: 卡行走 cards 参数(跟踪契约 source_files ∪ scope, 与 finish 同
    口径)—— 曾硬编码 [], 手工修卡只刷新 source_files 半边, 下轮 update 把
    卡重判 sources_changed, 刚修的内容被整轮重写覆盖。
    r7 #16: 只把**新增**跟踪文件入基线 —— 全量 upsert 会把与本次 write 无
    关的在途源码编辑顺手吸收成新基线(该页及共享文件的其他页此后对这些
    编辑永不再报 stale); 已有键保持上次 finish 的值, 宁可下轮 update 多报
    一次 stale, 不静默吸收。"""
    part = ctx.partition()
    pid = part["id"]
    baseline = ctx.db.file_hashes(pid)
    rows = []
    for page_id in page_ids:
        p = ctx.db.published_page(pid, page_id)
        if p is None:
            continue
        rows.append(p)
    page_rows = [r for r in rows if not config.is_card_kind(r["kind"])]
    card_rows = [r for r in rows if config.is_card_kind(r["kind"])]
    fresh = snapshot_hashes(ctx, page_rows, [], card_rows, baseline=baseline)
    ctx.db.upsert_file_hashes(pid, {k: v for k, v in fresh.items()
                                    if k not in baseline})


# --------------------------------------------------------------------------- #
# update: 快照差异 → INCREMENT run
# --------------------------------------------------------------------------- #

def cmd_update(args) -> int:
    ctx = Ctx(args).load()
    part = ctx.partition()
    ctx.ensure_publish_tree()   # P1: 与 init/begin 同源落发布骨架
    if not part["published_snapshot_id"]:
        die("还没有已发布 wiki — 先跑全量(`pwiki begin`)", config.EXIT_PRECOND)
    ctx.db.die_if_active_run(part["id"], "finish 或 cancel")
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

    # r7 #63: 直接传 plan 模块列表 —— snapshot_hashes 经 _as_list 解码,
    # 原垫片的 JSON 编码杂交形态是纯死工作(第三种模块行形态的维护负担)
    baseline = ctx.db.file_hashes(part["id"])
    current = snapshot_hashes(ctx, list(published.values()),
                              modules, cards, baseline=baseline)
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
    # r7 #45: 补 run 层(与 finish 的 run>plan>ctx 同序)—— 空盖章旧行的
    # 重盖章与 finish 的逐行合成曾按不同层级解析
    plan_style = str(plan.get("style")
                     or str((ctx.db.last_run(part["id"])
                             or {"style": ""})["style"] or "")
                     or ctx.style)
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
            # P11: 用户显式 skip 过的项不复活 —— "跳过是用户决定, 任何后续
            # run 都不该翻案"; 曾按 new_item 重新排队还整页 LLM 重写
            if str(pid) in skipped_ids:
                return
            stale.append((item, "new_item"))
            return
        srcs = expand_dirs(sources)
        cited = cited_by_page.get(str(pid), set())
        old_srcs = expand_dirs(_as_list(pub["source_files"]))
        scope = set()
        # pub 到此处必非 None(251-257 两臂皆早退), 旧代码的 `if pub else ""`
        # 是死分支 —— 留着只会让读者回推本函数的空值契约
        mid = item.get("module") or pub["module"]
        if mid in mod_files:
            scope = mod_files[mid]
        watch = srcs | cited | old_srcs | scope
        # r7 #6: 声明源集合本身的变化也对账 —— source_files/scope 曾不在
        # 任何对账元组里、不被 kept-merge 同步、跟踪集又按 DB 行旧值构建,
        # 手改声明源被三方同时静默丢弃(新文件永不跟踪, 该页后续变更全不
        # 触发 stale)
        new_declared = expand_dirs(_as_list(sources)
                                   + _as_list(item.get("scope")))
        old_declared = expand_dirs(_as_list(pub["source_files"])
                                   + _as_list(pub["scope"]))
        if watch & changed:
            stale.append((item, "sources_changed"))
        elif watch & removed:
            stale.append((item, "sources_removed"))
        elif new_declared != old_declared:
            stale.append((item, "sources_redeclared"))
        elif str(item.get("prompt") or "").rstrip("\n") != \
                str(pub["prompt"] or "").rstrip("\n"):
            # r8 #8/#9: (a) `or ""` 归一显式空 prompt:(yamlite 保留 None
            # 键)—— str(None)='None' 对行侧 '' 曾每轮 update 触发全量重写
            # (同 :310-312 type/slug 已修的 str(None) 家族); (b) 两侧
            # rstrip("\n"): r7 #28 起 clip('|') 按 YAML 语义保留恰一个尾
            # 换行, 而升级前的旧行存的是 rstrip 过的值 —— 不归一则一次
            # update 把全部块标量 prompt 页误判 prompt_changed 大 churn
            # (顺带覆盖期间的手工页编辑)
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

            # r7 #8: 对账补 module/sheet(kept-merge 一直同步这两个字段,
            # 曾"合而不比" —— 手改挂模块/换 sheet 对 update 完全不可见,
            # 文件永远留在旧目录, validate --content 永久报"计划页未落盘");
            # kind 不进元组: 行上是归一形态('card')与计划侧卡种类永不等,
            # 且改型必改 path 已被路径对账覆盖
            # r8 #7: type 认双拼写(与 kept-merge/_insert_job 同口径)——
            # 手滑把 type: 改写成 article_type:(repowiki 索引显示名)时,
            # 对账曾读 '' 对行 'feature' → catalog_changed 0-job 死循环
            before = (pub["title"], pub["parent_id"], pub["ordinal"],
                      pub["article_type"] or "", pub["slug"] or "",
                      _jjoin(pub["modules"]), str(pub["objective"] or ""),
                      _jjoin(pub["required_sections"]),
                      str(pub["pack_key"] or ""),
                      pub["module"] or "", pub["sheet"] or "")
            after = (str(item.get("title") or ""), str(item.get("parent") or ""),
                     item_ordinal, str(item.get("type")
                                       or item.get("article_type") or ""),
                     str(item.get("slug") or ""),
                     _jjoin(item.get("modules") or []),
                     str(item.get("objective") or ""),
                     _jjoin(item.get("required_sections") or []),
                     str(item.get("pack_key") or ""),
                     str(item.get("module") or ""),
                     str(item.get("sheet") or ""))
            if before != after:
                stale.append((item, "catalog_changed"))

    # P11: 跳过集一次取齐(begin --missing 同源 —— update 曾把从未发布的
    # 跳过项当 new_item 复活)
    skipped_ids = ctx.db.skipped_page_ids(part["id"])
    for p in pages:
        if p.get("kind") == "module_sheet" and modules_changed:
            stale.append((p, "modules_changed"))
        else:
            check_item(p, published.get(str(p.get("id"))),
                       p.get("source_files") or [])
    for c in cards:
        # P10: 卡片的陈旧对账与基线同口径(scope ∪ source_files) —— 曾只看
        # source_files, 卡片声明 scope 后 scope 文件的变更永不把它标 stale。
        # 走 _as_list 解码: 手编 `scope: "**"`(标量)曾让 + 拼接 TypeError
        # 裸崩(基线路径 96 行一直是容错的, 只有这处绕过解码)
        check_item(c, published.get(str(c.get("id"))),
                   list(_as_list(c.get("source_files"))
                        + _as_list(c.get("scope"))))
    removed_items = [pid for pid in published if pid not in
                     ({str(p.get("id")) for p in pages} |
                      {str(c.get("id")) for c in cards})]

    # 风格漂移: 按新预设复检已发布项, 只排队不过关的(过关页零 LLM 保留)
    style_stale = []
    if style_drift:
        from . import gates
        from .planner import mod_titles_for   # plan_modules 已在模块级导入
        drift_mods = plan_modules(plan)
        for pub in published.values():
            # 卡片也按风格分档(concise 0 行级 / deep ≥3)—— 曾按字面元组
            # 跳过, concise→deep 切换后旧卡永不再排, validate --content
            # 长期报错无修复路径
            # P50: kind 判定走注册表口径 —— 裸字面元组曾把原始卡片 kind
            # 的行静默退出漂移循环(style_drift 永不收敛)
            if pub["kind"] not in ("page", "module_sheet") \
                    and not config.is_card_kind(pub["kind"]):
                continue
            if pub["style"] == plan_style:
                continue
            errs, _ = gates.gate_item(pub["content"], pub, ctx.repo,
                                      plan_style, phase="lining",
                                      mod_titles=mod_titles_for(pub, drift_mods))
            errs = gates.blocking_errors(errs)   # 过滤单一属主(仅剔 auto)
            if errs:
                item = next((x for x in pages + cards
                             if str(x.get("id")) == pub["page_id"]), None)
                if item is not None:
                    style_stale.append((item, "style_changed"))
            else:
                # c005: 过得了新门禁的行在此重盖章(章是门禁挣来的) —— 曾只
                # requeue FAILING 行, PASSING 行永远带旧戳: style_drift 永不
                # 收敛, 每次 update 对全库重跑一遍门禁。
                # F06: 这里曾写 pid(本函数从未绑定, 只在嵌套 check_item/推导
                # 式里出现)→ NameError, update 当场崩; 分区号取 part["id"]
                ctx.db.set_page_style(part["id"], pub["page_id"], plan_style)

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
    # 分派统一走 planner.queue_item(卡片/页 + 路径派生的单一属主)
    items = [queue_item(item, pages, ctx.locale, modules_by_id)
             for item, _why in stale]
    ctx.db.queue_jobs(run_id, items, style=plan_style)
    # 排队窗口守卫式写(cmd_begin 同款): 期间被并发 cancel/begin --force
    # 取消的 run 不翻回活动态
    if not ctx.db.update_run_guarded(
            run_id, ("DIRECTORY_GENERATING",),
            state="DOCUMENT_GENERATING", stage="document"):
        die(f"run {run_id} 在排队期间被并发操作取消 — 重新 `pwiki update`",
            config.EXIT_PRECOND)
    # r8 #14: 模块注册表随 run 刷新 —— submit 的 mod_titles 走 DB 模块表
    # (r7 #52), update 曾不刷表, 运行中途改模块名的整个 run 里 submit 按
    # 旧表要旧标题, finish 按新 plan 硬拒(与 begin/regenerate 同口径)
    ctx.db.publish_modules(part["id"], modules)
    ctx.db.log(run_id, "increment_planned",
               f"{len(stale)} stale, {len(removed_items)} removed")
    print(f"run {run_id}: INCREMENT over {len(stale)} items — `pwiki next`")
    if removed_items:
        print(f"note: {len(removed_items)} 个已发布项离开计划, finish 时剪除")
    return config.EXIT_OK
