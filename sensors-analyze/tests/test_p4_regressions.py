"""P4 回归测试：2026-11-09 全仓代码审查（sensors-analyze 部分）。

F12 rg 调用 subprocess text 解码固定 utf-8/replace（区域设置为 C /
cp936 时不再 UnicodeDecodeError 或乱码进产物）；
F13 extract_entries._rg 退出码非 (0,1) 时告警并回退纯 Python（不再
  静默当无匹配 → 虚假 uncovered → 假 exit 3），且补齐排除目录参数，
  审计与第 3 步扫描的文件集可比；
F14 review_report 覆写 sensors.csv 前先落 .bak 备份，畸形行溢出值
  并回 file 列保真不截断，下游归档不再固化截断值；
F38 locate_call_sites 全部正则合并为单次 rg 全树扫描（坏正则预检拆出
  走纯 Python 回退），输出与逐正则实现 / 纯 Python 回退等价；
F42 CSV 表头常量收敛到 utils 单一来源，manual_note 等文档认可的附加列
  不再触发表头不一致告警，附加列变更可进差异报告。
"""
from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

rg = pytest.mark.skipif(shutil.which("rg") is None, reason="环境无 rg")


def _run(script: str, *args: str, cwd: Path,
         env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / script), *args],
        cwd=str(cwd), capture_output=True, text=True, env=env,
    )


def _c_locale_env() -> dict:
    """模拟区域设置为 C 的环境（cp936 Windows 同理）：默认解码器非 utf-8。

    PYTHONIOENCODING=utf-8 只救 stdout（next_step 含中文），不影响
    subprocess text=True 的 locale 解码路径——那正是 F12 的被测点。
    """
    env = {k: v for k, v in os.environ.items()
           if k not in {"PYTHONIOENCODING", "PYTHONUTF8", "PYTHONCOERCECLOCALE",
                        "LC_ALL", "LANG", "LC_CTYPE"}}
    env.update(LC_ALL="C", LANG="C", PYTHONUTF8="0",
               PYTHONCOERCECLOCALE="0", PYTHONIOENCODING="utf-8")
    return env


def _write_sensors(path: Path, grep_pattern: str,
                   global_sdk_apis: list | None = None) -> None:
    """最小合法 sensors.json（满足 sensors.schema.json 全部约束）。"""
    sensors = {
        "meta": {
            "project_path": ".", "language": "js",
            "analyzed_at": "2026-11-09T00:00:00Z",
            "sdk_summary": "s",
            "global_sdk_apis": global_sdk_apis or [],
        },
        "paradigms": [{
            "id": "p1", "name": "n", "category": "code",
            "reference_location": {"file": "src/a.js", "start_line": 1},
            "call_chain": [{
                "order": 1, "layer": "page", "symbol": "s",
                "description": "d", "data_flow": [],
            }],
            "entry_functions": [{
                "name": "trackEvent", "kind": "sdk_direct",
                "location": {"file": "src/a.js", "start_line": 1},
                "grep_pattern": grep_pattern,
            }],
            "sdk_apis": [{"name": "sensors.track", "purpose": "send"}],
        }],
    }
    path.write_text(json.dumps(sensors, ensure_ascii=False), encoding="utf-8")


# ---------- F12 ----------

def test_f12_rg_calls_pin_utf8_decode(monkeypatch, tmp_path: Path):
    """两处 rg 调用必须显式传 encoding='utf-8', errors='replace'。

    契约测试：不依赖区域设置，直接锁定 subprocess.run 的解码参数
    （对齐 crp/gitops、jaut/proc、pwiki/util 的既有修法）。
    """
    import locate_call_sites as lcs
    import extract_entries as ee

    captured: list[dict] = []

    class _Proc:
        returncode = 1
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured.append(kwargs)
        return _Proc()

    monkeypatch.setattr(lcs.subprocess, "run", fake_run)
    monkeypatch.setattr(ee.subprocess, "run", fake_run)

    lcs._rg_pattern_ok(r"\btrack\s*\(")          # 预检调用同样固定解码
    lcs.rg_search_multi([r"\btrack\s*\("], tmp_path)  # 组合全树扫描
    ee._rg(r"\btrack\s*\(", tmp_path)                 # 审计侧 rg 调用
    assert captured, "应至少捕获一次 subprocess.run 调用"
    for kwargs in captured:
        assert kwargs.get("encoding") == "utf-8", kwargs
        assert kwargs.get("errors") == "replace", kwargs


@rg
def test_f12_c_locale_locate_survives_chinese_output(tmp_path: Path):
    """LC_ALL=C 下 rg 输出中文：第 3 步不再因解码崩溃退出 1。

    修复前：subprocess.run 以 ascii 严格解码 rg 的 UTF-8 输出 →
    UnicodeDecodeError 被裸 except 吞掉 → 整步"执行失败"。
    """
    art = tmp_path / ".agent" / "sensors-analyze"
    art.mkdir(parents=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.js").write_text(
        'trackEvent("首页埋点");\n', encoding="utf-8")
    (art / "entry.txt").write_text("\\btrackEvent\\s*\\(\n# CONFIRMED\n",
                                   encoding="utf-8")

    proc = _run("locate_call_sites.py",
                "--entry", ".agent/sensors-analyze/entry.txt",
                "--root", ".", "--out", ".agent/sensors-analyze/cs.json",
                cwd=tmp_path, env=_c_locale_env())
    assert proc.returncode == 0, proc.stdout
    sites = json.loads((art / "cs.json").read_text(encoding="utf-8"))
    assert [s["location"] for s in sites] == ["src/a.js:1-1"]
    log = (art / "cs.log").read_text(encoding="utf-8")
    assert "UnicodeDecodeError" not in log


@rg
def test_f12_c_locale_audit_survives_chinese_output(tmp_path: Path):
    """LC_ALL=C 下 rg 输出中文：--audit 的 _rg 路径不再解码崩溃。"""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.js").write_text(
        'trackEvent("首页埋点");\n', encoding="utf-8")
    _write_sensors(tmp_path / "s.json", r"\btrackEvent\s*\(")

    proc = _run("extract_entries.py", "--sensors", "s.json",
                "--out", "e.txt", "--audit", "--root", ".",
                cwd=tmp_path, env=_c_locale_env())
    assert proc.returncode == 0, proc.stdout  # 审计通过（修复前退出 1）
    log = (tmp_path / "e.log").read_text(encoding="utf-8")
    assert "UnicodeDecodeError" not in log
    assert "全部通过" in log


# ---------- F13 ----------

@rg
def test_f13_audit_rejected_regex_falls_back_not_false_alarm(tmp_path: Path):
    """rg 拒绝 lookahead 正则时回退纯 Python 并留痕，不再虚假 exit 3。

    修复前：_rg 对退出码 2 静默返回 [] → 覆盖面为空 → 所有 SDK API 调用
    点报 uncovered → 虚假 audit_issues=True 且日志无解释。
    夹具里 dist/bundle.js 同时验证：审计与第 3 步扫描排除同一目录集
    （修复前 _rg 无排除参数，dist 产物会进 uncovered 误报）。
    sensors.json 按真实工作流放 .agent/ 产物目录（本就在排除集内）。
    """
    art = tmp_path / ".agent" / "sensors-analyze"
    art.mkdir(parents=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "dist").mkdir()
    (tmp_path / "src" / "a.js").write_text(
        'sensors.track("首页事件");\n', encoding="utf-8")
    (tmp_path / "dist" / "bundle.js").write_text(
        'sensors.track("decoy");\n', encoding="utf-8")
    _write_sensors(art / "s.json", r"sensors\.track(?=.*事件)",
                   global_sdk_apis=[{"name": "sensors.track", "purpose": "send"}])

    proc = _run("extract_entries.py", "--sensors", ".agent/sensors-analyze/s.json",
                "--out", "e.txt", "--audit", "--root", ".", cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout  # 修复前为 3（虚假遗漏告警）
    log = (tmp_path / "e.log").read_text(encoding="utf-8")
    # 回退必须留痕，用户能看到原因而不是被无解释地逐回第 1 步
    assert "回退纯 Python" in log
    assert "sensors" in log
    # dist/ 产物目录与第 3 步扫描同规则排除，不进审计结论
    assert "bundle.js" not in log
    assert "全部通过" in log
    assert (tmp_path / "e.txt").exists()


def test_f13_py_scan_skips_excluded_dirs(tmp_path: Path):
    """纯 Python 回退扫描同样排除 EXCLUDED_DIRS——两种模式文件集可比。"""
    import extract_entries as ee
    (tmp_path / "src").mkdir()
    (tmp_path / "dist").mkdir()
    (tmp_path / "src" / "a.js").write_text('trackEvent("x");\n', encoding="utf-8")
    (tmp_path / "dist" / "bundle.js").write_text('trackEvent("y");\n', encoding="utf-8")
    hits = ee._py_scan(r"\btrackEvent\s*\(", tmp_path, {".js"})
    assert hits == ["src/a.js:1"]


# ---------- F14 ----------

F14_CSV = ("page_name,first_biz_id,second_biz_id,first_biz_name,"
           "second_biz_name,event,file\n"
           "首页,1,2,3,4,pageView,src/a.js:1,1\n")


def _f14_fixture(tmp_path: Path) -> Path:
    """file 列 src/a.js:1,1 未加引号 → 8 字段 vs 7 表头（溢出值 '1'）。"""
    art = tmp_path / ".agent" / "sensors-analyze"
    art.mkdir(parents=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.js").write_text('trackEvent("pageView");\n',
                                           encoding="utf-8")
    (art / "call_sites.json").write_text(json.dumps([
        {"id": 1, "file": "src/a.js", "start_line": 1, "end_line": 1,
         "location": "src/a.js:1-1", "patterns": ["p"], "snippet": "x"}]),
        encoding="utf-8")
    (art / "sensors.csv").write_text(F14_CSV, encoding="utf-8")
    return art


def test_f14_malformed_overflow_preserved_with_backup(tmp_path: Path):
    """5.5 覆写不销毁证据：先落 .bak 备份，溢出值并回 file 列保真。"""
    art = _f14_fixture(tmp_path)
    proc = _run("review_report.py", "--root", ".", cwd=tmp_path)
    assert proc.returncode == 2  # 畸形行被标记（正常工作路径）

    # 备份与原始文件逐字节一致（人工修复/恢复的依据）
    backup = art / "sensors.csv.bak"
    assert backup.exists()
    assert backup.read_text(encoding="utf-8") == F14_CSV

    # 覆写后的 CSV：溢出值并回 file 列（不再截断成 src/a.js:1），
    # 且 illegal_reason 指引人工查阅备份
    with (art / "sensors.csv").open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["file"] == "src/a.js:1,1"
    assert "字段数多于表头" in rows[0]["illegal_reason"]
    assert "sensors.csv.bak" in rows[0]["illegal_reason"]


def test_f14_report_manager_archives_full_value(tmp_path: Path):
    """第 6 步归档的是保真值：不再把截断的 'src/a.js:1' 固化进归档历史。"""
    art = _f14_fixture(tmp_path)
    assert _run("review_report.py", "--root", ".", cwd=tmp_path).returncode == 2
    proc = _run("report_manager.py", cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout  # 5.5 修复后归档无字段数错误
    archives = list((art / "reports").glob("sensors_*.csv"))
    assert len(archives) == 1
    archived = archives[0].read_text(encoding="utf-8")
    assert '"src/a.js:1,1"' in archived  # 完整值被归档
    assert 'src/a.js:1,' not in archived.replace('"src/a.js:1,1"', "")


# ---------- F38 ----------

@rg
def test_f38_combined_scan_single_tree_sweep(monkeypatch, tmp_path: Path):
    """多正则共享一次全树 rg 扫描；坏正则预检拆出，好正则仍单次扫描。"""
    import locate_call_sites as lcs
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.js").write_text(
        'trackEvent("a");\nsensors.track("b");\n', encoding="utf-8")

    sweeps: list[list[str]] = []
    real_run = subprocess.run

    def counting_run(cmd, **kwargs):
        if cmd[-1] == ".":  # 以 "." 为搜索根 = 一次全树扫描
            sweeps.append(cmd)
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(lcs.subprocess, "run", counting_run)

    good1 = r"\btrackEvent\s*\("
    good2 = r"\bsensors\.track\s*\("
    bad_pat = r'trackEvent\("(?=a)'  # lookahead：Rust regex 拒绝

    union, bad = lcs.rg_search_multi([good1, good2], tmp_path)
    assert len(sweeps) == 1  # 两个正则 → 一次扫描（修复前逐正则各扫一次）
    assert bad == []
    assert set(union) == {("src/a.js", 1), ("src/a.js", 2)}

    sweeps.clear()
    union, bad = lcs.rg_search_multi([good1, bad_pat, good2], tmp_path)
    assert len(sweeps) == 1  # 坏正则被预检拆出后，好正则仍合并为一次扫描
    assert bad == [bad_pat]
    assert set(union) == {("src/a.js", 1), ("src/a.js", 2)}


@rg
def test_f38_rg_mode_equals_fallback_mode_multi_pattern(tmp_path: Path):
    """合并扫描 + Python 归因的输出与纯 Python 回退模式完全一致。

    覆盖：多文件多正则、同一行被多正则命中（patterns 归因合并）、
    多行调用的 end_line 计算、被拒正则回退、排除目录。
    """
    art = tmp_path / ".agent" / "sensors-analyze"
    art.mkdir(parents=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "dist").mkdir()
    (tmp_path / "src" / "a.js").write_text(
        'trackEvent("a1", {\n  page: "home"\n});\ntrackEvent("a2");\n',
        encoding="utf-8")
    (tmp_path / "src" / "b.js").write_text(
        'sensors.track("事件B");\n', encoding="utf-8")
    (tmp_path / "dist" / "bundle.js").write_text(
        'trackEvent("decoy");\n', encoding="utf-8")
    patterns = [
        r"\bsensors\.track\s*\(",
        r"\btrackEvent\s*\(",
        r'trackEvent\("(?=a)',  # lookahead：rg 拒绝 → 纯 Python 回退
    ]
    (art / "entry.txt").write_text(
        "\n".join(patterns) + "\n# CONFIRMED\n", encoding="utf-8")

    # rg 模式（正常 PATH）
    proc = _run("locate_call_sites.py",
                "--entry", ".agent/sensors-analyze/entry.txt",
                "--root", ".", "--out", ".agent/sensors-analyze/rg.json",
                cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout

    # 纯 Python 回退模式：PATH 只含 python3，shutil.which("rg") 落空
    minbin = tmp_path / "_minbin"
    minbin.mkdir()
    (minbin / "python3").symlink_to(shutil.which("python3"))
    proc = _run("locate_call_sites.py",
                "--entry", ".agent/sensors-analyze/entry.txt",
                "--root", ".", "--out", ".agent/sensors-analyze/fb.json",
                cwd=tmp_path, env={**os.environ, "PATH": str(minbin)})
    assert proc.returncode == 0, proc.stdout

    rg_json = json.loads((art / "rg.json").read_text(encoding="utf-8"))
    fb_json = json.loads((art / "fb.json").read_text(encoding="utf-8"))
    # 形状与语义等价：完整 JSON（id/file/start/end/location/file_csv/
    # patterns/snippet）逐字段一致，含多行调用的 end_line 与归因合并
    assert rg_json == fb_json
    assert [s["location"] for s in fb_json] == [
        "src/a.js:1-3",   # 多行调用：括号平衡扫到第 3 行
        "src/a.js:4-4",
        "src/b.js:1-1",
    ]
    # 同一行被多个正则命中 → patterns 按正则顺序合并（归因不丢不重）
    assert fb_json[0]["patterns"] == [patterns[1], patterns[2]]
    assert fb_json[2]["patterns"] == [patterns[0]]
    assert fb_json[0]["file_csv"] == "src/a.js:1,3"


# ---------- F42 ----------

def test_f42_header_constants_single_source():
    """三个脚本的表头/必填列常量全部引用 utils 单一来源，无本地字面量漂移。"""
    import utils
    import check_report
    import review_report
    import report_manager

    assert check_report.CSV_HEADER is utils.CSV_BASE_HEADER
    assert check_report.CSV_HEADER_WITH_REASON is utils.CSV_FULL_HEADER
    assert check_report.REQUIRED_NONEMPTY is utils.REQUIRED_NONEMPTY
    assert check_report.ALL_CHECK_FIELDS is utils.ALL_CHECK_FIELDS
    assert review_report.CSV_HEADER_WITH_REASON is utils.CSV_FULL_HEADER
    assert review_report.CSV_HEADER_OLD is utils.CSV_BASE_HEADER
    assert review_report.REQUIRED_NONEMPTY is utils.REQUIRED_NONEMPTY
    assert review_report.ALL_CHECK_FIELDS is utils.ALL_CHECK_FIELDS
    assert report_manager.CSV_HEADER is utils.CSV_FULL_HEADER
    assert report_manager.CSV_HEADER_OLD is utils.CSV_BASE_HEADER
    # 单一来源本身自洽
    assert utils.CSV_FULL_HEADER == utils.CSV_BASE_HEADER + ["illegal_reason"]


def test_f42_header_is_expected_tolerates_documented_columns():
    """文档认可的附加列（manual_note）不告警；缺列/乱序/未认可列仍告警。"""
    import utils
    base = list(utils.CSV_BASE_HEADER)
    ok_cases = [
        base,                                            # 第 4 步产出
        base + ["illegal_reason"],                       # 5.5 步后
        base + ["manual_note"],                          # 5.6 步人工加列
        base + ["illegal_reason", "manual_note"],        # 5.5 → 5.6
        base + ["manual_note", "illegal_reason"],        # 5.6 → 5.5 重跑
    ]
    for header in ok_cases:
        assert utils.header_is_expected(header), header
    bad_cases = [
        [],                                   # 空表头
        base[:-1],                            # 缺 file 列
        base[:3] + base[4:],                  # 缺中间列（event）
        ["file"] + base[1:],                  # 乱序
        base + ["rogue_col"],                 # 未认可的附加列
    ]
    for header in bad_cases:
        assert not utils.header_is_expected(header), header


def test_f42_manual_note_no_header_warning(tmp_path: Path):
    """按 SKILL.md 5.6 步加了 manual_note 列的 CSV 跑第 6 步：无表头告警。"""
    art = tmp_path / ".agent" / "sensors-analyze"
    art.mkdir(parents=True)
    header = ("page_name,first_biz_id,second_biz_id,first_biz_name,"
              "second_biz_name,event,file,illegal_reason,manual_note")
    (art / "sensors.csv").write_text(
        header + '\n首页,1,2,3,4,e,"src/a.js:1,1",,page_name 来自路由 meta\n',
        encoding="utf-8")
    proc = _run("report_manager.py", "--diff-only", cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout
    log = (art / "reports" / "report_manager.log").read_text(encoding="utf-8")
    assert "不一致" not in log  # 文档工作流的正确操作不产生噪音告警


def test_f42_manual_note_change_visible_in_diff(tmp_path: Path):
    """附加列的变更要进差异报告（签名覆盖全列，含 manual_note）。"""
    art = tmp_path / ".agent" / "sensors-analyze"
    reports = art / "reports"
    reports.mkdir(parents=True)
    header = ("page_name,first_biz_id,second_biz_id,first_biz_name,"
              "second_biz_name,event,file,illegal_reason,manual_note")
    # 上次归档：manual_note 为空
    (reports / "sensors_20260101_000000.csv").write_text(
        header + '\n首页,1,2,3,4,e,"src/a.js:1,1",,\n', encoding="utf-8")
    # 当前：人工补了复核备注 → 应报 1 处修改且展示 manual_note 变化
    (art / "sensors.csv").write_text(
        header + '\n首页,1,2,3,4,e,"src/a.js:1,1",,page_name 来自路由 meta\n',
        encoding="utf-8")
    proc = _run("report_manager.py", "--diff-only", cwd=tmp_path)
    assert proc.returncode == 2  # 有差异需人工复核
    diff = (reports / "diff_report.txt").read_text(encoding="utf-8")
    assert "修改行: 1" in diff
    assert "manual_note" in diff
    assert "page_name 来自路由 meta" in diff
