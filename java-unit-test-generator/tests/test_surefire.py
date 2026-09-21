"""surefire 报告解析单测: XML/TXT、fail-closed、断言计数、record_failures。"""

from __future__ import annotations

from pathlib import Path

from jaut import surefire


def _reports(tmp_path: Path) -> Path:
    d = tmp_path / "target" / "surefire-reports"
    d.mkdir(parents=True)
    return d


def test_missing_dir_not_green(tmp_path: Path):
    r = surefire.parse_surefire_reports(tmp_path, ".")
    assert r.report_found is False
    assert r.is_green() is False     # fail-closed


def test_xml_green(tmp_path: Path):
    d = _reports(tmp_path)
    (d / "TEST-com.x.FooTest.xml").write_text(
        '<?xml version="1.0"?>\n'
        '<testsuite name="com.x.FooTest" tests="2" failures="0" errors="0" skipped="0">\n'
        '  <testcase classname="com.x.FooTest" name="t1"/>\n'
        '  <testcase classname="com.x.FooTest" name="t2"/>\n'
        '</testsuite>', encoding="utf-8")
    r = surefire.parse_surefire_reports(tmp_path, ".")
    assert r.report_found and r.tests == 2 and r.failures == 0
    assert r.is_green() is True


def test_xml_failure_counts_case_and_assertion(tmp_path: Path):
    d = _reports(tmp_path)
    (d / "TEST-com.x.FooTest.xml").write_text(
        '<?xml version="1.0"?>\n'
        '<testsuite tests="1" failures="1" errors="0" skipped="0">\n'
        '  <testcase classname="com.x.FooTest" name="t1">\n'
        '    <failure type="org.opentest4j.AssertionFailedError" message="expected 1 but was 2"/>\n'
        '  </testcase>\n'
        '</testsuite>', encoding="utf-8")
    r = surefire.parse_surefire_reports(tmp_path, ".")
    assert r.failures == 1 and r.assertion_failures == 1
    assert r.is_green() is False
    assert r.failed_cases[0].summary_line().startswith("FooTest#t1 AssertionFailedError:")


def test_xml_malformed_counts_parse_error(tmp_path: Path):
    d = _reports(tmp_path)
    (d / "TEST-bad.xml").write_text("<testsuite><testcase", encoding="utf-8")
    r = surefire.parse_surefire_reports(tmp_path, ".")
    assert r.report_found is True
    assert r.parse_errors == 1
    assert r.is_green() is False     # fail-closed


def test_txt_fallback(tmp_path: Path):
    d = _reports(tmp_path)
    (d / "com.x.FooTest.txt").write_text(
        "Tests run: 3, Failures: 1, Errors: 0, Skipped: 0\n"
        "foo(com.x.FooTest)  Time elapsed: 0.01 s  <<< FAILURE!\n"
        "org.opentest4j.AssertionFailedError: boom\n"
        "\tat com.x.FooTest.foo(FooTest.java:10)\n", encoding="utf-8")
    r = surefire.parse_surefire_reports(tmp_path, ".")
    assert r.report_found and r.tests == 3 and r.failures == 1
    assert r.failed_cases[0].method == "foo"
    assert r.is_green() is False


def test_uncleaned_dir_forces_not_green(tmp_path: Path):
    d = _reports(tmp_path)
    (d / "TEST-ok.xml").write_text(
        '<testsuite tests="1" failures="0" errors="0" skipped="0"/>', encoding="utf-8")
    r = surefire.parse_surefire_reports(tmp_path, ".")
    assert r.is_green(uncleaned_dirs=False) is True
    assert r.is_green(uncleaned_dirs=True) is False   # 清理失败 -> 不可信


def test_failure_count_for_trajectory_fail_closed_minimum_one(tmp_path: Path):
    # 报告缺失: failures+errors==0 但不绿 -> 至少记 1
    r = surefire.parse_surefire_reports(tmp_path, ".")
    assert r.failure_count_for_trajectory() == 1


def test_failure_count_for_trajectory_errors_only(tmp_path: Path):
    # failures=0, errors=2 -> 至少记 1 (修复 errors-only 轮返回 0 的 bug)
    (tmp_path / "TEST-com.example.Foo.xml").write_text(
        '<testsuite tests="1" failures="0" errors="2" skipped="0"/>', encoding="utf-8")
    r = surefire.parse_surefire_reports(tmp_path, ".")
    assert r.failure_count_for_trajectory() >= 1


# --------------------------------------------------------------------------- #
# P3-新增-1: 补充边界测试
# --------------------------------------------------------------------------- #
def test_multiple_xml_files_aggregated(tmp_path: Path):
    """多个 TEST-*.xml 文件的计数应累加。"""
    d = _reports(tmp_path)
    (d / "TEST-com.x.FooTest.xml").write_text(
        '<testsuite tests="2" failures="0" errors="0" skipped="0">'
        '<testcase classname="com.x.FooTest" name="t1"/>'
        '<testcase classname="com.x.FooTest" name="t2"/>'
        '</testsuite>', encoding="utf-8")
    (d / "TEST-com.x.BarTest.xml").write_text(
        '<testsuite tests="3" failures="1" errors="0" skipped="0">'
        '<testcase classname="com.x.BarTest" name="b1"/>'
        '<testcase classname="com.x.BarTest" name="b2">'
        '<failure type="AssertionError" message="oops"/>'
        '</testcase>'
        '<testcase classname="com.x.BarTest" name="b3"/>'
        '</testsuite>', encoding="utf-8")
    r = surefire.parse_surefire_reports(tmp_path, ".")
    assert r.tests == 5
    assert r.failures == 1
    assert r.is_green() is False


def test_testsuites_root_element(tmp_path: Path):
    """testsuites 根节点(含多个 testsuite)应正确解析。"""
    d = _reports(tmp_path)
    (d / "TEST-all.xml").write_text(
        '<testsuites>'
        '<testsuite tests="1" failures="0" errors="0" skipped="0">'
        '<testcase classname="com.x.A" name="a1"/>'
        '</testsuite>'
        '<testsuite tests="1" failures="0" errors="0" skipped="1">'
        '<testcase classname="com.x.B" name="b1"><skipped/></testcase>'
        '</testsuite>'
        '</testsuites>', encoding="utf-8")
    r = surefire.parse_surefire_reports(tmp_path, ".")
    assert r.tests == 2
    assert r.skipped == 1
    assert r.is_green() is True


def test_txt_without_summary_fallback(tmp_path: Path):
    """TXT 无汇总行时应按失败用例兜底计数。"""
    d = _reports(tmp_path)
    (d / "com.x.FooTest.txt").write_text(
        "foo(com.x.FooTest)  Time elapsed: 0.01 s  <<< FAILURE!\n"
        "org.opentest4j.AssertionFailedError: expected 1\n"
        "\tat com.x.FooTest.foo(FooTest.java:10)\n"
        "bar(com.x.FooTest)  Time elapsed: 0.02 s  <<< ERROR!\n"
        "java.lang.NullPointerException: null\n"
        "\tat com.x.FooTest.bar(FooTest.java:20)\n", encoding="utf-8")
    r = surefire.parse_surefire_reports(tmp_path, ".")
    assert r.report_found is True
    assert r.failures == 1    # 兜底: FAILURE 计数
    assert r.errors == 1      # 兜底: ERROR 计数
    assert r.tests == 2       # 兜底: 总用例数
    assert len(r.failed_cases) == 2


def test_error_type_in_xml(tmp_path: Path):
    """XML 中的 error 类型用例应计入 errors 而非 failures。"""
    d = _reports(tmp_path)
    (d / "TEST-com.x.FooTest.xml").write_text(
        '<testsuite tests="1" failures="0" errors="1" skipped="0">'
        '<testcase classname="com.x.FooTest" name="t1">'
        '<error type="java.lang.NullPointerException" message="NPE"/>'
        '</testcase>'
        '</testsuite>', encoding="utf-8")
    r = surefire.parse_surefire_reports(tmp_path, ".")
    assert r.errors == 1
    assert r.failures == 0
    assert r.is_green() is False
    assert r.failed_cases[0].type == "java.lang.NullPointerException"


def test_module_path_handling(tmp_path: Path):
    """module 参数非空时应拼接子目录路径。"""
    d = tmp_path / "mod-a" / "target" / "surefire-reports"
    d.mkdir(parents=True)
    (d / "TEST-com.x.FooTest.xml").write_text(
        '<testsuite tests="1" failures="0" errors="0" skipped="0">'
        '<testcase classname="com.x.FooTest" name="t1"/>'
        '</testsuite>', encoding="utf-8")
    r = surefire.parse_surefire_reports(tmp_path, "mod-a")
    assert r.report_found is True
    assert r.tests == 1
    assert r.is_green() is True


def test_empty_report_dir_not_green(tmp_path: Path):
    """报告目录存在但无报告文件时 report_found=False, 不绿。"""
    _reports(tmp_path)
    r = surefire.parse_surefire_reports(tmp_path, ".")
    assert r.report_found is False
    assert r.is_green() is False


def test_skipped_counting_xml(tmp_path: Path):
    """跳过的测试用例应正确计入 skipped。"""
    d = _reports(tmp_path)
    (d / "TEST-com.x.FooTest.xml").write_text(
        '<testsuite tests="3" failures="0" errors="0" skipped="2">'
        '<testcase classname="com.x.FooTest" name="t1"/>'
        '<testcase classname="com.x.FooTest" name="t2"><skipped/></testcase>'
        '<testcase classname="com.x.FooTest" name="t3"><skipped/></testcase>'
        '</testsuite>', encoding="utf-8")
    r = surefire.parse_surefire_reports(tmp_path, ".")
    assert r.skipped == 2
    assert r.tests == 3
    assert r.is_green() is True
