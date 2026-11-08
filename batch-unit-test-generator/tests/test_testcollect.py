"""testcollect 单元测试: 命名变体全集发现 + 桩兜底语义(P1-2/P2-9)。

历史缺陷: batch_diff 探测与 batch_init/batch_finish 收集执行口径不一致
(探测含 Test 前缀/TestCase 后缀但执行不含; 执行含 IT 但探测不含),
本模块统一为 5 种变体; 桩兜底保证 -Dtest 恒非空与零测试模块可产出报告。
"""

from __future__ import annotations

import logging
from pathlib import Path

from jaut.testcollect import (
    TEST_PREFIX,
    TEST_SUFFIXES,
    collect_test_classes_with_stub,
    ensure_stub_test_class,
    existing_test_class_simple,
    find_test_classes,
    has_existing_test,
    remove_stub_files,
)
from jaut import testcollect as _testcollect
# 注意: test_file_for 名以 test_ 开头, 直接 import 会被 pytest 误收集为测试


def _logger() -> logging.Logger:
    return logging.getLogger("test_testcollect")


def _write_test(project_root: Path, module: str, fqcn: str,
                simple_test: str) -> Path:
    pkg, _, _ = fqcn.rpartition(".")
    d = project_root / module / "src" / "test" / "java"
    if pkg:
        d = d / pkg.replace(".", "/")
    d.mkdir(parents=True, exist_ok=True)
    t = d / f"{simple_test}.java"
    t.write_text(f"package {pkg};\npublic class {simple_test} {{}}\n",
                 encoding="utf-8")
    return t


# --------------------------------------------------------------------------- #
# 命名变体全集(探测与收集执行共用)
# --------------------------------------------------------------------------- #
def test_suffixes_contract():
    """命名变体契约: 4 后缀 + 1 前缀(SKILL.md §4 与 diff-analysis.md §5 同步)。"""
    assert TEST_SUFFIXES == ("Test", "Tests", "IT", "TestCase")
    assert TEST_PREFIX == "Test"


def test_find_test_classes_all_variants(tmp_path):
    """5 种命名变体全部被发现(任何一种都参与收集执行)。"""
    for name in ("BarTest", "BarTests", "BarIT", "BarTestCase", "TestBar"):
        _write_test(tmp_path, ".", "com.foo.Bar", name)
    found = find_test_classes(tmp_path, ".", "com.foo.Bar")
    assert sorted(found) == sorted([
        "com.foo.BarTest", "com.foo.BarTests", "com.foo.BarIT",
        "com.foo.BarTestCase", "com.foo.TestBar"])


def test_find_test_classes_empty_when_no_tests(tmp_path):
    assert find_test_classes(tmp_path, ".", "com.foo.Baz") == []
    assert has_existing_test(tmp_path, ".", "com.foo.Baz") is False


def test_has_existing_test_it_only(tmp_path):
    """仅 FooIT 存在也算"已有测试"(与收集执行同口径, 不再互相矛盾)。"""
    _write_test(tmp_path, ".", "com.foo.Foo", "FooIT")
    assert has_existing_test(tmp_path, ".", "com.foo.Foo") is True


def test_find_test_classes_module_scope(tmp_path):
    """module 非 '.' 时在模块子目录下查找。"""
    _write_test(tmp_path, "mod-a", "com.foo.Mod", "ModTest")
    assert find_test_classes(tmp_path, "mod-a", "com.foo.Mod") == ["com.foo.ModTest"]
    assert find_test_classes(tmp_path, ".", "com.foo.Mod") == []


# --------------------------------------------------------------------------- #
# 桩兜底(P1-2)
# --------------------------------------------------------------------------- #
def test_ensure_stub_test_class_creates_minimal_stub(tmp_path):
    """桩仅含类声明: 不导入任何 JUnit(避免与项目 JUnit 4/5 冲突)。"""
    created, stub = ensure_stub_test_class(
        tmp_path, ".", "com.foo.Qux", _logger())
    assert created is True
    assert stub.name == "QuxTest.java"
    content = stub.read_text(encoding="utf-8")
    assert "import" not in content
    assert "class QuxTest" in content
    # 已存在则不重复创建
    created2, stub2 = ensure_stub_test_class(
        tmp_path, ".", "com.foo.Qux", _logger())
    assert created2 is False
    assert stub2 == stub


def test_collect_test_classes_with_stub_mixed(tmp_path):
    """混合场景: 有测试类直接收集; 无测试类桩兜底且桩可被再次发现。"""
    _write_test(tmp_path, ".", "com.foo.Has", "HasTests")
    tests, stubs = collect_test_classes_with_stub(
        tmp_path, [(".", "com.foo.Has"), (".", "com.foo.Empty")], _logger())
    assert tests == ["com.foo.EmptyTest", "com.foo.HasTests"]
    assert [s.name for s in stubs] == ["EmptyTest.java"]


def test_collect_test_classes_with_stub_returns_non_empty(tmp_path):
    """返回列表恒非空: 空列表会让 -Dtest 退化为模块全量测试。"""
    tests, stubs = collect_test_classes_with_stub(
        tmp_path, [(".", "com.foo.Solo")], _logger())
    assert tests == ["com.foo.SoloTest"]
    assert len(stubs) == 1


def test_remove_stub_files(tmp_path):
    """回滚/清理: 删除本次创建的桩文件, 不在用户项目留下意外文件。"""
    _, stub = ensure_stub_test_class(
        tmp_path, ".", "com.foo.Roll", _logger())
    remove_stub_files([stub], _logger(), reason="回滚")
    assert not stub.exists()


# --------------------------------------------------------------------------- #
# c061 双副本 parity(F01): 类级 state 采用实际存在的测试类, 而非硬编码 {S}Test
# --------------------------------------------------------------------------- #
# 历史缺陷: e7dff8b 的五变体采用只落在 java 副本; batch make_plan 仍硬编码
# {S}Test, 用户既有 FooTests 时子代理被指向不存在的 FooTest.java —— 误建
# 重复测试类、-Dtest 跑错类、真实套件覆盖率从不被测量(方法误判 pending)。
def test_existing_test_class_simple_prefers_primary(tmp_path):
    """UserServiceTests 存在(无 UserServiceTest) -> 采用它; 主变体优先。"""
    fqcn = "com.foo.UserService"
    _write_test(tmp_path, ".", fqcn, "UserServiceTests")
    assert existing_test_class_simple(tmp_path, ".", fqcn) == "UserServiceTests"
    _write_test(tmp_path, ".", fqcn, "UserServiceTest")
    assert existing_test_class_simple(tmp_path, ".", fqcn) == "UserServiceTest"
    assert existing_test_class_simple(tmp_path, ".", "com.foo.Order") is None


def test_test_file_for_layout_matches_decisions(tmp_path):
    """采用的测试类文件路径与 decisions.test_class_relpath 同布局。"""
    from jaut import decisions

    p = _testcollect.test_file_for(tmp_path, "m1", "com.foo.UserService", "UserServiceIT")
    assert p == tmp_path / "m1" / "src" / "test" / "java" / "com" / "foo" \
        / "UserServiceIT.java"
    # 默认 {S}Test 命名时与 decisions 推导一致
    default = tmp_path / "m1" / decisions.test_class_relpath("com.foo.UserService")
    assert _testcollect.test_file_for(
        tmp_path, "m1", "com.foo.UserService", "UserServiceTest") == default


def test_ensure_test_file_exists_adopts_existing_variant(tmp_path):
    """init_coverage 探测: 既有 UserServiceTests 时不建桩、返回该文件。"""
    from init_coverage import _ensure_test_file_exists

    fqcn = "com.foo.UserService"
    _write_test(tmp_path, ".", fqcn, "UserServiceTests")
    created, test_file = _ensure_test_file_exists(
        tmp_path, ".", fqcn, _logger())
    assert created is False
    assert test_file == tmp_path / "src" / "test" / "java" / "com" / "foo" \
        / "UserServiceTests.java"
    # 无既有测试类时才建桩(默认 {S}Test 命名)
    created2, stub = _ensure_test_file_exists(
        tmp_path, ".", "com.foo.Order", _logger())
    assert created2 is True
    assert stub.name == "OrderTest.java"


def test_init_plan_adopts_existing_variant(tmp_path):
    """make_plan._init_plan: state.test_class_* 采用实际存在的测试类。

    batch 侧曾被硬编码 {S}Test: FooTests 用户的类级 workdir 指向不存在
    的 FooTest.java, 提示词让子代理创建重复测试类。"""
    from jaut import models
    from make_plan import _init_plan

    fqcn = "com.foo.UserService"
    _write_test(tmp_path, ".", fqcn, "UserServiceTests")
    state = models.State(
        project_root=str(tmp_path), workdir=str(tmp_path / ".agent" / "w"),
        threshold=80.0, target_class=fqcn, module=".",
        jacoco_version="0.8.12", coverage_excludes=[], mvn_log="mvn.log",
        class_coverage=models.ClassCoverage(rate=0.0, covered=0, missed=3),
        methods=[models.MethodCoverage(
            key=models.MethodKey(name="m1", desc=""), covered=0, missed=1,
            status=models.MethodStatus.PENDING)],
    )
    _init_plan(state, _logger())
    assert state.test_class_simple == "UserServiceTests"
    assert state.test_class_file.endswith("UserServiceTests.java")
    assert state.plan["test_class_file"].endswith("UserServiceTests.java")
