"""jacoco 覆盖率解析单测: 内部类聚合、<init>/<clinit>、desc、CSV/XML、边界。"""

from __future__ import annotations

from pathlib import Path

from jaut import jacoco
from jaut.models import coverage_rate

_XML = """<?xml version="1.0"?>
<report name="demo">
  <package name="com/x">
    <class name="com/x/Foo" sourcefilename="Foo.java">
      <method name="foo" desc="(Ljava/util/List;I)V" line="10">
        <counter type="LINE" missed="2" covered="8"/>
      </method>
      <method name="&lt;init&gt;" desc="()V" line="5">
        <counter type="LINE" missed="0" covered="1"/>
      </method>
      <method name="&lt;clinit&gt;" desc="()V" line="3">
        <counter type="LINE" missed="0" covered="1"/>
      </method>
    </class>
    <class name="com/x/Foo$Inner" sourcefilename="Foo.java">
      <method name="bar" desc="()V" line="30">
        <counter type="LINE" missed="3" covered="0"/>
      </method>
    </class>
  </package>
</report>
"""


def _write_xml(tmp_path: Path) -> Path:
    d = tmp_path / "target" / "site" / "jacoco"
    d.mkdir(parents=True)
    p = d / "jacoco.xml"
    p.write_text(_XML, encoding="utf-8")
    return p


def test_coverage_rate_zero_total_is_100():
    assert coverage_rate(0, 0) == 100.0
    assert coverage_rate(8, 2) == 80.0


def test_parse_xml_aggregates_inner_class_and_special_methods(tmp_path: Path):
    xml = _write_xml(tmp_path)
    parsed = jacoco.parse_jacoco_xml(xml)
    assert set(parsed.keys()) == {"com.x.Foo"}          # 内部类聚合到外部类
    methods = {m.key.name: m for m in parsed["com.x.Foo"]}
    assert "foo" in methods and methods["foo"].key.desc == "(List, int)"
    assert "static {...}" in methods                     # <clinit>
    assert "Foo" in methods                              # <init> -> 类简单名
    assert methods["foo"].covered == 8 and methods["foo"].missed == 2
    assert any(m.key.name == "bar" for m in parsed["com.x.Foo"])  # 内部类方法归入外部类


def test_parse_xml_missing_file_returns_empty(tmp_path: Path):
    assert jacoco.parse_jacoco_xml(tmp_path / "nope.xml") == {}


def test_parse_csv_aggregates_inner_class(tmp_path: Path):
    csv_path = tmp_path / "jacoco.csv"
    csv_path.write_text(
        "GROUP,PACKAGE,CLASS,LINE_MISSED,LINE_COVERED\n"
        "g,com.x,Foo,2,8\n"
        "g,com.x,Foo$Inner,3,0\n"
        "g,com.x,Other,9,9\n", encoding="utf-8")
    cov = jacoco.parse_jacoco_csv(csv_path, "com.x.Foo")
    assert cov == (8, 5)         # covered=8+0, missed=2+3
    assert jacoco.parse_jacoco_csv(csv_path, "com.x.Absent") is None


def test_class_rate_from_xml_fallback(tmp_path: Path):
    xml = _write_xml(tmp_path)
    covered, missed = jacoco.class_rate_from_xml(xml, "com.x.Foo")
    assert covered == 8 + 1 + 1 + 0 and missed == 2 + 0 + 0 + 3


def test_class_coverage_csv_priority_then_xml(tmp_path: Path):
    xml = _write_xml(tmp_path)
    csv_path = tmp_path / "target" / "site" / "jacoco" / "jacoco.csv"
    csv_path.write_text(
        "GROUP,PACKAGE,CLASS,LINE_MISSED,LINE_COVERED\ng,com.x,Foo,1,9\n", encoding="utf-8")
    cc = jacoco.class_coverage(xml, csv_path, "com.x.Foo")
    assert (cc.covered, cc.missed) == (9, 1)     # CSV 优先
    # 无 CSV -> 回退 XML 聚合
    cc2 = jacoco.class_coverage(xml, tmp_path / "missing.csv", "com.x.Foo")
    assert cc2.covered == 10 and cc2.missed == 5


def test_is_excluded_semantics():
    # '*' 跨 '.' 与 '$' 段, 与 JaCoCo 官方 excludes 语义一致
    assert jacoco.is_excluded("com.foo.dto.UserDto", ["com.foo.dto.*"])
    assert jacoco.is_excluded("com.foo.sub.Deep", ["com.foo.*"])
    assert jacoco.is_excluded("com.foo.User$Builder", ["*$Builder"])
    assert jacoco.is_excluded("com.foo.User$Builder", ["com.foo.User$*"])
    assert not jacoco.is_excluded("com.foo.User", ["com.foo.User$*"])
    assert not jacoco.is_excluded("com.foo.User", [])


def test_parse_xml_excludes_before_inner_class_aggregation(tmp_path: Path):
    xml = _write_xml(tmp_path)
    # 排除单个内部类: 其方法不归入外部类方法表
    parsed = jacoco.parse_jacoco_xml(xml, excludes=["com.x.Foo$Inner"])
    assert "bar" not in {m.key.name for m in parsed["com.x.Foo"]}
    # '*' 通配命中内部类(不误伤外部类)
    parsed2 = jacoco.parse_jacoco_xml(xml, excludes=["com.x.*$Inner"])
    assert "bar" not in {m.key.name for m in parsed2["com.x.Foo"]}
    # '*' 贪婪匹配: 'com.x.F*' 连外部类一起排除
    assert jacoco.parse_jacoco_xml(xml, excludes=["com.x.F*"]) == {}
    # 排除整个类
    assert jacoco.parse_jacoco_xml(xml, excludes=["com.x.*"]) == {}


def test_parse_csv_excludes_inner_class(tmp_path: Path):
    csv_path = tmp_path / "jacoco.csv"
    csv_path.write_text(
        "GROUP,PACKAGE,CLASS,LINE_MISSED,LINE_COVERED\n"
        "g,com.x,Foo,2,8\n"
        "g,com.x,Foo$Inner,3,0\n", encoding="utf-8")
    assert jacoco.parse_jacoco_csv(csv_path, "com.x.Foo",
                                   excludes=["com.x.Foo$Inner"]) == (8, 2)


def test_class_aggregation_respects_excludes(tmp_path: Path):
    xml = _write_xml(tmp_path)
    assert jacoco.class_rate_from_xml(xml, "com.x.Foo",
                                      excludes=["com.x.Foo$Inner"]) == (10, 2)
    cc = jacoco.class_coverage(xml, tmp_path / "missing.csv", "com.x.Foo",
                               excludes=["com.x.Foo$Inner"])
    assert (cc.covered, cc.missed) == (10, 2)


def test_report_paths(tmp_path: Path):
    xml, csv = jacoco.report_paths(tmp_path, ".")
    assert xml == tmp_path / "target" / "site" / "jacoco" / "jacoco.xml"
    xml2, _ = jacoco.report_paths(tmp_path, "mod")
    assert xml2 == tmp_path / "mod" / "target" / "site" / "jacoco" / "jacoco.xml"


# --------------------------------------------------------------------------- #
# P3-新增-1: 补充边界测试
# --------------------------------------------------------------------------- #
def test_synthetic_method_filtered(tmp_path: Path):
    """lambda / access / 桥接方法等合成方法不应出现在解析结果中。"""
    xml_content = """<?xml version="1.0"?>
<report name="demo">
  <package name="com/x">
    <class name="com/x/Svc" sourcefilename="Svc.java">
      <method name="process" desc="()V" line="10">
        <counter type="LINE" missed="0" covered="5"/>
      </method>
      <method name="lambda$process$0" desc="()V" line="12">
        <counter type="LINE" missed="1" covered="2"/>
      </method>
      <method name="access$000" desc="()V" line="1">
        <counter type="LINE" missed="0" covered="1"/>
      </method>
    </class>
  </package>
</report>"""
    d = tmp_path / "target" / "site" / "jacoco"
    d.mkdir(parents=True)
    xf = d / "jacoco.xml"
    xf.write_text(xml_content, encoding="utf-8")
    parsed = jacoco.parse_jacoco_xml(xf)
    method_names = {m.key.name for m in parsed.get("com.x.Svc", [])}
    assert "process" in method_names
    assert "lambda$process$0" not in method_names
    assert "access$000" not in method_names


def test_is_synthetic_method():
    """合成方法判定: 包含 '$' 且非 <init>/<clinit>。"""
    assert jacoco._is_synthetic_method("lambda$foo$0") is True
    assert jacoco._is_synthetic_method("access$000") is True
    assert jacoco._is_synthetic_method("bridge$method$1") is True
    assert jacoco._is_synthetic_method("<init>") is False
    assert jacoco._is_synthetic_method("<clinit>") is False
    assert jacoco._is_synthetic_method("process") is False


def test_parse_xml_malformed_returns_empty(tmp_path: Path):
    """畸形 XML(截断标签)应返回空 dict。"""
    d = tmp_path / "target" / "site" / "jacoco"
    d.mkdir(parents=True)
    xf = d / "jacoco.xml"
    xf.write_text("<report><package><class><method", encoding="utf-8")
    assert jacoco.parse_jacoco_xml(xf) == {}


def test_parse_csv_nonexistent_file_returns_none(tmp_path: Path):
    """CSV 不存在时返回 None。"""
    assert jacoco.parse_jacoco_csv(tmp_path / "missing.csv", "com.x.Foo") is None


def test_class_coverage_with_parsed_xml_param(tmp_path: Path):
    """传入 parsed_xml 时应跳过 XML 重新解析。"""
    xml = _write_xml(tmp_path)
    pre_parsed = jacoco.parse_jacoco_xml(xml)
    cc = jacoco.class_coverage(
        xml, tmp_path / "missing.csv", "com.x.Foo", parsed_xml=pre_parsed)
    assert cc.covered == 10 and cc.missed == 5


def test_parse_xml_empty_package_name(tmp_path: Path):
    """空包名时类名不应包含前导点号。"""
    xml_content = """<?xml version="1.0"?>
<report name="demo">
  <package name="">
    <class name="Standalone" sourcefilename="Standalone.java">
      <method name="run" desc="()V" line="1">
        <counter type="LINE" missed="0" covered="3"/>
      </method>
    </class>
  </package>
</report>"""
    d = tmp_path / "target" / "site" / "jacoco"
    d.mkdir(parents=True)
    xf = d / "jacoco.xml"
    xf.write_text(xml_content, encoding="utf-8")
    parsed = jacoco.parse_jacoco_xml(xf)
    assert "Standalone" in parsed
    methods = {m.key.name for m in parsed["Standalone"]}
    assert "run" in methods
