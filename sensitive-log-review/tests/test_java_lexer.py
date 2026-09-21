"""common.java_lexer 词法分析器单测：iter_fields / iter_classes / check_tostring_annotation。"""

from __future__ import annotations

import textwrap

import pytest

from common.java_lexer import check_tostring_annotation, iter_classes, iter_fields


def _write(tmp_path, source: str, name: str = "Sample.java"):
    f = tmp_path / name
    f.write_text(textwrap.dedent(source), encoding="utf-8")
    return f


def _names(fields):
    return [name for _, name in fields]


# ---------------------------------------------------------------------------
# iter_fields
# ---------------------------------------------------------------------------

class TestIterFields:
    def test_simple_fields(self, tmp_path):
        f = _write(tmp_path, """
            public class UserDO {
                private String userName;
                private Integer age;
                protected long id;
            }
        """)
        fields = list(iter_fields(f))
        assert _names(fields) == ["userName", "age", "id"]
        # 行号定位：userName 在第 3 行（dedent 后 1 起）
        assert fields[0][0] == 3

    def test_skip_local_variables_in_method(self, tmp_path):
        f = _write(tmp_path, """
            public class A {
                private String realField;
                public void doIt() {
                    String localVar = "x";
                    int count = 1;
                }
            }
        """)
        assert _names(list(iter_fields(f))) == ["realField"]

    def test_skip_fields_in_comments_and_strings(self, tmp_path):
        f = _write(tmp_path, """
            public class A {
                // private String fakeOne;
                /* private String fakeTwo; */
                private String desc = "private String fakeThree;";
                private String realField;
            }
        """)
        assert _names(list(iter_fields(f))) == ["desc", "realField"]

    def test_static_final_constant(self, tmp_path):
        f = _write(tmp_path, """
            public class A {
                private static final long serialVersionUID = 1L;
                public static final String CODE = "c";
            }
        """)
        names = _names(list(iter_fields(f)))
        assert "serialVersionUID" in names
        assert "CODE" in names

    def test_multi_line_declaration(self, tmp_path):
        f = _write(tmp_path, """
            public class A {
                private String
                    multiLineField
                    = "v";
            }
        """)
        assert "multiLineField" in _names(list(iter_fields(f)))

    def test_record_components(self, tmp_path):
        f = _write(tmp_path, """
            public record UserRecord(String name, Integer age) {
            }
        """, "UserRecord.java")
        names = _names(list(iter_fields(f)))
        assert "name" in names
        assert "age" in names

    def test_enum_constants_included_by_design(self, tmp_path):
        # 设计行为：枚举常量名也被提取（如 ID_CARD("身份证") 携带敏感语义，
        # 需纳入审计范围），与原版 extract_java_fields 行为一致（parity 要求）
        f = _write(tmp_path, """
            public enum Status {
                ENABLED, DISABLED;
                private final String code;
            }
        """, "Status.java")
        names = _names(list(iter_fields(f)))
        assert "code" in names
        assert "ENABLED" in names
        assert "DISABLED" in names

    def test_generic_and_annotation_modifiers(self, tmp_path):
        f = _write(tmp_path, """
            public class A {
                @Deprecated
                private List<String> tags;
                private Map<String, Integer> counts;
            }
        """)
        names = _names(list(iter_fields(f)))
        assert "tags" in names
        assert "counts" in names

    def test_empty_and_missing_file(self, tmp_path):
        f = _write(tmp_path, "public class Empty {}")
        assert list(iter_fields(f)) == []
        # 不存在的文件：应抛出异常或返回空（实现上由调用方兜底）
        missing = tmp_path / "missing.java"
        try:
            result = list(iter_fields(missing))
            assert result == []
        except OSError:
            pass  # 两种行为均可接受


# ---------------------------------------------------------------------------
# iter_classes
# ---------------------------------------------------------------------------

class TestIterClasses:
    def test_kinds_detected(self, tmp_path):
        f = _write(tmp_path, """
            public class Holder {
                class Inner {}
                interface MyInterface {}
                enum MyEnum { A }
                record MyRecord(String x) {}
            }
        """)
        kinds = {name: kind for _, name, _, kind in iter_classes(f)}
        assert kinds["Holder"] == "class"
        assert kinds["Inner"] == "class"
        assert kinds["MyInterface"] == "interface"
        assert kinds["MyEnum"] == "enum"
        assert kinds["MyRecord"] == "record"

    def test_annotations_collected(self, tmp_path):
        f = _write(tmp_path, """
            @Data
            @ToString(of = {"id"})
            public class UserDO {
                private Long id;
            }
        """)
        classes = list(iter_classes(f))
        assert len(classes) == 1
        line_no, name, annotations, kind = classes[0]
        assert name == "UserDO"
        assert kind == "class"
        assert any("ToString" in a for a in annotations)
        assert any("Data" in a for a in annotations)

    def test_class_keyword_in_comment_ignored(self, tmp_path):
        f = _write(tmp_path, """
            // class FakeInComment {}
            /* interface FakeInBlock {} */
            public class RealOne {}
        """)
        names = [name for _, name, _, _ in iter_classes(f)]
        assert names == ["RealOne"]

    def test_line_numbers_accurate(self, tmp_path):
        f = _write(tmp_path, """
            package com.x;

            public class First {}

            class Second {}
        """)
        classes = list(iter_classes(f))
        by_name = {name: line for line, name, _, _ in classes}
        assert by_name["First"] == 4
        assert by_name["Second"] == 6


# ---------------------------------------------------------------------------
# check_tostring_annotation
# ---------------------------------------------------------------------------

class TestCheckToStringAnnotation:
    def test_valid_tostring_with_of(self):
        ok, _ = check_tostring_annotation(['@ToString(of = {"id"})'])
        assert ok is True

    def test_tostring_without_of_is_invalid(self):
        ok, reason = check_tostring_annotation(["@ToString"])
        assert ok is False
        assert reason  # 应给出原因说明

    def test_fully_qualified_tostring(self):
        ok, _ = check_tostring_annotation(['@lombok.ToString(of={"x"})'])
        assert ok is True

    def test_no_tostring(self):
        ok, _ = check_tostring_annotation(["@Data", "@Builder"])
        assert ok is False

    def test_empty_annotations(self):
        ok, _ = check_tostring_annotation([])
        assert ok is False

    def test_similar_name_not_matched(self):
        # ToStringBuilder 之类不应被误识别为 @ToString
        ok, _ = check_tostring_annotation(["@ToStringExclude"])
        assert ok is False
