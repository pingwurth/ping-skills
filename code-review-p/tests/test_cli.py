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


from conftest import parse_block


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


def test_p1_处理期间告警落入rundir日志(tmp_path):
    """P1 回归(2026-11-02 审查): setup_logger 原在 handler 执行之后才调用,
    <run-dir>/logs/<cmd>.log 恒为空文件 —— 协议块缺失恢复步骤(SKILL.md)读尾部
    永远读到 0 字节。处理期间(如损坏 state 加载)的 module_logger 告警必须
    已在 handler 执行前接好的 run-dir 日志里。"""
    import subprocess, os
    home = tmp_path / "home"
    repo = tmp_path / "r2"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    (repo / "a.py").write_text("x=1\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "init"], check=True)
    (repo / "a.py").write_text("x=2\n")
    env = {**os.environ, config.CRP_HOME_ENV: str(home)}
    proc = subprocess.run(
        [sys.executable, str(ENTRY), "start", "--mode", "quick"],
        cwd=str(repo), capture_output=True, text=True, timeout=60, env=env)
    payload = parse_block(proc.stdout)
    run_dir = payload["next_step"]["submit"]["params"][
        payload["next_step"]["submit"]["params"].index("--run-dir") + 1]
    # start 自身的 run-dir 日志也已接上(_place_state 内 setup)
    start_log = Path(run_dir) / "logs" / "start.log"
    assert start_log.is_file(), "start 落定运行目录后应立即建立本命令日志"
    (Path(run_dir) / "state.json").write_text("{broken", encoding="utf-8")
    proc2 = subprocess.run(
        [sys.executable, str(ENTRY), "resume", "--run-dir", run_dir],
        cwd=str(repo), capture_output=True, text=True, timeout=60, env=env)
    assert proc2.returncode == config.EXIT_STATE
    log = Path(run_dir) / "logs" / "resume.log"
    content = log.read_text(encoding="utf-8")
    assert "state.json 读取失败" in content, "handler 执行期的告警必须落入本命令日志(原版恒空)"


def test_p11_共享handler无偏移互踩(tmp_path):
    """P11 回修(c020): setup_logger 与根原各开一个 mode='w' handler 指向同
    一文件 —— 经返回 logger 写一条即覆写 root 已写告警首字节(实测); 现共享
    同一实例, 偏移一致。"""
    from crp import logutil
    rd = tmp_path / "run"
    (rd / "logs").mkdir(parents=True)
    lg = logutil.setup_logger("submit", rd)
    logutil.module_logger().warning("W1 root warning line")
    lg.warning("W2 via returned logger line")
    text = (rd / "logs" / "submit.log").read_text(encoding="utf-8")
    assert "W1 root warning line" in text, "root 告警不得被返回 logger 的写入覆写"
    assert "W2 via returned logger line" in text


def test_p11_next别名进日志前置名单(tmp_path):
    """P11 回修(c013): 'next' 是独立子命令(args.cmd=='next'), 原不在
    _prehandler_run_dir 名单 —— 裸 next 的 run-dir 日志恒空(P1 症状复现)。"""
    import argparse
    import os
    from crp import cli
    from crp import state as state_mod
    home = tmp_path / "home"
    os.environ[config.CRP_HOME_ENV] = str(home)
    repo = tmp_path / "repo"
    repo.mkdir()
    rd = state_mod.create_run_dir(repo)
    state_mod.StateStore(rd).save(
        state_mod.State(run_id=rd.name, mode="quick", phase="sweep"))
    cwd = os.getcwd()
    os.chdir(repo)
    try:
        ns = argparse.Namespace(run_dir=None)
        assert cli._prehandler_run_dir("next", ns) is not None, \
            "裸 next 必须命中前置 run-dir 解析(别名与 resume 同源)"
        assert cli._prehandler_run_dir("resume", ns) is not None
    finally:
        os.chdir(cwd)


# ── F27 根修回归: ask 待决的单一记账点(2026-11-09 全仓审查) ──────────
def test_f27_待决键提取只认可答复询问():
    """F27 根修: gates 询问由纯路由器发出、原从不记录 pending_question ——
    陈旧守卫只能维护四份按键特定公式(P10 c015/c033 两次咬人)。单一记账点
    从 ask_user Decision 提取键: 只有 resume 走 `answer` 的询问占键。"""
    from crp import handlers
    from crp.models import Decision, ResumeOption, Route

    def ask(resume, status="needs_input", route=Route.ASK_USER):
        return Decision(status=status, exit_code=config.EXIT_CONTINUE,
                        summary="s", reason="r", route=route, resume=resume)

    gates = ask([ResumeOption("approve_all", "全部批准", config.ENTRY_FILENAME,
                              ["answer", "--run-dir", "/x", "--key", "gates",
                               "--choice", "approve_all"])])
    assert handlers._pending_ask_key(gates) == "gates"
    # 纯路由询问(resume 直达)不占键
    run_ask = ask([ResumeOption("resume_run", "续跑", config.ENTRY_FILENAME,
                                ["resume", "--run-dir", "/x"])])
    assert handlers._pending_ask_key(run_ask) is None
    # 错误提示(无 resume)不占键 —— 不得覆写在途待决
    assert handlers._pending_ask_key(ask([])) is None
    assert handlers._pending_ask_key(ask([], status="failed")) is None
    # 非 ask 路由不占键
    assert handlers._pending_ask_key(ask([], route=Route.DO_WORK)) is None

    # 记账: 有键写入, 无键不动(在途 drift 不被错误提示抹掉)
    from crp.models import State
    st = State(mode="quick", phase="gates")
    handlers._record_ask(st, gates)
    assert st.pending_question == {"key": "gates", "at": st.pending_question["at"]}
    st.pending_question = {"key": "drift", "at": "t"}
    handlers._record_ask(st, run_ask)
    assert st.pending_question["key"] == "drift", "非可答复询问不得覆写待决键"
