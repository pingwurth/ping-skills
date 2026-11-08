"""common 包基础模块单测：paths / text / pojo_config / dictionary / git_utils。"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

from common import dictionary, git_utils, paths, pojo_config, text


# ---------------------------------------------------------------------------
# paths
# ---------------------------------------------------------------------------

class TestGetOutputDir:
    def test_cli_arg_has_highest_priority(self, tmp_path, monkeypatch):
        monkeypatch.setenv(paths.ENV_KEY, str(tmp_path / "env-dir"))
        target = tmp_path / "cli-dir"
        result = paths.get_output_dir(str(target))
        assert result == target
        assert result.is_dir()

    def test_env_var_used_when_no_cli(self, tmp_path, monkeypatch):
        monkeypatch.setenv(paths.ENV_KEY, str(tmp_path / "env-dir"))
        result = paths.get_output_dir(None)
        assert result == tmp_path / "env-dir"
        assert result.is_dir()

    def test_default_falls_back_to_tempdir(self, monkeypatch):
        monkeypatch.delenv(paths.ENV_KEY, raising=False)
        result = paths.get_output_dir(None)
        assert result == Path(tempfile.gettempdir()) / paths.DIR_NAME
        assert result.is_dir()

    def test_run_id_appended(self, tmp_path):
        result = paths.get_output_dir(str(tmp_path), "run-2024_01.01")
        assert result == tmp_path / "run-2024_01.01"
        assert result.is_dir()

    @pytest.mark.parametrize("bad", ["../evil", "a/b", "a\\b", "-lead", ".dot", "x" * 65, ""])
    def test_run_id_rejected(self, tmp_path, bad):
        if bad == "":
            # 空 run_id 视为未提供
            assert paths.get_output_dir(str(tmp_path), "") == tmp_path
            return
        with pytest.raises(ValueError):
            paths.get_output_dir(str(tmp_path), bad)

    def test_artifact(self, tmp_path):
        assert paths.artifact(tmp_path, "x.list") == tmp_path / "x.list"


# ---------------------------------------------------------------------------
# text
# ---------------------------------------------------------------------------

class TestSplitFieldName:
    @pytest.mark.parametrize("name,expected", [
        ("getPassword", ["get", "password"]),
        ("user_name", ["user", "name"]),
        ("userId2", ["user", "id"]),
        ("getUserID", ["get", "user", "id"]),
        ("XMLParser", ["xml", "parser"]),
        ("cardNo", ["card", "no"]),
        ("", []),
        ("a", ["a"]),
    ])
    def test_split(self, name, expected):
        assert text.split_field_name(name) == expected


class TestNormalizeFilePath:
    @pytest.mark.parametrize("raw,expected", [
        ("some/path/../actual/file.java", "actual/file.java"),
        ("normal/path/file.java", "normal/path/file.java"),
        ("../../file.java", "file.java"),
    ])
    def test_normalize(self, raw, expected):
        assert text.normalize_file_path(raw) == expected

    @pytest.mark.parametrize("raw,expected", [
        # git quotePath 的 C 引号形态：剥去首尾双引号（F05 兜底）
        ('"src/main/java/demo/\\344\\270\\255.java"', "src/main/java/demo/\\344\\270\\255.java"),
        ('"plain.java"', "plain.java"),
        # 非引号包裹的普通路径不受影响
        ("normal/path/file.java", "normal/path/file.java"),
        # 单个引号字符不构成包裹形态
        ('"unterminated.java', '"unterminated.java'),
    ])
    def test_c_quote_stripped(self, raw, expected):
        assert text.normalize_file_path(raw) == expected


class TestParseListLine:
    def test_old_format_with_colon(self):
        parsed = text.parse_list_line(r"C:\repo\A.java#12: log.info(x)")
        assert parsed is not None
        path, line_no, tags, content = parsed
        assert path == r"C:\repo\A.java"
        assert line_no == 12
        assert tags == []
        assert content == "log.info(x)"

    def test_new_format_with_tags(self):
        parsed = text.parse_list_line(r"C:\repo\A.java#7 [JSON_LOG,SENSITIVE:cardno] log.info(u)")
        assert parsed is not None
        path, line_no, tags, content = parsed
        assert line_no == 7
        assert tags == ["JSON_LOG", "SENSITIVE:cardno"]
        assert content == "log.info(u)"

    def test_content_starting_with_bracket_not_misparsed(self):
        # 旧格式内容本身以 [ 开头时不应被当成 tags
        parsed = text.parse_list_line(r"A.java#3: [INFO] something")
        assert parsed is not None
        assert parsed[2] == []
        assert parsed[3] == "[INFO] something"

    def test_invalid_line_returns_none(self):
        assert text.parse_list_line("no line number here") is None

    def test_path_containing_hash(self):
        parsed = text.parse_list_line(r"C:\foo#bar\A.java#9: x")
        assert parsed is not None
        assert parsed[0] == r"C:\foo#bar\A.java"
        assert parsed[1] == 9


# ---------------------------------------------------------------------------
# pojo_config
# ---------------------------------------------------------------------------

class TestLoadPojoConfig:
    def test_missing_file_returns_defaults(self, tmp_path):
        directories, suffixes = pojo_config.load_pojo_config(tmp_path / "missing.config")
        assert directories == pojo_config.DEFAULT_DIRS
        assert suffixes == pojo_config.DEFAULT_SUFFIXES

    def test_custom_config_with_comments(self, tmp_path):
        cfg = tmp_path / "pojo.config"
        cfg.write_text(
            "# 注释行\n[pojo-dir]\nentity\n\n[pojo-file]\ndto.java\n# 注释\nvo.java\n",
            encoding="utf-8",
        )
        directories, suffixes = pojo_config.load_pojo_config(cfg)
        assert directories == frozenset({"entity"})
        assert suffixes == ("dto.java", "vo.java")

    def test_empty_section_falls_back(self, tmp_path):
        cfg = tmp_path / "pojo.config"
        cfg.write_text("[pojo-dir]\n\n[pojo-file]\n\n", encoding="utf-8")
        directories, suffixes = pojo_config.load_pojo_config(cfg)
        assert directories == pojo_config.DEFAULT_DIRS
        assert suffixes == pojo_config.DEFAULT_SUFFIXES


class TestSrcMainJavaIndex:
    def test_found(self):
        assert pojo_config.src_main_java_index(("mod", "src", "main", "java", "com")) == 3

    def test_case_insensitive(self):
        assert pojo_config.src_main_java_index(("SRC", "MAIN", "JAVA", "com")) == 2

    def test_not_found(self):
        assert pojo_config.src_main_java_index(("src", "test", "java", "com")) is None


class TestIsPojoFile:
    def _root(self, tmp_path) -> Path:
        return tmp_path

    def test_directory_match(self, tmp_path):
        f = tmp_path / "m" / "src" / "main" / "java" / "com" / "x" / "entity" / "User.java"
        assert pojo_config.is_pojo_file(f, tmp_path, pojo_config.DEFAULT_DIRS, ()) is True

    def test_suffix_match(self, tmp_path):
        f = tmp_path / "m" / "src" / "main" / "java" / "com" / "x" / "api" / "UserDTO.java"
        assert pojo_config.is_pojo_file(f, tmp_path, frozenset(), pojo_config.DEFAULT_SUFFIXES) is True

    def test_suffix_match_not_exact_match(self, tmp_path):
        # 修复前的精确匹配 bug：UserDTO.java 应命中 dto.java 后缀
        f = tmp_path / "m" / "src" / "main" / "java" / "com" / "UserDTO.java"
        assert pojo_config.is_pojo_file(f, tmp_path, frozenset(), ("dto.java",)) is True

    def test_outside_src_main_java_rejected(self, tmp_path):
        f = tmp_path / "m" / "src" / "test" / "java" / "com" / "entity" / "User.java"
        assert pojo_config.is_pojo_file(f, tmp_path, pojo_config.DEFAULT_DIRS,
                                        pojo_config.DEFAULT_SUFFIXES) is False

    def test_non_pojo_rejected(self, tmp_path):
        f = tmp_path / "m" / "src" / "main" / "java" / "com" / "x" / "service" / "UserService.java"
        assert pojo_config.is_pojo_file(f, tmp_path, pojo_config.DEFAULT_DIRS,
                                        pojo_config.DEFAULT_SUFFIXES) is False


# ---------------------------------------------------------------------------
# dictionary
# ---------------------------------------------------------------------------

class TestLoadWordSet:
    def test_skips_comments_and_blank_lines(self, tmp_path):
        f = tmp_path / "words.txt"
        f.write_text("# 版本头\napple\n\n  Banana  \n# 注释\n", encoding="utf-8")
        assert dictionary.load_word_set(f) == {"apple", "banana"}

    def test_missing_file_returns_empty(self, tmp_path):
        assert dictionary.load_word_set(tmp_path / "nope.txt") == set()


class TestClassify:
    def _dicts(self):
        return dictionary.SensitiveDicts(
            core={"password"},
            extended={"phone"},
            blacklist={"pwd"},
            whitelist={"cardtype"},
        )

    def test_whitelist_exempts_identifier(self):
        assert self._dicts().classify({"card", "type"}, "cardType") is None

    def test_blacklist_beats_core(self):
        level, hit = self._dicts().classify({"pwd", "password"}, "pwd")
        assert level == "blacklist"
        assert hit == "pwd"

    def test_blacklist_identifier_match(self):
        level, _ = self._dicts().classify(set(), "pwd")
        assert level == "blacklist"

    def test_core_hit(self):
        level, hit = self._dicts().classify({"login", "password"}, "loginPassword")
        assert level == "core"
        assert hit == "password"

    def test_extended_hit(self):
        level, hit = self._dicts().classify({"user", "phone"}, "userPhone")
        assert level == "extended"
        assert hit == "phone"

    def test_no_hit(self):
        assert self._dicts().classify({"nickname"}, "nickname") is None


# ---------------------------------------------------------------------------
# git_utils.validate_ref（纯函数，无需真实 git 仓库）
# ---------------------------------------------------------------------------

class TestValidateRef:
    @pytest.mark.parametrize("ref", [
        "master", "develop", "feature/login-page", "release/v1.2.3",
        "8c2b8ed894", "HEAD", "user@branch",
    ])
    def test_valid(self, ref):
        assert git_utils.validate_ref(ref) == ref

    @pytest.mark.parametrize("ref", [
        "", "-x", "--output=/tmp/evil", "a..b", "..", "branch name",
        "x" * 201, ".hidden", "/abs",
    ])
    def test_invalid(self, ref):
        with pytest.raises(ValueError):
            git_utils.validate_ref(ref)

    def test_branch_re_pattern(self):
        assert git_utils.BRANCH_RE.match("master")
        assert not git_utils.BRANCH_RE.match("--help")


# ---------------------------------------------------------------------------
# git_utils 真实临时仓库单测（F05 中文路径 / F33 批量新增行缓存 / F43 超时）
# ---------------------------------------------------------------------------

CJK_REL = "src/main/java/demo/中文测试.java"


def _git(repo, *args):
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        check=True, capture_output=True,
    )


@pytest.fixture
def cjk_repo(tmp_path):
    """master 基线 + feature 分支修改中文命名 Java 文件的仓库。"""
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
    java_file.write_text(
        "public class C {\n    void m() {\n        log.info(\"start\");\n"
        "        log.info(\"pwd: {}\", password);\n    }\n}\n",
        encoding="utf-8",
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "add sensitive log")
    return repo


class TestGetChangedFilesCjkPaths:
    """F05: -z + core.quotepath=false，中文路径原样返回、可被文件系统命中。"""

    def test_cjk_path_returned_raw_and_exists(self, cjk_repo):
        changed = git_utils.get_changed_files(cjk_repo, "master")
        # 旧版（无 -z）返回 '"src/main/java/demo/\\344\\270\\255...java"' 带 C 引号，
        # (repo / path).exists() 为 False → 文件被下游门禁静默剔除
        assert changed == [CJK_REL]
        assert '"' not in changed[0]
        assert (cjk_repo / changed[0]).is_file()

    def test_added_lines_cache_hits_cjk_file(self, cjk_repo):
        cache = git_utils.AddedLinesCache(cjk_repo, "master")
        added = cache.get(CJK_REL)
        assert 'log.info("pwd: {}", password);' in added
        # 基线已有行不在新增行中
        assert cache.get(CJK_REL).count("log.info") == 1


@pytest.fixture
def multi_repo(tmp_path):
    """master 基线 + feature 分支同时修改两个 Java 文件的仓库。"""
    repo = tmp_path / "repo"
    for rel in ("src/A.java", "src/B.java"):
        f = repo / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("public class X {}\n", encoding="utf-8")
    _git(repo, "init", "-b", "master")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    _git(repo, "checkout", "-b", "feature")
    (repo / "src/A.java").write_text(
        "public class A {\n    void m() {\n        log.info(\"a\");\n    }\n}\n",
        encoding="utf-8",
    )
    (repo / "src/B.java").write_text(
        "public class B {\n    void m() {\n        log.info(\"b\");\n    }\n}\n",
        encoding="utf-8",
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "change both")
    return repo


class TestAddedLinesCacheBatch:
    """F33: 一次树级 diff 建表，per-file 查表语义与逐文件 git diff 等价。"""

    def test_matches_per_file_git_diff(self, multi_repo):
        cache = git_utils.AddedLinesCache(multi_repo, "master")
        for rel in ("src/A.java", "src/B.java"):
            expected = git_utils.get_added_lines(multi_repo, "master", rel)
            assert cache.get(rel) == expected
            assert cache.get(rel) != ""

    def test_only_own_file_lines_returned(self, multi_repo):
        cache = git_utils.AddedLinesCache(multi_repo, "master")
        added_a = cache.get("src/A.java")
        assert 'log.info("a")' in added_a
        assert 'log.info("b")' not in added_a

    def test_absolute_path_resolved(self, multi_repo):
        # 扫描器产物写绝对路径：剥去仓库根前缀后命中（对齐旧版 git 接受绝对 pathspec）
        cache = git_utils.AddedLinesCache(multi_repo, "master")
        assert 'log.info("a")' in cache.get(str(multi_repo / "src" / "A.java"))

    def test_backslash_path_normalized(self, multi_repo):
        cache = git_utils.AddedLinesCache(multi_repo, "master")
        assert 'log.info("a")' in cache.get("src\\A.java")

    def test_unknown_file_returns_empty(self, multi_repo):
        cache = git_utils.AddedLinesCache(multi_repo, "master")
        assert cache.get("src/NoSuch.java") == ""

    def test_single_git_invocation_regardless_of_files(self, multi_repo, monkeypatch):
        """多个文件查询只 fork 一次 git（旧版每文件一次）。"""
        calls = []
        real_git = git_utils.git

        def counting_git(repo, *args, **kwargs):
            calls.append(args)
            return real_git(repo, *args, **kwargs)

        monkeypatch.setattr(git_utils, "git", counting_git)
        cache = git_utils.AddedLinesCache(multi_repo, "master")
        for rel in ("src/A.java", "src/B.java", "src/Unknown.java"):
            cache.get(rel)
        assert calls == [("diff", "-U0", "master", "--")]


class TestGitWrapper:
    """F43: git 包装器带挂起防护超时，并关闭 quotepath 转义。"""

    def _capture_run(self, monkeypatch):
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(git_utils.subprocess, "run", fake_run)
        return captured

    def test_timeout_and_quotepath_applied(self, tmp_path, monkeypatch):
        captured = self._capture_run(monkeypatch)
        git_utils.git(tmp_path, "status")
        cmd = captured["cmd"]
        assert cmd[0] == "git" and cmd[1] == "-C" and cmd[2] == str(tmp_path)
        assert "-c" in cmd and "core.quotepath=false" in cmd
        assert captured["kwargs"]["timeout"] == git_utils.GIT_TIMEOUT_SECONDS

    def test_timeout_expired_propagates(self, tmp_path, monkeypatch):
        def fake_run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))

        monkeypatch.setattr(git_utils.subprocess, "run", fake_run)
        with pytest.raises(subprocess.TimeoutExpired):
            git_utils.git(tmp_path, "status")

    def test_get_changed_files_timeout_propagates(self, tmp_path, monkeypatch):
        monkeypatch.setattr(git_utils, "git", lambda *a, **k: (_ for _ in ()).throw(
            subprocess.TimeoutExpired(["git"], 30)))
        with pytest.raises(subprocess.TimeoutExpired):
            git_utils.get_changed_files(tmp_path, "master")

    def test_added_lines_cache_init_timeout_propagates(self, tmp_path, monkeypatch):
        monkeypatch.setattr(git_utils, "git", lambda *a, **k: (_ for _ in ()).throw(
            subprocess.TimeoutExpired(["git"], 30)))
        with pytest.raises(subprocess.TimeoutExpired):
            git_utils.AddedLinesCache(tmp_path, "master")

    def test_resolve_repo_timeout_exits_2(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(git_utils, "git", lambda *a, **k: (_ for _ in ()).throw(
            subprocess.TimeoutExpired(["git"], 30)))
        with pytest.raises(SystemExit) as exc_info:
            git_utils.resolve_repo(str(tmp_path))
        assert exc_info.value.code == 2
        assert "执行超时" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# text.match_changed_files（F34 索引化，check_log_print / check_sensitive_word 共用）
# ---------------------------------------------------------------------------

class TestMatchChangedFilesShared:
    """F34: 索引化匹配与旧版"变更数 × 行数"全行子串扫描语义等价。"""

    @staticmethod
    def _reference_impl(changed_files, list_lines):
        """旧版实现（O(变更数x行数) 全行子串扫描），作等价性参照。"""
        normalized_lines = [(line, text.normalize_for_matching(line)) for line in list_lines]
        matched = []
        for file_path in changed_files:
            normalized_file = text.normalize_for_matching(file_path)
            for line, normalized_line in normalized_lines:
                if normalized_file in normalized_line:
                    matched.append(line)
        return matched

    def test_equivalent_to_legacy_full_line_scan(self):
        changed = [
            "src/main/java/com/demo/UserService.java",
            "src/main/java/com/demo/entity/UserDO.java",
            "src/main/java/demo/中文测试.java",
            "UserService.java",
        ]
        lines = [
            "/home/ci/repo/src/main/java/com/demo/UserService.java#3: log.info(a)",
            r"C:\work\repo\src\main\java\com\demo\UserService.java#4 [LOW:phone] x",
            "/home/ci/repo/src/main/java/com/demo/entity/UserDO.java#2: name",
            "/home/ci/repo/src/main/java/demo/中文测试.java#2 [C:password] cardNo",
            "/other/OrderService.java#9: unrelated",
            "see src/main/java/com/demo/UserService.java changed",  # 不可解析行兜底
        ]
        assert (text.match_changed_files(changed, lines)
                == self._reference_impl(changed, lines))

    def test_same_filename_different_dirs(self):
        changed = ["a/User.java", "b/User.java"]
        lines = ["/repo/a/User.java#1: x", "/repo/b/User.java#2: y"]
        matched = text.match_changed_files(changed, lines)
        assert matched == self._reference_impl(changed, lines)
        assert matched == ["/repo/a/User.java#1: x", "/repo/b/User.java#2: y"]

    def test_duplicate_changed_files_duplicate_output(self):
        # 同一行被两个变更路径命中 → 按旧版语义输出两次
        changed = ["demo/A.java", "demo/A.java"]
        lines = ["/repo/src/demo/A.java#1: x"]
        assert (text.match_changed_files(changed, lines)
                == ["/repo/src/demo/A.java#1: x"] * 2)

    def test_mid_path_substring_still_matches(self):
        # 子串（而非仅后缀）命中：仓库根与清单根不一致的场景
        changed = ["main/java/com/demo/UserService.java"]
        lines = ["/ci/repo/src/main/java/com/demo/UserService.java#3: x"]
        assert text.match_changed_files(changed, lines) == lines

    def test_empty_changed_file_matches_everything(self):
        # 空串子串恒命中（保留旧版行为）
        lines = ["/repo/A.java#1: x", "garbage without parser"]
        assert text.match_changed_files([""], lines) == lines

    def test_no_match_returns_empty(self):
        changed = ["src/main/java/com/demo/UserService.java"]
        lines = [r"C:\repo\src\main\java\com\demo\OrderService.java#3: x"]
        assert text.match_changed_files(changed, lines) == []
