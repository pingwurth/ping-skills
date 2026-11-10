"""门禁层: 引用/mermaid/文章形状/知识层, 按风格档位参数化。

移植:
  - qwiki.py:2082-2281(extract/validate_citations、cite 块、lint_mermaid、
    代码栅栏感知、article 形状 16 点、frontmatter 解析)
  - repowiki/validate_wiki.py(错误分类学、needs_review、--fix 的 D18/D23)

双严重度模型(qwiki 语义): submit 全部警告(省重写), LINING 按风格出错误级
—— concise 硬错只限引用/结构真实性, 尺寸仅警告; deep 尺寸也硬错。

行数口径(D23): data.count(b"\\n") + 末行无换行补 1 —— wc -l 少算,
writer 曾对 4 行的 package-info.java 引 L5。
"""

from __future__ import annotations

import json
import os
import re
from urllib.parse import unquote

from . import config, i18n
from .config import Ctx
from .scan import cite_lines
from .styles import cite_band_for, preset
from .util import json_list, posix, join_confined
from .yamlite import yaml_load

# --------------------------------------------------------------------------- #
# 基础: 行数 / 引用 / cite 块 / mermaid / frontmatter
# --------------------------------------------------------------------------- #

# 概述类小节免"章节来源"(gate 与 --fix 共用一份 —— 两份曾漂移出不可修复态)
OVERVIEW_SECTIONS = ("概述", "介绍", "简介", "引言", "目录", "结论",
                     "Overview", "Introduction", "Conclusion")


def extract_citations(content: str) -> list:
    """路径统一解码 + 去首尾空白: %20 与裸空格两种写法解析成同一个真实
    路径(校验/钳位/快照跟踪全链路一致; 发射侧 _md_href 重新编码)。"""
    out = []
    for m in config.CITE_RE.finditer(content):
        out.append({"label": m.group("label"),
                    "path": unquote(m.group("path").strip()),
                    "a": int(m.group("a")) if m.group("a") else None,
                    "b": int(m.group("b")) if m.group("b") else None})
    return out


def _md_href(p: str) -> str:
    """markdown 链接目标里空格编码为 %20(与 CITE_RE/extract_citations 的
    解码互逆 —— 发射的链接既被门禁认得, 也不被渲染器截断)。"""
    return str(p).replace(" ", "%20")


AUTO_FIXABLE_PREFIXES = ("citation/off-by-one",)
"""机械可修复错误类(D23 钳位落在发布层) —— "哪些前缀不算硬错"的策略
唯一来源(c071: 原先每个消费方各写一份 startswith, 漂移即口径分叉)。"""

LENIENT_DROP_PREFIXES = ("schema/h1", "citation/none")
"""--lenient 在 submit 放宽的错误类(H1 契约与"至少一条引用"); 引用真实性
(path/range)与卡片 frontmatter 永不放宽。前缀集合在此单一来源 ——
改错误码措辞曾让散落各处的 startswith 过滤静默失配。"""


def is_auto_fixable(err: str) -> bool:
    return bool(err) and err.startswith(AUTO_FIXABLE_PREFIXES)


def is_lenient_droppable(err: str) -> bool:
    return bool(err) and err.startswith(LENIENT_DROP_PREFIXES)


def validate_citations(content: str, repo: str) -> tuple:
    """(errors, cited_files): 路径存在 + 行范围在文件内 + 不得逃出 repo(c049)。"""
    return _validate_cited(extract_citations(content), repo)


def _validate_cited(cites: list, repo: str) -> tuple:
    """validate_citations 的核(引用列表直入)—— c047: check_item 已先抽好
    引用列表(要数 n_cites), 不再让核在同一个字符串上重抽一遍全文。"""
    errors, cited = [], set()
    for c in cites:
        p = c["path"]
        target = join_confined(repo, p)
        if target is None:
            # 越界一律同一句话 —— 不区分仓外文件是否存在(防存在性/行数 oracle)
            errors.append(f"citation/path: 引用路径越界(仅限仓内相对路径): {p}")
            continue
        # 只有真实存在的文件才进 cited 集合: 该集合被 rebuild_cite_block
        # 用来再生成导出 <cite> 块 —— 越界/不存在路径曾先入集再报错,
        # kept 页从不重过门禁, 引用死路径的链接被重新合成进发布树
        if not os.path.isfile(target):
            errors.append(f"citation/path: 引用路径不存在: {p}")
            continue
        cited.add(p)
        if c["a"] is not None:
            n = cite_lines(target)
            a, b = c["a"], c["b"] or c["a"]
            if a < 1 or b < a:
                errors.append(f"citation/range: 非法行范围 {p}#L{a}-L{b}")
            elif b > n:
                if b == n + 1:
                    # D23 差一: end==行数+1 在 --fix 里钳位; 这里报可修复错误
                    errors.append(f"citation/off-by-one: {p}#L{a}-L{b} (文件 {n} 行)")
                else:
                    errors.append(f"citation/range: 行范围越界 {p}#L{a}-L{b} (文件 {n} 行)")
    return errors, cited


def parse_cite_block(content: str) -> set:
    """正文 <cite> 块的文件集合(deep 的 lining 校验输入)。

    P19: 在剥栅栏的文本上找块 —— 围栏示例里的 <cite> 不是 writer 的真块,
    曾被抓来与正文引用集合比对, deep 页在 LINING 被硬拒且无机械修复。"""
    m = re.search(r"<cite>(.*?)</cite>", strip_code_fences(content), re.S)
    if not m:
        return set()
    return {c["path"] for c in extract_citations(m.group(1))}


def rebuild_cite_block(content: str, repo: str, locale: str = "zh") -> str:
    """从正文引用文件再生 <cite> 头(lining 修复; 只在导出层, 永不落库 ——
    qwiki bug10: LINING 曾把注入物写回 DB, 下一轮 finish 自我拒绝)。
    引用集合取自剥栅栏的正文(示例代码里的 file:// 不是证据)。"""
    _, cited = validate_citations(strip_code_fences(content), repo)
    lines = ["<cite>", i18n.CITE_BLOCK_TITLE.get(locale,
                                                 i18n.CITE_BLOCK_TITLE["zh"])]
    for p in sorted(cited):
        lines.append(f"- [{p}](file://{_md_href(p)})")
    lines.append("</cite>")
    block = "\n".join(lines)
    # 栅栏感知(与校验/钳位同口径): 曾裸搜整个 content —— 正文以代码栅栏
    # 开头(内含 '# 注释' 或示例 <cite>)时, 注入位/替换落进栅栏里, 发布页
    # 带着被劈开的示例代码出厂, 且无门禁能查出来
    parts = split_fences(content)
    for i, seg in enumerate(parts):
        if not seg.startswith("```") \
                and re.search(r"<cite>.*?</cite>", seg, re.S):
            parts[i] = re.sub(r"<cite>.*?</cite>", lambda _: block, seg,
                              count=1, flags=re.S)
            return "".join(parts)
    for i, seg in enumerate(parts):
        if seg.startswith("```"):
            continue
        m = re.search(r"^# .*$", seg, re.M)
        if m:
            parts[i] = (seg[:m.end()] + "\n\n" + block + "\n\n"
                        + seg[m.end():].lstrip("\n"))
            return "".join(parts)
    return block + "\n\n" + content


def lint_mermaid(content: str) -> list:
    """Qoder fixMermaid 等价门: 明显语法破损检测。"""
    errors = []
    for i, block in enumerate(
            re.findall(r"```mermaid\s*\n(.*?)```", content, re.S), 1):
        first = next((l.strip() for l in block.split("\n") if l.strip()), "")
        if not first:
            errors.append(f"mermaid/empty: 图 {i} 为空")
        elif not config.MERMAID_FIRST.match(first):
            errors.append(f"mermaid/type: 图 {i} 首行不是合法图型: {first[:40]!r}")
        # Qoder 严规: 默认主题、带空格标签要引号、<br/> 换行、字母数字下划线
        # 节点 id。:::name 只在指令位(行尾/空格/]/| 之前)判样式 —— 协议哨兵
        # :::NEXT_STEP_BEGIN::: 是标签字面量, 不是样式。
        if re.search(r"(?m):::[A-Za-z][\w-]*(?=\s*$|\s|\]|\|)", block):
            errors.append(f"mermaid/style: 图 {i} 不允许自定义 class 样式")
        if re.search(r"(?m)^\s*classDef\s", block):
            errors.append(f"mermaid/style: 图 {i} 不允许 classDef")
    return errors


# 栅栏模式单一来源: split_fences(捕获组保留栅栏段)与 iter_h2(偏移枚举)
# 共用 —— iter_h2 曾自持一份字面正则, 栅栏规则演进(~~~ 等)没有跟进渠道,
# 栅栏内标题会被当真实节(P61 描述的幻影节 bug 重现)
_FENCE_RE = re.compile(r"(```.*?```)", re.S)


def split_fences(content: str) -> list:
    """按代码栅栏切分: 以 ``` 开头的段是栅栏, 其余是正文(P61 共用实现
    —— 栅栏正则曾散在六个转换点各持一份, 规则要变只改这里)。"""
    return _FENCE_RE.split(content)


def strip_code_fences(content: str) -> str:
    """剥掉代码栅栏, 防止 # 注释行被当标题(qwiki bug12: ping 实跑)。"""
    return "".join(p for p in split_fences(content)
                   if not p.startswith("```"))


def iter_h2(content: str):
    """产出不在代码栅栏内的 ## 标题(match 对象, 按出现顺序)。

    章节来源 gate / --fix / 目录注入的共用枚举器: 栅栏里的 '## ' 是
    示例内容不是分析节 —— 三处调用点曾各自拿原始 content 枚举, 幻影节
    改变计数, --fix 还把"章节来源"块注进代码栅栏里。"""
    spans = [(m.start(1), m.end(1)) for m in _FENCE_RE.finditer(content)]
    for m in re.finditer(r"(?m)^##\s+(.+)$", content):
        if not any(s <= m.start() < e for s, e in spans):
            yield m


def section_spans(content: str) -> list:
    """[(title, body_text), ...] 栅栏感知; body 也剥栅栏(栅栏内的
    '正确示例'不得满足章节来源要求)。"""
    out = []
    marks = list(iter_h2(content))
    for idx, m in enumerate(marks):
        end = marks[idx + 1].start() if idx + 1 < len(marks) else len(content)
        body = strip_code_fences(content[m.end():end])
        out.append((m.group(1).strip(), body))
    return out


def parse_frontmatter(content: str) -> tuple:
    if not content.startswith("---"):
        return {}, content
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?", content, re.S)
    if not m:
        return {}, content
    try:
        data = yaml_load(m.group(1))
    except ValueError:
        return {}, content
    return (data if isinstance(data, dict) else {}), content[m.end():]


def rowget(obj, key, default=""):
    if isinstance(obj, dict):
        return obj.get(key, default)
    try:
        val = obj[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if val is None else val


# --------------------------------------------------------------------------- #
# 条目门禁(submit=警告口径 / lining=按风格错误级)
# --------------------------------------------------------------------------- #

SECTION_SOURCES_RE = re.compile(r"\*\*(?:章节来源|Section sources)\*\*")
DIAGRAM_SOURCES_RE = re.compile(r"\*\*(?:图表来源|Diagram sources)\*\*")


def blocking_errors(errs: list, lenient: bool = False) -> list:
    """门禁错误的过滤策略单一属主(r6 #41): 先剔机械可修项(compose 落盘
    时钳位, submit 与 finish 都不该为此拒稿), lenient 再剔"只放宽 H1/
    无引用"两类 —— 曾四个消费点各自内联组合, 正确性挂在"阶段路由 +
    旗标面"两处远端事实上。"""
    errs = [e for e in errs if not is_auto_fixable(e)]
    if lenient:
        errs = [e for e in errs if not is_lenient_droppable(e)]
    return errs


def gate_item(content: str, item, repo: str, style: str,
              phase: str = "submit", mod_titles=None) -> tuple:
    """一个条目的完整门禁。phase: 'submit'(全警告) | 'lining'(按风格)。

    返回 (errors, warnings)。submit 阶段 errors 只含真实性硬错(引用路径/
    H1/卡片 frontmatter), 形状与尺寸全警告 —— 拒绝也保 checkpoint。
    """

    errors, warnings = [], []
    strict = (phase == "lining")
    p = preset(style)

    # 严重度选择器局部提升(r6 #51): 同一表达式曾 11+7 处内联,
    # 新增检查抄错一个变体会静默改变 concise LINING 行为且无测试把关
    sev = errors if strict else warnings
    sev_hard = errors if (strict and p["size_hard"]) else warnings
    kind = str(rowget(item, "kind") or "page")
    title = str(rowget(item, "title") or "")
    prose = strip_code_fences(content)

    # Row/dict 归一: sqlite3.Row 的 article_type/source_files(JSON 串)翻成
    # plan dict 的键形 —— D15 分档与类型查表只用 meta, 两个入口同一口径
    # (曾因 fallback 漏 source_files/type, Row 任务全按 standard 分档误判)
    if isinstance(item, dict):
        meta = item
    else:
        # JSON 列解码统一 util.json_list(本文件三处内联手抄曾是
        # 第四/五份实现 —— 坏行形状的修复会漏掉副本)
        _srcs = json_list(rowget(item, "source_files"))
        _scope = json_list(rowget(item, "scope"))
        meta = {"kind": kind, "title": title,
                "type": rowget(item, "article_type") or rowget(item, "type"),
                "pack_key": rowget(item, "pack_key"), "source_files": _srcs,
                "scope": _scope}

    # --- H1 契约(page/module_sheet; 卡片无 H1, 身份在 frontmatter) ---
    if not config.is_card_kind(kind):
        h1 = re.findall(r"^# (.+)$", prose, re.M)
        if len(h1) != 1:
            errors.append(f"schema/h1: 应恰有一个 H1, 实得 {len(h1)}")
        elif h1[0].strip() != title.strip():
            errors.append(f"schema/h1: H1 {h1[0]!r} != 计划标题 {title!r}")

    # --- 引用真实性(硬错, 两风格一致)。栅栏感知: 示例代码里的 file:// 是
    # 示例不是证据 —— 曾既被当真硬拒(citation/path), 又能凑 D16 行级下限
    # c047: 引用列表只抽一次 —— validate_citations 的核直入, 同一 30-100KB
    # 页面此前要过 extract_citations 两遍(核内一遍 + 下方 n_cites 一遍)
    cites = extract_citations(prose)
    cite_errors, cited = _validate_cited(cites, repo)
    real_errors = [e for e in cite_errors if not is_auto_fixable(e)]
    offbyone = [e for e in cite_errors if is_auto_fixable(e)]
    errors.extend(real_errors)
    if offbyone:
        sev.extend(offbyone)

    n_cites = len(cites)
    n_files = len(cited)

    if config.is_card_kind(kind):
        _gate_card(content, item, p, strict, errors, warnings, repo)
        return errors, warnings
    if kind == "module_sheet":
        _gate_sheet(content, p, strict, errors, warnings, n_cites)
        return errors, warnings

    # --- 页面形状(qwiki 16 点, 风格差异: concise 禁 目录/cite/结论; deep 要 cite 块) ---
    if content.lstrip().startswith("---"):
        errors.append("schema/frontmatter: 页面不允许 frontmatter")
    if re.match(r"^```(?:markdown|md)?\s*\n", content.lstrip()):
        errors.append("schema/fence: 内容不得包在外层代码栅栏里")
    if re.search(r"^#{5,}\s", prose, re.M):
        sev.append(
            "schema/headings: 不允许 #### 以下的深层标题")
    if re.search(r"^#{2,4}\s+\d+(?:\.\d+)*[.、)]\s+", prose, re.M):
        sev.append(
            "schema/numbering: 不要手工编号小节标题")

    if not p.get("cite_block_required"):
        # 发布层注入 <cite>/目录; writer 不得自带(qwiki 契约; 判定走预设键,
        # 不再散落 style == "concise" 字面比较)。注入物类是风格错误 ——
        # validation.md: concise 在 LINING 也保持警告(曾一律升错误级)
        if i18n.TOC_HEADING_RE.search(prose):
            sev_hard.append("schema/toc: 不要写目录节 —— 发布层会注入")
        if "<cite>" in content:
            sev_hard.append("schema/cite: 不要写 <cite> 块 —— 发布层会注入")
        if re.search(r"^\s*##\s+(结论|结语|总结|Conclusion|Summary|Next Steps)\s*$",
                     prose, re.M):
            sev_hard.append("schema/wrapup: 不要写结论/收尾节")
    else:
        # deep: writer 自带 cite 块, 且集合必须与正文一致
        # (else 分支里 cite_block_required 恒真 —— 内层再测是死代码)
        if "<cite>" not in content:
            sev.append(
                "schema/cite: deep 页必须自带 <cite> 块")
        elif parse_cite_block(content) != cited:
            sev.append(
                "schema/cite: <cite> 块集合与正文引用集合不一致")

    # 节数下限: 有 required_sections 用之; 否则按 article_type 用途清单
    h2 = re.findall(r"^## (.+)$", prose, re.M)
    required = json_list(rowget(item, "required_sections"))
    if required:
        if len(h2) < len(required):
            # r7 #43: submit(strict=False)也报缺口(警告级)—— 曾整条消息
            # 被关进 strict 分支, writer 只在 finish 硬拒时才知道缺口,
            # 双严重度模型(submit 警告省一轮重写)在此规则上失效
            (errors if (strict and p.get("section_sources") == "error")
             else warnings).append(
                f"schema/sections: 计划要求 {len(required)} 节, 实得 {len(h2)}")
    else:
        job_type = str(rowget(item, "article_type") or
                       rowget(item, "type") or "")
        need = config.ARTICLE_TYPE_SECTIONS.get(job_type)
        if need and len(h2) < need:
            # 预设键门控(与上方 required_sections 分支同款): concise 在
            # LINING 也保持警告(validation.md 契约)—— 曾写死 strict 升错,
            # 合法 concise run(type 页节数不足)整批死在 finish
            (errors if (strict and p.get("section_sources") == "error")
             else warnings).append(
                f"schema/sections: 类型 {job_type!r} 需 ≥{need} 节, 实得 {len(h2)}")

    # --- mermaid(形状类: submit 全警告; LINING 按 size_hard 升错误) ---
    mermaid_errors = lint_mermaid(content)
    (sev_hard).extend(mermaid_errors)
    diagrams = len(re.findall(r"```mermaid", content))
    lo, hi = p["mermaid"]
    if diagrams < lo or diagrams > hi:
        # r7 #3: 曾 `sev = sev_hard` 重绑定覆盖局部选择器, 其后全部
        # sev.append(citation/extra、sources/*、module_mention)跟着
        # 路由进错误列表 —— 硬错误严重级随无关的图表数翻转
        sev_hard.append(f"size/mermaid: {diagrams} 图 (目标 {lo}-{hi})")

    # --- 尺寸(按风格) ---
    n_lines = len(content.splitlines())
    if p["page_lines"]:
        lo_l, hi_l = p["page_lines"]
        if strict and p["size_hard"] and not (lo_l <= n_lines <= hi_l):
            errors.append(f"size/page: {n_lines} 行 (目标 {lo_l}-{hi_l})")
        elif not (lo_l <= n_lines <= hi_l):
            warnings.append(f"size/page: {n_lines} 行 (目标 {lo_l}-{hi_l})")
    # 词上限: 预设值(3300)本身即 max_article_words 的 1.1 倍产物 —— 不再
    # 二次 ×1.1(曾实际执行到 3630 的死区); 与其他尺寸同走 size_hard
    # (concise 软执行=警告, 曾把 concise 页 LINING 硬拒)
    words = len(content.split())
    wlo, whi = p["page_words"]
    if whi and words > whi:
        (sev_hard).append(
            f"size/words: {words} 词 > 词上限 {whi}")

    # 引用数: 分档(deep)或总量(concise)
    band = cite_band_for(meta, p)
    if band:
        blo, bhi = band
        if strict and p["size_hard"] and not (blo <= n_cites <= bhi):
            errors.append(f"size/citations: {n_cites} 条引用 (本类分档 {blo}-{bhi})")
        elif not (blo <= n_cites <= bhi):
            warnings.append(f"size/citations: {n_cites} 条引用 (本类分档 {blo}-{bhi})")
    else:
        lo_c, hi_c = p["cites"]
        if n_cites > hi_c:
            warnings.append(f"size/citations: {n_cites} 条引用 (上限 {hi_c})")
    if n_cites < 1:
        errors.append("citation/none: 页面至少 1 条 file:// 引用")

    flo, fhi = p["cited_files"]
    if flo and n_files < flo:
        # P20: 下界双向反馈 —— 曾只在 strict 分支报, 低于下限的页提交时
        # 零提示、LINING 才硬拒, 白白烧掉一整轮重写(其余尺寸维度提交时
        # 都双向告警, 这是唯一不对称的维度)
        (sev_hard).append(
            f"size/cited-files: 引用 {n_files} 个文件 (目标 ≥{flo})")
    elif fhi and n_files > fhi:
        # 上界与 page/words/citations 同走 strict+size_hard 升级 —— 曾恒为
        # 警告, validation.md 却把 size/cited-files 列为 deep 的风格错误,
        # 按文档预测 finish 结果对超界情形失真
        (sev_hard).append(
            f"size/cited-files: 引用 {n_files} 个文件 (目标 ≤{fhi})")

    # --- 证据边界(repowiki 保证回补) ---
    # citation/extra: >10 个引用文件落在声明 source_files/scope 之外 ——
    # 计划的 scope 指派被架空(glob scope 无法枚举判定, 视为覆盖不罚)
    declared = {str(s).rstrip("/") for s in (meta.get("source_files") or [])}
    scope_dirs = [str(s).rstrip("/") for s in (meta.get("scope") or [])
                  if "*" not in str(s)]
    extras = {c for c in cited
              if c not in declared
              and not any(c == d or c.startswith(d + "/") for d in scope_dirs)}
    if len(extras) > p.get("extra_cite_files", 10):
        sev.append(
            f"citation/extra: {len(extras)} 个引用文件超出声明"
            " source_files/scope(例: " + ", ".join(sorted(extras)[:3]) + ")")
    # evidence/code: 代码引用 <2 —— 文档不能佐证代码行为(repowiki 警告口径)
    if cited:
        code_cited = {c for c in cited
                      if os.path.splitext(c)[1].lower() not in config.DOC_EXTS}
        _cmin = p.get("code_evidence_min", 2)
        if len(code_cited) < _cmin:
            warnings.append(f"evidence/code: 代码文件引用 {len(code_cited)} "
                            f"<{_cmin} — 纯文档证据不能佐证代码行为")
    # cite_line_ratio: 行级引用占比(qwiki 0.85 曾是 LINING 硬错; 这里降为
    # 警告保留观测, 无行号的 file:// 不再与已验证引用不可区分)
    ratio_min = p.get("cite_line_ratio")
    if ratio_min and n_cites:
        n_line = sum(1 for c in cites if c["a"] is not None)
        if n_line / n_cites < ratio_min:
            warnings.append(f"size/cite-lines: {n_cites} 条引用仅 {n_line} 条"
                            f"带行级范围(<{int(ratio_min * 100)}%)")

    # --- 章节来源 / 图表来源(栅栏感知: 示例里的 '## ' 不是节, 栅栏内的
    # "**章节来源**" 不满足要求) ---
    n_sections = 0
    missing_ss = 0
    for head, body in section_spans(content):
        n_sections += 1
        if any(k in head for k in OVERVIEW_SECTIONS):
            continue
        if not SECTION_SOURCES_RE.search(body):
            missing_ss += 1
    if missing_ss and missing_ss > max(
            1, n_sections // p.get("sections_tolerance_div", 3)):
        msg = f"{missing_ss} 个分析节缺 **章节来源** 块"
        if p["section_sources"] == "error":
            sev.append("sources/section: " + msg)
        else:
            warnings.append("sources/section: " + msg)
    # diagrams==0 时 findall 数恒 ≥0, 无需单列分支(曾 if…pass/elif 死形状)
    # r7 #5: 来源字面量按剥栅栏正文计数 —— 栅栏示例里的 '**图表来源**'
    # 曾满足 deep 错误级 diagram 门禁(mermaid 数量本身按栅栏标记数, 不变)
    _prose_diagram = len(DIAGRAM_SOURCES_RE.findall(
        strip_code_fences(content)))
    if diagrams and _prose_diagram < diagrams:
        msg = f"{diagrams} 图中部分缺 **图表来源**"
        if p["diagram_sources"] == "error":
            sev.append("sources/diagram: " + msg)
        else:
            warnings.append("sources/diagram: " + msg)

    # --- 指派模块须在正文出现 ---
    # 严重度走预设键(r5 P32): deep 的模块锚定架构页升错误(strict 时),
    # concise 的种子页常挂根模块(标题即仓库名, 全文不必复述)保持警告
    for name in (mod_titles or []):
        if name and name not in content:
            msg = f"shape/module: 指派模块 {name!r} 未在正文出现"
            if p.get("module_mention") == "error":
                sev.append(msg)
            else:
                warnings.append(msg)
    return errors, warnings


def _gate_card(content, item, p, strict, errors, warnings, repo=""):
    """卡片: frontmatter kind/name 机器校验 + 证据存在性 + D16 引用。

    kind 解析兼容两形态: job/page 行是 kind='card'+card_kind=<种类>;
    plan 卡片是 kind=<种类>、无 card_kind。"""
    title = str(rowget(item, "title") or "")
    kind = str(rowget(item, "card_kind") or "")
    if not kind:
        ik = str(rowget(item, "kind") or "")
        kind = ik if ik in config.CARD_KINDS else ""
    data, body = parse_frontmatter(content)
    if not data:
        errors.append("knowledge/frontmatter: 卡片必须带 YAML frontmatter")
        return
    if str(data.get("kind") or "") != kind:
        errors.append(f"knowledge/frontmatter: kind {data.get('kind')!r} != 计划 {kind!r}")
    if str(data.get("name") or "").strip() != title.strip():
        errors.append(f"knowledge/frontmatter: name {data.get('name')!r} != 卡片标题 {title!r}")
    # repowiki 保证回补: frontmatter 声明的 source_files 必须真实存在
    # (声明已删文件的"证据"不再能随卡发布)
    if repo and isinstance(data.get("source_files"), list):
        for s in data["source_files"]:
            sp = str(s).rstrip("/")
            if not sp:
                continue
            # c049: 越界与不存在同一句话, 不给仓外存在性 oracle
            full = join_confined(repo, sp)
            if full is None or not os.path.exists(full):
                errors.append(f"knowledge/frontmatter: source_files 越界或不存在: {s}")
    h1 = re.findall(r"^# (.+)$", strip_code_fences(body), re.M)
    if h1:
        errors.append("knowledge/h1: 卡片不带 H1(frontmatter 即身份)")
    # D16 是**行级**引用下限(validation.md: 卡 ≥3 行级) —— 曾把无行号的
    # 整文件链接也计入, 零行级证据的卡照样过门; 栅栏内的示例引用不算证据
    need = p["knowledge_citations"]["card"]
    n_line = sum(1 for c in extract_citations(strip_code_fences(content))
                 if c["a"] is not None)
    if n_line < need:
        msg = f"D16: 卡片需 ≥{need} 条行级引用, 实得 {n_line}"
        if need and strict:
            errors.append("knowledge/citations: " + msg)
        elif need:
            warnings.append("knowledge/citations: " + msg)


def _gate_sheet(content, p, strict, errors, warnings, n_cites):
    """模块速查表: concise 短式零链接; deep 需行级引用(D16)。"""
    # 严重度选择器局部提升(r6 #51): 同一表达式曾 11+7 处内联,
    # 新增检查抄错一个变体会静默改变 concise LINING 行为且无测试把关
    sev = errors if strict else warnings
    sev_hard = errors if (strict and p["size_hard"]) else warnings


    concise_ = p["sheets"] == "short"
    if concise_:
        # 形状类: submit 全警告(validation.md 双严重度模型), LINING 按
        # size_hard —— 曾在 submit 就把 concise 速查表硬拒(r7 #3: 别再
        # 重绑定局部选择器, 直接用 sev_hard)
        if "file://" in content:
            sev_hard.append("sheet/links: concise 速查表不写 file:// 链接(正文页承载)")
        if re.search(r"^##\s+", strip_code_fences(content), re.M):
            sev_hard.append("sheet/shape: concise 速查表只有 H1, 不写 ## 小节")
        n_sent = len([s for s in content.splitlines() if s.strip()])
        if n_sent > p.get("sheet_max_lines", 40):
            warnings.append(f"sheet/length: {n_sent} 行 — concise 速查表应 1-3 句(每节)")
    else:
        # D16 行级口径(同卡片); 栅栏内的示例引用不算证据
        need = p["knowledge_citations"]["sheet"]
        n_line = sum(1 for c in extract_citations(strip_code_fences(content))
                     if c["a"] is not None)
        if n_line < need:
            msg = f"D16: deep 速查表需 ≥{need} 条行级引用, 实得 {n_line}"
            sev.append("knowledge/citations: " + msg)
        # r7 #4: 剥栅栏 —— 栅栏示例里的 '**章节来源**' 字面量曾满足
        # deep 错误级门禁(紧邻两行之上的 D16 检查自己都剥)
        if n_cites and not SECTION_SOURCES_RE.search(
                strip_code_fences(content)):
            (errors if (strict and p["section_sources"] == "error")
             else warnings).append("sources/section: 速查表缺 **章节来源**")


# --------------------------------------------------------------------------- #
# 机械修复(D18 / D23) —— 只从页面自身推导, 不新增事实
# --------------------------------------------------------------------------- #

def fix_section_sources(content: str, locale: str = "zh") -> tuple:
    """D18: 为缺 **章节来源** 的 ## 节追加该节现有行内引用的去重块。

    栅栏感知: 只枚举真实节(iter_h2), 证据取自剥栅栏的正文(示例里的引用
    不生成来源块), 注入落在该节正文末尾 —— 曾把来源块注进代码栅栏。"""
    header = i18n.SECTION_SOURCES.get(locale, i18n.SECTION_SOURCES["zh"])
    marks = list(iter_h2(content))
    if not marks:
        return content, 0
    out, last, fixed = [], 0, 0
    for idx, m in enumerate(marks):
        start, end = m.start(), (marks[idx + 1].start()
                                 if idx + 1 < len(marks) else len(content))
        out.append(content[last:start])
        seg = content[start:end]
        head = seg[:m.end() - start]
        body = seg[m.end() - start:]
        body_clean = strip_code_fences(body)
        title = m.group(1)
        if not SECTION_SOURCES_RE.search(body_clean) and \
                not any(k in title for k in OVERVIEW_SECTIONS):
            seen, lines = set(), []
            for c in extract_citations(body_clean):
                if c["path"] in seen:
                    continue
                seen.add(c["path"])
                a, b = c["a"], (c["b"] or c["a"])
                if a is not None:
                    lines.append(f"- [{c['path']}:{a}-{b}]"
                                 f"(file://{_md_href(c['path'])}#L{a}-L{b})")
                else:
                    lines.append(f"- [{c['path']}](file://{_md_href(c['path'])})")
            if lines:
                block = "\n" + header + "\n" + "\n".join(lines) + "\n"
                body = re.sub(r"\s*$", "\n", body) + block
                fixed += 1
        out.append(head + body)
        last = end
    out.append(content[last:])
    return "".join(out), fixed


def fix_off_by_one(content: str, repo: str) -> tuple:
    """D23: end==行数+1 的引用钳位到真实末行(URL 与显示文本都改)。
    单行形式 [x](file://a.py#L5) 在 4 行文件上同样钳位到 #L4;
    a==b==n+1 的整范围差一钳到 n-n。纯文件链接(无 #L)无行号可钳,
    原样跳过。"""
    fixed = 0

    def _load_lines(p):
        # c049: 越界路径不读(读 = 行数 oracle), 直接视为不可修
        target = join_confined(repo, p)
        return cite_lines(target) if target and os.path.isfile(target) else None

    def repl(m):
        nonlocal fixed
        path, a, b = m.group("path"), m.group("a"), m.group("b")
        if a is None:
            # 纯文件链接(CITE_RE 的 #L 组可选; rebuild_cite_block/D18 收割
            # 都会产出)无行号 —— 曾 int(None) 把每条导出路径打成 TypeError
            return m.group(0)
        n = _load_lines(unquote(path).strip())   # %20/裸空格同归真实路径
        if n is None:
            return m.group(0)
        if n < 1:
            # P14: 空(0 行)文件无行可钳 —— 曾钳成 '#L0-L0' 非法范围, 发布层
            # 自己发出的不可自动修复硬错误(分类器只看 b==n+1, 0+1==1 恰好
            # 命中)。放行原文, 让 citation/range 硬错误如实上报
            return m.group(0)
        if not b:
            # 单行差一: 起始行==行数+1 → 钳到末行(曾"报告可修却永不修")
            a_i = int(a)
            if a_i == n + 1:
                fixed += 1
                return f"[{path}:{n}](file://{path}#L{n})"
            return m.group(0)
        a_i, b_i = int(a), int(b)
        if b_i == n + 1 and a_i <= n + 1:
            # 分类器只看 b(b==n+1 即 off-by-one): a==b==n+1 曾被 a<=n
            # 守卫跳过 —— 报告"可自动修"却永不钳, 坏行号引用静默发布
            fixed += 1
            a2 = min(a_i, n)
            return (f"[{path}:{a2}-{n}](file://{path}"
                    f"#L{a2}-L{n})")
        return m.group(0)

    # 栅栏感知: 示例代码里的引用不是证据, 钳位不碰(与校验口径一致)
    parts = split_fences(content)
    out = "".join(p if p.startswith("```") else config.CITE_RE.sub(repl, p)
                  for p in parts)
    return out, fixed


def strip_injected_layers(content: str) -> str:
    """剥掉导出层注入物(<cite> 块 + 目录节, 双语)。

    validate --content 复检 concise 发布树时先还原 writer 原稿口径;
    file_to_db 吸收手改文件时同样走这里(注入物永不落库)。

    P6: 栅栏感知 —— 围栏里的示例 <cite>/“## 目录”不是注入物, 曾被无
    差别剥掉: file_to_db/validate --fix 把带围栏示例的页面永久写坏,
    TOC 正则还从栅栏内标题一路吞到收尾反引号(产出未闭合代码块)。
    注入侧(rebuild_cite_block/目录注入)本就栅栏感知, 两侧自此同口径。"""
    parts = split_fences(content)
    return "".join(
        i18n.TOC_SECTION_RE.sub(
            "", re.sub(r"(?s)<cite>.*?</cite>\n*", "", p))
        if not p.startswith("```") else p
        for p in parts)


# --------------------------------------------------------------------------- #
# 发布树验证器(validate --content; repowiki 模式)
# --------------------------------------------------------------------------- #

def validate_content(ctx: Ctx, plan: dict, fix: bool = False) -> tuple:
    """已发布/计划页面树的内容验证。返回 (report, exit_code)。"""
    from .planner import (card_path_for, mod_titles_for, modules_by_id,
                          page_path_for, plan_cards, plan_modules,
                          plan_pages)
    # P32: 模块提及检查与 submit/finish 同源(曾 validate 从不传
    # mod_titles, strict 升级层死代码) —— 计划模块表一次取齐
    all_mods = plan_modules(plan)
    errors_total, warnings_total = [], []
    pages = plan_pages(plan)
    cards = plan_cards(plan)
    mods_by_id = modules_by_id(plan)
    # 每行按自己的盖章复检(行戳 > plan > last_run > ctx, 与 publish 逐行
    # 合成 / export 双向同口径)—— 曾用单一 plan 级 style 复检全树: 混合
    # 盖章树上剥除判定与注入侧分叉, 引擎自己刚发布的树被 validate 打
    # exit 2(空盖章行在 db_to_file/file_to_db 两方向也曾解析出不同风格)
    _stamps: dict = {}
    _run_style = ""
    if getattr(ctx, "db", None):
        # ctx 未 load(裸构造/只读探测)时无行戳可用, 整树走 base 兜底
        _pid = ctx.partition()["id"]
        _stamps = {str(r["page_id"]): str(r["style"] or "")
                   for r in ctx.db.published_pages(_pid)}
        _run_style = str((ctx.db.last_run(_pid) or {"style": ""})["style"] or "")
    _base_style = str(plan.get("style") or _run_style or ctx.style)

    def _row_style(item) -> str:
        return _stamps.get(str(item.get("id")), "") or _base_style

    style = _base_style   # 兼容下方内链 pass 等树级兜底
    report_pages = []
    contents: dict = {}      # path → 文件内容(one 与内链 pass 复用, 不重读)
    # 路径当场派生而不是读 plan 的 path 键: path 只在计划写入时由
    # assign_paths 刷新, 手改标题后从不回写 —— begin/finish/publish 都按
    # 派生值发布并清旧文件, 验证器读未维护的旧键会把每个改名页误报
    # "未落盘"(反向: path 键从未赋值的 plan 则 0 页受检还 exit 0)
    page_paths = {str(pg.get("id")):
                  posix(page_path_for(pages, pg, ctx.locale, mods_by_id))
                  for pg in pages}
    card_paths = {str(c.get("id")): posix(card_path_for(c, ctx.locale))
                  for c in cards}
    # 内链目标集提前算好(one 的 --fix 重合成与末尾内链 pass 共用)
    all_paths = set(page_paths.values()) | set(card_paths.values())

    # P32: 模块提及检查与 submit/finish 同源(曾 validate 从不传
    # mod_titles, strict 升级层死代码) —— 计划模块表一次取齐
    def one(item, path, kind_hint=""):
        nonlocal fix
        full = os.path.join(ctx.root, path)
        if not os.path.isfile(full):
            # 计划页未落盘是 coverage 错误 —— 曾在此 early-return: 构造好的
            # 错误从不进 report_pages/errors_total, 验证器对缺页树绿灯放行
            # (exit 0, "0 错误")
            entry = {"path": path, "errors": ["coverage/page: 计划页未落盘"],
                     "warnings": []}
            report_pages.append(entry)
            errors_total.extend(f"{path}: {e}" for e in entry["errors"])
            return entry
        try:
            with open(full, encoding="utf-8") as fh:
                content = fh.read()
        except UnicodeDecodeError as e:
            # c048 同族: 手编发布文件的坏字节报"哪一页", 不是裸 traceback
            from .util import die
            die(f"发布文件不是合法 UTF-8({full}: {e})", config.EXIT_GATE)
        contents[path] = content
        # 页面发布文件含导出层注入的 <cite>/目录 —— 先剥掉再按 writer
        # 原稿口径复检(曾把引擎自己的产出判成 toc/cite 错误, 验证器不可用)。
        # 剥除判定与注入侧共用一份(publish.export_injects, plan 级
        # export_style 同判): 曾只看预设键 —— concise + export_style:
        # current 下注入层从不注入, 作者手写的 cite/目录被当注入物剥掉;
        # 卡/sheet 从不注入, 也不剥
        from .publish import export_injects
        item2 = dict(item)   # P63: sqlite3.Row 也支持 dict(), 残留三元已无差异
        item2.setdefault("kind", kind_hint or "page")
        item_style = _row_style(item2)
        if item2.get("kind") == "page" and export_injects(ctx, item_style, plan):
            content = strip_injected_layers(content)
        errs, warns = gate_item(content, item2, ctx.repo, item_style,
                                phase="lining",
                                mod_titles=mod_titles_for(item, all_mods))
        # r7 #14: 过滤策略与 write/file_to_db 同一属主(blocking_errors)
        # —— 差一钳位是机械可修项, submit/finish/write/update 全部剔除后
        # 由 compose 落盘时钳位, validate 曾把它当硬错(exit 2)与 write
        # 对同一内容给出相反判定
        errs = blocking_errors(errs)
        if fix:
            # 顺序: 先 D23 钳位再 D18 收割 —— 曾先收割, 未钳位的行号被抄进
            # 新的 **章节来源** 块, 钳位守卫(a<=n)永远够不着, --fix 不收敛
            content2, n2 = fix_off_by_one(content, ctx.repo)
            content3, n1 = fix_section_sources(content2, ctx.locale)
            if n1 or n2:
                # 写回走导出合成层 —— 曾把剥掉注入物的原稿口径直接写回,
                # concise 发布树的 <cite>/编号目录被 --fix 永久删除
                from .publish import compose_export_content
                from .util import atomic_write
                row = dict(item2, content=content3, path=path)
                out_content, _ = compose_export_content(
                    ctx, row, item_style, all_paths, plan=plan)
                atomic_write(full, out_content)
                contents[path] = out_content
                warns.append(f"fix: 修复 {n1} 个章节来源块 / {n2} 处差一引用")
                # 复查保持原严重度(lining) —— 曾降回 submit 口径, 只要含
                # 一处可机械修复的引用, 其余风格错误全部跟着降成警告
                errs, warns2 = gate_item(content3, item2, ctx.repo, item_style,
                                         phase="lining",
                                         mod_titles=mod_titles_for(item, all_mods))
                errs = blocking_errors(errs)
                warns = warns + warns2
        entry = {"path": path, "errors": errs, "warnings": warns}
        report_pages.append(entry)
        errors_total.extend(f"{path}: {e}" for e in errs)
        warnings_total.extend(f"{path}: {w}" for w in warns)

    for pg in pages:
        one(pg, page_paths[str(pg.get("id"))])
    for c in cards:
        one(c, card_paths[str(c.get("id"))], kind_hint="card")

    # 内链检查(相对 .md 链接必须能落到已发布树)—— repowiki 口径是错误级:
    # 断链导航不得随"已验证"契约发布。目标集含卡片(引擎自己会把页链接
    # 重写成 knowledge/<locale>/ 卡片路径, 验证器不得反过来判它死链);
    # 带 #锚点 的相对链接同样校验路径部分(曾整类漏检)。与发布层对齐的
    # 两个口径: ①%XX 解码 —— normalize_links 落盘时把路径空格编码为
    # %20, 验证器不解码则引擎自家产出全数误报死链; ②栅栏内的示例链接
    # 是内容不是导航(改写侧同样跳过栅栏)。内容复用 one() 读过的。
    from urllib.parse import unquote
    # 跳过前缀与 normalize_links 同源(publish.LINK_SKIP_PREFIXES): 校验器
    # 自持的 lookahead 曾漏 #/mailto: —— 邮箱链接发布器保留、校验器硬错
    from .publish import LINK_SKIP_PREFIXES
    _lookahead = "(?!" + "|".join(LINK_SKIP_PREFIXES) + ")"
    _link_re = re.compile(
        rf"\[[^\]]*\]\({_lookahead}([^)#]+\.md(?:#[^)\s]*)?)\)")
    for pg in pages:
        path = page_paths[str(pg.get("id"))]
        content = contents.get(path)
        if content is None:
            continue
        base_dir = os.path.dirname(path)
        prose = "".join(x for x in split_fences(content)
                        if not x.startswith("```"))
        for m in _link_re.finditer(prose):
            rel = unquote(m.group(1).split("#", 1)[0])
            target = posix(os.path.normpath(os.path.join(base_dir, rel)))
            if target not in all_paths:
                errors_total.append(
                    f"links/internal: {path}: {m.group(1)} 不解析")

    needs_review = [e["path"] for e in report_pages if e["errors"]]
    report = {
        "summary": {"pages": len(pages), "knowledge_docs": len(cards),
                    "errors": len(errors_total), "warnings": len(warnings_total),
                    "needs_review": needs_review},
        "pages": report_pages,
        "global": {"errors": errors_total, "warnings": warnings_total},
    }
    out = os.path.join(ctx.root, "work", "validation-report.json")
    from .util import atomic_write   # P56: 中断不留半份报告
    atomic_write(out, json.dumps(report, ensure_ascii=False, indent=2))
    return report, (config.EXIT_GATE if errors_total else config.EXIT_OK)


def cmd_validate_content(ctx: Ctx, args) -> int:
    from .planner import load_plan
    plan = load_plan(ctx)
    report, code = validate_content(ctx, plan, fix=getattr(args, "fix", False))
    for e in report["global"]["errors"]:
        print(f"error: {e}")
    for w in report["global"]["warnings"]:
        print(f"warn: {w}")
    print(f"validate --content: {report['summary']['errors']} 错误, "
          f"{report['summary']['warnings']} 警告, needs_review "
          f"{len(report['summary']['needs_review'])} 页 — 报告在 "
          f"{os.path.join(ctx.root, 'work', 'validation-report.json')}")
    return code
