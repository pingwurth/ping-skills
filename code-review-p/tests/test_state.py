"""运行目录解析、state.json 原子写与锁的测试。"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from crp import config
from crp.models import State
from crp.state import (StateStore, create_run_dir, list_runs, new_run_id,
                       repo_slug, resolve_run_dir, state_home)


@pytest.fixture
def crp_home(tmp_path, monkeypatch) -> Path:
    home = tmp_path / "crp-state"
    monkeypatch.setenv(config.CRP_HOME_ENV, str(home))
    return home


def test_state_home_环境覆盖优先(crp_home):
    assert state_home() == crp_home


def test_repo_slug_不同路径不碰撞(tmp_path):
    a, b = tmp_path / "projA", tmp_path / "projB"
    a.mkdir(); b.mkdir()
    sa, sb = repo_slug(a), repo_slug(b)
    assert sa != sb and sa.startswith("projA-") and sb.startswith("projB-")


def test_new_run_id_形状():
    rid = new_run_id()
    # crp-<YYYYmmdd>-<HHMMSS>-<4hex>
    assert re.fullmatch(r"crp-\d{8}-\d{6}-[0-9a-f]{4}", rid)


def test_create_run_dir_布局(crp_home, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    run_dir = create_run_dir(repo)
    assert run_dir.parent.parent == crp_home
    for sub in ("in", "work", "out", "logs"):
        assert (run_dir / sub).is_dir()


def test_save_load_往返与原子性(crp_home, tmp_path):
    repo = tmp_path / "repo"; repo.mkdir()
    run_dir = create_run_dir(repo)
    store = StateStore(run_dir)
    state = State(run_id=run_dir.name, mode="deep", phase="sweep",
                  candidates=[{"id": "c001", "summary": "x"}])
    store.save(state)
    assert not (run_dir / (config.STATE_FILENAME + ".tmp")).exists(), "临时文件应已被 replace"
    loaded = store.load()
    assert loaded is not None and loaded.mode == "deep"
    assert loaded.candidates == [{"id": "c001", "summary": "x"}]
    # 默认值填充(旧状态缺字段)
    minimal = {"schema_version": 1, "run_id": "x"}
    assert State.from_dict(minimal).mode == config.DEFAULT_MODE
    assert State.from_dict(minimal).cursor["round"] == 0


def test_load_损坏状态返回_none(crp_home, tmp_path):
    repo = tmp_path / "repo"; repo.mkdir()
    run_dir = create_run_dir(repo)
    (run_dir / config.STATE_FILENAME).write_text("{ broken json", encoding="utf-8")
    assert StateStore(run_dir).load() is None


def test_list_runs_与_resolve_最新未完成(crp_home, tmp_path):
    repo = tmp_path / "repo"; repo.mkdir()
    for i, phase in enumerate(("done", "verify", "report")):
        run_dir = create_run_dir(repo, run_id=f"crp-20260101-00000{i}-abcd")
        StateStore(run_dir).save(State(run_id=run_dir.name, phase=phase))
    runs = list_runs(repo)
    assert [r["phase"] for r in runs] == ["report", "verify", "done"]  # 新 -> 旧
    picked = resolve_run_dir(None, repo)
    assert picked is not None and picked.name.endswith("000002-abcd"), "应跳过 done 选最新未完成"
    assert resolve_run_dir("explicit-dir", repo) == Path("explicit-dir").resolve()


def test_跨进程锁互斥(crp_home, tmp_path):
    """子进程持锁期间, 父进程非阻塞探测应报告锁被持有; 退出后应可获锁。"""
    repo = tmp_path / "repo"; repo.mkdir()
    run_dir = create_run_dir(repo)
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    holder = subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent(f"""
            import sys, time
            sys.path.insert(0, {str(scripts)!r})
            from crp.state import StateStore
            store = StateStore({str(run_dir)!r})
            with store.locked():
                print("locked", flush=True)
                time.sleep(1.5)
        """)],
        stdout=subprocess.PIPE, text=True,
    )
    try:
        assert holder.stdout.readline().strip() == "locked"
        from crp.state import _lock_held
        assert _lock_held(run_dir / (config.STATE_FILENAME + ".lock")) is True
    finally:
        holder.wait(timeout=10)
    # holder 退出后锁空闲, 可正常获取
    store = StateStore(run_dir)
    with store.locked():
        pass
