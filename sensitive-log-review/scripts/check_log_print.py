#!/usr/bin/env python3
"""日志打印变更检查工具（公共模块版）。

检查当前分支与目标分支之间的变更文件，是否命中扫描产物:
  - log-print-ok.list        合规日志（仅展示，不影响退出码）
  - log-print-violation.list 违规日志（tags 格式: {path}#{line} [TAGS] {内容}）

判定规则（P1-3 + P2-2）:
  1. 仅保留在当前分支 git diff 新增行中出现的内容
  2. 退出码只看违规清单：合规清单匹配不影响退出码
  3. tags 全部为 LOW:* 的违规默认不触发门禁（低置信提示），
     --fail-on-low 开启后同样触发
  4. 旧格式（无 tags）违规行按常规违规处理

stdout 末尾输出机器可读摘要行，供 main.py 解析:
  SUMMARY: violation=<n> low=<n> ok=<n>

退出码:
  0: 未发现触发门禁的违规
  1: 发现触发门禁的违规
  2: 执行错误（非 git 仓库、产物缺失等）
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common.git_utils import AddedLinesCache, get_changed_files, resolve_repo, validate_ref  # noqa: E402
from common.failures import FailureRecorder  # noqa: E402
from common.paths import LOG_OK, LOG_VIOLATION, artifact, get_output_dir  # noqa: E402
from common.text import normalize_for_matching, parse_list_line  # noqa: E402


def is_low_only(tags: list[str]) -> bool:
    """tags 非空且全部为 LOW:* 时视为低置信提示。"""
    return bool(tags) and all(t.startswith("LOW:") for t in tags)


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


def match_changed_files(
    changed_files: list[str],
    list_lines: list[str],
) -> list[str]:
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
    """仅保留内容出现在 git diff 新增行中的清单行。"""
    filtered: list[str] = []
    for line in lines:
        parsed = parse_list_line(line)
        if parsed is None:
            if verbose:
                print(f"警告: 无法解析清单行: {line}", file=sys.stderr)
            continue
        file_path, _line_no, _tags, content = parsed
        added = cache.get(file_path)
        if content and content in added:
            filtered.append(line)
        elif verbose:
            print(f"过滤: 内容未在新增行中找到: {line}", file=sys.stderr)
    return filtered


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="检查代码变更文件是否命中日志打印合规/违规清单",
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
        help="低置信（LOW:*）违规同样触发门禁（默认仅展示不触发）",
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

    if not changed_files:
        print("未发现变更文件。")
        print("SUMMARY: violation=0 low=0 ok=0")
        # 无记录时 flush 会清理旧分片，保证独立 CLI 重复运行幂等
        FailureRecorder("log_print", out_dir).flush()
        return 0

    if args.verbose:
        print(f"发现 {len(changed_files)} 个变更文件")

    # 第二步：加载清单并匹配变更文件（清单读取失败计入失败文件统计）
    recorder = FailureRecorder("log_print", out_dir)
    ok_lines = match_changed_files(changed_files, load_list_lines(artifact(out_dir, LOG_OK), recorder))
    violation_lines = match_changed_files(changed_files, load_list_lines(artifact(out_dir, LOG_VIOLATION), recorder))
    recorder.flush()

    # 第三步：git diff 新增行过滤
    cache = AddedLinesCache(repo, branch)
    filtered_ok = filter_by_git_diff(ok_lines, cache, args.verbose)
    filtered_violation = filter_by_git_diff(violation_lines, cache, args.verbose)

    # 第四步：区分常规违规与低置信（LOW）违规
    blocking: list[str] = []
    low: list[str] = []
    for line in filtered_violation:
        parsed = parse_list_line(line)
        if parsed is not None and is_low_only(parsed[2]):
            low.append(line)
        else:
            blocking.append(line)

    # 展示（合规清单仅展示，不影响退出码）
    if filtered_ok:
        print("---------------------------------------")
        print(f"合规日志（仅展示）: {len(filtered_ok)} 条")
        print("---------------------------------------")
        for line in filtered_ok:
            print(f"  {line}")
        print()

    if blocking:
        print("---------------------------------------")
        print(f"违规日志: {len(blocking)} 条")
        print("---------------------------------------")
        for line in blocking:
            print(f"  {line}")
        print()

    if low:
        print("---------------------------------------")
        print(f"低置信提示（LOW，默认不触发门禁）: {len(low)} 条")
        print("---------------------------------------")
        for line in low:
            print(f"  {line}")
        print()

    print(f"SUMMARY: violation={len(blocking)} low={len(low)} ok={len(filtered_ok)}")

    gate_triggered = bool(blocking) or (args.fail_on_low and bool(low))
    if not gate_triggered and not filtered_ok:
        print("No log print issues found in changed files.")
    return 1 if gate_triggered else 0


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
