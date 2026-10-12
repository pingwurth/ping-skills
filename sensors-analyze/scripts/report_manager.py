#!/usr/bin/env python3
"""CSV 报告管理器：归档带时间戳的 CSV，并与上一版本比对差异。

功能：
  1. 将 sensors.csv 复制到 .agent/sensors-analyze/reports/sensors_YYYYMMDD_HHmmss.csv
  2. 自动查找上一次归档的 CSV，比对差异（增删改）
  3. 输出差异报告供人工复核

用法（所有产物默认在 .agent/sensors-analyze/ 下）：
  python3 scripts/report_manager.py
  python3 scripts/report_manager.py --reports-dir .agent/sensors-analyze/reports
  python3 scripts/report_manager.py --diff-only  # 仅比对，不归档

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

from utils import ARTIFACT_DIR, next_step, setup_logging

LOG = None

# 固定 CSV 表头（含 illegal_reason）
CSV_HEADER = ["page_name", "first_biz_id", "second_biz_id",
              "first_biz_name", "second_biz_name", "event",
              "file", "illegal_reason"]

# file 列标识调用点，但同一调用点可产生多行（不同 page_name 等），
# 因此用全列签名做主键，支持重复行计数比对。


def generate_timestamped_name(base: str = "sensors") -> str:
    """生成带时间戳的文件名：sensors_YYYYMMDD_HHmmss.csv"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{base}_{ts}.csv"


def unique_archive_path(reports_dir: Path, ts_name: str) -> Path:
    """时间戳归档路径；同名已存在（同秒内多次运行）时追加 _1/_2… 序号。

    不加处理时 shutil.copy2 会静默覆盖上一次归档，diff 历史失真。
    """
    archive_path = reports_dir / ts_name
    stem = ts_name[: -len(".csv")]
    i = 1
    while archive_path.exists():
        archive_path = reports_dir / f"{stem}_{i}.csv"
        i += 1
    return archive_path


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
    """加载 CSV 文件，返回行列表。

    行内字段数多于表头（典型：file 列 `a.js:1,1` 含逗号未加双引号）时
    视为致命输入错误直接报错——不能把损坏行静默修剪后固化进归档历史。
    """
    rows = []
    with csv_path.open(encoding="utf-8", newline="") as f:
        # restkey 把多余字段收进 __extra__（默认会变成 None 键导致下游 .strip() 崩溃）
        reader = csv.DictReader(f, restkey="__extra__", restval="")
        header = list(reader.fieldnames or [])
        if header not in (CSV_HEADER, CSV_HEADER_OLD):
            LOG.warning("CSV 表头 %s 与期望 %s 不一致", header, CSV_HEADER)
        for i, row in enumerate(reader, 1):
            if row.pop("__extra__", None):
                raise ValueError(
                    f"CSV 第 {i} 行字段数多于表头（file 列形如 a.js:1,1 含逗号，"
                    f"必须整体加双引号），请修正 CSV 后重跑"
                )
            cleaned = {k: (v or "").strip() if isinstance(v, str) else "" for k, v in row.items()}
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

    按 file 列（调用点）分组比对，组内用全列签名做行实例计数：
      1. 整组 file 只在新版出现 → 组内全部行记「新增」；
      2. 整组 file 只在旧版出现 → 组内全部行记「删除」；
      3. 两版都有的 file → 先按计数抵消完全相同的行，剩余旧行与新行
         按签名排序 zip 配对为「修改」，配不上的分别记「删除」/「新增」。
    三类互斥：同一行变化只计入一类。旧实现先做全局签名差集（added）再在
    file 组内配对（modified），同一行会同时出现在两类里导致双重计数。
    """
    def file_of(row: dict) -> str:
        return (row.get("file") or "").strip()

    old_by_file: dict[str, list[dict]] = {}
    for row in old_rows:
        old_by_file.setdefault(file_of(row), []).append(row)
    new_by_file: dict[str, list[dict]] = {}
    for row in new_rows:
        new_by_file.setdefault(file_of(row), []).append(row)

    added: list[dict] = []
    deleted: list[dict] = []
    modified: list[dict] = []

    for file_val in sorted(set(new_by_file) - set(old_by_file)):
        added.extend(new_by_file[file_val])
    for file_val in sorted(set(old_by_file) - set(new_by_file)):
        deleted.extend(old_by_file[file_val])

    for file_val in sorted(set(old_by_file) & set(new_by_file)):
        old_group = old_by_file[file_val]
        new_group = new_by_file[file_val]
        old_counter = Counter(build_row_signature(r) for r in old_group)
        new_counter = Counter(build_row_signature(r) for r in new_group)
        common = old_counter & new_counter  # 按计数取交集：两边完全相同的行
        old_rest = sorted((old_counter - common).elements())
        new_rest = sorted((new_counter - common).elements())
        old_row_of = {build_row_signature(r): r for r in old_group}
        new_row_of = {build_row_signature(r): r for r in new_group}
        n_pair = min(len(old_rest), len(new_rest))
        for i in range(n_pair):
            modified.append({
                "file": file_val,
                "old": old_row_of[old_rest[i]],
                "new": new_row_of[new_rest[i]],
            })
        deleted.extend(old_row_of[s] for s in old_rest[n_pair:])
        added.extend(new_row_of[s] for s in new_rest[n_pair:])

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
    ap.add_argument("--csv", default=str(ARTIFACT_DIR / "sensors.csv"),
                    help="当前 CSV 文件路径（默认: .agent/sensors-analyze/sensors.csv）")
    ap.add_argument("--reports-dir", default=None,
                    help="报告归档目录（默认: .agent/sensors-analyze/reports/）")
    ap.add_argument("--diff-only", action="store_true",
                    help="仅比对，不执行归档")
    ap.add_argument("--log", default=None, help="日志文件路径")
    ap.add_argument("--out", default=None,
                    help="差异报告输出路径（默认: <reports-dir>/diff_report.txt）")
    args = ap.parse_args()

    csv_path = Path(args.csv)

    # 确定 reports 目录
    if args.reports_dir:
        reports_dir = Path(args.reports_dir)
    else:
        reports_dir = ARTIFACT_DIR / "reports"

    log_path = Path(args.log) if args.log else reports_dir / "report_manager.log"
    LOG = setup_logging("report_manager", log_path)

    LOG.info("CSV 文件: %s", csv_path)
    LOG.info("报告目录: %s", reports_dir)

    try:
        # CSV 缺失也要走 next_step 通道：本 skill 的脚本契约是 stdout 仅一行
        # next_step 驱动流程，直接 print 到 stderr 会让编排方失去指引
        if not csv_path.exists():
            LOG.error("找不到 CSV 文件 %s", csv_path)
            next_step(f"找不到 CSV 文件 {csv_path}，第 4/5.6 步是否已产出？请检查后重跑")
            return 1

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
        archive_path: Path | None = None
        if not args.diff_only:
            reports_dir.mkdir(parents=True, exist_ok=True)
            archive_path = unique_archive_path(reports_dir, generate_timestamped_name())

            # 复制 CSV 到归档目录
            import shutil
            shutil.copy2(str(csv_path), str(archive_path))
            LOG.info("已归档: %s", archive_path)
        else:
            LOG.info("--diff-only 模式，跳过归档")

        # 输出结果。归档路径必须引用实际写入的 archive_path——
        # 二次调用 generate_timestamped_name() 跨秒后文件名对不上，
        # next_step 会指向不存在的文件；--diff-only 未归档时不得声称已保存
        if not has_previous:
            if archive_path:
                next_step(f"首次归档完成。已保存到 {archive_path}")
            else:
                next_step(f"仅比对完成：无上次归档可对比（首次运行建议去掉 --diff-only 执行归档）。差异报告: {diff_report_path}")
            return 0
        if n_changes == 0:
            if archive_path:
                next_step(f"无差异，内容与上次归档一致。已归档到 {archive_path}")
            else:
                next_step("仅比对完成：无差异，内容与上次归档一致。未归档（--diff-only）")
            return 0
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
