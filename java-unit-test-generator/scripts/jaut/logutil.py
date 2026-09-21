"""文件日志工具。

日志仅写 <workdir>/logs/<script>.log(FileHandler-only, 不挂控制台 handler),
以保证 stdout 只出现 NEXT_STEP 协议块与显式计划摘要。
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from . import config

_logger_cache: dict[tuple[str, str], logging.Logger] = {}
_module_logger: logging.Logger | None = None


def _logger_name(script_name: str) -> str:
    return f"{config.LOGGER_NAMESPACE}.{script_name}"


def setup_logger(script_name: str, workdir: str | Path) -> logging.Logger:
    """创建/复用脚本专属文件 logger(每次以 "w" 覆盖, 只留最近一次运行)。"""
    name = _logger_name(script_name)
    cache_key = (script_name, str(workdir))
    if cache_key in _logger_cache:
        return _logger_cache[cache_key]
    logger = logging.getLogger(f"{name}.{workdir}")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False  # 禁止传播到根 logger，确保 stdout 仅用于 NEXT_STEP 协议块
    if not logger.handlers:
        log_dir = Path(workdir) / config.LOGS_SUBDIR
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(
            log_dir / f"{script_name}.log", mode="w", encoding="utf-8"
        )
        handler.setFormatter(
            logging.Formatter(config.LOG_FORMAT, config.LOG_DATE_FORMAT)
        )
        logger.addHandler(handler)
    _logger_cache[cache_key] = logger
    return logger


def get_logger(script_name: str) -> logging.Logger:
    """取已初始化的 logger(不创建 handler)。"""
    return logging.getLogger(_logger_name(script_name))


def module_logger() -> logging.Logger:
    """模块级兜底 logger, 防止工具函数在未 setup 时直接调用报错。
    
    将 FileHandler 挂到命名空间根 java_ut，子 logger 靠 propagate 汇聚。
    """
    global _module_logger
    if _module_logger is None:
        # 获取根 logger
        root_logger = logging.getLogger(config.LOGGER_NAMESPACE)
        if not root_logger.handlers:
            # 使用临时目录作为默认日志目录
            log_dir = Path(tempfile.gettempdir()) / "java-unit-test-generator" / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            handler = logging.FileHandler(
                log_dir / "common.log", mode="a", encoding="utf-8"
            )
            handler.setFormatter(
                logging.Formatter(config.LOG_FORMAT, config.LOG_DATE_FORMAT)
            )
            root_logger.addHandler(handler)
            root_logger.setLevel(logging.DEBUG)
            root_logger.propagate = False
        _module_logger = root_logger
    return _module_logger
