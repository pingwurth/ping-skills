"""文件日志工具。

日志仅写 <run-dir>/logs/<script>.log(FileHandler-only, 不挂控制台 handler),
以保证 stdout 只出现 NEXT_STEP 协议块。
"""

from __future__ import annotations

import logging
import sys
import tempfile
from pathlib import Path

from . import config

_logger_cache: dict[tuple[str, str], logging.Logger] = {}
_module_logger: logging.Logger | None = None
# 当前挂在命名空间根上的 run-dir handler: (handler, script_name, run_dir)。
# P1 回修(2026-11-02 审查): 处理期间一切 module_logger() 告警必须落入
# <run-dir>/logs/<cmd>.log —— 此前 setup_logger 在 handler 执行之后才被调用,
# run-dir 日志恒为空文件, SKILL.md 协议块缺失恢复步骤永远读到 0 字节。
_root_runlog: tuple[logging.Handler, str, str] | None = None


def _logger_name(script_name: str) -> str:
    return f"{config.LOGGER_NAMESPACE}.{script_name}"


def _attach_root_runlog(script_name: str, run_dir: str | Path) -> logging.Handler:
    """把 run-dir FileHandler 挂到命名空间根(换目录时摘旧挂新), 返回该 handler。

    module_logger() 返回命名空间根 —— 根上挂了本 handler 后, 处理期间的
    模块告警同时落入 run-dir 日志(/tmp 兜底 handler 若已存在则继续保留)。
    同一 (script, run_dir) 重复调用为 no-op, 不重复截断。
    """
    global _root_runlog
    key = (script_name, str(run_dir))
    if _root_runlog is not None and (_root_runlog[1], _root_runlog[2]) == key:
        return _root_runlog[0]
    root_logger = logging.getLogger(config.LOGGER_NAMESPACE)
    if _root_runlog is not None:
        old = _root_runlog[0]
        root_logger.removeHandler(old)
        # P11(c020): 旧 handler 从所有借用它的子 logger 摘除 —— 借用者
        # (setup_logger)与根共享同一实例, 换目录必须统一换新
        for lg in _logger_cache.values():
            if old in lg.handlers:
                lg.removeHandler(old)
        old.close()
    log_dir = Path(run_dir) / config.LOGS_SUBDIR
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(
        log_dir / f"{script_name}.log", mode="w", encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter(config.LOG_FORMAT, config.LOG_DATE_FORMAT)
    )
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.DEBUG)
    root_logger.propagate = False
    _root_runlog = (handler, script_name, str(run_dir))
    return handler


def setup_logger(script_name: str, run_dir: str | Path) -> logging.Logger:
    """创建/复用子命令专属文件 logger(每次以 "w" 覆盖, 只留最近一次运行)。

    P11(c020)回修: 本 logger 与命名空间根挂**同一个** FileHandler 实例 ——
    原版各开一个 mode='w' handler 指向同一文件, 两个独立偏移互踩: 经返回
    logger 写一条记录会覆写 root handler 已写告警的首字节(实测), 恢复日志
    (协议块缺失时读尾部)被毁; 同一实例偏移共享, 切换 run 时由
    _attach_root_runlog 统一换新并从借用者摘旧。
    """
    name = _logger_name(script_name)
    cache_key = (script_name, str(run_dir))
    if cache_key in _logger_cache:
        _attach_root_runlog(script_name, run_dir)
        return _logger_cache[cache_key]
    logger = logging.getLogger(f"{name}.{run_dir}")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False  # 禁止传播到根 logger, 确保 stdout 仅用于 NEXT_STEP 协议块
    handler = _attach_root_runlog(script_name, run_dir)
    if handler not in logger.handlers:
        logger.addHandler(handler)
    _logger_cache[cache_key] = logger
    return logger


def module_logger() -> logging.Logger:
    """模块级兜底 logger, 防止工具函数在未 setup 时直接调用报错。

    兜底日志位于系统临时目录 code-review-p/logs/common.log,
    首次建立时向 stderr 打印一次实际路径(stdout 保留给 NEXT_STEP 协议块)。
    """
    global _module_logger
    if _module_logger is None:
        root_logger = logging.getLogger(config.LOGGER_NAMESPACE)
        if not root_logger.handlers:
            log_dir = Path(tempfile.gettempdir()) / "code-review-p" / "logs"
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
            print(f"[crp] 模块级兜底日志(未 setup_logger 时使用): {log_dir / 'common.log'}",
                  file=sys.stderr)
        _module_logger = root_logger
    return _module_logger
