#!/usr/bin/env python3
"""CSV 报告管理器：归档带时间戳的 CSV，并与上一版本比对差异。

功能：
  1. 将 sensors.csv 复制到 .agent/sensors-analyze/reports/sensors_YYYYMMDD_HHmmss.csv
  2. 自动查找上一次归档的 CSV，比对差异（增删改）
  3. 输出差异报告供人工复核

用法：
  python3 scripts/report_manager.py --csv sensors.csv
  python3 scripts/report_manager.py --csv sensors.csv --reports-dir .agent/sensors-analyze/reports
  python3 scripts/report_manager.py --csv sensors.csv --diff-only  # 仅比对，不归档

退出码：
  0 = 成功（无差异或首次归档）
  1 = 执行异常
  2 = 存在差异需人工复核
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

from utils import next_step, setup_logging

LOG = None

# 固定 CSV 表头（含 illegal_reason）
CSV_HEADER = ["page_name", "first_biz_id", "second_biz_id",
              "first_biz_name", "second_biz_name", "event",
              "file", "illegal_reason"]

# file 列标识调用点，但同一调用点可产生多行（不同 page_name 等），
# 因此用全列签名做主键，支持重复行计数比对。


def setup_logging(log_path: Path) -> logging.Logger:
    """配置日志：所有输出写入 log 文件，stdout 仅保留给 next_step。"""
    logger = logging.getLogger("report_manager")
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


def generate_timestamped_name(base: str = "sensors") -> str:
    """生成带时间戳的文件名：sensors_YYYYMMDD_HHmmss.csv"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{base}_{ts}.csv"


def find_previous_csv(reports_dir: Path, base: str = "sensors") -> Path | None:
    """查找 reports_dir 中最近一次归档的 CSV 文件（按文件名时间戳排序）。"""
    if not reports_dir.exists():
        return None
    pattern = f"{base}_*.csv"
    csv_files = sorted(reports_dir.glob(pattern), reverse=True)
    # 排除当前正在处理的文件（如果有）
    return csv_files[0] if csv_files else None


# 旧表头（兼容无 illegal_reason 列的 CSV）
CSV_HEADER_OLD = ["page_name", "first_biz_id", "second_biz_id",
                   "first_biz_name", "second_biz_name", "event", "file"]


def load_csv_rows(csv_path: Path) -> list[dict]:
    """加载 CSV 文件，返回行列表。"""
    rows = []
    with csv_path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        header = list(reader.fieldnames or [])
        if header not in (CSV_HEADER, CSV_HEADER_OLD):
            LOG.warning("CSV 表头 %s 与期望 %s 不一致", header, CSV_HEADER)
        for row in reader:
            # 清理空白
            cleaned = {k: (v or "").strip() for k, v in row.items()}
            rows.append(cleaned)
    return rows


def build_row_signature(row: dict) -> str:
    """构建行的全列签名（用作主键 + 值检测）。"""
    parts = []
    for col in CSV_HEADER:
        parts.append(f"{col}={row.get(col, '').strip()}")
    return "|".join(parts)


def diff_csv(old_rows: list[dict], new_rows: list[dict]) -> dict:
    """比对两个 CSV 的差异，返回 {added, deleted, modified}。

    使用全列签名做主键，通过 multiset 计数正确处理重复行。
    同一调用点（file 列相同）可产生多行（不同 page_name 等），不再丢失。
    """
    old_map: dict[str, tuple[dict, Counter]] = {}  # signature -> (representative_row, count)
    for row in old_rows:
        sig = build_row_signature(row)
        if sig not in old_map:
            old_map[sig] = (row, Counter())
        old_map[sig][1][sig] += 1

    new_map: dict[str, tuple[dict, Counter]] = {}
    for row in new_rows:
        sig = build_row_signature(row)
        if sig not in new_map:
            new_map[sig] = (row, Counter())
        new_map[sig][1][sig] += 1

    old_sigs = set(old_map.keys())
    new_sigs = set(new_map.keys())

    added = []
    deleted = []
    modified = []

    # 新增：签名在 new 中但不在 old 中
    for sig in sorted(new_sigs - old_sigs):
        count = new_map[sig][1][sig]
        added.extend([new_map[sig][0]] * count)

    # 删除：签名在 old 中但不在 new 中
    for sig in sorted(old_sigs - new_sigs):
        count = old_map[sig][1][sig]
        deleted.extend([old_map[sig][0]] * count)

    # 修改：签名不同但 file 列相同（同一调用点的字段值变化）
    # 按 file 列分组，检测同 file 下签名变化
    old_by_file: dict[str, list[str]] = {}
    for sig in old_sigs:
        file_val = old_map[sig][0].get("file", "").strip()
        old_by_file.setdefault(file_val, []).append(sig)
    new_by_file: dict[str, list[str]] = {}
    for sig in new_sigs:
        file_val = new_map[sig][0].get("file", "").strip()
        new_by_file.setdefault(file_val, []).append(sig)

    for file_val in sorted(set(old_by_file) & set(new_by_file)):
        old_file_sigs = set(old_by_file[file_val])
        new_file_sigs = set(new_by_file[file_val])
        changed_sigs = old_file_sigs.symmetric_difference(new_file_sigs)
        if changed_sigs:
            # 有签名变化：报告 old→new 的配对
            for old_sig in sorted(old_file_sigs & changed_sigs):
                for new_sig in sorted(new_file_sigs & changed_sigs):
                    modified.append({
                        "file": file_val,
                        "old": old_map[old_sig][0],
                        "new": new_map[new_sig][0],
                    })
                    break  # 每个 old 只配对一个 new

    return {"added": added, "deleted": deleted, "modified": modified}


def format_row(row: dict) -> str:
    """格式化一行 CSV 为可读字符串。"""
    parts = []
    for col in CSV_HEADER:
        val = row.get(col, "").strip()
        parts.append(f"{col}={val!r}")
    return " | ".join(parts)


def _format_modified_entry(entry: dict) -> list[str]:
    """格式化一条修改记录，返回报告行列表。"""
    lines = []
    lines.append(f"  ~ file={entry['file']!r}")
    for col in CSV_HEADER:
        if col == "file":
            continue
        old_val = entry["old"].get(col, "").strip()
        new_val = entry["new"].get(col, "").strip()
        if old_val != new_val:
            lines.append(f"       {col}: {old_val!r} -> {new_val!r}")
    return lines


def write_diff_report(diff: dict, out_path: Path) -> list[str]:
    """写差异报告，返回报告行列表。"""
    added = diff["added"]
    deleted = diff["deleted"]
    modified = diff["modified"]

    lines = []
    lines.append("=" * 60)
    lines.append("CSV 版本差异报告")
    lines.append("=" * 60)
    lines.append(f"新增行: {len(added)}")
    lines.append(f"删除行: {len(deleted)}")
    lines.append(f"修改行: {len(modified)}")
    lines.append("")

    if not added and not deleted and not modified:
        lines.append("无差异，两个版本内容一致。")
    else:
        if added:
            lines.append("-" * 60)
            lines.append(f"新增 ({len(added)} 行):")
            lines.append("-" * 60)
            for i, row in enumerate(added, 1):
                lines.append(f"  +[{i}] {format_row(row)}")
            lines.append("")

        if deleted:
            lines.append("-" * 60)
            lines.append(f"删除 ({len(deleted)} 行):")
            lines.append("-" * 60)
            for i, row in enumerate(deleted, 1):
                lines.append(f"  -[{i}] {format_row(row)}")
            lines.append("")

        if modified:
            lines.append("-" * 60)
            lines.append(f"修改 ({len(modified)} 行):")
            lines.append("-" * 60)
            for i, entry in enumerate(modified, 1):
                lines.append(f"  ~[{i}]")
                lines.extend(_format_modified_entry(entry))
            lines.append("")

    lines.append("=" * 60)
    report_text = "\n".join(lines) + "\n"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report_text, encoding="utf-8")
    return lines


def main() -> int:
    global LOG
    ap = argparse.ArgumentParser(description="CSV 报告管理器：归档 + 版本比对")
    ap.add_argument("--csv", default="sensors.csv", help="当前 CSV 文件路径")
    ap.add_argument("--reports-dir", default=None,
                    help="报告归档目录（默认: .agent/sensors-analyze/reports/）")
    ap.add_argument("--diff-only", action="store_true",
                    help="仅比对，不执行归档")
    ap.add_argument("--log", default=None, help="日志文件路径")
    ap.add_argument("--out", default=None,
                    help="差异报告输出路径（默认: <reports-dir>/diff_report.txt）")
    args = ap.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"错误: 找不到 CSV 文件 {csv_path}", file=sys.stderr)
        return 1

    # 确定 reports 目录
    if args.reports_dir:
        reports_dir = Path(args.reports_dir)
    else:
        reports_dir = Path(".agent/sensors-analyze/reports")

    log_path = Path(args.log) if args.log else reports_dir / "report_manager.log"
    LOG = setup_logging("report_manager", log_path)

    LOG.info("CSV 文件: %s", csv_path)
    LOG.info("报告目录: %s", reports_dir)

    try:
        # 加载当前 CSV
        new_rows = load_csv_rows(csv_path)
        LOG.info("加载 CSV: %d 行", len(new_rows))

        # 查找上一次归档的 CSV
        prev_csv = find_previous_csv(reports_dir)
        has_previous = prev_csv is not None

        if has_previous:
            LOG.info("找到上次归档: %s", prev_csv)
            old_rows = load_csv_rows(prev_csv)
            LOG.info("加载上次 CSV: %d 行", len(old_rows))
        else:
            LOG.info("未找到上次归档，首次归档")
            old_rows = []

        # 比对差异
        diff = diff_csv(old_rows, new_rows)
        n_changes = len(diff["added"]) + len(diff["deleted"]) + len(diff["modified"])

        # 写差异报告
        diff_report_path = Path(args.out) if args.out else reports_dir / "diff_report.txt"
        report_lines = write_diff_report(diff, diff_report_path)
        LOG.info("差异报告已写入 %s", diff_report_path)
        for line in report_lines:
            LOG.info(line)

        # 归档（除非 --diff-only）
        if not args.diff_only:
            ts_name = generate_timestamped_name()
            archive_path = reports_dir / ts_name
            reports_dir.mkdir(parents=True, exist_ok=True)

            # 复制 CSV 到归档目录
            import shutil
            shutil.copy2(str(csv_path), str(archive_path))
            LOG.info("已归档: %s", archive_path)
        else:
            LOG.info("--diff-only 模式，跳过归档")

        # 输出结果
        if not has_previous:
            next_step(f"首次归档完成。已保存到 {reports_dir / generate_timestamped_name()}")
            return 0
        elif n_changes == 0:
            next_step(f"无差异，内容与上次归档一致。已保存到 {reports_dir / generate_timestamped_name()}")
            return 0
        else:
            next_step(
                f"发现 {n_changes} 处差异（+{len(diff['added'])} -{len(diff['deleted'])} ~{len(diff['modified'])}）。"
                f"请查看 {diff_report_path} 进行人工复核"
            )
            return 2

    except Exception as exc:
        LOG.exception("执行失败: %s", exc)
        next_step(f"执行失败，请查看日志: {log_path}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
