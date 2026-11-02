"""子命令处理器: crp.py 各子命令的业务编排。

每个处理器接收 argparse.Namespace, 返回 (Decision, EmitContext); 状态读写
一律经 StateStore.locked() 保护。可预期错误以 StepError 抛出(cli 统一转协议块)。

submit 统一流水: 锁 → 漂移探测 → 种类核对 → 校验(全有或全无) → 幂等检查
(sha256) → 入账 → decide_next 路由 → 恰一个协议块。
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import config, decisions, gitops, ledger, overlay, report, scope as scope_mod, verify
from .cli import EmitContext, load_state_or_error
from .logutil import setup_logger
from .models import Decision, NextCommand, ResumeOption, Route, State
from .state import StateStore, create_run_dir, list_runs, repo_slug, resolve_run_dir


class StepError(Exception):
    """可预期错误; 携带足以直接生成协议块的 Decision。"""

    def __init__(self, decision: Decision) -> None:
        self.decision = decision
        super().__init__(decision.summary)

    # -- 便捷构造 ----------------------------------------------------------- #
    @classmethod
    def state_error(cls, summary: str, question: Optional[str] = None) -> "StepError":
        """状态/提交错误(exit 3): state 缺失、参数缺失、错序提交等。"""
        return cls(Decision(status="failed", exit_code=config.EXIT_STATE, summary=summary,
                            route=Route.ASK_USER, reason=summary,
                            question=question or summary))

    @classmethod
    def exec_error(cls, summary: str, question: Optional[str] = None,
                   artifacts: Optional[list] = None) -> "StepError":
        """脚本执行错误(exit 2): 环境缺失、文件不可读等。"""
        return cls(Decision(status="failed", exit_code=config.EXIT_ERROR, summary=summary,
                            route=Route.ASK_USER, reason=summary,
                            question=question or summary, artifacts=artifacts or []))


# --------------------------------------------------------------------------- #
# 公共助手
# --------------------------------------------------------------------------- #
def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _ctx(state: State, run_dir: Path) -> decisions.RouteCtx:
    return decisions.RouteCtx(run_dir=run_dir)


def _touch(state: State) -> None:
    state.updated_at = _now()


def _run_ask(run_dir: Path, unfinished: Optional[list[dict]] = None) -> Decision:
    """存在未完成运行时, start 前的唯一询问(防 429 后重跑覆盖)。

    unfinished: 调用方已算好的未完成列表(P6 回修: cmd_start 算完一行后
    原版立刻重算同一 list_runs —— 全量重解析每个 state.json 双倍功)。"""
    runs = unfinished if unfinished is not None else [
        r for r in list_runs(Path.cwd()) if not r.get("done") and not r.get("corrupt")]
    listing = "\n".join(f"  - {r['run_id']} [{r.get('mode','?')}/{r.get('phase','?')}]" for r in runs[:5])
    return Decision(
        status="needs_input", exit_code=config.EXIT_CONTINUE,
        summary=f"存在 {len(runs)} 个未完成运行", route=Route.ASK_USER,
        reason="防止中断运行被新跑覆盖",
        question=(f"当前仓库有未完成的审查运行:\n{listing}\n"
                  "续跑最新一个, 还是终止本次 start?(并行新运行可用 start --force)"),
        resume=[
            ResumeOption("resume_run", "续跑最新运行",
                         script=config.ENTRY_FILENAME,
                         params=["resume", "--run-dir", str(run_dir)]),
            ResumeOption("terminate", "终止本次 start"),
        ])


def _scope_escape_ask(sc: dict, run_dir: Path) -> Decision:
    probes = "\n".join(f"  `{p['cmd']}` -> rc={p['rc']} empty={p['empty']}"
                       for p in (sc.get("escape_hatch") or {}).get("probes", []))
    return Decision(
        status="needs_input", exit_code=config.EXIT_CONTINUE,
        summary="范围解析全空 —— 逃逸口", route=Route.ASK_USER,
        reason="Phase 0 全部梯级失败/为空, 禁止静默空审",
        question=(f"所有 diff 源都为空或失败:\n{probes}\n"
                  "请给出审查目标(分支/PR 号/区间/路径/repo), 或终止。"),
        resume=[
            ResumeOption("choice", "提供目标",
                         script=config.ENTRY_FILENAME,
                         params=["answer", "--run-dir", str(run_dir),
                                 "--key", "scope", "--choice", "choice", "--value", "TARGET"],
                         note="TARGET 替换为用户给定的目标"),
            ResumeOption("terminate", "终止"),
        ])


def _overwrite_ask(run_dir: Path) -> Decision:
    """start --run-dir 指向已有运行时的防覆盖询问(P2 回修)。

    原版防覆盖守卫仅在无 --run-dir 时生效 —— 显式传入即跳过, 中断运行的
    台账/选票/已完成运行的报告状态被全新 state 无提示覆写。
    """
    return Decision(
        status="needs_input", exit_code=config.EXIT_CONTINUE,
        summary="start --run-dir 指向已有运行", route=Route.ASK_USER,
        reason="防止既有运行被新跑覆盖",
        question=(f"--run-dir {run_dir} 下已有 state.json。\n"
                  "续跑该运行, 还是放弃本次 start?(确需覆盖用 start --force)"),
        resume=[
            ResumeOption("resume_run", "续跑该运行",
                         script=config.ENTRY_FILENAME,
                         params=["resume", "--run-dir", str(run_dir)]),
            ResumeOption("terminate", "放弃本次 start"),
        ])


def _drift_ask(recorded_head: Optional[str], current_head: Optional[str], run_dir: Path) -> Decision:
    return Decision(
        status="needs_input", exit_code=config.EXIT_CONTINUE,
        summary="仓库 HEAD 已漂移", route=Route.ASK_USER,
        reason="中断期间被审仓库前进 —— 记录的范围是审查基准, 漂移须显式裁决",
        question=(f"运行记录 HEAD={recorded_head}, 当前 HEAD={current_head}。\n"
                  "重导范围(保留台账) / 沿用原范围(候选锚点将标记 stale) / 终止?"),
        resume=[
            ResumeOption("new_scope", "重导范围(保留台账)",
                         script=config.ENTRY_FILENAME,
                         params=["answer", "--run-dir", str(run_dir),
                                 "--key", "drift", "--choice", "new_scope"]),
            ResumeOption("resume_old_scope", "沿用原范围(标 stale)",
                         script=config.ENTRY_FILENAME,
                         params=["answer", "--run-dir", str(run_dir),
                                 "--key", "drift", "--choice", "resume_old_scope"]),
            ResumeOption("terminate", "终止"),
        ])


def _drift_gate(state: State, run_dir: Path) -> Optional[Decision]:
    """漂移探测: HEAD 移动 → ask_user; 工作树变化 → 仅警告(由调用方并入 summary)。"""
    cwd = (state.repo or {}).get("cwd") or str(Path.cwd())
    if state.scope.get("escape_hatch"):
        # P10 回修: 范围逃逸期不装配 drift 待决 —— 范围未定谈不上漂移,
        # 且覆写 pending_question 会丢掉 scope 待决(互踩主源)
        return None
    current = scope_mod.current_signature(cwd)
    drift = scope_mod.drift_between(scope_mod.signature_of(state.repo), current)
    if drift == "head_moved":
        state.pending_question = {"key": "drift", "at": _now()}
        return _drift_ask(state.repo.get("head"), current.get("head"), run_dir)
    if drift == "working_tree_changed":
        state.repo["wt_drift_note"] = "工作树在运行期间有新改动(范围基准不变)"
    return None


def _resolve_entry_point(args, include_done: bool = False) -> tuple[Path, StateStore]:
    run_dir = resolve_run_dir(getattr(args, "run_dir", None), Path.cwd(),
                              include_done=include_done)
    if run_dir is None:
        raise StepError.state_error(
            "未找到可续跑运行", "当前仓库无运行记录 —— 先执行 crp.py start")
    return run_dir, StateStore(run_dir)


# --------------------------------------------------------------------------- #
# start
# --------------------------------------------------------------------------- #
def _init_overlay_state(state: State, cwd: Path) -> None:
    """overlay 初始化(cmd_start 与 cmd_answer 共用; 回修 c025/c057/c001)。

    - 一切发现/信任判定在仓库顶层执行(scope 路径即顶层相对 —— 子目录植入的
      overlay 不得因此被判 trusted, A3/A4 不被绕过);
    - 需要 overlay 工作时 phase=overlay 且 **awaiting="overlay"**(否则提交
      过不了种类核对 → 无限 exit-3 循环); 无 overlay 且无启发式门禁 → 直达 brief。
    """
    root = gitops.repo_root(cwd) or cwd
    overlay_path = overlay.discover(root)
    trust, basis = overlay.trust_of(state.scope, overlay_path)
    state.overlay = {"path": overlay_path, "trust": trust, "trust_basis": basis,
                     "exclusions_applied": [], "exclusions_suspended": [],
                     "gates": []}
    if overlay_path is None:
        gates = overlay.heuristic_gates(root)
        state.overlay["heuristic_gates"] = gates
        if not gates:
            state.phase, state.cursor["awaiting"] = "brief", "brief"
        else:
            # 有启发式候选 → 走 overlay 提取(LLM 查看实际执行内容并标注语义)
            # P9 回修(自食验收 P1): 同时置 phase —— 原版只置 awaiting, 中途
            # 调用(scope 答复重定目标)后相位/期待永久失谐, 路由器按相位发
            # candidates 而 submit 按 awaiting 拒收 → 逐字执行器 exit-3 活锁
            state.phase, state.cursor["awaiting"] = "overlay", "overlay"
    else:
        state.overlay["heuristic_gates"] = []
        state.phase, state.cursor["awaiting"] = "overlay", "overlay"


def cmd_start(args) -> tuple[Decision, EmitContext]:
    cwd = Path.cwd()
    mode = args.mode
    target = args.target
    fix_requested = bool(args.fix)

    # P2 回修: start --run-dir 指向已有运行 → 防覆盖(原版守卫仅在无 --run-dir
    # 时生效, 显式传入即无提示覆写中断运行/已完成运行的 state.json)
    if args.run_dir and not args.force:
        existing = Path(args.run_dir).resolve()
        if (existing / config.STATE_FILENAME).is_file():
            return _overwrite_ask(existing), EmitContext(run_dir=existing)

    if not gitops.is_git_repo(cwd):
        sc = {"escape_hatch": {"probes": [
            {"cmd": "git rev-parse --is-inside-work-tree", "rc": 1, "empty": True}]}}
        state = _new_state(cwd, mode, fix_requested, target, sc)
        state.pending_question = {"key": "scope", "at": _now()}
        run_dir = _place_state(args, state)
        return _scope_escape_ask(sc, run_dir), EmitContext(run_dir=run_dir, state=state)

    # 未完成运行防覆盖(429 中断后重跑是验收实测过的失败模式)
    if not args.force and not args.run_dir:
        unfinished = [r for r in list_runs(cwd) if not r.get("done") and not r.get("corrupt")]
        if unfinished:
            newest = Path(unfinished[0]["run_dir"])
            return _run_ask(newest, unfinished), EmitContext(run_dir=newest)

    # P6 回修: 一份 porcelain 快照贯穿(原版 start 全程跑 2-3 次
    # git status --porcelain -z -uall: 阶梯入口 / 重导 / 签名)
    porcelain = gitops.porcelain_z(cwd)
    sc = scope_mod.resolve_scope(cwd, target, porcelain=porcelain)
    state = _new_state(cwd, mode, fix_requested, target, sc, porcelain=porcelain)
    if sc.get("escape_hatch"):
        # P2 回修: 逃逸口 ask 落 pending_question(resume 侧据此重问, 见 cmd_resume)
        state.pending_question = {"key": "scope", "at": _now()}
    run_dir = _place_state(args, state)

    if sc.get("escape_hatch"):
        return _scope_escape_ask(sc, run_dir), EmitContext(run_dir=run_dir, state=state)

    _init_overlay_state(state, cwd)
    _touch(state)
    StateStore(run_dir).save(state)
    return decisions.decide_next(state, _ctx(state, run_dir)), EmitContext(run_dir=run_dir, state=state)


def _new_state(cwd: Path, mode: str, fix_requested: bool, target: Optional[str], sc: dict,
               porcelain: Optional[list[dict]] = None) -> State:
    sig = scope_mod.current_signature(cwd, porcelain=porcelain)
    return State(
        run_id="",  # 由 _place_state 落定
        created_at=_now(), updated_at=_now(),
        repo={"cwd": str(cwd), "root": str(gitops.repo_root(cwd) or cwd),
              "slug": repo_slug(cwd), "head": sig.get("head"),
              "wt_signature": sig.get("wt"), "is_git": gitops.is_git_repo(cwd)},
        mode=mode, fix_requested=fix_requested, target=target,
        phase="overlay", scope=sc,
    )


def _place_state(args, state: State) -> Path:
    """落定运行目录(新建或复用 --run-dir)并写盘。"""
    if args.run_dir:
        run_dir = Path(args.run_dir).resolve()
        StateStore(run_dir).ensure_layout()
    else:
        run_dir = create_run_dir(Path(state.repo["cwd"]))
    state.run_id = run_dir.name
    _touch(state)
    StateStore(run_dir).save(state)
    # P1 回修: start 的目录在 handler 内才存在 —— 落定即接上 run-dir 日志
    try:
        setup_logger("start", run_dir)
    except OSError:
        pass
    return run_dir


# --------------------------------------------------------------------------- #
# resume / next
# --------------------------------------------------------------------------- #
def cmd_resume(args) -> tuple[Decision, EmitContext]:
    # include_done(P2 回修): 唯一运行已完成时, 裸 resume 重发 finish 块与逐字
    # 报告(原版报错要求重新 start, done 后幂等面不传 --run-dir 不可达)
    run_dir, store = _resolve_entry_point(args, include_done=True)
    with store.locked():
        state, err = load_state_or_error(store, "resume 失败")
        if err is not None:
            return err, EmitContext(run_dir=run_dir)
        if state.is_done:
            # P12(c012): 修复陈旧 meta 边车 —— 终写在两 rename 间崩溃会留下
            # done=false 的 meta, done 运行不再 save 故永不自愈(list_runs 永远
            # 误读为未完成); resume 是天然的修复点
            meta_path = run_dir / config.META_FILENAME
            if meta_path.is_file():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    if isinstance(meta, dict) and meta.get("done") != state.is_done:
                        store.save(state)
                except (OSError, ValueError):
                    pass
            return decisions.decide_next(state, _ctx(state, run_dir)), \
                EmitContext(run_dir=run_dir, state=state)
        # P2 回修: 范围逃逸口存活时不得继续 —— 原版 resume 只看 phase, 一条普通
        # resume 即绕过 '禁止静默空审' 门禁在空范围上跑完整场审查
        if state.scope.get("escape_hatch"):
            state.pending_question = {"key": "scope", "at": _now()}
            _touch(state); store.save(state)
            return _scope_escape_ask(state.scope, run_dir), EmitContext(run_dir=run_dir, state=state)
        drift = _drift_gate(state, run_dir)
        if drift is not None:
            store.save(state)
            return drift, EmitContext(run_dir=run_dir, state=state)
        if not state.is_done:
            state.interrupted["count"] = state.interrupted.get("count", 0) + 1
            state.interrupted["last_at"] = _now()
        _touch(state)
        store.save(state)
        return decisions.decide_next(state, _ctx(state, run_dir)), EmitContext(run_dir=run_dir, state=state)


cmd_next = cmd_resume


# --------------------------------------------------------------------------- #
# submit
# --------------------------------------------------------------------------- #
def cmd_submit(args) -> tuple[Decision, EmitContext]:
    run_dir, store = _resolve_entry_point(args)
    kind = args.kind

    with store.locked():
        state, err = load_state_or_error(store, "submit 失败")
        if err is not None:
            return err, EmitContext(run_dir=run_dir)

        drift = _drift_gate(state, run_dir)
        if drift is not None:
            store.save(state)
            return drift, EmitContext(run_dir=run_dir, state=state)

        # ── 种类核对(路由器派生期待, 不看历史 → 错序自愈) ──
        expected = state.cursor.get("awaiting", "")
        legal = (kind == expected
                 or (kind == "carrier1" and expected == "candidates" and state.phase == "sweep"))
        if not legal:
            corrective = Decision(
                status="failed", exit_code=config.EXIT_STATE,
                summary=f"错序提交: 期待 {expected or '(无)'}, 收到 {kind}",
                route=Route.RESUME, reason="提交种类与状态机不符 —— 交回路由器自愈",
                route_params=["resume", "--run-dir", str(run_dir)])
            store.save(state)
            return corrective, EmitContext(run_dir=run_dir, state=state)

        # ── 载体 1: transcript 目录 / 压缩 journal 双模式(M5) ──
        if kind == "carrier1":
            from . import journal
            if not (args.transcript_dir or args.file):
                raise StepError.state_error("carrier1 需要 --transcript-dir 或 --file")
            transcript_dir: Optional[Path] = None
            compact_file: Optional[Path] = None
            js_counters: Optional[dict] = None
            if args.transcript_dir:
                transcript_dir = Path(args.transcript_dir)
            try:
                if transcript_dir is None:
                    # 内容嗅探(P4 回修: 移入 try —— 空文件 IndexError/不可读/
                    # 非 UTF-8 原版逃到 cli 兜底 exit-2 内部错误, 丢失重试引导)
                    sniff = Path(args.file).read_text(encoding="utf-8").strip()
                    lines = [l for l in sniff.splitlines() if l.strip()]
                    if lines and lines[0].startswith("{"):
                        compact_file = Path(args.file)  # 压缩 journal 模式
                    else:
                        transcript_dir = Path(lines[0].strip())
                        # 可选第二行: workflow 返回值计数 JSON —— parity 对照(分歧上报告)
                        if len(lines) > 1 and lines[1].strip().startswith("{"):
                            try:
                                js_counters = json.loads(lines[1])
                            except ValueError:
                                js_counters = None
                # ── 幂等键(P1 回修): 载体 1 同入共享幂等管线 —— 同轮同 journal
                # 字节重投 = 孤儿结果吸收, 不得双重入账(原版 sha256 恒空绕开) ──
                if transcript_dir is not None and (transcript_dir / "journal.jsonl").is_file():
                    digest_src = transcript_dir / "journal.jsonl"
                else:
                    digest_src = compact_file if compact_file is not None else Path(args.file)
                digest = hashlib.sha256(digest_src.read_bytes()).hexdigest()
                # P11(c004): carrier1 幂等键不含 round —— 常规种类含 round 防
                # 连续空 gap 轮误吞, 但 carrier1 的同一 journal 字节无论在哪个
                # 轮次重贴都是同一份工作; 含 round 使引擎侧重开 gap 轮后的
                # 陈旧重贴绕开吸收分支而双重入账(实证)。
                # P12: 含 scope_epoch —— drift new_scope 重环后的同字节 journal
                # 按新工作摄取(否则重置 round 后命中旧 idem 无限吸收 = 活锁,
                # 第二轮自食验收实证); 同范围内的 429 重贴仍吸收。
                idem = f"carrier1|{state.cursor.get('scope_epoch', 0)}|{digest}"
                dup = next((s for s in state.submissions
                            if s.get("idem") == idem and s.get("applied")), None)
                if dup is not None:
                    decision = decisions.decide_next(state, _ctx(state, run_dir))
                    decision.summary = f"重复提交已吸收(carrier1, sha256 相同) —— {decision.summary}"
                    store.save(state)
                    return decision, EmitContext(run_dir=run_dir, state=state)

                if transcript_dir is not None and (transcript_dir / "journal.jsonl").is_file():
                    note = journal.ingest_transcript(state, transcript_dir,
                                                     js_counters=js_counters)
                else:
                    compact_file = compact_file or Path(args.file)
                    note = journal.apply_compact(state, journal.load_compact(compact_file))
            except (ValueError, OSError, KeyError, IndexError) as exc:
                raise StepError.exec_error(
                    f"载体 1 journal 摄取失败: {exc}",
                    question=f"检查 transcript 目录/journal 文件后重试: {exc}")
            state.submissions.append({
                "seq": len(state.submissions) + 1, "kind": "carrier1",
                "file": str(args.transcript_dir or args.file), "sha256": digest,
                "idem": idem, "applied": True, "note": note,
            })
            _touch(state)
            store.save(state)
            return decisions.decide_next(state, _ctx(state, run_dir)), EmitContext(run_dir=run_dir, state=state)

        # ── 常规种类: 读文件 + 幂等 + 校验(全有或全无) + 应用 ──
        if not args.file:
            raise StepError.state_error(f"submit --kind {kind} 需要 --file")
        path = Path(args.file)
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise StepError.state_error(f"提交文件不可读: {path} ({exc})")
        digest = hashlib.sha256(raw).hexdigest()
        # 幂等键含范围代次+轮次: 同轮内字节相同重投 = 孤儿结果吸收; 跨轮同
        # 字节(如连续空的 gap 轮)是新工作, 不得误吞 —— 否则 gap 循环死锁;
        # scope_epoch 随 drift new_scope 递增(跨范围重环同字节按新工作摄取)
        idem = f"{kind}|{state.cursor.get('scope_epoch', 0)}|{state.cursor.get('round', 0)}|{digest}"
        dup = next((s for s in state.submissions
                    if s.get("idem") == idem and s.get("applied")), None)
        if dup is not None:
            # 字节相同的重投 = 无操作成功(孤儿结果幂等吸收)
            decision = decisions.decide_next(state, _ctx(state, run_dir))
            decision.summary = f"重复提交已吸收({kind}, sha256 相同) —— {decision.summary}"
            store.save(state)
            return decision, EmitContext(run_dir=run_dir, state=state)

        if kind == "brief":
            # P4 回修: strict 解码原版在 try 之外 —— 非 UTF-8 brief 得 exit-2
            # 内部错误, 与 JSON 路径同类错误的 exit-3 处理不一致
            try:
                data: object = raw.decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise StepError.state_error(f"brief 提交不是合法 UTF-8 文本: {exc}")
        else:
            try:
                data = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as exc:
                raise StepError.state_error(f"提交文件不是合法 JSON: {exc}")

        errors, apply_fn = _validate_kind(state, kind, data)
        if errors:
            raise StepError.state_error(
                "提交校验失败(全有或全无, 未入账):\n" + "\n".join(f"- {e}" for e in errors),
                question="按错误修正后重新提交; 协议路由不变。")
        note = apply_fn(state, run_dir) or ""
        state.submissions.append({
            "seq": len(state.submissions) + 1, "kind": kind, "file": str(path),
            "sha256": digest, "idem": idem, "applied": True, "note": note,
        })
        _touch(state)
        store.save(state)
        return decisions.decide_next(state, _ctx(state, run_dir)), EmitContext(run_dir=run_dir, state=state)


def _validate_kind(state: State, kind: str, data: object):
    """按种类校验并返回 (errors, apply_fn)。apply 仅在校验全过后调用(state, run_dir)。"""
    if kind == "overlay":
        errs = overlay.validate_overlay_file(data)

        def _apply_overlay(st: State, rd: Path) -> str:
            summary = json.dumps(overlay.apply_overlay(st, data), ensure_ascii=False)
            # phase 推进(P1 回修): 一律先 brief —— 门禁确认是 brief 之后的一次
            # 批量 ask_user(SKILL.md §4 / gates_question 契约); 有待确认门禁时
            # 由 _apply_brief 进 gates。原版有待确认门禁即先跳 gates, 而 gates
            # 答复又无条件转 carrier —— brief 阶段被整体跳过。
            st.phase, st.cursor["awaiting"] = "brief", "brief"
            return summary
        return errs, _apply_overlay

    if kind == "brief":
        errs = _validate_brief(data)
        return errs, lambda st, rd: _apply_brief(st, data)

    if kind == "carrier":
        errs = _validate_carrier(data)
        return errs, lambda st, rd: _apply_carrier(st, data)

    if kind == "candidates":
        errs = ledger.validate_candidates_file(data)
        return errs, lambda st, rd: _apply_candidates(st, data)

    if kind == "verdicts":
        errs = verify.validate_votes_file(data, state)
        return errs, lambda st, rd: _apply_verdicts(st, data)

    if kind == "details":
        errs = report.validate_details_file(data, state)
        return errs, lambda st, rd: _apply_details(st, data, rd)

    if kind == "fix-outcomes":
        errs = _validate_fix_outcomes(data, state)
        return errs, lambda st, rd: _apply_fix_outcomes(st, data)

    return [f"未知种类 {kind}"], None


def _validate_brief(data: object) -> list[str]:
    if not isinstance(data, str) or not data.strip():
        return ["brief 必须是非空 markdown 文本"]
    sections = len(re.findall(r"^##\s+\S", data, re.MULTILINE))
    if sections < config.BRIEF_MIN_SECTIONS:
        return [f"brief 的 ## 节数 {sections} < 六节契约 {config.BRIEF_MIN_SECTIONS}"
                "(六节: What/Intent/Targeted/Red lines/Verification/Angles; 区域 brief 可更多)"]
    return []


def _apply_brief(state: State, data: str) -> str:
    lines = data.count("\n") + 1
    warnings = []
    if lines > config.BRIEF_SOFT_LINE_LIMIT:
        warnings.append(f"line_count {lines} > {config.BRIEF_SOFT_LINE_LIMIT} (advisory)")
    # P5 回修: 删恒空/恒真占位键(sha256 真值在 submissions 幂等账本;
    # 节校验在 _validate_brief, 应用时必过 —— sections_ok 恒 True 无信息量)
    state.brief = {"file": f"{config.IN_SUBDIR}/02-brief.md",
                   "line_count": lines, "warnings": warnings}
    state.sweep_plan = decisions.build_sweep_plan(state.mode, state.overlay, state.scope)
    pending = overlay.pending_gate_question_count(state)
    if pending:
        state.phase, state.cursor["awaiting"] = "gates", "answer"
    else:
        state.phase, state.cursor["awaiting"] = "carrier", "carrier"
    return f"brief {lines} 行; 扫描计划 {len(state.sweep_plan['angles'])} 角度" + (f"; {warnings[0]}" if warnings else "")


def _validate_carrier(data: object) -> list[str]:
    if not isinstance(data, dict):
        return ["carrier 提交必须是 JSON 对象"]
    errs = []
    if data.get("carrier") not in (1, 2, 3):
        errs.append("carrier 必须是 1|2|3")
    if not str(data.get("reason") or "").strip():
        errs.append("reason 必须非空(载体留痕 —— 静默降级不可审计)")
    # P2 回修: 显式排除 bool —— isinstance(True, int) 为真, 原版 {'max_concurrent': true}
    # 过校验入库(对照 ledger.py 对 line/confidence 的同类处理)
    ma = data.get("max_agents")
    if ma is not None and (isinstance(ma, bool) or not isinstance(ma, int) or ma < 1):
        errs.append("max_agents 必须是正整数或缺省")
    mc = data.get("max_concurrent")
    if mc is not None and (isinstance(mc, bool) or not isinstance(mc, int) or mc < 1):
        errs.append("max_concurrent 必须是正整数或缺省")
    return errs


def _apply_carrier(state: State, data: dict) -> str:
    plan = state.sweep_plan or {}
    state.carrier = {"carrier": data.get("carrier"), "reason": data.get("reason"),
                     "max_agents": data.get("max_agents") or plan.get("max_agents"),
                     "max_concurrent": data.get("max_concurrent") or plan.get("max_concurrent"),
                     "degradations": []}
    state.phase = "sweep"
    state.cursor["round_kind"] = "sweep"
    state.cursor["pending_angles"] = list(plan.get("angles") or [])
    state.cursor["awaiting"] = "candidates"
    return f"carrier {data.get('carrier')}: {data.get('reason')}"


def _apply_candidates(state: State, data: dict) -> str:
    cursor = state.cursor
    round_no = cursor.get("round", 0) + 1  # 本轮(执行中)
    origin = cursor.get("round_kind", "sweep")
    result = ledger.ingest_candidates(state, data, origin=origin, round_no=round_no)

    # 从待回报角度中划掉已报的
    pending = [a for a in (cursor.get("pending_angles") or [])
               if a not in {i.get("angle") for i in data.get("items", [])}]
    cursor["pending_angles"] = pending

    if pending:
        cursor["awaiting"] = "candidates"
        return f"{len(result.fresh_ids)} fresh / {result.dup_count} dup; 剩余角度 {'/'.join(pending)}"

    # ── 本轮完成 ──
    cursor["round"] = round_no
    if state.mode == "quick" or origin == "fix_review":
        verify.finalize_without_verify(state)
        verify.finalize_round(state)
        if origin == "fix_review":
            state.fix["re_review_done"] = True
        state.phase, state.cursor["awaiting"] = "report", "details"
        return f"round {round_no} 完成(quick/复审 语义无验证): {len(result.fresh_ids)} fresh"
    verify.build_verify_queue(state, result.fresh_ids)
    if not cursor.get("pending_verify"):
        nxt = verify.finalize_round(state)
        if nxt == "gap":
            state.phase, state.cursor["round_kind"] = "sweep", "gap"
            state.cursor["pending_angles"] = [config.GAP_ANGLE_KEY]
            state.cursor["awaiting"] = "candidates"
            return f"round {round_no} 零新候选进入验证 —— 开启 gap 轮"
        state.phase, state.cursor["awaiting"] = "report", "details"
        return f"round {round_no} 完成, 收敛/达上限 —— 进入报告"
    state.phase, state.cursor["awaiting"] = "verify", "verdicts"
    return f"round {round_no}: {len(result.fresh_ids)} fresh 进入验证"


def _apply_verdicts(state: State, data: dict) -> str:
    outcome = verify.apply_votes(state, data.get("votes") or [])
    if state.cursor.get("pending_verify"):
        state.cursor["awaiting"] = "verdicts"
        return (f"{len(outcome.finalized)} 终裁 / {len(outcome.upgraded)} 升级补透镜; "
                f"剩 {len(state.cursor['pending_verify'])} 条待验证")
    nxt = verify.finalize_round(state)
    if nxt == "gap" and (state.carrier or {}).get("carrier") == 1:
        # P11(c023/c006): 载体 1 的 gap 循环在 workflow 内部完成(契约)——
        # 升级补透镜后不重开引擎侧 gap 轮(state.carrier 仍记 1 且
        # degradations 空, 审计会错误归属扫描位置); 注记上报告留痕
        state.accounting.setdefault("parity_notes", []).append(
            "升级补透镜后按载体 1 契约直接收口(未重开引擎 gap 轮)")
        nxt = "report"
    if nxt == "gap":
        state.phase, state.cursor["round_kind"] = "sweep", "gap"
        state.cursor["pending_angles"] = [config.GAP_ANGLE_KEY]
        state.cursor["awaiting"] = "candidates"
        return f"全部终裁完成 —— 开启 gap 轮(dry_streak={state.cursor.get('dry_streak', 0)})"
    state.phase, state.cursor["awaiting"] = "report", "details"
    return "全部终裁完成, 收敛/达上限 —— 进入报告"


def _apply_details(state: State, data: dict, run_dir: Path) -> str:
    """渲染报告落盘并推进到 fix(--fix)或 done。"""
    text = report.render(state, data)
    out_dir = run_dir / config.OUT_SUBDIR
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "report.md"
    out.write_text(text, encoding="utf-8")
    report.export_ledger(state, out_dir)
    state.report = {"file": str(out), "rendered_at": _now(), "details": data,
                    "text": text}
    if state.fix_requested and not state.fix.get("re_review_done"):
        state.phase = "fix"
        state.cursor["awaiting"] = "fix-outcomes"  # 与 argparse/decisions 的提交种类逐字一致(回修 c017)
        return f"报告已渲染({len(text.splitlines())} 行) —— 进入 --fix"
    state.phase = "done"
    state.cursor["awaiting"] = ""
    return "报告已渲染 —— 运行完成"


def _validate_fix_outcomes(data: object, state: State) -> list[str]:
    if not isinstance(data, dict):
        return ["fix-outcomes 必须是 JSON 对象"]
    outcomes = data.get("outcomes")
    if not isinstance(outcomes, list):
        return ["outcomes 必须是数组"]
    kept_ids = {c.get("id") for c in state.candidates if c.get("kept")}
    errs = []
    for i, o in enumerate(outcomes):
        if not isinstance(o, dict) or o.get("id") not in kept_ids:
            errs.append(f"outcomes[{i}].id 必须在 kept 名单")
        if isinstance(o, dict) and o.get("outcome") not in ("fixed", "skipped", "no_change_needed"):
            errs.append(f"outcomes[{i}].outcome 必须是 fixed|skipped|no_change_needed")
    return errs


def _apply_fix_outcomes(state: State, data: dict) -> str:
    state.fix["outcomes"] = data.get("outcomes", [])
    # 修复 diff 复审: quick 语义单轮(A/B/C + 红线时 K)
    state.phase = "fix_review"
    state.cursor["round_kind"] = "fix_review"
    state.cursor["pending_angles"] = [a for a in ("A", "B", "C", "K")
                                      if a in ("A", "B", "C") or (state.overlay.get("content") or {}).get("red_lines")]
    state.cursor["awaiting"] = "candidates"
    return f"{len(data.get('outcomes', []))} 条结果入账 —— 进入修复复审轮"


# --------------------------------------------------------------------------- #
# answer
# --------------------------------------------------------------------------- #
def cmd_answer(args) -> tuple[Decision, EmitContext]:
    run_dir = Path(args.run_dir).resolve()
    store = StateStore(run_dir)
    with store.locked():
        state, err = load_state_or_error(store, "answer 失败")
        if err is not None:
            return err, EmitContext(run_dir=run_dir)
        key, choice = args.key, args.choice

        # ── 错序守卫(P1 回修): 陈旧 ask_user 答复不得重放传送状态机 ──
        # 每类答复只在对应待决问题存活时合法; 与 submit 的错序自愈对齐,
        # 拒绝时交回路由器(429 中断后重贴旧命令是最常见的触发方式)。
        if key == "scope":
            stale = (not state.scope.get("escape_hatch")
                     or (state.pending_question or {}).get("key") == "drift")
        elif key == "gates":
            # P10 回修(c015/c033): gates 问题不入 pending_question, 守卫原只查
            # 相位 —— drift 待决在途时重放的 gates 答复被放行并抹掉漂移标记
            stale = (state.phase != "gates"
                     or (state.pending_question or {}).get("key") == "drift")
        elif key == "drift":
            stale = (state.pending_question or {}).get("key") != "drift"
        elif key == "run":
            stale = False  # run 答复仅交回路由器(resume 幂等), 无状态可传送
        else:
            raise StepError.state_error(f"未知 answer key: {key}")
        if stale:
            # return 而非 raise: raise 会丢掉 EmitContext, RESUME 路由解析不了 run_dir
            store.save(state)
            return Decision(
                status="failed", exit_code=config.EXIT_STATE,
                summary=f"错序答复: key={key} 已无对应待决问题(当前 phase={state.phase})",
                route=Route.RESUME, reason="陈旧答复重放 —— 交回路由器自愈",
                route_params=["resume", "--run-dir", str(run_dir)]
            ), EmitContext(run_dir=run_dir, state=state)

        if key == "scope":
            if choice == "terminate":
                state.phase = "aborted"
                _touch(state); store.save(state)
                return Decision(status="failed", exit_code=config.EXIT_STATE,
                                summary="用户终止(范围逃逸口)", route=Route.ABORT,
                                reason="用户选择终止", question="已按用户指示终止本次审查。"), EmitContext(run_dir=run_dir, state=state)
            if choice == "choice" and args.value:
                cwd_a = Path(state.repo.get("cwd", str(Path.cwd())))
                sc = scope_mod.resolve_scope(cwd_a, args.value)
                state.target = args.value
                state.scope = sc
                if sc.get("escape_hatch"):
                    state.pending_question = {"key": "scope", "at": _now()}
                    _touch(state); store.save(state)
                    return _scope_escape_ask(sc, run_dir), EmitContext(run_dir=run_dir, state=state)
                _init_overlay_state(state, cwd_a)
                # P11(c037): 刷新 head/wt 签名(与 drift 分支对齐)—— 原版不刷,
                # 下一次 submit 对用户刚裁决过的移动再发漂移询问, 误答
                # resume_old_scope 会把全部候选标 stale(反转 new_scope 意图)
                sig = scope_mod.current_signature(cwd_a)
                state.repo["head"] = sig.get("head")
                state.repo["wt_signature"] = sig.get("wt")
                # P10 回修(c015): 不抹在途 drift 待决
                if (state.pending_question or {}).get("key") != "drift":
                    state.pending_question = None
                _touch(state); store.save(state)
                d = decisions.decide_next(state, _ctx(state, run_dir))
                return d, EmitContext(run_dir=run_dir, state=state)
            raise StepError.state_error("scope 答复需要 --value <目标>")

        if key == "gates":
            gates = state.overlay.get("gates") or []
            if choice == "approve_all":
                for g in gates:
                    if g.get("approval") == "unset":
                        g["approval"] = "approved"
            elif choice == "skip_all":
                for g in gates:
                    if g.get("approval") == "unset":
                        g["approval"] = "denied"
            elif choice == "choice" and args.value:
                bits = [b.strip() for b in args.value.split(",")]
                unset = [g for g in gates if g.get("approval") == "unset"]
                if len(bits) != len(unset):
                    raise StepError.state_error(
                        f"逐条赋值长度不符: 需要 {len(unset)} 位, 收到 {len(bits)}")
                # P8 回修: token 校验 —— '2'/'yes' 等任意位原版静默按 denied
                # 处理(方向虽 fail-closed, 但违背全有或全无的提交纪律)
                bad = [b for b in bits if b not in ("0", "1")]
                if bad:
                    raise StepError.state_error(
                        f"逐条赋值只接受 0/1 位, 收到非法位: {bad}")
                for g, b in zip(unset, bits):
                    g["approval"] = "approved" if b == "1" else "denied"
            else:
                raise StepError.state_error("gates 答复需要 approve_all/skip_all/choice+--value")
            # P10 回修(c015): 不抹在途 drift 待决(下次 submit/resume 会重问)
            if (state.pending_question or {}).get("key") != "drift":
                state.pending_question = None
            # P1 回修: gates 答复不得越过 brief —— brief 未建时回 brief 阶段。
            # 正常流程(overlay→brief→gates)此处 brief 必已建好; 该分支同时兜住
            # 修复前遗留的旧 state(phase=gates 且 brief 为空的中断运行)。
            if state.brief.get("line_count"):
                state.phase, state.cursor["awaiting"] = "carrier", "carrier"
            else:
                state.phase, state.cursor["awaiting"] = "brief", "brief"
            _touch(state); store.save(state)
            return decisions.decide_next(state, _ctx(state, run_dir)), EmitContext(run_dir=run_dir, state=state)

        if key == "drift":
            cwd = state.repo.get("cwd", str(Path.cwd()))
            if choice == "terminate":
                state.phase = "aborted"
                _touch(state); store.save(state)
                return Decision(status="failed", exit_code=config.EXIT_STATE,
                                summary="用户终止(范围漂移)", route=Route.ABORT,
                                reason="用户选择终止", question="已按用户指示终止本次审查。"), EmitContext(run_dir=run_dir, state=state)
            if choice == "new_scope":
                sc = scope_mod.resolve_scope(cwd, state.target)
                if sc.get("escape_hatch"):
                    # P2 回修: 重导后全空 → 逃逸口重问(scope-answer 分支同款),
                    # 不得在零文件范围上静默空审
                    state.scope = sc
                    state.pending_question = {"key": "scope", "at": _now()}
                    _touch(state); store.save(state)
                    return _scope_escape_ask(sc, run_dir), EmitContext(run_dir=run_dir, state=state)
                state.scope = sc
                # P12 回修(c016): 重导范围 = 新一轮审查 —— 收敛计数器/验证队列/
                # 修复状态归零(原版沿用旧范围计数: 新 diff 以 round 3 撞轮顶单轮
                # 收口; --fix 残留 re_review_done 跳过新 diff 复审; 旧 pending_verify
                # 候选混入新验证队列)。台账(candidates/总账)按契约保留。
                state.cursor["round"] = 0
                # 幂等翻页(P12, c016 衍生/c001 同族): 重置 round=0 会使跨范围
                # 同字节提交命中旧 idem 被误吞(实测: 同字节 overlay 重投被吸收,
                # 重提取从未发生; 同字节 journal 重环活锁同根)。scope_epoch 递增
                # 后: 同一范围内的 429 重贴仍吸收(c004), 跨范围重环按新工作摄取。
                state.cursor["scope_epoch"] = state.cursor.get("scope_epoch", 0) + 1
                state.cursor["dry_streak"] = 0
                state.cursor["round_kind"] = "sweep"
                state.cursor["pending_verify"] = []
                state.cursor["pending_angles"] = []
                state.accounting["round_history"] = []
                if state.fix:
                    state.fix["re_review_done"] = False
                    state.fix.pop("outcomes", None)
                # P10 回修(c009, 吸收 P1-4 手写重评块): 重导范围后一律回 overlay
                # 阶段重新发现/提取 —— 信任判定、内容、门禁、排除全部以当前
                # 文件为准。原版沿用中断前的 ov['content']: 旧排除施加于新
                # 范围、已删 overlay 的用户已批门禁存活(A4 边界被绕)。
                # _init_overlay_state(P9 起)会同时置 phase='overlay'。
                _init_overlay_state(state, Path(cwd))
            elif choice == "resume_old_scope":
                for c in state.candidates:
                    c["stale"] = True
            else:
                # P11(c028): 未知 choice 原版静默落进共尾(重置 head+清 pending+
                # 路由)—— 用户裁决被无声丢弃且漂移永不再问(实证)
                raise StepError.state_error(
                    f"drift 答复需要 new_scope/resume_old_scope/terminate, 收到 {choice!r}")
            sig = scope_mod.current_signature(cwd)
            state.repo["head"] = sig.get("head")
            state.repo["wt_signature"] = sig.get("wt")
            state.repo.pop("wt_drift_note", None)
            state.pending_question = None
            _touch(state); store.save(state)
            return decisions.decide_next(state, _ctx(state, run_dir)), EmitContext(run_dir=run_dir, state=state)

        if key == "run":
            # 回修 c009: 交回路由器逐字执行(单出口)。注: locked() 已支持
            # 同进程重入(P8), 当年的 flock 自死锁形态不复存在。
            return Decision(status="success", exit_code=config.EXIT_OK,
                            summary="run 答复已接收 —— 交回路由器",
                            route=Route.RESUME, reason="answer run → resume",
                            route_params=["resume", "--run-dir", str(run_dir)]), \
                EmitContext(run_dir=run_dir, state=state)
        raise StepError.state_error(f"未知 answer key: {key}")


# --------------------------------------------------------------------------- #
# report(幂等重渲染)
# --------------------------------------------------------------------------- #
def cmd_report(args) -> tuple[Decision, EmitContext]:
    # include_done(P2 回修): done 后裸形式可解析(原版唯一运行完成后不可达)
    run_dir, store = _resolve_entry_point(args, include_done=True)
    with store.locked():
        state, err = load_state_or_error(store, "report 失败")
        if err is not None:
            return err, EmitContext(run_dir=run_dir)
        # P2 回修: phase=report 表示 details 尚未提交 —— 原版放行此相位, 提前
        # 调用会用空 details 渲染占位报告并以 finish/exit-0 终结整个审查
        if state.phase not in ("fix", "done"):
            raise StepError.state_error(
                f"report 仅在报告完成后可用(当前 phase={state.phase})",
                "details 尚未提交 —— 先完成 05-details 提交; 此命令用于 done 后幂等重渲染。")
        details = state.report.get("details") or {}
        text = report.render(state, details)
        out = store.out_dir() / "report.md"
        out.write_text(text, encoding="utf-8")
        report.export_ledger(state, store.out_dir())
        state.report["file"] = str(out)
        state.report["rendered_at"] = _now()
        _touch(state)
        store.save(state)
        return Decision(
            status="success", exit_code=config.EXIT_OK, summary="报告已(重)渲染",
            route=Route.FINISH, reason="report 命令完成", report=text,
            deliverables=[str(out), str(store.out_dir() / "ledger.json")],
        ), EmitContext(run_dir=run_dir, state=state)


# --------------------------------------------------------------------------- #
# status / runs(只读)
# --------------------------------------------------------------------------- #
def _state_metrics(state) -> dict:
    acc = state.accounting
    return {
        "run_id": state.run_id,
        "mode": state.mode,
        "phase": state.phase,
        "awaiting": state.cursor.get("awaiting", ""),
        "round": state.cursor.get("round", 0),
        "round_kind": state.cursor.get("round_kind", ""),
        "candidates_seen": acc.get("total_seen", 0),
        "dup_dropped": acc.get("dup_dropped", 0),
        "refuted": acc.get("refuted", 0),
        # P2 回修: kept 实时取台账(原版读 kept_after_thresholds, 唯一写入点在
        # report.render —— verify 阶段候选已 kept=True 时 status 仍报 kept=0)
        "kept": sum(1 for c in state.candidates if c.get("kept")),
        "carrier": state.carrier.get("carrier"),
        "pending_question": (state.pending_question or {}).get("key"),
    }


def cmd_status(args) -> tuple[Decision, EmitContext]:
    # include_done(P2 回修): 唯一运行完成后裸 status 仍能解析它
    run_dir = resolve_run_dir(getattr(args, "run_dir", None), Path.cwd(),
                              include_done=True)
    if run_dir is None:
        return Decision(
            status="empty", exit_code=config.EXIT_OK, summary="当前仓库无运行记录",
            route=Route.FINISH, reason="无运行",
        ), EmitContext()

    store = StateStore(run_dir)
    state, err = load_state_or_error(store, "status 失败")
    if err is not None:
        return err, EmitContext(run_dir=run_dir)

    metrics = _state_metrics(state)
    if getattr(args, "json", False):
        metrics["state"] = state.to_dict()
    summary = (f"run {state.run_id} · {state.mode} · phase={state.phase}"
               + (f" · awaiting={state.cursor.get('awaiting')}" if state.cursor.get("awaiting") else "")
               + f" · seen={metrics['candidates_seen']} kept={metrics['kept']}")
    return Decision(
        status="success", exit_code=config.EXIT_OK, summary=summary,
        route=Route.FINISH, reason="状态查询完成",
        metrics=metrics,
        artifacts=[{"path": str(store.path), "kind": "state"}],
    ), EmitContext(run_dir=run_dir, state=state)


def cmd_runs(args) -> tuple[Decision, EmitContext]:
    runs = list_runs(Path.cwd())
    if not runs:
        return Decision(
            status="empty", exit_code=config.EXIT_OK, summary="当前仓库无运行记录",
            route=Route.FINISH, reason="无运行",
        ), EmitContext()
    metrics = {"runs": runs} if getattr(args, "json", False) else {"count": len(runs)}
    lines = []
    for r in runs:
        flag = "done" if r.get("done") else ("损坏" if r.get("corrupt") else r.get("phase", "?"))
        lines.append(f"- {r['run_id']} [{r.get('mode','?')}/{flag}]"
                     + (f" target={r['target']}" if r.get("target") else ""))
    return Decision(
        status="success", exit_code=config.EXIT_OK,
        summary=f"{len(runs)} 个运行:\n" + "\n".join(lines),
        route=Route.FINISH, reason="运行列举完成", metrics=metrics,
    ), EmitContext()
