"""check_log_print 变更日志检查单测（真实临时 git 仓库驱动）。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import check_log_print as clp
import common.git_utils as git_utils
from common.git_utils import AddedLinesCache

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"

BASE_LOG = '        log.info("service started");'
NEW_LOG = '        log.info("pwd: {}", password);'
REL_PATH = "src/main/java/com/demo/UserService.java"
CJK_REL = "src/main/java/demo/中文测试.java"


def _git(repo, *args):
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        check=True, capture_output=True,
    )


@pytest.fixture
def repo(tmp_path):
    """基线 master + feature 分支新增一行敏感日志的小仓库。"""
    repo = tmp_path / "repo"
    java_file = repo / REL_PATH
    java_file.parent.mkdir(parents=True)

    java_file.write_text(
        "public class UserService {\n    void m() {\n" + BASE_LOG + "\n    }\n}\n",
        encoding="utf-8",
    )
    _git(repo, "init", "-b", "master")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")

    _git(repo, "checkout", "-b", "feature")
    java_file.write_text(
        "public class UserService {\n    void m() {\n"
        + BASE_LOG + "\n" + NEW_LOG + "\n    }\n}\n",
        encoding="utf-8",
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "add sensitive log")
    return repo


class TestIsLowOnly:
    @pytest.mark.parametrize("tags,expected", [
        ([], False),
        (["LOW:phone"], True),
        (["LOW:phone", "LOW:email"], True),
        (["SENSITIVE:password"], False),
        (["SENSITIVE:password", "LOW:phone"], False),
        (["JSON_LOG"], False),
    ])
    def test_split(self, tags, expected):
        assert clp.is_low_only(tags) is expected


class TestMatchChangedFiles:
    def test_backslash_list_line_matches_posix_changed_file(self):
        # 清单为 Windows 反斜杠绝对路径，变更文件为 git 正斜杠相对路径
        lines = [r"C:\work\repo\src\main\java\com\demo\UserService.java#3 [SENSITIVE:password] x"]
        matched = clp.match_changed_files([REL_PATH], lines)
        assert matched == lines

    def test_relative_changed_file_substring_match(self):
        lines = ["/home/ci/repo/" + REL_PATH + "#3: x"]
        assert clp.match_changed_files([REL_PATH], lines) == lines

    def test_unrelated_file_not_matched(self):
        lines = [r"C:\repo\src\main\java\com\demo\OrderService.java#3: x"]
        assert clp.match_changed_files([REL_PATH], lines) == []


class TestFilterByGitDiff:
    def test_keeps_lines_added_in_diff(self, repo):
        cache = AddedLinesCache(repo, "master")
        abs_path = str(repo / REL_PATH)
        lines = [f"{abs_path.replace(chr(92), '/')}#4 [SENSITIVE:password] {NEW_LOG}"]
        # 用相对路径清单行同样能匹配（cache 直接以清单行内路径调用 git）
        rel_lines = [f"{REL_PATH}#4 [SENSITIVE:password] {NEW_LOG}"]
        assert clp.filter_by_git_diff(lines, _RelocatingCache(cache)) == lines
        assert clp.filter_by_git_diff(rel_lines, cache) == rel_lines

    def test_filters_out_pre_existing_line(self, repo):
        cache = AddedLinesCache(repo, "master")
        # 基线中已存在的日志行不在新增行中，应被过滤
        lines = [f"{REL_PATH}#3: {BASE_LOG}"]
        assert clp.filter_by_git_diff(lines, cache) == []

    def test_unparsable_line_dropped(self, repo):
        cache = AddedLinesCache(repo, "master")
        assert clp.filter_by_git_diff(["not a list line"], cache) == []

    def test_backslash_path_normalized_for_cache(self, repo):
        cache = AddedLinesCache(repo, "master")
        # 反斜杠写法的相对路径应经 normalize 后命中同一 git diff
        win_path = REL_PATH.replace("/", "\\")
        lines = [f"{win_path}#4 [SENSITIVE:password] {NEW_LOG}"]
        assert clp.filter_by_git_diff(lines, cache) == lines


class _RelocatingCache:
    """将清单行中的绝对路径转发为仓库相对路径的测试适配器。"""

    def __init__(self, inner: AddedLinesCache):
        self._inner = inner

    def get(self, file_path: str) -> str:
        normalized = file_path.replace("\\", "/")
        marker = "src/main/java"
        index = normalized.find(marker)
        return self._inner.get(normalized[index:] if index >= 0 else normalized)


class TestLoadListLines:
    def test_missing_file_returns_empty_with_warning(self, tmp_path, capsys):
        assert clp.load_list_lines(tmp_path / "nope.list") == []
        assert "清单文件不存在" in capsys.readouterr().err

    def test_reads_non_empty_lines(self, tmp_path):
        f = tmp_path / "a.list"
        f.write_text("line1\n\n  line2  \n", encoding="utf-8")
        assert clp.load_list_lines(f) == ["line1", "line2"]


class TestCjkPathEndToEnd:
    """F05 端到端：中文命名 Java 文件的违规不再被两个下游门禁静默剔除。"""

    def test_cjk_file_violation_reported(self, tmp_path):
        repo = tmp_path / "repo"
        java_file = repo / CJK_REL
        java_file.parent.mkdir(parents=True)
        java_file.write_text(
            "public class C {\n    void m() {\n        log.info(\"start\");\n    }\n}\n",
            encoding="utf-8",
        )
        _git(repo, "init", "-b", "master")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "base")
        _git(repo, "checkout", "-b", "feature")
        cjk_new_log = '        log.info("pwd: {}", password);'
        java_file.write_text(
            "public class C {\n    void m() {\n        log.info(\"start\");\n"
            + cjk_new_log + "\n    }\n}\n",
            encoding="utf-8",
        )
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "add sensitive log")

        out_dir = tmp_path / "out"
        out_dir.mkdir()
        (out_dir / "log-print-ok.list").write_text(
            f"{CJK_REL}#3:         log.info(\"start\");\n", encoding="utf-8")
        (out_dir / "log-print-violation.list").write_text(
            f"{CJK_REL}#4 [SENSITIVE:password] {cjk_new_log}\n", encoding="utf-8")

        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        result = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "check_log_print.py"),
             "--repo", str(repo), "-b", "master", "-o", str(out_dir)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
        )
        # 旧版：C 引号路径 exists() 为 False → 变更匹配/新增行过滤双双落空
        # → SUMMARY violation=0 且退出码 0（漏报）
        assert result.returncode == 1, result.stdout + result.stderr
        assert "SUMMARY: violation=1" in result.stdout


class TestGitTimeoutCli:
    """F43: 树级 diff 超时按执行错误（退出码 2）处理，而非静默/挂死。"""

    def test_timeout_on_tree_diff_exits_2(self, repo, tmp_path, monkeypatch, capsys):
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        real_git = git_utils.git

        def fake_git(repo_arg, *args, **kwargs):
            if "-U0" in args:
                raise subprocess.TimeoutExpired(
                    ["git"], git_utils.GIT_TIMEOUT_SECONDS)
            return real_git(repo_arg, *args, **kwargs)

        monkeypatch.setattr(git_utils, "git", fake_git)
        monkeypatch.setattr(
            sys, "argv",
            ["check_log_print.py", "--repo", str(repo), "-b", "master",
             "-o", str(out_dir)])
        assert clp.main() == 2
        assert "执行超时" in capsys.readouterr().err
