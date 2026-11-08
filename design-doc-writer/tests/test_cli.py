"""CLI 契约：退出码 / next_step 末行 / --log / kb-scan / 全局参数位置。"""
from __future__ import annotations

from conftest import make_analysis, make_design, read_ledger, write_doc


def test_every_subcommand_prints_next_step_last_line(dd, grilling, tmp_path):
    """沿完整流程走一遍，每步 stdout 末行都是 next_step: 。"""
    steps = [
        ("frontier",),
        ("q-answer", "Q001", "--answer", "最小集"),
        ("q-answer", "Q002", "--answer", "异步"),
        ("frontier",),
        ("status",),
        ("phase", "analysis-doc"),
    ]
    for step in steps:
        code, out = dd(*step)
        assert out, step
        last = out.rstrip().splitlines()[-1]
        assert last.startswith("next_step: "), f"{step}: 末行不是 next_step: {last!r}"
    write_doc(grilling, make_analysis())
    for step in (("check", "--doc", "analysis"),
                 ("confirm", "--doc", "analysis", "--because", "ok")):
        code, out = dd(*step)
        assert code == 0, (step, out)
        assert out.rstrip().splitlines()[-1].startswith("next_step: ")
    dd("phase", "design-doc")
    write_doc(grilling, make_design(), analysis=False)
    for step in (("check", "--doc", "design"),
                 ("confirm", "--doc", "design", "--because", "ok"),
                 ("finish",)):
        code, out = dd(*step)
        assert code == 0, (step, out)
        assert out.rstrip().splitlines()[-1].startswith("next_step: ")


def test_frontier_exit_codes(dd, grilling):
    assert dd("frontier")[0] == 1          # 有 open
    dd("q-answer", "Q001", "--answer", "x")
    assert dd("frontier")[0] == 1          # Q002 刚解锁仍 open
    dd("q-answer", "Q002", "--answer", "y")
    assert dd("frontier")[0] == 0          # 全清
    out = dd("frontier")[1]
    assert "shared-understanding" in out


def test_frontier_lists_blocked_and_deferred(dd, grilling):
    dd("q-answer", "Q001", "--defer", "--reason", "等排期")
    code, out = dd("frontier")
    assert code == 1
    assert "Q002" in out and "Q001" in out and "延期" in out


def test_global_repo_after_subcommand(dd, booted, tmp_path):
    """--repo 允许出现在子命令之后（parents=SUPPRESS 合并）；在别的 cwd 也能定位台账。"""
    neutral = tmp_path.parent / "ddw-neutral-cwd"
    neutral.mkdir(exist_ok=True)
    code, out = dd("status", "--repo", str(booted), cwd=neutral)
    assert code == 0 and "phase: intake" in out


def test_log_file_gets_command_header(dd, booted):
    dd("status")
    log = booted / ".agent" / "design-doc-writer" / "dd.log"
    assert log.is_file()
    assert "[cmd] dd.py" in log.read_text(encoding="utf-8")


def test_kb_scan_detects_and_prioritizes(dd, booted):
    (booted / ".codegraph").mkdir()
    (booted / ".codegraph" / "db.sqlite").write_text("x", encoding="utf-8")
    (booted / ".repowise").mkdir()
    (booted / ".repowise" / "idx.bin").write_text("x", encoding="utf-8")
    (booted / ".pwiki").mkdir()  # 空目录 → 不可用
    code, out = dd("kb-scan", "--openwiki")
    assert code == 0
    assert "codegraph" in out and "repowise" in out
    assert "空目录" in out and "openwiki" in out
    led = read_ledger(booted)
    assert led["kb"]["primary"] == "codegraph"
    assert led["kb"]["openwiki_mcp"] is True
    kinds = [d["kind"] for d in led["kb"]["detected"]]
    assert kinds == ["codegraph", "repowise", "pwiki"]


def test_kb_scan_falls_back_to_direct_reading(dd, booted):
    code, out = dd("kb-scan")
    assert code == 0
    led = read_ledger(booted)
    assert led["kb"]["primary"] is None
    assert "降级" in out or "直接读码" in out


def test_kb_scan_prefers_repowise_when_no_codegraph(dd, booted):
    (booted / ".repowise").mkdir()
    (booted / ".repowise" / "x").write_text("x", encoding="utf-8")
    (booted / ".wiki").mkdir()
    (booted / ".wiki" / "home.md").write_text("# w\n", encoding="utf-8")
    assert dd("kb-scan")[0] == 0
    led = read_ledger(booted)
    assert led["kb"]["primary"] == "repowise"
    kinds = [d["kind"] for d in led["kb"]["detected"]]
    assert kinds == ["repowise", "wiki"]


def test_status_shows_phase_and_history(dd, grilling):
    code, out = dd("status")
    assert code == 0
    assert "phase: grilling" in out and "order-export" in out
    assert "Q001" in out and "q-add" in out
    assert "analysis" in out and "design" in out


def test_check_exit_codes(dd, grilling):
    write_doc(grilling, make_analysis())
    assert dd("check", "--doc", "analysis")[0] == 1   # open 未清
    dd("q-answer", "Q001", "--answer", "x")
    dd("q-answer", "Q002", "--answer", "y")
    assert dd("check", "--doc", "analysis")[0] == 0


def test_confirm_because_required_by_argparse(dd, grilling):
    code, out = dd("confirm", "--doc", "analysis")
    assert code == 2  # argparse required 缺失


def test_done_phase_rejects_writes(dd, grilling):
    from conftest import write_ledger
    led = read_ledger(grilling)
    led["phase"] = "done"
    write_ledger(grilling, led)
    assert dd("q-add", "--dimension", "scope", "--title", "t",
              "--body", "b", "--recommendation", "r")[0] == 2
    assert dd("q-answer", "Q001", "--answer", "x")[0] == 2
