"""Surefire 测试报告解析。

测试是否真正通过以 surefire-reports 为唯一判定依据(不能只依据 Maven exit code)。
解析产物为 models.TestResult, 其 is_green() 采用 fail-closed: 报告缺失/不可解析/
存在失败用例一律按不绿计, 防止绕过双条件闸门。

优先解析 TEST-*.xml(testsuite 计数属性 + testcase 的 failure/error 子元素);
无 XML 时回退解析 *.txt。无法解析的报告计入 parse_errors(调用方按失败计)。
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from .logutil import module_logger
from .models import FailedCase, TestResult

_SUMMARY_RE = re.compile(
    r"Tests run:\s*(\d+),\s*Failures:\s*(\d+),\s*Errors:\s*(\d+),\s*Skipped:\s*(\d+)")
_CASE_RE = re.compile(
    r"^(\S+)\(([^()]+)\)\s+Time elapsed:.*?<<<\s*(FAILURE|ERROR)!")


def report_paths(project_root: str | Path, module: str) -> Path:
    """返回模块的 surefire-reports 目录路径(module 为空或 '.' 时用项目根)。"""
    base = Path(project_root) / module if module and module != "." else Path(project_root)
    return base / "target" / "surefire-reports"


def _tag_endswith(element, suffix: str) -> bool:
    """判断 XML 元素标签是否以指定后缀结尾(忽略命名空间前缀)。"""
    return str(element.tag).endswith(suffix)


def _suite_int_attr(suite, attr: str, fallback: int) -> int:
    """testsuite 计数属性安全转 int, 缺失/非法时回退用例级统计值。"""
    try:
        return int(suite.get(attr))
    except (TypeError, ValueError):
        return fallback


def parse_surefire_reports(project_root: str | Path, module: str) -> TestResult:
    """解析模块 surefire-reports 目录, 返回 TestResult。

    目录不存在 / 无报告文件 -> 全零且 report_found=False(调用方按不绿计)。
    """
    result = TestResult()
    report_dir = report_paths(project_root, module)
    if not report_dir.is_dir():
        return result

    xml_files = sorted(report_dir.glob("TEST-*.xml"))
    if xml_files:
        result.report_found = True
        for xf in xml_files:
            _parse_xml_report(xf, result)
    else:
        txt_files = sorted(report_dir.glob("*.txt"))
        if not txt_files:
            return result
        result.report_found = True
        for tf in txt_files:
            _parse_txt_report(tf, result)

    result.assertion_failures = sum(1 for fc in result.failed_cases if fc.is_assertion)
    return result


def _parse_xml_report(xf: Path, result: TestResult) -> None:
    try:
        root = ET.parse(str(xf)).getroot()
    except (ET.ParseError, OSError):
        # 畸形/不可读报告不得静默跳过, 否则失败计数蒸发; 按失败计(fail-closed)
        result.parse_errors += 1
        module_logger().warning(f"Surefire XML 无法解析, 按失败计: {xf}")
        return

    suites = ([root] if _tag_endswith(root, "testsuite")
              else [el for el in root.iter() if _tag_endswith(el, "testsuite")])
    for suite in suites:
        cases = [el for el in suite.iter() if _tag_endswith(el, "testcase")]
        for case in cases:
            _collect_failed_case(case, result)

        def _has(case, suffix: str) -> bool:
            return any(_tag_endswith(ch, suffix) for ch in case)

        result.tests += _suite_int_attr(suite, "tests", len(cases))
        result.failures += _suite_int_attr(suite, "failures", sum(1 for c in cases if _has(c, "failure")))
        result.errors += _suite_int_attr(suite, "errors", sum(1 for c in cases if _has(c, "error")))
        result.skipped += _suite_int_attr(suite, "skipped", sum(1 for c in cases if _has(c, "skipped")))


def _collect_failed_case(case, result: TestResult) -> None:
    """每个 testcase 至多计一次失败(取首个 failure/error 子元素)。"""
    for child in case:
        if _tag_endswith(child, ("failure", "error")):
            result.failed_cases.append(FailedCase(
                class_name=case.get("classname", ""),
                method=case.get("name", ""),
                type=child.get("type", "") or "",
                message=child.get("message", "") or "",
            ))
            break


def _parse_txt_report(tf: Path, result: TestResult) -> None:
    try:
        text = tf.read_text(encoding="utf-8", errors="replace")
    except OSError:
        result.parse_errors += 1
        module_logger().warning(f"Surefire txt 不可读, 按失败计: {tf}")
        return

    summary_found = False
    for m in _SUMMARY_RE.finditer(text):
        result.tests += int(m.group(1))
        result.failures += int(m.group(2))
        result.errors += int(m.group(3))
        result.skipped += int(m.group(4))
        summary_found = True

    lines = text.splitlines()
    case_kinds: list[str] = []
    for i, line in enumerate(lines):
        m = _CASE_RE.match(line)
        if not m:
            continue
        case_kinds.append(m.group(3))
        # 失败用例紧随其后的首个非空非堆栈行为异常类型行: type: message
        exc_line = next((ln.strip() for ln in lines[i + 1:]
                         if ln.strip() and not ln.strip().startswith("at ")), "")
        etype, _, emsg = exc_line.partition(":")
        result.failed_cases.append(FailedCase(
            class_name=m.group(2), method=m.group(1),
            type=etype.strip(), message=emsg.strip(),
        ))

    if not summary_found:  # 无汇总行时按失败用例兜底计数
        result.failures += sum(1 for k in case_kinds if k == "FAILURE")
        result.errors += sum(1 for k in case_kinds if k == "ERROR")
        result.tests += len(case_kinds)
