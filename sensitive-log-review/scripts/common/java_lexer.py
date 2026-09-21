"""Java 词法分析器（P1-1 公共抽取）。

本模块合并了原 extract_java_fields.py 与 check_tostring_annotation.py 中
重复的约 1000 行词法分析代码，行为与原实现保持一致：

- _mask_comments_and_literals: 注释/字符串/字符/文本块屏蔽状态机
- iter_fields: 类体字段迭代（含 record 组件、枚举常量、匿名类处理）
- iter_classes: 命名类型声明迭代（含前置注解提取，yield 类型 kind）

抽取自:
- scripts/extract_java_fields.py   (字段提取相关全部辅助函数)
- scripts/check_tostring_annotation.py (类声明/注解提取相关函数)
"""

from __future__ import annotations

import bisect
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

# ---------------------------------------------------------------------------
# 常量定义
# ---------------------------------------------------------------------------

# Java 标识符正则：字母/下划线/$ 开头，后跟字母数字/_/$，支持 Unicode
IDENTIFIER_PATTERN = r"(?:[^\W\d]|\$)[\w$]*"
IDENTIFIER_RE = re.compile(IDENTIFIER_PATTERN, flags=re.UNICODE)

# 命名类型声明正则（负向回顾排除 obj.class 成员访问）
NAMED_TYPE_DECLARATION_RE = re.compile(
    rf"(?<![.\w$])(?P<kind>class|interface|enum|record)\s+(?P<name>{IDENTIFIER_PATTERN})",
    flags=re.UNICODE,
)

# 字段提取热路径正则预编译(每个字段声明都会命中, 避免重复构造模式串)
_FIRST_FIELD_RE = re.compile(
    rf"(?P<type>.+?)\s+(?P<name>{IDENTIFIER_PATTERN})\s*(?:\[\s*\])?\s*$",
    flags=re.DOTALL,
)
_EXTRA_FIELD_RE = re.compile(
    rf"(?P<name>{IDENTIFIER_PATTERN})\s*(?:\[\s*\])?\s*$",
    flags=re.DOTALL,
)
_ANNOTATION_NAME_RE = re.compile(rf"@(?:{IDENTIFIER_PATTERN}\.)*{IDENTIFIER_PATTERN}")

# 字段修饰符
FIELD_MODIFIERS = frozenset({
    "public", "protected", "private", "static", "final", "transient", "volatile",
})

# 类修饰符（注解回溯提取时跳过）
CLASS_MODIFIERS = frozenset({
    "public", "private", "protected", "static", "final",
    "abstract", "strictfp", "sealed", "non-sealed",
})


@dataclass
class BraceContext:
    """花括号上下文追踪器 - 用于解析Java代码中的嵌套结构

    属性说明：
    - kind: 上下文类型，'type'表示类型体，'block'表示其他块结构
    - reset_statement: 是否在遇到右花括号时重置语句起始位置
    - resume_statement_start: 匿名类型关闭后恢复的语句起始位置
    - type_kind: 具体类型种类（class/interface/enum/record/enum_constant/anonymous）
    - enum_constants_active: 枚举常量是否处于活动状态
    """
    kind: str
    reset_statement: bool
    resume_statement_start: int | None = None
    type_kind: str | None = None
    enum_constants_active: bool = False


# ---------------------------------------------------------------------------
# 注释和字面量屏蔽
# ---------------------------------------------------------------------------


def _mask_comments_and_literals(source: str) -> str:
    """屏蔽Java源代码中的注释和字符串字面量。

    将注释和字面量内容替换为空格，保留换行符以维护行号准确性。
    支持：单行注释、多行注释、文本块、字符串字面量、字符字面量。
    """
    result = list(source)
    index = 0
    state = "code"

    while index < len(source):
        char = source[index]
        next_char = source[index + 1] if index + 1 < len(source) else ""

        if state == "code":
            if char == "/" and next_char == "/":
                result[index] = result[index + 1] = " "
                index += 2
                state = "line_comment"
                continue
            if char == "/" and next_char == "*":
                result[index] = result[index + 1] = " "
                index += 2
                state = "block_comment"
                continue
            if source.startswith('"""', index):
                result[index: index + 3] = [" ", " ", " "]
                index += 3
                state = "text_block"
                continue
            if char == '"':
                result[index] = " "
                index += 1
                state = "string"
                continue
            if char == "'":
                result[index] = " "
                index += 1
                state = "char"
                continue
            index += 1
            continue

        if state == "line_comment":
            if char == "\n":
                state = "code"
            else:
                result[index] = " "
            index += 1
            continue

        if state == "block_comment":
            if char == "*" and next_char == "/":
                result[index] = result[index + 1] = " "
                index += 2
                state = "code"
            else:
                if char != "\n":
                    result[index] = " "
                index += 1
            continue

        if state == "text_block":
            if source.startswith('"""', index):
                result[index: index + 3] = [" ", " ", " "]
                index += 3
                state = "code"
            else:
                if char != "\n":
                    result[index] = " "
                index += 1
            continue

        quote = '"' if state == "string" else "'"
        if char == "\\" and index + 1 < len(source):
            result[index] = " "
            if source[index + 1] != "\n":
                result[index + 1] = " "
            index += 2
        elif char == quote:
            result[index] = " "
            index += 1
            state = "code"
        else:
            if char != "\n":
                result[index] = " "
            index += 1

    return "".join(result)


# ---------------------------------------------------------------------------
# 字段迭代（类体字段 / record 组件 / 枚举常量）
# ---------------------------------------------------------------------------


def iter_fields(path: Path, on_error=None) -> Iterator[tuple[int, str]]:
    """从Java源文件中迭代提取字段信息

    轻量级词法解析器（非Java编译器），只提取类体内的字段声明，
    排除方法局部变量；支持匿名类型、枚举常量、记录组件等复杂情况。

    on_error: 可选回调 (文件路径, 异常)，读取失败时调用（失败文件统计）。

    Yields:
        (行号, 字段名) 元组
    """
    try:
        original = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as exc:
        print(f"warning: cannot read {path}: {exc}", file=sys.stderr)
        if on_error is not None:
            on_error(path, exc)
        return

    source = _mask_comments_and_literals(original)

    line_starts = [0]
    line_starts.extend(index + 1 for index, char in enumerate(source) if char == "\n")

    statement_start = 0
    braces: list[BraceContext] = []
    found: list[tuple[int, int, str]] = []

    for index, char in enumerate(source):
        if char == "{":
            prefix = source[statement_start:index]
            type_kind = _named_type_kind(prefix)

            if type_kind is not None:
                if type_kind == "record":
                    for field_name, name_offset in _extract_record_component_names(prefix):
                        name_index = statement_start + name_offset
                        line_number = bisect.bisect_right(line_starts, name_index)
                        found.append((name_index, line_number, field_name))
                braces.append(
                    BraceContext(
                        kind="type",
                        reset_statement=True,
                        type_kind=type_kind,
                        enum_constants_active=type_kind == "enum",
                    )
                )
                statement_start = index + 1

            elif _is_enum_constant_body(prefix, braces):
                braces.append(BraceContext("type", True, statement_start, "enum_constant"))
                statement_start = index + 1

            elif _is_anonymous_type_body(prefix):
                braces.append(BraceContext("type", True, statement_start, "anonymous"))
                statement_start = index + 1

            else:
                preserve_statement = bool(
                    braces
                    and braces[-1].kind == "type"
                    and (
                        _has_assignment(prefix)
                        or _is_lambda_body(prefix)
                        or _inside_annotation_arguments(prefix)
                    )
                )
                braces.append(BraceContext("block", not preserve_statement))
            continue

        if char == "}":
            if braces:
                if braces[-1].type_kind == "enum" and braces[-1].enum_constants_active:
                    statement = source[statement_start:index]
                    for field_name, name_offset in _extract_enum_constant_names(statement):
                        name_index = statement_start + name_offset
                        line_number = bisect.bisect_right(line_starts, name_index)
                        found.append((name_index, line_number, field_name))
                    braces[-1].enum_constants_active = False

                context = braces.pop()
                if context.resume_statement_start is not None:
                    statement_start = context.resume_statement_start
                elif context.reset_statement:
                    statement_start = index + 1
            else:
                statement_start = index + 1
            continue

        if char != ";" or not braces or braces[-1].kind != "type":
            continue

        statement = source[statement_start:index]

        if braces[-1].type_kind == "enum" and braces[-1].enum_constants_active:
            for field_name, name_offset in _extract_enum_constant_names(statement):
                name_index = statement_start + name_offset
                line_number = bisect.bisect_right(line_starts, name_index)
                found.append((name_index, line_number, field_name))
            braces[-1].enum_constants_active = False

        for field_name, name_offset in _extract_field_names(statement):
            name_index = statement_start + name_offset
            line_number = bisect.bisect_right(line_starts, name_index)
            found.append((name_index, line_number, field_name))

        statement_start = index + 1

    for _, line_number, field_name in sorted(found, key=lambda item: (item[0], item[2])):
        yield line_number, field_name


def _extract_field_names(statement: str) -> list[tuple[str, int]]:
    """从字段声明语句中提取所有字段名及其偏移位置。

    支持单字段、多字段(int x, y, z)、带初始化、数组标记、注解修饰符前缀。
    """
    declaration, declaration_offset = _strip_prefix(statement)
    if not declaration:
        return []

    parts = _split_top_level(declaration, ",")
    if not parts:
        return []

    first_part, first_offset = parts[0]
    first_without_initializer = _before_top_level(first_part, "=")
    first_match = _FIRST_FIELD_RE.search(first_without_initializer)
    if not first_match or not _looks_like_type(first_match.group("type")):
        return []

    result = [
        (
            first_match.group("name"),
            declaration_offset + first_offset + first_match.start("name"),
        )
    ]

    for part, offset in parts[1:]:
        name_source = _before_top_level(part, "=")
        match = _EXTRA_FIELD_RE.search(name_source)
        if match:
            result.append((match.group("name"), declaration_offset + offset + match.start("name")))
    return result


def _strip_prefix(statement: str) -> tuple[str, int]:
    """去除字段声明语句中的注解和修饰符前缀，返回(声明部分, 前缀偏移量)。"""
    index = 0
    while True:
        index = _skip_whitespace(statement, index)
        annotation_end = _annotation_end(statement, index)
        if annotation_end is not None:
            index = annotation_end
            continue

        modifier = IDENTIFIER_RE.match(statement, index)
        if modifier and modifier.group() in FIELD_MODIFIERS:
            index = modifier.end()
            continue
        break
    return statement[index:], index


def _annotation_end(value: str, index: int) -> int | None:
    """识别Java注解的结束位置，非注解返回 None。支持全限定名与带参注解。"""
    if index >= len(value) or value[index] != "@":
        return None
    match = _ANNOTATION_NAME_RE.match(value[index:])
    if not match:
        return None
    end = _skip_whitespace(value, index + match.end())
    if end < len(value) and value[end] == "(":
        closing = _matching_paren(value, end)
        if closing is None:
            return None
        end = closing + 1
    return end


def _matching_paren(value: str, opening: int) -> int | None:
    """查找与指定左括号匹配的右括号位置（深度计数法）。"""
    depth = 0
    for index in range(opening, len(value)):
        if value[index] == "(":
            depth += 1
        elif value[index] == ")":
            depth -= 1
            if depth == 0:
                return index
    return None


def _skip_whitespace(value: str, index: int) -> int:
    """跳过空白字符，返回第一个非空白字符的位置。"""
    while index < len(value) and value[index].isspace():
        index += 1
    return index


def _looks_like_type(value: str) -> bool:
    """判断字符串是否像Java类型声明（排除 return/throw/new/case 与含括号的表达式）。"""
    compact = _remove_annotations(value).strip()
    if not compact or "(" in compact or ")" in compact:
        return False
    first_word = IDENTIFIER_RE.match(compact)
    return bool(first_word and first_word.group() not in {"return", "throw", "new", "case"})


def _has_assignment(value: str) -> bool:
    """检查声明语句中是否包含顶层赋值操作符。"""
    declaration, _ = _strip_prefix(value)
    return _before_top_level(declaration, "=") != declaration


def _named_type_kind(value: str) -> str | None:
    """识别命名类型声明关键字（class/interface/enum/record）。"""
    match = NAMED_TYPE_DECLARATION_RE.search(value)
    return match.group("kind") if match else None


def _is_enum_constant_body(value: str, braces: list[BraceContext]) -> bool:
    """识别枚举常量附加的匿名类型体。"""
    if not braces:
        return False
    context = braces[-1]
    return (
        context.kind == "type"
        and context.type_kind == "enum"
        and context.enum_constants_active
        and not _is_lambda_body(value)
    )


def _is_lambda_body(value: str) -> bool:
    """判断语句是否以 lambda 箭头运算符（->）结尾。"""
    return bool(re.search(r"->\s*$", value))


def _inside_annotation_arguments(value: str) -> bool:
    """判断当前花括号是否位于某个注解的未闭合参数列表内。"""
    for match in re.finditer(rf"@(?:{IDENTIFIER_PATTERN}\.)*{IDENTIFIER_PATTERN}", value):
        opening = _skip_whitespace(value, match.end())
        if opening >= len(value) or value[opening] != "(":
            continue
        depth = 0
        for char in value[opening:]:
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
        if depth > 0:
            return True
    return False


def _remove_annotations(value: str) -> str:
    """去除字符串中的注解（替换为空格，保留原始长度）。"""
    result = list(value)
    index = 0
    while index < len(value):
        if value[index] == "@":
            end = _annotation_end(value, index)
            if end is not None:
                result[index:end] = " " * (end - index)
                index = end
                continue
        index += 1
    return "".join(result)


def _extract_record_component_names(record_declaration: str) -> list[tuple[str, int]]:
    """提取记录类型（record）的组件字段名及偏移位置。"""
    match = NAMED_TYPE_DECLARATION_RE.search(record_declaration)
    if not match or match.group("kind") != "record":
        return []
    opening = record_declaration.find("(", match.end())
    if opening == -1:
        return []
    closing = _matching_paren(record_declaration, opening)
    if closing is None:
        return []

    components = record_declaration[opening + 1: closing]
    result: list[tuple[str, int]] = []
    for component, offset in _split_top_level(components, ","):
        declaration, declaration_offset = _strip_prefix(component)
        match = re.search(
            rf"(?P<type>.+?)\s+(?P<name>{IDENTIFIER_PATTERN})\s*(?:\.\.\.)?\s*$",
            declaration,
            flags=re.DOTALL,
        )
        if match and _looks_like_type(match.group("type")):
            result.append((match.group("name"), opening + 1 + offset + declaration_offset + match.start("name")))
    return result


def _extract_enum_constant_names(enum_constants: str) -> list[tuple[str, int]]:
    """提取枚举常量名称及偏移位置。"""
    result: list[tuple[str, int]] = []
    for constant, offset in _split_top_level(enum_constants, ","):
        declaration, declaration_offset = _strip_prefix(constant)
        match = IDENTIFIER_RE.search(declaration)
        if match:
            result.append((match.group(), offset + declaration_offset + match.start()))
    return result


def _is_anonymous_type_body(value: str) -> bool:
    """识别匿名类型体（new Type(...) { ... } 形式）。"""
    current_statement = value[value.rfind(";") + 1:]
    return bool(re.search(r"\bnew\b[\s\S]*\)\s*$", current_statement))


def _before_top_level(value: str, delimiter: str) -> str:
    """获取第一个顶层分隔符之前的内容。"""
    for index in _top_level_indexes(value, delimiter):
        return value[:index]
    return value


def _split_top_level(value: str, delimiter: str) -> list[tuple[str, int]]:
    """按顶层分隔符分割字符串，返回每部分及其偏移位置。"""
    indexes = list(_top_level_indexes(value, delimiter))
    boundaries = [-1, *indexes, len(value)]
    return [
        (value[boundaries[position] + 1: boundaries[position + 1]], boundaries[position] + 1)
        for position in range(len(boundaries) - 1)
    ]


def _top_level_indexes(value: str, delimiter: str) -> Iterator[int]:
    """查找顶层分隔符位置（不在任何括号/尖括号内；= 之后停止追踪尖括号）。"""
    angles = parentheses = brackets = braces = 0
    track_angles = True

    for index, char in enumerate(value):
        if char == "=" and not any((parentheses, brackets, braces)):
            if delimiter == "=" and not angles:
                yield index
            track_angles = False
            angles = 0
        elif char == "<" and track_angles:
            angles += 1
        elif char == ">" and track_angles and angles:
            angles -= 1
        elif char == "(":
            parentheses += 1
        elif char == ")" and parentheses:
            parentheses -= 1
        elif char == "[":
            brackets += 1
        elif char == "]" and brackets:
            brackets -= 1
        elif char == "{":
            braces += 1
        elif char == "}" and braces:
            braces -= 1
        elif char == delimiter and not any((angles, parentheses, brackets, braces)):
            yield index


# ---------------------------------------------------------------------------
# 类声明迭代与注解提取
# ---------------------------------------------------------------------------


def _extract_annotations_before(source: str, position: int) -> list[str]:
    """从指定位置向前提取注解列表。

    跳过空白字符和类修饰符关键字，提取所有紧邻的注解
    （包括带参数和不带参数的注解），按源代码顺序返回。
    """
    annotations: list[str] = []
    pos = position

    while pos > 0:
        while pos > 0 and source[pos - 1].isspace():
            pos -= 1

        # 带参数的注解：以 ) 结尾
        if pos > 0 and source[pos - 1] == ")":
            depth = 1
            end = pos
            pos -= 1
            while pos > 0 and depth > 0:
                if source[pos - 1] == ")":
                    depth += 1
                elif source[pos - 1] == "(":
                    depth -= 1
                pos -= 1
            while pos > 0 and (source[pos - 1].isalnum() or source[pos - 1] in "._$"):
                pos -= 1
            if pos > 0 and source[pos - 1] == "@":
                pos -= 1
                annotations.append(source[pos:end])
                continue
            else:
                break

        # 不带参数的注解
        end = pos
        while pos > 0 and (source[pos - 1].isalnum() or source[pos - 1] in "._$"):
            pos -= 1
        if pos > 0 and source[pos - 1] == "@":
            pos -= 1
            annotations.append(source[pos:end])
            continue

        # 类修饰符关键字
        word = source[pos:end].strip()
        if word in CLASS_MODIFIERS:
            continue

        break

    return list(reversed(annotations))


def iter_classes(path: Path, on_error=None) -> Iterator[tuple[int, str, list[str], str]]:
    """从Java源文件中迭代提取命名类型声明及其前置注解。

    匿名类不会被匹配（没有名称）。

    on_error: 可选回调 (文件路径, 异常)，读取失败时调用（失败文件统计）。

    Yields:
        (行号, 类名, 前置注解列表, 类型kind) 元组；
        kind 为 "class" | "interface" | "enum" | "record"（P2-3，
        供调用方对 interface/enum/record 采用不同检查策略）。
    """
    try:
        original = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as exc:
        print(f"warning: cannot read {path}: {exc}", file=sys.stderr)
        if on_error is not None:
            on_error(path, exc)
        return

    source = _mask_comments_and_literals(original)

    line_starts = [0]
    line_starts.extend(index + 1 for index, char in enumerate(source) if char == "\n")

    for match in NAMED_TYPE_DECLARATION_RE.finditer(source):
        class_position = match.start("kind")
        class_name = match.group("name")
        kind = match.group("kind")

        line_number = bisect.bisect_right(line_starts, class_position)
        annotations = _extract_annotations_before(source, class_position)

        yield line_number, class_name, annotations, kind


def check_tostring_annotation(annotations: list[str]) -> tuple[bool, str]:
    """检查注解列表中是否包含有效的 @ToString(of = {...}) 注解。

    Returns:
        (True, "") 通过；(False, reason) 未通过。
    """
    for annotation in annotations:
        # 匹配 @ToString 或 @lombok.ToString，但不匹配 @ToStringBuilder 等
        match = re.match(r"@(?:\w+\.)*ToString\b", annotation)
        if not match:
            continue

        of_match = re.search(r"\bof\s*=", annotation)
        if of_match:
            return True, ""
        else:
            return False, "Missing 'of' attribute in @ToString annotation"

    return False, "Missing @ToString annotation"
