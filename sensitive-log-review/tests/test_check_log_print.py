"""check_log_print 变更日志检查单测（真实临时 git 仓库驱动）。"""

from __future__ import annotations

import subprocess

import pytest

import check_log_print as clp
from common.git_utils import AddedLinesCache

BASE_LOG = '        log.info("service started");'
NEW_LOG = '        log.info("pwd: {}", password);'
REL_PATH = "src/main/java/com/demo/UserService.java"


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
