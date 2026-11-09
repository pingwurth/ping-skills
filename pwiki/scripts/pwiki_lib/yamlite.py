"""极小 YAML 子集(发射器 + 解析器) — 满足 wiki_plan.yaml 往返。

原样移植自 qwiki/scripts/qwiki.py:258-494(实战 18 bug 中的 1-3 号修复都在
这里: 转义处理 / 注释剥离 / 引号策略 —— 不要"简化"本模块)。

支持: 嵌套块映射、块列表、映射列表("- key: v")、行内 [a, b] 列表、
引号/纯标量、注释、"|" 块标量(不含内部空行)。发射器对解析器可能误读的
一切加引号, 因此本引擎自己写的文件可以精确往返。

已知限制(继承 qwiki): "|" 块内不能有空行(注释行可以, 原样保留);
折叠 ">" 是有损的(空格连接); 行内列表必须在一行内闭合。

人工编辑面(wiki_plan.yaml / frontmatter)的解析原则: 拿不准就报错,
绝不静默丢行/吞字段 —— 块标量里的 '#' 行是正文, 错缩进的续行、跨行的
行内列表都必须显式失败。
"""

from __future__ import annotations

import json
import re

_NUM_RE = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")


def _yaml_scalar(v) -> str:
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    if s == "" or re.search(r'[:#\[\]{},&*!|>\'"%@`]', s) or s != s.strip() \
            or s.startswith(("-", "?")) or "\n" in s or "\t" in s \
            or s.lower() in ("null", "true", "false", "yes", "no", "on", "off", "~") \
            or _NUM_RE.match(s) \
            or re.fullmatch(r"[+-]?(nan|inf|infinity)", s, re.IGNORECASE):
        # json.dumps 产出合法的 YAML 双引号标量(含 \n 转义); inf/nan 形的
        # 字符串也要引号 —— 否则裸写后被解析侧按 float 收走(P46 往返)
        return json.dumps(s, ensure_ascii=False)
    return s


def _yaml_flow(v: list) -> str:
    return "[" + ", ".join(_yaml_scalar(x) for x in v) + "]"


def _yaml_emit(obj, indent: int = 0) -> list:
    pad = " " * indent
    lines = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = _yaml_scalar(k)
            if isinstance(v, dict) and v:
                lines.append(f"{pad}{key}:")
                lines.extend(_yaml_emit(v, indent + 4))
            elif isinstance(v, list) and v:
                lines.append(f"{pad}{key}:")
                lines.extend(_yaml_emit(v, indent + 4))
            elif isinstance(v, list):
                lines.append(f"{pad}{key}: []")
            elif isinstance(v, dict):
                # 空 dict 曾落进标量分支发射成字符串 "{}"(重读类型翻转)
                lines.append(f"{pad}{key}: {{}}")
            else:
                lines.append(f"{pad}{key}: {_yaml_scalar(v)}")
    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, dict):
                first = True
                for k, v in item.items():
                    key = _yaml_scalar(k)
                    prefix = f"{pad}- " if first else f"{pad}  "
                    first = False
                    if isinstance(v, (dict, list)) and v:
                        lines.append(f"{prefix}{key}:")
                        lines.extend(_yaml_emit(v, indent + 4))
                    elif isinstance(v, list):
                        lines.append(f"{prefix}{key}: {_yaml_flow(v)}")
                    elif isinstance(v, dict):
                        lines.append(f"{prefix}{key}: {{}}")
                    else:
                        lines.append(f"{prefix}{key}: {_yaml_scalar(v)}")
            elif isinstance(item, list):
                lines.append(f"{pad}-")
                lines.extend(_yaml_emit(item, indent + 4))
            else:
                lines.append(f"{pad}- {_yaml_scalar(item)}")
    else:
        lines.append(f"{pad}{_yaml_scalar(obj)}")
    return lines


def yaml_dump(obj) -> str:
    return "\n".join(_yaml_emit(obj)) + "\n"


def _strip_comment(line: str) -> str:
    """剥离行尾注释。引号只在"值起点"开引 —— 纯标量里的撇号
    (don't / it's) 不是引号, 曾把行尾注释焊进值里。"""
    out = []
    quote = ""            # 当前引号字符('' = 无)
    value_start = True    # 行首视为值起点(裸标量 / "- " 列表项)
    i, n = 0, len(line)
    while i < n:
        ch = line[i]
        if not quote and value_start and ch in " \t":
            out.append(ch)              # 值前空白, 仍是值起点
        elif not quote and value_start and ch in "'\"":
            quote = ch
            value_start = False
            out.append(ch)
        elif quote and ch == quote and i + 1 < n and line[i + 1] == quote:
            out.append(ch)                  # '' / "" 转义的字面引号
            out.append(line[i + 1])
            i += 2
            continue
        elif quote and ch == quote:
            quote = ""
            out.append(ch)
        elif quote == '"' and ch == "\\" and i + 1 < n:
            out.append(ch)
            out.append(line[i + 1])
            i += 2
            continue
        elif not quote and ch == "#" and (i == 0 or line[i - 1] in " \t"):
            break                        # 注释(不在引号内, 前有空白或行首)
        else:
            out.append(ch)
        i += 1
        if not quote and ch == ":" and (i >= n or line[i] in " \t"):
            value_start = True           # "key: " → 值起点
        elif not quote and value_start and ch == "-" and i < n \
                and line[i] in " \t":
            value_start = True           # "- " 列表标记 → 值起点
        elif ch not in " \t":
            value_start = False
    return "".join(out).rstrip()


def _parse_scalar(tok: str):
    tok = tok.strip()
    if tok == "" or tok in ("~", "null", "Null", "NULL"):
        return None
    if tok == "{}":
        return {}   # 与发射侧 `key: {}` 互逆(曾解析回字符串 "{}")
    if tok in ("true", "True", "TRUE"):
        return True
    if tok in ("false", "False", "FALSE"):
        return False
    # YAML 1.1 布尔(发射侧 :38 一直按"歧义词"加引号, 手写裸词却解析成
    # 真值字符串 —— 'skeleton: no' 曾把"不是占位"写成恒真)
    if tok in ("yes", "Yes", "YES", "on", "On", "ON"):
        return True
    if tok in ("no", "No", "NO", "off", "Off", "OFF"):
        return False
    if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in "'\"":
        body = tok[1:-1]
        if tok[0] == '"':
            try:
                return json.loads(tok)
            except ValueError:
                return body
        return body.replace("''", "'")
    # 锚点/别名不支持(被当普通标量曾是静默灾难: '*default_scope' 成了
    # scope 字符串, 消费方解码失败回空列表 —— 条目被"什么都不跟踪")
    if tok[:1] in ("&", "*"):
        raise ValueError(f"yaml: 锚点/别名不支持: {tok!r}")
    # c003: PEP 515 下划线数字 —— 裸 int()/float() 认, 发射侧 _NUM_RE 不认
    # → 不加引号写回, 重读后类型从字符串变数值, 破坏"引擎写文件精确往返",
    # 保持字符串身份。nan/inf 相反: 发射侧把非有限 float 裸写成 inf/nan
    # (str(v)), 解析侧必须转回 float 往返才精确(P46; 字符串 'inf' 由发射
    # 侧加引号保住字符串身份)。
    if "_" not in tok:
        try:
            return int(tok)
        except ValueError:
            pass
        try:
            return float(tok)
        except ValueError:
            pass
    if tok.startswith("[") and tok.endswith("]"):
        inner = tok[1:-1].strip()
        if not inner:
            return []
        parts, cur, in_s, in_d, depth = [], "", False, False, 0
        for ch in inner:
            if ch == "'" and not in_d:
                in_s = not in_s
            elif ch == '"' and not in_s:
                in_d = not in_d
            elif not in_s and not in_d and ch in "[{":
                depth += 1
            elif not in_s and not in_d and ch in "]}":
                depth -= 1
            elif ch == "," and not in_s and not in_d and depth == 0:
                parts.append(cur)
                cur = ""
                continue
            cur += ch
        if depth != 0:
            # 嵌套未配平 = 手写错误: 曾静默撕成碎片标量('[[b, c], d]' →
            # ['[b', 'c]', 'd']), 碎片被下游当真路径/真 id 消费
            raise ValueError(f"yaml: 行内列表括号不配对: {tok!r}")
        parts.append(cur)
        return [_parse_scalar(p) for p in parts if p.strip() != ""]
    return tok


class _YLine:
    __slots__ = ("indent", "text", "raw")

    def __init__(self, raw: str):
        self.raw = raw
        stripped = _strip_comment(raw)
        self.text = stripped.strip()
        # 缩进取原始行的物理缩进: 注释整行(text="")曾被算成 indent 0,
        # 块标量体里缩进的 '#' 行进不了体
        self.indent = len(raw) - len(raw.lstrip(" "))
        if not raw.strip():
            self.indent = len(stripped) - len(stripped.lstrip(" "))


_KV_RE = re.compile(
    r'^("(?P<qk>[^"]*)"|\'(?P<sk>[^\']*)\'|'
    r'(?P<pk>[^:#][^:]*?(?::[^:\s]+)*))\s*:\s*(?P<val>.*)$')
# P40: 裸键允许"后跟非空白的冒号"段(a:b / go:generate) —— YAML 里它们是
# 键的一部分; 曾绑到第一个 ':' 之前的最短前缀, 'a:b: v' 被静默重塑成
# {'a': 'b: v'}(违"拿不准就报错, 绝不静默吞字段"); 发射侧对含 ':' 键加
# 引号, 往返精确。


def _is_seq_line(ln) -> bool:
    return ln.text.startswith("- ") or ln.text == "-"


def _parse_block(lines: list, i: int, min_indent: int):
    """Parse lines[i:] at >= min_indent. Returns (value, next_index).

    空行/注释行(text="")在块内被跳过(不给值也不终结块); 块标量体由
    raw 原样重组, '#' 行是正文不是注释。"""
    while i < len(lines) and lines[i].text == "":
        i += 1
    if i >= len(lines) or lines[i].indent < min_indent or \
            lines[i].text in ("", "---"):
        return None, i
    base = lines[i].indent
    if _is_seq_line(lines[i]):
        seq = []
        while i < len(lines):
            if lines[i].text == "":
                i += 1
                continue
            if lines[i].indent != base or not _is_seq_line(lines[i]):
                break
            rest = lines[i].text[2:].strip() if lines[i].text != "-" else ""
            if rest == "":
                val, i = _parse_block(lines, i + 1, base + 2)
                seq.append(val)
            elif _KV_RE.match(rest):
                # map item: re-parse as virtual lines with the "- " stripped。
                # 续行吸收连同**注释行**一起收(text=="" 由 _parse_block 跳过):
                # 曾在 text != "" 处停 —— 条目中段一条 '#' 注释让余下字段
                # 掉出块, 合法 YAML 报 unparsed line
                virt = [_YLine(" " * (base + 2) + rest)]
                j = i + 1
                while j < len(lines) and lines[j].indent > base:
                    virt.append(lines[j])
                    j += 1
                val, consumed = _parse_block(virt, 0, base + 2)
                # 曾丢弃 consumed —— 错缩进的续行被吸进 virt 后既不解析
                # 也不报错, 字段(如 parent:)静默蒸发
                if consumed < len(virt):
                    raise ValueError(
                        f"yaml: cannot parse line: {virt[consumed].raw!r}")
                seq.append(val)
                i = j
            else:
                if rest.startswith("[") and not rest.endswith("]"):
                    raise ValueError(
                        f"yaml: 行内列表未在一行内闭合(不支持跨行): "
                        f"{lines[i].raw!r}")
                seq.append(_parse_scalar(rest))
                i += 1
        return seq, i
    obj = {}
    while i < len(lines):
        if lines[i].text == "":
            i += 1
            continue
        if lines[i].indent != base or _is_seq_line(lines[i]):
            break
        if lines[i].text == "---":
            # 同缩进的 '---' 是文档边界: 结束本映射(残余行由 yaml_load 的
            # unparsed 检查显式报错, 多文档不支持)。块标量体内的 '---'
            # 缩进更深, 由块收集器原样收进体 —— 曾在 yaml_load 全局按
            # text 过滤, 手写块标量里的 '---' 正文行被静默丢弃
            break
        m = _KV_RE.match(lines[i].text)
        if not m:
            raise ValueError(f"yaml: cannot parse line: {lines[i].raw!r}")
        if m.group("qk") is not None:
            key_raw = '"' + m.group("qk") + '"'
        elif m.group("sk") is not None:
            key_raw = "'" + m.group("sk") + "'"
        else:
            key_raw = m.group("pk")
        val_raw = m.group("val")
        key = _parse_scalar(key_raw)
        try:
            key_seen = key in obj
        except TypeError:
            # c008: 流列表等病态键([a, b]: v)不可哈希 —— 曾"交给赋值自然
            # 报错", 但自然错误是 TypeError, load_plan 等调用方只捕 ValueError
            # → 唯一人工编辑面上裸 traceback; 统一抛可诊断的 ValueError
            raise ValueError(
                f"yaml: 键必须是标量(不可哈希的键 {key!r}: {lines[i].raw!r})")
        if key_seen:
            # 复制粘贴出的重复键曾是静默 last-wins —— 人工编辑面上
            # 必须显式失败(前一个值无声蒸发, prompt 对账基准还错位)
            raise ValueError(f"yaml: 重复键 {key!r}: {lines[i].raw!r}")
        if val_raw == "" or val_raw in ("|", "|-", "|+", ">", ">-", ">+"):
            if val_raw.startswith(("|", ">")):
                raws, j = [], i + 1
                # 块体 = 比 key 更深缩进的一切行(含注释行: '#' 在块标量
                # 里是正文)。text=="" 的行只可能是注释行(空行已在
                # yaml_load 过滤), 缩进比 key 浅的注释不属于体
                while j < len(lines) and (lines[j].indent > base):
                    raws.append(lines[j].raw)
                    j += 1
                while raws and not raws[-1].strip():
                    raws.pop()
                nonblank = [r for r in raws if r.strip()]
                # 去缩进锚定**内容行**(裸 '#' 行是块标量正文但不应锚定缩进):
                # 曾对全部非空行取 min —— 一条浅缩进注释让整个块的相对
                # 缩进静默重排。全注释体退回 nonblank 兜底
                anchor = [r for r in nonblank
                          if not r.lstrip().startswith("#")] or nonblank
                strip_n = min((len(r) - len(r.lstrip(" ")) for r in anchor),
                              default=0)
                buf = []
                for r in raws:
                    if not r.strip():
                        buf.append("")
                        continue
                    own = len(r) - len(r.lstrip(" "))
                    buf.append(r[min(strip_n, own):])
                text = "\n".join(buf).rstrip("\n")
                obj[key] = text if val_raw.startswith("|") else " ".join(text.split())
                i = j
                continue
            # 嵌套值: 更深缩进的块, 或同缩进的 "- " 列表
            # (PyYAML 默认块列表风格 'key:\n- a'; 曾直接 unparsed line,
            # 卡片 frontmatter 误报"必须带 YAML frontmatter")
            k = i + 1
            while k < len(lines) and lines[k].text == "":
                k += 1
            if k < len(lines) and lines[k].text != "" and (
                    lines[k].indent >= base + 1 or
                    (lines[k].indent == base and _is_seq_line(lines[k]))):
                nxt_min = base + 1 if lines[k].indent >= base + 1 else base
                obj[key], i = _parse_block(lines, k, nxt_min)
            else:
                obj[key] = None
                i = k
        else:
            if val_raw.startswith("[") and not val_raw.endswith("]"):
                # 跨行的行内列表曾静默解析成 '[' —— 明确报错而不是降级
                raise ValueError(
                    f"yaml: 行内列表未在一行内闭合(不支持跨行): "
                    f"{lines[i].raw!r}")
            obj[key] = _parse_scalar(val_raw)
            i += 1
    return obj, i


def yaml_load(text: str):
    lines = [_YLine(r) for r in text.replace("\r\n", "\n").split("\n")]
    # 只过滤纯空白行(块标量不支持空行是文档化限制); 注释整行保留 ——
    # 它们在块标量里是正文, 在映射/序列里当作可跳过的空行。
    # '---' 文档分隔符只跳过文档首/尾的顶层出现; 中途出现的交给
    # _parse_block 的边界 break + unparsed 检查显式报错(多文档不支持),
    # 块标量体内的 '---'(缩进比 key 深)由块收集器收进体 —— 曾按
    # text 全局过滤, 手写块标量里的 '---' 正文行被静默丢行(prompt
    # 等多行内容无声缺行, 后续保存还把缺行版持久化)
    lines = [ln for ln in lines if ln.raw.strip() != ""]
    for ln in lines:
        # Tab 缩进在 YAML 里非法: 曾按"缩进 0"解析, 嵌套键静默逃逸到顶层
        # (手改 plan 一次, 树形就重组)—— 显式报错, 与"拿不准就报错"契约
        # 一致(themes 的 pwiki.yaml Tab 守卫由此兜底)
        lead = ln.raw[:len(ln.raw) - len(ln.raw.lstrip(" \t"))]
        if "\t" in lead:
            raise ValueError(f"yaml: 缩进用了 Tab(YAML 不允许): {ln.raw!r}")
    while lines and lines[0].text == "---" and lines[0].indent == 0:
        lines.pop(0)
    while lines and lines[-1].text == "---" and lines[-1].indent == 0:
        lines.pop()
    if not lines:
        return {}
    val, consumed = _parse_block(lines, 0, 0)
    # pwiki 加严: wiki_plan.yaml 是人工编辑面, 残余未解析行必须报错而不是
    # 静默丢弃(qwiki 对自家产物无妨, 对手改文件是数据丢失)。
    while consumed < len(lines) and lines[consumed].text == "":
        consumed += 1
    if consumed < len(lines):
        raise ValueError(f"yaml: unparsed line: {lines[consumed].raw!r}")
    return val if isinstance(val, (dict, list)) else {}
