"""check_tostring_annotation 类型检查策略单测（tmp_path 构造样例 Java 文件）。"""

from __future__ import annotations

import textwrap

import check_tostring_annotation as cta


def _check(tmp_path, source: str, record_policy: str = "skip", name: str = "Sample.java"):
    f = tmp_path / name
    f.write_text(textwrap.dedent(source), encoding="utf-8")
    tostring_violations, sensitive_violations = cta.check_file(f, record_policy)
    return tostring_violations


class TestClassCheck:
    def test_class_missing_tostring_reported(self, tmp_path):
        results = _check(tmp_path, """
            public class UserDO {
                private Long id;
            }
        """)
        assert len(results) == 1
        assert "Missing @ToString annotation" in results[0]

    def test_class_with_tostring_of_passes(self, tmp_path):
        results = _check(tmp_path, """
            @ToString(of = {"id"})
            public class UserDO {
                private Long id;
            }
        """)
        assert results == []

    def test_tostring_without_of_reported(self, tmp_path):
        results = _check(tmp_path, """
            @ToString
            public class UserDO {
                private Long id;
            }
        """)
        assert len(results) == 1
        assert "Missing 'of' attribute" in results[0]

    def test_fully_qualified_lombok_tostring_passes(self, tmp_path):
        results = _check(tmp_path, """
            @lombok.ToString(of = {"id"})
            public class UserDO {
                private Long id;
            }
        """)
        assert results == []


class TestKindPolicies:
    def test_interface_and_enum_skipped(self, tmp_path):
        results = _check(tmp_path, """
            public interface UserApi {
                String NAME = "x";
            }
            enum Status { ON, OFF }
        """)
        assert results == []

    def test_record_skipped_by_default(self, tmp_path):
        results = _check(tmp_path, """
            public record UserRecord(String name) {
            }
        """)
        assert results == []

    def test_record_warn_policy_reports_with_marker(self, tmp_path):
        results = _check(tmp_path, """
            public record UserRecord(String name) {
            }
        """, record_policy="warn")
        assert len(results) == 1
        assert results[0].endswith("(record)")

    def test_mixed_file_only_class_reported(self, tmp_path):
        results = _check(tmp_path, """
            public class Outer {
                interface Inner1 {}
                enum Inner2 { A }
            }
        """)
        # 仅外层 class 被报告，interface/enum 一律跳过
        assert len(results) == 1
        assert "Missing @ToString annotation" in results[0]


def _check_with_sensitive(tmp_path, source: str, record_policy: str = "skip", name: str = "Sample.java"):
    """检查文件并返回 (tostring_violations, sensitive_violations) 元组。"""
    f = tmp_path / name
    f.write_text(textwrap.dedent(source), encoding="utf-8")
    # 加载敏感词典（使用默认词典）
    from common.dictionary import load_sensitive_dicts
    sensitive_dicts = load_sensitive_dicts()
    return cta.check_file(f, record_policy, sensitive_dicts=sensitive_dicts)


class TestSensitiveWords:
    def test_tostring_with_sensitive_field_detected(self, tmp_path):
        """测试 @ToString(of = {...}) 中包含敏感字段时被检测到。"""
        tostring_violations, sensitive_violations = _check_with_sensitive(tmp_path, """
            @ToString(of = {"password", "id"})
            public class UserDO {
                private String password;
                private Long id;
            }
        """)
        assert len(tostring_violations) == 0
        assert len(sensitive_violations) == 1
        assert "password" in sensitive_violations[0]
        assert "命中敏感词" in sensitive_violations[0]

    def test_tostring_with_no_sensitive_field_passes(self, tmp_path):
        """测试 @ToString(of = {...}) 中不包含敏感字段时通过。"""
        tostring_violations, sensitive_violations = _check_with_sensitive(tmp_path, """
            @ToString(of = {"id", "description"})
            public class UserDO {
                private Long id;
                private String description;
            }
        """)
        assert len(tostring_violations) == 0
        assert len(sensitive_violations) == 0

    def test_tostring_with_multiple_sensitive_fields(self, tmp_path):
        """测试 @ToString(of = {...}) 中包含多个敏感字段时全部被检测到。"""
        tostring_violations, sensitive_violations = _check_with_sensitive(tmp_path, """
            @ToString(of = {"password", "cardNo", "id"})
            public class UserDO {
                private String password;
                private String cardNo;
                private Long id;
            }
        """)
        assert len(tostring_violations) == 0
        assert len(sensitive_violations) == 2
        # 检查是否包含两个敏感词违规
        violations_text = " ".join(sensitive_violations)
        assert "password" in violations_text
        assert "cardNo" in violations_text
