"""统一错误码与修复建议（易用性优化）。

对常见环境/参数/依赖错误定义错误码枚举与分步修复建议模板，
各脚本在打印错误信息处调用 print_error 输出结构化提示（stderr）。

设计约束:
  - 只升级错误信息的呈现格式，不改变既有异常控制流；
  - 这些错误的退出码契约保持不变（仍为 2）；
  - 输出不使用 emoji，保持纯文本，CI 日志友好。
"""

from __future__ import annotations

import sys
from enum import Enum


class ErrorCode(Enum):
    """常见错误的错误码枚举（值为对外展示的短码）。"""

    E001_GIT_NOT_REPO = "E001"
    E002_JAVA_NOT_FOUND = "E002"
    E003_BRANCH_INVALID = "E003"
    E004_RULE_FILE_BROKEN = "E004"
    E005_RUN_ID_INVALID = "E005"
    E006_OUTPUT_DIR_UNWRITABLE = "E006"
    E007_JAR_CHECKSUM_MISMATCH = "E007"


# 错误码 -> (标题, 分步修复建议) 模板
_CATALOG: dict[ErrorCode, tuple[str, tuple[str, ...]]] = {
    ErrorCode.E001_GIT_NOT_REPO: (
        "-r 指向的目录不是 git 仓库",
        (
            "在该目录下执行 git status，确认是否为 git 仓库",
            "确认 -r 参数指向仓库根目录（包含 .git 的目录）；在仓库根下执行时传 -r .",
        ),
    ),
    ErrorCode.E002_JAVA_NOT_FOUND: (
        "找不到 java 命令",
        (
            "安装 JRE/JDK 并将 java 加入 PATH（java -version 可验证）",
            "该错误仅影响步骤6 POJO 注释检查（checkstyle 依赖 Java），其余步骤不受影响",
        ),
    ),
    ErrorCode.E003_BRANCH_INVALID: (
        "分支名非法或不存在",
        (
            "核对 --branch 参数拼写（不允许以 - 开头、不允许包含 ..）",
            "执行 git branch -a 确认目标分支存在（CI 中常用 origin/master、origin/develop）",
        ),
    ),
    ErrorCode.E004_RULE_FILE_BROKEN: (
        "规则 JSON 文件损坏",
        (
            "按提示的行列位置修复 JSON 语法（常见为缺逗号、缺引号、多余尾逗号）",
            "可用 python -m json.tool <文件路径> 验证修复结果",
        ),
    ),
    ErrorCode.E005_RUN_ID_INVALID: (
        "run-id 含非法字符",
        (
            "run-id 仅允许字符集 [A-Za-z0-9._-]，且首字符必须是字母或数字，最长 64 字符",
            "清洗 CI 变量后再传入，如将 feature/xxx 中的 / 替换为 -",
        ),
    ),
    ErrorCode.E006_OUTPUT_DIR_UNWRITABLE: (
        "输出目录不可写",
        (
            "检查目录权限与磁盘剩余空间",
            "改用 -o 指定其他可写目录，或设置环境变量 SLR_OUTPUT_DIR",
        ),
    ),
    ErrorCode.E007_JAR_CHECKSUM_MISMATCH: (
        "checkstyle jar SHA256 校验不符",
        (
            "若非预期改动：从可信源重新获取 checkstyle jar 文件",
            "若有意升级版本：同步更新 <jar>.sha256 基线文件，并回归单测（python -m pytest tests -q）",
        ),
    ),
}


def format_error(code: ErrorCode, location: str = "", detail: str = "") -> str:
    """格式化结构化错误信息（错误码 + 位置 + 详情 + 分步修复建议）。"""
    title, steps = _CATALOG[code]
    lines = [f"错误[{code.value}]: {title}"]
    if location:
        lines.append(f"  位置: {location}")
    if detail:
        lines.append(f"  详情: {detail}")
    lines.append("  修复建议:")
    lines.extend(f"    {index}. {step}" for index, step in enumerate(steps, 1))
    return "\n".join(lines)


def print_error(code: ErrorCode, location: str = "", detail: str = "") -> None:
    """将结构化错误信息输出到 stderr（不改变调用方退出码契约）。"""
    print(format_error(code, location, detail), file=sys.stderr)
