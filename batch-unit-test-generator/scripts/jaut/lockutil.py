"""跨平台文件锁工具。

提供独占文件锁的上下文管理器, 消除 batch_next / batch_update / batch_finish
三处重复的 _batch_lock 实现。

POSIX 使用 fcntl.flock, Windows 使用 msvcrt.locking。
"""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path
from typing import IO, Generator

if sys.platform == "win32":
    import msvcrt

    def _lock_file(fp: IO) -> None:
        msvcrt.locking(fp.fileno(), msvcrt.LK_LOCK, 1)

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

    使用 ``<state_path>.lock`` 作为锁文件; with 块结束时自动
    释放锁、关闭文件句柄并删除锁文件。

    Args:
        state_path: batch_state.json 的路径。
    """
    lock_path = state_path.with_name(state_path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a", encoding="utf-8") as fp:
        _lock_file(fp)
        try:
            yield
        finally:
            _unlock_file(fp)
    try:
        lock_path.unlink(missing_ok=True)
    except OSError:
        pass
