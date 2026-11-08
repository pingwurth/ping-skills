"""code-review-p 工作流脚本的门禁测试。

deep-review.js 是 Workflow 工具的脚本片段（顶层 return + workflow 全局函数），
不能直接 ``node --check``：这里剥掉 ``export const meta``、把余下主体包进
异步函数（以 args/agent/parallel/pipeline/phase/log/budget 为参数）后再做
语法检查；并抽取脚本中的纯函数块（路径归一 normFile / 去重 isDup / 裁决聚合
aggregateVerdict）用 node 断言行为——覆盖绝对/相对路径统一、CJK 摘要不塌缩、
±3 行漂移容忍、平局与法定票数。
无 node 环境时跳过（skip 并标注），脚本存在性与契约标记检查不依赖 node。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_DIR / "workflows" / "deep-review.js"
NODE = shutil.which("node")

requires_node = pytest.mark.skipif(NODE is None, reason="node 不在 PATH，无法校验/执行 workflow 脚本")


def script_src() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def extract_pure_block(src: str) -> str:
    m = re.search(r"// ── pure helpers[^\n]*\n(.*?)// ── end pure helpers", src, re.S)
    assert m, "deep-review.js 中未找到 pure helpers 标记块（本测试依赖它抽取被测函数）"
    return m.group(1)


def run_node(js: str) -> str:
    proc = subprocess.run([NODE, "-e", js], capture_output=True, text=True)
    assert proc.returncode == 0, f"node 执行失败:\n{proc.stderr}"
    return proc.stdout.strip()


def run_case(seen: list, cand: dict, votes: list, high: bool) -> dict:
    """在 node 里执行抽取出的纯函数块，返回 {dup, verdict}。"""
    block = extract_pure_block(script_src())
    js = (
        block
        + f"\nconst seen = {json.dumps(seen, ensure_ascii=False)};"
        + f"\nconst c = {json.dumps(cand, ensure_ascii=False)};"
        + f"\nconst votes = {json.dumps(votes, ensure_ascii=False)};"
        + f"\nconst high = {json.dumps(high)};"
        + "\nconsole.log(JSON.stringify({dup: isDup(seen, c), verdict: aggregateVerdict(votes, high)}));"
    )
    return json.loads(run_node(js))


# ── 契约标记（不依赖 node） ──────────────────────────────────────────

def test_script_exists_and_contract_markers():
    """回归标记：severity enum（防自由字符串排序失真）与 maxRounds 下界钳制。"""
    src = script_src()
    assert "enum: ['P0', 'P1', 'P2', 'P3']" in src, "VERDICT_SCHEMA.severity 缺少 P0-P3 enum"
    assert "Math.max(1, Math.min(" in src, "maxRounds 缺少下界钳制（maxRounds:0 会空跑）"


def test_concurrency_gate_markers():
    """限流治理契约：所有 agent() 调用经 spawn() 信号量门控 + args.concurrency 钳位。"""
    src = script_src()
    assert "args?.concurrency" in src, "缺少 args.concurrency 并发参数（钳位 [1,16]）"
    assert "const spawn = async" in src, "缺少 spawn() 信号量门控封装"
    # agent() 直连只允许出现在 spawn 内部这一个调用点
    assert src.count("await agent(prompt, opts)") == 1, "agent() 必须只在 spawn() 内被调用"
    assert "spawn(finderPrompt(" in src, "finder 未走门控"
    assert "spawn(refuterPrompt(" in src, "refuter 未走门控"
    assert "find:${a.key}-retry" in src, "finder 死亡（限流）缺少一次性重试"


def test_self_describing_schemas_and_journal():
    """v2.0 契约：finder/refuter 返回自描述（angle / file+line+summary+lens），
    返回值携带压缩 journal —— crp.py submit --kind carrier1 的摄取前提。"""
    src = script_src()
    # finder 返回含 angle
    assert "angle: { type: 'string'" in src, "CANDIDATES_SCHEMA 缺少自描述 angle"
    assert "required: ['angle', 'candidates']" in src
    # refuter 返回回显 finding 身份与透镜
    for marker in ("file: { type: 'string'", "summary: { type: 'string'",
                   "lens: { type: 'string', enum: ['correctness', 'reproduction', 'security']",
                   "required: ['file', 'line', 'summary', 'lens', 'verdict', 'evidence', 'severity']"):
        assert marker in src, f"VERDICT_SCHEMA 缺少 {marker.split(':')[0]}"
    # 压缩 journal(兜底摄取通道) + 计数键为 snake_case(Python apply_compact 对齐)
    assert "journal: {" in src and "journalFinders" in src and "journalVerdicts" in src
    assert "counters: { rounds: round, total_seen: seen.length, dup_dropped: dupDropped }" in src
    # 透镜按构造打标(不信任 agent 回显)
    assert "lens: ls[i][0]" in src
    # 生成端限长与 Python 台账接收上限逐值对齐(计数口径回修):
    # summary 500 / failure_scenario 2000 / evidence 4000
    assert src.count("maxLength: 500") == 2, "finder+refuter 的 summary 均须限长 500"
    assert "maxLength: 2000" in src and "maxLength: 4000" in src


# ── 奇偶校验：与 Python(ledger.py) 共享夹具的双向断言 ────────────────

FIXTURES = Path(__file__).parent / "fixtures"
DEDUP_CASES = json.loads((FIXTURES / "dedup_cases.json").read_text(encoding="utf-8"))


@requires_node
def test_parity_fixtures_against_js():
    """tests/fixtures/dedup_cases.json 由 Python 与 JS 两侧共同遍历 —— 锁死语义漂移。"""
    block = extract_pure_block(script_src())
    js = (
        block
        + f"\nconst cases = {json.dumps(DEDUP_CASES['dup_cases'], ensure_ascii=False)};"
        + "\nconst out = cases.map(c => ({name: c.name, got: !!isDup(c.seen, c.cand), want: c.dup}));"
        + "\nconsole.log(JSON.stringify(out.filter(r => r.got !== r.want)))"
    )
    mismatches = json.loads(run_node(js))
    assert mismatches == [], f"JS/期望分歧: {mismatches}"


@requires_node
def test_parity_norm_fixtures_against_js():
    block = extract_pure_block(script_src())
    js = (
        block
        + f"\nconst cases = {json.dumps(DEDUP_CASES['norm_cases'], ensure_ascii=False)};"
        + "\nconst out = cases.map(c => ({f: c.f, got: normFile(c.f, c.root), want: c.expected}));"
        + "\nconsole.log(JSON.stringify(out.filter(r => r.got !== r.want)))"
    )
    mismatches = json.loads(run_node(js))
    assert mismatches == [], f"normFile JS/期望分歧: {mismatches}"


# ── 语法门禁（node 包装检查） ────────────────────────────────────────

@requires_node
def test_workflow_script_syntax(tmp_path: Path):
    body = re.sub(r"export const meta = \{.*?\n\}\n", "", script_src(), flags=re.S)
    assert "export " not in body, "除 meta 外不应再有 export（会破坏包装语法检查）"
    wrapped = (
        "const run = async (args, agent, parallel, pipeline, phase, log, budget) => {\n"
        + body
        + "\n};\n"
    )
    target = tmp_path / "wrapped-deep-review.js"
    target.write_text(wrapped, encoding="utf-8")
    proc = subprocess.run([NODE, "--check", str(target)], capture_output=True, text=True)
    assert proc.returncode == 0, f"语法检查失败:\n{proc.stderr}"


# ── 去重：CJK 不塌缩、±3 行漂移容忍（B1/B2 回归） ───────────────────

@requires_node
def test_cjk_distinct_summaries_same_line_not_merged():
    r = run_case(
        [{"file": "src/auth.ts", "line": 42, "summary": "令牌比较先于过期解析，NaN 通过校验"}],
        {"file": "src/auth.ts", "line": 42, "summary": "缺少并发锁，重复提交订单"},
        [], False,
    )
    assert r["dup"] is False, "同 file:line 的两条不同中文候选被误并（CJK 塌缩回归）"


@requires_node
def test_cjk_same_defect_line_drift_dedup():
    r = run_case(
        [{"file": "src/auth.ts", "line": 42, "summary": "令牌比较先于过期解析，NaN 通过校验"}],
        {"file": "src/auth.ts", "line": 44, "summary": "令牌比较先于过期解析，NaN 通过校验"},
        [], False,
    )
    assert r["dup"] is True


@requires_node
def test_english_same_defect_within_band_dedup():
    r = run_case(
        [{"file": "a.py", "line": 10, "summary": "Missing await on fetchUser before reading result"}],
        {"file": "a.py", "line": 13, "summary": "Missing await on fetchUser before reading result"},
        [], False,
    )
    assert r["dup"] is True  # |10-13| = 3，带内


@requires_node
def test_english_beyond_band_not_dedup():
    r = run_case(
        [{"file": "a.py", "line": 10, "summary": "Missing await on fetchUser before reading result"}],
        {"file": "a.py", "line": 14, "summary": "Missing await on fetchUser before reading result"},
        [], False,
    )
    assert r["dup"] is False  # |10-14| = 4，带外宁可保留（召回安全方向）


@requires_node
def test_distinct_summaries_same_line_not_merged():
    r = run_case(
        [{"file": "a.py", "line": 7, "summary": "Missing await on fetchUser"}],
        {"file": "a.py", "line": 7, "summary": "Off-by-one in the retry loop bound"},
        [], False,
    )
    assert r["dup"] is False


@requires_node
def test_empty_summary_never_merges():
    r = run_case(
        [{"file": "a.py", "line": 7, "summary": ""}],
        {"file": "a.py", "line": 7, "summary": ""},
        [], False,
    )
    assert r["dup"] is False


# ── 路径归一（v1.4.1 #9 回归）：绝对/相对混用不得击穿去重 ────────────

@requires_node
def test_normfile_unifies_absolute_relative_dotslash_and_windows():
    block = extract_pure_block(script_src())
    js = block + """
const root = '/repo/x';
const cases = [
  [normFile('/repo/x/services/billing/A.java', root), 'services/billing/A.java'],
  [normFile('services/billing/A.java', root), 'services/billing/A.java'],
  [normFile('./services/billing/A.java', root), 'services/billing/A.java'],
  [normFile('\\\\repo\\\\x\\\\services\\\\B.java', root), 'services/B.java'],
  [normFile('/elsewhere/C.java', root), '/elsewhere/C.java'],
];
console.log(JSON.stringify(cases.filter(([got, want]) => got !== want)));
"""
    assert json.loads(run_node(js)) == []


@requires_node
def test_dedup_composes_with_normfile():
    """端到端纯函数证明：绝对路径入账 + 相对路径候选 → 判重（#9 现场）。"""
    block = extract_pure_block(script_src())
    js = (
        block
        + "\nconst root = '/repo/x';"
        + "\nconst seen = [{file: normFile('/repo/x/a.py', root), line: 10,"
        + " summary: 'Missing await on fetchUser before reading result'}];"
        + "\nconst c = {file: normFile('a.py', root), line: 10,"
        + " summary: 'Missing await on fetchUser before reading result'};"
        + "\nconsole.log(JSON.stringify(isDup(seen, c)));"
    )
    assert json.loads(run_node(js)) is True


# ── 裁决聚合：法定票数与平局（B5/B6 回归） ──────────────────────────

AGG_CASES = json.loads((SKILL_DIR / "tests" / "fixtures" / "aggregate_cases.json")
                       .read_text(encoding="utf-8"))["cases"]


@requires_node
def test_aggregate_quorum_and_ties():
    """矩阵与 Python 侧(test_aggregate.py)共享 fixtures/aggregate_cases.json
    (P5 回修: 原双份手抄, 两引擎可带绿色测试漂移)。"""
    for case in AGG_CASES:
        votes = [{"verdict": v} for v in case["votes"]]
        r = run_case([], {"file": "x", "line": 1, "summary": "s"}, votes, case["high"])
        assert r["verdict"] == case["expected"], f"{case['name']}: -> {r['verdict']}，期望 {case['expected']}"


# ── 限流治理：并发信号量 + finder 一次性重试（v2.1） ─────────────────

STUB_JS = """
const ANGLES = ['A','B','C','D','E','F'];
let inFlight = 0, peak = 0; const labels = [];
const sleep = ms => new Promise(r => setTimeout(r, ms));
const agent = async (prompt, opts) => {
  inFlight++; peak = Math.max(peak, inFlight); labels.push(opts.label);
  await sleep(3);
  inFlight--;
  // 角度 A 的首个 finder 死于限流（返回 null）——触发一次性重试
  if (opts.label.startsWith('find:A-') && !opts.label.endsWith('-retry')) return null;
  if (opts.label.startsWith('find:')) {
    const key = opts.label.slice(5).split('-')[0];
    const idx = ANGLES.indexOf(key);
    return { angle: key, candidates: [{ file: 'src/a.ts', line: 10 + idx,
      summary: 'finding ' + key, failure_scenario: 'scenario ' + key,
      confidence: 0.9, severity: 'P2', category: 'correctness' }] };
  }
  if (opts.label.startsWith('verify:')) {
    return { file: 'src/a.ts', line: 10, summary: 's', lens: 'correctness',
             verdict: 'CONFIRMED', evidence: 'quoted line', severity: 'P2' };
  }
  return null;
};
// 与宿主语义对齐：parallel 是屏障 + 失败为 null；pipeline 逐条流转无栅栏
const parallel = thunks => Promise.all(thunks.map(t => Promise.resolve().then(t).catch(() => null)));
const pipeline = (items, ...stages) => Promise.all(items.map((item, i) => (async () => {
  let r = item;
  for (const st of stages) {
    try { r = await st(r, item, i); } catch (e) { return null; }
  }
  return r;
})()));
const phase = () => {}; const log = () => {};
const budget = { total: null, spent: () => 0, remaining: () => Infinity };
const args = { brief: 'b', scope: 'git diff', cwd: '/repo', angles: ANGLES,
               maxRounds: 1, maxAgents: 60, concurrency: 3 };
"""


@requires_node
def test_concurrency_semaphore_behavioral():
    """行为测试（限流治理）：stub 宿主原语跑全脚本 ——
    1) 峰值在飞 agent 数 == concurrency 钳位（finder 波与 refuter 嵌套扇出共享同一闸门）;
    2) finder 死亡(null)补发一次 find:<key>-retry, coverage 恢复 ran=true;
    3) concurrency 透传到返回值。"""
    body = re.sub(r"export const meta = \{.*?\n\}\n", "", script_src(), flags=re.S)
    js = (
        "const run = async (args, agent, parallel, pipeline, phase, log, budget) => {\n"
        + body + "\n};\n" + STUB_JS + "\n"
        + "run(args, agent, parallel, pipeline, phase, log, budget).then(out => {\n"
        + "  console.log(JSON.stringify({peak, labels, rounds: out.rounds, concurrency: out.concurrency,\n"
        + "    findings: out.findings.length, covA: out.coverage.A.ran}));\n"
        + "}).catch(e => { console.error('SCRIPT_THREW', e); process.exit(9); });\n"
    )
    r = json.loads(run_node(js))
    assert r["peak"] == 3, f"峰值在飞 {r['peak']} != concurrency 3 —— 信号量未钳住并发"
    assert r["concurrency"] == 3, "concurrency 未透传到返回值"
    assert r["rounds"] == 1
    assert "find:A-retry" in r["labels"], "finder 死亡未触发一次性重试"
    first_calls = [l for l in r["labels"]
                   if l.startswith("find:A-") and not l.endswith("-retry")]
    assert len(first_calls) == 1, f"A 角度应恰好一次失败调用: {first_calls}"
    assert r["covA"] is True, "重试成功后 coverage.A.ran 应恢复为 true"
    assert r["findings"] >= 1


# ── P7 回修回归: 常量 parity 锁 / verify:false / dryRequired(2026-11-02) ──
def test_p7_js常量与config对齐():
    """P7 回归: deep-review.js 复刻的 config 常量逐值锁定 —— config 改动而
    JS 未同步时此处变红(操作者读 workflow 输出与最终报告不再两套口径)。"""
    from crp import config

    src = script_src()

    def grab(pattern):
        m = re.search(pattern, src)
        assert m, f"deep-review.js 中未找到 {pattern!r} —— 常量位置变了?"
        return m.group(1)

    assert int(grab(r"maxRounds \?\? (\d+)")) == config.MODE_PARAMS["deep"]["max_rounds"]
    assert int(grab(r"dryRequired \?\? (\d+)")) == config.MODE_PARAMS["deep"]["dry_required"]
    assert int(grab(r"maxRounds \?\? \d+, (\d+)\)")) == config.MAX_ROUNDS_MAX
    assert int(grab(r"args\?\.concurrency \?\? (\d+)")) == config.DEFAULT_MAX_CONCURRENT
    assert int(grab(r"concurrency \?\? \d+, (\d+)\)")) == config.MAX_CONCURRENT_MAX
    assert float(grab(r"\(f\.confidence \?\? 1\) >= (\d+(?:\.\d+)?)")) == config.CONFIDENCE_FLOOR
    assert int(grab(r"raw\.slice\(0, (\d+)\)")) == config.MAX_CANDIDATES_PER_ANGLE
    assert int(grab(r"reportable\.slice\(0, (\d+)\)")) == config.MODE_PARAMS["deep"]["cap"]
    assert int(grab(r"JOURNAL_EVIDENCE_CHARS = (\d+)")) == config.MAX_EVIDENCE_CHARS
    # 排序表: severity 键序 / category 逐值
    order_keys = [kv.split(":")[0].strip() for kv in
                  re.search(r"const order = \{([^}]+)\}", src).group(1).split(",")]
    assert order_keys == list(config.SEVERITIES)
    js_cat = {k.strip(): int(v) for k, v in
              (kv.split(":") for kv in
               re.search(r"const catOrder = \{([^}]+)\}", src).group(1).split(","))}
    assert js_cat == config.CATEGORY_RANK, "类别排序表与 config.CATEGORY_RANK 漂移"


@requires_node
def test_p7_verify_false免对抗验证():
    """P7 回归: quick 模式引擎传 verify:false —— workflow 不跑反驳(免烧
    P0/P1 三透镜预算), 候选原样通过(Python floor-only 摄取)。"""
    body = re.sub(r"export const meta = \{.*?\n\}\n", "", script_src(), flags=re.S)
    stub = STUB_JS.replace(
        "maxRounds: 1, maxAgents: 60, concurrency: 3 }",
        "maxRounds: 1, maxAgents: 60, concurrency: 3, verify: false }")
    js = (
        "const run = async (args, agent, parallel, pipeline, phase, log, budget) => {\n"
        + body + "\n};\n" + stub + "\n"
        + "run(args, agent, parallel, pipeline, phase, log, budget).then(out => {\n"
        + "  console.log(JSON.stringify({labels, findings: out.findings.length,\n"
        + "    verdicts: out.journal.verdicts.length}));\n"
        + "}).catch(e => { console.error('SCRIPT_THREW', e); process.exit(9); });\n"
    )
    r = json.loads(run_node(js))
    assert not any(l.startswith("verify:") for l in r["labels"]), "verify:false 不得 spawn 反驳者"
    assert r["findings"] >= 1, "候选应原样通过(引擎 floor-only 摄取)"
    assert r["verdicts"] == 0


@requires_node
def test_p7_dryRequired_单轮dry即收口():
    """P7 回归: dryRequired 由引擎下发 —— standard(dry_required=1)首轮 dry
    即收口(原版硬编码 dry<2 的 deep 语义多跑一整轮 gap)。"""
    body = re.sub(r"export const meta = \{.*?\n\}\n", "", script_src(), flags=re.S)
    stub = STUB_JS.replace(
        "maxRounds: 1, maxAgents: 60, concurrency: 3 }",
        "maxRounds: 2, maxAgents: 60, concurrency: 3, dryRequired: 1 }")
    stub = re.sub(r"return \{ angle: key, candidates: \[.*?\] \};",
                  "return { angle: key, candidates: [] };", stub, flags=re.S)
    js = (
        "const run = async (args, agent, parallel, pipeline, phase, log, budget) => {\n"
        + body + "\n};\n" + stub + "\n"
        + "run(args, agent, parallel, pipeline, phase, log, budget).then(out => {\n"
        + "  console.log(JSON.stringify({rounds: out.rounds,\n"
        + "    gapRounds: out.coverage.GAP ? 1 : 0}));\n"
        + "}).catch(e => { console.error('SCRIPT_THREW', e); process.exit(9); });\n"
    )
    r = json.loads(run_node(js))
    assert r["rounds"] == 1, f"dryRequired=1 首轮 dry 即收口(实际 {r['rounds']} 轮)"
    assert r["gapRounds"] == 0, "不应再开 gap 轮"


@requires_node
def test_p10_js尊重下传maxAgents():
    """P10 回修(c010): JS 原 'Math.max(12, args?.maxAgents ?? …)' 把载体收紧的
    1-11 静默抬回 12 —— 返回值上报 12 而 state.carrier 记用户的 5, 行为与
    审计留痕不一致; override 现原样生效(仅缺省才用扇出默认)。"""
    body = re.sub(r"export const meta = \{.*?\n\}\n", "", script_src(), flags=re.S)
    stub = STUB_JS.replace(
        "maxRounds: 1, maxAgents: 60, concurrency: 3 }",
        "maxRounds: 1, maxAgents: 5, concurrency: 3 }")
    js = (
        "const run = async (args, agent, parallel, pipeline, phase, log, budget) => {\n"
        + body + "\n};\n" + stub + "\n"
        + "run(args, agent, parallel, pipeline, phase, log, budget).then(out => {\n"
        + "  console.log(JSON.stringify({maxAgents: out.maxAgents}));\n"
        + "}).catch(e => { console.error('SCRIPT_THREW', e); process.exit(9); });\n"
    )
    r = json.loads(run_node(js))
    assert r["maxAgents"] == 5, f"下传 5 必须生效(原版被 Math.max(12,…) 抬到 12): {r}"


# ── F4 回修回归: 门禁白名单按批复(非来源)定键(2026-11-09 全仓审查) ────
def test_f4_gate_whitelist_markers():
    """F4 回归: 引擎按用户批复下发白名单(args.gates), 载体 1 提示只认白名单
    —— 来源(overlay/heuristic)不是授权; 无白名单 = 不运行任何门禁(fail-closed)。
    原版按"来自 diff 新增的 overlay 或启发式"定键, 用户明确拒绝(denied)的
    门禁仍被子代理执行。"""
    src = script_src()
    assert "gates?: array<string>" in src, "args 注释缺少 gates 白名单参数"
    assert "approvedGates" in src, "缺少白名单变量"
    assert "(none — run no gates)" in src, "缺少 fail-closed 空白名单文案"
    # 两个 prompt(finder/refuter)都携带 gateRule
    assert src.count("${gateRule}") == 2, "finder/refuter 提示都必须携带白名单规则"
    # 旧的按来源定键指令必须移除
    assert "comes from an overlay the diff itself added" not in src, \
        "来源定键的旧指令必须删除(来源≠批准)"
