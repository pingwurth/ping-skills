"""P1 回归测试：2026-10-12 审查报告 M1/M2/M3/M5。

M1 extract_entries 内置 stdlib schema 校验（schema_lite），违规拒绝且 next_step 指引回第 1 步；
M2 report_manager 归档消息引用真实 archive_path，--diff-only 不谎称已归档；
M3 report_manager CSV 缺失仍走 next_step 通道；
M5 check_report 占位符检测覆盖尖括号/模板插值/整值占位三种形式。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"

# 最小合法 sensors.json（满足 sensors.schema.json 全部约束）
GOOD_SENSORS = {
    "meta": {
        "project_path": ".", "language": "js",
        "analyzed_at": "2026-10-12T00:00:00Z",
        "sdk_summary": "s", "global_sdk_apis": [],
    },
    "paradigms": [{
        "id": "p1", "name": "n", "category": "code",
        "reference_location": {"file": "a.js", "start_line": 1},
        "call_chain": [{
            "order": 1, "layer": "page", "symbol": "s", "description": "d",
            "data_flow": [],
        }],
        "entry_functions": [{
            "name": "trackEvent", "kind": "wrapper",
            "location": {"file": "a.js", "start_line": 1},
            "grep_pattern": r"\btrackEvent\s*\(",
        }],
        "sdk_apis": [{"name": "sensors.track", "purpose": "send"}],
    }],
}

CSV_HEADER_FULL = ("page_name,first_biz_id,second_biz_id,first_biz_name,"
                   "second_biz_name,event,file,illegal_reason")


def _run(script: str, *args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / script), *args],
        cwd=str(cwd), capture_output=True, text=True,
    )


# ---------- M1 ----------

def test_m1_schema_validation_rejects_incomplete_sensors_json(tmp_path: Path):
    """缺 meta/必需字段/空 paradigms → exit 1 + next_step 指引回第 1 步 + 日志列出违规项。"""
    (tmp_path / "bad.json").write_text(
        json.dumps({"paradigms": [], "extra": 1}), encoding="utf-8")
    proc = _run("extract_entries.py", "--sensors", "bad.json",
                "--out", "entry.txt", cwd=tmp_path)
    assert proc.returncode == 1
    assert "不符合 schema" in proc.stdout
    assert "第 1 步" in proc.stdout
    log = (tmp_path / "entry.log").read_text(encoding="utf-8")
    assert "缺少必需字段" in log
    assert "不允许的额外字段" in log


def test_m1_schema_validation_accepts_valid_sensors_json(tmp_path: Path):
    (tmp_path / "good.json").write_text(
        json.dumps(GOOD_SENSORS, ensure_ascii=False), encoding="utf-8")
    proc = _run("extract_entries.py", "--sensors", "good.json",
                "--out", "entry.txt", cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout
    assert "schema 校验通过" in (tmp_path / "entry.log").read_text(encoding="utf-8")


def test_m1_schema_lite_semantics():
    """schema_lite 关键语义：bool 不是 integer；enum 拒绝；$ref 解析。"""
    from schema_lite import validate
    schema = {
        "definitions": {"loc": {"type": "object", "required": ["file"],
                                 "additionalProperties": False,
                                 "properties": {"file": {"type": "string"}}}},
        "type": "object", "required": ["pos"],
        "properties": {"pos": {"$ref": "#/definitions/loc"}},
    }
    assert validate({"pos": {"file": "a"}}, schema) == []
    # bool-as-int：integer 类型不能接受 True
    int_schema = {"type": "object", "properties": {"n": {"type": "integer"}}}
    errs = validate({"n": True}, int_schema)
    assert errs, "True 不应被当作合法 integer"
    enum_schema = {"type": "object", "properties": {"k": {"type": "string",
                                                          "enum": ["code", "hybrid"]}}}
    errs = validate({"k": "visual"}, enum_schema)
    assert errs and "枚举" in errs[0]


# ---------- M2 / M3 ----------

def _write_csv(root: Path, text: str) -> None:
    art = root / ".agent" / "sensors-analyze"
    art.mkdir(parents=True, exist_ok=True)
    (art / "sensors.csv").write_text(text, encoding="utf-8")


def test_m2_diff_only_no_previous_does_not_claim_archived(tmp_path: Path):
    _write_csv(tmp_path, CSV_HEADER_FULL + '\nh,1,2,3,4,e,"a.js:1,1",\n')
    proc = _run("report_manager.py", "--diff-only", cwd=tmp_path)
    assert proc.returncode == 0
    assert "仅比对完成" in proc.stdout
    assert "已保存到" not in proc.stdout  # 未归档不得谎称已保存
    assert not list((tmp_path / ".agent" / "sensors-analyze" / "reports")
                    .glob("sensors_*.csv"))


def test_m2_archive_message_points_to_real_file(tmp_path: Path):
    _write_csv(tmp_path, CSV_HEADER_FULL + '\nh,1,2,3,4,e,"a.js:1,1",\n')
    proc = _run("report_manager.py", cwd=tmp_path)
    assert proc.returncode == 0
    line = proc.stdout.strip()
    assert "已保存到" in line
    # next_step 里的归档路径必须是磁盘上真实存在的文件（不能是二次生成的时间戳）
    claimed = line.rsplit("已保存到 ", 1)[1].strip()
    assert (tmp_path / claimed).exists(), f"消息指向不存在的文件: {claimed}"


def test_m2_diff_only_with_previous_and_no_changes(tmp_path: Path):
    csv_text = CSV_HEADER_FULL + '\nh,1,2,3,4,e,"a.js:1,1",\n'
    _write_csv(tmp_path, csv_text)
    assert _run("report_manager.py", cwd=tmp_path).returncode == 0  # 先归档一次
    proc = _run("report_manager.py", "--diff-only", cwd=tmp_path)
    assert proc.returncode == 0
    assert "未归档" in proc.stdout
    archives = list((tmp_path / ".agent" / "sensors-analyze" / "reports")
                    .glob("sensors_*.csv"))
    assert len(archives) == 1  # --diff-only 不新增归档


def test_m3_missing_csv_still_emits_next_step(tmp_path: Path):
    (tmp_path / ".agent" / "sensors-analyze").mkdir(parents=True)
    proc = _run("report_manager.py", cwd=tmp_path)
    assert proc.returncode == 1
    assert proc.stdout.startswith("next_step:")  # 不再绕过契约裸写 stderr
    assert "找不到 CSV" in proc.stdout


# ---------- M5 ----------

def test_m5_placeholder_forms_all_fail(tmp_path: Path):
    _write = tmp_path / "call_sites.json"
    _write.write_text(json.dumps([
        {"id": i, "file": "a.js", "start_line": i, "end_line": i,
         "location": f"a.js:{i}-{i}", "patterns": ["p"], "snippet": ""}
        for i in range(1, 6)
    ]), encoding="utf-8")
    header = ("page_name,first_biz_id,second_biz_id,first_biz_name,"
              "second_biz_name,event,file")
    rows = [
        f'<待定>,1,2,3,4,e,"a.js:1,1"',        # 尖括号中文占位
        'home,TODO,2,3,4,e,"a.js:2,2"',        # 整值 TODO
        'home,1,{{second_id}},3,4,e,"a.js:3,3"',  # 模板插值
        'home,1,2,N/A,4,e,"a.js:4,4"',         # 整值 N/A
        'home,1,2,3,4,e,"a.js:5,5"',           # 干净行（不得误报）
    ]
    (tmp_path / "sensors.csv").write_text(
        header + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
    proc = _run("check_report.py", "--sites", "call_sites.json",
                "--csv", "sensors.csv", "--out", "out.txt", cwd=tmp_path)
    assert proc.returncode == 2
    report = (tmp_path / "out.txt").read_text(encoding="utf-8")
    for needle in ("'<待定>'", "'TODO'", "'{{second_id}}'", "'N/A'"):
        assert needle in report, f"漏检占位符 {needle}"
    assert report.count("包含动态占位符") == 4  # 干净行不计入
