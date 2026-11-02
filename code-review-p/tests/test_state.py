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
    for sub in ("in", "out", "logs"):
        assert (run_dir / sub).is_dir()
    assert not (run_dir / "work").exists(), "work/ 只建不读已删(P5)"


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


# ── P6 回修回归: meta 边车(2026-11-02 全面审查) ─────────────────────
def test_p6_list_runs优先读meta边车(crp_home, tmp_path):
    """P6 回修: list_runs 原版对每个运行完整 json.load 多 MB state.json 只为
    取 5 个标量 —— save() 现落轻量 meta.json, state.json 损坏也能照常列举。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    run_dir = create_run_dir(repo)
    store = StateStore(run_dir)
    store.save(State(run_id=run_dir.name, mode="deep", phase="verify",
                     updated_at="2026-11-02T00:00:00"))
    assert (run_dir / config.META_FILENAME).is_file(), "save 应同步落 meta 边车"
    # state.json 损坏(模拟多 MB 解析失败的场景)→ meta 仍可服务列举
    (run_dir / config.STATE_FILENAME).write_text("{broken", encoding="utf-8")
    runs = list_runs(repo)
    assert len(runs) == 1
    assert runs[0]["mode"] == "deep" and runs[0]["phase"] == "verify"
    assert runs[0]["done"] is False and not runs[0].get("corrupt")
    # 旧式运行(无 meta)回退 state.json
    run2 = create_run_dir(repo)
    StateStore(run2).save(State(run_id=run2.name, mode="quick", phase="done"))
    (run2 / config.META_FILENAME).unlink()
    runs2 = list_runs(repo)
    by_id = {r["run_id"]: r for r in runs2}
    assert by_id[run2.name]["mode"] == "quick" and by_id[run2.name]["done"] is True


# ── P8 回修回归: schema 迁移 / 重入锁(2026-11-02 全面审查) ───────────
def test_p8_v1状态加载迁移回填(crp_home, tmp_path):
    """P8 回修: _MIGRATIONS 注册表空置, 新字段靠读点四处 or-回退兼容 ——
    现走真迁移: v1 加载回填 sweep_plan 并发/预算键与 repo.root, 幂等。"""
    import json
    repo = tmp_path / "repo"
    repo.mkdir()
    rd = create_run_dir(repo)
    v1 = {"schema_version": 1, "run_id": rd.name, "mode": "standard", "phase": "sweep",
          "repo": {"cwd": str(repo)}, "sweep_plan": {"angles": ["A", "B", "C"],
                                                     "keep": "confirmed_p01", "cap": 10}}
    (rd / config.STATE_FILENAME).write_text(json.dumps(v1), encoding="utf-8")
    st = StateStore(rd).load()
    assert st is not None and st.schema_version == 2
    plan = st.sweep_plan
    assert plan["max_concurrent"] == config.max_concurrent_agents()
    assert plan["max_rounds"] == 2 and plan["dry_required"] == 1  # standard 参数
    assert plan["max_agents"] == config.agent_budget(3)
    assert st.repo["root"] == str(repo)  # 旧运行降级回填 cwd
    # 已有键不覆盖(keep/cap 保留 v1 原值); 迁移幂等
    assert plan["keep"] == "confirmed_p01" and plan["cap"] == 10
    StateStore(rd).save(st)
    st2 = StateStore(rd).load()
    assert st2.schema_version == 2 and st2.sweep_plan == plan


def test_p8_同进程嵌套加锁不自锁(tmp_path):
    """P8 回修: locked() 每次获锁新开 fd, 对自身已持的 flock 永久阻塞 ——
    原版防死锁只靠调用点约定注释(c009)。嵌套重入必须 no-op 返回。

    子进程带超时执行: 原版会挂死 → TimeoutExpired → 测试失败(而非挂起)。"""
    import subprocess
    import sys
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    rd = tmp_path / "run"
    rd.mkdir()
    code = (
        "import sys; sys.path.insert(0, %r)\n" % str(scripts)
        + "from crp.state import StateStore\n"
        + "s = StateStore(%r)\n" % str(rd)
        + "with s.locked():\n"
        + "    with s.locked():\n"   # 原版: 对自身 flock → 挂死
        + "        pass\n"
        + "print('NESTED_OK')\n"
    )
    proc = subprocess.run([sys.executable, "-c", code],
                          capture_output=True, text=True, timeout=15)
    assert proc.returncode == 0 and "NESTED_OK" in proc.stdout
