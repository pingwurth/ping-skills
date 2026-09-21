"""JaCoCo 覆盖率解析。

从 jacoco.xml 解析方法级行覆盖率, 从 jacoco.csv 解析类级行覆盖率(含内部类聚合)。
解析产物为 models.MethodCoverage / 覆盖率数值, 不做门槛判定(判定属 decisions)。

约定:
    - 内部类以 '$' 聚合到外部类 FQCN
    - excludes 排除在内部类聚合之前按完整类名(pkg.Outer$Inner)匹配, 模式为
      fnmatch(* 可跨 '.'/'$' 段, 与 JaCoCo 官方 excludes 语义一致), 命中的类
      不进入方法表与类级统计; 两种挂载方式(用户 pom / 完整坐标)行为一致
    - <init> -> 类简单名; <clinit> -> 'static {...}'
    - desc 人读化(委托 javasrc.jvm_desc_to_readable)
    - 方法唯一键 = name + desc
"""

from __future__ import annotations

import csv
import xml.etree.ElementTree as ET
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Sequence

from .javasrc import jvm_desc_to_readable
from .models import ClassCoverage, MethodCoverage, MethodKey, MethodStatus, coverage_rate
from .logutil import module_logger

# 便于调用方从单一模块取得覆盖率原语
__all__ = [
    "coverage_rate",
    "is_excluded",
    "parse_jacoco_xml",
    "parse_jacoco_csv",
    "class_rate_from_xml",
    "class_coverage",
    "report_paths",
    "aggregate_jacoco_xml",
    "aggregate_class_coverage",
]

_CLINIT = "static {...}"


def _is_synthetic_method(name: str) -> bool:
    """判断是否为合成方法（lambda、access、桥接方法）。

    合成方法名通常包含 '$' 符号（如 `lambda$foo$0`、`access$001`）。
    构造函数 `<init>` 和静态初始化块 `<clinit>` 虽然也包含 `$` 的前缀，
    但它们是合法的方法实体，需要排除。
    """
    return "$" in name and name not in ("<init>", "<clinit>")


def is_excluded(full_name: str, excludes: Sequence[str]) -> bool:
    """完整类名(含 '$' 内部类)是否命中任一排除模式。

    模式为 fnmatch(* / ?), '*' 可跨 '.' 与 '$' 段(与 JaCoCo 官方 excludes 语义一致);
    匹配发生在内部类聚合之前, 故 'com.foo.User$Builder' 可精确排除单个内部类。
    """
    return any(fnmatchcase(full_name, pat) for pat in excludes)


def report_paths(project_root: str | Path, module: str) -> tuple[Path, Path]:
    """返回模块的 (jacoco.xml, jacoco.csv) 路径(module 为空或 '.' 时用项目根)。"""
    base = Path(project_root) / module if module and module != "." else Path(project_root)
    jacoco = base / "target" / "site" / "jacoco"
    return jacoco / "jacoco.xml", jacoco / "jacoco.csv"


def _tag_endswith(element, suffix: str) -> bool:
    return str(element.tag).endswith(suffix)


def parse_jacoco_xml(xml_path: str | Path,
                     *, excludes: Sequence[str] = ()) -> dict[str, list[MethodCoverage]]:
    """解析 jacoco.xml 方法级行覆盖率; 失败返回空 dict。

    excludes 按完整类名(pkg.Outer$Inner, 归并内部类之前)匹配, 命中的类不进方法表。
    返回的 MethodCoverage.status 一律为 PENDING(默认), 轨迹为空,
    门槛/状态判定由上层(init/verify/decisions)负责。
    """
    method_cov: dict[str, list[MethodCoverage]] = {}
    try:
        root = ET.parse(str(xml_path)).getroot()
    except (ET.ParseError, OSError):
        return method_cov

    for pkg in root.iter():
        if not _tag_endswith(pkg, "package"):
            continue
        pkg_name = pkg.get("name", "").replace("/", ".")
        for cls in pkg:
            if not _tag_endswith(cls, "class"):
                continue
            simple = cls.get("name", "").rsplit("/", 1)[-1]
            full_name = f"{pkg_name}.{simple}" if pkg_name else simple
            if is_excluded(full_name, excludes):
                continue
            outer = simple.split("$")[0]
            fqcn = f"{pkg_name}.{outer}" if pkg_name else outer
            bucket = method_cov.setdefault(fqcn, [])
            for meth in cls:
                if not _tag_endswith(meth, "method"):
                    continue
                method = _parse_method(meth, simple)
                if method is not None:
                    bucket.append(method)
    return method_cov


def _parse_method(meth, simple_name: str) -> MethodCoverage | None:
    m_name = meth.get("name", "")
    # 跳过合成方法（lambda、access、桥接方法）
    if _is_synthetic_method(m_name):
        return None
    m_desc = meth.get("desc", "")
    if m_name == "<clinit>":
        m_name, m_desc = _CLINIT, ""
    elif m_name == "<init>":
        m_name = simple_name.replace("$", ".")
    m_desc = "" if m_name == _CLINIT else jvm_desc_to_readable(m_desc)

    covered = missed = 0
    for counter in meth:
        if _tag_endswith(counter, "counter") and counter.get("type") == "LINE":
            covered = int(counter.get("covered", "0"))
            missed = int(counter.get("missed", "0"))
            break
    return MethodCoverage(
        key=MethodKey(name=m_name, desc=m_desc),
        covered=covered, missed=missed,
        status=MethodStatus.PENDING,
    )


def parse_jacoco_csv(csv_path: str | Path, fqcn: str,
                     *, excludes: Sequence[str] = ()) -> tuple[int, int] | None:
    """类级行覆盖率: CSV 的 LINE_COVERED/LINE_MISSED 聚合(含内部类 '$' 行)。

    excludes 命中的类(完整名 pkg.Outer$Inner)不计入聚合。
    未找到目标类返回 None(由调用方回退 XML 聚合)。
    """
    pkg, _, simple = fqcn.rpartition(".")
    try:
        covered = missed = 0
        found = False
        with open(str(csv_path), newline="", encoding="utf-8") as fp:
            for row in csv.DictReader(fp):
                cls = row.get("CLASS", "")
                if row.get("PACKAGE", "") == pkg and (cls == simple or cls.startswith(simple + "$")):
                    if is_excluded(f"{pkg}.{cls}", excludes):
                        continue
                    covered += int(row.get("LINE_COVERED") or 0)
                    missed += int(row.get("LINE_MISSED") or 0)
                    found = True
        return (covered, missed) if found else None
    except (OSError, ValueError):
        return None


def class_rate_from_xml(
    xml_path: str | Path, fqcn: str, *, parsed_xml: dict[str, list[MethodCoverage]] | None = None,
    excludes: Sequence[str] = (),
) -> tuple[int, int]:
    """XML 兜底类级聚合: 目标类所有方法 LINE 计数求和(excludes 同 parse_jacoco_xml)。"""
    methods = (parsed_xml or parse_jacoco_xml(xml_path, excludes=excludes)).get(fqcn, [])
    return (sum(m.covered for m in methods), sum(m.missed for m in methods))


def class_coverage(
    xml_path: str | Path, csv_path: str | Path, fqcn: str,
    *, parsed_xml: dict[str, list[MethodCoverage]] | None = None,
    excludes: Sequence[str] = (),
) -> ClassCoverage:
    """类级覆盖率: CSV 优先, 缺失时回退 XML 聚合(excludes 对两者一致生效)。"""
    cov = (parse_jacoco_csv(csv_path, fqcn, excludes=excludes)
           if Path(csv_path).is_file() else None)
    covered, missed = (cov if cov
                       else class_rate_from_xml(xml_path, fqcn, parsed_xml=parsed_xml,
                                                excludes=excludes))
    return ClassCoverage.of(covered, missed)


# --------------------------------------------------------------------------- #
# 多模块聚合(批量模式)
# --------------------------------------------------------------------------- #
def aggregate_jacoco_xml(
    project_root: str | Path, modules: Sequence[str],
    *, excludes: Sequence[str] = (),
) -> dict[str, list[MethodCoverage]]:
    """遍历涉及模块各自的 jacoco.xml, 合并为 {fqcn: [MethodCoverage]}。

    单模块路径下退化为 parse_jacoco_xml。
    """
    log = module_logger()
    merged: dict[str, list[MethodCoverage]] = {}
    for m in modules:
        xml_path, _ = report_paths(project_root, m)
        if not Path(xml_path).is_file():
            log.warning(f"模块 {m} 的 jacoco.xml 不存在: {xml_path}")
            continue
        parsed = parse_jacoco_xml(xml_path, excludes=excludes)
        for fqcn, methods in parsed.items():
            if fqcn in merged:
                merged[fqcn].extend(methods)
            else:
                merged[fqcn] = list(methods)
    return merged


def aggregate_class_coverage(
    project_root: str | Path, modules: Sequence[str], fqcn: str,
    *, parsed_xml: dict[str, list[MethodCoverage]] | None = None,
    excludes: Sequence[str] = (),
) -> ClassCoverage:
    """多模块类级覆盖率聚合: 遍历各模块 CSV/XML 求和。

    单模块路径下退化为 class_coverage。
    """
    if len(modules) == 1:
        xml_path, csv_path = report_paths(project_root, modules[0])
        return class_coverage(xml_path, csv_path, fqcn, parsed_xml=parsed_xml,
                              excludes=excludes)
    log = module_logger()
    total_covered = 0
    total_missed = 0
    found = False
    for m in modules:
        xml_path, csv_path = report_paths(project_root, m)
        if not Path(xml_path).is_file():
            continue
        cov = (parse_jacoco_csv(csv_path, fqcn, excludes=excludes)
               if Path(csv_path).is_file() else None)
        if cov:
            total_covered += cov[0]
            total_missed += cov[1]
            found = True
        else:
            c, d = class_rate_from_xml(xml_path, fqcn, parsed_xml=parsed_xml,
                                       excludes=excludes)
            if c or d:
                total_covered += c
                total_missed += d
                found = True
    if not found:
        log.warning(f"多模块聚合未找到类 {fqcn} 的覆盖率数据")
    return ClassCoverage.of(total_covered, total_missed)
