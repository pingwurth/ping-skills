"""P3 回归测试：2026-10-12 审查报告 L1/L2/L3/L5/L6/L7/L8/L10。

L1 setup_logging 幂等（不叠加 handler、不串日志）；
L2 audit_uncovered 删除未使用的 have_rg 形参；
L3 --audit 发现问题时退出码 3（entry.txt 仍生成）；
L5 括号平衡扫描跳过 /* ... */ 块注释；
L6 模板字符串字面量跨行可提取；
L7 空字段推断提示为提示性线索，且过滤超长字面量；
L8 5.5 重跑保留 manual_note 等额外列；
L10 call_sites.json 输出与 CSV 同格式的 file_csv 字段。
"""
from __future__ import annotations

import inspect
import json
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))


def _run(script: str, *args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / script), *args],
        cwd=str(cwd), capture_output=True, text=True,
    )


# ---------- L1 ----------

def test_l1_setup_logging_idempotent(tmp_path: Path):
    import utils
    log1, log2 = tmp_path / "a.log", tmp_path / "b.log"
    logger = utils.setup_logging("p3_l1", log1)
    logger.info("first")
    # 同名二次配置：不得叠加 handler（否则日志行成倍重复）
    logger = utils.setup_logging("p3_l1", log2)
    assert len(logger.handlers) == 1
    logger.info("second")
    assert log2.read_text(encoding="utf-8").count("second") == 1
    assert "second" not in log1.read_text(encoding="utf-8")
    utils._restore_stderr()  # 清理重定向，不污染同进程后续测试


# ---------- L2 ----------

def test_l2_audit_uncovered_has_no_dead_param():
    import extract_entries
    params = inspect.signature(extract_entries.audit_uncovered).parameters
    assert "have_rg" not in params


# ---------- L3 ----------

VALID_SENSORS = {
    "meta": {
        "project_path": ".", "language": "js",
        "analyzed_at": "2026-10-12T00:00:00Z",
        "sdk_summary": "s", "global_sdk_apis": [],
    },
    "paradigms": [{
        "id": "p1", "name": "n", "category": "code",
        "reference_location": {"file": "src/home.js", "start_line": 1},
        "call_chain": [{
            "order": 1, "layer": "page", "symbol": "s",
            "description": "d", "data_flow": [],
        }],
        "entry_functions": [{
            "name": "trackEvent", "kind": "wrapper",
            "location": {"file": "src/home.js", "start_line": 1},
            "grep_pattern": r"\btrackEvent\s*\(",
        }],
        "sdk_apis": [{"name": "sensors.track", "purpose": "send"}],
    }],
}


def test_l3_audit_issues_exit_code_3(tmp_path: Path):
    """代码里有 sensorsDataInit 但 sensors.json 未收录 → --audit 退出码 3，entry.txt 仍生成。"""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "init.js").write_text(
        'function sensorsDataInit() {\n  sensors.quick("autoTrack");\n}\n',
        encoding="utf-8")
    (tmp_path / "s.json").write_text(
        json.dumps(VALID_SENSORS, ensure_ascii=False), encoding="utf-8")

    proc = _run("extract_entries.py", "--sensors", "s.json",
                "--out", "e.txt", "--audit", "--root", ".", cwd=tmp_path)
    assert proc.returncode == 3
    assert "修正 sensors.json" in proc.stdout
    assert (tmp_path / "e.txt").exists()  # 仍生成供预览
    log = (tmp_path / "e.log").read_text(encoding="utf-8")
    assert "疑似埋点函数" in log and "sensorsDataInit" in log


# ---------- L5 ----------

def test_l5_block_comments_ignored_in_bracket_scan(tmp_path: Path):
    from locate_call_sites import compute_body_end, compute_end_line

    f = tmp_path / "w.js"
    f.write_text('function t(a) { /* } */\n  x(a);\n}\n', encoding="utf-8")
    # 旧实现会把注释里的 '}' 当函数体结束（返回 1）；正确答案 3
    assert compute_body_end(f, 1) == 3

    lines = ["foo(", "  /* ) */", "  bar)", "after"]
    # 旧实现把注释里的 ')' 当调用结束（返回 2）；正确答案 3
    assert compute_end_line(lines, 1, 0) == 3


# ---------- L6 ----------

def test_l6_cross_line_template_literal_extracted():
    from review_report import extract_string_literals
    lits = extract_string_literals(["const s = `hello", "world`;"])
    assert "hello\nworld" in lits  # 跨行模板的静态部分被整体提取


# ---------- L7 ----------

def test_l7_infer_hint_is_advisory_and_filters_long_literals():
    from review_report import check_empty_field_can_infer
    # 只有超长字面量 → 无线索，不产生噪音
    assert check_empty_field_can_infer("page_name", {"x" * 80}) is None
    r = check_empty_field_can_infer("page_name", {"home", "y" * 80})
    assert r is not None
    assert "提示性线索" in r and "home" in r and "y" * 80 not in r


# ---------- L8 ----------

def test_l8_manual_note_preserved_on_rerun(tmp_path: Path):
    art = tmp_path / ".agent" / "sensors-analyze"
    art.mkdir(parents=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.js").write_text('trackEvent("e");\n', encoding="utf-8")
    (art / "call_sites.json").write_text(json.dumps([
        {"id": 1, "file": "src/a.js", "start_line": 1, "end_line": 1,
         "location": "src/a.js:1-1", "patterns": ["p"], "snippet": "x"}]),
        encoding="utf-8")
    # page_name 留空（会触发 illegal_reason）+ 5.6 步留下的人工备注
    (art / "sensors.csv").write_text(
        "page_name,first_biz_id,second_biz_id,first_biz_name,second_biz_name,"
        "event,file,manual_note\n"
        ',1,2,3,4,e,"src/a.js:1,1",人工确认：page_name 来自路由 meta\n',
        encoding="utf-8")

    proc = _run("review_report.py", "--root", ".", cwd=tmp_path)
    assert proc.returncode == 2  # 有标记行（page_name 为空）
    csv_text = (art / "sensors.csv").read_text(encoding="utf-8")
    # illegal_reason 由脚本重算追加，manual_note 原样保留
    assert "manual_note" in csv_text.splitlines()[0]
    assert "人工确认：page_name 来自路由 meta" in csv_text
    assert "illegal_reason" in csv_text.splitlines()[0]


# ---------- L10 ----------

def test_l10_call_sites_have_file_csv_field(tmp_path: Path):
    art = tmp_path / ".agent" / "sensors-analyze"
    art.mkdir(parents=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "home.js").write_text('trackEvent("a");\n', encoding="utf-8")
    (art / "entry.txt").write_text("\\btrackEvent\\s*\\(\n# CONFIRMED\n",
                                   encoding="utf-8")
    proc = _run("locate_call_sites.py",
                "--entry", ".agent/sensors-analyze/entry.txt",
                "--root", ".", "--out", ".agent/sensors-analyze/cs.json",
                cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout
    sites = json.loads((art / "cs.json").read_text(encoding="utf-8"))
    assert sites[0]["file_csv"] == "src/home.js:1,1"  # 与 CSV file 列同格式
    assert sites[0]["location"] == "src/home.js:1-1"  # 原连字符格式保留
