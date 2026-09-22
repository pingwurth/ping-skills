"""全局常量与配置(单一事实源)。

所有阈值/预算/超时/路径模板/协议枚举集中于此, 其余模块只读引用, 禁止散落字面量。
行为准绳为 SKILL.md; 与其冲突时以 SKILL.md 为准。
"""

from __future__ import annotations

import os

# --------------------------------------------------------------------------- #
# 路径与工作目录
# --------------------------------------------------------------------------- #
STATE_FILENAME = "state.json"
COVERAGE_FILENAME = "coverage.json"
MVN_LOG_FILENAME = "mvn.log"
LOGS_SUBDIR = "logs"

# <project-root>/.agent/batch-unit-test-generator/
WORKDIR_PARTS = (".agent", "batch-unit-test-generator")

# 权威文档(相对 scripts/ 的上一级)
REFERENCES_DIRNAME = "references"
UNIT_TEST_RULES_FILENAME = "UnitTestRules.md"
PROTOCOL_DIRNAME = "protocol"
NEXT_STEP_SCHEMA_FILENAME = "next-step.schema.json"
STATE_SCHEMA_FILENAME = "state.schema.json"

# 遍历项目时需要剪枝的目录
PRUNE_DIRS = frozenset({".git", "target", "node_modules", ".idea", ".vscode", ".agent"})

# --------------------------------------------------------------------------- #
# NEXT_STEP 协议
# --------------------------------------------------------------------------- #
NEXT_STEP_PROTOCOL_VERSION = "1.0"
NEXT_STEP_BEGIN_MARKER = ":::NEXT_STEP_BEGIN:::"
NEXT_STEP_END_MARKER = ":::NEXT_STEP_END:::"

# status 取值
NEXT_STEP_STATUSES = ("success", "empty", "failed", "needs_input")
# next_step.type 取值
NEXT_STEP_TYPES = ("run_script", "write_code", "ask_user", "finish", "abort")

# 退出码契约(SKILL.md §2 Exit Code)
EXIT_OK = 0        # 脚本正常完成
EXIT_CONTINUE = 1  # 需要继续处理(未达标等正常流转)
EXIT_ERROR = 2     # 脚本执行错误(mvn 失败/环境缺失/内部错误)
EXIT_STATE = 3     # 状态/协议错误(state 缺失/参数缺失)

# --------------------------------------------------------------------------- #
# 覆盖率与测试规则(SKILL.md §4)
# --------------------------------------------------------------------------- #
DEFAULT_THRESHOLD = 80.0
DEFAULT_JACOCO_VERSION = "0.8.12"

# 抽象/接口方法(covered+missed==0)视为达标
ABSTRACT_METHOD_COVERAGE = 100.0

# surefire 判定为"断言失败"的异常类型前缀(含子类包路径)
ASSERTION_FAILURE_TYPE_PREFIXES = (
    "org.opentest4j.AssertionFailedError",
    "org.junit.ComparisonFailure",
    "java.lang.AssertionError",
    "junit.framework.ComparisonFailure",
    "junit.framework.AssertionFailedError",
)

# --------------------------------------------------------------------------- #
# 迭代预算与升级(SKILL.md §6)
# --------------------------------------------------------------------------- #
NO_IMPROVEMENT_ROUNDS = 3        # 连续 N 轮覆盖率无提升 -> ask_user
NO_IMPROVEMENT_NEAR_THRESHOLD_FACTOR = 0.9  # 覆盖率接近门槛(>=threshold*此因子)时抑制无提升升级
TEST_FAIL_STREAK_ROUNDS = 3      # 连续 N 轮测试失败     -> ask_user
METHOD_ROUND_BUDGET = 8          # 单方法最多 N 轮
GLOBAL_ROUND_BUDGET = 30         # 全局最多 N 轮
FINAL_CHECK_FAIL_STREAK_LIMIT = 2  # 终验连续 N 轮不绿且无待修复方法 -> ask_user
RESUME_GRANT_ROUNDS = 3          # ask_user "继续" 时追加的预算轮数(make_plan --grant-rounds)
VALIDATE_FAIL_STREAK_LIMIT = 5   # validate_rules 连续 N 轮违规未通过 -> ask_user

# --------------------------------------------------------------------------- #
# Maven 执行(SKILL.md §3)
# --------------------------------------------------------------------------- #
MVN_TIMEOUT_ENV = "JAVA_UT_MVN_TIMEOUT"
_DEFAULT_MVN_TIMEOUT = 1800
MVN_KILL_GRACE_SECONDS = 5   # 超时两段式终止: SIGTERM 后宽限秒数, 未退出升级 SIGKILL
POST_EOF_WAIT_SECONDS = 30   # 输出 EOF 后等待进程退出的秒数, 超时终止进程组

# mvn 进度报告(防止主 Agent 误判进程无响应)
MVN_PROGRESS_INTERVAL_SECONDS = 30    # 进度输出间隔(秒)
MVN_PROGRESS_FILENAME = "mvn_progress.json"  # 进度状态文件名

# mvn 必带参数: 忽略测试失败以保证失败轮仍产出覆盖率报告, 由 surefire 做唯一判定
MVN_ALWAYS_FLAGS = (
    "-Dsurefire.failIfNoSpecifiedTests=false",
    "-Dmaven.test.failure.ignore=true",
    "-DfailIfNoTests=false",
)


def mvn_timeout() -> int:
    """mvn 超时秒数: 环境变量 JAVA_UT_MVN_TIMEOUT 覆盖, 非法值回退默认。

    以函数(而非模块级常量)暴露, 便于测试期动态改写环境变量。
    """
    raw = os.environ.get(MVN_TIMEOUT_ENV) or _DEFAULT_MVN_TIMEOUT
    try:
        return int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_MVN_TIMEOUT


# --------------------------------------------------------------------------- #
# JVM 描述符
# --------------------------------------------------------------------------- #
JVM_PRIMITIVE_TYPES = {
    "B": "byte", "C": "char", "D": "double", "F": "float",
    "I": "int", "J": "long", "S": "short", "Z": "boolean", "V": "void",
}

# --------------------------------------------------------------------------- #
# 日志格式
# --------------------------------------------------------------------------- #
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
LOGGER_NAMESPACE = "java_ut"

# --------------------------------------------------------------------------- #
# git 命令超时
# --------------------------------------------------------------------------- #
GIT_COMMAND_TIMEOUT_SECONDS = 30  # git 命令超时秒数, 防止 NFS/网络挂起

# --------------------------------------------------------------------------- #
# git worktree(select_worktree)
# --------------------------------------------------------------------------- #
WORKTREE_CONTAINER_SUFFIX = ".worktrees"   # 工作树容器目录后缀(复数)
WORKTREE_NAME_SUFFIX = ".worktree"         # 单个 worktree 目录名前缀
WORKTREE_DEFAULT_NEW_SENTINEL = "__DEFAULT__"  # --new 未附名称时的哨兵值
WORKTREE_NAME_DISALLOWED_CHARS = '<>:"/\\|?*'

# --------------------------------------------------------------------------- #
# 批量执行(batch-unit-test-generator 专用)
# --------------------------------------------------------------------------- #
_BATCH_CLASS_ROUND_BUDGET_ENV = "JAVA_UT_BATCH_CLASS_ROUND_BUDGET"
_DEFAULT_BATCH_CLASS_ROUND_BUDGET = 30


def batch_class_round_budget() -> int:
    """类级总预算: 环境变量 JAVA_UT_BATCH_CLASS_ROUND_BUDGET 覆盖, 非法值回退默认。

    以函数(而非模块级常量)暴露, 便于测试期动态改写环境变量。
    """
    raw = os.environ.get(_BATCH_CLASS_ROUND_BUDGET_ENV) or _DEFAULT_BATCH_CLASS_ROUND_BUDGET
    try:
        return int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_BATCH_CLASS_ROUND_BUDGET
BATCH_AUTO_GRANT = True            # 预算内升级点自动"继续"
DEFAULT_TARGET_BRANCHES = ("master", "main", "develop")  # 自动探测顺序
BATCH_STATE_FILENAME = "batch_state.json"
CLASSES_SUBDIR = "classes"

# --------------------------------------------------------------------------- #
# 大类拆分(batch-unit-test-generator 大类策略)
# --------------------------------------------------------------------------- #
LARGE_CLASS_DIFF_THRESHOLD = 300   # diff_add >= 此值视为大类(行数)
LARGE_CLASS_METHOD_THRESHOLD = 30  # pending 方法数 >= 此值视为大类
METHOD_GROUP_SIZE = 10             # 每组方法数上限
EXPLORATION_MODE_MULTIPLIER = 2   # 探索模式预算倍数(batch_class_round_budget() * 此值)

# --------------------------------------------------------------------------- #
# state.json schema 版本(顺序迁移; 每次结构变更递增并追加迁移函数)
# --------------------------------------------------------------------------- #
STATE_SCHEMA_VERSION = 1

# --------------------------------------------------------------------------- #
# 方法状态
# --------------------------------------------------------------------------- #
STATUS_PENDING = "pending"
STATUS_DONE = "done"
STATUS_SKIPPED = "skipped"
