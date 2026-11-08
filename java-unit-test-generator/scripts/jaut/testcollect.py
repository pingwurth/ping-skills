"""测试类发现(单类模式; 与 batch 副本 testcollect.py 同口径)。

历史缺陷(正是 batch 副本建此模块要修的): 单类模式只探测 {S}Test 一种
命名变体 —— 用户既有 FooTests/FooIT/FooTestCase/TestFoo 时探测不到,
误建空桩 FooTest.java, -Dtest 只跑空桩: 基线 0%、已覆盖方法误判
pending, 生成器重复造用户已有的测试。

目标类 {S} 的测试类命名变体全集(探测与采用共用):

    {S}Test / {S}Tests / {S}IT / {S}TestCase / Test{S}

单类模式的 state/提示词以**一个**测试类为中心(只能修改一个文件),
故探测命中多个时按确定性顺序取其一: {S}Test 优先, 其余按后缀序,
最后 Test 前缀(batch 批量模式为列表驱动, 全部并入 -Dtest)。
"""

from __future__ import annotations

from pathlib import Path

# 目标类测试类的命名变体: 后缀 4 种 + 前缀 1 种, 探测与采用共用
TEST_SUFFIXES: tuple[str, ...] = ("Test", "Tests", "IT", "TestCase")
TEST_PREFIX = "Test"


def _module_dir(project_root: Path, module: str) -> Path:
    """模块目录(module 为 '.' 时即项目根)。"""
    return project_root if module == "." else project_root / module


def find_test_classes(project_root: Path, module: str, target_class: str) -> list[str]:
    """查找目标类已存在的全部测试类 FQCN(覆盖全部命名变体)。

    返回顺序即确定性优先序: {S}Test 最先, 然后 Tests/IT/TestCase, 最后
    Test{S} —— 单类模式取首个, 批量模式全取。
    """
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


def existing_test_class_simple(project_root: Path, module: str,
                               target_class: str) -> str | None:
    """返回应采用的测试类简单名(命中变体按确定性优先序取首个)。

    无任何已存在测试类时返回 None —— 调用方回落 {S}Test 桩语义
    (decisions.test_simple_name / test_class_relpath)。
    """
    found = find_test_classes(project_root, module, target_class)
    if not found:
        return None
    return found[0].rsplit(".", 1)[-1]


def test_file_for(project_root: Path, module: str, target_class: str,
                  test_simple: str) -> Path:
    """测试类简单名对应的源文件路径(与 decisions.test_class_relpath 同布局)。"""
    pkg, _, _ = target_class.rpartition(".")
    parts = ["src", "test", "java"]
    if pkg:
        parts.extend(pkg.split("."))
    parts.append(f"{test_simple}.java")
    return _module_dir(project_root, module).joinpath(*parts)
