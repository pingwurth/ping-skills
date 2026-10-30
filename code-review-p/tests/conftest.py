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
