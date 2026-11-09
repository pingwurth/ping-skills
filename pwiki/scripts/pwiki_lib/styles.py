"""风格档位(单一事实源) — 两种写作口径的门禁参数表。

concise = qwiki/Qoder 口径: 短页、sheet 1-3 句、无硬性行数下限; qwiki 的
legacy 观测数字(225-611 行、40-70 引用、6-8 图)保留为**警告带**, LINING
硬错只在 deep。
deep = repowiki 口径: 400-520 行、引用分档(D15)、章节来源/图表来源为
错误级、知识层引用强制(D16)。

references/validation.md 的尺寸表必须与本表一致 —— test_styles.py 断言
文档表 ⊆ 预设值(修 repowiki 文档与代码漂移)。
"""

from __future__ import annotations

from . import config

STYLE_PRESETS = {
    "concise": {
        "page_lines": None,            # 无行数下限(自然长度)
        # 词上限 3300 本身即 max_article_words(3000)×1.1 的产物 —— 代码
        # 不再二次 ×1.1; 软执行(size_hard=False)恒为警告
        "page_words": (None, 3300),
        "mermaid": (0, 8),
        "cites": (1, 70),              # 下限 1 条; 超 70 警告
        "cited_files": (1, None),
        "cites_band": None,            # 无分档
        "cite_line_ratio": 0.85,       # 行级引用占比(qwiki 曾 LINING 硬错; 降为警告)
        "size_hard": False,            # 尺寸类只在 LINING 且此键为真时升错误
        "section_sources": "warn",     # 章节来源缺失 = 警告
        "diagram_sources": "warn",
        # 指派模块未提及: concise 的种子页常挂根模块(标题是仓库名), 全文
        # 不必复述 —— 警告即可; deep 的模块锚定架构页按模块写, 升错误
        "module_mention": "warn",
        "sheet_max_lines": 40,        # concise 速查表行数上限(r5 P70 收进预设)
        "extra_cite_files": 10,       # 引用文件超出声明 scope 的容忍度
        "code_evidence_min": 2,       # 代码引用下限(纯文档不佐证代码行为)
        "sections_tolerance_div": 3,  # 缺章节来源块的容忍比例 n/3
        "knowledge_citations": {"card": 0, "sheet": 0},   # 证据走 source_files
        "sheets": "short",             # 1-3 句, 无链接无图
        "export": "legacy",            # 注入 <cite> + ## 目录(幂等不落库)
        # required_sections 兜底是否盖章(九节模板): 走预设键而非 style 字面
        # 比较 —— 第三个风格曾按否定字面量静默继承 deep 的模板
        "required_sections_fallback": False,
        "cite_block_required": False,
    },
    "deep": {
        "page_lines": (400, 520),
        "page_words": (None, None),
        "mermaid": (5, 8),             # 0 图 = 错误
        "cites": (1, 120),             # 分档另算; 总上限兜底
        "cited_files": (6, 13),
        # D15: 引用链接数按页面类别分档
        "cites_band": {"thin_leaf": (40, 70), "standard": (60, 90),
                       "module": (60, 110), "arch_ai_api": (80, 120)},
        "cite_line_ratio": 0.85,       # 行级引用占比(qwiki ARTICLE_CITE_LINE_RATIO; 警告级)
        "size_hard": True,             # 尺寸类 LINING 硬错(deep 口径)
        "section_sources": "error",    # 章节来源缺失 = 错误
        "diagram_sources": "error",
        "module_mention": "error",     # 模块锚定架构页须点名所属模块(r5 P32)
        "extra_cite_files": 10,
        "code_evidence_min": 2,
        "sections_tolerance_div": 3,
        "knowledge_citations": {"card": 3, "sheet": 1},   # D16 错误级
        "sheets": "full",
        "export": "as-written",        # writer 自带 cite 块; 门禁查块==正文集合
        "required_sections_fallback": True,
        "cite_block_required": True,   # <cite> 块必须存在且 == 正文集合
    },
}


def preset(style: str) -> dict:
    return STYLE_PRESETS.get(style, STYLE_PRESETS[config.DEFAULT_STYLE])


def cite_band_for(page: dict, preset_: dict):
    """按页面类别取 D15 分档; 无分档返回 None。

    宽带(80-120)判定: type=overview, 或 pack_key 命中
    config.WIDE_CITE_PACK_KEYS —— 该集合与 themes.build_packs 的包 key
    对应(集中到 config 是为了让"加包要同步改分档"可见, 不再埋在样式模块)。"""
    band = preset_.get("cites_band")
    if not band:
        return None
    pack = str(page.get("pack_key") or "")
    kind = str(page.get("kind") or "page")
    atype = str(page.get("type") or "")
    if pack in config.WIDE_CITE_PACK_KEYS or atype == "overview":
        return band["arch_ai_api"]
    if kind == "module_sheet":
        return band["module"]
    n_src = len(page.get("source_files") or [])
    if n_src and n_src <= 2:
        return band["thin_leaf"]
    return band["standard"]
