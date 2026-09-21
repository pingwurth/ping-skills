"""Java 源码静态分析。

集中旧 common.py 与 build_prompt.py 中分散的 Java 文本处理:
    - 注释/字符串/字符/文本块剥离(严格保长度, 下标与原串一一对应)
    - JVM 方法描述符人读化
    - 目标方法及其调用的本类 private 方法源码抽取(宁多勿漏)

所有函数为纯函数, 不做 I/O。抽取在净化文本上定位、下标回切原串, 保证字符串/
注释中的花括号不干扰配对, 同时保留真实源码。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from . import config
from . import maven

# private 方法声明(可含 static/final/synchronized/abstract 修饰)
_PRIVATE_METHOD_RE = re.compile(
    r"\bprivate\s+(?:static\s+|final\s+|synchronized\s+|abstract\s+)*"
    r"[\w$<>\[\],.\s?]+?\s+([A-Za-z_$][\w$]*)\s*\(")
# 任意方法调用点
_CALL_RE = re.compile(r"\b([A-Za-z_$][\w$]*)\s*\(")
# 顶层括号配对表
_BRACKET_PAIRS = {"<": ">", "(": ")", "[": "]"}


# --------------------------------------------------------------------------- #
# JVM 描述符
# --------------------------------------------------------------------------- #
def jvm_desc_to_readable(desc: str) -> str:
    """JVM 描述符参数部分转人读格式: (Ljava/util/List;[I)V -> (List, int[])。

    非描述符(不以 '(' 开头或结构不完整)原样返回。
    """
    if not desc or not desc.startswith("("):
        return desc
    end = desc.find(")")
    if end < 0:
        return desc
    params_str = desc[1:end]
    params: list[str] = []
    i = 0
    while i < len(params_str):
        array_dims = 0
        while i < len(params_str) and params_str[i] == "[":
            array_dims += 1
            i += 1
        if i >= len(params_str):
            return desc
        ch = params_str[i]
        if ch in config.JVM_PRIMITIVE_TYPES:
            ptype = config.JVM_PRIMITIVE_TYPES[ch]
            i += 1
        elif ch == "L":
            j = params_str.find(";", i)
            if j < 0:
                return desc
            ptype = params_str[i + 1:j].rsplit("/", 1)[-1].replace("$", ".")
            i = j + 1
        else:
            return desc
        params.append(ptype + "[]" * array_dims)
    return "(" + ", ".join(params) + ")" if i == len(params_str) else desc


# --------------------------------------------------------------------------- #
# 注释/字符串剥离
# --------------------------------------------------------------------------- #
def strip_comments_and_strings(content: str) -> str:
    """剥离 Java 注释/字符串/字符字面量/文本块, 严格保长度。

    被剥离字符以空格替换, 换行保留, 因此净化文本下标与原串一一对应,
    供规则校验(行号定位)与方法抽取(下标回切)共用。
    """
    out: list[str] = []
    i, n = 0, len(content)
    state = "code"
    while i < n:
        ch = content[i]
        nxt = content[i + 1] if i + 1 < n else ""
        if state == "code":
            if ch == "/" and nxt == "/":
                state, i = "line_comment", i + 2
                out.append("  ")
                continue
            if ch == "/" and nxt == "*":
                state, i = "block_comment", i + 2
                out.append("  ")
                continue
            if ch == '"' and content[i:i + 3] == '"""':
                state, i = "text_block", i + 3
                out.append("   ")
                continue
            if ch in "\"'":
                state = "string" if ch == '"' else "char"
                out.append(" ")
                i += 1
                continue
            out.append(ch)
            i += 1
        elif state == "line_comment":
            if ch == "\n":
                state = "code"
                out.append(ch)
            else:
                out.append(" ")
            i += 1
        elif state == "block_comment":
            if ch == "*" and nxt == "/":
                state, i = "code", i + 2
                out.append("  ")
                continue
            out.append(ch if ch == "\n" else " ")
            i += 1
        elif state == "text_block":
            if ch == "\\":
                out.append("  ")
                i += 2
                continue
            if content[i:i + 3] == '"""':
                state, i = "code", i + 3
                out.append("   ")
                continue
            out.append(ch if ch == "\n" else " ")
            i += 1
        else:  # string / char
            if ch == "\\":
                out.append("  ")
                i += 2
                continue
            if ch == ('"' if state == "string" else "'"):
                state = "code"
                out.append(" ")
            elif ch == "\n":
                state = "code"
                out.append(ch)
            else:
                out.append(" ")
            i += 1
    return "".join(out)


# --------------------------------------------------------------------------- #
# 参数列表 / 大括号配对
# --------------------------------------------------------------------------- #
def split_top_level(text: str) -> list[str]:
    """按顶层逗号拆分参数列表(忽略 <>、()、[] 内的逗号)。"""
    parts: list[str] = []
    buf: list[str] = []
    stack: list[str] = []
    for ch in text:
        if stack and ch == _BRACKET_PAIRS.get(stack[-1], "\x00"):
            stack.pop()
        elif ch in _BRACKET_PAIRS:
            stack.append(ch)
        if ch == "," and not stack:
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    if "".join(buf).strip():
        parts.append("".join(buf).strip())
    return [p for p in parts if p]


def match_braces(code: str, brace_pos: int) -> tuple[str | None, int]:
    """从 '{' 起做大括号配对, 返回 (方法体文本, 结束下标); 不配对返回 (None, -1)。"""
    depth = 0
    for i in range(brace_pos, len(code)):
        if code[i] == "{":
            depth += 1
        elif code[i] == "}":
            depth -= 1
            if depth == 0:
                return code[brace_pos + 1:i], i
    return None, -1


def arity_from_desc(desc: str) -> int:
    """人读化 desc 的参数个数: '' -> 0; '(List, int[])' -> 2。"""
    if not desc:
        return 0
    inner = desc.strip()
    if inner.startswith("(") and inner.endswith(")"):
        inner = inner[1:-1]
    if not inner.strip():
        return 0
    return len(split_top_level(inner))


# --------------------------------------------------------------------------- #
# 方法抽取
# --------------------------------------------------------------------------- #
def extract_method_blocks(source: str, method_name: str,
                          arity: int | None = None,
                          stripped: str | None = None) -> list[str]:
    """静态扫描抽取方法完整源码(声明行起至方法体 '}' 止), 宁多勿漏。

    在净化文本上定位(字符串/注释中的花括号不干扰配对), 下标回切原串保留真实源码;
    排除 a.name( / a::name( 形式的调用点; arity 非 None 时按参数个数过滤;
    抽象/接口式无体声明(先遇 ';')跳过。

    Args:
        stripped: 调用方预先剥离的净化文本(须与 source 等长), 复用可避免
                  重复剥离; 未传或长度不匹配时才重新剥离。
    """
    results: list[str] = []
    if stripped is None or len(stripped) != len(source):
        stripped = strip_comments_and_strings(source)
    for m in re.finditer(rf"\b{re.escape(method_name)}\s*\(", stripped):
        start = m.start()
        prev = stripped[:start].rstrip()
        if prev.endswith(".") or prev.endswith("::"):
            continue  # 限定调用 / 方法引用, 不是声明
        open_pos = stripped.index("(", start)
        depth, j = 0, open_pos
        while j < len(stripped):
            if stripped[j] == "(":
                depth += 1
            elif stripped[j] == ")":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        if j >= len(stripped):
            continue
        params = stripped[open_pos + 1:j].strip()
        n_params = 0 if not params else len(split_top_level(params))
        if arity is not None and n_params != arity:
            continue
        brace = stripped.find("{", j)
        semi = stripped.find(";", j)
        if brace < 0 or (0 <= semi < brace):
            continue  # 抽象/接口声明, 无方法体
        _, end = match_braces(stripped, brace)
        if end < 0:
            continue
        line_start = source.rfind("\n", 0, start) + 1
        results.append(source[line_start:end + 1])
    return results


def private_method_names(source: str,
                         stripped: str | None = None) -> set[str]:
    """本类 private 方法声明名集合。

    在净化文本上扫描, 排除字符串/注释中的伪声明、字段初始化式('=' 之后)
    与 new Foo( 构造调用点。

    Args:
        stripped: 调用方预先剥离的净化文本(须与 source 等长), 复用可避免
                  重复剥离; 未传或长度不匹配时才重新剥离。
    """
    if stripped is None or len(stripped) != len(source):
        stripped = strip_comments_and_strings(source)
    names: set[str] = set()
    for m in _PRIVATE_METHOD_RE.finditer(stripped):
        prefix = stripped[m.start():m.start(1)]
        if "=" in prefix or re.search(r"\bnew\s+$", prefix):
            continue  # 字段初始化式调用或 new 构造, 非方法声明
        names.add(m.group(1))
    return names


def extract_with_helpers(source: str, method_name: str,
                         arity: int | None = None) -> tuple[list[str], list[str]]:
    """返回 (主方法源码块列表, 被调用的本类 private 方法源码块列表)。

    从主方法出发递归收集其调用到的 private 方法(规则 a 需连带覆盖), 宁多勿漏。
    净化文本只在入口剥离一次, 经 stripped 参数贯穿主方法/ helper 递归抽取,
    避免对同一源文件重复剥离导致的 O(n*m) 开销。
    """
    stripped = strip_comments_and_strings(source)
    main_blocks = extract_method_blocks(source, method_name, arity, stripped)
    helper_names = private_method_names(source, stripped)
    collected: dict[str, list[str]] = {}
    visited = {method_name}
    frontier = list(main_blocks)
    while frontier:
        block = frontier.pop()
        for call in _CALL_RE.findall(block):
            if call in helper_names and call not in visited:
                visited.add(call)
                blocks = extract_method_blocks(source, call, stripped=stripped)
                collected[call] = blocks
                frontier.extend(blocks)
    helpers: list[str] = []
    for name, blocks in collected.items():
        helpers.extend(blocks or [f"// 未找到 private 方法 {name} 的源码(可能继承自父类)"])
    return main_blocks, helpers


# --------------------------------------------------------------------------- #
# 目标类源文件定位
# --------------------------------------------------------------------------- #
def locate_source_file(project_root: str | Path, fqcn: str,
                       modules: list[str] | None = None) -> Path | None:
    """按 FQCN 在 **/src/main/java/ 下定位源文件; 未找到返回 None。

    Args:
        modules: Maven 模块列表；提供时优先在模块目录下搜索，避免全树遍历。
    """
    parts = fqcn.split(".")
    rel = Path("src", "main", "java", *parts[:-1], parts[-1] + ".java")
    root = Path(project_root)

    # 优先在模块目录下搜索
    if modules:
        for module in modules:
            module_dir = (root / module) if module != "." else root
            candidate = module_dir / rel
            if candidate.is_file():
                return candidate

    # fallback 到全树搜索
    for dirpath, dirnames, _ in os.walk(str(project_root)):
        dirnames[:] = [d for d in dirnames if d not in config.PRUNE_DIRS]
        candidate = Path(dirpath) / rel
        if candidate.is_file():
            return candidate
    return None


def fqcn_from_source_path(source_file: str | Path) -> str | None:
    """从 src/main/java 之后的相对路径反推 FQCN; 不在该目录下返回 None。"""
    parts = Path(source_file).parts
    for i in range(len(parts) - 3):
        if parts[i:i + 3] == ("src", "main", "java"):
            rel = parts[i + 3:]
            if not rel or not rel[-1].endswith(".java"):
                return None
            return ".".join([*rel[:-1], rel[-1][:-len(".java")]])
    return None


def resolve_target(project_root: Path, class_arg: str) -> tuple[str | None, Path | None]:
    """解析目标类参数: 返回 (FQCN, 源文件路径|None)。

    class_arg 可为 FQCN, 也可为 .java 源文件路径(含分隔符或以 .java 结尾)。
    """
    if class_arg.endswith(".java") or "/" in class_arg or "\\" in class_arg:
        src = Path(class_arg)
        if not src.is_absolute():
            src = project_root / src
        fqcn = fqcn_from_source_path(src)
        return fqcn, (src if src.is_file() else None)

    # 解析 Maven 模块结构，优先在模块目录下搜索
    modules = maven.get_maven_modules(project_root) if maven.is_multi_module(project_root) else None
    return class_arg, locate_source_file(project_root, class_arg, modules)


def diagnose_missing_source(project_root: Path, class_arg: str) -> str:
    """为源文件未找到的情况生成诊断提示。

    检查:
    1. FQCN 是否拼写有误（搜索相近的 .java 文件名）
    2. 类是否存在于 src/test/java（误传测试类）
    3. 类是否存在于其他非标准路径
    """
    hints: list[str] = []
    # 当 class_arg 是路径时取文件名(去 .java); 是 FQCN 时取简单类名
    if class_arg.endswith(".java") or "/" in class_arg or "\\" in class_arg:
        simple_name = Path(class_arg).stem
    else:
        simple_name = class_arg.rsplit(".", 1)[-1]

    # 检查是否在测试目录（仅对 FQCN 形式有意义）
    if not (class_arg.endswith(".java") or "/" in class_arg or "\\" in class_arg):
        test_rel = Path("src", "test", "java", *class_arg.split("."))
        test_rel = test_rel.with_suffix(".java")
        for dirpath, dirnames, _ in os.walk(str(project_root)):
            dirnames[:] = [d for d in dirnames if d not in config.PRUNE_DIRS]
            candidate = Path(dirpath) / test_rel
            if candidate.is_file():
                hints.append(f"在测试目录找到: {candidate}(请传入主源码 FQCN, 而非测试类)")
                break

    # 搜索相近文件名（简单名匹配）
    matches: list[str] = []
    for dirpath, dirnames, filenames in os.walk(str(project_root)):
        dirnames[:] = [d for d in dirnames if d not in config.PRUNE_DIRS]
        for fn in filenames:
            if fn == simple_name + ".java" and "src" in dirpath and "main" in dirpath:
                matches.append(str(Path(dirpath) / fn))
    if matches:
        hints.append(f"找到同名文件: {', '.join(matches[:3])}(检查包名是否正确)")

    if not hints:
        hints.append(f"确认 FQCN '{class_arg}' 拼写正确且文件在 src/main/java 下")

    return "; ".join(hints)
