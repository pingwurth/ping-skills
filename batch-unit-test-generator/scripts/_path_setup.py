"""入口脚本共享的路径初始化。

将 scripts/ 目录加入 sys.path，使入口脚本能导入 jaut 包。
各入口脚本只需 `import _path_setup`（无需调用任何函数）。
"""

import sys
from pathlib import Path

_scripts_dir = str(Path(__file__).resolve().parent)
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)
