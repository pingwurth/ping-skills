"""共享工具：日志配置与 next_step 输出。

与 sensors-analyze/scripts/utils.py 镜像同面（setup_logging / next_step /
original_stderr / _restore_stderr），同源修复可以文件 diff 传播。与镜像源
的有意差异（待 sensors 侧回修后对齐）：
- setup_logging 额外接受 create_dirs=False：只读探测（无台账的 status）不
  创建日志目录，杜绝拼错 --repo 路径被塞进 .agent/ 目录树；
- stderr 与日志共用同一文件句柄——两个独立句柄各持各的偏移，后写者会把
  先写者的尾部内容互相覆盖（stderr 诊断被静默销毁）。
"""
from __future__ import annotations

import atexit
import logging
import sys
from pathlib import Path

# 保存原始 stderr，setup_logging 重定向后仍可通过 original_stderr() 回退
_ORIGINAL_STDERR = sys.stderr

# 当前承载 stderr 重定向的 FileHandler（跨次配置先关闭旧句柄；进程退出时恢复）
_STDERR_REDIRECT = None
_ATEXIT_REGISTERED = False


def _restore_stderr() -> None:
    """恢复原始 stderr 并关闭重定向句柄（atexit / 重复配置时调用）。"""
    global _STDERR_REDIRECT
    sys.stderr = _ORIGINAL_STDERR
    if _STDERR_REDIRECT is not None:
        try:
            _STDERR_REDIRECT.close()
        except OSError:
            pass
        _STDERR_REDIRECT = None


def setup_logging(name: str, log_path: Path, create_dirs: bool = True) -> logging.Logger:
    """配置日志：全部输出写入日志文件，stdout 仅保留给 next_step。

    - 同名 logger 重复配置时先摘除旧 handler（否则日志行成倍重复）；
    - stderr 重定向到与日志相同的文件句柄（单句柄，见模块 docstring）；
    - create_dirs=False 且父目录不存在时跳过文件日志（只读探测零副作用）；
    - IO 失败抛 OSError，由调用方统一转协议错误（exit 2）。
    """
    global _STDERR_REDIRECT, _ATEXIT_REGISTERED
    _restore_stderr()
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    for h in list(logger.handlers):  # 幂等：摘掉上次配置的 handler
        logger.removeHandler(h)
        h.close()
    if not create_dirs and not log_path.parent.is_dir():
        return logger  # 只读探测：不建目录、不落日志
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(str(log_path), encoding="utf-8", mode="w")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(fh)
    sys.stderr = fh.stream
    _STDERR_REDIRECT = fh
    if not _ATEXIT_REGISTERED:
        atexit.register(_restore_stderr)
        _ATEXIT_REGISTERED = True
    return logger


def next_step(msg: str) -> None:
    """脚本结束时调用：唯一向 stdout 收尾的 next_step 行（带 flush）。"""
    print(f"next_step: {msg}", flush=True)


def original_stderr():
    """返回脚本启动时的原始 stderr（日志不可用时的回退输出通道）。"""
    return _ORIGINAL_STDERR
