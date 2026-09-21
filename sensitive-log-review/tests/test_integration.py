"""main.py 端到端集成测试（真实小 git 仓库 + subprocess 全流程）。

用例控制在少量核心场景（每次运行完整四链路流水线，含 checkstyle）。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
MAIN = SCRIPTS_DIR / "main.py"

ENTITY_REL = "src/main/java/com/demo/entity/UserDO.java"
SERVICE_REL = "src/main/java/com/demo/service/UserService.java"


def _git(repo, *args):
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        check=True, capture_output=True,
    )


def _write(repo, rel: str, content: str):
    f = repo / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(content, encoding="utf-8")


def _init_repo_with_base(repo: Path):
    _write(repo, "README.md", "demo\n")
    _git(repo, "init", "-b", "master")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    _git(repo, "checkout", "-b", "feature")


def _run_main(repo: Path, out_dir: Path, *extra_args: str):
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(
        [sys.executable, str(MAIN), "-r", str(repo), "-b", "master",
         "-o", str(out_dir), *extra_args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, timeout=300,
    )


@pytest.fixture
def dirty_repo(tmp_path):
    """feature 分支引入敏感日志 + 敏感 POJO 字段的仓库。"""
    repo = tmp_path / "dirty-repo"
    _init_repo_with_base(repo)
    _write(repo, ENTITY_REL,
           "public class UserDO {\n"
           "    private String loginPassword;\n"
           "}\n")
    _write(repo, SERVICE_REL,
           "public class UserService {\n"
           "    void login() {\n"
           '        log.info("pwd: {}", loginPassword);\n'
           "    }\n"
           "}\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "add sensitive code")
    return repo


@pytest.fixture
def clean_repo(tmp_path):
    """feature 分支仅引入无敏感信息变更的仓库。"""
    repo = tmp_path / "clean-repo"
    _init_repo_with_base(repo)
    _write(repo, SERVICE_REL,
           "public class UserService {\n"
           "    void hello() {\n"
           '        log.info("service ready");\n'
           "    }\n"
           "}\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "add clean code")
    return repo


class TestPipelineDirty:
    def test_gate_triggered_and_artifacts_produced(self, dirty_repo, tmp_path):
        out_dir = tmp_path / "out"
        result = _run_main(dirty_repo, out_dir)

        # 默认门禁 violation,sensitive：敏感日志 + 敏感字段 → 退出码 1
        assert result.returncode == 1, result.stdout + result.stderr
        assert "门禁触发" in result.stdout

        # 关键产物存在且含预期条目
        violation = (out_dir / "log-print-violation.list").read_text(encoding="utf-8")
        assert "UserService.java" in violation
        assert "SENSITIVE:password" in violation

        sensitive = (out_dir / "sensitive.results").read_text(encoding="utf-8")
        assert "[SENSITIVE]" in sensitive
        assert "loginPassword" in sensitive

        analyze = (out_dir / "analyze-sensitize.result").read_text(encoding="utf-8")
        assert "loginPassword" in analyze

        # HTML 报告生成且为完整页面
        report = (out_dir / "sensitive-log-review-report.html").read_text(encoding="utf-8")
        assert "敏感信息日志审查报告" in report
        assert "</html>" in report


class TestPipelineClean:
    def test_clean_change_exits_zero(self, clean_repo, tmp_path):
        out_dir = tmp_path / "out"
        result = _run_main(clean_repo, out_dir)

        assert result.returncode == 0, result.stdout + result.stderr
        assert "门禁通过" in result.stdout

        # 合规日志进入 ok 清单，违规清单为空
        assert (out_dir / "log-print-violation.list").read_text(encoding="utf-8").strip() == ""
        assert (out_dir / "sensitive-log-review-report.html").is_file()


class TestDoctor:
    def test_doctor_all_pass_exits_zero(self, clean_repo, tmp_path):
        out_dir = tmp_path / "out"
        result = _run_main(clean_repo, out_dir, "--doctor")

        assert result.returncode == 0, result.stdout + result.stderr
        assert "[OK]" in result.stdout
        assert "[FAIL]" not in result.stdout
        assert "环境诊断" in result.stdout

    def test_doctor_non_git_dir_fails_with_exit_2(self, tmp_path):
        not_repo = tmp_path / "not-a-repo"
        not_repo.mkdir()
        out_dir = tmp_path / "out"
        result = _run_main(not_repo, out_dir, "--doctor")

        assert result.returncode == 2
        assert "[FAIL]" in result.stdout
