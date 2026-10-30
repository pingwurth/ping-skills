"""CLI 入口冒烟测试: 单入口运行、恰一个协议块、退出码契约。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from crp import config

SKILL_ROOT = Path(__file__).resolve().parents[1]
ENTRY = SKILL_ROOT / "scripts" / "crp.py"


def run_entry(args: list[str], env: dict | None = None) -> subprocess.CompletedProcess:
    import os
    environ = dict(os.environ)
    if env:
        environ.update(env)
    return subprocess.run(
        [sys.executable, str(ENTRY), *args],
        capture_output=True, text=True, timeout=60, env=environ,
    )


def parse_block(stdout: str) -> dict:
    assert stdout.count(config.NEXT_STEP_BEGIN_MARKER) == 1, "必须恰输出一个协议块"
    assert stdout.count(config.NEXT_STEP_END_MARKER) == 1
    begin = stdout.index(config.NEXT_STEP_BEGIN_MARKER) + len(config.NEXT_STEP_BEGIN_MARKER)
    end = stdout.index(config.NEXT_STEP_END_MARKER)
    return json.loads(stdout[begin:end])


def test_runs_空仓库输出空状态块(tmp_path):
    proc = run_entry(["runs"], env={config.CRP_HOME_ENV: str(tmp_path / "home")})
    payload = parse_block(proc.stdout)
    assert proc.returncode == payload["exit_code"] == config.EXIT_OK
    assert payload["status"] == "empty"
    assert payload["next_step"]["type"] == "finish"
    assert "report" not in payload["next_step"], "信息型命令的 finish 不携带 report"


def test_status_无运行(tmp_path):
    proc = run_entry(["status"], env={config.CRP_HOME_ENV: str(tmp_path / "home")})
    payload = parse_block(proc.stdout)
    assert payload["status"] == "empty" and payload["exit_code"] == 0


def test_缺子命令报错走协议块(tmp_path):
    """c038 回归: argparse 用法错误必须恰出一个 exit-3 协议块, 不裸 SystemExit。"""
    proc = run_entry([], env={config.CRP_HOME_ENV: str(tmp_path / "home")})
    payload = parse_block(proc.stdout)
    assert proc.returncode == payload["exit_code"] == config.EXIT_STATE
    assert "用法错误" in payload["summary"]
    assert "Traceback" not in proc.stderr


def test_非法参数值走协议块(tmp_path):
    proc = run_entry(["start", "--mode", "ultra"], env={config.CRP_HOME_ENV: str(tmp_path / "home")})
    payload = parse_block(proc.stdout)
    assert payload["exit_code"] == config.EXIT_STATE and "用法错误" in payload["summary"]


def test_无运行可续走协议块(tmp_path):
    """resume 无运行: exit 3 + ask_user 协议块(不栈追踪)。"""
    proc = run_entry(["resume"], env={config.CRP_HOME_ENV: str(tmp_path / "home")})
    payload = parse_block(proc.stdout)
    assert payload["exit_code"] == config.EXIT_STATE
    assert payload["next_step"]["type"] == "ask_user"
    assert "Traceback" not in proc.stderr


def test_损坏state恰出一块exit3(tmp_path):
    """c048 回归: state.json 损坏 → CLI 级契约: 恰一个 exit-3 协议块, 不静默重建。"""
    home = tmp_path / "home"
    # 先正常 start 产生运行, 再写坏 state
    import subprocess, os, json as _json
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    (repo / "a.py").write_text("x=1\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "init"], check=True)
    (repo / "a.py").write_text("x=2\n")
    proc = subprocess.run(
        [sys.executable, str(ENTRY), "start", "--mode", "quick"],
        cwd=str(repo), capture_output=True, text=True, timeout=60,
        env={**os.environ, config.CRP_HOME_ENV: str(home)})
    payload = parse_block(proc.stdout)
    run_dir = payload["next_step"]["submit"]["params"][
        payload["next_step"]["submit"]["params"].index("--run-dir") + 1]
    (Path(run_dir) / "state.json").write_text("{broken", encoding="utf-8")
    proc2 = subprocess.run(
        [sys.executable, str(ENTRY), "resume", "--run-dir", run_dir],
        cwd=str(repo), capture_output=True, text=True, timeout=60,
        env={**os.environ, config.CRP_HOME_ENV: str(home)})
    p2 = parse_block(proc2.stdout)
    assert proc2.returncode == p2["exit_code"] == config.EXIT_STATE
    assert p2["next_step"]["type"] == "ask_user" and "损坏" in p2["next_step"]["question"]
