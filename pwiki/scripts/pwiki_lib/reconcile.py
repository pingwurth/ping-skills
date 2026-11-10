"""和解层: writer 报告的结构化待办(D22 标题勘误 / D7 卡片冲突)落为动作。

repowiki 的 D7/D22 是"散文编排"(主 agent 手工改名/手工修卡) —— pwiki 把
它们变成引擎命令:
  - D22 改名: 移动文件 + 更新 plan 的 title/path + 改 H1 + 重算索引;
    引用不动(citation 只认源码路径)。零 LLM。
  - D7 卡片冲突: 把卡片排进 regenerate 队列(术语之争以卡为准由人工定夺,
    事实之争以行级引用证据为准 —— 冲突卡片本身重写)。
"""

from __future__ import annotations

import json
import os
import re

from . import config
from .config import Ctx
from .util import die


def cmd_reconcile(args) -> int:
    ctx = Ctx(args).load()
    pid = ctx.partition()["id"]
    pending = ctx.db.reconcile_pending(pid)
    if not pending:
        print("没有待处理的和解项")
        return config.EXIT_OK

    apply_id = getattr(args, "apply", None)
    apply_all = getattr(args, "all", False)
    dry_run = getattr(args, "dry_run", False)
    apply_n = None
    if apply_id is not None:
        # 非数字 --apply 曾在列表推导里裸 int() 抛 ValueError traceback
        try:
            apply_n = int(apply_id)
        except (TypeError, ValueError):
            die(f"--apply 需要 reconcile 编号(数字), 得到 {apply_id!r}",
                config.EXIT_GATE)
    todo = [r for r in pending
            if apply_all or (apply_n is not None and r["id"] == apply_n)]
    if not todo:
        print(f"{len(pending)} 项待处理(用 --apply <id> 或 --all):")
        for r in pending:
            _print_item(r)
        return config.EXIT_OK

    done = []
    for r in todo:
        payload = json.loads(r["payload"])
        if r["kind"] == "title_mismatch":
            status, outcome = _apply_title_rename(ctx, payload, dry_run)
        else:
            status, outcome = _apply_card_conflict(ctx, payload, dry_run)
        # applied→落 applied; rejected→结构性拒绝(计划里没有/空标题),
        # 落 rejected 不复活; retry→瞬态拒绝(存在活动 run), 保留 pending
        # 等下次。曾一律 applied —— 拒绝项从队列静默消失, writer 报的
        # 冲突只有操作者恰好瞄到那一行输出才知道被丢
        if not dry_run:
            if status in ("applied", "rejected"):
                ctx.db.reconcile_resolve(r["id"], status)
        done.append((r["id"], r["kind"], status, outcome))
    marks = {"applied": "->", "rejected": "-×", "retry": "-?"}
    for rid, kind, status, outcome in done:
        print(f"#{rid} [{kind}] {marks.get(status, '->')} {outcome}"
              + ("" if status != "retry" else "(保留待处理)"))
    if dry_run:
        print("(dry-run: 未落盘)")
    return config.EXIT_OK


def _print_item(r: dict) -> None:
    payload = json.loads(r["payload"])
    if r["kind"] == "title_mismatch":
        print(f"  #{r['id']} D22 改名: {payload.get('page_id')} "
              f"“{payload.get('old')}” → “{payload.get('proposed')}”"
              f"  证据: {str(payload.get('evidence'))[:60]}")
    else:
        print(f"  #{r['id']} D7 卡片冲突: {payload.get('card')} "
              f"卡上说“{str(payload.get('card_line'))[:40]}” vs "
              f"{str(payload.get('evidence'))[:50]}")


def _move_published_file(ctx: Ctx, plan: dict, pub, content: str,
                         new_path: str, moved: list, missing: list,
                         all_paths=None) -> None:
    """把发布文件从 DB 行记录的真实旧路径搬到 new_path, 落盘走导出合成层。

    旧位置取 pub["path"](发布行)而不是 plan 的 path 键 —— plan 从不回写,
    拿它当旧位置曾导致: DB 指向不存在的新文件, 磁盘没人动, 旧文件随后
    被 finish 当孤儿清走。落盘与 finish 同口径(compose_export_content):
    曾直写 DB 原文, legacy 页一改名就丢 <cite>/编号目录。
    all_paths= 调用方已取的发布路径集(改名级联曾每搬一个文件全表
    SELECT * 拖一遍整库稿体)。"""
    from . import publish
    old_path = str(pub["path"] or "")
    old_full = os.path.join(ctx.root, old_path) if old_path else ""
    if not old_path or not os.path.isfile(old_full):
        missing.append(old_path or "(空路径)")
        return
    row = dict(pub)
    row["content"] = content
    row["path"] = new_path
    pid = ctx.partition()["id"]
    if all_paths is None:
        all_paths = ctx.db.published_paths(pid)
    all_paths = set(all_paths)
    all_paths.discard(old_path)
    all_paths.add(new_path)
    # P3: 落盘按行自己的盖章风格(与 publish_run 逐行合成、file_to_db 的
    # 剥除判定同口径) —— 曾用 plan 级风格: deep 行撞 concise 计划时文件被
    # 注入 TOC 而行戳仍 deep, file_to_db 由此把注入层吸收进 page.content
    # (与 export db_to_file 同族; 行戳在此不变更, 保持与 DB 内容一致)
    # r7 #9: 兜底链补 last_run 层(与 file_to_db 的 行戳>plan>run>ctx 同序)
    # —— 空盖章旧行 + plan 无显式风格时, 曾按 ctx 解析出与 file_to_db
    # 相反的风格: 改名文件注入 cite/TOC 而 file_to_db 判"该风格不注入"
    # 不剥, 注入物被吸进 page.content(bug10 防线重开)
    _run_style = str((ctx.db.last_run(ctx.partition()["id"])
                      or {"style": ""})["style"] or "")
    style = str(pub["style"] or (plan or {}).get("style")
                or _run_style or ctx.style)
    exported, _ = publish.compose_export_content(ctx, row, style, all_paths,
                                                 plan=plan)
    new_full = os.path.join(ctx.root, new_path)
    # c045 同款原子写: 改名落盘被中断时不再留半份新文件
    from .util import atomic_write
    atomic_write(new_full, exported)
    # 派生路径未变的改名(module_sheet 路径由模块目录+显示名派生与标题无关;
    # 或两标题 safe_filename 归一后同名): 写新=写旧, 随手 unlink 会把刚写
    # 的文件删掉 —— 曾"成功搬移"报数却把页面从导出树里静默抹掉
    if os.path.normpath(new_full) != os.path.normpath(old_full):
        os.unlink(old_full)
        moved.append(old_path)


def _rename_h1(content: str, title: str) -> str:
    """把真 H1(栅栏外第一个 '# ' 行)改成新标题。

    P13: 栅栏感知, 与 gate_item 的 H1 口径同源(校验在剥栅栏文本上找 H1)
    —— 曾对原始内容 re.sub(count=1): 页首代码栅栏里的 '#' 注释被当 H1
    改写, 真 H1 原样留着, 行标题与内容 H1 从此永久不一致且无 stale 原因
    修复它。找不到栅栏外 H1 时原样返回(不改)。"""
    from .gates import split_fences   # P61: 共用切分器
    parts = split_fences(content)
    for i, seg in enumerate(parts):
        if seg.startswith("```"):
            continue
        m = re.search(r"^# .*$", seg, re.M)
        if m:
            parts[i] = seg[:m.start()] + f"# {title}" + seg[m.end():]
            return "".join(parts)
    return content


def _apply_title_rename(ctx: Ctx, payload: dict, dry_run: bool) -> tuple:
    """返回 (status, msg): status ∈ applied | rejected。"""
    from .planner import (assign_paths, load_plan, plan_pages, save_plan)
    page_id = str(payload.get("page_id"))
    proposed = str(payload.get("proposed"))
    plan = load_plan(ctx)
    pages = plan_pages(plan)
    page = next((p for p in pages if str(p.get("id")) == page_id), None)
    if page is None:
        return "rejected", f"计划里没有 {page_id} — 拒绝"
    if not proposed.strip():
        return "rejected", "空标题 — 拒绝"
    # 兄弟标题冲突守卫(validate_plan 同规): 同父同名页派生同一路径,
    # _move_published_file 会直接覆盖兄弟的发布文件 —— 曾无守卫静默覆盖
    # 还把 begin/update 都过不去的非法 plan 持久化
    if page.get("kind", "page") == "page":
        parent = str(page.get("parent") or "").strip()
        sib_titles = {str(p.get("title") or "").strip()
                      for p in pages
                      if p is not page and p.get("kind", "page") == "page"
                      and str(p.get("parent") or "").strip() == parent}
        if proposed.strip() in sib_titles:
            return ("rejected", f"兄弟标题冲突(父 {parent or '<root>'}): "
                    f"{proposed!r} 与兄弟同名 — 拒绝(validate 同规)")
    pid = ctx.partition()["id"]
    if ctx.db.active_run(pid) is not None:
        # _apply_card_conflict 同款守卫: 活跃 run 的 finish 会用旧标题行
        # INSERT OR REPLACE 回滚改名、把新路径文件当 moved-out 孤儿清掉
        # —— 曾改完被静默回滚还 exit 0
        return ("retry", "存在活动 run — 先 finish/cancel 再和解"
                "(避免发布回滚改名; 本项保留待处理)")
    old_title = str(page.get("title"))
    # 改名会级联: 子页路径以父标题为目录段 —— 记录改名前全部页面路径
    # (仅作"哪些子页受影响"的对照; 实际旧位置以 DB 发布行为准)
    old_paths = {str(p.get("id")): str(p.get("path") or "") for p in pages}
    page["title"] = proposed
    assign_paths(plan, ctx.locale)
    new_path = str(page.get("path") or "")

    moved = []
    missing = []
    if not dry_run:
        # 两阶段: 先把全部受影响 DB 行改到新路径, 再统一落盘搬文件。
        # 曾边改边搬 —— 父页对着半更新的 DB 合成, 内链被改写指向即将
        # 搬走的旧路径, 自家 validate --content 随即报 links/internal
        moves = []   # (改名前的发布行快照, 落盘内容, 新路径)
        pub = ctx.db.published_page(pid, page_id)
        # P37: 行/内容/后代路径同事务 —— 曾是一串裸自动提交(UPDATE 行 →
        # save_page_content → 后代 UPDATE), 中断留下行已改/内容仍旧 H1/
        # 文件旧路径的三方分歧, read/pages/export 全程服务该不一致态
        ctx.db.begin()
        try:
            # 事务内复查活动 run(c036 同窗): 前置检查是裸 SELECT, 检查与
            # 本事务之间并发 begin 建的 run 会按盘上旧标题排 job, 其
            # finish 用旧标题行 INSERT OR REPLACE 把改名静默回滚、新路径
            # 文件当 moved-out 孤儿清掉 —— BEGIN IMMEDIATE 与 begin 的
            # create_run_guarded 串行化, 复查看到就放弃
            if ctx.db.active_run(pid) is not None:
                ctx.db.rollback()
                return ("retry", "存在活动 run — 先 finish/cancel 再和解"
                        "(避免发布回滚改名; 本项保留待处理)")
            if pub is not None:
                # 发布层: 改 H1 + 更新 DB(content 引用不动) + 移文件。
                # H1 改写栅栏感知(_rename_h1, P13); 标题插值走 lambda/拼接
                # —— 用户标题里的反斜杠曾是 regex 替换转义
                content = _rename_h1(pub["content"], proposed)
                ctx.db.conn.execute(
                    "UPDATE page SET title=?, path=? "
                    "WHERE partition_id=? AND page_id=?",
                    (proposed, new_path, pid, page_id))
                ctx.db.save_page_content(pid, page_id, content)
                moves.append((pub, content, new_path))
            # 子页/后代: 派生路径变了(父标题目录段) —— 以各发布行的真实
            # path 为旧位置, 同步 DB 行与文件
            # c050: 后代行一次取齐 —— 曾每后代一条 SELECT(顶层页改名 = 数百
            # 次顺序查询, 每次都拖整页稿体 blob); UPDATE 因各行新路径不同保留
            # 逐条(值参差, executemany 无益)
            changed_desc = []
            for p in pages:
                cpid = str(p.get("id"))
                if cpid == page_id:
                    continue
                newp = str(p.get("path") or "")
                if not newp or old_paths.get(cpid) == newp:
                    continue
                changed_desc.append((cpid, newp))
            if changed_desc:
                pubs = {str(r2["page_id"]): r2
                        for r2 in ctx.db.published_pages_in(
                            pid, [c for c, _ in changed_desc])}
                for cpid, newp in changed_desc:
                    cpub = pubs.get(cpid)
                    if cpub is None:
                        continue
                    ctx.db.conn.execute(
                        "UPDATE page SET path=? WHERE partition_id=? AND page_id=?",
                        (newp, pid, cpid))
                    moves.append((cpub, cpub["content"], newp))
            ctx.db.commit()
        except Exception:
            ctx.db.rollback()
            raise
        # 路径集取一次共用(阶段 A 后的 DB 已全部指向新路径)
        paths_base = set(ctx.db.published_paths(pid))
        for row, content, target in moves:
            _move_published_file(ctx, plan, row, content, target,
                                 moved, missing, all_paths=paths_base)
        save_plan(ctx, plan)
        _refresh_indexes(ctx)
        ctx.db.log("manual", "reconcile_title",
                   f"{page_id}: {old_title} -> {proposed} "
                   f"({len(moved)} files moved"
                   + (f", {len(missing)} 不在旧路径未动" if missing else "")
                   + ")")
    msg = (f"“{old_title}”→“{proposed}”; 文件 →{new_path}"
           + (f", 移动 {len(moved)} 个文件" if not dry_run and moved else "")
           + (f", {len(missing)} 个不在旧路径(未动)"
              if not dry_run and missing else "")
           + (" (dry-run)" if dry_run else ""))
    return "applied", msg


def _refresh_indexes(ctx: Ctx) -> None:
    """改名/移动后重算发布索引(_index.yaml / repowiki-metadata.json /
    _module.yaml) —— docstring 承诺过"重算索引", 曾留给下一次 finish。"""
    try:
        from . import publish
        part = ctx.partition()
        pid = part["id"]
        pages = ctx.db.published_pages(pid)
        modules = ctx.db.modules(pid)
        publish._write_indexes(ctx, part, pages, modules, "manual")
        # r7 #46: generation-state 同步刷新(cmd_delete 同口径)—— 改名后
        # 它曾携带旧页路径直到下次 finish, Qoder 导入面读到旧位置
        publish._write_generation_state(ctx, part, {"id": "manual"}, pages)
    except Exception as e:   # noqa: BLE001 — 索引尽力而为, 不挡改名
        from .util import info
        info(f"note: 索引重算失败({e}) — 下次 finish 会重写")


def _apply_card_conflict(ctx: Ctx, payload: dict, dry_run: bool) -> tuple:
    """冲突卡片排进 regenerate 队列(单卡重写, 事实以行级证据为准)。

    返回 (status, msg): applied | rejected(计划里没有) | retry(有活动 run,
    保留 pending 等下次)。"""
    from .planner import load_plan, plan_cards, plan_hash, plan_pages
    card_name = str(payload.get("card") or "")
    plan = load_plan(ctx)
    cards = plan_cards(plan)
    card = next((c for c in cards if str(c.get("title")) == card_name
                 or str(c.get("id")) == card_name), None)
    if card is None:
        return "rejected", f"计划里没有卡片 {card_name!r} — 拒绝"
    pid = ctx.partition()["id"]
    if ctx.db.active_run(pid) is not None:
        return ("retry", "存在活动 run — 先 finish/cancel 再和解"
                "(避免队列交叉; 本项保留待处理)")
    if dry_run:
        return "applied", f"卡片“{card_name}”将重写 (dry-run)"
    # c036: 检查与建 run 同窗口可被并发 begin 钻入 —— 守卫式创建
    run_id, raced = ctx.db.create_run_guarded(
        pid, "regenerate", "retry", "INCREMENT",
        "DOCUMENT_GENERATING", ctx.commit,
        plan_hash(plan_pages(plan), cards),
        {"locale": ctx.locale, "reason": "card_conflict"},
        style=str(plan.get("style") or ""))
    if run_id is None:
        return ("retry", f"和解建 run 时发现并发活动 run {raced['id']}"
                f"({raced['state']}) — 本项保留待处理, 稍后重试和解")
    from .planner import card_job
    # 规范化(kind='card'/card_kind/ordinal 950)在 db.queue_jobs
    ctx.db.queue_jobs(run_id, [card_job(card, ctx.locale)],
                      style=str(plan.get("style") or ""))
    ctx.db.log(run_id, "reconcile_card", card_name)
    return "applied", f"卡片“{card_name}”排进 run {run_id} 重写 — `pwiki next`"
