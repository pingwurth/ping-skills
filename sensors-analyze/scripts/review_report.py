#!/usr/bin/env python3
"""第 5.5 步：脚本预审 sensors.csv，添加 illegal_reason 列。

职责：
  1. 读取 sensors.csv，追加 illegal_reason 列（初始为空）
  2. 结构校验：
     - file 字段指向的文件是否存在
     - 行号范围是否有效
     - 必填字段（page_name/first_biz_id/second_biz_id/first_biz_name/second_biz_name）是否为空
  3. 代码级字面量比对：
     - 读取 file 字段指向的代码片段
     - 提取字符串字面量，与 CSV 字段值比对
     - 标记"字段值在代码中无字面量匹配"或"可从代码字面量推断"
  4. 输出：
     - 覆写 sensors.csv（带 illegal_reason 列）
     - 输出 review_report.txt（预审摘要）

用法：
  python3 scripts/review_report.py --csv sensors.csv --sites call_sites.json --root <target-codebase>
  python3 scripts/review_report.py --csv sensors.csv --sites call_sites.json --root <target-codebase> --out review_report.txt

退出码：
  0 = 无问题（所有 illegal_reason 为空）
  1 = 执行异常
  2 = 有问题需 LLM 复核（有待标记的行）
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

from utils import next_step, setup_logging

LOG = None

# CSV 表头（含 illegal_reason）
CSV_HEADER_WITH_REASON = [
    "page_name", "first_biz_id", "second_biz_id",
    "first_biz_name", "second_biz_name", "event",
    "file", "illegal_reason",
]

# 旧表头（兼容读取无 illegal_reason 列的 CSV）
CSV_HEADER_OLD = [
    "page_name", "first_biz_id", "second_biz_id",
    "first_biz_name", "second_biz_name", "event",
    "file",
]

# 必填非空字段
REQUIRED_NONEMPTY = [
    "page_name", "first_biz_id", "second_biz_id",
    "first_biz_name", "second_biz_name",
]

# 所有检查字段（含 event）
ALL_CHECK_FIELDS = REQUIRED_NONEMPTY + ["event"]

# 解析 file 字段：格式为 "文件名:start,end"
_FILE_LOC_RE = re.compile(r'^(.+):(\d+),(\d+)$')

# 匹配单引号、双引号字符串（排除转义）
_STRING_LITERAL_RE = re.compile(
    r"""(?<!\\)(?:'([^'\\]*(?:\\.[^'\\]*)*)'|"([^"\\]*(?:\\.[^"\\]*)*)")"""
)

# 匹配模板字符串中的静态部分（不含 ${} 表达式）
_TEMPLATE_LITERAL_RE = re.compile(r"`([^`${]*)`")


def setup_logging(log_path: Path) -> logging.Logger:
    """配置日志：所有输出写入 log 文件，stdout 仅保留给 next_step。"""
    logger = logging.getLogger("review_report")
    logger.setLevel(logging.DEBUG)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(str(log_path), encoding="utf-8", mode="w")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(fh)
    sys.stderr = open(str(log_path), "a", encoding="utf-8")
    return logger


def next_step(msg: str) -> None:
    """脚本结束时调用，唯一向 stdout 输出的内容。"""
    print(f"next_step: {msg}", flush=True)


def parse_file_field(raw: str) -> tuple[str | None, int, int]:
    """解析 file 字段，返回 (相对路径, start_line, end_line)。解析失败返回 (None, 0, 0)。"""
    m = _FILE_LOC_RE.match(raw.strip())
    if not m:
        return None, 0, 0
    return m.group(1), int(m.group(2)), int(m.group(3))


def count_file_lines(path: Path) -> int:
    """计算文件总行数。"""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
        return text.count("\n") + 1
    except Exception:
        return 0


def read_code_lines(path: Path, start: int, end: int) -> list[str]:
    """读取文件指定行范围（1-indexed，闭区间）。"""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
        lines = text.split("\n")
        # start/end 是 1-indexed
        a = max(0, start - 1)
        b = min(len(lines), end)
        return lines[a:b]
    except Exception:
        return []


def extract_string_literals(lines: list[str]) -> set[str]:
    """从代码行中提取所有字符串字面量的值。"""
    literals: set[str] = set()
    for line in lines:
        # 单引号、双引号字符串
        for m in _STRING_LITERAL_RE.finditer(line):
            val = m.group(1) if m.group(1) is not None else m.group(2)
            if val is not None and val.strip():
                literals.add(val.strip())
        # 模板字符串静态部分
        for m in _TEMPLATE_LITERAL_RE.finditer(line):
            val = m.group(1).strip()
            if val:
                literals.add(val)
    return literals


def check_csv_value_against_literals(
    val: str, field: str, literals: set[str]
) -> str | None:
    """检查 CSV 字段值是否在代码字面量中找到匹配。

    返回 None 表示匹配通过，返回原因字符串表示有问题。
    """
    if not val:
        return None  # 空值由必填字段检查处理

    # 精确匹配
    if val in literals:
        return None

    # 包含匹配：CSV 值是某个字面量的子串，或字面量是 CSV 值的子串
    for lit in literals:
        if val in lit or lit in val:
            return None

    return f"{field}='{val}'在代码中无字面量匹配"


def check_empty_field_can_infer(
    field: str, literals: set[str]
) -> str | None:
    """检查空字段是否可从代码字面量推断。

    返回 None 表示无推断线索，返回原因字符串表示可推断。
    """
    if not literals:
        return None
    # 如果有字面量，提示可推断
    sample = list(literals)[:3]
    sample_str = "', '".join(sample)
    return f"{field}可从代码字面量'{sample_str}'推断"


def review_row(
    row: dict, root: Path, site_keys: set[tuple[str, int, int]]
) -> list[str]:
    """对单行 CSV 执行所有预审检查，返回原因列表。"""
    reasons: list[str] = []

    # 1. 必填字段空值检查
    for field in REQUIRED_NONEMPTY:
        val = (row.get(field) or "").strip()
        if not val:
            reasons.append(f"{field}为空")

    # 2. file 字段结构校验
    raw_file = (row.get("file") or "").strip()
    fpath, start, end = parse_file_field(raw_file)

    if fpath is None:
        reasons.append(f"file格式非法（期望 文件名:start,end）: {raw_file!r}")
        return reasons  # 无法继续代码级检查

    file_exists = (root / fpath).exists()
    if not file_exists:
        reasons.append(f"文件{fpath}不存在")
        return reasons  # 无法读取代码

    total_lines = count_file_lines(root / fpath)
    if start < 1 or end < start or start > total_lines:
        reasons.append(f"行号范围{start}-{end}无效（文件共{total_lines}行）")
        return reasons

    # 3. 调用点对应关系检查
    key = (fpath, start, end)
    if key not in site_keys:
        reasons.append(f"{raw_file}在call_sites.json中无对应调用点")

    # 4. 代码级字面量比对
    code_lines = read_code_lines(root / fpath, start, end)
    if not code_lines:
        reasons.append(f"无法读取{fpath}:{start},{end}的代码")
        return reasons

    literals = extract_string_literals(code_lines)

    for field in ALL_CHECK_FIELDS:
        val = (row.get(field) or "").strip()
        if val:
            # 已填值：检查是否在代码中有字面量匹配
            result = check_csv_value_against_literals(val, field, literals)
            if result:
                reasons.append(result)
        else:
            # 空值且非必填字段（event）：跳过
            if field not in REQUIRED_NONEMPTY:
                continue
            # 空值且必填：检查是否可从字面量推断
            result = check_empty_field_can_infer(field, literals)
            if result:
                reasons.append(result)

    return reasons


def main() -> int:
    global LOG
    ap = argparse.ArgumentParser(
        description="预审 sensors.csv，添加 illegal_reason 列 -> review_report.txt"
    )
    ap.add_argument("--csv", default="sensors.csv", help="第 4 步产物（默认: sensors.csv）")
    ap.add_argument("--sites", default="call_sites.json", help="第 3 步产物（默认: call_sites.json）")
    ap.add_argument("--root", required=True, help="目标代码库根目录")
    ap.add_argument("--out", default="review_report.txt", help="预审报告输出（默认: review_report.txt）")
    ap.add_argument("--log", default=None, help="日志文件路径（默认: <out>.log）")
    args = ap.parse_args()

    out = Path(args.out)
    log_path = Path(args.log) if args.log else out.with_suffix(".log")
    LOG = setup_logging("review_report", log_path)

    exit_code = 0
    n_flagged = 0

    try:
        csv_path = Path(args.csv)
        sites_path = Path(args.sites)
        root = Path(args.root).resolve()

        if not csv_path.exists():
            LOG.error("找不到 %s，第 4 步是否已执行？", csv_path)
            exit_code = 1
            return exit_code

        if not sites_path.exists():
            LOG.error("找不到 %s，第 3 步是否已执行？", sites_path)
            exit_code = 1
            return exit_code

        if not root.is_dir():
            LOG.error("代码库根不存在: %s", root)
            exit_code = 1
            return exit_code

        # 加载 call_sites.json，构建 (file, start_line, end_line) 集合
        sites = json.loads(sites_path.read_text(encoding="utf-8"))
        site_keys: set[tuple[str, int, int]] = set()
        for s in sites:
            site_keys.add((s["file"], s["start_line"], s["end_line"]))
        LOG.info("加载 %d 个调用点", len(site_keys))

        # 读取 CSV（兼容有/无 illegal_reason 列）
        rows: list[dict] = []
        with csv_path.open(encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            existing_header = list(reader.fieldnames or [])
            for row in reader:
                rows.append(row)

        LOG.info("加载 CSV: %d 行，表头: %s", len(rows), existing_header)

        # 逐行预审
        report_lines: list[str] = []
        report_lines.append("=" * 60)
        report_lines.append("埋点预审报告（Step 5.5）")
        report_lines.append("=" * 60)
        report_lines.append(f"CSV 行数: {len(rows)}")
        report_lines.append(f"调用点数: {len(site_keys)}")
        report_lines.append(f"代码库根: {root}")
        report_lines.append("")

        for idx, row in enumerate(rows, 1):
            reasons = review_row(row, root, site_keys)
            row["illegal_reason"] = "; ".join(reasons) if reasons else ""
            if reasons:
                n_flagged += 1
                report_lines.append(f"第{idx}行 [{row.get('file', '').strip()}]:")
                for r in reasons:
                    report_lines.append(f"  - {r}")
                report_lines.append("")

        # 统计
        n_clean = len(rows) - n_flagged
        report_lines.append("-" * 60)
        report_lines.append(f"统计: 总行数={len(rows)}, 无问题={n_clean}, 有问题={n_flagged}")
        if n_flagged > 0:
            pct = n_flagged / len(rows) * 100 if rows else 0
            report_lines.append(f"建议: {pct:.0f}% 的行需要 LLM 深入复核")
        else:
            report_lines.append("所有行通过预审，无需 LLM 复核")
        report_lines.append("")

        # 写报告
        out.write_text("\n".join(report_lines) + "\n", encoding="utf-8")
        for line in report_lines:
            LOG.info(line)
        LOG.info("预审报告已写入 %s", out)

        # 覆写 sensors.csv（带 illegal_reason 列），保留原始表头中的额外列
        output_fields = list(existing_header)
        if "illegal_reason" not in output_fields:
            output_fields.append("illegal_reason")
        with csv_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=output_fields)
            writer.writeheader()
            writer.writerows(rows)
        LOG.info("已覆写 %s（添加 illegal_reason 列，保留 %d 列）", csv_path, len(output_fields))

        if n_flagged > 0:
            exit_code = 2
        else:
            exit_code = 0

    except Exception as exc:
        LOG.exception("执行失败: %s", exc)
        exit_code = 1

    finally:
        if exit_code == 1:
            next_step(f"执行失败，请查看日志: {log_path}")
        elif exit_code == 2:
            next_step(
                f"预审完成，{n_flagged} 行存在问题。请进入第 5.6 步：LLM 逐行复核 sensors.csv 中 illegal_reason 非空的行"
            )
        else:
            next_step("预审通过，所有行无问题。可直接进入第 6 步归档")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
