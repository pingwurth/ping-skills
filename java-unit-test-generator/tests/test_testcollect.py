# -*- coding: utf-8 -*-
"""testcollect(测试类命名变体发现)回归测试 —— c061 双副本 parity。

历史缺陷: 单类模式只探测 {S}Test 一种变体, 用户既有 FooTests/FooIT/
FooTestCase/TestFoo 时误建空桩、真实套件从不被执行(基线 0%、
已覆盖方法误判 pending)。本组测试锁定: 探测覆盖全部变体、确定性
优先序、init/make_plan 采用实际存在的测试类而非默认 {S}Test。
"""
from pathlib import Path

from jaut import testcollect


def _make_test_file(root: Path, module: str, fqcn: str, simple: str) -> None:
    pkg, _, _ = fqcn.rpartition(".")
    d = root if module == "." else root / module
    d = d / "src" / "test" / "java"
    if pkg:
        d = d / Path(*pkg.split("."))
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{simple}.java").write_text(
        f"package {pkg};\npublic class {simple} {{}}\n" if pkg
        else f"public class {simple} {{}}\n", encoding="utf-8")


def test_find_test_classes_all_variants(tmp_path):
    """全部 5 种命名变体都被发现(FQCN 形态)。"""
    fqcn = "com.foo.UserService"
    for simple in ("UserServiceTest", "UserServiceTests", "UserServiceIT",
                   "UserServiceTestCase", "TestUserService"):
        _make_test_file(tmp_path, ".", fqcn, simple)
    found = testcollect.find_test_classes(tmp_path, ".", fqcn)
    assert found == [
        "com.foo.UserServiceTest", "com.foo.UserServiceTests",
        "com.foo.UserServiceIT", "com.foo.UserServiceTestCase",
        "com.foo.TestUserService",
    ]


def test_find_test_classes_deterministic_order(tmp_path):
    """{S}Test 优先, 然后 Tests/IT/TestCase, 最后 Test 前缀。"""
    fqcn = "Foo"
    _make_test_file(tmp_path, ".", fqcn, "TestFoo")
    _make_test_file(tmp_path, ".", fqcn, "FooIT")
    _make_test_file(tmp_path, ".", fqcn, "FooTest")
    found = testcollect.find_test_classes(tmp_path, ".", fqcn)
    assert found == ["FooTest", "FooIT", "TestFoo"]


def test_find_test_classes_none_existing(tmp_path):
    """无任何测试类 -> 空列表(调用方回落桩语义)。"""
    assert testcollect.find_test_classes(tmp_path, ".", "com.foo.Foo") == []
    assert testcollect.has_existing_test(tmp_path, ".", "com.foo.Foo") is False
    assert testcollect.existing_test_class_simple(
        tmp_path, ".", "com.foo.Foo") is None


def test_existing_test_class_simple_prefers_primary(tmp_path):
    """UserServiceTests 存在(无 UserServiceTest) -> 采用它而非建桩。"""
    fqcn = "com.foo.UserService"
    _make_test_file(tmp_path, ".", fqcn, "UserServiceTests")
    assert testcollect.existing_test_class_simple(tmp_path, ".", fqcn) \
        == "UserServiceTests"
    # 主变体存在时优先
    _make_test_file(tmp_path, ".", fqcn, "UserServiceTest")
    assert testcollect.existing_test_class_simple(tmp_path, ".", fqcn) \
        == "UserServiceTest"


def test_test_file_for_layout_matches_decisions(tmp_path):
    """采用的测试类文件路径与 decisions.test_class_relpath 同布局。"""
    from jaut import decisions
    p = testcollect.test_file_for(tmp_path, "m1", "com.foo.UserService",
                                  "UserServiceIT")
    assert p == tmp_path / "m1" / "src" / "test" / "java" / "com" / "foo" \
        / "UserServiceIT.java"
    # 默认 {S}Test 命名时与 decisions 推导一致
    default = tmp_path / "m1" / decisions.test_class_relpath("com.foo.UserService")
    assert testcollect.test_file_for(
        tmp_path, "m1", "com.foo.UserService", "UserServiceTest") == default


def test_ensure_test_file_exists_adopts_existing_variant(tmp_path):
    """init_coverage 探测: 既有 UserServiceTests 时不建桩、返回该文件。"""
    from init_coverage import _ensure_test_file_exists
    import logging

    fqcn = "com.foo.UserService"
    _make_test_file(tmp_path, ".", fqcn, "UserServiceTests")
    created, test_file = _ensure_test_file_exists(
        tmp_path, ".", fqcn, logging.getLogger("t"))
    assert created is False
    assert test_file == tmp_path / "src" / "test" / "java" / "com" / "foo" \
        / "UserServiceTests.java"
    # 无既有测试类时才建桩(默认 {S}Test 命名)
    created2, stub = _ensure_test_file_exists(
        tmp_path, ".", "com.foo.Order", logging.getLogger("t"))
    assert created2 is True
    assert stub.name == "OrderTest.java"


def test_init_plan_adopts_existing_variant(tmp_path):
    """make_plan._init_plan: state.test_class_* 采用实际存在的测试类。"""
    from jaut import models
    from make_plan import _init_plan
    import logging

    fqcn = "com.foo.UserService"
    _make_test_file(tmp_path, ".", fqcn, "UserServiceTests")
    state = models.State(
        project_root=str(tmp_path), workdir=str(tmp_path / ".agent" / "w"),
        threshold=80.0, target_class=fqcn, module=".",
        jacoco_version="0.8.12", coverage_excludes=[], mvn_log="mvn.log",
        class_coverage=models.ClassCoverage(rate=0.0, covered=0, missed=3),
        methods=[models.MethodCoverage(
            key=models.MethodKey(name="m1", desc=""), covered=0, missed=1,
            status=models.MethodStatus.PENDING)],
    )
    _init_plan(state, logging.getLogger("t"))
    assert state.test_class_simple == "UserServiceTests"
    assert state.test_class_file.endswith("UserServiceTests.java")
    assert state.plan["test_class_file"].endswith("UserServiceTests.java")
