"""gates.py: 双风格门禁矩阵、D15 分档、D16、D18/D23 修复、off-by-one。"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from pwiki_lib import config, gates, styles


CITE_FILES = [f"src/{c}.py" for c in "abcdefgh"]


def _page(title="测试页", n_sections=6, per_section_cites=12, diagrams=6,
          lines=450, cite_block=True):
    body = [f"# {title}", ""]
    if cite_block:
        body += ["<cite>", "**本文引用的文件**"] + \
            [f"- [{f}](file://{f})" for f in CITE_FILES] + ["</cite>", ""]
    body += ["## 概述", "", "这是概述。", ""]
    for i in range(n_sections):
        body += [f"## 主题{i}", "", "正文内容若干。", ""]
        if diagrams and i < diagrams:
            body += ["```mermaid", "graph TB", "A[模块A] --> B[模块B]", "```", "",
                     "**图表来源**",
                     "- [src/a.py:1-5](file://src/a.py#L1-L5)", ""]
        body += ["**章节来源**"]
        for j in range(per_section_cites):
            f = CITE_FILES[(i * per_section_cites + j) % len(CITE_FILES)]
            body.append(f"- [{f}:{j+1}-{j+5}](file://{f}#L{j+1}-L{j+5})")
        body.append("")
    while len(body) < lines:
        body.append("正文填充行。")
    return "\n".join(body) + "\n"


def _item(kind="page", **kw):
    base = {"kind": kind, "title": "测试页", "article_type": "deep_dive",
            "pack_key": "", "source_files": list(CITE_FILES)}
    base.update(kw)
    return base


def _repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    for c in "abcdefgh":
        (repo / "src" / f"{c}.py").write_text("\n" * 200)
    return str(repo)


def test_concise_allows_short_page(tmp_path):
    repo = _repo(tmp_path)
    sec_src = "\n**章节来源**\n- [src/a.py:1-3](file://src/a.py#L1-L3)\n"
    content = ("# 测试页\n\n## 概述\n\n短页允许。\n\n## 架构\n\nx\n"
               + sec_src + "\n## 能力\n\nx\n" + sec_src +
               "\n## 模块地图\n\nx\n" + sec_src)
    errs, warns = gates.gate_item(content, _item(article_type="overview"),
                                  repo, "concise", phase="lining")
    assert errs == [], errs
    # 无 <cite>/目录/结论 是 concise 契约(发布层注入); 章节来源缺失只是警告
    assert not any("章节来源" in w for w in warns)  # 有来源块, 不警告


def test_concise_toc_cite_wrapup_stay_warnings(tmp_path):
    """validation.md 契约: schema/toc|cite|wrapup 是风格错误, concise 在
    LINING 也保持警告(曾一律升错误级, 文档为准)。"""
    repo = _repo(tmp_path)
    content = ("# 测试页\n\n## 目录\n- [x](#x)\n\n## 正文\n\n内容。\n\n"
               "## 结论\n总结。\n\n**章节来源**\n"
               "- [src/a.py:1-3](file://src/a.py#L1-L3)\n")
    errs, warns = gates.gate_item(content, _item(), repo, "concise", phase="lining")
    assert not any("目录" in e for e in errs), errs
    assert not any("结论" in e for e in errs), errs
    assert any("目录" in w for w in warns)
    assert any("结论" in w for w in warns)


def test_deep_requires_size_band_and_cite_block(tmp_path):
    repo = _repo(tmp_path)
    errs, warns = gates.gate_item(_page(), _item(), repo, "deep", phase="lining")
    assert errs == [], errs
    # 砍半引用数 → 分档不足
    short = _page(per_section_cites=3, diagrams=5)
    errs, _ = gates.gate_item(short, _item(), repo, "deep", phase="lining")
    assert any("size/citations" in e for e in errs), errs
    # 行数不足
    tiny = _page(lines=150, per_section_cites=12, diagrams=6)
    errs, _ = gates.gate_item(tiny, _item(), repo, "deep", phase="lining")
    assert any("size/page" in e for e in errs)
    # 无 cite 块
    nocite = _page(cite_block=False)
    errs, _ = gates.gate_item(nocite, _item(), repo, "deep", phase="lining")
    assert any("<cite>" in e for e in errs)
    # 同样的页面在 submit 阶段全是警告(双严重度)
    errs2, warns2 = gates.gate_item(short, _item(), repo, "deep", phase="submit")
    assert errs2 == []
    assert warns2


def test_d15_band_by_class(tmp_path):
    repo = _repo(tmp_path)
    # thin leaf(单源)与 arch 页用不同分档
    band = styles.cite_band_for({"pack_key": "architecture"}, styles.preset("deep"))
    assert band == (80, 120)
    band = styles.cite_band_for({"pack_key": "", "source_files": ["a"]},
                                styles.preset("deep"))
    assert band == (40, 70)


def test_citation_truthiness_both_styles(tmp_path):
    repo = _repo(tmp_path)
    content = ("# 测试页\n\n## 正文\n\n引用不存在的文件。\n\n**章节来源**\n"
               "- [src/ghost.py:1-3](file://src/ghost.py#L1-L3)\n")
    for style in ("concise", "deep"):
        errs, _ = gates.gate_item(content, _item(), repo, style, phase="submit")
        assert any("ghost" in e for e in errs)
    # 越界引用
    content2 = ("# 测试页\n\n## 正文\n\nx\n\n**章节来源**\n"
                "- [src/a.py:1-99999](file://src/a.py#L1-L99999)\n")
    errs, _ = gates.gate_item(content2, _item(), repo, "concise", phase="submit")
    assert any("citation/range" in e for e in errs)


def test_d23_off_by_one_and_fix(tmp_path):
    repo = _repo(tmp_path)
    # b.py 实际 200 行; 引 201 是差一(可修复), 引 250 是真错误
    content = ("# 测试页\n\n## 正文\n\nx\n\n**章节来源**\n"
               "- [src/b.py:1-201](file://src/b.py#L1-L201)\n"
               "- [src/b.py:1-250](file://src/b.py#L1-L250)\n")
    errs, _ = gates.gate_item(content, _item(), repo, "deep", phase="lining")
    assert any("off-by-one" in e for e in errs)
    assert any("citation/range" in e for e in errs)
    fixed, n = gates.fix_off_by_one(content, repo)
    assert n == 1
    assert "L1-L201" not in fixed and "L1-L250" in fixed


def test_d23_line_counting_convention(tmp_path):
    """行数口径: 末行无换行也要计数(wc -l 会少算)。"""
    from pwiki_lib.scan import cite_lines
    p = tmp_path / "no_newline.py"
    p.write_bytes(b"a = 1\nb = 2\nc = 3")   # 3 行, 无末换行
    assert cite_lines(str(p)) == 3


def test_d18_fix_section_sources(tmp_path):
    content = ("# 测试页\n\n## 概述\n\n简介。\n\n## 核心机制\n\n"
               "引用 [src/a.py:1-9](file://src/a.py#L1-L9) 与 "
               "[src/b.py:2-8](file://src/b.py#L2-L8)。\n\n## 结尾\n\n完。\n")
    fixed, n = gates.fix_section_sources(content)
    assert n == 1
    assert fixed.count("**章节来源**") == 1
    assert "[src/a.py:1-9]" in fixed and "[src/b.py:2-8]" in fixed
    # 不新增事实: 无引用的节不会被加块
    content2 = "# 测试页\n\n## 核心机制\n\n没有引用的节。\n"
    _, n2 = gates.fix_section_sources(content2)
    assert n2 == 0


def test_card_frontmatter_gate(tmp_path):
    repo = _repo(tmp_path)
    good = ("---\nkind: external_dependency\nname: lodash\n---\n\n"
            "## 1. 总体方案\n事实。\n")
    item = {"kind": "card", "card_kind": "external_dependency", "title": "lodash"}
    errs, warns = gates.gate_item(good, item, repo, "concise", phase="lining")
    assert not any("frontmatter" in e for e in errs)
    bad = ("---\nkind: error_handling\nname: lodash\n---\n\nx\n")
    errs, _ = gates.gate_item(bad, item, repo, "concise", phase="lining")
    assert any("kind" in e for e in errs)
    # deep: D16 卡片 ≥3 行级引用为错误
    errs, _ = gates.gate_item(good, item, repo, "deep", phase="lining")
    assert any("D16" in e for e in errs)
    errs2, warns2 = gates.gate_item(good, item, repo, "deep", phase="submit")
    assert not any("D16" in e for e in errs2)   # submit 只警告


def test_sheet_gates(tmp_path):
    repo = _repo(tmp_path)
    item = {"kind": "module_sheet", "title": "概述", "sheet": "overview",
            "module": "src"}
    concise_sheet = "# 概述\n\n模块负责 X；位于 Y；对外提供 Z。\n"
    errs, _ = gates.gate_item(concise_sheet, item, repo, "concise", phase="lining")
    assert errs == []
    with_links = ("# 概述\n\n模块负责 X。见 [a](file://src/a.py#L1-L5)。\n")
    # 形状类: concise 在 LINING 也保持警告(双严重度模型; 曾硬拒)
    errs, warns = gates.gate_item(with_links, item, repo, "concise",
                                  phase="lining")
    assert not any("file://" in e for e in errs), errs
    assert any("file://" in w for w in warns)
    deep_sheet = ("# 概述\n\n模块负责 X。依据 [src/a.py:1-5](file://src/a.py#L1-L5)。\n\n"
                  "**章节来源**\n- [src/a.py:1-5](file://src/a.py#L1-L5)\n")
    errs, _ = gates.gate_item(deep_sheet, item, repo, "deep", phase="lining")
    # r8 #30: D15 'module' 总量带(60-110)接线 —— 该分支从 gate_item 不可达
    # 时, 低引用深速查表过 LINING 与 references/validation.md 契约相悖
    assert any("速查表分档 60-110" in e for e in errs), errs
    assert not any(e for e in errs
                   if "速查表分档" not in e), errs   # 其余维度干净
    # 引用数进带: 无总量带错误
    in_band = ("# 概述\n\n模块负责 X。依据 "
               + " ".join(f"[a](file://src/a.py#L{i}-L{i})"
                          for i in range(1, 61))
               + "。\n\n**章节来源**\n- [src/a.py:1-5](file://src/a.py#L1-L5)\n")
    errs, _ = gates.gate_item(in_band, item, repo, "deep", phase="lining")
    assert not any("速查表分档" in e for e in errs), errs


def test_h1_contract_hard_error(tmp_path):
    repo = _repo(tmp_path)
    content = ("# 错标题\n\n## 正文\n\nx\n\n**章节来源**\n"
               "- [src/a.py:1-3](file://src/a.py#L1-L3)\n")
    for style in ("concise", "deep"):
        errs, _ = gates.gate_item(content, _item(), repo, style, phase="submit")
        assert any("H1" in e for e in errs)


def test_code_fence_aware_headings(tmp_path):
    """qwiki bug12: 代码栅栏里的 # 注释不是标题。"""
    repo = _repo(tmp_path)
    content = ("# 测试页\n\n## 正文\n\n```bash\n# 这不是标题\n```\n\n"
               "**章节来源**\n- [src/a.py:1-3](file://src/a.py#L1-L3)\n")
    errs, _ = gates.gate_item(content, _item(), repo, "concise", phase="submit")
    assert not any("H1" in e for e in errs)


def test_mermaid_gates(tmp_path):
    repo = _repo(tmp_path)
    content = ("# 测试页\n\n## 正文\n\n```mermaid\nbogusDiagram TB\n```\n\n"
               "**图表来源**\n- [src/a.py:1-3](file://src/a.py#L1-L3)\n\n"
               "**章节来源**\n- [src/a.py:1-3](file://src/a.py#L1-L3)\n")
    # 形状类(submit 全警告; deep LINING 升错误)
    errs, warns = gates.gate_item(content, _item(), repo, "concise", phase="submit")
    assert not any("mermaid/type" in e for e in errs), errs
    assert any("mermaid/type" in w for w in warns)
    # 协议哨兵不是样式(qwiki bug13)
    content2 = ("# 测试页\n\n## 正文\n\n```mermaid\ngraph TB\n"
                "A[\":::NEXT_STEP_BEGIN:::\"] --> B\n```\n\n"
                "**图表来源**\n- [src/a.py:1-3](file://src/a.py#L1-L3)\n\n"
                "**章节来源**\n- [src/a.py:1-3](file://src/a.py#L1-L3)\n")
    errs2, _ = gates.gate_item(content2, _item(), repo, "concise", phase="submit")
    assert not any("class 样式" in e for e in errs2)
