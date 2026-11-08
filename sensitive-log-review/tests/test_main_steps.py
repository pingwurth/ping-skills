"""main.py 步骤表 / 表驱动 runner 单测（F07/F09/F31/F45 回归）。

F07: 执行失败判定统一为"返回码不在子脚本契约的正常集合内"
     （负数信号死亡码、意外码与 2 一律失败；1=有发现不算失败）。
F09: warn-only 类别（步骤3/6）的扫描器执行错误同样进入 critical_errors
     （退出码 2），不再让 --fail-on 在"检查从未执行"下"干净"通过。
F31: 门禁计数键 / 步骤显示名 / warn 语义收敛为单一数据源（STEP_SPECS）。
F45: 七个复制粘贴步骤函数收敛为参数化 runner，stdout 标签保持不变。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import main as slr_main

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"

SERVICE_REL = "src/main/java/com/demo/service/UserService.java"

# 各子脚本正常完成时的 stdout SUMMARY（与真实子脚本输出格式一致）
SUMMARIES = {
    "check_log_print.py": "SUMMARY: violation=0 low=0 ok=0",
    "check_sensitive_word.py": "SUMMARY: sensitive=0 low=0 unqualified=0",
    "check_pojo_comments.py": "SUMMARY: pojo_changed=0 miss_comments=0",
}


def _git(repo, *args):
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        check=True, capture_output=True,
    )


@pytest.fixture
def clean_repo(tmp_path):
    """feature 分支仅引入无敏感信息变更文件的仓库。"""
    repo = tmp_path / "clean-repo"
    f = repo / SERVICE_REL
    f.parent.mkdir(parents=True)
    (repo / "README.md").write_text("demo\n", encoding="utf-8")
    f.write_text("public class UserService {\n}\n", encoding="utf-8")
    _git(repo, "init", "-b", "master")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    _git(repo, "checkout", "-b", "feature")
    f.write_text(
        "public class UserService {\n"
        "    void hello() {\n"
        '        log.info("service ready");\n'
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "add clean code")
    return repo


def _make_ctx(tmp_path, changed_only=False):
    return slr_main.Context(
        root=tmp_path / "repo", branch="master",
        out_dir=tmp_path / "out", verbose=False, changed_only=changed_only,
    )


def _fake_run_script(results, default_rc=0):
    """构造按脚本名分发 (rc, stdout) 的 run_script 替身。

    results: {脚本文件名: (rc, stdout)}；未列出的脚本用 (default_rc, 默认 SUMMARY)。
    """
    def fake(cmd, verbose, description):
        script = Path(cmd[1]).name
        rc, stdout = results.get(script, (default_rc, SUMMARIES.get(script, "")))
        return rc, stdout, ""
    return fake


# ---------------------------------------------------------------------------
# F07: 返回码契约判定
# ---------------------------------------------------------------------------

class TestRunnerFailureContract:
    @pytest.mark.parametrize("step_name", [
        slr_main.STEP2_NAME, slr_main.STEP5_NAME, slr_main.STEP6_NAME,
    ])
    def test_verdict_scripts_signal_death_fails(self, tmp_path, monkeypatch, step_name):
        """判定类脚本（契约 0/1/2）被信号杀死（rc=-9）必须判失败（F07 复现）。"""
        spec = slr_main._SPEC_BY_NAME[step_name]
        assert spec.ok_rcs == frozenset({0, 1})
        monkeypatch.setattr(slr_main, "run_script", _fake_run_script({}, default_rc=-9))
        result = slr_main.run_scanner_step(spec, _make_ctx(tmp_path))
        assert result.ok is False
        assert "rc=-9" in result.error

    @pytest.mark.parametrize("step_name", [
        slr_main.STEP2_NAME, slr_main.STEP5_NAME, slr_main.STEP6_NAME,
    ])
    @pytest.mark.parametrize("rc", [2, 3, -15])
    def test_verdict_scripts_unexpected_rc_fails(self, tmp_path, monkeypatch, step_name, rc):
        spec = slr_main._SPEC_BY_NAME[step_name]
        monkeypatch.setattr(slr_main, "run_script", _fake_run_script({}, default_rc=rc))
        result = slr_main.run_scanner_step(spec, _make_ctx(tmp_path))
        assert result.ok is False

    @pytest.mark.parametrize("step_name", [
        slr_main.STEP2_NAME, slr_main.STEP5_NAME, slr_main.STEP6_NAME,
    ])
    def test_verdict_scripts_rc1_findings_not_failure(self, tmp_path, monkeypatch, step_name):
        """rc=1 仅为"有发现"：不得误判为执行错误。"""
        spec = slr_main._SPEC_BY_NAME[step_name]
        monkeypatch.setattr(slr_main, "run_script", _fake_run_script({}, default_rc=1))
        result = slr_main.run_scanner_step(spec, _make_ctx(tmp_path))
        assert result.ok is True

    @pytest.mark.parametrize("step_name", [
        slr_main.STEP1_NAME, slr_main.STEP3_NAME, slr_main.STEP4_NAME, slr_main.STEP7_NAME,
    ])
    @pytest.mark.parametrize("rc", [1, 2, -9, 3])
    def test_scanner_scripts_any_nonzero_fails(self, tmp_path, monkeypatch, step_name, rc):
        """纯扫描/产出类脚本契约仅 0/2：任何非 0 返回码（含 1）都判失败。"""
        spec = slr_main._SPEC_BY_NAME[step_name]
        assert spec.ok_rcs == frozenset({0})
        monkeypatch.setattr(slr_main, "run_script", _fake_run_script({}, default_rc=rc))
        result = slr_main.run_scanner_step(spec, _make_ctx(tmp_path))
        assert result.ok is False

    def test_rc0_success_with_summary_parsed(self, tmp_path, monkeypatch):
        spec = slr_main._SPEC_BY_NAME[slr_main.STEP2_NAME]
        monkeypatch.setattr(slr_main, "run_script", _fake_run_script({}))
        result = slr_main.run_scanner_step(spec, _make_ctx(tmp_path))
        assert result.ok is True
        assert result.summary["violation"] == 0

    def test_artifact_counts_merged_on_success(self, tmp_path, monkeypatch):
        """产物行数型门禁计数（步骤3）在成功后并入 summary（F31）。"""
        spec = slr_main._SPEC_BY_NAME[slr_main.STEP3_NAME]
        ctx = _make_ctx(tmp_path)
        ctx.out_dir.mkdir(parents=True, exist_ok=True)
        (ctx.out_dir / "miss-tostring-annotation.list").write_text(
            "A.java#1: 缺少注解\nB.java#2: 缺少注解\n", encoding="utf-8")
        (ctx.out_dir / "tostring-sensitive-violations.list").write_text(
            "C.java#3 [SENSITIVE:password] pwd\n", encoding="utf-8")
        monkeypatch.setattr(slr_main, "run_script", _fake_run_script({}))
        result = slr_main.run_scanner_step(spec, ctx)
        assert result.ok is True
        assert result.summary["tostring"] == 2
        assert result.summary["tostring_sensitive"] == 1


# ---------------------------------------------------------------------------
# F45: 表驱动命令构造（stdout 步骤标签保持不变）
# ---------------------------------------------------------------------------

class TestStepTable:
    def test_step_labels_locked(self):
        """stdout 步骤标签与旧版逐字一致（[OK] 行格式契约）。"""
        assert [spec.name for spec in slr_main.STEP_SPECS] == [
            "步骤1: 扫描日志输出",
            "步骤2: 检查变更文件日志",
            "步骤3: 检查@ToString注解",
            "步骤4: 提取Java字段",
            "步骤5: 检查敏感词",
            "步骤6: 检查POJO注释",
            "步骤7: 深度敏感性分析",
        ]
        assert len({spec.name for spec in slr_main.STEP_SPECS}) == 7

    def test_build_step_cmd_repo_flag_branch(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        cmd = slr_main.build_step_cmd(slr_main._SPEC_BY_NAME[slr_main.STEP5_NAME], ctx)
        assert cmd[1] == str(SCRIPTS_DIR / "check_sensitive_word.py")
        assert cmd[2:3] == ["-o"]
        assert cmd[-4:] == ["--repo", str(ctx.root), "-b", "master"]

    def test_build_step_cmd_positional_root_with_manifest(self, tmp_path):
        ctx = _make_ctx(tmp_path, changed_only=True)
        cmd = slr_main.build_step_cmd(slr_main._SPEC_BY_NAME[slr_main.STEP1_NAME], ctx)
        assert str(ctx.root) in cmd
        assert "--files" in cmd
        assert str(ctx.changed_java_manifest) in cmd
        # 步骤3 用 POJO 清单
        cmd3 = slr_main.build_step_cmd(slr_main._SPEC_BY_NAME[slr_main.STEP3_NAME], ctx)
        assert str(ctx.changed_pojo_manifest) in cmd3

    def test_build_step_cmd_no_root_no_branch(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        cmd = slr_main.build_step_cmd(slr_main._SPEC_BY_NAME[slr_main.STEP7_NAME], ctx)
        assert str(ctx.root) not in cmd
        assert "-b" not in cmd


# ---------------------------------------------------------------------------
# F31: 门禁计数单一数据源
# ---------------------------------------------------------------------------

class TestGateKeysSingleSource:
    def test_gate_keys_content(self):
        assert set(slr_main.GATE_KEYS) == {
            "violation", "sensitive", "unqualified", "tostring",
            "tostring_sensitive", "analyze", "miss_comments", "low",
        }
        # 派生过程不产生重复键
        assert len(slr_main.GATE_KEYS) == 8

    def test_every_gate_key_has_source_step(self):
        """每个门禁键都能从步骤表拿到计数来源（失配=counts 永远读 0）。"""
        for key in slr_main.GATE_KEYS:
            sources = [
                spec for spec in slr_main.STEP_SPECS
                if key in spec.gate_keys
                or key in {counter.key for counter in spec.artifact_counts}
            ]
            assert sources, f"门禁键 {key} 在步骤表中无来源步骤"

    def test_aggregate_counts_sums_all_sources(self):
        """low = 步骤2 + 步骤5 之和；各键取自其全部来源步骤。"""

        def _result(name, summary):
            step = slr_main.StepResult(name)
            step.summary = dict(summary)
            return step

        results = [
            _result(slr_main.STEP2_NAME, {"violation": 1, "low": 2, "ok": 5}),
            _result(slr_main.STEP3_NAME, {"tostring": 1, "tostring_sensitive": 2}),
            _result(slr_main.STEP5_NAME, {"sensitive": 1, "low": 3, "unqualified": 4}),
            _result(slr_main.STEP6_NAME, {"pojo_changed": 9, "miss_comments": 1}),
            _result(slr_main.STEP7_NAME, {"analyze": 1}),
        ]
        counts = slr_main.aggregate_counts(results)
        assert counts == {
            "violation": 1, "low": 5, "tostring": 1, "tostring_sensitive": 2,
            "sensitive": 1, "unqualified": 4, "miss_comments": 1, "analyze": 1,
        }

    def test_aggregate_counts_missing_step_defaults_zero(self):
        """链路前置失败导致步骤缺位时，缺位键按 0 计（不 KeyError）。"""
        counts = slr_main.aggregate_counts([slr_main.StepResult(slr_main.STEP1_NAME)])
        assert counts == {key: 0 for key in slr_main.GATE_KEYS}


# ---------------------------------------------------------------------------
# F07/F09: main() 端到端（monkeypatch run_script，真实小仓库）
# ---------------------------------------------------------------------------

def _run_main_inprocess(clean_repo, tmp_path, monkeypatch, capsys,
                        results=None, default_rc=0, extra_args=()):
    """进程内运行 main()：monkeypatch run_script 按脚本分发返回码。"""
    monkeypatch.setattr(slr_main, "run_script", _fake_run_script(results or {}, default_rc))
    argv = ["main.py", "-r", str(clean_repo), "-b", "master",
            "-o", str(tmp_path / "out"), *extra_args]
    monkeypatch.setattr(sys, "argv", argv)
    code = slr_main.main()
    captured = capsys.readouterr()
    return code, captured


class TestPipelineExitCode:
    def test_all_green_exits_zero(self, clean_repo, tmp_path, monkeypatch, capsys):
        code, captured = _run_main_inprocess(clean_repo, tmp_path, monkeypatch, capsys)
        assert code == 0
        assert "门禁通过" in captured.out

    def test_signal_killed_scanner_exits_2(self, clean_repo, tmp_path, monkeypatch, capsys):
        """F07 复现：check_sensitive_word 被 OOM 杀死（rc=-9、无 SUMMARY）→
        旧版步骤5 记 OK、counts=0、退出 0"门禁通过"；修复后必须退出 2。"""
        code, captured = _run_main_inprocess(
            clean_repo, tmp_path, monkeypatch, capsys,
            results={"check_sensitive_word.py": (-9, "")})
        assert code == 2
        assert "门禁通过" not in captured.out
        assert "执行错误" in captured.err

    def test_tostring_scanner_error_fails_gate_exit_2(
            self, clean_repo, tmp_path, monkeypatch, capsys):
        """F09 复现：--fail-on tostring 且步骤3 扫描器执行错误（rc=2）→
        旧版 warn-only 排除出 critical → counts=0 → 退出 0"干净通过"；
        修复后必须退出 2。"""
        code, captured = _run_main_inprocess(
            clean_repo, tmp_path, monkeypatch, capsys,
            results={"check_tostring_annotation.py": (2, "")},
            extra_args=("--fail-on", "tostring"))
        assert code == 2
        assert "门禁通过" not in captured.out

    def test_miss_comments_scanner_error_exits_2(
            self, clean_repo, tmp_path, monkeypatch, capsys):
        """F09：步骤6（POJO 注释，同为 warn-only 类别）执行错误同样退出 2。"""
        code, _ = _run_main_inprocess(
            clean_repo, tmp_path, monkeypatch, capsys,
            results={"check_pojo_comments.py": (-9, "")},
            extra_args=("--fail-on", "miss_comments"))
        assert code == 2

    def test_rc1_findings_trigger_gate_not_execution_error(
            self, clean_repo, tmp_path, monkeypatch, capsys):
        """rc=1（有发现）走门禁退出码 1，不得误判执行错误（F07 反向保护）。"""
        code, captured = _run_main_inprocess(
            clean_repo, tmp_path, monkeypatch, capsys,
            results={"check_log_print.py": (1, "SUMMARY: violation=2 low=0 ok=0")},
            extra_args=("--fail-on", "violation"))
        assert code == 1
        assert "门禁触发" in captured.out
        assert "执行错误" not in captured.err


class TestDoctorGitTimeout:
    def test_rev_parse_timeout_records_fail_exit_2(
            self, clean_repo, tmp_path, monkeypatch, capsys):
        """F43: doctor 的 rev-parse 检查走统一包装器，超时记 FAIL 并退出 2。"""

        def fake_git(repo, *args, **kwargs):
            raise subprocess.TimeoutExpired(["git"], slr_main.GIT_TIMEOUT_SECONDS)

        monkeypatch.setattr(slr_main, "git", fake_git)
        import argparse as _argparse
        args = _argparse.Namespace(root=str(clean_repo), output_dir=str(tmp_path / "out"),
                                   run_id=None)
        assert slr_main.run_doctor(args) == 2
        out = capsys.readouterr().out
        assert "[FAIL]" in out
        assert "超时" in out
