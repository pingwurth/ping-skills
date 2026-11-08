#!/usr/bin/env python3
"""pwiki 统一仓库 wiki 引擎唯一入口(子命令见 --help)。

pwiki 融合三个前身 skill:
  - qwiki        引擎/生命周期/门禁/快照增量/检索(状态机 + SQLite)
  - repowiki     确定性规划器(scan/modules/主题包)与内容验证器
  - wiki-plan-gen Qoder IDE wiki_plan.yaml 契约(qoder-plan)

用法: python3 pwiki.py <子命令> [参数...]   (所有命令接受 --repo/--root/--locale)
"""

from __future__ import annotations

import _path_setup  # noqa: F401  # 将 scripts/ 注入 sys.path

from pwiki_lib.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
