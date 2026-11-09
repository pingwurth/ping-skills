"""引擎: run 生命周期(begin/next/submit/fail/skip/regenerate/pause/status/finish)。

移植 qwiki cmd_* (2308-3815), 关键升级:
  - next 用事务认领(claim_jobs)+ 租约, 取代"重置全部 processing"的协作约定
    —— 并行 writer 安全; submit/fail/skip 带 claim token 匹配, 失配走孤儿稿。
  - submit 门禁按 job 盖章的风格执行(gates.gate_item); --report 摄入
    D22 标题勘误/D7 卡片冲突进 reconcile 队列, key_topics 进活术语表。
  - finish: LINING(按风格硬门禁)→ publish → snapshot(P5 模块, 惰性导入)。
"""

from __future__ import annotations

import json
import os
import re
import sys
import uuid

from . import config
from .config import Ctx, now_iso
from .util import posix, die, info


def _jobs_summary(db, run_id: str) -> dict:
    counts = db.pending_counts(run_id)
    total = sum(counts.values())
    return {"total": total, **counts}


def _json_list(raw):
    """JSON 列表列的容错解码 —— 单一实现在 util.json_list(engine 消费点
    与 publish/snapshot 共用; `source_files: null` 曾以 'null'(truthy)
    入库, 绕过 `json.loads(x or '[]')` 守卫让 next/submit 裸 TypeError)。"""
    from .util import json_list
    return json_list(raw)


# --------------------------------------------------------------------------- #
# init
# --------------------------------------------------------------------------- #

def cmd_init(args) -> int:
    ctx = Ctx(args).load()
    cfg = {
        "repo": ctx.repo,
        "locale": ctx.locale,
        "profile": getattr(args, "profile", "standard"),
        "style": getattr(args, "style", config.DEFAULT_STYLE),
        "export_style": getattr(args, "export_style", "legacy"),
        "lease_minutes": config.DEFAULT_LEASE_MINUTES,
    }
    import json as _json
    os.makedirs(ctx.root, exist_ok=True)
    ctx.ensure_publish_tree()   # P1: 骨架随 init 落位, 首次 finish 不再要 --yes
    with open(os.path.join(ctx.root, config.CONFIG_NAME), "w",
              encoding="utf-8") as fh:
        _json.dump(cfg, fh, ensure_ascii=False, indent=2)
    part = ctx.partition()
    print(f"initialized: {ctx.root}")
    print(f"  repo={ctx.repo}  branch={part['branch']}  locale={ctx.locale}")
    print(f"  profile={cfg['profile']}  style={cfg['style']}  "
          f"export={cfg['export_style']}")
    info(f"hint: 把 {ctx.root} 加进 .gitignore(pwiki 状态不入库)")
    print("next: pwiki scan → pwiki modules → pwiki plan --themes|--seed")
    return config.EXIT_OK


# --------------------------------------------------------------------------- #
# begin
# --------------------------------------------------------------------------- #

def cmd_begin(args) -> int:
    from .planner import (card_job, load_plan, page_path_for, plan_cards,
                          plan_hash, plan_modules, plan_pages, validate_plan)

    ctx = Ctx(args).load()
    part = ctx.partition()
    plan = load_plan(ctx)
    profile = ctx.config.get("profile", "standard")
    errors, _ = validate_plan(ctx, plan, profile=profile)
    if errors:
        for e in errors:
            print(f"  error: {e}", file=sys.stderr)
        die("plan 校验失败 — 先修 wiki_plan.yaml(validatePlanConfig 门禁)",
            config.EXIT_GATE)
    style = str(plan.get("style") or ctx.style)

    pages, cards, modules = plan_pages(plan), plan_cards(plan), plan_modules(plan)
    modules_by_id = {m.get("id"): m for m in modules}
    ph = plan_hash(pages, cards)
    ctx.ensure_publish_tree()   # P1: 与 init 同源落骨架(begin/update 皆然)
    active = ctx.db.active_run(part["id"])
    if active and not args.force:
        s = _jobs_summary(ctx.db, active["id"])
        print(f"run {active['id']} 已在跑({active['state']}), 续用")
        print(f"  jobs: {s['completed']}/{s['total']} completed, "
              f"{s['failed']} failed, {s['pending']} pending")
        print("next: pwiki next")
        return config.EXIT_OK
    if active and args.force:
        if active["state"] == "LINING":
            # finish 正在校验/发布: 强取消后它的终态写入曾把 CANCELLED
            # 复活成 COMPLETED/PARTLY_COMPLETED, 还把分区基线错记到被取消
            # 的 run 头上 —— 等 finish 出结果再 --force
            die(f"run {active['id']} 正在 finish(LINING) — 等 finish 完成/"
                "失败后再 begin --force", config.EXIT_PRECOND)
        # 取消写带状态前置(P8): LINING 预检与 UPDATE 之间被 finish 抢先置
        # LINING 时如实报错 —— publish 不在 CANCELLED run 下跑
        if not ctx.db.update_run_guarded(
                active["id"], config.CANCELABLE_RUN_STATES,
                state="CANCELLED", finished_at=now_iso()):
            die(f"run {active['id']} 状态已被并发操作改变(如进入 finish) — "
                "重试 `pwiki begin --force`", config.EXIT_PRECOND)
        ctx.db.log(active["id"], "force_cancelled", "superseded by --force begin")
        ctx.db.release_all_claims(active["id"])
        info(f"已取消旧 run {active['id']}")

    commit = ctx.commit
    if not commit and args.require_git:
        die("不是 git 仓库(NOT_SOURCE_CONTROL_FAILED)", config.EXIT_PRECOND)
    # --missing: 恢复跑 —— 只排队未发布且未跳过的项; finish 的 kept 规则原样
    # 重发布已发布项。队列先算清楚再 create_run: 无旧 run / 无缺失两条早退
    # 路径不再留下 0 任务的僵尸活动 run(曾把后续裸 begin 卡进"续用"分支)。
    queue_pages, queue_cards = pages, cards
    if getattr(args, "missing", False):
        last = ctx.db.last_run(part["id"])
        if not last:
            # c086 同族: 无旧 run 是调用顺序的前置不满足(3), 不是门禁拒绝
            # (2) —— 编排方按 2 会误判成内容问题
            die("没有可恢复的旧 run", config.EXIT_PRECOND)
        published = ctx.db.published_ids(part["id"])
        # 跳过集看全部分区 run, 不只 last_run —— update/regenerate 的
        # INCREMENT run 成为 last_run 后, 显式跳过的页面不该复活重排
        skipped = ctx.db.skipped_page_ids(part["id"])
        queue_pages = [p for p in pages
                       if str(p.get("id")) not in published
                       and str(p.get("id")) not in skipped]
        queue_cards = [c for c in cards
                       if str(c.get("id")) not in published
                       and str(c.get("id")) not in skipped]
        if not queue_pages and not queue_cards:
            print("nothing missing — 计划项全部已发布或已跳过")
            return config.EXIT_OK
    # c036: 守卫式建 run —— 检查+插入同事务; 并发 begin 的输家吸收进赢家
    run_id, raced = ctx.db.create_run_guarded(
        part["id"], "generate",
        "resume" if getattr(args, "missing", False) else "start",
        "INCREMENT" if getattr(args, "missing", False) else "FULL",
        "DIRECTORY_GENERATING", commit, ph,
        {"locale": ctx.locale, "profile": profile}, style=style)
    if run_id is None:
        print(f"run {raced['id']} 已在跑({raced['state']}), 续用"
              "(并发 begin 竞态被吸收)")
        print("next: pwiki next")
        return config.EXIT_OK
    items = []
    for p in queue_pages:
        item = dict(p)
        item["path"] = posix(page_path_for(pages, item, ctx.locale,
                                          modules_by_id))
        items.append(item)
    for c in queue_cards:
        # 卡片 job 字面量统一走 planner.card_job(四个排队入口曾各抄一份)
        items.append(card_job(c, ctx.locale))
    ctx.db.queue_jobs(run_id, items, style=style)
    ctx.db.update_run(run_id, state="DOCUMENT_GENERATING", stage="document")
    ctx.db.log(run_id, "plan_applied",
               f"{len(items)} items queued (style={style})")
    print(f"run {run_id} started (generate/"
          f"{'INCREMENT' if getattr(args, 'missing', False) else 'FULL'}) — "
          f"DOCUMENT_GENERATING")
    print(f"  branch={part['branch']} locale={ctx.locale} "
          f"commit={commit or '-'} style={style}")
    print(f"  jobs: {len(items)} (knowledge first: sheets → cards → 页自底向上)")
    if not os.path.isfile(ctx.digest_path):
        info("hint: 先跑 `pwiki digest`, 所有 writer 共享一份仓库摘要")
    print("next: `pwiki next` 逐个取 job; `pwiki submit <id> --file PAGE.md` 交稿")
    return config.EXIT_OK


# --------------------------------------------------------------------------- #
# next(事务认领)
# --------------------------------------------------------------------------- #

def _job_payload(job, ctx: Ctx, run, all_jobs, summary) -> dict:
    """--json 载荷(D19: 路径绝对化, 批量派发由变量替换生成, 不许手抄)。"""
    payload = {
        "status": "ok",
        "run": {"id": run["id"], "state": run["state"],
                "generation_type": run["generation_type"],
                "style": job["style"] or ""},
        "job": {
            "page_id": job["page_id"], "title": job["title"],
            "kind": job["kind"], "parent": job["parent_id"],
            "module": job["module"], "sheet": job["sheet"],
            "card_kind": job["card_kind"], "path": job["path"],
            "prompt": job["prompt"],
            "source_files": _json_list(job["source_files"]),
            "article_type": job["article_type"], "slug": job["slug"],
            "objective": job["objective"],
            "modules": _json_list(job["modules"]),
            "scope": _json_list(job["scope"]),
            "required_sections": _json_list(job["required_sections"]),
            "title_check": bool(job["title_check"]),
            "pack_key": job["pack_key"], "attempts": job["attempts"],
            "style": job["style"] or "",
            # 认领令牌: submit/fail/skip 必须携带(token 失配 = 租约被回收)
            "claim_token": job["claim_token"],
        },
        "repo": ctx.repo,
        "digest": ctx.digest_path if os.path.isfile(ctx.digest_path) else None,
        "summary": summary,
    }
    return payload


def _fmt_job(job, ctx: Ctx, all_jobs, plan_pages_=None) -> str:
    kind_label = {"page": "页面", "module_sheet": "模块速查表",
                  "card": "知识卡"}.get(job["kind"], job["kind"])
    out = [f"job      : {job['page_id']}  ({kind_label}, "
           f"attempt {job['attempts']})",
           f"title    : {job['title']}"]
    if job["parent_id"]:
        out.append(f"parent   : {job['parent_id']}")
    if job["module"]:
        out.append(f"module   : {job['module']}" +
                   (f" · sheet: {job['sheet']}" if job["sheet"] else ""))
    if job["card_kind"]:
        out.append(f"card kind: {job['card_kind']}")
    out.append(f"path     : {job['path']}")
    out.append(f"style    : {job['style'] or '(默认)'}")
    srcs = _json_list(job["source_files"])
    out.append(f"sources  : {', '.join(srcs) or '(无 — 自选真实证据)'}")
    out.append(f"prompt   : {job['prompt'] or '(无)'}")
    if job["article_type"]:
        out.append(f"type     : {job['article_type']}")
    if job["objective"]:
        out.append(f"objective: {job['objective']}")
    # 计划要求的 H2 节列表: schema/sections 门禁在 submit 是静默的(仅
    # strict), 文本模式的 writer 曾整个 run 看不到节单, 写完才在 LINING
    # 被整批拒 —— payload(--json)一直带, 人类行同样要带
    req_sections = _json_list(job["required_sections"])
    if req_sections:
        out.append(f"sections : {', '.join(req_sections)}"
                   "  (计划要求的 H2 节; 缺节在 finish 是硬错)")
    mods = _json_list(job["modules"])
    if mods:
        out.append(f"modules  : {', '.join(mods)}")
    if job["title_check"]:
        out.append("title_check: 标题概念可能来自主题模板 —— 验证它在本仓库存在, "
                   "不存在则在 report 里提 title mismatch(D22)")
    # 兄弟大纲(保持主题不重叠); 已完成子页的 summary(聚合不复述)
    if plan_pages_ is None:
        try:
            from .planner import load_plan, plan_pages
            plan_pages_ = plan_pages(load_plan(ctx))
        except SystemExit:
            plan_pages_ = []
    sibs = [p for p in plan_pages_
            if str(p.get("parent") or "") == str(job["parent_id"] or "")
            and str(p.get("id")) != job["page_id"]
            and p.get("kind", "page") == "page"]
    if sibs:
        out.append("siblings :")
        for p in sibs[:12]:
            out.append(f"  - {p.get('title')} ({p.get('id')}) — "
                       f"{p.get('objective') or p.get('prompt') or ''}")
        out.append("  (兄弟页用相对链接指过去, 不要重复它们的内容)")
    kids = [j2 for j2 in all_jobs
            if j2["parent_id"] == job["page_id"] and j2["state"] == "completed"]
    if kids:
        out.append("children (已生成 — 聚合它们的 summary, 不要复述):")
        for k in kids:
            topics = _json_list(k["key_topics"])
            out.append(f"  - {k['title']} ({k['page_id']}): "
                       f"{k['summary'] or '(无 summary)'}"
                       + (f" · topics: {', '.join(topics)}" if topics else ""))
    # 知识优先: 本条目的模块综合记录是首要来源
    kw = []
    for j2 in all_jobs:
        if j2["kind"] == "module_sheet" and j2["module"] and (
                j2["module"] == job["module"] and job["module"]):
            p = os.path.join(ctx.root, j2["path"])
            if os.path.isfile(p) and p not in kw:
                kw.append(p)
    if kw:
        out.append("knowledge: " + ", ".join(kw))
        out.append("  (先用这些模块综合记录; 读源码只为取准行号与验证)")
    if os.path.isfile(ctx.digest_path):
        out.append(f"context  : {ctx.digest_path}")
    out.append(f"claim    : {job['claim_token'][:8]}…  "
               f"(submit 建议 --claim 携带完整令牌; 省略仅在首次认领"
               f"(attempts=1)时安全)")
    return "\n".join(out)


def cmd_next(args) -> int:
    ctx = Ctx(args).load()
    part = ctx.partition()

    def _say(msg: str):
        # --json 契约: stdout 只准是 JSON —— 人类可读状态行走 stderr。
        # 编排方 `next --json | json.loads(stdout)` 曾恰好在 in_flight/
        # complete 的切换点(轮询要检测的时刻)炸掉
        print(msg, file=sys.stderr if args.json else sys.stdout)

    run = ctx.db.active_run(part["id"])
    if not run:
        last = ctx.db.last_run(part["id"])
        if last and last["state"] in config.TERMINAL_STATES:
            _say(f"status: {last['state']} (run {last['id']}) — 无活动 run")
            info("用 `pwiki begin`(全量) 或 `pwiki update`(增量) 开跑")
        else:
            _say("status: 尚无 run — 先 `pwiki begin`")
        # c086: 契约表(config)把"无活动 run"归 EXIT_PRECOND —— 与
        # submit/pause/regenerate/finish 的同类前置一致, 别让编排方误判环境故障
        return config.EXIT_PRECOND
    if run["state"] == "PAUSED":
        _say(f"status: PAUSED (run {run['id']}) — `pwiki resume` 继续")
        return config.EXIT_PRECOND
    if run["state"] in ("WAITING", "INITIALIZING", "DIRECTORY_GENERATING"):
        # P23: create_run_guarded 与 queue_jobs 事务之间 job 表还是空的 ——
        # 只数 job 行会把该窗口当 "complete"(退出码 0), 轮询协调者提前
        # finish 又被 run 状态前置弹回("完成然后费解的前置失败"); 如实报
        # 排队中(与 PAUSED/无活动 run 同归 EXIT_PRECOND)
        _say(f"status: {run['state']} (run {run['id']}) — run 正在建立/"
             "排队, 稍后重试 next")
        if args.json:
            print(json.dumps({"status": "queueing", "run": run["id"],
                              "state": run["state"]}, ensure_ascii=False))
        return config.EXIT_PRECOND
    batch = max(1, getattr(args, "batch", 1) or 1)
    # 手编 config.json 的 null/非数值曾裸 TypeError 打死每个 next —— 全部
    # 并行 writer 停摆; as_int 容错回默认(同 ordinal/lease 家族)
    from .util import as_int
    lease = as_int(ctx.config.get("lease_minutes"),
                   config.DEFAULT_LEASE_MINUTES)
    claimed = ctx.db.claim_jobs(run["id"], count=batch, lease_minutes=lease)
    summary = _jobs_summary(ctx.db, run["id"])
    if not claimed:
        # pending=0 但仍有租约在写 —— 不是"已终态"(轮询驱动曾据此提前
        # finish, 又被 in-flight 前置挡回); 如实报 in_flight
        if summary["processing"]:
            _say(f"status: {summary['processing']} 个 job 在写(租约未回收), "
                 f"{summary['completed']}/{summary['total']} completed — "
                 "等 writer submit/fail/skip; 租约过期后 next 自动回收重发")
            if args.json:
                print(json.dumps({"status": "in_flight", "run": run["id"],
                                  "summary": summary}, ensure_ascii=False))
            return config.EXIT_OK
        _say(f"status: 全部 {summary['total']} 个 job 已终态"
             f"({summary['completed']} completed, {summary['failed']} failed, "
             f"{summary['skipped']} skipped) — `pwiki finish`")
        if args.json:
            print(json.dumps({"status": "complete", "run": run["id"],
                              "summary": summary}, ensure_ascii=False))
        return config.EXIT_OK
    # 三个重物在批内只算一次(每认领一单曾各来一遍: 全量 job 扫描含 content
    # blob + GROUP BY 计数 + 整个 plan YAML 重解析)。plan 解析只在人类行走
    # —— payload(--json)从不读它, 编排方高频轮询模式曾每单白付 ~24ms
    all_jobs = ctx.db.jobs_meta(run["id"])
    if args.json:
        payloads = [_job_payload(j, ctx, run, all_jobs, summary)
                    for j in claimed]
        print(json.dumps(payloads if batch > 1 else payloads[0],
                         ensure_ascii=False, indent=2))
    else:
        try:
            from .planner import load_plan, plan_pages
            plan_pages_ = plan_pages(load_plan(ctx))
        except SystemExit:
            plan_pages_ = []
        for job in claimed:
            print(_fmt_job(job, ctx, all_jobs, plan_pages_))
            print()
    info(f"写完后: pwiki submit <id> --file <md> [--report report.json] "
         f"(一次认领了 {len(claimed)} 个)")
    return config.EXIT_OK


# --------------------------------------------------------------------------- #
# submit
# --------------------------------------------------------------------------- #

def _load_report(report_path):
    """writer --report 的单一解析入口。

    cmd_submit 曾双解析同一文件且键枚举各持一份: _rep_summary 只认精确
    键, _ingest_report 认别名 —— 别名拼写(files_read_beyond 等)进了
    run_log 却漏出 job 行报告。这里读一次, 两个消费方共用同一 dict。"""
    if not report_path:
        return None
    if not os.path.isfile(report_path):
        # 显式给了 --report 却不在盘上: 曾静默跳过 —— D22/D7/uncertainties
        # 通道整体丢失, DB 行还记着 phantom report_file(审计口径失真)
        info(f"report 文件不存在: {report_path} — 报告通道未摄入"
             "(确认 writer 的输出路径)")
        return None
    try:
        with open(report_path, encoding="utf-8") as fh:
            report = json.load(fh)
    except (OSError, ValueError) as e:
        info(f"report 解析失败({e}) — 忽略")
        return None
    return report if isinstance(report, dict) else None


def _ingest_report(ctx: Ctx, run, job, report: dict) -> list:
    """已解析的 writer 报告 → reconcile 队列(D22/D7)+ 活术语表。返回摘要行。"""
    if not report:
        return []
    pid = ctx.partition()["id"]
    added = []
    for tm in report.get("title_mismatch") or report.get("title_mismatches") or []:
        if isinstance(tm, dict) and tm.get("proposed"):
            rid = ctx.db.reconcile_add(pid, "title_mismatch", {
                "page_id": job["page_id"], "old": job["title"],
                "proposed": str(tm.get("proposed")),
                "evidence": str(tm.get("evidence") or tm.get("grep") or "")},
                run_id=run["id"])
            added.append(f"D22 改名提案 #{rid}: {tm.get('proposed')}")
    for cc in report.get("card_conflict") or report.get("card_conflicts") or []:
        if isinstance(cc, dict):
            rid = ctx.db.reconcile_add(pid, "card_conflict", {
                "page_id": job["page_id"],
                "card": str(cc.get("card") or ""),
                "card_line": str(cc.get("card_line") or ""),
                "evidence": str(cc.get("evidence") or cc.get("file_lines") or "")},
                run_id=run["id"])
            added.append(f"D7 卡片冲突 #{rid}: {cc.get('card')}")
    # c026: SKILL.md 规则 9 声明的报告通道全部入账 —— files_read_beyond_sources
    # 与 uncertainties 没有 reconcile 动作, 但必须被记录(run_log 审计)而不是
    # 静默丢弃; 全文已由 set_job_report 落库(report_file + 内容摘要)
    beyond = report.get("files_read_beyond_sources") \
        or report.get("files_read_beyond") or []
    if isinstance(beyond, list) and beyond:
        ctx.db.log(run["id"], "files_read_beyond",
                   f"{job['page_id']}: {', '.join(str(b) for b in beyond[:20])}")
        added.append(f"额外读文件 {len(beyond)} 个(已入 run_log 审计)")
    unc = report.get("uncertainties") or []
    if isinstance(unc, list) and unc:
        ctx.db.log(run["id"], "uncertainties",
                   f"{job['page_id']}: {' | '.join(str(u) for u in unc[:10])}")
        added.append(f"不确定点 {len(unc)} 条(已入 run_log, 复盘时看)")
    return added


def _orphan_save(ctx: Ctx, page_id: str, content: str) -> str:
    """claim token 失配时的孤儿稿保存 —— 工作永不丢。

    c034: page_id 来自计划(唯一人工编辑面), 只被校验过非空 —— 含 '/' 或
    以 '/' 开头的 id 会把孤儿稿写到 work/orphans 之外(最高到绝对路径)。
    越界 id 一律落安全名(不 die: 孤儿保存是"稿件不丢"的最后通道)。"""
    safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", str(page_id))
    d = os.path.join(ctx.root, "work", config.ORPHAN_SUBMIT_DIR)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{safe_id}-{uuid.uuid4().hex[:6]}.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return path


def cmd_submit(args) -> int:
    from . import gates
    from .planner import load_plan, mod_titles_for, plan_modules

    ctx = Ctx(args).load()
    part = ctx.partition()
    # 先读稿再进一切状态守卫("稿件不丢"曾只对 completed 守卫兑现):
    # 无活动 run(begin --force 刚取消)/LINING/未知 job 的 die 曾发生在
    # 读稿前 —— stdin 交稿被消费后整篇蒸发, 连 orphan 目录都进不去。
    # 读稿本体 = util.read_submission(submit/write 共用)
    from .util import read_submission
    content = read_submission(args.file).strip() + "\n"

    run = ctx.db.active_run(part["id"])
    if not run:
        saved = _orphan_save(ctx, args.page_id, content)
        # run 已消失(如被 begin --force 取消)—— 审计走 manual 频道
        ctx.db.log("manual", "orphan_submit",
                   f"{args.page_id}: 无活动 run, 稿件存 {saved}")
        die(f"无活动 run — 先 `pwiki begin`; 稿件已保存 {saved}",
            config.EXIT_PRECOND)
    if run["state"] == "LINING":
        # c037: finish 正在 LINING 校验本批 completed 内容 —— 此刻 submit
        # (尤其 --force)会换掉被校验的稿件; 等 finish 出结果再动。
        # 先读稿再挡: stdin 稿曾在此连 orphan 目录都进不去
        saved = _orphan_save(ctx, args.page_id, content)
        ctx.db.log(run["id"], "orphan_submit",
                   f"{args.page_id}: run LINING 中, 稿件存 {saved}")
        die(f"run 正在 LINING(finish 校验中) — 等 finish 完成 or 失败后再提交"
            f"(稿件已保存 {saved})", config.EXIT_PRECOND)
    job = ctx.db.job(run["id"], args.page_id)
    if job is None:
        saved = _orphan_save(ctx, args.page_id, content)
        ctx.db.log(run["id"], "orphan_submit",
                   f"{args.page_id}: 未知 job, 稿件存 {saved}")
        die(f"未知 job: {args.page_id} (run {run['id']}) — 稿件已保存 {saved}",
            config.EXIT_PRECOND)

    if job["state"] == "completed" and not args.force:
        saved = _orphan_save(ctx, args.page_id, content)
        ctx.db.log(run["id"], "orphan_submit",
                   f"{args.page_id}: job 已 completed, 稿件存 {saved}")
        die(f"job {args.page_id} 已 completed(重做用 `pwiki regenerate`) — "
            f"稿件已保存 {saved}", config.EXIT_PRECOND)

    # 认领匹配: --claim 携带 next 发放的令牌(并行 writer 必传; 令牌失配 =
    # 租约已被回收/重认领 → 孤儿稿路径)。省略时按单写便利模式取当前令牌,
    # 但只在"从未被换发过"(attempts==1)时安全 —— 重认领后行内令牌可能
    # 属于别的 writer, 过期 writer 省略 --claim 会顶掉在写 writer 的稿
    supplied = getattr(args, "claim", None)
    # token 的最终取值在 gate_item 之后按重读的行状态决定(见下)
    if supplied is None and job["state"] == "processing" \
            and int(job["attempts"] or 0) > 1:
        saved = _orphan_save(ctx, args.page_id, content)
        ctx.db.log(run["id"], "orphan_submit",
                   f"{args.page_id}: 重认领后省略 --claim, 稿件存 {saved}")
        die(f"job {args.page_id} 已被重新认领过(attempts={job['attempts']}) — "
            "行内令牌可能属于其他 writer; 用你最近一次 `pwiki next` 输出的"
            f"令牌加 --claim 重交(稿件已保存 {saved}; 令牌丢失则等租约过期"
            "后重新 next 认领)", config.EXIT_PRECOND)
    if supplied is not None and not ctx.db.match_claim(
            run["id"], args.page_id, supplied):
        saved = _orphan_save(ctx, args.page_id, content)
        ctx.db.log(run["id"], "orphan_submit",
                   f"{args.page_id}: claim token 失配, 稿件存 {saved}")
        die(f"claim token 失配(租约已被回收并可能被其他 writer 认领) — "
            f"稿件已保存 {saved}, 重新 `pwiki next` 取新令牌",
            config.EXIT_PRECOND)

    # P31: 有效样式链与 finish 同序(job > run > plan > ctx) —— 曾缺 plan
    # 层: 未盖章行 submit 按 ctx、finish 按 plan 各判各的, 双重门禁互斥。
    # 排队路径都盖章, 只有手编/旧库行会走到 plan 兜底(惰性解析)
    style = str(job["style"] or run["style"] or "")
    if not style:
        try:
            style = str(load_plan(ctx).get("style") or ctx.style)
        except SystemExit:
            style = str(ctx.style)
    mod_titles = []
    # 计划 YAML 只为解模块标题而解析 —— 卡片(12-60 张/run)从不带
    # module, 白付 ~24-80ms/张; 无模块的 job 整段跳过
    if _json_list(job["modules"]) or job["module"]:
        try:
            mod_titles = mod_titles_for(job, plan_modules(load_plan(ctx)))
        except SystemExit:
            pass

    errs, warns = gates.gate_item(content, job, ctx.repo, style,
                                  phase="submit", mod_titles=mod_titles)
    # submit 阶段只拒绝真实性硬错(路径/范围/H1/frontmatter), 形状警告放行
    for w in warns:
        info(f"warn: {w}")
    if warns:
        info("形状/尺寸问题 submit 时是警告, finish(LINING)时按风格升错误级 — "
             "尽早修省一轮重写")
    problems = errs
    if args.lenient:
        # lenient 放宽 H1 契约与"至少一条引用"两条; 引用真实性(path/range)
        # 与卡片 frontmatter 永不放宽。前缀集合单一来源在 gates。
        problems = [p for p in problems
                    if not gates.is_lenient_droppable(p)]
    # gate_item 做了数秒文件 I/O —— 以"现在"的行状态决定令牌与去向, 不信
    # 入场快照: job 曾在门禁期间被其他 writer 认领, 旧快照说"非 processing"
    # 便走无 token UPDATE, 直接顶掉在写 writer 的租约(其 submit 随即孤儿化)
    # —— 正是租约系统要防的交叉覆盖
    job_now = ctx.db.job(run["id"], args.page_id)
    state_now = job_now["state"] if job_now is not None else ""
    if state_now == "completed" and not args.force:
        saved = _orphan_save(ctx, args.page_id, content)
        ctx.db.log(run["id"], "orphan_submit",
                   f"{args.page_id}: 提交途中已被他稿完成, 稿件存 {saved}")
        die(f"job {args.page_id} 已 completed(重做用 `pwiki regenerate`) — "
            f"稿件已保存 {saved}", config.EXIT_PRECOND)
    if supplied is None:
        if state_now == "processing" and int(job_now["attempts"] or 0) > 1:
            saved = _orphan_save(ctx, args.page_id, content)
            ctx.db.log(run["id"], "orphan_submit",
                       f"{args.page_id}: 门禁期间被重认领, 稿件存 {saved}")
            die(f"job {args.page_id} 在提交期间被其他 writer 重新认领"
                f"(attempts={job_now['attempts']}) — 稿件已保存 {saved}; "
                "带 --claim(你最近一次 next 的令牌)重交, 或等租约过期后"
                "重新 next 认领", config.EXIT_PRECOND)
        token = job_now["claim_token"] if state_now == "processing" else ""
        if token:
            # 省略 --claim 采纳行内令牌只在单写便利模式下安全(attempts==1
            # 分不清"同一 writer"与"从不转发令牌的编排方派来的别人")——
            # 采纳必留审计行, 误用可回溯
            ctx.db.log(run["id"], "claim_adopted",
                       f"{args.page_id}: submit 省略 --claim, 采纳行内令牌"
                       "(单写便利; 并行 writer 必传 --claim)")
    else:
        token = supplied

    if problems and not args.force:
        for p in problems:
            print(f"  rejected: {p}", file=sys.stderr)
        if state_now == "processing":
            # 回 pending 即释放认领(set_job_state 已清 token/租约)—— 无需
            # 也无法续租; 曾在此调 extend_lease, 恒为 0 行 no-op
            ctx.db.set_job_state(run["id"], args.page_id, "pending",
                                 error="; ".join(problems[:3]),
                                 claim_token=token)
        ctx.db.log(run["id"], "submit_rejected",
                   f"{args.page_id}: {problems[:3]}")
        die(f"submit 被拒({args.page_id}) — 修稿重交(--force 强过, "
            "--lenient 放宽)", config.EXIT_GATE)

    # summary/key_topics 先算好, 随终写进同一守卫事务(P9): 曾在终写之后的
    # 一句裸自动提交 —— 竞争 finish 在两步之间置 LINING 并快照 jobs(),
    # 空摘要被发布并基线化, 且行永不自愈(kept-merge 只带目录字段)
    summary = (getattr(args, "summary", "") or "").strip()
    if not summary and job["kind"] == "page":
        # 栅栏整体先剥(gates.strip_code_fences)再取首段 —— 手搓的
        # startswith('```') 只挡"段落以栅栏开头", 栅栏贴着首段时示例
        # 代码漏进 240 字摘要, 随 prompt 注入后续 writer 的上下文
        body = gates.strip_code_fences(
            re.sub(r"<cite>.*?</cite>", "", content, flags=re.S))
        for para in re.split(r"\n\s*\n", body):
            para = para.strip()
            if para and not para.startswith("#"):
                summary = re.sub(r"\s+", " ", para)[:240]
                break
    topics = [t.strip() for t in (getattr(args, "key_topics", "") or "").split(",")
              if t.strip()]

    # 终写走守卫事务: finish 的 LINING 置位与其 jobs() 快照之间曾是无守卫
    # 窗口 —— 此处迟到一步的 completed 写入会落在快照之后, run 报完成而
    # 该页从未发布(单事务内核 run 状态, 与 finish 的 UPDATE run 串行化)
    verdict = ctx.db.complete_job_guarded(
        run["id"], args.page_id, content, claim_token=token,
        summary=summary,
        key_topics=json.dumps(topics, ensure_ascii=False))
    if verdict == "busy":
        saved = _orphan_save(ctx, args.page_id, content)
        ctx.db.log(run["id"], "orphan_submit",
                   f"{args.page_id}: finish 进行中(LINING/已终态), 稿件存 {saved}")
        die(f"run 正在 finish(LINING/发布)或已终态 — 提交与快照竞态, "
            f"稿件已保存 {saved}; 等 finish 出结果后重交", config.EXIT_PRECOND)
    if verdict == "token_lost":
        saved = _orphan_save(ctx, args.page_id, content)
        die(f"提交时租约刚好被回收 — 稿件已保存 {saved}", config.EXIT_PRECOND)

    # key_topics 进活术语表(摘要/话题本体已随终写同事务落行, 见上)
    if topics:
        ctx.db.glossary_add_terms(ctx.partition()["id"], topics,
                                  source_page_id=args.page_id)
    # writer 报告摄入(D22/D7 → reconcile 队列; c026: 全通道入账);
    # 单次解析喂两个消费方(入账别名与 job 行报告同一份 dict)
    _rep_path = getattr(args, "report", None)
    _rep = _load_report(_rep_path)
    for line in _ingest_report(ctx, run, job, _rep):
        info("reconcile: " + line)
    _rep_summary = {}
    if _rep:
        beyond = _rep.get("files_read_beyond_sources") \
            or _rep.get("files_read_beyond") or []
        if beyond:
            _rep_summary["files_read_beyond_sources"] = beyond
        if _rep.get("uncertainties"):
            _rep_summary["uncertainties"] = _rep["uncertainties"]
    ctx.db.set_job_report(run["id"], args.page_id, {
        # 只有真读到的报告才记 report_file —— 路径拼错时曾照记不误,
        # 审计口径声称摄入了从未读过的报告
        "report_file": (_rep_path or "") if _rep else "", **_rep_summary})

    # knowledge-first: sheets/cards 提交即落盘, 后续 writer 立即可读
    # (ping 实跑曾因只在 finish 落盘而需要 dump 变通)
    if (job["kind"] == "module_sheet" or config.is_card_kind(job["kind"])) \
            and job["path"]:
        # c045 同款原子写: 提前落盘的知识文件被中断时不再留半份假完成
        from .util import atomic_write
        atomic_write(os.path.join(ctx.root, job["path"]), content)
    # 引用文件计数走正则(gate_item 已做过全套校验 —— 曾为一行日志把每个被引
    # 文件再整读一遍)
    n_cited = len({c["path"] for c in gates.extract_citations(content)})
    ctx.db.log(run["id"], "page_submitted",
               f"{args.page_id}: {len(content.splitlines())} lines, "
               f"{n_cited} files cited")
    s = _jobs_summary(ctx.db, run["id"])
    print(f"submitted {args.page_id} ({len(content.splitlines())} lines, "
          f"{n_cited} files cited, style={style})")
    print(f"progress: {s['completed']}/{s['total']} completed, "
          f"{s['pending']} pending")
    if s["pending"] == 0 and s["processing"]:
        # P24: 与 cmd_next 的 in_flight 修法同源 —— 只看 pending 会把
        # "剩 N 个在写"报成全终态, 协调者提前 finish 又被 in-flight 前置
        # 弹回(退出码 3)
        print(f"pending 已清, 仍有 {s['processing']} 个 in-flight job"
              "(租约未回收) — 等 writer submit/fail/skip 后再 "
              "`pwiki finish`")
    elif s["pending"] == 0:
        print("全部 job 已终态 — `pwiki finish` 做 LINING + 发布")
    return config.EXIT_OK


# --------------------------------------------------------------------------- #
# fail / skip / regenerate / pause / resume / cancel
# --------------------------------------------------------------------------- #

def _active(ctx) -> tuple:
    part = ctx.partition()
    run = ctx.db.active_run(part["id"])
    if not run:
        die("无活动 run", config.EXIT_PRECOND)
    return part, run


def _lining_guard(run, action: str) -> None:
    """c037 家族守卫(P8): finish 的 LINING/发布窗口内禁止一切改 run 状态
    的操作 —— cancel/regenerate --force/pause 曾各自漏挡, publish/snapshot
    在被取消的 run 下完整跑完, 基线错记到 CANCELLED run 头上, finish 自己
    的终态写入再被"不复活已取消 run"弹回。恢复路径永远是重跑
    `pwiki finish`(接受 LINING 前置)。"""
    if run["state"] == "LINING":
        die(f"run {run['id']} 正在 finish(LINING) — 等 finish 完成/失败后再"
            f"{action}(卡死的 LINING 重跑 `pwiki finish` 恢复)",
            config.EXIT_PRECOND)


def _fail_skip_guard(ctx: Ctx, run, job, args) -> str:
    """fail/skip 共用守卫: LINING 窗口 / 幽灵 id / completed 撤销 /
    重认领后令牌收紧。

    返回应使用的 claim token(c034 便利模式仅在首次认领时采纳行内令牌)。"""
    if run["state"] == "LINING":
        # P15: finish 的结论取自 LINING 置位前的计数快照 —— 此间 fail/skip
        # 把已计入的 job 改成终局失败态, 重处理从 run 结果里静默消失(本应
        # PARTLY_COMPLETED); submit 对同一串行化是拒收的(c037)
        _lining_guard(run, "fail/skip")
    if job is None:
        # c022: 幽灵 id 曾"成功"退出(no-token UPDATE 0 行也返回 True)
        die(f"未知 job: {args.page_id} (run {run['id']})", config.EXIT_PRECOND)
    if job["state"] == "completed" and not getattr(args, "force", False):
        # 与 submit 的 completed 守卫同款: 完成稿不经确认不许降级
        # failed/skipped(曾一个手滑把整批已完成项踢出发布集)
        die(f"job {args.page_id} 已 completed — 重做用 `pwiki regenerate`, "
            "确认撤销完成态加 --force", config.EXIT_PRECOND)
    supplied = getattr(args, "claim", None)
    if supplied is None and job["state"] == "processing" \
            and int(job["attempts"] or 0) > 1:
        die(f"job {args.page_id} 已被重新认领过(attempts={job['attempts']}) — "
            "行内令牌可能属于其他 writer; 带 --claim(你最近一次 next 输出的"
            "令牌)再 fail/skip", config.EXIT_PRECOND)
    return supplied if supplied is not None else (
        job["claim_token"] if job["state"] == "processing" else "")


def cmd_fail(args) -> int:
    ctx = Ctx(args).load()
    part, run = _active(ctx)
    job = ctx.db.job(run["id"], args.page_id)
    token = _fail_skip_guard(ctx, run, job, args)
    ok = ctx.db.set_job_state(run["id"], args.page_id, "failed",
                              error=args.error or "unspecified",
                              claim_token=token)
    if ok is False:
        die("claim token 失配 — 该 job 已被回收/重认领", config.EXIT_PRECOND)
    ctx.db.log(run["id"], "page_failed", f"{args.page_id}: {args.error}")
    print(f"marked {args.page_id} failed: {args.error}")
    return config.EXIT_OK


def cmd_skip(args) -> int:
    ctx = Ctx(args).load()
    part, run = _active(ctx)
    job = ctx.db.job(run["id"], args.page_id)
    token = _fail_skip_guard(ctx, run, job, args)
    ok = ctx.db.set_job_state(run["id"], args.page_id, "skipped",
                              notes=args.reason or "", claim_token=token)
    if ok is False:
        die("claim token 失配 — 该 job 已被回收/重认领", config.EXIT_PRECOND)
    ctx.db.log(run["id"], "page_skipped", f"{args.page_id}: {args.reason}")
    print(f"skipped {args.page_id}")
    return config.EXIT_OK


def cmd_regenerate(args) -> int:
    from .planner import (card_job, load_plan, page_path_for, plan_hash,
                          plan_modules, plan_pages, plan_cards)
    ctx = Ctx(args).load()
    part, run = _active(ctx)
    _lining_guard(run, f" regenerate {args.page_id}")
    plan = load_plan(ctx)
    pages = plan_pages(plan)
    cards = plan_cards(plan)
    modules_by_id = {m.get("id"): m for m in plan_modules(plan)}
    item = next((p for p in pages if str(p.get("id")) == args.page_id), None)
    kind = "page"
    if item is None:
        item = next((c for c in cards if str(c.get("id")) == args.page_id), None)
        kind = "card"
    if item is None:
        die(f"计划里没有 {args.page_id}", config.EXIT_PRECOND)
    # c040: 与 pause/cancel 同款租约守卫 —— 取消+释放会把在制 writer 的
    # submit 全部挤成孤儿稿; 确要强撤加 --force(稿件不丢)
    leased = ctx.db.leased_jobs(run["id"])
    if leased and not getattr(args, "force", False):
        ids = ", ".join(j["page_id"] for j in leased)
        die(f"有 writer 仍持有租约({ids}) — regenerate 会取消当前 run; "
            f"等 submit/fail/skip 后再试, 或 --force 强撤(在制稿件走孤儿稿)",
            config.EXIT_PRECOND)
    # 取消整个 run 会把"已完成未发布"的其他 job 搁置(发布只认活动 run,
    # 曾静默取消后 2 份完成稿只能靠 LLM 全部重写找回)—— 挡一道: 先 finish
    # 发布它们再重做; 确要搁置加 --force(草稿仍留在旧 run 的 job 行里)
    counts = ctx.db.pending_counts(run["id"])
    tgt = ctx.db.job(run["id"], args.page_id)
    done_others = counts["completed"] - \
        (1 if tgt is not None and tgt["state"] == "completed" else 0)
    if done_others > 0 and not getattr(args, "force", False):
        die(f"当前 run 有 {done_others} 个已完成未发布的 job — regenerate "
            "会取消整个 run 把它们搁置(重排=全部 LLM 重写)。先 `pwiki "
            "finish` 发布, 再 begin + regenerate 重做单项; 确认搁置加 "
            "--force", config.EXIT_PRECOND)
    # 取消写带状态前置(P8): 同 cmd_cancel —— 守卫与 UPDATE 之间被 finish
    # 抢先置 LINING 时如实报错
    if not ctx.db.update_run_guarded(
            run["id"], config.CANCELABLE_RUN_STATES,
            state="CANCELLED", finished_at=now_iso()):
        die(f"run {run['id']} 状态已被并发操作改变(如进入 finish) — "
            f"重试 `pwiki regenerate {args.page_id}`", config.EXIT_PRECOND)
    ctx.db.release_all_claims(run["id"])
    ctx.db.log(run["id"], "run_cancelled_by_regenerate",
               f"superseded by regenerate {args.page_id}: "
               f"pending={counts['pending']} processing={counts['processing']} "
               f"completed_stranded={done_others}")
    info(f"已取消 run {run['id']}(pending {counts['pending']}, "
         f"processing {counts['processing']}, 已完成未发布搁置 "
         f"{done_others}) — 单项重走 {args.page_id}")
    style = str(plan.get("style") or ctx.style)
    # c036: 取消与重建之间可能有并发 begin 插入 —— 守卫式重建, 输家如实报错
    run_id, raced = ctx.db.create_run_guarded(
        part["id"], "regenerate", "retry", "INCREMENT",
        "DOCUMENT_GENERATING", ctx.commit,
        plan_hash(pages, cards),
        {"locale": ctx.locale}, style=style)
    if run_id is None:
        die(f"取消旧 run 后有并发 run {raced['id']}({raced['state']})插入 — "
            f"重试 `pwiki regenerate {args.page_id}`", config.EXIT_PRECOND)
    # path 必须在此显式派生(plan 条目不带 path; 与 begin/update 同口径) ——
    # 曾以 path='' 入队, finish 打开 <root> 直接 IsADirectoryError 且复崩
    if kind == "card":
        job_item = card_job(item, ctx.locale)
    else:
        job_item = dict(item)
        job_item["path"] = posix(page_path_for(pages, job_item, ctx.locale,
                                            modules_by_id))
    ctx.db.queue_jobs(run_id, [job_item], style=style)
    print(f"run {run_id} (regenerate) — 单项重做 {args.page_id}")
    print("next: pwiki next")
    return config.EXIT_OK


def cmd_pause(args) -> int:
    ctx = Ctx(args).load()
    part, run = _active(ctx)
    _lining_guard(run, "pause")
    leased = ctx.db.leased_jobs(run["id"])
    if leased:
        ids = ", ".join(j["page_id"] for j in leased)
        die(f"有 writer 仍持有租约({ids}) — 等它们 submit/fail/skip "
            "或租约过期后再 pause", config.EXIT_PRECOND)
    ctx.db.update_run(run["id"], state="PAUSED")
    ctx.db.log(run["id"], "run_paused", "")
    print(f"paused run {run['id']} — `pwiki resume` 继续")
    return config.EXIT_OK


def cmd_resume(args) -> int:
    ctx = Ctx(args).load()
    part, run = _active(ctx)
    if run["state"] != "PAUSED":
        print(f"run {run['id']} 状态 {run['state']}, 无需 resume")
        return config.EXIT_OK
    ctx.db.update_run(run["id"], state="DOCUMENT_GENERATING")
    ctx.db.log(run["id"], "run_resumed", "")
    print(f"resumed run {run['id']} — `pwiki next` 继续")
    return config.EXIT_OK


def cmd_cancel(args) -> int:
    ctx = Ctx(args).load()
    part, run = _active(ctx)
    _lining_guard(run, "cancel")
    # c040: 在制 writer 的租约先挡一道 —— 释放后其 submit 全走孤儿稿;
    # 确要强撤加 --force(稿件不丢, 落 work/orphan-submits/)
    leased = ctx.db.leased_jobs(run["id"])
    if leased and not getattr(args, "force", False):
        ids = ", ".join(j["page_id"] for j in leased)
        die(f"有 writer 仍持有租约({ids}) — 等 submit/fail/skip 或 pause 后 "
            f"再 cancel; 强撤加 --force(在制稿件走孤儿稿)", config.EXIT_PRECOND)
    # 先置 CANCELLED 再释放认领(与 cmd_regenerate/begin --force 同序):
    # 曾先 release —— 释放与置态之间的并发 next 仍看到活动 run, 又认领
    # 新 job, cancel 完成后该 job 被困在已取消的 run 里(stranded processing)
    # 取消写带状态前置(P8, c022 同族): LINING 守卫与 UPDATE 之间被并发
    # finish 抢先置 LINING 时如实报错, 不把发布中的 run 拖去 CANCELLED
    if not ctx.db.update_run_guarded(
            run["id"], config.CANCELABLE_RUN_STATES,
            state="CANCELLED", finished_at=now_iso()):
        die(f"run {run['id']} 状态已被并发操作改变(如进入 finish) — "
            "重读后重试", config.EXIT_PRECOND)
    released = ctx.db.release_all_claims(run["id"])
    ctx.db.log(run["id"], "run_cancelled", f"released {released} claims")
    print(f"cancelled run {run['id']} (释放 {released} 个认领)")
    return config.EXIT_OK


# --------------------------------------------------------------------------- #
# status
# --------------------------------------------------------------------------- #

def cmd_status(args) -> int:
    from .planner import progress_pct
    ctx = Ctx(args).load()
    part = ctx.partition()
    n_published = sum(ctx.db.published_kind_counts(part["id"]).values())
    run = ctx.db.active_run(part["id"]) or ctx.db.last_run(part["id"])
    if args.quiet:
        print(run["state"] if run else "NONE")
        return config.EXIT_OK
    base = {
        "root": ctx.root, "repo": ctx.repo,
        "branch": part["branch"], "locale": part["locale"],
        "published": n_published,
        "baseline": {"snapshot": part["published_snapshot_id"],
                     "commit": part["baseline_commit"],
                     "run": part["baseline_run_id"]},
    }
    if not run:
        base["run"] = None
        print(json.dumps(base, ensure_ascii=False, indent=2) if args.json
              else f"no run yet — published={n_published}")
        return config.EXIT_OK
    summary = _jobs_summary(ctx.db, run["id"])
    pct = progress_pct(run["state"], run["generation_type"],
                       summary["total"], summary["completed"])
    leased = [{"page_id": j["page_id"],
               "lease_expires_at": j["lease_expires_at"]}
              for j in ctx.db.leased_jobs(run["id"])]
    base["run"] = {"id": run["id"], "kind": run["kind"],
                   "state": run["state"], "style": run["style"],
                   "generation_type": run["generation_type"],
                   "progress": pct, "summary": summary,
                   "leased": leased}
    if args.json:
        print(json.dumps(base, ensure_ascii=False, indent=2))
        return config.EXIT_OK
    print(f"run      : {run['id']} ({run['kind']}/{run['generation_type']}, "
          f"style={run['style'] or '-'})")
    print(f"state    : {run['state']}  progress: {pct}%")
    print(f"jobs     : {summary['completed']}/{summary['total']} completed, "
          f"{summary['pending']} pending, {summary['failed']} failed, "
          f"{summary['skipped']} skipped, {summary['processing']} in-flight")
    if leased:
        print(f"leases   : " + ", ".join(
            f"{l['page_id']}(至 {l['lease_expires_at'][11:19]}Z)"
            for l in leased))
    print(f"published: {n_published} items "
          f"(baseline {part['published_snapshot_id'] or '-'})")
    pending_reconcile = ctx.db.reconcile_pending(part["id"])
    if pending_reconcile:
        print(f"reconcile: {len(pending_reconcile)} 项待处理 — `pwiki reconcile --all`")
    return config.EXIT_OK


# --------------------------------------------------------------------------- #
# finish(LINING → publish → snapshot)
# --------------------------------------------------------------------------- #

def cmd_finish(args) -> int:
    ctx = Ctx(args).load()
    part = ctx.partition()
    run = ctx.db.active_run(part["id"])
    if not run:
        die("无活动 run — 先 `pwiki begin`", config.EXIT_PRECOND)
    counts = ctx.db.pending_counts(run["id"])
    if counts["pending"] or counts["processing"]:
        die(f"仍有 {counts['pending']} pending / {counts['processing']} in-flight "
            "job — 全部终态后才能 finish(或 fail/skip 收尾)",
            config.EXIT_PRECOND)

    from . import gates
    from .planner import load_plan, plan_modules
    plan = load_plan(ctx)
    # LINING 与发布合成按**每行自己的盖章风格**(job['style'] > run > plan
    # > config, 与 submit 同序) —— 曾统一重取当前 plan: begin 后手改
    # plan style 会让 submit(按盖章)与 finish(按 plan)两套门禁互斥,
    # 按 concise 提示词写的整批页被 deep 口径 LINING 拒绝。行缺盖章时用
    # 下方 fallback_style。
    fallback_style = str(run["style"] or plan.get("style") or ctx.style)
    # c022: LINING 置位带状态前置条件 —— 并发 cancel/regenerate --force 已
    # 把 run 置 CANCELLED 时, 无守卫 UPDATE 会把已取消的 run 复活成 LINING
    # (LINING ∈ 活动态集合) → 双活动 run, 僵尸 run 永卡 LINING。
    # LINING 本身也合法: d19 拒绝/发布中断都把 run 留在 LINING 等重跑 finish
    if not ctx.db.update_run_guarded(
            run["id"], ("DOCUMENT_GENERATING", "DOCUMENT_FAILED", "LINING"),
            state="LINING", stage="lining"):
        die(f"run {run['id']} 状态已被并发操作改变(如 cancel/regenerate) — "
            "重读后重试", config.EXIT_PRECOND)
    ctx.db.log(run["id"], "lining_started", f"style={fallback_style}")

    # LINING: 对每个 completed job 按风格跑硬门禁。jobs 单读直传 publish
    # (SELECT * 含稿体 blob —— finish 曾在数秒内整队读两遍)。
    # 循环在 try 里: 门禁层抛非 OSError 异常曾裸奔, run 卡死 LINING
    from .planner import mod_titles_for
    all_mods = plan_modules(plan)
    lining_failed = []
    all_jobs = ctx.db.jobs(run["id"])
    try:
        for job in all_jobs:
            if job["state"] != "completed" or not job["content"]:
                continue
            # P32: mod_titles 与 submit 同源 —— 曾只有 submit 传, 模块提及
            # 检查的 strict 升级层是死代码, 规则在本应强制执行的 LINING
            # 从不生效(页发布而模块分配静默未兑现)
            errs, _warns = gates.gate_item(
                job["content"], job, ctx.repo,
                str(job["style"] or fallback_style), phase="lining",
                mod_titles=mod_titles_for(job, all_mods))
            errs = [e for e in errs
                    if not gates.is_auto_fixable(e)]   # 发布层钳位(见 publish)
            if args.lenient and not args.force:
                # lenient 契约与 submit 同源(LENIENT_DROP_PREFIXES 单一集合):
                # 只放宽 H1 契约与"至少一条引用"。finish 曾把 --lenient 当
                # --force 全放 —— citation/path(引用真实性)这类承诺永不
                # 放宽的错误也照样发布; 全放请用 --force
                errs = [e for e in errs
                        if not gates.is_lenient_droppable(e)]
            if errs and not args.force:
                lining_failed.append((job["page_id"], errs))
                ctx.db.set_job_state(run["id"], job["page_id"], "pending",
                                     error="lining: " + "; ".join(errs[:3]))
    except SystemExit:
        raise
    except Exception as e:   # noqa: BLE001
        # 守卫式落态(P7 同族): 无守卫 UPDATE 会把并发 cancel 的 CANCELLED
        # 复活成 DOCUMENT_FAILED(∈ 活动态)
        ctx.db.update_run_guarded(
            run["id"], ("LINING",), state="DOCUMENT_FAILED",
            error=f"lining 失败: {type(e).__name__}: {e}",
            finished_at=now_iso())
        ctx.db.log(run["id"], "lining_failed",
                   f"{type(e).__name__}: {e}")
        die(f"LINING 执行失败({type(e).__name__}: {e}) — run 已标 "
            "DOCUMENT_FAILED; 修好后重跑 `pwiki finish`", config.EXIT_ENV)
    if lining_failed:
        for pid, errs in lining_failed[:20]:
            print(f"  lining rejected {pid}: {errs[0]}", file=sys.stderr)
        ctx.db.update_run(run["id"], state="DOCUMENT_FAILED",
                          error=f"lining rejected {len(lining_failed)} items",
                          finished_at=now_iso())
        ctx.db.log(run["id"], "lining_failed", f"{len(lining_failed)} items")
        die(f"LINING 拒绝 {len(lining_failed)} 项 — 修稿后重新 submit, "
            "再 `pwiki finish`(DOCUMENT_FAILED 可恢复; --lenient 放宽"
            "H1/无引用两类, --force 全放)",
            config.EXIT_GATE)

    # 发布 + 快照(同包同装, 直接导入 —— 不可达的 ImportError 守卫只会
    # 把真实导入错误伪装成"P5 未落地")
    from . import publish, snapshot

    # c043: 发布/快照的失败不该是裸 traceback —— 落 DOCUMENT_FAILED
    # (可恢复: 修环境后重跑 finish, kept 规则原样重发布)并给可诊断的错。
    # 曾只捕 OSError: sqlite3.OperationalError(锁竞争)与门禁/合成层的
    # 其他异常照样裸奔, run 永卡 LINING(c037 会孤儿化所有并发 submit)
    def _mark_publish_failed(detail: str) -> None:
        """P7 配套: 落 DOCUMENT_FAILED 用守卫式写 —— 无守卫 UPDATE 会把
        并发 cancel 的 CANCELLED 复活成 DOCUMENT_FAILED(∈ 活动态) →
        僵尸活动 run(c022 同族)。"""
        if not ctx.db.update_run_guarded(
                run["id"], ("LINING",), state="DOCUMENT_FAILED",
                error=f"publish/snapshot 失败: {detail}",
                finished_at=now_iso()):
            die(f"run {run['id']} 已不处于 LINING(被并发 cancel?) — "
                "发布中断且状态已被并发操作改走", config.EXIT_PRECOND)

    try:
        needs_review, pages = publish.publish_run(
            ctx, run, plan, fallback_style,
            prune=getattr(args, "prune", False),
            yes=getattr(args, "yes", False), jobs=all_jobs)
        reason = ("incremental_update"
                  if run["generation_type"] == "INCREMENT" else "full_generation")
        snapshot_id = snapshot.snapshot_run(ctx, run, plan, pages=pages)
    except SystemExit as e:
        # P7: publish/snapshot 里的 die()(如同路径冲突)抛的是 SystemExit
        # —— 只捕 Exception 时它带着"页面表已提交"的残局逃逸: run 卡
        # LINING、0 文件落盘、并发 submit 被 c037 孤儿化。EXIT_PRECOND 的
        # die 是前置确认类(D19 要 --yes), 按既定契约留在 LINING 等重跑;
        # 其余与 Exception 同罚(可恢复: 修好重跑 finish)
        if e.code == config.EXIT_PRECOND:
            raise
        _mark_publish_failed(f"die({e.code})")
        ctx.db.log(run["id"], "publish_failed", f"SystemExit({e.code})")
        die(f"发布/快照失败(exit {e.code}) — run 已标 DOCUMENT_FAILED; "
            "修好后重跑 `pwiki finish`", config.EXIT_ENV)
    except Exception as e:   # noqa: BLE001 — LINING 后的一切失败都要落状态
        _mark_publish_failed(f"{type(e).__name__}: {e}")
        ctx.db.log(run["id"], "publish_failed",
                   f"{type(e).__name__}: {e}")
        die(f"发布/快照失败({type(e).__name__}: {e}) — run 已标 "
            "DOCUMENT_FAILED; 修好后重跑 `pwiki finish`", config.EXIT_ENV)
    final = "PARTLY_COMPLETED" if (counts["failed"] or counts["skipped"]
                                   or needs_review) else "COMPLETED"
    # 终态写入带状态复核: begin --force 刚取消本 run 时(守卫放开的历史
    # 窗口), 曾无条件把 CANCELLED 复活成 COMPLETED/PARTLY_COMPLETED
    cur = ctx.db.conn.execute(
        "UPDATE run SET state=?, stage='done', finished_at=? "
        "WHERE id=? AND state='LINING'",
        (final, now_iso(), run["id"]))
    if cur.rowcount == 0:
        die(f"run {run['id']} 已不处于 LINING(被并发 cancel/force?) — "
            "不复活已取消的 run", config.EXIT_PRECOND)
    ctx.db.log(run["id"], "run_finished",
               f"state={final} snapshot={snapshot_id} reason={reason}")

    n_by_kind = ctx.db.published_kind_counts(part["id"])
    # qwiki bug16: 计数取发布层, 不取 run job 数(sbs 恢复跑曾报 38/0/0)
    print(f"finish: {final} (reason={reason})")
    print(f"published: {sum(n_by_kind.values())} items — "
          + ", ".join(f"{k}×{v}" for k, v in sorted(n_by_kind.items())))
    print(f"snapshot : {snapshot_id} (commit {ctx.commit or '-'})")
    if needs_review:
        print(f"needs_review: {len(needs_review)} 项(见 work/validation-report.json)")
    print(f"search it: pwiki search \"关键词\"")
    return config.EXIT_OK
