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

# ==================== sensors.csv 表头单一来源 ====================
# 此前固定表头在 check_report.py / review_report.py / report_manager.py
# 里各自硬编码成 6 个列表字面量并已漂移（7 列 / 8 列混写）；收敛到这里，
# 三个脚本统一引用，杜绝"改一处漏两处"。
#
# 基线 7 列（第 4 步 LLM 产出，不含 illegal_reason）：
CSV_BASE_HEADER = [
    "page_name", "first_biz_id", "second_biz_id",
    "first_biz_name", "second_biz_name", "event", "file",
]
# 完整 8 列（第 5.5 步脚本追加 illegal_reason 后）：
CSV_FULL_HEADER = CSV_BASE_HEADER + ["illegal_reason"]
# 必填非空字段（check_report / review_report 共用；空值只 WARN 不 FAIL）：
REQUIRED_NONEMPTY = [
    "page_name", "first_biz_id", "second_biz_id",
    "first_biz_name", "second_biz_name",
]
# 所有检查字段（必填 + event）：
ALL_CHECK_FIELDS = REQUIRED_NONEMPTY + ["event"]

# 文档认可的可选附加列（SKILL.md 第 5.6 步：人工备注写 manual_note 列）。
# 出现在表头时不算异常——按文档工作流正确操作的 CSV 不应触发表头告警。
ALLOWED_EXTRA_COLUMNS = ("manual_note",)


def header_is_expected(header: list[str]) -> bool:
    """sensors.csv 表头是否符合期望（供 report_manager 等做一致性告警）。

    期望形态：7 列基线按序开头，其后只允许 illegal_reason 与文档认可的
    附加列（manual_note 等）以任意顺序出现。基线缺列 / 乱序 / 出现
    未认可列时返回 False，仍照常告警。
    """
    if list(header[: len(CSV_BASE_HEADER)]) != CSV_BASE_HEADER:
        return False
    return all(
        c == "illegal_reason" or c in ALLOWED_EXTRA_COLUMNS
        for c in header[len(CSV_BASE_HEADER):]
    )


# ==================== 搜索排除规则（第 3 步定位与第 2 步 --audit 共用） ====================
# rg 与纯 Python 回退统一排除的目录：依赖产物与本 skill 自身的产物目录。
# 两处脚本共用一份定义，保证 --audit 与第 3 步扫描比较的是同一文件集。
EXCLUDED_DIRS = {".agent", "node_modules", "vendor", "dist", "build", "venv", ".venv"}

# rg 侧等价的 glob 排除参数（每个排除目录一条）
RG_EXCLUDE_ARGS = [f"--glob=!{d}/**" for d in sorted(EXCLUDED_DIRS)]


def is_excluded(rel_parts: tuple[str, ...]) -> bool:
    """相对路径的任一段命中排除目录或为隐藏目录则排除（与 rg 默认跳过隐藏一致）。"""
    for part in rel_parts:
        if part in EXCLUDED_DIRS:
            return True
    # 隐藏目录对齐 rg 默认行为（rg 不加 --hidden 时跳过 . 开头路径）；
    # 文件名本身以 . 开头（如 .foo.js）也一并跳过，与 rg 一致
    return any(p.startswith(".") for p in rel_parts)


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
