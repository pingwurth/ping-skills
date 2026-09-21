"""javasrc 静态分析单测: 剥离保长度、描述符人读化、方法抽取、目标定位。"""

from __future__ import annotations

from pathlib import Path

from jaut import javasrc


def test_strip_keeps_length_and_newlines():
    src = 'int a = 1; // c="x"\nString s = "a{b}c";\n/* block\n still */ char c = \'{\';\n'
    out = javasrc.strip_comments_and_strings(src)
    assert len(out) == len(src)                       # 严格保长度
    assert out.count("\n") == src.count("\n")         # 换行保留
    assert '"' not in out and "{" not in out.split("\n")[1].replace("s", "")  # 字符串内花括号被清


def test_strip_text_block():
    src = 'String t = """\n  he said "hi" {\n""";\nint x = 1;'
    out = javasrc.strip_comments_and_strings(src)
    assert len(out) == len(src)
    assert "int x = 1;" in out


def test_jvm_desc_to_readable():
    assert javasrc.jvm_desc_to_readable("(Ljava/util/List;[I)V") == "(List, int[])"
    assert javasrc.jvm_desc_to_readable("()V") == "()"
    assert javasrc.jvm_desc_to_readable("(IJ)Z") == "(int, long)"
    assert javasrc.jvm_desc_to_readable("not-a-desc") == "not-a-desc"


def test_split_top_level_ignores_nested_commas():
    assert javasrc.split_top_level("Map<String,Integer>, int") == ["Map<String,Integer>", "int"]
    assert javasrc.split_top_level("") == []


def test_arity_from_desc():
    assert javasrc.arity_from_desc("") == 0
    assert javasrc.arity_from_desc("()") == 0
    assert javasrc.arity_from_desc("(List, int[])") == 2


def test_extract_method_blocks_excludes_call_sites():
    src = (
        "class A {\n"
        "  public int foo(int x) {\n"
        "    return bar(x) + this.foo(x - 1);\n"
        "  }\n"
        "  private int bar(int y) { return y; }\n"
        "}\n"
    )
    blocks = javasrc.extract_method_blocks(src, "foo", arity=1)
    assert len(blocks) == 1                 # 只抽声明, 排除 this.foo( 调用点
    assert "public int foo(int x)" in blocks[0]


def test_extract_with_helpers_collects_private():
    src = (
        "class A {\n"
        "  public int foo(int x) { return helper(x); }\n"
        "  private int helper(int y) { return y * 2; }\n"
        "}\n"
    )
    main_blocks, helpers = javasrc.extract_with_helpers(src, "foo", arity=1)
    assert main_blocks and "foo" in main_blocks[0]
    assert any("helper" in h for h in helpers)


def test_extract_with_helpers_strips_once(monkeypatch):
    """P0-3: 净化文本只在入口剥离一次, 递归抽取经 stripped 参数复用。

    构造多层 helper 调用链, 统计 strip_comments_and_strings 调用次数;
    修复前为 helper 数 + 2 次, 修复后恒为 1 次。
    """
    calls = {"n": 0}
    real_strip = javasrc.strip_comments_and_strings

    def counting_strip(content):
        calls["n"] += 1
        return real_strip(content)

    monkeypatch.setattr(javasrc, "strip_comments_and_strings", counting_strip)
    src = (
        "class A {\n"
        "  public int foo(int x) { return h1(x) + h2(x); }\n"
        "  private int h1(int y) { return y; }\n"
        "  private int h2(int y) { return h2b(y); }\n"
        "  private int h2b(int y) { return y; }\n"
        "}\n"
    )
    main_blocks, helpers = javasrc.extract_with_helpers(src, "foo", arity=1)
    assert calls["n"] == 1
    assert main_blocks and "foo" in main_blocks[0]
    assert len(helpers) == 3  # h1 / h2 / h2b 全部收集


def test_extract_method_blocks_reuses_stripped(monkeypatch):
    """传入 stripped 参数时不再重复剥离; 长度不匹配时回退重新剥离。"""
    calls = {"n": 0}
    real_strip = javasrc.strip_comments_and_strings

    def counting_strip(content):
        calls["n"] += 1
        return real_strip(content)

    monkeypatch.setattr(javasrc, "strip_comments_and_strings", counting_strip)
    src = (
        "class A {\n"
        "  public int foo(int x) { return x; }\n"
        "}\n"
    )
    stripped = javasrc.strip_comments_and_strings(src)
    calls["n"] = 0
    # 传入等长 stripped: 不再调用剥离
    blocks = javasrc.extract_method_blocks(src, "foo", arity=1, stripped=stripped)
    assert blocks and calls["n"] == 0
    # 传入不等长 stripped: 回退重新剥离
    blocks = javasrc.extract_method_blocks(src, "foo", arity=1, stripped=stripped[:-1])
    assert blocks and calls["n"] == 1


def test_abstract_method_skipped():
    src = "interface I {\n  void foo(int x);\n}\n"
    assert javasrc.extract_method_blocks(src, "foo", arity=1) == []


def test_private_method_names_excludes_field_init_and_new():
    src = (
        "class A {\n"
        "  private int field = compute(1);\n"
        "  private Foo f = new Foo(1);\n"
        "  private int compute(int x) { return x; }\n"
        "}\n"
    )
    names = javasrc.private_method_names(src)
    assert "compute" in names
    assert "field" not in names and "f" not in names


def test_fqcn_from_source_path_and_locate(tmp_path: Path):
    root = tmp_path
    src = root / "mod" / "src" / "main" / "java" / "com" / "x" / "Foo.java"
    src.parent.mkdir(parents=True)
    src.write_text("package com.x; class Foo {}", encoding="utf-8")
    assert javasrc.fqcn_from_source_path(src) == "com.x.Foo"
    found = javasrc.locate_source_file(root, "com.x.Foo")
    assert found is not None and found.name == "Foo.java"
    fqcn, path = javasrc.resolve_target(root, "com.x.Foo")
    assert fqcn == "com.x.Foo" and path == found


# --------------------------------------------------------------------------- #
# diagnose_missing_source 测试
# --------------------------------------------------------------------------- #
def test_diagnose_fqcn_not_found(tmp_path: Path):
    """FQCN 在项目中不存在 → 返回拼写提示。"""
    root = tmp_path
    # 空项目，无任何源文件
    result = javasrc.diagnose_missing_source(root, "com.nonexist.Foo")
    assert "拼写正确" in result
    assert "com.nonexist.Foo" in result


def test_diagnose_test_class(tmp_path: Path):
    """传入的 FQCN 只存在于测试目录 → 提示“在测试目录找到”。"""
    root = tmp_path
    test_src = root / "src" / "test" / "java" / "com" / "x" / "FooTest.java"
    test_src.parent.mkdir(parents=True)
    test_src.write_text("package com.x; class FooTest {}", encoding="utf-8")
    result = javasrc.diagnose_missing_source(root, "com.x.FooTest")
    assert "测试目录" in result


def test_diagnose_typo_finds_similar(tmp_path: Path):
    """FQCN 包名有误但同名文件存在 → 提示“找到同名文件”。"""
    root = tmp_path
    # 正确包名是 com.x, 用户传入了 com.y.Foo
    src = root / "src" / "main" / "java" / "com" / "x" / "Foo.java"
    src.parent.mkdir(parents=True)
    src.write_text("package com.x; class Foo {}", encoding="utf-8")
    result = javasrc.diagnose_missing_source(root, "com.y.Foo")
    assert "同名文件" in result
    assert "Foo.java" in result
