"""lockutil 单测: Windows _lock_file 重试语义与 batch_lock 持锁行为。

lockutil 的平台分支在导入期由 sys.platform 决定, 故 Windows 分支用例以
伪 msvcrt 模块 + patch sys.platform 重新执行源码加载, 不触碰已导入的
jaut.lockutil, 保证 POSIX 上也能验证 Windows 实现且互不污染。
"""

from __future__ import annotations

import errno
import importlib.util
import io
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

LOCKUTIL_PATH = Path(__file__).resolve().parent.parent / "scripts" / "jaut" / "lockutil.py"


def _load_win32_variant(locking: MagicMock):
    """加载 lockutil 的 Windows 分支副本, 注入伪 msvcrt.locking。"""
    fake_msvcrt = types.ModuleType("msvcrt")
    fake_msvcrt.LK_LOCK = 1
    fake_msvcrt.LK_UNLCK = 2
    fake_msvcrt.locking = locking
    with patch.dict(sys.modules, {"msvcrt": fake_msvcrt}), \
         patch.object(sys, "platform", "win32"):
        spec = importlib.util.spec_from_file_location("_lockutil_win32", LOCKUTIL_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------- #
# Windows 分支 _lock_file 重试语义
# --------------------------------------------------------------------------- #
class TestWin32LockRetry:
    """仅锁竞争 errno(EACCES/EDEADLK)允许重试, 其余 OSError 立即抛出。"""

    def test_retries_until_lock_acquired_on_eacces(self):
        """锁被占用(EACCES)时应持续重试, 直到获锁后正常返回。"""
        locking = MagicMock(side_effect=[
            OSError(errno.EACCES, "locked"),
            OSError(errno.EACCES, "locked"),
            None,  # 第三次获锁
        ])
        module = _load_win32_variant(locking)

        module._lock_file(MagicMock())

        assert locking.call_count == 3

    def test_retries_on_edeadlk_contention(self):
        """EDEADLK 同样视为锁竞争, 重试后获锁。"""
        locking = MagicMock(side_effect=[OSError(errno.EDEADLK, "deadlock"), None])
        module = _load_win32_variant(locking)

        module._lock_file(MagicMock())

        assert locking.call_count == 2

    def test_reraises_bad_descriptor_without_retry(self):
        """EBADF(无效句柄)不可恢复: 即使后续重试能成功, 也必须立即抛出。

        side_effect 第二次即返回成功 —— 若实现退化为无条件重试, 用例将以
        "未抛出异常"失败, 而不是把无限重试变成挂起。
        """
        locking = MagicMock(side_effect=[OSError(errno.EBADF, "bad fd"), None])
        module = _load_win32_variant(locking)

        with pytest.raises(OSError) as excinfo:
            module._lock_file(MagicMock())

        assert excinfo.value.errno == errno.EBADF
        assert locking.call_count == 1

    def test_reraises_oserror_without_errno_without_retry(self):
        """errno 为 None 的 OSError(如 io.UnsupportedOperation)立即抛出。"""
        locking = MagicMock(side_effect=[io.UnsupportedOperation("fileno"), None])
        module = _load_win32_variant(locking)

        with pytest.raises(io.UnsupportedOperation):
            module._lock_file(MagicMock())

        assert locking.call_count == 1


# --------------------------------------------------------------------------- #
# POSIX 分支 batch_lock 行为
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX only")
class TestBatchLockPosix:
    """POSIX 下 batch_lock 的互斥持锁、释放与锁文件常驻语义。"""

    def test_lock_is_exclusive_then_released_and_file_resident(self, tmp_path):
        import fcntl

        from jaut.lockutil import batch_lock

        state_path = tmp_path / "batch_state.json"
        lock_path = tmp_path / "batch_state.json.lock"
        with batch_lock(state_path):
            assert lock_path.is_file()
            # 持锁期间, 另一句柄非阻塞抢锁应失败
            with open(lock_path, "a", encoding="utf-8") as probe:
                with pytest.raises(OSError):
                    fcntl.flock(probe.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        # 退出 with 后锁文件常驻不删(避免 unlink 竞态), 锁已释放可立即再抢
        assert lock_path.is_file()
        with open(lock_path, "a", encoding="utf-8") as probe:
            fcntl.flock(probe.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(probe.fileno(), fcntl.LOCK_UN)
        # 同一路径可重复获取
        with batch_lock(state_path):
            pass