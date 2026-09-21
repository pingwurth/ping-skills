#!/usr/bin/env python3
"""Java @ToString 注解审查工具（公共模块版）。

检查 POJO 类是否使用 @ToString(of = {...}) 注解:
  1. 未使用 @ToString 注解 -> 不通过
  2. 使用了 @ToString 但未定义 of 属性 -> 不通过
  3. 支持全限定注解名（@lombok.ToString），排除 @ToStringBuilder 等
  4. 检查 @ToString(of = {...}) 中的字段是否包含敏感词

检查范围（P2-3）:
  - class:     检查
  - interface: 跳过（无实例字段）
  - enum:      跳过
  - record:    默认跳过；--record-policy warn 时降级为提示
  - 匿名类:    不检查（无法匹配命名声明）

输出:
  <输出目录>/miss-tostring-annotation.list  格式: {path}#{line}: {reason}
  <输出目录>/tostring-sensitive-violations.list  格式: {path}#{line}: @ToString(of={{...}}) 中的字段 '{field}' 命中敏感词 '{word}'（级别: {level}）

退出码:
  0: 成功
  2: 执行错误（目录不存在、写文件失败等）
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common.dictionary import load_sensitive_dicts  # noqa: E402
from common.failures import FailureRecorder  # noqa: E402
from common.java_lexer import check_tostring_annotation, iter_classes  # noqa: E402
from common.paths import TOSTRING_MISSING, TOSTRING_SENSITIVE_VIOLATIONS, artifact, get_output_dir  # noqa: E402
from common.pojo_config import find_java_files, load_pojo_config  # noqa: E402

POJO_CONFIG_FILE = Path(__file__).resolve().parent / "rules" / "pojo.config"


def extract_tostring_of_fields(annotations: list[str], original_content: str = "") -> list[str]:
    """从注解列表中提取 @ToString(of = {...}) 中的字段名。
    
    Args:
        annotations: 注解列表（可能被屏蔽）
        original_content: 原始文件内容（用于提取未屏蔽的字段名）
    
    Returns:
        字段名列表，如果未找到 @ToString(of = {...}) 则返回空列表。
    """
    # 首先尝试从原始内容中提取
    if original_content:
        # 查找 @ToString(of = {...}) 模式
        tostring_match = re.search(r"@(?:\w+\.)*ToString\s*\(\s*of\s*=\s*\{([^}]*)\}", original_content)
        if tostring_match:
            fields_str = tostring_match.group(1)
            fields = []
            # 匹配 "fieldName" 或 'fieldName' 或 fieldName
            for field_match in re.finditer(r"[\"']([^\"']+)[\"']|([^\s,]+)", fields_str):
                if field_match.group(1) is not None:
                    fields.append(field_match.group(1))
                elif field_match.group(2) is not None:
                    fields.append(field_match.group(2))
            return fields
    
    # 如果原始内容提取失败，尝试从注解中提取（可能被屏蔽）
    for annotation in annotations:
        # 匹配 @ToString 或 @lombok.ToString
        match = re.match(r"@(?:\w+\.)*ToString\b", annotation)
        if not match:
            continue
        
        # 提取 of = {...} 部分
        of_match = re.search(r"\bof\s*=\s*\{([^}]*)\}", annotation)
        if not of_match:
            continue
        
        # 提取字段名（支持双引号和单引号）
        fields_str = of_match.group(1)
        fields = []
        # 匹配 "fieldName" 或 'fieldName' 或 fieldName
        for field_match in re.finditer(r"[\"']([^\"']+)[\"']|([^\s,]+)", fields_str):
            if field_match.group(1) is not None:
                fields.append(field_match.group(1))
            elif field_match.group(2) is not None:
                fields.append(field_match.group(2))
        return fields
    return []


def check_file(path: Path, record_policy: str, sensitive_dicts=None, on_error=None) -> tuple[list[str], list[str]]:
    """检查单个 Java 文件，返回违规记录列表。

    on_error: 可选回调 (文件路径, 异常)，读取失败时调用（失败文件统计）。
    
    Returns:
        (tostring_missing_violations, sensitive_violations) 元组
    """
    tostring_violations: list[str] = []
    sensitive_violations: list[str] = []
    
    # 读取原始文件内容用于提取字段名
    try:
        original_content = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        original_content = ""
    
    for line_number, _class_name, annotations, kind in iter_classes(path, on_error=on_error):
        if kind in ("interface", "enum"):
            continue
        if kind == "record" and record_policy == "skip":
            continue

        has_tostring_of, reason = check_tostring_annotation(annotations)
        if has_tostring_of:
            # 检查 @ToString(of = {...}) 中的字段是否包含敏感词
            if sensitive_dicts is not None:
                fields = extract_tostring_of_fields(annotations, original_content)
                for field_name in fields:
                    # 将字段名拆分为单词（驼峰命名拆分）
                    words = set(re.findall(r'[a-z]+', field_name.lower()))
                    classification = sensitive_dicts.classify(words, field_name)
                    if classification:
                        level, hit_word = classification
                        sensitive_violations.append(
                            f"{path}#{line_number}: @ToString(of={{...}}) 中的字段 '{field_name}' "
                            f"命中敏感词 '{hit_word}'（级别: {level}）"
                        )
            continue
        
        if kind == "record":
            # record_policy == "warn"：降级提示，原因中标注类型
            tostring_violations.append(f"{path}#{line_number}: {reason} (record)")
        else:
            tostring_violations.append(f"{path}#{line_number}: {reason}")
    
    return tostring_violations, sensitive_violations


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="检查 Java POJO 类是否使用 @ToString(of = {...}) 注解",
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
        help="仅检查清单文件中列出的 Java 文件（每行一个路径），替代全量扫描",
    )
    parser.add_argument(
        "--record-policy",
        choices=("skip", "warn"),
        default="skip",
        help="record 类型检查策略: skip=跳过（默认）, warn=降级为提示",
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

    if args.files is not None:
        if not args.files.is_file():
            print(f"错误: 文件清单不存在: {args.files}", file=sys.stderr)
            return 2
        files = [
            Path(line.strip())
            for line in args.files.read_text(encoding="utf-8", errors="replace").splitlines()
            if line.strip() and line.strip().endswith(".java")
        ]
        scan_desc = f"文件清单 {args.files}（{len(files)} 个文件）"
    else:
        project_root = args.project_root.resolve()
        if not project_root.is_dir():
            print(f"错误: 目录不存在 {project_root}", file=sys.stderr)
            return 2
        directories, suffixes = load_pojo_config(POJO_CONFIG_FILE)
        files = find_java_files(project_root, directories, suffixes)
        scan_desc = f"目录 {project_root}（{len(files)} 个文件）"

    print(f"# 扫描范围: {scan_desc}", file=sys.stderr)
    print(f"# record 策略: {args.record_policy}（interface/enum 一律跳过）", file=sys.stderr)

    out_dir = get_output_dir(args.output_dir, args.run_id)
    # 失败文件记录器（步骤标识: tostring）
    recorder = FailureRecorder("tostring", out_dir)

    # 加载敏感词典
    sensitive_dicts = load_sensitive_dicts()
    print(f"# 已加载敏感词典: core={len(sensitive_dicts.core)} extended={len(sensitive_dicts.extended)} blacklist={len(sensitive_dicts.blacklist)}", file=sys.stderr)

    tostring_results: list[str] = []
    sensitive_results: list[str] = []
    for path in files:
        try:
            tostring_violations, sensitive_violations = check_file(
                path, args.record_policy, sensitive_dicts=sensitive_dicts, on_error=recorder.record
            )
            tostring_results.extend(tostring_violations)
            sensitive_results.extend(sensitive_violations)
        except Exception as exc:
            print(f"警告: 处理文件失败 {path}: {exc}", file=sys.stderr)
            recorder.record(path, exc)

    # 写出失败文件分片（无失败时清理旧分片）
    recorder.flush()

    # 写出 @ToString 缺失违规
    output_file = artifact(out_dir, TOSTRING_MISSING)
    try:
        with open(output_file, "w", encoding="utf-8") as f:
            for line in tostring_results:
                f.write(line + "\n")
    except OSError as exc:
        print(f"错误: 写入文件失败 {output_file}: {exc}", file=sys.stderr)
        return 2

    # 写出敏感词违规
    sensitive_output_file = artifact(out_dir, TOSTRING_SENSITIVE_VIOLATIONS)
    try:
        with open(sensitive_output_file, "w", encoding="utf-8") as f:
            for line in sensitive_results:
                f.write(line + "\n")
    except OSError as exc:
        print(f"错误: 写入文件失败 {sensitive_output_file}: {exc}", file=sys.stderr)
        return 2

    print(f"# 检查完成: {len(tostring_results)} 个类缺少 @ToString(of = ...) -> {output_file}", file=sys.stderr)
    print(f"# 敏感词违规: {len(sensitive_results)} 个字段命中敏感词 -> {sensitive_output_file}", file=sys.stderr)
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
