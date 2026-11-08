# -*- coding: utf-8 -*-
"""jaut/protocol.py 双副本 parity 锁定(F40)。

java 与 batch 两 skill 的 jaut/protocol.py 应字节相同(历史事实: P3-5 序列化
修复时曾三处各付一次手工同步成本)。本测试在两 skill 仓库共存时锁定 parity:
任一侧单独修改发射器/结构约定而未同步另一侧, 两侧本测试同时变红。
(skill 自包含安装, 无法共享文件 —— crp 副本枚举已分化, 不在本锁定范围。)
"""
from pathlib import Path

import pytest

_THIS = Path(__file__).resolve().parent
_SELF_PROTOCOL = _THIS.parent / "scripts" / "jaut" / "protocol.py"
_PEER = _THIS.parents[1] / "java-unit-test-generator" / "scripts" / "jaut" / "protocol.py"


def test_protocol_py_copies_are_byte_identical():
    """batch 与 java 的 protocol.py 必须字节相同(修复三处同步, 见文件头注释)。"""
    if not _PEER.is_file():
        pytest.skip("java-unit-test-generator 副本不在本仓库布局中(单独提取时)")
    assert _SELF_PROTOCOL.read_bytes() == _PEER.read_bytes(), (
        "jaut/protocol.py 双副本漂移: 修改发射器/结构约定必须同步 java 与 batch "
        "两份(以及 crp/protocol.py 的对应语义), 只改一处会让另一个 skill 的 "
        "发射器带着半套契约继续运行")
