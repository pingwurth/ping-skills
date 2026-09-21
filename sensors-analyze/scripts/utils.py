"""共享工具函数：日志配置、next_step 输出。

所有脚本统一从此模块导入 setup_logging / next_step，
避免重复代码并确保 stderr 回退通道不丢失。
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

# 保存原始 stderr，setup_logging 重定向后仍可通过此变量回退输出
_ORIGINAL_STDERR = sys.stderr


def setup_logging(name: str, log_path: Path) -> logging.Logger:
    """配置日志：所有输出写入 log 文件，stdout 仅保留给 next_step。

    保存原始 stderr 到 _ORIGINAL_STDERR，重定向后如遇日志文件打开失败，
    可通过它输出错误信息。
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(str(log_path), encoding="utf-8", mode="w")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(fh)
    try:
        sys.stderr = open(str(log_path), "a", encoding="utf-8")
    except OSError:
        # 日志文件无法打开时，保留原始 stderr 作为回退
        pass
    return logger


def next_step(msg: str) -> None:
    """脚本结束时调用，唯一向 stdout 输出的内容。"""
    print(f"next_step: {msg}", flush=True)


def original_stderr():
    """返回脚本启动时的原始 stderr（用于日志文件打开失败时的回退输出）。"""
    return _ORIGINAL_STDERR


def check_rg_available() -> bool:
    """检测 ripgrep 是否可用（仅调用一次，结果可缓存）。"""
    import subprocess
    return subprocess.run(["which", "rg"], capture_output=True).returncode == 0
