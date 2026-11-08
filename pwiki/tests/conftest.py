"""pytest 共享配置: 把 skill 根与 scripts/ 加入 sys.path。

scripts/ 供导入 pwiki_lib 包; skill 根供以 `from scripts.xxx import` 形式导入入口。
(先例: code-review-p/tests/conftest.py)
"""

from __future__ import annotations

import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_ROOT / "scripts"
for _p in (SCRIPTS_DIR, SKILL_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def _ctx(repo, root):
    """测试共用的 Ctx 构造(r4 #63: 曾在 test_review_fixes 与
    test_deep_review_fixes 各存一份逐字拷贝, Ctx 签名一改就漏改副本)。"""
    import types
    from pwiki_lib.config import Ctx
    return Ctx(types.SimpleNamespace(repo=repo.path, root=root,
                                     locale="zh")).load()


def _cli(argv):
    from pwiki_lib import cli
    return cli.main(argv)
