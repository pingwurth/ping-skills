"""extract_java_fields 字段提取与审计单测。"""

from __future__ import annotations

import textwrap

import pytest

import extract_java_fields as ejf
from common.dictionary import SensitiveDicts
from common.pojo_config import DEFAULT_DIRS, DEFAULT_SUFFIXES, find_java_files

EN_US = {"user", "name", "login", "password", "phone", "order", "status", "card"}
EN_WHITELIST = {"id", "vo"}


def _dicts() -> SensitiveDicts:
    return SensitiveDicts(
        core={"password"},
        extended={"phone"},
        blacklist={"pwd"},
        whitelist={"cardtype"},
    )


class TestAuditField:
    def test_camel_case_qualified_and_clean(self):
        is_qualified, level, hit = ejf.audit_field("userName", EN_US, EN_WHITELIST, _dicts())
        assert is_qualified is True
        assert level is None
        assert hit is None

    def test_underscore_split_hits_core(self):
        is_qualified, level, hit = ejf.audit_field("login_password", EN_US, EN_WHITELIST, _dicts())
        assert is_qualified is True  # 两个单词都在英文词典
        assert level == "C"
        assert hit == "password"

    def test_camel_split_hits_core(self):
        _, level, hit = ejf.audit_field("loginPassword", EN_US, EN_WHITELIST, _dicts())
        assert level == "C"
        assert hit == "password"

    def test_blacklist_identifier_is_high_confidence(self):
        _, level, hit = ejf.audit_field("pwd", EN_US, EN_WHITELIST, _dicts())
        assert level == "C"
        assert hit == "pwd"

    def test_extended_hit_is_low_confidence(self):
        _, level, hit = ejf.audit_field("userPhone", EN_US, EN_WHITELIST, _dicts())
        assert level == "E"
        assert hit == "phone"

    def test_unqualified_when_word_not_in_dict(self):
        is_qualified, level, _ = ejf.audit_field("usrNm", EN_US, EN_WHITELIST, _dicts())
        assert is_qualified is False
        assert level is None

    def test_tech_abbreviation_whitelist_counts_as_qualified(self):
        # id 不在英文词典但在缩写白名单，视为合格命名
        is_qualified, _, _ = ejf.audit_field("orderId", EN_US, EN_WHITELIST, _dicts())
        assert is_qualified is True

    def test_sensitive_whitelist_exempts_identifier(self):
        # cardType 在敏感词白名单中，敏感性豁免
        _, level, hit = ejf.audit_field("cardType", EN_US, EN_WHITELIST, _dicts())
        assert level is None
        assert hit is None

    def test_empty_name_is_unqualified(self):
        assert ejf.audit_field("", EN_US, EN_WHITELIST, _dicts()) == (False, None, None)


class TestPojoScope:
    """POJO 判定范围：extract 全量扫描依赖的 find_java_files 行为。"""

    def _write(self, root, rel: str):
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("public class X { private String userName; }\n", encoding="utf-8")
        return f.resolve()

    def test_only_pojo_files_collected(self, tmp_path):
        entity = self._write(tmp_path, "m/src/main/java/com/x/entity/User.java")
        dto = self._write(tmp_path, "m/src/main/java/com/x/api/UserDTO.java")
        service = self._write(tmp_path, "m/src/main/java/com/x/service/UserService.java")
        test_scope = self._write(tmp_path, "m/src/test/java/com/x/entity/UserTest.java")

        files = find_java_files(tmp_path, DEFAULT_DIRS, DEFAULT_SUFFIXES)
        assert entity in files          # 目录名 entity 命中
        assert dto in files             # 文件名 DTO.java 后缀命中
        assert service not in files     # 普通 service 不是 POJO
        assert test_scope not in files  # src/test/java 范围外

    def test_fields_extracted_from_pojo(self, tmp_path):
        f = tmp_path / "src" / "main" / "java" / "com" / "x" / "entity" / "UserDO.java"
        f.parent.mkdir(parents=True)
        f.write_text(textwrap.dedent("""
            public class UserDO {
                private String loginPassword;
                private String user_phone;
            }
        """), encoding="utf-8")
        from common.java_lexer import iter_fields
        names = [name for _, name in iter_fields(f)]
        assert names == ["loginPassword", "user_phone"]

        # 提取出的字段走审计链路：一个 C 级、一个 E 级
        levels = [ejf.audit_field(n, EN_US, EN_WHITELIST, _dicts())[1] for n in names]
        assert levels == ["C", "E"]


class TestCollectTargetFiles:
    def test_files_manifest_filters_non_java(self, tmp_path):
        manifest = tmp_path / "changed.files"
        manifest.write_text("a/B.java\nreadme.md\n\nc/D.java\n", encoding="utf-8")
        args = _Args(files=manifest)
        files, desc = ejf.collect_target_files(args)
        assert [f.name for f in files] == ["B.java", "D.java"]
        assert "2 个文件" in desc

    def test_missing_manifest_exits_2(self, tmp_path):
        args = _Args(files=tmp_path / "missing.files")
        with pytest.raises(SystemExit) as excinfo:
            ejf.collect_target_files(args)
        assert excinfo.value.code == 2


class _Args:
    """collect_target_files 所需的最小参数对象。"""

    def __init__(self, files=None, project_root=None):
        self.files = files
        self.project_root = project_root
