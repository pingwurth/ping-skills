#!/usr/bin/env python3
"""第 5 步：校验 call_sites.json 与 sensors.csv 的一致性，输出 check_report.txt。

校验规则：
  ERROR（导致 FAIL）：
    1. CSV 数据行数 < len(call_sites)（遗漏调用点）
    2. CSV 每行的 file（格式 文件名:start,end）必须能在 call_sites.json 中找到对应
    3. 所有字段值不得包含动态占位符（如 <动态:...>、<变量:...>、<unknown> 等）
  WARNING（不影响 PASS/FAIL，报告中列出）：
    4. page_name / first_biz_id / second_biz_id / first_biz_name / second_biz_name 五字段为空
       → 同时输出 resolution_report.csv 供人工复核

退出码：
  0 = PASS（无错误无警告）
  1 = 执行异常
  2 = FAIL（有结构性错误）
  3 = WARN（无结构性错误，但有空字段需人工复核）
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

from utils import next_step, setup_logging

LOG = None  # main() 中由 setup_logging 初始化

REQUIRED_NONEMPTY = ["page_name", "first_biz_id", "second_biz_id", "first_biz_name", "second_biz_name"]
ALL_CHECK_FIELDS = REQUIRED_NONEMPTY + ["event"]
CSV_HEADER = ["page_name", "first_biz_id", "second_biz_id",
              "first_biz_name", "second_biz_name", "event",
              "file"]
# review_report.py 会追加 illegal_reason 列，check_report 兼容两种表头
CSV_HEADER_WITH_REASON = CSV_HEADER + ["illegal_reason"]

# 解析 file 字段：格式为 "文件名:start,end"
_FILE_LOC_RE = re.compile(r'^(.+):(\d+),(\d+)$')

# 动态占位符检测：匹配 <动态:...>、<变量:...>、<unknown>、<dynamic:...> 等
_DYNAMIC_PLACEHOLDER_RE = re.compile(r'<\s*(?:动态|变量|unknown|dynamic|未确定)\s*[:>]', re.IGNORECASE)


def main() -> int:
    global LOG
    ap = argparse.ArgumentParser(description="校验调用点与上报字段一致性 -> check_report.txt")
    ap.add_argument("--sites", default="call_sites.json", help="第 3 步产物")
    ap.add_argument("--csv", default="sensors.csv", help="第 4 步产物")
    ap.add_argument("--out", default="check_report.txt", help="校验报告输出")
    ap.add_argument("--resolution-report", default=None,
                    help="字段追踪报告输出（默认: 与 --out 同目录的 resolution_report.csv）")
    ap.add_argument("--log", default=None, help="日志文件路径（默认: <out>.log）")
    args = ap.parse_args()

    out = Path(args.out)
    log_path = Path(args.log) if args.log else out.with_suffix(".log")
    LOG = setup_logging("check_report", log_path)

    resolution_out = Path(args.resolution_report) if args.resolution_report else out.parent / "resolution_report.csv"

    exit_code = 0
    result_status = "PASS"
    n_errors = 0
    n_warnings = 0

    try:
        sites_path = Path(args.sites)
        csv_path = Path(args.csv)
        if not sites_path.exists():
            LOG.error("找不到 %s", sites_path)
            exit_code = 1
            return exit_code
        if not csv_path.exists():
            LOG.error("找不到 %s，第 4 步是否已执行？", csv_path)
            exit_code = 1
            return exit_code

        sites = json.loads(sites_path.read_text(encoding="utf-8"))
        site_keys = {(s["file"], s["start_line"], s["end_line"]) for s in sites}

        rows: list[dict] = []
        with csv_path.open(encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            # 检查必需列是否存在（顺序无关，允许多余列）
            actual_cols = set(reader.fieldnames or [])
            required_cols = set(CSV_HEADER)
            missing = required_cols - actual_cols
            if missing:
                LOG.warning("CSV 表头缺少必需列: %s（实际表头: %s）", missing, reader.fieldnames)
            rows = list(reader)

        errors: list[str] = []
        warnings: list[str] = []
        resolution_rows: list[dict] = []  # 用于 resolution_report.csv

        # 规则 1（ERROR）：记录数充足（CSV 行数 >= 调用点数）
        if len(rows) < len(sites):
            errors.append(
                f"记录数不足: call_sites={len(sites)} 条 vs csv={len(rows)} 行"
                f"（至少需要 {len(sites)} 行，缺少 {len(sites) - len(rows)}）"
            )

        # 规则 2/3/4：逐行校验
        orphan_rows: list[str] = []
        placeholder_fields: list[str] = []
        for idx, row in enumerate(rows, 1):
            # 规则 4（WARNING）：required 字段为空
            for col in REQUIRED_NONEMPTY:
                val = (row.get(col) or "").strip()
                if not val:
                    warnings.append(f"第{idx}行 {col} 为空")
                    resolution_row = {
                        "file": (row.get("file") or "").strip(),
                        "row": idx,
                        "field": col,
                    }
                    # 若已有 illegal_reason，附带原因
                    reason = (row.get("illegal_reason") or "").strip()
                    if reason:
                        resolution_row["illegal_reason"] = reason
                    resolution_rows.append(resolution_row)
            # 规则 3（ERROR）：检测所有字段（含 event）中的动态占位符
            for col in ALL_CHECK_FIELDS:
                val = (row.get(col) or "").strip()
                if val and _DYNAMIC_PLACEHOLDER_RE.search(val):
                    placeholder_fields.append(
                        f"第{idx}行 {col} 包含动态占位符: {val!r}"
                    )
            # 规则 2（ERROR）：file 格式与对应关系
            raw_file = (row.get("file") or "").strip()
            m = _FILE_LOC_RE.match(raw_file)
            if not m:
                orphan_rows.append(f"第{idx}行 file 格式非法（期望 文件名:start,end）: {raw_file!r}")
                continue
            fpath, sl, el = m.group(1), int(m.group(2)), int(m.group(3))
            key = (fpath, sl, el)
            if key not in site_keys:
                orphan_rows.append(f"第{idx}行 {raw_file} 在 call_sites.json 中无对应调用点")

        errors.extend(orphan_rows)
        errors.extend(placeholder_fields)
        n_errors = len(errors)
        n_warnings = len(warnings)

        # 写报告
        report_lines: list[str] = []
        report_lines.append("=" * 60)
        report_lines.append("埋点分析校验报告")
        report_lines.append("=" * 60)
        report_lines.append(f"call_sites.json 调用点数: {len(sites)}")
        report_lines.append(f"sensors.csv 数据行数:    {len(rows)}")
        has_reason_col = any("illegal_reason" in row for row in rows)
        report_lines.append(f"必填非空字段: {', '.join(REQUIRED_NONEMPTY)}")
        report_lines.append(f"illegal_reason 列: {'已存在' if has_reason_col else '未添加（将在 Step 5.5 追加）'}")
        report_lines.append(f"动态占位符检测字段: {', '.join(ALL_CHECK_FIELDS)}")
        report_lines.append("")

        if errors:
            result_status = "FAIL"
            report_lines.append(f"校验结果: FAIL ({n_errors} errors, {n_warnings} warnings)")
            report_lines.append("-" * 60)
            report_lines.append("ERRORS (结构性错误，必须修正):")
            for p in errors:
                report_lines.append(f"  - {p}")
        elif warnings:
            result_status = "WARN"
            report_lines.append(f"校验结果: WARN (0 errors, {n_warnings} warnings)")
            report_lines.append("-" * 60)
        else:
            report_lines.append("校验结果: PASS")
            report_lines.append("-" * 60)
            report_lines.append("  - 记录数充足（CSV 行数 >= 调用点数）")
            report_lines.append("  - 必填字段均非空")
            report_lines.append("  - CSV 行均能在调用点中找到对应")
            report_lines.append("  - 无动态占位符")

        if warnings:
            if not errors:
                report_lines.append("")
            report_lines.append("WARNINGS (需人工复核):")
            for w in warnings:
                report_lines.append(f"  - {w}")

        # 统计
        total_fields = len(rows) * len(REQUIRED_NONEMPTY)
        resolved_fields = total_fields - n_warnings
        if total_fields > 0:
            pct = n_warnings / total_fields * 100
            report_lines.append("")
            report_lines.append(f"统计: 总字段={total_fields}, 已解析={resolved_fields}, 未解析={n_warnings} ({pct:.0f}%)")
            if pct > 30:
                report_lines.append("建议: unresolvable 比例超过 30%，建议人工深入复核")

        report_lines.append("")

        out.write_text("\n".join(report_lines) + "\n", encoding="utf-8")
        for line in report_lines:
            LOG.info(line)
        LOG.info("报告已写入 %s", out)

        # 自动生成 resolution_report.csv
        # 若 resolution_rows 中包含 illegal_reason，表头加上该列
        has_reason_in_resolution = any("illegal_reason" in r for r in resolution_rows)
        resolution_fieldnames = ["file", "row", "field"]
        if has_reason_in_resolution:
            resolution_fieldnames.append("illegal_reason")

        if resolution_rows:
            resolution_out.parent.mkdir(parents=True, exist_ok=True)
            with resolution_out.open("w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=resolution_fieldnames)
                writer.writeheader()
                writer.writerows(resolution_rows)
            LOG.info("字段追踪报告已写入 %s（%d 条记录）", resolution_out, len(resolution_rows))
        else:
            # 无空字段时也生成空文件（只有表头），保持一致性
            resolution_out.parent.mkdir(parents=True, exist_ok=True)
            with resolution_out.open("w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=resolution_fieldnames)
                writer.writeheader()
            LOG.info("字段追踪报告已写入 %s（无未解析字段）", resolution_out)

        if errors:
            exit_code = 2
        elif warnings:
            exit_code = 3
        else:
            exit_code = 0

    except Exception as exc:
        LOG.exception("执行失败: %s", exc)
        exit_code = 1

    finally:
        if exit_code == 1:
            next_step(f"执行失败，请查看日志: {log_path}")
        elif exit_code == 2:
            next_step(f"校验未通过 (FAIL)，共 {n_errors} 个结构性错误。请查看 {out} 修正后重跑")
        elif exit_code == 3:
            next_step(f"校验基本通过 (WARN)，{n_warnings} 个字段无法通过静态分析解析。请查看 {out} 和 {resolution_out} 了解详情")
        else:
            next_step(f"校验通过 (PASS)。分析完成")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
