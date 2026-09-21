#!/usr/bin/env python3
"""敏感/不合格字段变更检查工具（公共模块版）。

检查当前分支与目标分支之间的变更文件，是否命名字段审计产物:
  - maybe-sensitive.fields   敏感字段（tags: C=高置信, E=低置信）
  - unqualified.fields       命名不合格字段

判定规则:
  1. 仅保留字段名出现在当前分支 git diff 新增行中的记录
  2. [E:*] 低置信敏感默认不触发门禁（--fail-on-low 开启后触发）
  3. 结果写入 sensitive.results / unqualified.results（[LOW] 前缀行计入前者）

stdout 末尾输出机器可读摘要行，供 main.py 解析:
  SUMMARY: sensitive=<n> low=<n> unqualified=<n>

退出码:
  0: 未发现问题
  1: 发现敏感或不合格内容
  2: 执行错误（非 git 仓库、git 执行失败等）
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common.git_utils import AddedLinesCache, get_changed_files, resolve_repo, validate_ref  # noqa: E402
from common.failures import FailureRecorder  # noqa: E402
from common.paths import (  # noqa: E402
    MAYBE_SENSITIVE_FIELDS,
    SENSITIVE_RESULTS,
    UNQUALIFIED_FIELDS,
    UNQUALIFIED_RESULTS,
    artifact,
    get_output_dir,
)
from common.text import normalize_for_matching, parse_list_line  # noqa: E402


def load_list_lines(file_path: Path, recorder: FailureRecorder | None = None) -> list[str]:
    """加载清单文件全部非空行；文件不存在返回空列表并告警。"""
    if not file_path.is_file():
        print(f"警告: 清单文件不存在: {file_path}", file=sys.stderr)
        return []
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]
    except OSError as exc:
        print(f"错误: 读取清单失败 {file_path}: {exc}", file=sys.stderr)
        if recorder is not None:
            recorder.record(file_path, exc)
        return []


def match_changed_files(changed_files: list[str], list_lines: list[str]) -> list[str]:
    """找出路径命中变更文件集合的清单行(子串匹配, 路径分隔符统一规范化)。"""
    matched: list[str] = []
    # 清单行规范化只做一次, 避免 变更文件数x清单行数 量级的重复 replace
    normalized_lines = [(line, normalize_for_matching(line)) for line in list_lines]
    for file_path in changed_files:
        normalized_file = normalize_for_matching(file_path)
        for line, normalized_line in normalized_lines:
            if normalized_file in normalized_line:
                matched.append(line)
    return matched


def filter_by_git_diff(
    lines: list[str],
    cache: AddedLinesCache,
    verbose: bool = False,
) -> list[str]:
    """仅保留字段名出现在 git diff 新增行中的清单行。"""
    filtered: list[str] = []
    for line in lines:
        parsed = parse_list_line(line)
        if parsed is None:
            if verbose:
                print(f"警告: 无法解析清单行: {line}", file=sys.stderr)
            continue
        file_path, _line_no, _tags, field_name = parsed
        added = cache.get(file_path)
        if field_name and field_name in added:
            filtered.append(line)
        elif verbose:
            print(f"过滤: 字段未在新增行中找到: {line}", file=sys.stderr)
    return filtered


def is_low_confidence(line: str) -> bool:
    """判断敏感字段清单行是否为低置信（tags 全部为 E:*）。"""
    parsed = parse_list_line(line)
    if parsed is None:
        return False
    tags = parsed[2]
    return bool(tags) and all(t.startswith("E:") for t in tags)


def write_results(file_path: Path, lines: list[str]) -> None:
    """写结果文件，失败时抛 SystemExit(2)。"""
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            for line in lines:
                f.write(line + "\n")
    except OSError as exc:
        print(f"错误: 写入文件失败 {file_path}: {exc}", file=sys.stderr)
        raise SystemExit(2)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="检查代码变更文件是否包含敏感或不合格字段",
    )
    parser.add_argument(
        "--repo",
        type=str,
        default=None,
        help="git 仓库目录（默认: 当前目录）",
    )
    parser.add_argument(
        "-b", "--branch",
        type=str,
        default="master",
        help="目标分支名称（默认: master）",
    )
    parser.add_argument(
        "--fail-on-low",
        action="store_true",
        default=False,
        help="低置信（E 级）敏感字段同样触发门禁（默认仅展示不触发）",
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
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        default=False,
        help="启用详细输出模式",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_arguments()

    try:
        repo = resolve_repo(args.repo)
        branch = validate_ref(args.branch)
        out_dir = get_output_dir(args.output_dir, args.run_id)
    except (SystemExit, ValueError) as exc:
        if isinstance(exc, ValueError):
            print(f"错误: {exc}", file=sys.stderr)
            return 2
        raise

    if args.verbose:
        print(f"仓库: {repo}")
        print(f"正在检查与分支 '{branch}' 的差异...")

    # 第一步：获取变更文件列表
    try:
        changed_files = get_changed_files(repo, branch)
    except ValueError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as exc:
        print(f"错误: git diff 执行失败: {exc.stderr.strip()}", file=sys.stderr)
        return 2

    sensitive_output = artifact(out_dir, SENSITIVE_RESULTS)
    unqualified_output = artifact(out_dir, UNQUALIFIED_RESULTS)

    if not changed_files:
        print("未发现变更文件。")
        write_results(sensitive_output, [])
        write_results(unqualified_output, [])
        print("SUMMARY: sensitive=0 low=0 unqualified=0")
        # 无记录时 flush 会清理旧分片，保证独立 CLI 重复运行幂等
        FailureRecorder("sensitive_word", out_dir).flush()
        return 0

    if args.verbose:
        print(f"发现 {len(changed_files)} 个变更文件")

    # 第二步：加载清单并匹配变更文件（清单读取失败计入失败文件统计）
    recorder = FailureRecorder("sensitive_word", out_dir)
    sensitive_lines = match_changed_files(
        changed_files, load_list_lines(artifact(out_dir, MAYBE_SENSITIVE_FIELDS), recorder))
    unqualified_lines = match_changed_files(
        changed_files, load_list_lines(artifact(out_dir, UNQUALIFIED_FIELDS), recorder))
    recorder.flush()

    # 第三步：git diff 新增行过滤（AddedLinesCache 缓存，每文件一次 git 进程）
    cache = AddedLinesCache(repo, branch)
    filtered_sensitive = filter_by_git_diff(sensitive_lines, cache, args.verbose)
    filtered_unqualified = filter_by_git_diff(unqualified_lines, cache, args.verbose)

    # 第四步：拆分高/低置信敏感
    blocking = [line for line in filtered_sensitive if not is_low_confidence(line)]
    low = [line for line in filtered_sensitive if is_low_confidence(line)]

    # 第五步：写结果文件
    write_results(sensitive_output,
                  [f"[SENSITIVE] {line}" for line in blocking]
                  + [f"[LOW] {line}" for line in low])
    write_results(unqualified_output, [f"[UNQUALIFIED] {line}" for line in filtered_unqualified])

    # 第六步：展示
    if blocking:
        print(f"敏感字段（高置信）: {len(blocking)} 条")
        for line in blocking:
            print(f"  [SENSITIVE] {line}")
        print()
    if low:
        print(f"敏感字段（低置信，默认不触发门禁）: {len(low)} 条")
        for line in low:
            print(f"  [LOW] {line}")
        print()
    if filtered_unqualified:
        print(f"不合格字段: {len(filtered_unqualified)} 条")
        for line in filtered_unqualified:
            print(f"  [UNQUALIFIED] {line}")
        print()
    if not blocking and not low and not filtered_unqualified:
        print("No issues found in changed files.")

    print(f"SUMMARY: sensitive={len(blocking)} low={len(low)} unqualified={len(filtered_unqualified)}")

    issue_found = bool(blocking) or bool(filtered_unqualified) or (args.fail_on_low and bool(low))
    return 1 if issue_found else 0


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
