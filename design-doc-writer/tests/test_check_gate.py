"""check 门禁：每个检查项至少一正一反。"""
from __future__ import annotations

from conftest import make_analysis, make_design, read_ledger, write_doc, write_ledger


def answer_all(dd, repo, *answers):
    for qid, ans in answers:
        assert dd("q-answer", qid, "--answer", ans)[0] == 0


def good_analysis_state(dd, grilling):
    answer_all(dd, grilling, ("Q001", "最小集"), ("Q002", "异步"))
    write_doc(grilling, make_analysis())


def test_statediagram_v2_version_suffix_accepted(dd, grilling):
    """E2E 回归：stateDiagram-v2 的 -v2 版本后缀不得导致白名单误拒。"""
    answer_all(dd, grilling, ("Q001", "x"), ("Q002", "y"))
    write_doc(grilling, make_analysis(
        extra="```mermaid\nstateDiagram-v2\n    [*] --> S1\n    S1 --> [*]\n```"))
    code, out = dd("check", "--doc", "analysis")
    assert code == 0, out
    assert "mermaid-type" not in out


def test_h3_subsection_counts_as_section_content(dd, grilling):
    """E2E 回归：H3 子标题结构化的章节不算空章节（边界只认 H1/H2）。"""
    answer_all(dd, grilling, ("Q001", "x"), ("Q002", "y"))
    text = make_analysis().replace(
        "## 术语表\n\n内容：术语表的实质内容。",
        "## 术语表\n\n### 领域词\n\n**导出任务**: 定义。")
    write_doc(grilling, text)
    code, out = dd("check", "--doc", "analysis")
    assert code == 0, out
    assert "section-empty" not in out


def test_pass_when_analysis_complete(dd, grilling):
    good_analysis_state(dd, grilling)
    code, out = dd("check", "--doc", "analysis")
    assert code == 0, out
    assert "FAIL 0" in out


def test_missing_doc_fails(dd, grilling):
    answer_all(dd, grilling, ("Q001", "x"), ("Q002", "y"))
    code, out = dd("check", "--doc", "analysis")
    assert code == 1 and "doc-missing" in out


def test_missing_and_empty_sections(dd, grilling):
    answer_all(dd, grilling, ("Q001", "x"), ("Q002", "y"))
    text = make_analysis().replace("## 范围：IN 与 OUT\n\n内容：范围：IN 与 OUT的实质内容。", "## 其他\n\nx")
    text = text.replace("内容：术语表的实质内容。", "")
    write_doc(grilling, text)
    code, out = dd("check", "--doc", "analysis")
    assert code == 1
    assert "section-missing" in out and "范围：IN 与 OUT" in out
    assert "section-empty" in out and "术语表" in out


def test_open_questions_block_analysis(dd, grilling):
    write_doc(grilling, make_analysis())  # 文档齐全但 Q001/Q002 仍 open
    code, out = dd("check", "--doc", "analysis")
    assert code == 1 and "open-questions" in out


def test_fence_imbalance(dd, grilling):
    answer_all(dd, grilling, ("Q001", "x"), ("Q002", "y"))
    write_doc(grilling, make_analysis() + "\n```\n散落围栏\n")
    code, out = dd("check", "--doc", "analysis")
    assert code == 1 and "fence-imbalance" in out


def test_mermaid_type_whitelist_and_alias(dd, grilling):
    answer_all(dd, grilling, ("Q001", "x"), ("Q002", "y"))
    write_doc(grilling, make_analysis(extra="\n```mermaid\nmindmap\n  根\n  叶\n```\n"))
    code, out = dd("check", "--doc", "analysis")
    assert code == 1 and "mermaid-type" in out and "mindmap" in out
    # graph 是 flowchart 别名 → 放行
    write_doc(grilling, make_analysis(extra="\n```mermaid\ngraph TD\n  A --> B\n```\n"))
    assert dd("check", "--doc", "analysis")[0] == 0


def test_complexity_warn_only(dd, grilling):
    answer_all(dd, grilling, ("Q001", "x"), ("Q002", "y"))
    chain = "\n".join(f"  N{i:02d} --> N{i + 1:02d}" for i in range(1, 20))
    write_doc(grilling, make_analysis(extra=f"\n```mermaid\nflowchart TD\n{chain}\n```\n"))
    code, out = dd("check", "--doc", "analysis")
    assert code == 0  # WARN 不 FAIL
    assert "WARN" in out and "mermaid-complex" in out


def test_unresolved_marker_always_fails(dd, grilling):
    answer_all(dd, grilling, ("Q001", "x"), ("Q002", "y"))
    write_doc(grilling, make_analysis(extra="\n此处 [未决:Q001] 仍悬着\n"))
    code, out = dd("check", "--doc", "analysis")
    assert code == 1 and "unresolved-marker" in out


def test_deferred_reconciliation_both_ways(dd, grilling):
    dd("q-answer", "Q001", "--defer", "--reason", "等排期")
    dd("q-answer", "Q002", "--answer", "异步")
    # 台账有延期但文档没标 → FAIL（missing 方向）
    write_doc(grilling, make_analysis())
    code, out = dd("check", "--doc", "analysis")
    assert code == 1 and "deferred-reconcile" in out and "Q001" in out
    # 文档标了台账不存在的延期 → FAIL（dangling 方向）
    write_doc(grilling, make_analysis(deferred=["Q888"]))
    code, out = dd("check", "--doc", "analysis")
    assert code == 1 and "deferred-reconcile" in out and "Q888" in out
    # 一致 → PASS
    write_doc(grilling, make_analysis(deferred=["Q001"]))
    assert dd("check", "--doc", "analysis")[0] == 0


def test_placeholder_fails(dd, grilling):
    answer_all(dd, grilling, ("Q001", "x"), ("Q002", "y"))
    write_doc(grilling, make_analysis().replace("内容：术语表的实质内容。", "内容：术语表待补充。"))
    code, out = dd("check", "--doc", "analysis")
    assert code == 1 and "placeholder" in out and "待补充" in out


def test_status_line_missing_and_drift(dd, grilling):
    answer_all(dd, grilling, ("Q001", "x"), ("Q002", "y"))
    write_doc(grilling, make_analysis().replace("状态: Draft\n\n", ""))
    code, out = dd("check", "--doc", "analysis")
    assert code == 1 and "status-line" in out
    write_doc(grilling, make_analysis(status="Confirmed"))  # 手改漂移
    code, out = dd("check", "--doc", "analysis")
    assert code == 1 and "status-line" in out and "Draft" in out


def test_adr_three_way_reconciliation(dd, grilling):
    dd("q-answer", "Q001", "--answer", "异步", "--adr", "--adr-slug", "async-job")
    dd("q-answer", "Q002", "--answer", "REST")
    # a: 分配了 ADR 但文件不存在
    write_doc(grilling, make_analysis(adr_refs=[("Q001", "ADR-0001")]))
    code, out = dd("check", "--doc", "analysis")
    assert code == 1 and "adr-file" in out
    # 文件存在但文档没引用 → c 方向
    adr = grilling / "docs" / "adr" / "0001-async-job.md"
    adr.parent.mkdir(parents=True, exist_ok=True)
    adr.write_text("# 导出走异步任务\n\n数据量大，同步必超时。\n", encoding="utf-8")
    write_doc(grilling, make_analysis())
    code, out = dd("check", "--doc", "analysis")
    assert code == 1 and "未被任何决策表引用" in out
    # 文件 + 引用齐全 → PASS
    write_doc(grilling, make_analysis(adr_refs=[("Q001", "ADR-0001")]))
    assert dd("check", "--doc", "analysis")[0] == 0
    # b: 文档引用了不存在的 ADR → FAIL
    write_doc(grilling, make_analysis(adr_refs=[("Q002", "ADR-0099")]))
    code, out = dd("check", "--doc", "analysis")
    assert code == 1 and "adr-ref" in out and "0099" in out


def confirmed_analysis(dd, grilling):
    answer_all(dd, grilling, ("Q001", "最小集"), ("Q002", "异步"))
    write_doc(grilling, make_analysis())
    assert dd("confirm", "--doc", "analysis", "--because", "用户：分析没问题")[0] == 0
    assert dd("phase", "design-doc")[0] == 0


def test_design_prereq_requires_confirmed_analysis_and_zero_deferred(dd, grilling):
    # 未确认 analysis 时直接检查 design：前置 FAIL
    answer_all(dd, grilling, ("Q001", "x"), ("Q002", "y"))
    write_doc(grilling, make_design())
    code, out = dd("check", "--doc", "design")
    assert code == 1 and "design-prereq" in out
    # 确认 analysis 后，把 Q001 改延期 → 零 deferred 破坏 → FAIL
    led = read_ledger(grilling)
    led["docs"]["analysis"]["status"] = "confirmed"
    led["questions"][0]["status"] = "deferred"
    write_ledger(grilling, led)
    write_doc(grilling, make_design())
    code, out = dd("check", "--doc", "design")
    assert code == 1 and "design-prereq" in out


def test_design_deferred_marker_zero_tolerance(dd, grilling):
    confirmed_analysis(dd, grilling)
    write_doc(grilling, make_design(extra="\n- [延期:Q001] 说明\n"), analysis=False)
    code, out = dd("check", "--doc", "design")
    assert code == 1 and "deferred-marker" in out


def test_design_pass_and_check_all(dd, grilling):
    confirmed_analysis(dd, grilling)
    write_doc(grilling, make_design(), analysis=False)
    assert dd("check", "--doc", "design")[0] == 0
    assert dd("check", "--doc", "all")[0] == 0


def test_check_infers_doc_from_phase(dd, grilling):
    good_analysis_state(dd, grilling)
    code, out = dd("check")  # phase=grilling → 推断 analysis
    assert code == 0 and "analysis" in out
