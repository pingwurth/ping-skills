#!/usr/bin/env python3
"""POJO 字段敏感性深度分析（P0-4）。

读取 pojo-changed.list 中的变更 POJO 文件，复用 common.java_lexer 的
词法分析器提取字段（支持多行声明/record 组件/枚举常量），对照
rules/sensitive-field-rules.json 中的结构化规则（源自 JR/T 0171-2020
敏感数据分类）判定敏感级别，输出 analyze-sensitize.result。

输出格式（与 SKILL.md 文档一致）：
    {file_path}#{field_name}: {reason} (line {line_no})

设计说明：
    本脚本完成"规则初筛"层；更进一步的语义复核（如结合字段注释判断）
    由 AI 调用方在 Skill 编排层完成，见 SKILL.md。

退出码：
    0: 正常完成（无论是否发现敏感字段）
    2: 执行错误（规则文件缺失/损坏等）
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common.dictionary import load_word_set, WHITELIST_FILE  # noqa: E402
from common.errors import ErrorCode, print_error  # noqa: E402
from common.failures import FailureRecorder  # noqa: E402
from common.java_lexer import iter_fields  # noqa: E402
from common.paths import (  # noqa: E402
    ANALYZE_RESULT,
    POJO_CHANGED,
    artifact,
    get_output_dir,
)

RULES_FILE = Path(__file__).resolve().parent / "rules" / "sensitive-field-rules.json"


def load_rules(rules_path: Path) -> list[dict]:
    """加载结构化敏感字段规则并预编译正则。

    Raises:
        SystemExit(2): 规则文件不存在或 JSON 损坏
    """
    if not rules_path.is_file():
        print(f"错误: 敏感字段规则文件不存在: {rules_path}", file=sys.stderr)
        raise SystemExit(2)
    try:
        rules = json.loads(rules_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        # 结构化错误提示（E004），指出具体文件与 JSON 错误行列位置
        print_error(ErrorCode.E004_RULE_FILE_BROKEN, location=str(rules_path),
                    detail=f"{exc.msg} (行 {exc.lineno} 列 {exc.colno})")
        raise SystemExit(2)

    for rule in rules:
        try:
            rule["_re"] = [re.compile(pat, re.IGNORECASE) for pat in rule["patterns"]]
        except re.error as exc:
            print(f"错误: 规则正则编译失败 {rule.get('category')}: {exc}", file=sys.stderr)
            raise SystemExit(2)
    return rules


def analyze_file(java_file: Path, rules: list[dict], whitelist: set[str],
                 on_error=None) -> list[str]:
    """分析单个 Java 文件的字段敏感性，返回命中结果列表。

    on_error: 可选回调 (文件路径, 异常)，读取/解析失败时调用（失败文件统计）。
    """
    hits: list[str] = []
    try:
        # iter_fields 为惰性生成器，读文件异常在迭代时触发，
        # 因此 for 循环一并纳入 try 保护
        for line_no, field_name in iter_fields(java_file, on_error=on_error):
            # 白名单豁免（评审确认的安全字段名，如 cardType）
            if field_name.lower() in whitelist:
                continue
            lower = field_name.lower()
            for rule in rules:
                if any(rx.search(lower) for rx in rule["_re"]):
                    hits.append(f"{java_file}#{field_name}: {rule['reason']} (line {line_no})")
                    break
    except Exception as exc:  # 词法分析器内部已告警，这里兜底
        print(f"警告: 分析文件失败 {java_file}: {exc}", file=sys.stderr)
        if on_error is not None:
            on_error(java_file, exc)
    return hits


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="POJO 字段敏感性深度分析（规则初筛层）",
    )
    parser.add_argument(
        "-i", "--input-list",
        type=Path,
        default=None,
        help="POJO 变更文件清单路径（默认: 输出目录下的 pojo-changed.list）",
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
    out_dir = get_output_dir(args.output_dir, args.run_id)

    input_list = args.input_list or artifact(out_dir, POJO_CHANGED)
    output_file = artifact(out_dir, ANALYZE_RESULT)

    rules = load_rules(RULES_FILE)
    whitelist = load_word_set(WHITELIST_FILE)
    # 失败文件记录器（步骤标识: analyze）
    recorder = FailureRecorder("analyze", out_dir)

    # 变更清单不存在时产出空结果（属于正常场景：无 POJO 变更）
    if not input_list.is_file():
        if args.verbose:
            print(f"提示: POJO 变更清单不存在: {input_list}，产出空结果", file=sys.stderr)
        output_file.write_text("", encoding="utf-8")
        recorder.flush()
        return 0

    pojo_files = [
        Path(line.strip())
        for line in input_list.read_text(encoding="utf-8", errors="replace").splitlines()
        if line.strip()
    ]

    results: list[str] = []
    for pojo_file in pojo_files:
        if not pojo_file.is_file():
            if args.verbose:
                print(f"警告: 文件不存在，跳过: {pojo_file}", file=sys.stderr)
            continue
        results.extend(analyze_file(pojo_file, rules, whitelist, on_error=recorder.record))

    # 写出失败文件分片（无失败时清理旧分片）
    recorder.flush()

    with open(output_file, "w", encoding="utf-8") as f:
        for result in results:
            f.write(result + "\n")

    print(f"分析完成: 检查 {len(pojo_files)} 个文件，发现 {len(results)} 个可能的敏感字段 -> {output_file}",
          file=sys.stderr)
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
