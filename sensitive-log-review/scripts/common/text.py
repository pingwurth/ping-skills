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

    兼容 git quotePath 的 C 引号形态：首尾为双引号时剥去引号（F05 兜底；
    git_utils 已统一 ``-z`` + ``core.quotepath=false``，正常流程不应再出现）。

    示例:
        "some/path/../actual/file.java" -> "actual/file.java"
        "normal/path/file.java" -> "normal/path/file.java"
        "../../file.java" -> "file.java"
        '"src/main/java/\\344\\270\\255.java"' -> 'src/main/java/\\344\\270\\255.java'
    """
    if len(file_path) >= 2 and file_path.startswith('"') and file_path.endswith('"'):
        file_path = file_path[1:-1]
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


def match_changed_files(changed_files: list[str], list_lines: list[str]) -> list[str]:
    """找出路径命中变更文件集合的清单行（F34 索引化，check_log_print /
    check_sensitive_word 共用）。

    匹配语义与旧版一致：路径分隔符统一规范化（反斜杠→正斜杠）后，
    变更文件路径（git 返回仓库相对路径）作为**子串**出现在清单行中
    （扫描器写绝对路径，因此实践中是"清单行路径以 /<相对路径> 结尾"）
    即命中；命中行按变更文件顺序、清单行顺序输出，同一行可被多个
    变更文件重复命中。

    实现差异（相对旧版"变更数 × 行数"的全行子串扫描）：
      - 每行经 parse_list_line 解析一次，按**路径字段**的末段文件名
        建立索引后再做子串验证，复杂度降为近似线性；
      - 无法解析的行（罕见）退回旧版全行子串匹配；
      - 匹配目标收窄到路径字段，不再覆盖行内容中的巧合命中
        （Java 源码内容不可能含 .java 路径，实际结果等价）。
    """
    from collections import defaultdict

    # rows: (原始行, 规范化路径或 None, 匹配目标文本), 保持清单行序
    rows: list[tuple[str, str | None, str]] = []
    by_filename: dict[str, list[int]] = defaultdict(list)  # 路径末段 -> 行序号
    has_unparsable = False
    for line in list_lines:
        parsed = parse_list_line(line)
        if parsed is None:
            rows.append((line, None, normalize_for_matching(line)))
            has_unparsable = True
        else:
            path = normalize_for_matching(parsed[0])
            rows.append((line, path, path))
            by_filename[path.rsplit("/", 1)[-1]].append(len(rows) - 1)

    matched: list[str] = []
    for file_path in changed_files:
        normalized_file = normalize_for_matching(file_path)
        if not normalized_file:
            # 空串子串恒命中（保留旧版行为，正常流程不会出现）
            matched.extend(line for line, _p, _m in rows)
            continue
        candidates = by_filename.get(normalized_file.rsplit("/", 1)[-1], ())
        if has_unparsable:
            # 存在不可解析行: 保持行序需全量走一遍（不可解析行无法索引）
            candidate_set = set(candidates)
            for index, (line, path, match_target) in enumerate(rows):
                if path is None or index in candidate_set:
                    if normalized_file in match_target:
                        matched.append(line)
        else:
            # 常规路径: 候选桶内已按行序排列，仅对候选做子串验证
            for index in candidates:
                line, _path, match_target = rows[index]
                if normalized_file in match_target:
                    matched.append(line)
    return matched
