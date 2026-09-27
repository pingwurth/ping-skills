"""pytest 共享配置: 把 scripts/ 与 skill 根目录加入 sys.path。

scripts/ 供 `import jaut` 与顶层入口脚本导入; skill 根目录供
`from scripts.batch_finish import handler` 形式导入(handler 测试使用),
同时也使测试套件不依赖运行时 cwd(可在仓库根等任意目录运行)。
"""

from __future__ import annotations

import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_ROOT / "scripts"
for p in (SCRIPTS_DIR, SKILL_ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
