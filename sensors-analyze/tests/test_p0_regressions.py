"""P0 回归测试：锁死 2026-10-12 代码审查报告中的三个高严重度缺陷。

H1 check_report 重复行凑数不得 PASS——每个调用点必须至少被一行覆盖；
H2 locate_call_sites rg/回退模式结果一致，rg 不支持的正则回退 Python 而非静默丢弃；
H3 file 列逗号未加引号（字段数多于表头）时 review_report 标记、report_manager 友好报错，均不崩溃。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"


def _run(script: str, *args: str, cwd: Path, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    import os
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / script), *args],
        cwd=str(cwd), capture_output=True, text=True, env=env,
    )


def _write_sites(path: Path, n: int = 2) -> None:
    sites = [
        {"id": i, "file": f"f{i}.js", "start_line": 1, "end_line": 1,
         "location": f"f{i}.js:1-1", "patterns": ["p"], "snippet": ""}
        for i in range(1, n + 1)
    ]
    path.write_text(json.dumps(sites), encoding="utf-8")


# ---------- H1 ----------

CSV_HEADER = ("page_name,first_biz_id,second_biz_id,first_biz_name,"
              "second_biz_name,event,file")


def _run_check(cwd: Path) -> tuple[int, str]:
    proc = _run("check_report.py", "--sites", "call_sites.json",
                "--csv", "sensors.csv", "--out", "out.txt", cwd=cwd)
    report = (cwd / "out.txt").read_text(encoding="utf-8")
    return proc.returncode, report


def test_h1_duplicate_rows_cannot_cover_missing_site(tmp_path: Path):
    """2 个调用点、2 行全指向同一处 → 必须 FAIL 并指出遗漏的调用点。"""
    _write_sites(tmp_path / "call_sites.json", n=2)
    (tmp_path / "sensors.csv").write_text(
        CSV_HEADER + '\nx,1,2,3,4,e,"f1.js:1,1"\nx,1,2,3,4,e,"f1.js:1,1"\n',
        encoding="utf-8")
    code, report = _run_check(tmp_path)
    assert code == 2
    assert "f2.js:1-1" in report  # 明确指出遗漏的调用点


def test_h1_full_coverage_still_passes(tmp_path: Path):
    """每个调用点各一行 → PASS（不因新增覆盖校验而误伤正常流程）。"""
    _write_sites(tmp_path / "call_sites.json", n=2)
    (tmp_path / "sensors.csv").write_text(
        CSV_HEADER + '\nx,1,2,3,4,e,"f1.js:1,1"\nx,1,2,3,4,e,"f2.js:1,1"\n',
        encoding="utf-8")
    code, report = _run_check(tmp_path)
    assert code == 0
    assert "PASS" in report


# ---------- H2 ----------

def _make_root(tmp_path: Path, pattern: str) -> Path:
    """夹具：src 源码 + node_modules/.agent 中各放一个应被排除的命中。"""
    (tmp_path / "src").mkdir()
    (tmp_path / "node_modules" / "pkg").mkdir(parents=True)
    art = tmp_path / ".agent" / "sensors-analyze"
    art.mkdir(parents=True)
    (tmp_path / "src" / "home.js").write_text('trackEvent("a");\n', encoding="utf-8")
    (tmp_path / "node_modules" / "pkg" / "d.js").write_text('trackEvent("b");\n', encoding="utf-8")
    (art / "leak.js").write_text('trackEvent("c");\n', encoding="utf-8")
    (art / "entry.txt").write_text(f"{pattern}\n# CONFIRMED\n", encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize("pattern", [
    r"\btrackEvent\s*\(",          # 常规正则
    r"trackEvent(?=\s*\()",        # lookahead：rg 不支持，应回退 Python
])
def test_h2_excluded_dirs_not_scanned(tmp_path: Path, pattern: str):
    """rg 模式：node_modules 与 .agent 产物目录不进结果；lookahead 正则不被静默丢弃。"""
    root = _make_root(tmp_path, pattern)
    proc = _run("locate_call_sites.py", "--entry", ".agent/sensors-analyze/entry.txt",
                "--root", ".", "--out", ".agent/sensors-analyze/cs.json", cwd=root)
    assert proc.returncode == 0, proc.stdout
    sites = json.loads((root / ".agent" / "sensors-analyze" / "cs.json")
                       .read_text(encoding="utf-8"))
    locations = [s["location"] for s in sites]
    assert locations == ["src/home.js:1-1"]


def test_h2_fallback_mode_matches_rg_mode(tmp_path: Path):
    """纯 Python 回退（PATH 无 rg）与 rg 模式产出一致。"""
    import shutil
    if not shutil.which("rg"):
        pytest.skip("环境无 rg，跳过模式一致性对照")

    root = _make_root(tmp_path, r"\btrackEvent\s*\(")

    proc = _run("locate_call_sites.py", "--entry", ".agent/sensors-analyze/entry.txt",
                "--root", ".", "--out", ".agent/sensors-analyze/rg.json", cwd=root)
    assert proc.returncode == 0

    # 构造只含 python3 可执行文件的 PATH，使 shutil.which("rg") 落空 → 回退模式
    minbin = root / "_minbin"
    minbin.mkdir()
    for tool in ("python3", "which"):  # which 供旧实现/兼容使用
        link = minbin / tool
        link.symlink_to(shutil.which(tool))
    proc = _run("locate_call_sites.py", "--entry", ".agent/sensors-analyze/entry.txt",
                "--root", ".", "--out", ".agent/sensors-analyze/fb.json",
                cwd=root, env_extra={"PATH": str(minbin)})
    assert proc.returncode == 0, proc.stdout

    art = root / ".agent" / "sensors-analyze"
    rg = json.loads((art / "rg.json").read_text(encoding="utf-8"))
    fb = json.loads((art / "fb.json").read_text(encoding="utf-8"))
    assert [s["location"] for s in fb] == [s["location"] for s in rg] == ["src/home.js:1-1"]


def test_h2_zero_hits_routes_back_to_step2(tmp_path: Path):
    """0 命中时 next_step 应引导回第 2 步，而非进入第 4 步。"""
    root = _make_root(tmp_path, r"noSuchEntry123\s*\(")
    proc = _run("locate_call_sites.py", "--entry", ".agent/sensors-analyze/entry.txt",
                "--root", ".", "--out", ".agent/sensors-analyze/cs.json", cwd=root)
    assert proc.returncode == 0
    assert proc.stdout.startswith("next_step:")
    assert "第 2 步" in proc.stdout
    assert "第 4 步" not in proc.stdout


# ---------- H3 ----------

def _write_malformed_csv(cwd: Path) -> None:
    art = cwd / ".agent" / "sensors-analyze"
    art.mkdir(parents=True, exist_ok=True)
    (art / "call_sites.json").write_text(json.dumps([
        {"id": 1, "file": "src/a.js", "start_line": 1, "end_line": 1,
         "location": "src/a.js:1-1", "patterns": ["p"], "snippet": "x"}]), encoding="utf-8")
    (cwd / "src").mkdir(exist_ok=True)
    (cwd / "src" / "a.js").write_text('trackEvent("e");\n', encoding="utf-8")
    # file 列 src/a.js:1,1 未加引号 → 8 字段 vs 7 表头
    (art / "sensors.csv").write_text(
        CSV_HEADER + "\nh,1,2,3,4,e,src/a.js:1,1\n", encoding="utf-8")


def test_h3_review_report_flags_instead_of_crash(tmp_path: Path):
    _write_malformed_csv(tmp_path)
    proc = _run("review_report.py", "--root", ".", cwd=tmp_path)
    assert proc.returncode == 2  # 有标记行，属正常工作路径而非异常(1)
    csv_text = (tmp_path / ".agent" / "sensors-analyze" / "sensors.csv").read_text(encoding="utf-8")
    assert "字段数多于表头" in csv_text


def test_h3_report_manager_friendly_error_instead_of_crash(tmp_path: Path):
    _write_malformed_csv(tmp_path)
    proc = _run("report_manager.py", cwd=tmp_path)
    assert proc.returncode == 1
    log = (tmp_path / ".agent" / "sensors-analyze" / "reports" / "report_manager.log")\
        .read_text(encoding="utf-8")
    assert "字段数多于表头" in log
    assert "AttributeError" not in log
