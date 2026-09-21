"""prompt 提示词组装单测。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from jaut import config
from jaut.models import MethodCoverage, MethodKey, State
from jaut.prompt import PromptBuilder, default_rules_file


# --------------------------------------------------------------------------- #
# Helper
# --------------------------------------------------------------------------- #
def _make_state(
    tmp_path: Path,
    target_class: str = "com.example.FooService",
    source_file: Path | None = None,
    test_class_file: str | None = None,
    test_class_simple: str = "FooServiceTest",
    threshold: float = 80.0,
    iteration: int = 0,
    current_method_name: str = "doSomething",
    current_method_desc: str = "(int, String)",
    current_method_covered: int = 0,
    current_method_missed: int = 10,
) -> State:
    """构造测试用 State。source_file/test_class_file 基于 tmp_path。"""
    if source_file is None:
        source_file = tmp_path / "src" / "main" / "java" / "com" / "example" / "FooService.java"
    if test_class_file is None:
        test_class_file = str(tmp_path / "src" / "test" / "java" / "com" / "example" / "FooServiceTest.java")

    state = State()
    state.target_class = target_class
    state.source_file = str(source_file)
    state.test_class_file = test_class_file
    state.test_class_simple = test_class_simple
    state.threshold = threshold
    state.iteration = iteration
    state.current_method = MethodKey(name=current_method_name, desc=current_method_desc)
    state.methods = [
        MethodCoverage(
            key=state.current_method,
            covered=current_method_covered,
            missed=current_method_missed,
        )
    ]
    return state


def _builder(rules_file: Path | None = None) -> PromptBuilder:
    """构造 PromptBuilder。"""
    if rules_file is None:
        rules_file = Path("/rules/UnitTestRules.md")
    return PromptBuilder(rules_file)


def _write_source(state: State, content: str = "class FooService {}") -> None:
    """写入源文件。"""
    source = Path(state.source_file)
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(content, encoding="utf-8")


def _write_test(state: State, content: str = "class FooServiceTest {}") -> None:
    """写入测试文件。"""
    test = Path(state.test_class_file)
    test.parent.mkdir(parents=True, exist_ok=True)
    test.write_text(content, encoding="utf-8")


# --------------------------------------------------------------------------- #
# default_rules_file
# --------------------------------------------------------------------------- #
def test_default_rules_file_path():
    scripts_dir = Path("/project/scripts")
    result = default_rules_file(scripts_dir)
    assert result == Path("/project/references/UnitTestRules.md")


def test_default_rules_file_with_custom_config(tmp_path):
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    result = default_rules_file(scripts_dir)
    assert result.name == config.UNIT_TEST_RULES_FILENAME
    assert config.REFERENCES_DIRNAME in str(result)


# --------------------------------------------------------------------------- #
# PromptBuilder.build - 基础流程
# --------------------------------------------------------------------------- #
def test_build_includes_header(tmp_path):
    state = _make_state(tmp_path)
    _write_source(state)

    builder = _builder()
    with patch("jaut.prompt.extract_with_helpers", return_value=([], [])):
        prompt = builder.build(state, tmp_path)

    assert "com.example.FooService" in prompt
    assert "doSomething" in prompt
    assert "(int, String)" in prompt
    assert "第 1 轮" in prompt


def test_build_includes_threshold_and_rate(tmp_path):
    state = _make_state(tmp_path, threshold=85.0, current_method_covered=3, current_method_missed=7)
    _write_source(state)

    builder = _builder()
    with patch("jaut.prompt.extract_with_helpers", return_value=([], [])):
        prompt = builder.build(state, tmp_path)

    assert "85.0%" in prompt
    assert "30.0%" in prompt  # 3/(3+7) = 30%


def test_build_includes_rules_file_path(tmp_path):
    state = _make_state(tmp_path)
    _write_source(state)

    rules_file = Path("/custom/path/UnitTestRules.md")
    builder = _builder(rules_file)
    with patch("jaut.prompt.extract_with_helpers", return_value=([], [])):
        prompt = builder.build(state, tmp_path)

    assert str(rules_file) in prompt


def test_build_includes_source_and_test_paths(tmp_path):
    state = _make_state(tmp_path)
    _write_source(state)

    builder = _builder()
    with patch("jaut.prompt.extract_with_helpers", return_value=([], [])):
        prompt = builder.build(state, tmp_path)

    assert state.source_file in prompt
    assert state.test_class_file in prompt


def test_build_round_1_no_mvn_log(tmp_path):
    """第 1 轮不应包含 mvn.log 提示。"""
    state = _make_state(tmp_path, iteration=0)
    _write_source(state)

    builder = _builder()
    with patch("jaut.prompt.extract_with_helpers", return_value=([], [])):
        prompt = builder.build(state, tmp_path)

    assert "上一轮未达标" not in prompt


def test_build_round_2_includes_mvn_log(tmp_path):
    """第 2 轮起应包含 mvn.log 提示。"""
    state = _make_state(tmp_path, iteration=1)
    state.mvn_log = str(tmp_path / "mvn.log")
    _write_source(state)

    builder = _builder()
    with patch("jaut.prompt.extract_with_helpers", return_value=([], [])):
        prompt = builder.build(state, tmp_path)

    assert "上一轮未达标" in prompt
    assert state.mvn_log in prompt


def test_build_round_2_default_mvn_log_path(tmp_path):
    """第 2 轮未指定 mvn_log 时使用默认路径。"""
    state = _make_state(tmp_path, iteration=1)
    state.mvn_log = None
    _write_source(state)

    builder = _builder()
    with patch("jaut.prompt.extract_with_helpers", return_value=([], [])):
        prompt = builder.build(state, tmp_path)

    assert str(tmp_path / config.MVN_LOG_FILENAME) in prompt


def test_build_test_not_exists_includes_skeleton(tmp_path):
    """测试类不存在时应包含骨架要求。"""
    state = _make_state(tmp_path, test_class_file=str(tmp_path / "nonexistent" / "FooTest.java"))
    _write_source(state)

    builder = _builder()
    with patch("jaut.prompt.extract_with_helpers", return_value=([], [])):
        prompt = builder.build(state, tmp_path)

    assert "测试类尚不存在" in prompt
    assert "JUnit5" in prompt
    assert "Mockito" in prompt


def test_build_test_exists_no_skeleton(tmp_path):
    """测试类已存在时不应包含骨架要求。"""
    state = _make_state(tmp_path)
    _write_source(state)
    _write_test(state)

    builder = _builder()
    with patch("jaut.prompt.extract_with_helpers", return_value=([], [])):
        prompt = builder.build(state, tmp_path)

    assert "测试类尚不存在" not in prompt


def test_build_includes_main_method_source(tmp_path):
    """应包含主方法源码。"""
    state = _make_state(tmp_path)
    _write_source(state, "class FooService { void doSomething() {} }")

    main_block = "void doSomething(int x, String y) {\n    // impl\n}"
    builder = _builder()
    with patch("jaut.prompt.extract_with_helpers", return_value=([main_block], [])):
        prompt = builder.build(state, tmp_path)

    assert "当前方法源码" in prompt
    assert "doSomething" in prompt


def test_build_includes_helper_methods_source(tmp_path):
    """应包含被调用的 private 方法源码。"""
    state = _make_state(tmp_path)
    _write_source(state, "class FooService { void doSomething() {} }")

    main_block = "void doSomething() { helper(); }"
    helper_block = "private void helper() { /* impl */ }"
    builder = _builder()
    with patch("jaut.prompt.extract_with_helpers", return_value=([main_block], [helper_block])):
        prompt = builder.build(state, tmp_path)

    assert "private 方法" in prompt
    assert "helper" in prompt


def test_build_includes_requirements(tmp_path):
    """应包含编写要求。"""
    state = _make_state(tmp_path)
    _write_source(state)

    builder = _builder()
    with patch("jaut.prompt.extract_with_helpers", return_value=([], [])):
        prompt = builder.build(state, tmp_path)

    assert "编写要求" in prompt
    assert "禁止 try/catch" in prompt
    assert "Mockito mock" in prompt


def test_build_no_source_file(tmp_path):
    """source_file 为空时不应包含源码块。"""
    state = _make_state(tmp_path)
    state.source_file = ""

    builder = _builder()
    with patch("jaut.prompt.extract_with_helpers", return_value=([], [])) as mock_extract:
        prompt = builder.build(state, tmp_path)

    # 不应调用 extract_with_helpers (因为 source 为空)
    mock_extract.assert_not_called()
    assert "当前方法源码" not in prompt


def test_build_missing_current_method_raises(tmp_path):
    """current_method 缺失时应抛异常。"""
    state = _make_state(tmp_path)
    state.current_method = None

    builder = _builder()
    with pytest.raises(ValueError, match="current_method"):
        builder.build(state, tmp_path)


# --------------------------------------------------------------------------- #
# PromptBuilder._current_rate
# --------------------------------------------------------------------------- #
def test_current_rate_returns_method_rate(tmp_path):
    state = _make_state(tmp_path, current_method_covered=7, current_method_missed=3)
    builder = _builder()
    rate = builder._current_rate(state)
    assert rate == 70.0  # 7/(7+3) = 70%


def test_current_rate_returns_zero_when_method_not_found(tmp_path):
    state = _make_state(tmp_path)
    state.methods = []  # 清空方法列表
    builder = _builder()
    rate = builder._current_rate(state)
    assert rate == 0.0


# --------------------------------------------------------------------------- #
# PromptBuilder._read_source
# --------------------------------------------------------------------------- #
def test_read_source_returns_file_content(tmp_path):
    source_file = tmp_path / "Foo.java"
    source_file.write_text("class Foo {}", encoding="utf-8")
    state = _make_state(tmp_path, source_file=source_file)

    builder = _builder()
    content = builder._read_source(state)
    assert content == "class Foo {}"


def test_read_source_returns_empty_when_no_source(tmp_path):
    state = _make_state(tmp_path)
    state.source_file = ""

    builder = _builder()
    content = builder._read_source(state)
    assert content == ""


def test_read_source_handles_encoding_errors(tmp_path):
    source_file = tmp_path / "Foo.java"
    source_file.write_bytes(b"class Foo { \xff }")  # 无效 UTF-8

    state = _make_state(tmp_path, source_file=source_file)
    builder = _builder()
    content = builder._read_source(state)
    # 应使用 errors="replace" 处理
    assert "Foo" in content


# --------------------------------------------------------------------------- #
# PromptBuilder._section_header
# --------------------------------------------------------------------------- #
def test_section_header_format(tmp_path):
    state = _make_state(tmp_path)
    current = state.current_method
    builder = _builder()
    lines = builder._section_header(state, current, round_no=3, rate=45.5)

    assert len(lines) == 3
    assert "com.example.FooService" in lines[0]
    assert "doSomething" in lines[0]
    assert "(int, String)" in lines[0]
    assert "第 3 轮" in lines[0]
    assert "80.0%" in lines[1]
    assert "45.5%" in lines[1]


# --------------------------------------------------------------------------- #
# PromptBuilder._section_rules
# --------------------------------------------------------------------------- #
def test_section_rules_includes_path():
    builder = _builder(Path("/my/rules.md"))
    lines = builder._section_rules()
    # 检查规则文件路径是否在输出中(兼容 Windows/Unix 路径分隔符)
    rules_text = "\n".join(lines)
    assert "rules.md" in rules_text
    # 检查是否包含关键规则摘要
    assert "规则 a" in rules_text
    assert "规则 b" in rules_text
    assert "规则 c" in rules_text
    assert "规则 d" in rules_text
    assert "规则 f" in rules_text
    assert "@SpringBootTest" in rules_text
    assert "strict stubs" in rules_text
    assert "独立预期值" in rules_text
    assert "抄实际值" in rules_text


# --------------------------------------------------------------------------- #
# PromptBuilder._section_paths
# --------------------------------------------------------------------------- #
def test_section_paths_includes_files(tmp_path):
    state = _make_state(tmp_path)
    builder = _builder()
    lines = builder._section_paths(state, "/test/FooTest.java")
    assert state.source_file in lines[0]
    assert "/test/FooTest.java" in lines[1]


# --------------------------------------------------------------------------- #
# PromptBuilder._section_mvn_log
# --------------------------------------------------------------------------- #
def test_section_mvn_log_with_explicit_path(tmp_path):
    state = _make_state(tmp_path)
    state.mvn_log = str(tmp_path / "mvn.log")
    builder = _builder()
    lines = builder._section_mvn_log(state, tmp_path)
    assert "上一轮未达标" in lines[1]
    assert state.mvn_log in lines[1]


def test_section_mvn_log_default_path(tmp_path):
    state = _make_state(tmp_path)
    state.mvn_log = None
    builder = _builder()
    lines = builder._section_mvn_log(state, tmp_path)
    assert str(tmp_path / config.MVN_LOG_FILENAME) in lines[1]


# --------------------------------------------------------------------------- #
# PromptBuilder._section_skeleton
# --------------------------------------------------------------------------- #
def test_section_skeleton_includes_package_and_class():
    builder = _builder()
    lines = builder._section_skeleton("com.example", "FooServiceTest")
    skeleton_text = "\n".join(lines)
    assert "com.example" in skeleton_text
    assert "FooServiceTest" in skeleton_text
    assert "JUnit5" in skeleton_text
    assert "Mockito" in skeleton_text


# --------------------------------------------------------------------------- #
# PromptBuilder._section_code
# --------------------------------------------------------------------------- #
def test_section_code_wraps_in_markdown():
    builder = _builder()
    blocks = ["void foo() {}", "void bar() {}"]
    lines = builder._section_code("标题:", blocks)
    text = "\n".join(lines)
    assert "标题:" in text
    assert "```java" in text
    assert "```" in text
    assert "void foo() {}" in text


# --------------------------------------------------------------------------- #
# PromptBuilder._section_requirements
# --------------------------------------------------------------------------- #
def test_section_requirements_includes_restrictions():
    builder = _builder()
    lines = builder._section_requirements("/test/FooTest.java")
    text = "\n".join(lines)
    assert "禁止 try/catch" in text
    assert "Mockito mock" in text
    assert "/test/FooTest.java" in text
    assert "@SpringBootTest" in text
    assert "独立预期值" in text
    assert "抄实际值" in text
