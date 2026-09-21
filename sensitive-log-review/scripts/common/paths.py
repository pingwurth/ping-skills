"""跨平台输出目录与产物路径集中管理（P0-3）。

输出目录解析优先级：
    1. CLI 参数 -o/--output-dir
    2. 环境变量 SLR_OUTPUT_DIR
    3. 系统临时目录下的 sensitive-log-review 子目录
       （Windows: %LOCALAPPDATA%\\Temp\\sensitive-log-review，
        Linux/macOS: /tmp/sensitive-log-review）

所有产物文件名保持不变（log-print-ok.list 等），保证下游习惯兼容。
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

ENV_KEY = "SLR_OUTPUT_DIR"
DIR_NAME = "sensitive-log-review"

# ---- 产物文件名集中定义，消除散布各脚本的字面量 ----
LOG_OK = "log-print-ok.list"
LOG_VIOLATION = "log-print-violation.list"
TOSTRING_MISSING = "miss-tostring-annotation.list"
TOSTRING_SENSITIVE_VIOLATIONS = "tostring-sensitive-violations.list"
ALL_FIELDS = "all-fields.list"
UNQUALIFIED_FIELDS = "unqualified.fields"
MAYBE_SENSITIVE_FIELDS = "maybe-sensitive.fields"
SENSITIVE_RESULTS = "sensitive.results"
UNQUALIFIED_RESULTS = "unqualified.results"
POJO_CHANGED = "pojo-changed.list"
POJO_COMMENTS = "pojo-miss-comments.txt"
ANALYZE_RESULT = "analyze-sensitize.result"
HTML_REPORT = "sensitive-log-review-report.html"
# 失败文件汇总清单（行格式: {步骤标识}\t{文件路径}\t{原因摘要}）
FAILED_FILES_LIST = "failed-files.list"

# run-id 只允许安全字符，防止路径穿越
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def get_output_dir(cli_arg: str | None = None, run_id: str | None = None) -> Path:
    """解析输出目录并确保其存在。

    Args:
        cli_arg: CLI -o/--output-dir 参数值（最高优先级）
        run_id: 可选的并行运行隔离子目录名（CI 场景）

    Returns:
        已创建（含父目录）的输出目录 Path

    Raises:
        ValueError: run_id 含非法字符（路径穿越防护）
    """
    base = Path(cli_arg or os.environ.get(ENV_KEY) or (Path(tempfile.gettempdir()) / DIR_NAME))
    if run_id:
        if not _RUN_ID_RE.match(run_id):
            raise ValueError(f"非法 run-id: {run_id!r}（仅允许字母数字/._-，且不以符号开头）")
        base = base / run_id
    base.mkdir(parents=True, exist_ok=True)
    return base


def artifact(out_dir: Path, name: str) -> Path:
    """返回输出目录下指定产物文件的完整路径。"""
    return out_dir / name
