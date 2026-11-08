"""pwiki_lib — pwiki 引擎包(仅 Python stdlib)。

分层规则(评审强制, 非导入强制):
  1. db.py 是唯一打开 SQLite 的模块; 状态变更一律短 BEGIN IMMEDIATE 事务。
  2. wiki_plan.yaml(v3) 是唯一人工编辑面; DB 是唯一引擎状态; work/ 是可再生缓存。
  3. 门禁在 submit 永不改内容(拒绝→pending 保 checkpoint); 注入/链接规范化
     只发生在 LINING/publish 的导出层, 且永不写回 page.content。
  4. 零 LLM、零网络; 唯一子进程是 git。
"""

from __future__ import annotations

VERSION = "0.1.0"
