"""2026-11-09 max 审查回修回归：协议契约与健壮性（D、E 簇）。

对应审查报告 design-doc-writer/docs/code-review/2026-11-09-max-review.md
的 D1…E5 编号。E3 已随批次①（A2 逐行替换）落地并有测试。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from conftest import DD, make_analysis, read_ledger, write_doc, write_ledger


def run_dd(*args, cwd, with_stderr=False):
    p = subprocess.run([sys.executable, str(DD), *[str(a) for a in args]],
                       cwd=str(cwd), capture_output=True, text=True)
    return (p.returncode, p.stdout, p.stderr) if with_stderr else (p.returncode, p.stdout)


# ---------- D1：日志初始化 IO 失败 → exit 2 + next_step（非 traceback 退出 1） ----------

def test_d1_log_path_is_directory_exits_2(dd, booted):
    code, out = dd("status", "--log", str(booted))
    assert code == 2
    assert "日志" in out and "错误" in out
    assert out.rstrip().splitlines()[-1].startswith("next_step: ")


def test_d1_readonly_agent_dir_exits_2(dd, booted):
    logfile = booted / ".agent" / "design-doc-writer" / "dd.log"
    logfile.chmod(0o444)  # 目录可写没用：重建 mode="w" 日志需要文件本身可写
    try:
        code, out = dd("status")
        assert code == 2 and "日志" in out
        assert "Traceback" not in out
    finally:
        logfile.chmod(0o644)


# ---------- D2：只读 status 探测不创建 .agent/ 目录树 ----------

def test_d2_status_probe_on_typo_path_creates_nothing(dd, repo, tmp_path):
    typo = tmp_path / "definitely-not-a-repo"
    code, out = dd("status", "--repo", str(typo))
    assert code == 2 and "台账不存在" in out
    assert not typo.exists()  # 拼错的路径不被塞进 .agent/ 目录树


def test_d2_status_probe_on_ledgerless_repo_keeps_it_clean(dd, tmp_path):
    bare = tmp_path / "bare-repo"
    bare.mkdir()
    (bare / ".git").mkdir()
    code, out = dd("status", "--repo", str(bare))
    assert code == 2
    assert not (bare / ".agent").exists()
    # 有台账后正常落日志（--log 显式指定则总是允许建目录）
    (bare / ".agent").mkdir(parents=True)
    (bare / ".agent" / "dd.log").write_text("x", encoding="utf-8")
    code, out = dd("status", "--repo", str(bare), "--log", str(tmp_path / "probe.log"))
    assert (tmp_path / "probe.log").is_file()


# ---------- D3：next_step 阶段感知（建议的 phase 指令本身合法） ----------

def test_d3_frontier_suggestion_legal_from_analyzing(dd, booted):
    assert dd("phase", "analyzing")[0] == 0
    code, out = dd("frontier")  # 零问题 → 清零
    assert code == 0
    assert "phase grilling" in out and "phase analysis-doc" in out
    # 照建议执行：两步都合法
    assert dd("phase", "grilling")[0] == 0
    code, out = dd("frontier")
    assert code == 0 and "phase analysis-doc" in out
    assert dd("phase", "analysis-doc")[0] == 0


def test_d3_frontier_suggestion_no_phase_push_in_late_phases(dd, grilling):
    assert dd("q-answer", "Q001", "--answer", "x")[0] == 0
    assert dd("q-answer", "Q002", "--answer", "y")[0] == 0
    from conftest import write_ledger as _wl
    led = read_ledger(grilling)
    led["phase"] = "design-doc"
    _wl(grilling, led)
    code, out = dd("frontier")
    assert code == 0
    assert "dd.py phase analysis-doc" not in out  # design-doc 中不再推销非法转移


# ---------- D4：q-add@intake 指引可直接执行（不循环） ----------

def test_d4_q_add_intake_guidance_unlocks(dd, booted):
    code, out = dd("q-add", "--dimension", "scope", "--title", "t",
                   "--body", "b", "--recommendation", "r")
    assert code == 2 and "phase analyzing" in out
    # 照 next_step 执行后即可提问
    assert dd("phase", "analyzing")[0] == 0
    assert dd("q-add", "--dimension", "scope", "--title", "t",
              "--body", "b", "--recommendation", "r")[0] == 0


# ---------- D5：argparse 例外已入协议（usage→stderr、stdout 空、exit 2） ----------

def test_d5_argparse_error_contract(booted):
    code, out, err = run_dd("init", "--feature", "x", "--shape", "moblie",
                            cwd=booted, with_stderr=True)
    assert code == 2
    assert out == ""            # stdout 为空：协议记录在案的例外
    assert "usage" in err       # usage 走 stderr
    skill = Path(DD).parent.parent / "SKILL.md"
    assert "argparse 参数错误" in skill.read_text(encoding="utf-8")  # 例外已写入文档


# ---------- D6：日志与 stderr 共用单一文件句柄（互不覆盖） ----------

def test_d6_single_handle_no_overwrite(tmp_path):
    import dd as ddmod
    lp = tmp_path / "dd.log"
    ddmod.setup_logging(lp)
    try:
        ddmod.LOG.info("info-before")
        sys.stderr.write("stderr-burst\n")
        sys.stderr.flush()
        ddmod.LOG.info("info-after")
        ddmod.LOG.handlers[0].flush()
    finally:
        ddmod._restore_stderr()
        for h in list(ddmod.LOG.handlers):
            ddmod.LOG.removeHandler(h)
            h.close()
    text = lp.read_text(encoding="utf-8")
    assert "info-before" in text and "info-after" in text
    assert "stderr-burst" in text  # 不再被日志写回的过期偏移覆盖


# ---------- E1：部分损坏的台账走「结构非法」通道，不伪装成引擎 bug ----------

def test_e1_partial_corruption_reports_structured_error(dd, booted):
    led = read_ledger(booted)
    del led["docs"]["analysis"]["path"]
    write_ledger(booted, led)
    code, out = dd("status")
    assert code == 2 and "台账结构非法" in out
    assert "未预期异常" not in out and "请回报" not in out


def test_e1_manifest_sections_shape_and_kb_checked(dd, booted):
    led = read_ledger(booted)
    led["manifest"]["sections"] = ["背景与目标", "范围：IN 与 OUT"]
    write_ledger(booted, led)
    assert dd("status")[0] == 2
    led = read_ledger(booted)
    led["manifest"]["sections"] = [
        {"doc": "analysis", "id": "background", "title": "背景与目标", "enabled": True}]
    led["kb"] = "codegraph"
    write_ledger(booted, led)
    code, out = dd("status")
    assert code == 2 and "kb 非法" in out
    led = read_ledger(booted)
    led["kb"] = {"detected": [], "openwiki_mcp": False, "primary": None}
    led["questions"] = [{"id": "Q001", "title": "t", "status": "open",
                         "dimension": "scope", "adr": "0001-x.md"}]
    write_ledger(booted, led)
    code, out = dd("status")
    assert code == 2 and "adr 非法" in out


# ---------- E2：非 UTF-8 文件不落入「引擎 bug」通道 ----------

def test_e2_gbk_doc_fails_gracefully(dd, grilling):
    (grilling / "docs" / "design" / "order-export").mkdir(parents=True, exist_ok=True)
    led = read_ledger(grilling)
    p = grilling / led["docs"]["analysis"]["path"]
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(make_analysis().encode("gbk"))
    code, out = dd("check", "--doc", "analysis")
    assert code == 1 and "doc-read" in out and "UTF-8" in out
    assert "未预期异常" not in out


def test_e2_gbk_ledger_reports_corruption(dd, booted):
    lp = booted / ".agent" / "design-doc-writer" / "ledger.json"
    lp.write_bytes('{"phase": "intake"}'.encode("gbk") + b'"\x90\x87}')
    code, out = dd("status")
    assert code == 2 and "损坏" in out
    assert "未预期异常" not in out


# ---------- E4：.gitignore 等价形态 /.agent、**/.agent 不再误报提醒 ----------

def test_e4_gitignore_equivalent_patterns(dd, tmp_path):
    for pattern in ("/.agent", "**/.agent"):
        r = tmp_path / f"repo-{pattern.strip('/').replace('*', 'x')}"
        (r / ".git").mkdir(parents=True)
        (r / ".gitignore").write_text(f"node_modules\n{pattern}\n", encoding="utf-8")
        code, out = dd("init", "--feature", "f", "--repo", str(r))
        assert code == 0, out
        assert "提醒" not in out, pattern


# ---------- E5：命令速查的 --enable 形态带 --reason（照抄不再 exit 2） ----------

def test_e5_quickref_enable_carries_reason(booted):
    skill = Path(DD).parent.parent / "SKILL.md"
    line = [l for l in skill.read_text(encoding="utf-8").splitlines()
            if "dd.py manifest --enable" in l][0]
    assert "--reason" in line
    # 照抄速查的 --enable 形态可执行
    from conftest import read_ledger as _rl
    led = _rl(booted)
    assert led["phase"] == "intake"  # intake 无已确认文档，清单可调
    code, out = run_dd("manifest", "--enable", "ui,cache", "--reason", "web 项目",
                       cwd=booted)
    assert code == 0, out
