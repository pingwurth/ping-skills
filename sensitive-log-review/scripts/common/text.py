"""文本工具：字段名拆分与路径规范化（消除三处重复实现）。"""

from __future__ import annotations

import re

# 字段名拆分所用正则预编译(避免热路径内重复查找 re 模块缓存)
_DIGIT_SPLIT_RE = re.compile(r"(\d+)")
_CAMEL_LOWER_UPPER_RE = re.compile(r"([a-z])([A-Z])")
_CAMEL_ACRONYM_RE = re.compile(r"([A-Z]+)([A-Z][a-z])")


def split_field_name(field_name: str) -> list[str]:
    """将标识符拆分为单词列表（驼峰/下划线/数字拆分）。

    拆分规则:
    1. 下划线(_)拆分, 不保留下划线
    2. 数字拆分, 不保留数字
    3. 驼峰命名拆分（小写字母后紧跟大写字母、连续大写边界）

    示例:
        "getPassword" -> ["get", "password"]
        "user_name" -> ["user", "name"]
        "userId2" -> ["user", "id"]
        "getUserID" -> ["get", "user", "id"]
    """
    if not field_name:
        return []
    parts = field_name.split("_")
    words: list[str] = []
    for part in parts:
        if not part:
            continue
        segments = _DIGIT_SPLIT_RE.split(part)
        for segment in segments:
            if not segment or segment.isdigit():
                continue
            camel_parts = _CAMEL_LOWER_UPPER_RE.sub(r"\1 \2", segment)
            camel_parts = _CAMEL_ACRONYM_RE.sub(r"\1 \2", camel_parts)
            for word in camel_parts.split():
                if word:
                    words.append(word.lower())
    return words


def normalize_file_path(file_path: str) -> str:
    """规范化 git 输出的文件路径，处理连续双点(..)的情况。

    如果路径中存在连续两个点，则从开始位置到最后一处连续两个点的内容截断，
    只保留后面的部分。

    示例:
        "some/path/../actual/file.java" -> "actual/file.java"
        "normal/path/file.java" -> "normal/path/file.java"
        "../../file.java" -> "file.java"
    """
    last_double_dot = file_path.rfind("..")

    if last_double_dot != -1:
        start_index = last_double_dot + 2
        if start_index < len(file_path) and file_path[start_index] in ('/', '\\'):
            start_index += 1
        return file_path[start_index:]

    return file_path


def normalize_for_matching(path: str) -> str:
    """路径匹配统一规范化: 仅将反斜杠转为 POSIX 风格正斜杠。

    用于跨脚本的路径比较/缓存键统一, 消除 Windows 反斜杠与 git 输出
    正斜杠的差异。注意: 不改变大小写(大小写策略由调用方自行把握)。
    """
    return path.replace("\\", "/")


# 清单行统一格式（java_log_scanner / extract_java_fields 产物）:
#   带 tags: {path}#{line} [TAG1,TAG2] {内容}
#   无 tags: {path}#{line}: {内容}
LIST_LINE_RE = re.compile(
    r"^(?P<path>.+?)#(?P<line>\d+)(?: \[(?P<tags>[^\]]*)\])?:?\s(?P<content>.*)$"
)


def parse_list_line(line: str) -> tuple[str, int, list[str], str] | None:
    """解析清单行，返回 (文件路径, 行号, tags列表, 内容)；无法解析返回 None。

    兼容新旧两种格式（见 LIST_LINE_RE），旧格式 tags 为空列表。
    """
    match = LIST_LINE_RE.match(line)
    if not match:
        return None
    tags = [t.strip() for t in (match.group("tags") or "").split(",") if t.strip()]
    return match.group("path"), int(match.group("line")), tags, match.group("content")
