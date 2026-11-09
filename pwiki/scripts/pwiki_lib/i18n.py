"""locale 参数化字符串表(修 qwiki zh 硬编码: sheet 身份=slug, 显示名查表)。

qwiki 把五个中文 sheet 名当身份校验(SHEET_NAMES); pwiki 反转: 身份是 slug
(overview/architecture/stack/conventions/config), plan 里写 slug 或本地名都能
解析(sheet_slug 双向解析), 但落盘/比较一律用 slug。知识层目录文件名用显示名
(与 Qoder 导出布局一致: knowledge/<locale>/<模块>/<显示名>.md)。

卡片标题表同构。提示词按 (style, locale) 取: concise 沿用 qwiki 1-3 句零链接
约束; deep 沿用 repowiki 模块表模板并强制 D16 引用。
"""

from __future__ import annotations

import re

from . import config

# sheet 身份(有序; 与 Qoder 五表一一对应)
SHEET_SLUGS = ["overview", "architecture", "stack", "conventions", "config"]

SHEET_DISPLAY = {
    "zh": {
        "overview": "概述",
        "architecture": "架构设计",
        "stack": "技术栈",
        "conventions": "编码规范",
        "config": "特殊配置与命令",
    },
    "en": {
        "overview": "Overview",
        "architecture": "Architecture",
        "stack": "Tech Stack",
        "conventions": "Conventions",
        "config": "Config & Commands",
    },
}

# profile → sheet slugs(qwiki SHEET_PROFILE 的 slug 化)
SHEET_PROFILE = {
    "none": [],
    "lite": ["overview"],
    "standard": ["overview", "architecture", "stack"],
    "full": list(SHEET_SLUGS),
}

CARD_KIND_DISPLAY = {
    "zh": {
        "business_term": "业务术语表",
        "external_dependency": "外部依赖",
        "configuration_system": "配置系统",
        "build_system": "构建系统",
        "dependency_management": "依赖管理",
        "frontend_style": "前端样式规范",
        "logging_system": "日志系统",
        "error_handling": "错误处理",
    },
    "en": {
        "business_term": "Business Terms",
        "external_dependency": "External Dependency",
        "configuration_system": "Configuration System",
        "build_system": "Build System",
        "dependency_management": "Dependency Management",
        "frontend_style": "Frontend Style",
        "logging_system": "Logging System",
        "error_handling": "Error Handling",
    },
}

# 章节来源的本地化写法(gates 双语识别; 发射按 locale 选主形)。
# 图表来源的识别/计数统一走 gates.DIAGRAM_SOURCES_RE(曾留一张零消费者的
# 同义表在这里, 编辑者以为改它就能本地化门禁 —— 实则不接线)
SECTION_SOURCES = {
    "zh": "**章节来源**",
    "en": "**Section sources**",
}
CITE_BLOCK_TITLE = {
    "zh": "**本文引用的文件**",
    "en": "**Files cited in this article**",
}
TOC_TITLE = {
    "zh": "目录",
    "en": "Contents",
}


def toc_title(locale: str) -> str:
    return TOC_TITLE.get(locale, TOC_TITLE["zh"])


def toc_titles() -> set:
    """目录标题集合(注入侧过滤旧标题用)—— 与 TOC_SECTION_RE/TOC_HEADING_RE
    同源; compose 曾手抄第三份 set(TOC_TITLE.values())|{'Table of
    Contents'}, 双向漂移时新 locale 的旧目录标题会漏过滤(被编号进新目录
    又被删节, 锚向已删标题)。"""
    return set(TOC_TITLE.values()) | {"Table of Contents"}


# 注入目录节的识别/剥除正则(发射与剥除共用一份 —— 双份曾漂移漏掉 "Contents"):
# 覆盖 i18n 两拼法 + Qoder 兼容的 "Table of Contents"。
TOC_SECTION_RE = re.compile(
    r"(?ms)^## (?:目录|Table of Contents|Contents)\s*\n.*?(?=^## |\Z)")

# 门禁侧的目录标题行(单行形态, "不要写目录节"警告用)—— 与 TOC_SECTION_RE
# 同源。曾两份漂移漏掉 'Contents': en writer 的合法 Contents 节过得了
# submit, 发布层却把它整节删掉(内容静默丢失)。
TOC_HEADING_RE = re.compile(
    r"^\s*## (?:目录|Table of Contents|Contents)\s*$", re.M)

# --------------------------------------------------------------------------- #
# 提示词(style × locale)
# --------------------------------------------------------------------------- #

_SHEET_PROMPTS_CONCISE_ZH = {
    "overview": "用 1-3 句话说明模块 {title} 的职责边界、在仓库中的位置、对外提供什么。"
                "只写事实，不要写 file:// 链接。",
    "architecture": "用 1-3 句话概括模块 {title} 的内部组成与依赖方向；如需图示，指向"
                    "正文页而不是在这里画图。不要写 file:// 链接。",
    "stack": "列出模块 {title} 所用语言/框架/关键库及其版本（以清单文件为准），"
             "每条一句。不要写 file:// 链接。",
    "conventions": "用 1-3 句归纳模块 {title} 代码里真实存在的命名/分层/错误处理约定，"
                   "不要写通用建议，不要写 file:// 链接。",
    "config": "列出模块 {title} 的构建/运行/测试入口与环境变量名（只写名字），"
              "每条一句。不要写 file:// 链接。",
}

# deep 风格 sheet: repowiki 模块表模板口径 + D16(≥1 条行级引用为错误级门禁)
_SHEET_PROMPTS_DEEP_ZH = {
    "overview": "说明模块 {title} 的职责边界、对外提供什么、在仓库中的位置；3-8 句或"
                "等量条目，末尾给 **章节来源**，至少 1 条行级 file:// 引用（D16）。",
    "architecture": "概括模块 {title} 的内部组成与依赖方向；3-8 句或等量条目，深入到"
                    "关键子目录的分工，末尾给 **章节来源**，至少 1 条行级 file:// 引用。",
    "stack": "列出模块 {title} 所用语言/框架/关键库及版本，以清单文件行级引用为证"
             "（pom/package.json/go.mod 等），每条一句，末尾给 **章节来源**。",
    "conventions": "归纳模块 {title} 代码里真实存在的命名/分层/错误处理约定，逐条"
                   "指到真实文件行；不要通用建议。末尾给 **章节来源**。",
    "config": "列出模块 {title} 的构建/运行/测试入口与环境变量名（只写名字），并指到"
              "定义处行级引用。末尾给 **章节来源**。",
}

# en 表(SHEET_DISPLAY/CARD_KIND_DISPLAY 早已双语; 提示词曾只备 zh, 英文
# wiki 的 sheet job 仍拿中文指令 → 英文内容树里混出中文速查表)
_SHEET_PROMPTS_CONCISE_EN = {
    "overview": "In 1-3 sentences, state what module {title} is responsible for, "
                "where it sits in the repo, and what it exposes. Facts only; "
                "no file:// links.",
    "architecture": "In 1-3 sentences, summarize the internal structure and "
                    "dependency direction of module {title}; point to prose "
                    "pages instead of drawing diagrams. No file:// links.",
    "stack": "List the languages/frameworks/key libraries (with versions, per "
             "manifest files) used by module {title}, one line each. No "
             "file:// links.",
    "conventions": "In 1-3 sentences, capture the naming/layering/error-handling "
                   "conventions actually present in module {title}'s code; no "
                   "generic advice, no file:// links.",
    "config": "List the build/run/test entry points and environment variable "
              "names (names only) for module {title}, one line each. No "
              "file:// links.",
}

_SHEET_PROMPTS_DEEP_EN = {
    "overview": "Describe module {title}: responsibility boundary, what it "
                "exposes, where it sits; 3-8 sentences or equivalent bullets, "
                "ending with **Section sources** and at least 1 line-level "
                "file:// citation (D16).",
    "architecture": "Summarize the internal structure and dependency direction "
                    "of module {title}; 3-8 sentences or equivalent bullets, "
                    "down to key sub-directories, ending with **Section "
                    "sources** and at least 1 line-level file:// citation.",
    "stack": "List languages/frameworks/key libraries and versions for module "
             "{title}, evidenced by line-level citations into manifests "
             "(pom/package.json/go.mod etc.), one line each, ending with "
             "**Section sources**.",
    "conventions": "Capture the naming/layering/error-handling conventions "
                   "actually present in module {title}'s code, each pointing "
                   "to real file lines; no generic advice. End with **Section "
                   "sources**.",
    "config": "List the build/run/test entry points and environment variable "
              "names (names only) for module {title}, with line-level "
              "citations to their definitions. End with **Section sources**.",
}

SHEET_PROMPTS = {
    "concise": {"zh": _SHEET_PROMPTS_CONCISE_ZH, "en": _SHEET_PROMPTS_CONCISE_EN},
    "deep": {"zh": _SHEET_PROMPTS_DEEP_ZH, "en": _SHEET_PROMPTS_DEEP_EN},
}


def sheet_display(slug: str, locale: str) -> str:
    return SHEET_DISPLAY.get(locale, SHEET_DISPLAY["zh"]).get(slug, slug)


def sheet_slug(value: str) -> str:
    """plan 里的 sheet 字段双向解析: slug 原样; 中文/英文显示名反查 slug;
    未知值原样返回(validate 层报错)。"""
    if value in SHEET_SLUGS:
        return value
    for _loc, table in SHEET_DISPLAY.items():
        for slug, name in table.items():
            if value == name:
                return slug
    return value


def card_kind_display(kind: str, locale: str) -> str:
    return CARD_KIND_DISPLAY.get(locale, CARD_KIND_DISPLAY["zh"]).get(kind, kind)


# 页面提示词(style × locale × kind; r4 #59 —— 曾是 themes.prompt_for
# 的 zh-only 分支表, 与本机制平行: --locale en 时 sheet 是英文、页面是
# 中文。模板变量 {title}; 缺 (style, locale, kind) 回落 zh 再回落 default)
_PAGE_PROMPTS = {
    "concise": {
        "zh": {
            "overview": "为该项目写简短概述：产品目标、技术栈、仓库结构、主要模块与阅读路径。短页口径，不写目录/结论。",
            "architecture": "写「{title}」的架构说明：职责、关键组件、依赖方向与边界。短页口径，不写目录/结论。",
            "feature": "为「{title}」写功能说明：用户流程、数据来源、调用链、边界。必须从实际代码出发。短页口径，不写目录/结论。",
            "module": "为模块「{title}」写技术说明：职责与边界、目录结构、关键入口、主要流程。短页口径，不写目录/结论。",
            "default": "写「{title}」技术说明：具体实现、关键文件、数据结构、流程、边界。短页口径，不写目录/结论。",
        },
        "en": {
            "overview": "Write a brief overview of this project: product goals, tech stack, repo structure, main modules and reading paths. Short-page style; no TOC/conclusion.",
            "architecture": "Write the architecture of '{title}': responsibilities, key components, dependency direction and boundaries. Short-page style; no TOC/conclusion.",
            "feature": "Write feature notes for '{title}': user flows, data sources, call chains, boundaries. Ground them in actual code. Short-page style; no TOC/conclusion.",
            "module": "Write technical notes for module '{title}': responsibilities and boundaries, directory layout, key entry points, main flows. Short-page style; no TOC/conclusion.",
            "default": "Write technical notes for '{title}': concrete implementation, key files, data structures, flows, boundaries. Short-page style; no TOC/conclusion.",
        },
    },
    "deep": {
        "zh": {
            "overview": "为该项目创建全面的项目概述：产品目标、目标用户、核心价值、技术栈、仓库结构、快速开始、主要模块与阅读路径。",
            "architecture": "创建架构文档：「{title}」的职责、关键组件、依赖方向、数据与调用流、边界与失败模式。配 {mermaid_band} 张 mermaid 图说明结构与运行时关系（门禁硬性要求 {mermaid_band} 张，少于下限会被 finish 拒绝）。",
            "feature": "为「{title}」创建功能文档：用户流程、页面状态、数据来源、调用链、边界与异常、相关组件。必须从实际页面与调用代码出发。",
            "module": "为模块「{title}」创建技术文档：职责与边界、目录结构、关键入口、核心数据结构、主要流程、依赖方向、扩展点、已知约束与坑。",
            "default": "创建「{title}」技术文档：具体实现、关键文件、数据结构、流程、依赖、边界与常见问题。",
        },
        "en": {
            "overview": "Create a comprehensive project overview: product goals, target users, core value, tech stack, repo structure, quick start, main modules and reading paths.",
            "architecture": "Create architecture documentation for '{title}': responsibilities, key components, dependency direction, data and call flows, boundaries and failure modes. Include {mermaid_band} mermaid diagrams (hard gate: {mermaid_band} at finish).",
            "feature": "Create feature documentation for '{title}': user flows, page states, data sources, call chains, boundaries and errors, related components. Ground them in actual pages and call code.",
            "module": "Create technical documentation for module '{title}': responsibilities and boundaries, directory layout, key entry points, core data structures, main flows, dependency direction, extension points, known constraints and pitfalls.",
            "default": "Create technical documentation for '{title}': concrete implementation, key files, data structures, flows, dependencies, boundaries and common issues.",
        },
    },
}


def page_prompt(kind: str, title: str, style: str, locale: str) -> str:
    """页面 writer 提示词(style × locale × kind; 与 sheet_prompt 同机制)。"""
    table = _PAGE_PROMPTS.get(style,
                             _PAGE_PROMPTS[config.DEFAULT_STYLE])
    by_locale = table.get(locale, table.get("zh", {}))
    tmpl = by_locale.get(kind) or by_locale.get("default") or \
        table.get("zh", {}).get(kind) or table.get("zh", {})["default"]
    out = tmpl.replace("{title}", title)
    if "{mermaid_band}" in out:
        # P34: mermaid 数量带派生自 STYLE_PRESETS(与门禁同源) —— 曾手写
        # '5-8', 预设收紧后整批架构页按旧数写作、finish 全拒
        from .styles import preset as _preset
        _band = _preset(style).get("mermaid") or (5, 8)
        out = out.replace("{mermaid_band}", f"{_band[0]}-{_band[1]}")
    return out


def sheet_prompt(slug: str, style: str, locale: str, title: str) -> str:
    table = SHEET_PROMPTS.get(style, SHEET_PROMPTS[config.DEFAULT_STYLE])
    by_locale = table.get(locale, table.get("zh", {}))
    tmpl = by_locale.get(slug, "")
    return tmpl.replace("{title}", title)
