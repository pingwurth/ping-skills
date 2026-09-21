"""main.py 失败文件链路集成测试（--strict-mode / failed-files.list / HTML 失败区块）。

失败文件的稳定构造方式说明：
  main.py 入口会先调用 clear_failure_shards 清理输出目录中全部
  failed-files.*.part 分片，直接预写分片会在流水线启动时被删除；
  而变更清单构造阶段（prepare_changed_manifests）对每个变更文件做
  is_file() 过滤，目录伪装成 .java 文件名无法进入清单，难以触发
  各步骤的 FailureRecorder。
  因此采用 Windows 文件共享语义：测试进程预写分片后保持句柄打开，
  子进程 main.py 的 unlink 会抛 PermissionError（被 clear_failure_shards
  的 except OSError 捕获忽略），分片得以存活并进入 merge_failure_shards
  汇总。分片步骤标识取 "pretest"，与任何真实步骤标识都不同，
  不会被各步骤 FailureRecorder.flush 覆盖或清理。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
MAIN = SCRIPTS_DIR / "main.py"

SERVICE_REL = "src/main/java/com/demo/service/UserService.java"

# 依赖 Windows 下"文件被其他进程持有句柄时 unlink 失败"的语义保住预写分片
pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="预写分片防清理依赖 Windows 文件句柄共享语义")


def _git(repo, *args):
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        check=True, capture_output=True,
    )


def _write(repo, rel: str, content: str):
    f = repo / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(content, encoding="utf-8")


def _run_main_with_locked_shard(repo: Path, out_dir: Path, fake_path: str,
                                *extra_args: str) -> subprocess.CompletedProcess:
    """预写失败分片并在持有句柄期间运行 main.py，模拟存在失败文件的流水线。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    shard = out_dir / "failed-files.pretest.part"
    shard.write_text(f"pretest\t{fake_path}\t模拟读取失败\n", encoding="utf-8")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    # 保持句柄打开，使 clear_failure_shards 的 unlink 失败（PermissionError 被忽略）
    with open(shard, "r", encoding="utf-8"):
        return subprocess.run(
            [sys.executable, str(MAIN), "-r", str(repo), "-b", "master",
             "-o", str(out_dir), *extra_args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=env, timeout=300,
        )


@pytest.fixture(scope="module")
def clean_repo(tmp_path_factory):
    """feature 分支仅引入 1 个无敏感信息变更文件的仓库（不触发门禁）。"""
    repo = tmp_path_factory.mktemp("failure-pipeline") / "clean-repo"
    _write(repo, "README.md", "demo\n")
    _git(repo, "init", "-b", "master")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    _git(repo, "checkout", "-b", "feature")
    _write(repo, SERVICE_REL,
           "public class UserService {\n"
           "    void hello() {\n"
           '        log.info("service ready");\n'
           "    }\n"
           "}\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "add clean code")
    return repo


@pytest.fixture(scope="module")
def fake_failed_path(clean_repo) -> str:
    """分片中登记的失败文件路径（无需真实存在，仅作展示与断言）。"""
    return str(clean_repo / "src" / "Broken.java")


@pytest.fixture(scope="module")
def strict_run(clean_repo, fake_failed_path, tmp_path_factory):
    """--strict-mode 下带失败分片的完整流水线运行结果。"""
    out_dir = tmp_path_factory.mktemp("strict-out")
    result = _run_main_with_locked_shard(clean_repo, out_dir, fake_failed_path,
                                         "--strict-mode")
    return result, out_dir


@pytest.fixture(scope="module")
def normal_run(clean_repo, fake_failed_path, tmp_path_factory):
    """默认（非 strict）模式下带失败分片的完整流水线运行结果。"""
    out_dir = tmp_path_factory.mktemp("normal-out")
    result = _run_main_with_locked_shard(clean_repo, out_dir, fake_failed_path)
    return result, out_dir


class TestStrictMode:
    def test_strict_mode_exit_2_with_failure_list(self, strict_run, fake_failed_path):
        """断言①: --strict-mode 下退出码 2 且 stderr 含失败清单。"""
        result, _out_dir = strict_run
        assert result.returncode == 2, result.stdout + result.stderr
        assert "严格模式" in result.stderr
        assert fake_failed_path in result.stderr


class TestNonStrictMode:
    def test_exit_code_not_raised_and_stats_printed(self, normal_run):
        """断言②: 非 strict 模式退出码不因失败文件抬升，stdout 含统计行。"""
        result, _out_dir = normal_run
        # 干净仓库 + 默认门禁（violation,sensitive 均为 0）→ 失败文件不抬升退出码
        assert result.returncode == 0, result.stdout + result.stderr
        assert "failedFiles: 1" in result.stdout

    def test_failed_files_list_artifact_format(self, normal_run, fake_failed_path):
        """断言③: failed-files.list 产物存在且行含 2 个制表符。"""
        _result, out_dir = normal_run
        failed_list = out_dir / "failed-files.list"
        assert failed_list.is_file()
        lines = [l for l in failed_list.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert lines, "failed-files.list 不应为空"
        assert all(l.count("\t") >= 2 for l in lines)
        assert any("pretest" in l and fake_failed_path in l for l in lines)

    def test_html_report_failure_section(self, normal_run, fake_failed_path):
        """断言④: HTML 报告含"失败文件"区块且列出失败路径。"""
        _result, out_dir = normal_run
        report = (out_dir / "sensitive-log-review-report.html").read_text(encoding="utf-8")
        assert "失败文件" in report
        assert fake_failed_path in report

    def test_failure_ratio_warning_emitted(self, normal_run):
        """断言⑤: failedFiles(1) > 已处理总数(1) 的 10%，告警文案出现。"""
        result, _out_dir = normal_run
        assert "警告: 失败文件数" in result.stdout
        assert "10%" in result.stdout
