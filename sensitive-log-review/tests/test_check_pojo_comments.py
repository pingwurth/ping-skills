"""check_pojo_comments.py 单测（正常检出 / E007 基线不符 / 参数错误 E003、E005）。

正常场景以 subprocess 端到端运行（依赖 Java 与 checkstyle jar，环境已具备）；
E007 场景在进程内构造 checkstyle 目录的临时副本（伪造 jar + 错误基线），
通过 monkeypatch 替换模块常量 CHECKSTYLE_DIR，全程不触碰真实基线文件。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

import check_pojo_comments

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
SCRIPT = SCRIPTS_DIR / "check_pojo_comments.py"

ENTITY_REL = "src/main/java/com/demo/entity/UserDO.java"


def _git(repo, *args):
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        check=True, capture_output=True,
    )


def _write(repo, rel: str, content: str):
    f = repo / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(content, encoding="utf-8")


def _run_script(repo: Path, out_dir: Path, *extra_args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--repo", str(repo), "-b", "master",
         "-o", str(out_dir), *extra_args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, timeout=120,
    )


@pytest.fixture(scope="module")
def pojo_repo(tmp_path_factory):
    """feature 分支新增 1 个缺字段注释的 entity POJO 的仓库。"""
    repo = tmp_path_factory.mktemp("pojo-comments") / "repo"
    _write(repo, "README.md", "demo\n")
    _git(repo, "init", "-b", "master")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    _git(repo, "checkout", "-b", "feature")
    _write(repo, ENTITY_REL,
           "public class UserDO {\n"
           "    private String userName;\n"
           "}\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "add pojo without field comment")
    return repo


class TestNormalScenario:
    def test_miss_comments_detected_exit_1(self, pojo_repo, tmp_path):
        """缺注释 POJO 应检出：退出码 1 + 产物齐全 + SUMMARY 行。"""
        out_dir = tmp_path / "out"
        result = _run_script(pojo_repo, out_dir)

        assert result.returncode == 1, result.stdout + result.stderr

        # pojo-changed.list 含该 POJO 文件
        changed = (out_dir / "pojo-changed.list").read_text(encoding="utf-8")
        assert "UserDO.java" in changed

        # pojo-miss-comments.txt 非空（checkstyle [ERROR] 输出）
        comments = (out_dir / "pojo-miss-comments.txt").read_text(encoding="utf-8")
        assert comments.strip()

        # stdout 含机器可读 SUMMARY 行且 miss_comments >= 1
        match = re.search(r"SUMMARY: pojo_changed=(\d+) miss_comments=(\d+)", result.stdout)
        assert match, result.stdout
        assert int(match.group(1)) == 1
        assert int(match.group(2)) >= 1


class TestJarChecksumMismatch:
    def test_e007_wrong_baseline_exit_2(self, pojo_repo, tmp_path, monkeypatch, capsys):
        """E007: 临时副本目录中的基线为错误值时报 错误[E007] 且退出码 2。"""
        # 构造 checkstyle 目录临时副本：伪造 jar 内容 + 必然不匹配的 SHA256 基线
        fake_dir = tmp_path / "checkstyle"
        fake_dir.mkdir()
        jar = fake_dir / check_pojo_comments.JAR_NAME
        jar.write_bytes(b"fake jar content for checksum mismatch test")
        baseline = fake_dir / (check_pojo_comments.JAR_NAME + ".sha256")
        baseline.write_text("0" * 64 + "  " + check_pojo_comments.JAR_NAME + "\n",
                            encoding="utf-8")
        (fake_dir / "pojo-field-doc.xml").write_text("<module/>", encoding="utf-8")

        # monkeypatch 替换模块常量，真实 checkstyle 目录与基线文件不被触碰
        monkeypatch.setattr(check_pojo_comments, "CHECKSTYLE_DIR", fake_dir)
        monkeypatch.setattr(sys, "argv",
                            ["check_pojo_comments.py", "--repo", str(pojo_repo),
                             "-b", "master", "-o", str(tmp_path / "out")])

        with pytest.raises(SystemExit) as exc_info:
            check_pojo_comments.main()
        assert exc_info.value.code == 2
        assert "错误[E007]" in capsys.readouterr().err


class TestParameterErrors:
    def test_e003_invalid_branch_exit_2(self, pojo_repo, tmp_path):
        """非法分支名（含 ..）应报 错误[E003] 且退出码 2。"""
        result = _run_script(pojo_repo, tmp_path / "out", "-b", "bad..branch")
        assert result.returncode == 2
        assert "错误[E003]" in result.stderr

    def test_e005_invalid_run_id_exit_2(self, pojo_repo, tmp_path):
        """非法 --run-id（路径穿越）应报 错误[E005] 且退出码 2。"""
        result = _run_script(pojo_repo, tmp_path / "out", "--run-id", "../evil")
        assert result.returncode == 2
        assert "错误[E005]" in result.stderr
