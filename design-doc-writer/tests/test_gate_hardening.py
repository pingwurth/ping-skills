"""2026-11-09 max 审查回修回归：门禁完整性 / 死锁逃逸 / 假 FAIL（A、B、C 簇 + E3）。

每条测试对应审查报告 design-doc-writer/docs/code-review/2026-11-09-max-review.md
中的一个编号（A1…C3），防同类回归。
"""
from __future__ import annotations

import os

from conftest import make_analysis, make_design, read_ledger, write_doc


def answer_all(dd, *answers):
    for qid, ans in answers:
        assert dd("q-answer", qid, "--answer", ans)[0] == 0


def write_adr(repo, rel, text="# 决策\n\n一段。\n"):
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


# ---------- A1/A2：围栏代码块不参与门禁判定 ----------

def test_a1_fenced_heading_does_not_satisfy_section_gate(dd, grilling):
    """围栏内引用的 `## 术语表` 不能顶替真实章节（空/缺失两个方向）。"""
    answer_all(dd, ("Q001", "x"), ("Q002", "y"))
    # 真实「术语表」空 + 围栏骨架里有同名标题 → 必须 FAIL section-empty
    text = make_analysis().replace("## 术语表\n\n内容：术语表的实质内容。", "## 术语表\n")
    text += "\n```markdown\n## 术语表\n\n（引用模板骨架）\n```\n"
    write_doc(grilling, text)
    code, out = dd("check", "--doc", "analysis")
    assert code == 1 and "section-empty" in out and "术语表" in out
    # 真实标题整个缺失，只出现在围栏里 → 必须 FAIL section-missing
    text = make_analysis().replace("## 术语表\n\n内容：术语表的实质内容。", "## 其他\n\nx")
    text += "\n````markdown\n## 术语表\n\n（四反引号围栏骨架，内含 ``` 的嵌套示例）\n````\n"
    write_doc(grilling, text)
    code, out = dd("check", "--doc", "analysis")
    assert code == 1 and "section-missing" in out and "术语表" in out


def test_a2_fenced_status_line_invisible_and_confirm_does_not_rewrite_fence(dd, grilling):
    """围栏内的 `状态: Draft` 不满足门禁；confirm 只改真实状态行。"""
    answer_all(dd, ("Q001", "x"), ("Q002", "y"))
    fenced = "\n```markdown\n# 示例 需求分析\n\n状态: Draft\n\n## 背景与目标\n\n示例\n```\n"
    # 无真实状态行 → check FAIL，confirm 被拒
    text = make_analysis().replace("状态: Draft\n\n", "")
    write_doc(grilling, text + fenced)
    code, out = dd("check", "--doc", "analysis")
    assert code == 1 and "status-line" in out
    assert dd("confirm", "--doc", "analysis", "--because", "x")[0] == 1
    # 有真实状态行 → confirm 通过；围栏内示例行保持 Draft 不被改写
    p = write_doc(grilling, make_analysis() + fenced)
    assert dd("confirm", "--doc", "analysis", "--because", "ok")[0] == 0
    after = p.read_text(encoding="utf-8")
    assert "状态: Confirmed" in after.splitlines()[2]
    assert "# 示例 需求分析\n\n状态: Draft" in after  # 围栏示例原样保留


def test_e3_confirm_preserves_blank_line_after_status(dd, grilling):
    """confirm 逐行替换状态行，不再吞掉状态行后的空行。"""
    answer_all(dd, ("Q001", "x"), ("Q002", "y"))
    p = write_doc(grilling, make_analysis())
    assert "状态: Draft\n\n## 背景与目标" in p.read_text(encoding="utf-8")
    assert dd("confirm", "--doc", "analysis", "--because", "ok")[0] == 0
    assert "状态: Confirmed\n\n## 背景与目标" in p.read_text(encoding="utf-8")


# ---------- A3：全角冒号标记 ----------

def test_a3_fullwidth_colon_markers_reconciled(dd, grilling):
    dd("q-answer", "Q001", "--defer", "--reason", "等排期")
    dd("q-answer", "Q002", "--answer", "异步")
    # 全角 [延期：Q001] 视为已标注 → 不再假 FAIL
    write_doc(grilling, make_analysis(extra="- [延期：Q001] 影响面说明"))
    code, out = dd("check", "--doc", "analysis")
    assert code == 0, out


def test_a3_fullwidth_unresolved_marker_fails(dd, grilling):
    """全角过期 [未决：Q001] 同样要拦——不能只认半角。"""
    answer_all(dd, ("Q001", "x"), ("Q002", "y"))
    write_doc(grilling, make_analysis(extra="此处 [未决：Q001] 仍悬着"))
    code, out = dd("check", "--doc", "analysis")
    assert code == 1 and "unresolved-marker" in out and "Q001" in out


# ---------- A4：mermaid 围栏变体不再被跳过 ----------

def test_a4_mermaid_fence_variants_are_checked(dd, grilling):
    answer_all(dd, ("Q001", "x"), ("Q002", "y"))
    for fence in ("```Mermaid", "``` mermaid", "````mermaid"):
        block = f"\n{fence}\npie\n  \"a\" : 1\n```\n" if not fence.startswith("````") else \
                f"\n{fence}\npie\n  \"a\" : 1\n````\n"
        write_doc(grilling, make_analysis(extra=block))
        code, out = dd("check", "--doc", "analysis")
        assert code == 1 and "mermaid-type" in out and "pie" in out, fence


# ---------- A5：confirm/finish 的阶段守卫 ----------

def test_a5_confirm_rejected_at_intake_bypasses_interview(dd, booted):
    """intake（零问题零轮次）直接 confirm 必须被拒，访谈不可整体绕过。"""
    write_doc(booted, make_analysis())
    code, out = dd("confirm", "--doc", "analysis", "--because", "x")
    assert code == 2 and "intake" in out
    led = read_ledger(booted)
    assert led["docs"]["analysis"]["status"] == "draft"


# ---------- A6：design 确认复检已确认的 analysis ----------

def test_a6_design_check_rechecks_degraded_analysis(dd, grilling):
    answer_all(dd, ("Q001", "x"), ("Q002", "y"))
    write_doc(grilling, make_analysis())
    assert dd("confirm", "--doc", "analysis", "--because", "ok")[0] == 0
    assert dd("phase", "design-doc")[0] == 0
    # 确认后劣化：删「术语表」标题 + 残留未决标记（阶段 3 允许的增量编辑路径）
    degraded = make_analysis().replace("## 术语表\n\n内容：术语表的实质内容。", "## 其他\n\nx")
    degraded = degraded.replace("内容：术语表的实质内容。", "")
    degraded += "\n残留 [未决:Q001]\n"
    write_doc(grilling, degraded)
    write_doc(grilling, make_design(), analysis=False)
    code, out = dd("check", "--doc", "design")
    assert code == 1 and "section-missing" in out and "unresolved-marker" in out
    assert dd("confirm", "--doc", "design", "--because", "x")[0] == 1


# ---------- A7：manifest 的 done/confirmed 守卫 ----------

def test_a7_manifest_locked_after_done_and_confirmed_doc(dd, grilling):
    answer_all(dd, ("Q001", "x"), ("Q002", "y"))
    write_doc(grilling, make_analysis())
    assert dd("confirm", "--doc", "analysis", "--because", "ok")[0] == 0
    assert dd("phase", "design-doc")[0] == 0
    # analysis 已 confirmed：其章节清单锁定（停用=削弱依据，启用=引入未写章节）
    assert dd("manifest", "--disable", "glossary", "--reason", "裁")[0] == 2
    assert dd("manifest", "--enable", "background", "--reason", "加")[0] == 2
    # design 仍 draft：design 章节可调
    assert dd("manifest", "--enable", "cache", "--reason", "web 项目")[0] == 0
    # 终态后全部锁定
    write_doc(grilling, make_design(), analysis=False)
    assert dd("confirm", "--doc", "design", "--because", "ok")[0] == 0
    assert dd("finish")[0] == 0
    assert dd("manifest", "--enable", "release", "--reason", "r")[0] == 2


# ---------- A8：finish 重跑门禁 ----------

def test_a8_finish_reruns_checks_blocks_deferred_trapped_in_done(dd, grilling):
    answer_all(dd, ("Q001", "x"), ("Q002", "y"))
    write_doc(grilling, make_analysis())
    assert dd("confirm", "--doc", "analysis", "--because", "ok")[0] == 0
    assert dd("phase", "design-doc")[0] == 0
    write_doc(grilling, make_design(), analysis=False)
    assert dd("confirm", "--doc", "design", "--because", "ok")[0] == 0
    # confirm 之后 design-doc 阶段仍可 q-add/--defer → finish 必须拦下
    assert dd("q-add", "--dimension", "scope", "--title", "补充？",
              "--body", "b", "--recommendation", "r")[0] == 0
    assert dd("q-answer", "Q003", "--defer", "--reason", "等排期")[0] == 0
    code, out = dd("finish")
    assert code == 1 and "design-prereq" in out and "deferred=1" in out
    assert read_ledger(grilling)["phase"] == "design-doc"  # 未被封进 done


# ---------- A9：模板省略号 `……` 进门禁 ----------

def test_a9_ellipsis_placeholder_flagged(dd, grilling):
    answer_all(dd, ("Q001", "x"), ("Q002", "y"))
    write_doc(grilling, make_analysis(extra="| …… | …… |"))
    code, out = dd("check", "--doc", "analysis")
    assert code == 1 and "placeholder" in out and "……" in out


def test_a9_prose_ellipsis_not_flagged(dd, grilling):
    answer_all(dd, ("Q001", "x"), ("Q002", "y"))
    write_doc(grilling, make_analysis(extra="正常行文的省略：先做导出、再做多格式等等……与占位无关。"))
    assert dd("check", "--doc", "analysis")[0] == 0


# ---------- B1：ADR 编号台账占坑 ----------

def test_b1_consecutive_adr_assignments_do_not_collide(dd, grilling):
    answer_all(dd, ("Q001", "a"), ("Q002", "b"))
    assert dd("q-answer", "Q001", "--answer", "异步", "--adr", "--adr-slug", "cache")[0] == 0
    assert dd("q-answer", "Q002", "--answer", "REST", "--adr", "--adr-slug", "auth")[0] == 0
    qs = read_ledger(grilling)["questions"]
    assert qs[0]["adr"]["path"] == "docs/adr/0001-cache.md"
    assert qs[1]["adr"]["path"] == "docs/adr/0002-auth.md"  # 不再撞 0001


# ---------- B2：ADR 分配生命周期可逃逸 ----------

def test_b2_no_adr_clears_stale_assignment(dd, grilling):
    answer_all(dd, ("Q001", "a"), ("Q002", "b"))
    dd("q-answer", "Q001", "--answer", "A", "--adr", "--adr-slug", "export")
    write_adr(grilling, "docs/adr/0001-export.md")
    # 决策反转：--no-adr 清除分配，删除矛盾文件后不再永久 FAIL
    assert dd("q-answer", "Q001", "--answer", "B", "--no-adr")[0] == 0
    assert read_ledger(grilling)["questions"][0]["adr"] is None
    os.remove(grilling / "docs" / "adr" / "0001-export.md")
    write_doc(grilling, make_analysis())
    assert dd("check", "--doc", "analysis")[0] == 0


def test_b2_defer_clears_adr_assignment(dd, grilling):
    answer_all(dd, ("Q002", "b"))
    dd("q-answer", "Q001", "--answer", "A", "--adr", "--adr-slug", "export")
    assert dd("q-answer", "Q001", "--defer", "--reason", "再想想")[0] == 0
    assert read_ledger(grilling)["questions"][0]["adr"] is None
    # 延期后无 adr-file 压力
    write_doc(grilling, make_analysis(deferred=["Q001"]))
    assert dd("check", "--doc", "analysis")[0] == 0


# ---------- B3：状态行同步失败不得谎报成功 ----------

def test_b3_confirm_sync_failure_aborts_without_ledger_change(dd, grilling):
    answer_all(dd, ("Q001", "x"), ("Q002", "y"))
    p = write_doc(grilling, make_analysis())
    # 只读目录注入写失败（文件级 444 挡不住 os.replace，目录级才挡得住创建临时文件）
    p.parent.chmod(0o555)
    try:
        code, out = dd("confirm", "--doc", "analysis", "--because", "ok")
        assert code == 2 and "同步失败" in out and "台账未变更" in out
        led = read_ledger(grilling)
        assert led["docs"]["analysis"]["status"] == "draft"
        assert "状态: Draft" in p.read_text(encoding="utf-8")
    finally:
        p.parent.chmod(0o755)


# ---------- B4：漂移自愈与崩溃窗口恢复 ----------

def test_b4_confirm_heals_status_drift(dd, grilling):
    """confirmed 后作者按模板惯例重写 Draft → 重跑 confirm 自愈（不再死锁）。"""
    answer_all(dd, ("Q001", "x"), ("Q002", "y"))
    p = write_doc(grilling, make_analysis())
    assert dd("confirm", "--doc", "analysis", "--because", "第一次")[0] == 0
    assert dd("phase", "grilling")[0] == 0  # 合法回边
    p.write_text(p.read_text(encoding="utf-8").replace("状态: Confirmed", "状态: Draft"), encoding="utf-8")
    assert dd("check", "--doc", "analysis")[0] == 1  # check 严格对账仍报漂移
    code, out = dd("confirm", "--doc", "analysis", "--because", "重新确认")
    assert code == 0, out
    assert "状态: Confirmed" in p.read_text(encoding="utf-8")
    assert dd("check", "--doc", "analysis")[0] == 0


def test_b4_confirm_heals_doc_confirmed_ledger_draft_window(dd, grilling):
    """崩溃窗口（文档 Confirmed / 台账 draft）重跑 confirm 自愈。"""
    answer_all(dd, ("Q001", "x"), ("Q002", "y"))
    p = write_doc(grilling, make_analysis())
    p.write_text(p.read_text(encoding="utf-8").replace("状态: Draft", "状态: Confirmed"), encoding="utf-8")
    code, out = dd("confirm", "--doc", "analysis", "--because", "补台账")
    assert code == 0, out
    assert read_ledger(grilling)["docs"]["analysis"]["status"] == "confirmed"


# ---------- C1：mermaid %% 指令/注释 ----------

def test_c1_mermaid_init_directive_and_comment_ok(dd, grilling):
    answer_all(dd, ("Q001", "x"), ("Q002", "y"))
    block = ("\n```mermaid\n%%{init: {'theme':'dark'}}%%\n%% 一张状态图\n"
             "stateDiagram-v2\n    [*] --> S1\n    S1 --> [*]\n```\n")
    write_doc(grilling, make_analysis(extra=block))
    code, out = dd("check", "--doc", "analysis")
    assert code == 0 and "mermaid-type" not in out


# ---------- C2：ADR 目录年份不得冒充编号 ----------

def test_c2_adrs_dir_year_fragment_not_confused(dd, repo, tmp_path):
    """--adrs-dir 含年份片段（docs/2024-adr）时不得报 ADR-2024 假 FAIL。"""
    dd("init", "--feature", "f", "--adrs-dir", "docs/2024-adr", "--repo", str(repo),
       cwd=tmp_path)
    dd("phase", "analyzing", "--repo", str(repo), cwd=tmp_path)
    dd("q-add", "--dimension", "technical", "--title", "同步还是异步", "--body", "b",
       "--recommendation", "异步", "--repo", str(repo), cwd=tmp_path)
    dd("q-answer", "Q001", "--answer", "异步任务", "--adr", "--adr-slug", "async",
       "--repo", str(repo), cwd=tmp_path)
    write_adr(repo, "docs/2024-adr/0001-async.md")
    from conftest import write_doc as _wd
    _wd(repo, make_analysis(adr_refs=[("Q001", "ADR-0001")]))
    code, out = dd("check", "--doc", "analysis", "--repo", str(repo), cwd=tmp_path)
    assert code == 0, out


# ---------- C3：占位符词边界 ----------

def test_c3_placeholder_word_boundaries(dd, grilling):
    answer_all(dd, ("Q001", "x"), ("Q002", "y"))
    # 正常中文行文（等待定时）与标识符（/TODOS）不误伤
    write_doc(grilling, make_analysis(extra="系统等待定时任务完成后回调；接口见 /api/TODOS 列表。"))
    code, out = dd("check", "--doc", "analysis")
    assert code == 0, out
    # 裸 TODO / 独立 待定 仍要拦
    write_doc(grilling, make_analysis(extra="此处 TODO 未处理"))
    assert dd("check", "--doc", "analysis")[0] == 1
    write_doc(grilling, make_analysis(extra="结论：待定。"))
    code, out = dd("check", "--doc", "analysis")
    assert code == 1 and "待定" in out
