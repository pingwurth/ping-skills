"""pytest 共享配置: 把 skill 根与 scripts/ 加入 sys.path。

scripts/ 供导入 crp 包; skill 根供以 `from scripts.xxx import` 形式导入入口。
"""

from __future__ import annotations

import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_ROOT / "scripts"
for _p in (SCRIPTS_DIR, SKILL_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def parse_block(stdout: str) -> dict:
    """解析 NEXT_STEP 协议块; 共享实现(P5 回修: test_e2e/test_cli 双份手抄,
    e2e 副本丢了 '恰一个 END 标记' 断言)。"""
    import json

    from crp import config
    assert stdout.count(config.NEXT_STEP_BEGIN_MARKER) == 1, "必须恰输出一个协议块"
    assert stdout.count(config.NEXT_STEP_END_MARKER) == 1
    begin = stdout.index(config.NEXT_STEP_BEGIN_MARKER) + len(config.NEXT_STEP_BEGIN_MARKER)
    end = stdout.index(config.NEXT_STEP_END_MARKER)
    return json.loads(stdout[begin:end])
