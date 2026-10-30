#!/usr/bin/env python3
"""code-review-p 确定性引擎唯一入口(子命令见 --help)。

子命令: start / resume / submit / answer / status / runs / report。
每次调用在 stdout 末尾输出唯一 NEXT_STEP 协议块, 进程退出码 == 协议块 exit_code。
"""

from __future__ import annotations

import _path_setup  # noqa: F401  # 将 scripts/ 注入 sys.path

from crp.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
