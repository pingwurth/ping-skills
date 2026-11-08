"""styles.py: 预设完整性、分档、风格漂移口径、文档表 ⊆ 预设(防漂移)。"""

from __future__ import annotations

import os
import re

from pwiki_lib import config, styles


def test_both_presets_present_and_complete():
    for name in config.STYLES:
        p = styles.preset(name)
        for key in ("page_lines", "mermaid", "cites", "cited_files",
                    "section_sources", "knowledge_citations", "export"):
            assert key in p, f"{name} 缺 {key}"
    assert styles.preset("bogus") is styles.preset(config.DEFAULT_STYLE)


def test_concise_no_floor_deep_hard():
    assert styles.preset("concise")["page_lines"] is None
    assert styles.preset("deep")["page_lines"] == (400, 520)
    assert styles.preset("concise")["section_sources"] == "warn"
    assert styles.preset("deep")["section_sources"] == "error"
    # D16: deep 卡片≥3 行级引用, sheet≥1; concise 零(证据走 source_files)
    assert styles.preset("deep")["knowledge_citations"] == {"card": 3, "sheet": 1}
    assert styles.preset("concise")["knowledge_citations"] == {"card": 0, "sheet": 0}
    # 导出: concise 注入, deep 原样
    assert styles.preset("concise")["export"] == "legacy"
    assert styles.preset("deep")["export"] == "as-written"


def test_band_by_class():
    d = styles.preset("deep")
    assert styles.cite_band_for({"pack_key": "architecture"}, d) == (80, 120)
    assert styles.cite_band_for({"pack_key": "ai"}, d) == (80, 120)
    assert styles.cite_band_for({"pack_key": "api"}, d) == (80, 120)
    assert styles.cite_band_for({"pack_key": "deploy"}, d) == (60, 90)
    assert styles.cite_band_for({"pack_key": "", "source_files": ["a"]}, d) == (40, 70)
    assert styles.cite_band_for({"pack_key": ""}, d) == (60, 90)
    assert styles.cite_band_for({}, styles.preset("concise")) is None


def test_validation_doc_matches_presets():
    """references/validation.md 的尺寸表必须与 STYLE_PRESETS 双向对齐
    (防文档漂移, repowiki 教训: 文档列了代码没有的检查)。

    按行解析尺寸表: 每一行的数字带与预设值逐一对照(单个带漂移即失败);
    反向 —— 预设里已建档键的每个数值界都必须落在对应行里(doc ⊇ presets)。
    对行序不敏感, 对数字严格。"""
    doc = os.path.join(os.path.dirname(__file__), "..", "references",
                       "validation.md")
    assert os.path.isfile(doc), "references/validation.md 缺失 —— 文档与" \
        "预设同源是契约的一部分, 不许静默跳过"
    text = open(doc, encoding="utf-8").read()
    deep = styles.preset("deep")
    concise = styles.preset("concise")

    # ---- 尺寸表按行解析(| 维度 | concise | deep |) ----
    rows = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not (stripped.startswith("|") and stripped.endswith("|")):
            continue
        cells = [c.strip() for c in stripped[1:-1].split("|")]
        if len(cells) < 3 or cells[0] in ("维度", "") \
                or set(cells[0]) <= {"-", ":", " "}:
            continue
        rows[cells[0]] = {"concise": cells[1], "deep": cells[2]}

    def row(needle):
        for label, cells in rows.items():
            if needle in label:
                return cells
        raise AssertionError(f"validation.md 尺寸表缺少行: {needle!r}")

    def band(cell):
        m = re.search(r"(\d+)\s*[–—-]\s*(\d+)", cell)
        return (int(m.group(1)), int(m.group(2))) if m else None

    def bands(cell):
        return [(int(a), int(b))
                for a, b in re.findall(r"(\d+)\s*[–—-]\s*(\d+)", cell)]

    def nums(cell):
        return [int(x) for x in re.findall(r"\d+", cell)]

    _SEV = {"warn": "警告", "error": "错误"}

    # 页面行数: deep 硬带逐一对照; concise 无下限 → 行内不得出现数字带
    cells = row("页面行数")
    assert band(cells["deep"]) == tuple(deep["page_lines"])
    assert concise["page_lines"] is None and band(cells["concise"]) is None
    # 词上限: concise 软执行上界(page_words[1])须出现在行内
    cells = row("词上限")
    assert concise["page_words"][1] in nums(cells["concise"])
    # mermaid 图: 两风格的带各归各行
    cells = row("mermaid")
    assert band(cells["concise"]) == tuple(concise["mermaid"])
    assert band(cells["deep"]) == tuple(deep["mermaid"])
    # 引用链接(D15 分档): 逐档对照 + 档集合完全一致(多列/少列都算漂移)
    cells = row("引用链接")
    assert concise["cites_band"] is None and band(cells["concise"]) is None
    assert concise["cites"][1] in nums(cells["concise"])
    zh_label = {"thin_leaf": "thin leaf", "standard": "standard",
                "module": "module", "arch_ai_api": "宽带"}
    for key, label in zh_label.items():
        m = re.search(re.escape(label) + r"\D*(\d+)\s*[–—-]\s*(\d+)",
                      cells["deep"])
        assert m, f"文档缺 {label}({key})分档"
        assert (int(m.group(1)), int(m.group(2))) == \
            tuple(deep["cites_band"][key]), f"{key} 分档漂移"
    assert set(bands(cells["deep"])) == \
        {tuple(v) for v in deep["cites_band"].values()}
    # 引用文件数
    cells = row("引用文件数")
    assert band(cells["deep"]) == tuple(deep["cited_files"])
    assert concise["cited_files"][1] is None and \
        band(cells["concise"]) is None
    assert concise["cited_files"][0] in nums(cells["concise"])
    # 行级引用占比(两风格同为警告级)
    cells = row("行级引用占比")
    assert round(concise["cite_line_ratio"] * 100) in nums(cells["concise"])
    assert round(deep["cite_line_ratio"] * 100) in nums(cells["deep"])
    # 章节来源/图表来源: 严重度措辞与预设一致
    cells = row("章节来源")
    for key in ("section_sources", "diagram_sources"):
        assert _SEV[concise[key]] in cells["concise"], key
        assert _SEV[deep[key]] in cells["deep"], key
    # 知识层引用(D16): 卡/sheet 各自下限逐一对照
    cells = row("知识层引用")
    kc, kd = concise["knowledge_citations"], deep["knowledge_citations"]
    m = re.search(r"卡\s*/\s*sheet\s*(\d+)", cells["concise"])
    assert m and int(m.group(1)) == kc["card"] == kc["sheet"]
    m = re.search(r"卡\s*≥\s*(\d+)", cells["deep"])
    assert m and int(m.group(1)) == kd["card"], "deep 卡片行级引用下限漂移"
    m = re.search(r"sheet\s*≥\s*(\d+)", cells["deep"])
    assert m and int(m.group(1)) == kd["sheet"], "deep 速查表下限漂移"
    # 导出契约: concise 禁注入物(发布层 legacy 注入) / deep 自带 cite 块
    cells = row("cite")
    assert concise["export"] == "legacy" \
        and concise["cite_block_required"] is False \
        and "禁止" in cells["concise"]
    assert deep["export"] == "as-written" \
        and deep["cite_block_required"] is True \
        and "自带" in cells["deep"]

    # ---- 原口径保留(全文级粗检) ----
    assert f"{deep['page_lines'][0]}–{deep['page_lines'][1]}" in text \
        or f"{deep['page_lines'][0]}-{deep['page_lines'][1]}" in text, \
        "文档应含 deep 行数带"
    for lo, hi in deep["cites_band"].values():
        assert str(lo) in text and str(hi) in text, f"文档应含分档 {lo}-{hi}"
    assert str(concise["mermaid"][1]) in text
    assert str(deep["mermaid"][0]) in text
