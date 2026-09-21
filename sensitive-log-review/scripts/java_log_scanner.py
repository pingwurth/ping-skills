#!/usr/bin/env python3
"""扫描 Java 工程中的日志输出，检查是否合规。

检测类型:
  JSON_LOG      - 日志将对象转 JSON 后打印
  SENSITIVE     - 日志标识符命中高置信敏感词（core/blacklist 层）
  LOW           - 日志标识符命中低置信上下文词（extended 层，默认不触发门禁）
  SYSOUT        - 使用 System.out/err 打印（rules/log-scanner.json 开启后生效）
  STACK_TRACE   - 调用 printStackTrace（同上，默认关闭）

输出:
  合规日志写入 <输出目录>/log-print-ok.list       格式: {path}#{line}: {原始行}
  违规日志写入 <输出目录>/log-print-violation.list 格式: {path}#{line} [TAGS] {原始行}

扫描范围:
  默认全量扫描项目根目录；--files <清单> 时仅扫描清单中的 Java 文件
  （main.py --changed-only 模式使用该入口）。

退出码:
  0: 扫描完成
  2: 执行错误（目录不存在、配置文件损坏等）
"""

from __future__ import annotations

import argparse
import functools
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common.dictionary import SensitiveDicts, load_sensitive_dicts  # noqa: E402
from common.failures import FailureRecorder  # noqa: E402
from common.paths import LOG_OK, LOG_VIOLATION, artifact, get_output_dir  # noqa: E402
from common.text import split_field_name  # noqa: E402

SCRIPT_DIR = Path(__file__).resolve().parent
SCANNER_CONFIG_FILE = SCRIPT_DIR / "rules" / "log-scanner.json"

# 对象转 JSON 的常见模式
JSON_CONVERSION_PATTERN = re.compile(
    r'(?:'
    r'JSON\s*\.\s*(?:toJSONString|parseObject|parseArray)'       # fastjson
    r'|JSONObject\s*\.\s*(?:toJSONString|fromObject)'             # org.json
    r'|Gson\s*\.\s*toJson'                                        # Gson
    r'|(?:\w+\.)?writeValueAsString'                              # Jackson (含变量名)
    r'|(?:\w+\.)?toJson(?:Str|PrettyStr)?'                        # 通用 toJson
    r'|JSONUtil\s*\.\s*(?:toJsonStr|toJsonPrettyStr)'             # Hutool
    r'|XmlUtils\s*\.\s*objectToXmlStr'                            # 自定义 XML 工具
    r')',
    re.IGNORECASE
)

# 日志语句中变量/方法调用提取（排除字符串字面量）
IDENTIFIER_PATTERN = re.compile(r'\b([a-zA-Z_]\w*)\b')

# 可选检测项（默认关闭）
SYSTEM_OUT_PATTERN = re.compile(r'\bSystem\s*\.\s*(?:out|err)\s*\.\s*print')
PRINT_STACK_TRACE_PATTERN = re.compile(r'\.\s*printStackTrace\s*\(')

# 字符串字面量剥离正则(每行日志都会调用, 预编译避免重复查找)
STRING_LITERAL_PATTERN = re.compile(r'"(?:[^"\\]|\\.)*"')

DEFAULT_LOGGER_NAMES = ["log", "logger", "LOG", "LOGGER", "LogHelper", "LogUtil"]


def load_scanner_config(config_path: Path = SCANNER_CONFIG_FILE) -> dict:
    """加载日志扫描配置；文件缺失/损坏时回退默认配置并告警。"""
    defaults = {
        "logger_names": list(DEFAULT_LOGGER_NAMES),
        "detect_system_out": False,
        "detect_print_stack_trace": False,
    }
    if not config_path.is_file():
        return defaults
    try:
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"警告: 扫描配置 JSON 损坏，使用默认配置: {config_path}: {exc}", file=sys.stderr)
        return defaults
    names = cfg.get("logger_names")
    if not isinstance(names, list) or not all(isinstance(n, str) and n for n in names):
        cfg["logger_names"] = list(DEFAULT_LOGGER_NAMES)
    return {**defaults, **{k: v for k, v in cfg.items() if not k.startswith("_")}}


@functools.lru_cache(maxsize=8)
def _build_log_call_pattern_cached(logger_names: tuple[str, ...]) -> re.Pattern:
    """按 logger_names 元组缓存编译结果, 同一配置只编译一次(线程安全)。"""
    names = "|".join(re.escape(n) for n in logger_names)
    return re.compile(rf"\b(?:{names})\s*\.\s*(?:trace|debug|info|warn|error|fatal)\s*\(")


def build_log_call_pattern(logger_names: list[str]) -> re.Pattern:
    """根据配置的日志变量名构建日志调用匹配正则(P2-1, 覆盖 LOGGER 等常量风格)。

    内部基于 lru_cache 模块级缓存, 独立调用路径下多次调用不会重复编译。
    """
    return _build_log_call_pattern_cached(tuple(logger_names))


def is_parentheses_closed(text: str) -> bool:
    """检查文本中的括号是否完全闭合。"""
    depth = 0
    in_string = False
    escape_next = False
    for ch in text:
        if escape_next:
            escape_next = False
            continue
        if ch == '\\' and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
            if depth < 0:
                return True
    return depth == 0


def extract_log_args(log_line: str, log_call_pattern: re.Pattern) -> str:
    """从日志调用行中提取括号内的参数部分。"""
    m = log_call_pattern.search(log_line)
    if not m:
        return ""
    start = m.end() - 1  # '(' 的位置
    depth = 0
    for i in range(start, len(log_line)):
        if log_line[i] == '(':
            depth += 1
        elif log_line[i] == ')':
            depth -= 1
            if depth == 0:
                return log_line[start + 1:i]
    return log_line[start + 1:]


def detect_violations(
    line: str,
    dicts: SensitiveDicts,
    log_call_pattern: re.Pattern,
    *,
    detect_system_out: bool = False,
    detect_print_stack_trace: bool = False,
) -> list[str]:
    """检测单行（已拼接多行）日志的违规，返回违规标签列表。

    标签格式: JSON_LOG / SENSITIVE:<word> / LOW:<word> / SYSOUT / STACK_TRACE
    """
    violations: list[str] = []

    args = extract_log_args(line, log_call_pattern)
    is_log_call = bool(args)
    if not is_log_call:
        # 非日志调用：仅在开启可选检测时才可能违规
        if detect_system_out and SYSTEM_OUT_PATTERN.search(line):
            violations.append("SYSOUT")
        if detect_print_stack_trace and PRINT_STACK_TRACE_PATTERN.search(line):
            violations.append("STACK_TRACE")
        return violations

    # 检测 1: 对象转 JSON 后打印
    if JSON_CONVERSION_PATTERN.search(args):
        violations.append("JSON_LOG")

    # 检测 2: 敏感词（分层词典）
    # 仅从日志参数中提取标识符，排除字符串常量（避免字符串中的单词被误判）
    args_without_strings = STRING_LITERAL_PATTERN.sub('', args)
    all_identifiers = set(m.group(1) for m in IDENTIFIER_PATTERN.finditer(args_without_strings))

    core_hit: str | None = None
    low_hit: str | None = None
    for ident in all_identifiers:
        words = set(split_field_name(ident))
        result = dicts.classify(words, ident)
        if result is None:
            continue
        level, word = result
        if level in ("blacklist", "core"):
            core_hit = word
            break
        if low_hit is None:
            low_hit = word

    if core_hit is not None:
        violations.append(f"SENSITIVE:{core_hit}")
    elif low_hit is not None:
        violations.append(f"LOW:{low_hit}")

    # 多词敏感短语（如 "bank account"），在行级（排除字符串常量）检查
    line_without_strings = STRING_LITERAL_PATTERN.sub('', line).lower()
    for phrase in dicts.core:
        if ' ' in phrase and phrase in line_without_strings:
            if not any(t.startswith("SENSITIVE:") for t in violations):
                violations.append(f"SENSITIVE:{phrase}")
            break

    if detect_system_out and SYSTEM_OUT_PATTERN.search(line):
        violations.append("SYSOUT")
    if detect_print_stack_trace and PRINT_STACK_TRACE_PATTERN.search(line):
        violations.append("STACK_TRACE")

    return violations


def scan_java_file(
    filepath: str,
    dicts: SensitiveDicts,
    log_call_pattern: re.Pattern,
    *,
    detect_system_out: bool = False,
    detect_print_stack_trace: bool = False,
    on_error=None,
) -> tuple[list[str], list[str]]:
    """扫描单个 Java 文件，返回 (违规结果列表, 合规结果列表)。

    on_error: 可选回调 (文件路径, 异常)，读取失败时调用（失败文件统计）。
    """
    violation_results: list[str] = []
    ok_results: list[str] = []
    try:
        # errors='replace' 而非 'ignore': 非 UTF-8 字节以替换符(\ufffd)保留，
        # 避免静默丢字节把敏感词截断导致漏检
        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
    except Exception as e:
        print(f"# 无法读取文件: {filepath} ({e})", file=sys.stderr)
        if on_error is not None:
            on_error(filepath, e)
        return violation_results, ok_results

    in_block_comment = False
    i = 0
    while i < len(lines):
        line = lines[i]
        line_no = i + 1
        stripped = line.strip()

        if in_block_comment:
            if '*/' in stripped:
                in_block_comment = False
            i += 1
            continue
        if stripped.startswith('/*'):
            if '*/' not in stripped:
                in_block_comment = True
            i += 1
            continue
        if stripped.startswith('//'):
            i += 1
            continue

        is_candidate = bool(log_call_pattern.search(line))
        if not is_candidate:
            # 可选检测项需要扫描非日志调用行
            if detect_system_out and SYSTEM_OUT_PATTERN.search(line):
                is_candidate = True
            elif detect_print_stack_trace and PRINT_STACK_TRACE_PATTERN.search(line):
                is_candidate = True
        if not is_candidate:
            i += 1
            continue

        # 多行日志支持：如果括号未闭合，拼接后续行
        full_line = line
        while not is_parentheses_closed(full_line) and i + 1 < len(lines):
            i += 1
            next_line = lines[i]
            next_stripped = next_line.strip()
            if next_stripped.startswith('//'):
                continue
            full_line += next_line

        violations = detect_violations(
            full_line, dicts, log_call_pattern,
            detect_system_out=detect_system_out,
            detect_print_stack_trace=detect_print_stack_trace,
        )
        abs_path = os.path.abspath(filepath)
        if violations:
            tags = ",".join(violations)
            violation_results.append(f"{abs_path}#{line_no} [{tags}] {line.rstrip()}")
        else:
            ok_results.append(f"{abs_path}#{line_no}: {line.rstrip()}")

        i += 1

    return violation_results, ok_results


def scan_files(
    files: list[str],
    dicts: SensitiveDicts,
    log_call_pattern: re.Pattern,
    on_error=None,
    **detect_opts,
) -> tuple[list[str], list[str]]:
    """扫描给定 Java 文件列表，返回 (违规结果列表, 合规结果列表)。"""
    all_violations: list[str] = []
    all_ok: list[str] = []
    for fp in files:
        violations, ok = scan_java_file(fp, dicts, log_call_pattern, on_error=on_error, **detect_opts)
        all_violations.extend(violations)
        all_ok.extend(ok)
    return all_violations, all_ok


def collect_java_files(root_dir: str) -> list[str]:
    """递归收集目录下所有 Java 文件。"""
    collected: list[str] = []
    for dirpath, _, filenames in os.walk(root_dir):
        for fn in filenames:
            if fn.endswith('.java'):
                collected.append(os.path.join(dirpath, fn))
    return collected


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="扫描 Java 工程中的日志输出，检查是否合规",
    )
    parser.add_argument(
        "project_root",
        nargs="?",
        default=".",
        help="Java 工程根目录（默认: 当前目录）",
    )
    parser.add_argument(
        "--files",
        type=Path,
        default=None,
        help="仅扫描清单文件中列出的 Java 文件（每行一个路径），替代全量扫描",
    )
    parser.add_argument(
        "-o", "--output-dir",
        type=str,
        default=None,
        help="审查产物输出目录（默认: 环境变量 SLR_OUTPUT_DIR 或系统临时目录）",
    )
    parser.add_argument(
        "--run-id",
        type=str,
        default=None,
        help="并行运行隔离子目录名（CI 场景）",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_arguments()

    # 扫描目标集合
    if args.files is not None:
        if not args.files.is_file():
            print(f"错误: 文件清单不存在: {args.files}", file=sys.stderr)
            return 2
        files = [
            line.strip() for line in args.files.read_text(encoding="utf-8", errors="replace").splitlines()
            if line.strip() and line.strip().endswith(".java")
        ]
        scan_desc = f"文件清单 {args.files}（{len(files)} 个文件）"
    else:
        root_dir = args.project_root
        if not os.path.isdir(root_dir):
            print(f"错误: 目录不存在 {root_dir}", file=sys.stderr)
            return 2
        files = collect_java_files(root_dir)
        scan_desc = f"目录 {root_dir}（{len(files)} 个文件）"

    config = load_scanner_config()
    log_call_pattern = build_log_call_pattern(config["logger_names"])
    detect_opts = {
        "detect_system_out": bool(config.get("detect_system_out")),
        "detect_print_stack_trace": bool(config.get("detect_print_stack_trace")),
    }

    dicts = load_sensitive_dicts()
    out_dir = get_output_dir(args.output_dir, args.run_id)
    # 失败文件记录器（步骤标识: log_scan）
    recorder = FailureRecorder("log_scan", out_dir)
    print(f"# 已加载敏感词: core={len(dicts.core)} extended={len(dicts.extended)} "
          f"blacklist={len(dicts.blacklist)} whitelist={len(dicts.whitelist)}", file=sys.stderr)
    print(f"# 日志变量名: {config['logger_names']}", file=sys.stderr)
    print(f"# 扫描范围: {scan_desc}", file=sys.stderr)

    violation_results, ok_results = scan_files(
        files, dicts, log_call_pattern, on_error=recorder.record, **detect_opts)
    # 写出失败文件分片（无失败时清理旧分片）
    recorder.flush()

    ok_file = artifact(out_dir, LOG_OK)
    violation_file = artifact(out_dir, LOG_VIOLATION)
    try:
        with open(ok_file, 'w', encoding='utf-8') as f:
            for r in ok_results:
                f.write(r + '\n')
        with open(violation_file, 'w', encoding='utf-8') as f:
            for r in violation_results:
                f.write(r + '\n')
        total = len(violation_results) + len(ok_results)
        print(f"# 扫描完成，共 {total} 条日志调用", file=sys.stderr)
        print(f"#   合规: {len(ok_results)} 条 -> {ok_file}", file=sys.stderr)
        print(f"#   违规: {len(violation_results)} 条 -> {violation_file}", file=sys.stderr)
    except Exception as e:
        print(f"# 写入文件失败: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n操作已取消。", file=sys.stderr)
        raise SystemExit(130)
    except BrokenPipeError:
        try:
            sys.stdout.close()
        finally:
            raise SystemExit(0)
    except SystemExit:
        raise
    except Exception as e:
        print(f"错误: 发生未预期的异常: {e}", file=sys.stderr)
        raise SystemExit(2)
