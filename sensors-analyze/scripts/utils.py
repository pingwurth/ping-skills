"""共享工具函数：日志配置、next_step 输出。

所有脚本统一从此模块导入 setup_logging / next_step，
避免重复代码并确保 stderr 回退通道不丢失。
"""
from __future__ import annotations

import atexit
import logging
import sys
from pathlib import Path

# 保存原始 stderr，setup_logging 重定向后仍可通过此变量回退输出
_ORIGINAL_STDERR = sys.stderr

# 当前重定向到日志文件的 stderr 句柄（跨次调用需关闭旧句柄，进程退出时恢复）
_STDERR_REDIRECT = None
_ATEXIT_REGISTERED = False

# 工作产物统一存放目录（相对工作目录，即脚本运行时的 CWD）：
# sensors.json / entry.txt / call_sites.json / sensors.csv / 各报告与日志默认都落在这里
ARTIFACT_DIR = Path(".agent/sensors-analyze")


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


def setup_logging(name: str, log_path: Path) -> logging.Logger:
    """配置日志：所有输出写入 log 文件，stdout 仅保留给 next_step。

    - 同名 logger 重复配置时先摘除旧 handler（否则日志行会成倍重复）
    - stderr 重定向到同一日志文件；旧重定向句柄先关闭，避免 fd 泄漏；
      进程退出时通过 atexit 恢复原始 stderr
    - 日志文件打开失败时保留原始 stderr 作为回退
    """
    global _STDERR_REDIRECT, _ATEXIT_REGISTERED
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    for h in list(logger.handlers):  # 幂等：摘掉上次配置的 handler
        logger.removeHandler(h)
        h.close()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(str(log_path), encoding="utf-8", mode="w")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(fh)
    try:
        f = open(str(log_path), "a", encoding="utf-8")
    except OSError:
        # 日志文件无法打开时，保留原始 stderr 作为回退
        pass
    else:
        if _STDERR_REDIRECT is not None:  # 上一次重定向的句柄不再需要
            try:
                _STDERR_REDIRECT.close()
            except OSError:
                pass
        _STDERR_REDIRECT = f
        sys.stderr = f
        if not _ATEXIT_REGISTERED:
            atexit.register(_restore_stderr)
            _ATEXIT_REGISTERED = True
    return logger


def next_step(msg: str) -> None:
    """脚本结束时调用，唯一向 stdout 输出的内容。"""
    print(f"next_step: {msg}", flush=True)


def original_stderr():
    """返回脚本启动时的原始 stderr（用于日志文件打开失败时的回退输出）。"""
    return _ORIGINAL_STDERR


def check_rg_available() -> bool:
    """检测 ripgrep 是否可用（仅调用一次，结果可缓存）。

    用 shutil.which 而非 `which` 子进程：不依赖外部二进制，最小环境也安全。
    """
    import shutil
    return shutil.which("rg") is not None
