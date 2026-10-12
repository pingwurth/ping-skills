"""回归测试：所有工作产物默认落在 .agent/sensors-analyze/ 下。

覆盖三点：
1. 五个脚本的 CLI 默认路径均指向 .agent/sensors-analyze/（help 文本含默认值）；
2. review_report / report_manager 不再本地遮蔽 utils 的 setup_logging / next_step
   （历史缺陷：本地单参数定义导致 TypeError，第 5.5 / 6 步脚本无法运行）；
3. 端到端最小流程：第 2 → 6 步全部产物（含日志、归档）都在
   <工作目录>/.agent/sensors-analyze/ 内，工作目录根无散落文件。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from utils import ARTIFACT_DIR

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"

# 各脚本 --help 中应出现、证明默认值已指向产物目录的文件名
HELP_EXPECTATIONS = {
    "extract_entries.py": "entry.txt",
    "locate_call_sites.py": "call_sites.json",
    "check_report.py": "check_report.txt",
    "review_report.py": "review_report.txt",
    "report_manager.py": "sensors.csv",
}


@pytest.mark.parametrize("script,expected", sorted(HELP_EXPECTATIONS.items()))
def test_help_documents_artifact_dir_defaults(script: str, expected: str):
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / script), "--help"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0
    assert ".agent/sensors-analyze/" in proc.stdout
    assert expected in proc.stdout


def test_no_local_shadowing_of_utils_helpers():
    """utils 模块注释约定：所有脚本统一从此导入 setup_logging / next_step。"""
    import report_manager
    import review_report
    import utils

    assert review_report.setup_logging is utils.setup_logging
    assert review_report.next_step is utils.next_step
    assert report_manager.setup_logging is utils.setup_logging
    assert report_manager.next_step is utils.next_step


def _write_fixture(root: Path) -> None:
    """构造最小目标库：一个 trackEvent 调用 + 对应 sensors.json。"""
    src = root / "src"
    src.mkdir(exist_ok=True)
    (src / "home.js").write_text(
        'function onPageEnter() {\n'
        '  trackEvent("page_enter", { page_name: "home" });\n'
        '}\n',
        encoding="utf-8",
    )
    # signature 刻意写成不含 "trackEvent("，避免 sensors.json 自身被当作调用点
    sensors = {
        "meta": {
            "project_path": ".", "language": "js",
            "analyzed_at": "2026-10-12T00:00:00Z",
            "sdk_summary": "smoke", "global_sdk_apis": [],
        },
        "paradigms": [{
            "id": "direct-call", "name": "直调", "category": "code",
            "reference_location": {"file": "src/home.js", "start_line": 2},
            "call_chain": [{
                "order": 1, "layer": "page", "symbol": "onPageEnter",
                "location": {"file": "src/home.js", "start_line": 1, "end_line": 3},
                "description": "页面入口调用 trackEvent", "data_flow": [],
            }],
            "entry_functions": [{
                "name": "trackEvent", "kind": "wrapper",
                "grep_pattern": r"\btrackEvent\s*\(",
                "signature": "trackEvent: name, props",
                "location": {"file": "src/home.js", "start_line": 1, "end_line": 1},
            }],
            "sdk_apis": [{"name": "trackEvent", "purpose": "send event"}],
        }],
    }
    (root / "sensors.json").write_text(
        json.dumps(sensors, ensure_ascii=False), encoding="utf-8")


def _run(script: str, *args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / script), *args],
        cwd=str(cwd), capture_output=True, text=True,
    )


def test_full_pipeline_artifacts_land_in_agent_dir(tmp_path: Path):
    """第 2 → 6 步全链路：所有产物都落在 .agent/sensors-analyze/ 内。"""
    _write_fixture(tmp_path)
    art = tmp_path / ARTIFACT_DIR

    # 第 2 步：默认 --out 应自动创建产物目录
    proc = _run("extract_entries.py", "--sensors", "sensors.json", cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    entry = art / "entry.txt"
    assert entry.exists()
    assert (art / "entry.log").exists()

    # 第 3 步：人工确认后，默认 --entry/--out/--sensors 均在产物目录
    entry.write_text(entry.read_text(encoding="utf-8") + "# CONFIRMED\n",
                     encoding="utf-8")
    proc = _run("locate_call_sites.py", "--root", ".", cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    call_sites = art / "call_sites.json"
    assert call_sites.exists()
    sites = json.loads(call_sites.read_text(encoding="utf-8"))
    assert [s["location"] for s in sites] == ["src/home.js:2-2"]

    # 第 4 步：LLM 产物 CSV 也写进产物目录（file 列含逗号需引号）
    (art / "sensors.csv").write_text(
        "page_name,first_biz_id,second_biz_id,first_biz_name,second_biz_name,event,file\n"
        'home,b1,b2,fb,sb,page_enter,"src/home.js:2,2"\n',
        encoding="utf-8",
    )

    # 第 5 步：默认 --sites/--csv/--out，报告与 resolution_report 同目录
    proc = _run("check_report.py", cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr  # PASS
    assert (art / "check_report.txt").exists()
    assert (art / "resolution_report.csv").exists()

    # 第 5.5 步：默认 --csv/--sites/--out（返回 2 = 有标记行，属正常工作路径）
    proc = _run("review_report.py", "--root", ".", cwd=tmp_path)
    assert proc.returncode == 2, proc.stderr
    assert (art / "review_report.txt").exists()
    assert "illegal_reason" in (art / "sensors.csv").read_text(encoding="utf-8")

    # 第 6 步：默认 --csv 与 --reports-dir，归档进 reports/ 子目录
    proc = _run("report_manager.py", cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    archives = list((art / "reports").glob("sensors_*.csv"))
    assert len(archives) == 1
    assert (art / "reports" / "diff_report.txt").exists()

    # 工作目录根无散落产物（仅夹具自身与目标代码）
    stray = {p.name for p in tmp_path.iterdir()} - {
        ".agent", "src", "sensors.json"}
    assert not stray, f"根目录出现散落产物: {stray}"
