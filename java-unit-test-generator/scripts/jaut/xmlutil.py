"""XML 解析小工具(jacoco.py 与 surefire.py 共用, 双副本 parity)。"""

from __future__ import annotations


def tag_endswith(element, suffix: str) -> bool:
    """判断 XML 元素标签是否以指定后缀结尾(忽略命名空间前缀)。

    曾在 jacoco.py 与 surefire.py 各自重复定义(_tag_endswith ×4, 两 skill
    副本各两份)—— 收敛到本模块单一来源, 修复须只改一处。
    """
    return str(element.tag).endswith(suffix)
