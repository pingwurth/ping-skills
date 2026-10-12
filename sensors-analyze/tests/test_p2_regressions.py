"""P2 回归测试：2026-10-12 审查报告 M4/M6/M7/L4/L9。

M4 diff 报告按 file 分组、三类互斥——同 file 下"改一行+加一行"只算 +1 ~1，不双重计数；
M6 --exts 后缀白名单在纯 Python 回退路径真正生效；
M7 同秒归档冲突追加 _1 序号而非静默覆盖；
L4 CSV 表头缺列升为结构性 ERROR，不再逐行刷"字段为空"噪音；
L9 --sensors 缺失时 build_exclusions 降级必须在日志留痕。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"

CSV_HEADER_FULL = ("page_name,first_biz_id,second_biz_id,first_biz_name,"
                   "second_biz_name,event,file,illegal_reason")


def _run(script: str, *args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / script), *args],
        cwd=str(cwd), capture_output=True, text=True,
    )


# ---------- M4 ----------

def test_m4_diff_no_double_counting(tmp_path: Path):
    """v1: a.js 一行；v2: a.js 改一行 + a.js 加一行 + b.js 新调用点
    → 应报 +2 -0 ~1（旧实现报 +3 并同时 ~1，双重计数）。"""
    art = tmp_path / ".agent" / "sensors-analyze"
    art.mkdir(parents=True)

    (art / "sensors.csv").write_text(
        CSV_HEADER_FULL + '\nh,1,2,3,4,e,"a.js:1,1",\n', encoding="utf-8")
    assert _run("report_manager.py", cwd=tmp_path).returncode == 0  # 首次归档

    (art / "sensors.csv").write_text(
        CSV_HEADER_FULL + '\n'
        'h,1,2,3,4X,e,"a.js:1,1",\n'   # a.js：字段值变化 → 修改
        'h2,1,2,3,4,e,"a.js:1,1",\n'   # a.js：新增取值记录 → 新增
        'h,1,2,3,4,e,"b.js:1,1",\n',   # 新调用点 → 新增
        encoding="utf-8")
    proc = _run("report_manager.py", cwd=tmp_path)
    assert proc.returncode == 2
    assert "+2 -0 ~1" in proc.stdout

    diff = (art / "reports" / "diff_report.txt").read_text(encoding="utf-8")
    assert "新增行: 2" in diff
    assert "删除行: 0" in diff
    assert "修改行: 1" in diff


def test_m4_diff_removed_call_site_reported_as_deleted(tmp_path: Path):
    art = tmp_path / ".agent" / "sensors-analyze"
    art.mkdir(parents=True)
    (art / "sensors.csv").write_text(
        CSV_HEADER_FULL + '\nh,1,2,3,4,e,"a.js:1,1",\nh,1,2,3,4,e,"b.js:1,1",\n',
        encoding="utf-8")
    assert _run("report_manager.py", cwd=tmp_path).returncode == 0

    (art / "sensors.csv").write_text(
        CSV_HEADER_FULL + '\nh,1,2,3,4,e,"a.js:1,1",\n', encoding="utf-8")
    proc = _run("report_manager.py", cwd=tmp_path)
    assert proc.returncode == 2
    assert "+0 -1 ~0" in proc.stdout


# ---------- M6 ----------

def test_m6_exts_filter_applies_in_fallback(tmp_path: Path):
    """--exts .py：同目录 a.js/b.py 都有命中，回退路径只应命中 .py。

    用 lookahead 正则强制走纯 Python 回退（rg 不支持该语法），
    无论环境是否装有 rg，结果都确定。
    """
    art = tmp_path / ".agent" / "sensors-analyze"
    art.mkdir(parents=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.js").write_text('trackEvent("js");\n', encoding="utf-8")
    (tmp_path / "src" / "b.py").write_text('trackEvent("py");\n', encoding="utf-8")
    (art / "entry.txt").write_text(
        "trackEvent(?=\\s*\\()\n# CONFIRMED\n", encoding="utf-8")

    proc = _run("locate_call_sites.py",
                "--entry", ".agent/sensors-analyze/entry.txt",
                "--root", ".", "--out", ".agent/sensors-analyze/cs.json",
                "--exts", ".py", cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout
    sites = json.loads((art / "cs.json").read_text(encoding="utf-8"))
    assert [s["location"] for s in sites] == ["src/b.py:1-1"]


# ---------- M7 ----------

def test_m7_same_timestamp_archive_gets_suffix():
    import report_manager as rm
    from pathlib import Path
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        reports_dir = Path(d)
        ts = "sensors_20260101_000000.csv"
        p1 = rm.unique_archive_path(reports_dir, ts)
        assert p1.name == ts
        p1.touch()
        p2 = rm.unique_archive_path(reports_dir, ts)
        assert p2.name == "sensors_20260101_000000_1.csv"
        p2.touch()
        p3 = rm.unique_archive_path(reports_dir, ts)
        assert p3.name == "sensors_20260101_000000_2.csv"


# ---------- L4 ----------

def test_l4_missing_column_is_structural_error_not_row_noise(tmp_path: Path):
    (tmp_path / "call_sites.json").write_text(json.dumps([
        {"id": 1, "file": "a.js", "start_line": 1, "end_line": 1,
         "location": "a.js:1-1", "patterns": ["p"], "snippet": ""}]),
        encoding="utf-8")
    # 表头缺 event 列
    (tmp_path / "sensors.csv").write_text(
        "page_name,first_biz_id,second_biz_id,first_biz_name,second_biz_name,file\n"
        'h,1,2,3,4,"a.js:1,1"\n', encoding="utf-8")
    proc = _run("check_report.py", "--sites", "call_sites.json",
                "--csv", "sensors.csv", "--out", "out.txt", cwd=tmp_path)
    assert proc.returncode == 2
    report = (tmp_path / "out.txt").read_text(encoding="utf-8")
    assert "表头缺少必需列" in report and "'event'" in report
    assert "为空" not in report  # 不再对缺失列逐行刷空值警告


# ---------- L9 ----------

def test_l9_missing_sensors_json_logs_exclusion_warning(tmp_path: Path):
    art = tmp_path / ".agent" / "sensors-analyze"
    art.mkdir(parents=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "home.js").write_text('trackEvent("a");\n', encoding="utf-8")
    (art / "entry.txt").write_text("\\btrackEvent\\s*\\(\n# CONFIRMED\n", encoding="utf-8")

    proc = _run("locate_call_sites.py",
                "--entry", ".agent/sensors-analyze/entry.txt",
                "--root", ".", "--out", ".agent/sensors-analyze/cs.json",
                "--sensors", ".agent/sensors-analyze/no-such.json",
                cwd=tmp_path)
    assert proc.returncode == 0  # 降级不阻断
    sites = json.loads((art / "cs.json").read_text(encoding="utf-8"))
    assert len(sites) == 1
    log = (art / "cs.log").read_text(encoding="utf-8")
    assert "无法排除 wrapper/hook" in log
