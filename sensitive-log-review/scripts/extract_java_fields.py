#!/usr/bin/env python3
"""Java 字段提取与审计工具（公共模块版）。

功能:
  1. 提取 POJO 文件字段   -> all-fields.list        格式: {path}#{line}: {field}
  2. 命名规范审计         -> unqualified.fields      格式: {path}#{line}: {field}
  3. 敏感字段审计(分层)   -> maybe-sensitive.fields  格式: {path}#{line} [{C|E}:{hit}] {field}
     - C: 高置信敏感（blacklist/core 层命中）
     - E: 低置信提示（extended 层命中，默认不触发门禁）

POJO 判定:
  统一由 common.pojo_config 实现（src/main/java 范围内 + 目录名/文件名后缀命中），
  配置见 rules/pojo.config，缺失时回退默认值。

扫描范围:
  默认全量扫描项目根目录；--files <清单> 时仅扫描清单中的 Java 文件
  （调用方负责保证清单内为 POJO 文件）。

退出码:
  0: 成功
  2: 执行错误（目录不存在、写文件失败等）
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common.dictionary import (  # noqa: E402
    SensitiveDicts,
    load_en_us_dict,
    load_en_us_whitelist,
    load_sensitive_dicts,
)
from common.failures import FailureRecorder  # noqa: E402
from common.java_lexer import iter_fields  # noqa: E402
from common.paths import (  # noqa: E402
    ALL_FIELDS,
    MAYBE_SENSITIVE_FIELDS,
    UNQUALIFIED_FIELDS,
    artifact,
    get_output_dir,
)
from common.pojo_config import find_java_files, load_pojo_config  # noqa: E402
from common.text import split_field_name  # noqa: E402

POJO_CONFIG_FILE = Path(__file__).resolve().parent / "rules" / "pojo.config"


def audit_field(
    field_name: str,
    en_us_dict: set[str],
    en_us_whitelist: set[str],
    dicts: SensitiveDicts,
) -> tuple[bool, str | None, str | None]:
    """审计单个字段的命名规范性与敏感性。

    判定逻辑:
      1. 命名规范: 拆分后的全部单词在英文词典或技术缩写白名单中 → 合格
      2. 敏感性: SensitiveDicts.classify（whitelist > blacklist > core > extended）

    Returns:
        (is_qualified, level, hit_word)
        - is_qualified: 命名是否合格
        - level: None（不敏感）| "C"（高置信）| "E"（低置信）
        - hit_word: 命中的敏感词（level 为 None 时为 None）
    """
    words = split_field_name(field_name)
    if not words:
        return False, None, None

    is_qualified = all(word in en_us_dict or word in en_us_whitelist for word in words)

    result = dicts.classify(set(words), field_name)
    if result is None:
        return is_qualified, None, None
    level_name, hit = result
    level = "C" if level_name in ("blacklist", "core") else "E"
    return is_qualified, level, hit


def collect_target_files(args: argparse.Namespace) -> tuple[list[Path], str]:
    """确定扫描目标文件集合，返回 (文件列表, 描述)。"""
    if args.files is not None:
        if not args.files.is_file():
            print(f"错误: 文件清单不存在: {args.files}", file=sys.stderr)
            raise SystemExit(2)
        files = [
            Path(line.strip())
            for line in args.files.read_text(encoding="utf-8", errors="replace").splitlines()
            if line.strip() and line.strip().endswith(".java")
        ]
        return files, f"文件清单 {args.files}（{len(files)} 个文件）"

    project_root = args.project_root.resolve()
    if not project_root.is_dir():
        print(f"错误: 目录不存在 {project_root}", file=sys.stderr)
        raise SystemExit(2)
    directories, suffixes = load_pojo_config(POJO_CONFIG_FILE)
    files = find_java_files(project_root, directories, suffixes)
    return files, f"目录 {project_root}（{len(files)} 个文件）"


def write_lines(file_path: Path, lines: list[str]) -> None:
    """写产物文件，失败时抛 SystemExit(2)。"""
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            for line in lines:
                f.write(line + "\n")
    except OSError as exc:
        print(f"错误: 写入文件失败 {file_path}: {exc}", file=sys.stderr)
        raise SystemExit(2)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="提取 Java POJO 文件字段并做命名规范/敏感性审计",
    )
    parser.add_argument(
        "project_root",
        nargs="?",
        type=Path,
        default=Path.cwd(),
        help="项目根目录（默认: 当前工作目录）",
    )
    parser.add_argument(
        "--files",
        type=Path,
        default=None,
        help="仅处理清单文件中列出的 Java 文件（每行一个路径），替代全量扫描",
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

    files, scan_desc = collect_target_files(args)
    print(f"# 扫描范围: {scan_desc}", file=sys.stderr)

    # 加载词典
    en_us_dict = load_en_us_dict()
    en_us_whitelist = load_en_us_whitelist()
    dicts = load_sensitive_dicts()
    if not en_us_dict:
        print("警告: 英文词典不可用，命名规范审计退化为全部不合格", file=sys.stderr)
    print(f"# 已加载词典: en_US={len(en_us_dict)} 缩写白名单={len(en_us_whitelist)} "
          f"core={len(dicts.core)} extended={len(dicts.extended)}", file=sys.stderr)

    all_fields: list[str] = []
    unqualified_fields: list[str] = []
    sensitive_fields: list[str] = []

    out_dir = get_output_dir(args.output_dir, args.run_id)
    # 失败文件记录器（步骤标识: extract_fields）
    recorder = FailureRecorder("extract_fields", out_dir)

    for path in files:
        try:
            for line_number, field_name in iter_fields(path, on_error=recorder.record):
                all_fields.append(f"{path}#{line_number}: {field_name}")

                is_qualified, level, hit = audit_field(field_name, en_us_dict, en_us_whitelist, dicts)
                if not is_qualified:
                    unqualified_fields.append(f"{path}#{line_number}: {field_name}")
                if level is not None:
                    sensitive_fields.append(f"{path}#{line_number} [{level}:{hit}] {field_name}")
        except Exception as exc:  # 单文件解析异常不中断整体扫描
            print(f"警告: 处理文件失败 {path}: {exc}", file=sys.stderr)
            recorder.record(path, exc)

    # 写出失败文件分片（无失败时清理旧分片）
    recorder.flush()

    all_fields_file = artifact(out_dir, ALL_FIELDS)
    unqualified_file = artifact(out_dir, UNQUALIFIED_FIELDS)
    sensitive_file = artifact(out_dir, MAYBE_SENSITIVE_FIELDS)

    write_lines(all_fields_file, all_fields)
    write_lines(unqualified_file, unqualified_fields)
    write_lines(sensitive_file, sensitive_fields)

    print("# 提取与审计完成:", file=sys.stderr)
    print(f"#   全部字段:     {len(all_fields)} -> {all_fields_file}", file=sys.stderr)
    print(f"#   不合格字段:   {len(unqualified_fields)} -> {unqualified_file}", file=sys.stderr)
    print(f"#   可能敏感字段: {len(sensitive_fields)} -> {sensitive_file}", file=sys.stderr)
    return 0


if __name__ == "__main__":
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
