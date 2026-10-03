"""跨平台文件锁工具。

提供独占文件锁的上下文管理器, 消除 batch_next / batch_update / batch_finish
三处重复的 _batch_lock 实现。

POSIX 使用 fcntl.flock, Windows 使用 msvcrt.locking。
"""

from __future__ import annotations

import contextlib
import errno
import os
import sys
from pathlib import Path
from typing import IO, Generator

if sys.platform == "win32":
    import msvcrt

    def _lock_file(fp: IO) -> None:
        # LK_LOCK 每次最多阻塞约 10 秒后抛 OSError, 而批量终验 mvn 可持锁
        # 30 分钟 —— 仅锁被占用时循环重试直到获锁, 与 POSIX flock 的无限
        # 阻塞语义对齐; 其余 OSError(无效句柄/权限等)不可恢复, 直接抛出
        # 以免无限重试挂起整个批量流程
        while True:
            try:
                msvcrt.locking(fp.fileno(), msvcrt.LK_LOCK, 1)
                return
            except OSError as exc:
                # msvcrt 以 EACCES(锁被占用)/EDEADLK 表示锁竞争, 才可重试
                if exc.errno not in (errno.EDEADLK, errno.EACCES):
                    raise

    def _unlock_file(fp: IO) -> None:
        msvcrt.locking(fp.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lock_file(fp: IO) -> None:
        fcntl.flock(fp.fileno(), fcntl.LOCK_EX)

    def _unlock_file(fp: IO) -> None:
        fcntl.flock(fp.fileno(), fcntl.LOCK_UN)


@contextlib.contextmanager
def batch_lock(state_path: Path) -> Generator[None, None, None]:
    """获取 batch_state.json 的独占文件锁。

    使用 ``<state_path>.lock`` 作为锁文件; with 块结束时自动释放锁并关闭
    文件句柄。

    锁文件常驻不删(与 StateStore.locked 同模式): unlock 后 unlink 与后来者
    open 新 inode 存在竞态 —— 进程 B 已 open 旧 inode 并阻塞在 flock, 进程 A
    unlock 后 unlink, 进程 C 以 open("a") 创建新 inode 并立即获锁, B 与 C
    会同时"持锁"进入临界区, 破坏 batch_next 原子认领的互斥保证; 空锁文件
    长期存在无害, 获锁时刷新 mtime 供残留检测。

    Args:
        state_path: batch_state.json 的路径。
    """
    lock_path = state_path.with_name(state_path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a", encoding="utf-8") as fp:
        _lock_file(fp)
        # 获锁后刷新 mtime: 标记最近一次持锁时间(残留检测以"最近获锁"为准)
        try:
            os.utime(lock_path)
        except OSError:
            pass
        try:
            yield
        finally:
            _unlock_file(fp)
    # 不 unlink: 见 docstring 中的竞态说明
