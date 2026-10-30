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


def _run_ask(run_dir: Path) -> Decision:
    """存在未完成运行时, start 前的唯一询问(防 429 后重跑覆盖)。"""
    runs = [r for r in list_runs(Path.cwd()) if not r.get("done") and not r.get("corrupt")]
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
    current = scope_mod.current_signature(cwd)
    drift = scope_mod.drift_between(scope_mod.signature_of(state.repo), current)
    if drift == "head_moved":
        state.pending_question = {"key": "drift", "at": _now()}
        return _drift_ask(state.repo.get("head"), current.get("head"), run_dir)
    if drift == "working_tree_changed":
        state.repo["wt_drift_note"] = "工作树在运行期间有新改动(范围基准不变)"
    return None


def _resolve_entry_point(args) -> tuple[Path, StateStore]:
    run_dir = resolve_run_dir(getattr(args, "run_dir", None), Path.cwd())
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
            state.cursor["awaiting"] = "overlay"
    else:
        state.overlay["heuristic_gates"] = []
        state.cursor["awaiting"] = "overlay"


def cmd_start(args) -> tuple[Decision, EmitContext]:
    cwd = Path.cwd()
    mode = args.mode
    target = args.target
    fix_requested = bool(args.fix)

    if not gitops.is_git_repo(cwd):
        sc = {"escape_hatch": {"probes": [
            {"cmd": "git rev-parse --is-inside-work-tree", "rc": 1, "empty": True}]}}
        state = _new_state(cwd, mode, fix_requested, target, sc)
        run_dir = _place_state(args, state)
        return _scope_escape_ask(sc, run_dir), EmitContext(run_dir=run_dir, state=state)

    # 未完成运行防覆盖(429 中断后重跑是验收实测过的失败模式)
    if not args.force and not args.run_dir:
        unfinished = [r for r in list_runs(cwd) if not r.get("done") and not r.get("corrupt")]
        if unfinished:
            newest = Path(unfinished[0]["run_dir"])
            return _run_ask(newest), EmitContext(run_dir=newest)

    sc = scope_mod.resolve_scope(cwd, target)
    state = _new_state(cwd, mode, fix_requested, target, sc)
    run_dir = _place_state(args, state)

    if sc.get("escape_hatch"):
        return _scope_escape_ask(sc, run_dir), EmitContext(run_dir=run_dir, state=state)

    _init_overlay_state(state, cwd)
    _touch(state)
    StateStore(run_dir).save(state)
    return decisions.decide_next(state, _ctx(state, run_dir)), EmitContext(run_dir=run_dir, state=state)


def _new_state(cwd: Path, mode: str, fix_requested: bool, target: Optional[str], sc: dict) -> State:
    sig = scope_mod.current_signature(cwd)
    return State(
        run_id="",  # 由 _place_state 落定
        created_at=_now(), updated_at=_now(),
        repo={"cwd": str(cwd), "slug": repo_slug(cwd), "head": sig.get("head"),
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
    return run_dir


# --------------------------------------------------------------------------- #
# resume / next
# --------------------------------------------------------------------------- #
def cmd_resume(args) -> tuple[Decision, EmitContext]:
    run_dir, store = _resolve_entry_point(args)
    with store.locked():
        state, err = load_state_or_error(store, "resume 失败")
        if err is not None:
            return err, EmitContext(run_dir=run_dir)
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
_KIND_FILE_HINT = {
    "overlay": "overlay 提取(overlay.json)",
    "brief": "审查 brief(brief.md)",
    "candidates": "候选(candidates.json)",
    "verdicts": "裁决票(verdicts.json)",
    "details": "报告细节(details.json)",
    "carrier": "载体选择(carrier.json)",
    "fix-outcomes": "修复结果(fix-outcomes.json)",
}


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
            if args.transcript_dir:
                transcript_dir = Path(args.transcript_dir)
            else:
                sniff = Path(args.file).read_text(encoding="utf-8").strip()
                transcript_dir = None if sniff.startswith("{") else Path(sniff.splitlines()[0])
                compact_file = Path(args.file) if transcript_dir is None else None
            try:
                if transcript_dir is not None and (transcript_dir / "journal.jsonl").is_file():
                    note = journal.ingest_transcript(state, transcript_dir)
                else:
                    compact_file = compact_file or Path(args.file)
                    note = journal.apply_compact(state, journal.load_compact(compact_file))
            except (ValueError, OSError, KeyError) as exc:
                raise StepError.exec_error(
                    f"载体 1 journal 摄取失败: {exc}",
                    question=f"检查 transcript 目录/journal 文件后重试: {exc}")
            state.submissions.append({
                "seq": len(state.submissions) + 1, "kind": "carrier1",
                "file": str(args.transcript_dir or args.file), "sha256": "",
                "applied": True, "note": note,
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
        # 幂等键含轮次: 同轮内字节相同重投 = 孤儿结果吸收; 跨轮同字节(如连续
        # 空的 gap 轮)是新工作, 不得误吞 —— 否则 gap 循环死锁
        idem = f"{kind}|{state.cursor.get('round', 0)}|{digest}"
        dup = next((s for s in state.submissions
                    if s.get("idem") == idem and s.get("applied")), None)
        if dup is not None:
            # 字节相同的重投 = 无操作成功(孤儿结果幂等吸收)
            decision = decisions.decide_next(state, _ctx(state, run_dir))
            decision.summary = f"重复提交已吸收({kind}, sha256 相同) —— {decision.summary}"
            store.save(state)
            return decision, EmitContext(run_dir=run_dir, state=state)

        text = raw.decode("utf-8", errors="strict") if kind == "brief" else None
        if kind == "brief":
            data: object = text
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
            # phase 推进(回修 c057 第二半): 有待确认门禁 → gates; 否则直达 brief
            if overlay.pending_gate_question_count(st):
                st.phase, st.cursor["awaiting"] = "gates", "answer"
            else:
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
    state.brief = {"file": f"{config.IN_SUBDIR}/02-brief.md", "sha256": "",
                   "line_count": lines, "sections_ok": True, "warnings": warnings}
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
    ma = data.get("max_agents")
    if ma is not None and (not isinstance(ma, int) or ma < 1):
        errs.append("max_agents 必须是正整数或缺省")
    return errs


def _apply_carrier(state: State, data: dict) -> str:
    plan = state.sweep_plan or {}
    state.carrier = {"carrier": data.get("carrier"), "reason": data.get("reason"),
                     "max_agents": data.get("max_agents") or plan.get("max_agents"),
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
                for g, b in zip(unset, bits):
                    g["approval"] = "approved" if b == "1" else "denied"
            else:
                raise StepError.state_error("gates 答复需要 approve_all/skip_all/choice+--value")
            state.pending_question = None
            state.phase, state.cursor["awaiting"] = "carrier", "carrier"
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
                state.scope = sc
            elif choice == "resume_old_scope":
                for c in state.candidates:
                    c["stale"] = True
            sig = scope_mod.current_signature(cwd)
            state.repo["head"] = sig.get("head")
            state.repo["wt_signature"] = sig.get("wt")
            state.repo.pop("wt_drift_note", None)
            state.pending_question = None
            _touch(state); store.save(state)
            return decisions.decide_next(state, _ctx(state, run_dir)), EmitContext(run_dir=run_dir, state=state)

        if key == "run":
            # 回修 c009: 锁内不得再调 cmd_resume(其自身加锁 → 同进程 flock 自死锁);
            # 交回路由器逐字执行
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
    run_dir, store = _resolve_entry_point(args)
    with store.locked():
        state, err = load_state_or_error(store, "report 失败")
        if err is not None:
            return err, EmitContext(run_dir=run_dir)
        if state.phase not in ("report", "fix", "done"):
            raise StepError.state_error(
                f"report 仅在报告阶段可用(当前 phase={state.phase})",
                "details 提交后报告由引擎渲染; 此命令用于 done 后幂等重渲染。")
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
        "kept": acc.get("kept_after_thresholds", 0),
        "carrier": state.carrier.get("carrier"),
        "pending_question": (state.pending_question or {}).get("key"),
    }


def cmd_status(args) -> tuple[Decision, EmitContext]:
    run_dir = resolve_run_dir(getattr(args, "run_dir", None), Path.cwd())
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
