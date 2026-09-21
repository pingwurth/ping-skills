"""common 包基础模块单测：paths / text / pojo_config / dictionary / git_utils。"""

from __future__ import annotations

import os
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
