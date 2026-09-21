"""check_sensitive_word 敏感/不合格字段变更检查单测（真实临时 git 仓库）。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import check_sensitive_word as csw
from common.git_utils import AddedLinesCache

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
REL_PATH = "src/main/java/com/demo/entity/UserDO.java"


def _git(repo, *args):
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        check=True, capture_output=True,
    )


@pytest.fixture
def repo(tmp_path):
    """master 基线 + feature 分支新增两个敏感字段与一个不合格字段。"""
    repo = tmp_path / "repo"
    java_file = repo / REL_PATH
    java_file.parent.mkdir(parents=True)

    java_file.write_text("public class UserDO {\n}\n", encoding="utf-8")
    _git(repo, "init", "-b", "master")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")

    _git(repo, "checkout", "-b", "feature")
    java_file.write_text(
        "public class UserDO {\n"
        "    private String loginPassword;\n"
        "    private String userPhone;\n"
        "    private String badNm;\n"
        "}\n",
        encoding="utf-8",
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "add fields")
    return repo


class TestIsLowConfidence:
    @pytest.mark.parametrize("line,expected", [
        (f"{REL_PATH}#3 [E:phone] userPhone", True),
        (f"{REL_PATH}#2 [C:password] loginPassword", False),
        (f"{REL_PATH}#2 [C:password,E:phone] mixed", False),
        (f"{REL_PATH}#4: oldFormatNoTags", False),
        ("unparsable garbage", False),
    ])
    def test_tag_split(self, line, expected):
        assert csw.is_low_confidence(line) is expected


class TestMatchChangedFiles:
    def test_backslash_normalized_match(self):
        lines = [r"C:\ci\repo\src\main\java\com\demo\entity\UserDO.java#2 [C:password] loginPassword"]
        assert csw.match_changed_files([REL_PATH], lines) == lines

    def test_no_match_for_other_file(self):
        lines = [f"{REL_PATH}#2 [C:password] loginPassword"]
        assert csw.match_changed_files(["src/main/java/com/demo/entity/OrderDO.java"], lines) == []


class TestFilterByGitDiff:
    def test_keeps_field_added_in_diff(self, repo):
        cache = AddedLinesCache(repo, "master")
        lines = [f"{REL_PATH}#2 [C:password] loginPassword"]
        assert csw.filter_by_git_diff(lines, cache) == lines

    def test_filters_field_not_in_diff(self, repo):
        cache = AddedLinesCache(repo, "master")
        # legacyField 未出现在新增行中，应被过滤
        lines = [f"{REL_PATH}#9 [C:password] legacyField"]
        assert csw.filter_by_git_diff(lines, cache) == []


class TestCliEndToEnd:
    """独立 CLI 运行：验证变更行过滤、标签拆分与 *.results 产出。"""

    def _run(self, repo, out_dir, branch="master"):
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        return subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "check_sensitive_word.py"),
             "--repo", str(repo), "-b", branch, "-o", str(out_dir)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
        )

    def test_results_files_produced(self, repo, tmp_path):
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        (out_dir / "maybe-sensitive.fields").write_text(
            f"{REL_PATH}#2 [C:password] loginPassword\n"
            f"{REL_PATH}#3 [E:phone] userPhone\n",
            encoding="utf-8",
        )
        (out_dir / "unqualified.fields").write_text(
            f"{REL_PATH}#4: badNm\n", encoding="utf-8",
        )

        result = self._run(repo, out_dir)
        assert result.returncode == 1  # 高置信敏感 + 不合格字段触发门禁

        sensitive = (out_dir / "sensitive.results").read_text(encoding="utf-8").splitlines()
        assert f"[SENSITIVE] {REL_PATH}#2 [C:password] loginPassword" in sensitive
        assert f"[LOW] {REL_PATH}#3 [E:phone] userPhone" in sensitive

        unqualified = (out_dir / "unqualified.results").read_text(encoding="utf-8").splitlines()
        assert unqualified == [f"[UNQUALIFIED] {REL_PATH}#4: badNm"]

        assert "SUMMARY: sensitive=1 low=1 unqualified=1" in result.stdout

    def test_low_only_does_not_trigger_gate(self, repo, tmp_path):
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        (out_dir / "maybe-sensitive.fields").write_text(
            f"{REL_PATH}#3 [E:phone] userPhone\n", encoding="utf-8",
        )
        (out_dir / "unqualified.fields").write_text("", encoding="utf-8")

        result = self._run(repo, out_dir)
        assert result.returncode == 0
        assert "SUMMARY: sensitive=0 low=1 unqualified=0" in result.stdout

    def test_no_changed_files_exits_zero(self, repo, tmp_path):
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        # 与当前分支自身比较：无变更文件，产出空结果
        result = self._run(repo, out_dir, branch="feature")
        assert result.returncode == 0
        assert (out_dir / "sensitive.results").read_text(encoding="utf-8") == ""
        assert (out_dir / "unqualified.results").read_text(encoding="utf-8") == ""
