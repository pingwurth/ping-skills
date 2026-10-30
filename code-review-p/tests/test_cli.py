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


def test_缺子命令报错(tmp_path):
    proc = run_entry([], env={config.CRP_HOME_ENV: str(tmp_path / "home")})
    assert proc.returncode != 0 and "子命令" in proc.stderr


def test_未实现子命令转协议块而非崩溃(tmp_path):
    """M1+ 前的占位处理器: StepError -> exit 3 + ask_user 协议块(不栈追踪)。"""
    proc = run_entry(["resume"], env={config.CRP_HOME_ENV: str(tmp_path / "home")})
    payload = parse_block(proc.stdout)
    assert payload["exit_code"] == config.EXIT_STATE
    assert payload["next_step"]["type"] == "ask_user"
    assert "Traceback" not in proc.stderr
