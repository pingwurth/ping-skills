"""P0-3 端到端语义回归验证: 复杂 Java 源码下的抽取结果应保持不变。

验证 javasrc.extract_with_helpers 在以下干扰场景下的鲁棒性:
    - 注释中的伪方法声明(fakeInComment)不被抽取
    - 字符串中的伪花括号/伪调用不干扰配对
    - 字段初始化中的 compute(未被 biz 调用)不进入 helper 集合
    - 真实 helper 链(h1 -> h2 -> h2b)完整收集
"""
import sys

import _path_setup  # noqa: F401  — 初始化 sys.path 以导入 jaut 包

from jaut import javasrc  # noqa: E402

src = (
    "package com.x;\n"
    '// private int fakeInComment(int a) { return 0; }\n'
    '/* String s = "}"; */\n'
    "class Foo {\n"
    "    private int field = compute(1);          // 字段初始化\n"
    '    private String tricky = "if (x) { bar(1); }";\n'
    "\n"
    "    public int biz(int x) {                  // 主方法\n"
    '        String s = "helper(99)";             // 字符串伪调用\n'
    "        return h1(x) + h2(x);\n"
    "    }\n"
    "    private int h1(int y) { return y; }\n"
    "    private int h2(int y) { return h2b(y) + 1; }\n"
    "    private int h2b(int y) { return y * 2; }\n"
    "    private int compute(int x) { return x; } // 未被主方法调用\n"
    "}\n"
)

main_blocks, helpers = javasrc.extract_with_helpers(src, "biz", arity=1)
assert len(main_blocks) == 1 and "public int biz" in main_blocks[0]
joined = "\n".join(helpers)
for name in ("h1", "h2", "h2b"):
    assert ("private int " + name) in joined, f"{name} 未被收集"
assert "private int compute(" not in joined, "compute 不应被收集"
assert "fakeInComment" not in joined

print(f"主方法块数: {len(main_blocks)} | helper 块数: {len(helpers)}")
print("语义验证通过: 注释/字符串/字段初始化干扰项全部排除, helper 链完整收集")
