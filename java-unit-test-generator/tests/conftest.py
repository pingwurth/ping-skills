"""pytest 共享配置: 把 skill 根与 scripts/ 加入 sys.path。

scripts/ 供导入 jaut 包; skill 根供 handler 级测试以 `from scripts.xxx import
handler` 形式导入入口脚本(仅注入 scripts/ 时, 该形式只有在 skill 目录内
运行 pytest 才能解析, 从仓库根运行会 ModuleNotFoundError)。
"""

from __future__ import annotations

import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_ROOT / "scripts"
for _p in (SCRIPTS_DIR, SKILL_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
