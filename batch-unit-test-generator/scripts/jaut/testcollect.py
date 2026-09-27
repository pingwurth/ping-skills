"""批量测试类发现与桩兜底(batch_init / batch_finish / batch_diff 共用)。

历史实现口径不一致: batch_diff 探测 Test 前缀 + Test/Tests/TestCase 后缀,
batch_init/batch_finish 只收集执行 Test/Tests/IT —— 用户既有 FooTestCase/
TestFoo 时基线与终验都不执行它, 覆盖率被低估、已覆盖方法误判 pending
(子代理重复劳动); 反向 FooIT 被执行却不被视为"已有测试"。本模块统一
两种口径, 目标类 {S} 的测试类命名变体全集:

    {S}Test / {S}Tests / {S}IT / {S}TestCase / Test{S}

桩兜底(与单类模式 init_coverage 的桩语义一致): 无任何已存在测试类的
目标类创建最小桩测试类, 保证:
    - 批量 mvn 的 -Dtest 恒非空(空列表会退化为模块全量测试, SKILL.md §4);
    - surefire 有 fork、JaCoCo 能产出覆盖率报告(全新类/全新模块零测试场景);
    - 失败可回滚、成功可清理, 不在用户项目留下意外文件。
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from .decisions import test_class_relpath, test_simple_name

# 目标类测试类的命名变体: 后缀 4 种 + 前缀 1 种, 探测与收集执行共用
TEST_SUFFIXES: tuple[str, ...] = ("Test", "Tests", "IT", "TestCase")
TEST_PREFIX = "Test"


def _module_dir(project_root: Path, module: str) -> Path:
    """模块目录(module 为 '.' 时即项目根)。"""
    return project_root if module == "." else project_root / module


def find_test_classes(project_root: Path, module: str, target_class: str) -> list[str]:
    """查找目标类已存在的全部测试类 FQCN(覆盖全部命名变体)。"""
    pkg, _, simple = target_class.rpartition(".")
    search_dir = _module_dir(project_root, module) / "src" / "test" / "java"
    if pkg:
        search_dir = search_dir / pkg.replace(".", "/")
    if not search_dir.is_dir():
        return []
    names = [simple + s for s in TEST_SUFFIXES] + [TEST_PREFIX + simple]
    found = []
    for name in names:
        if (search_dir / f"{name}.java").is_file():
            found.append(f"{pkg}.{name}" if pkg else name)
    return found


def has_existing_test(project_root: Path, module: str, fqcn: str) -> bool:
    """探测 src/test/java 下是否已有目标类的测试类(任意命名变体)。"""
    return bool(find_test_classes(project_root, module, fqcn))


def ensure_stub_test_class(project_root: Path, module: str, fqcn: str,
                           logger) -> tuple[bool, Path]:
    """目标类无任何已存在测试类时创建最小桩测试类。

    桩类仅含类声明(无测试方法, 不导入任何 JUnit 版本, 避免与 JUnit 4/5 冲突),
    保证 test-compile 不被跳过、JaCoCo 能产出覆盖率报告(所有方法 0% 覆盖)。

    Returns:
        (是否新建了桩文件, 桩测试文件路径)。
    """
    test_file = _module_dir(project_root, module) / test_class_relpath(fqcn)
    if test_file.is_file():
        return False, test_file

    pkg, _, _ = fqcn.rpartition(".")
    test_simple = test_simple_name(fqcn)
    pkg_line = f"package {pkg};\n\n" if pkg else ""
    content = (
        f"{pkg_line}"
        f"public class {test_simple} {{\n"
        f"    // 桩测试占位: 确保 JaCoCo 能产出覆盖率报告\n"
        f"    // 后续由子代理补充真实测试方法\n"
        f"}}\n"
    )
    test_file.parent.mkdir(parents=True, exist_ok=True)
    with open(test_file, "w", encoding="utf-8") as fp:
        fp.write(content)
    logger.info(f"未找到测试文件, 已创建桩测试类: {test_file}")
    return True, test_file


def collect_test_classes_with_stub(
        project_root: Path, pairs: Sequence[tuple[str, str]],
        logger) -> tuple[list[str], list[Path]]:
    """收集 (module, fqcn) 列表对应的测试类; 无测试类的目标类桩兜底。

    Args:
        project_root: 项目根目录。
        pairs: (module, fqcn) 序列(batch_init 传候选 dict 字段, batch_finish
            传活跃 entry 字段)。
        logger: 日志器。

    Returns:
        (去重排序后的测试类 FQCN 列表, 本次新建的桩文件路径列表)。
        返回的测试类列表恒非空(每个目标类要么有已存在测试类, 要么有桩)。
    """
    all_test_classes: list[str] = []
    stub_files: list[Path] = []
    for module, fqcn in pairs:
        test_classes = find_test_classes(project_root, module, fqcn)
        if not test_classes:
            created, stub_file = ensure_stub_test_class(
                project_root, module, fqcn, logger)
            if created:
                stub_files.append(stub_file)
                # 桩即 {Simple}Test 命名, 创建后可被 find 发现
                test_classes = find_test_classes(project_root, module, fqcn)
        all_test_classes.extend(test_classes)
    return sorted(set(all_test_classes)), stub_files


def remove_stub_files(stub_files: list[Path], logger, *, reason: str) -> None:
    """删除本次运行创建的桩测试文件(mvn 失败回滚或基线成功清理)。"""
    for stub_file in stub_files:
        try:
            stub_file.unlink(missing_ok=True)
            logger.info(f"{reason}: {stub_file}")
        except OSError:
            logger.warning(f"桩测试文件删除失败({reason}): {stub_file}")
