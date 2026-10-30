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

V = lambda v: {"verdict": v}  # noqa: E731 — 测试用的最小票对象

@requires_node
def test_aggregate_quorum_and_ties():
    cases = [
        # (votes, high, expected)
        ([], True, "PLAUSIBLE"),            # 全部失败——既非赞成也非反对
        ([V("REFUTED")], True, "PLAUSIBLE"),   # 法定票数不足：单票不能杀 P0/P1
        ([V("CONFIRMED")], True, "PLAUSIBLE"),  # 单票也不能确认 P0/P1
        ([V("CONFIRMED"), V("REFUTED"), V("PLAUSIBLE")], True, "PLAUSIBLE"),  # 平局=不确定
        ([V("REFUTED"), V("REFUTED"), V("CONFIRMED")], True, "REFUTED"),      # 严格多数反驳
        ([V("CONFIRMED"), V("CONFIRMED"), V("PLAUSIBLE")], True, "CONFIRMED"),  # 多数确认
        ([V("CONFIRMED"), V("PLAUSIBLE"), V("PLAUSIBLE")], True, "PLAUSIBLE"),  # 少数确认
        ([V("REFUTED")], False, "REFUTED"),    # 非高危：单 correctness 透镜裁决
        ([V("CONFIRMED")], False, "CONFIRMED"),
    ]
    for votes, high, expected in cases:
        r = run_case([], {"file": "x", "line": 1, "summary": "s"}, votes, high)
        assert r["verdict"] == expected, f"{[v['verdict'] for v in votes]} high={high} -> {r['verdict']}，期望 {expected}"
