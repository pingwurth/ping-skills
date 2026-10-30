"""code-review-p 确定性引擎全局常量(单一事实源)。

所有阈值/上限/路径模板/协议枚举集中于此, 其余模块只读引用, 禁止散落字面量。
行为准绳为 SKILL.md; 冲突时权威链: Schema > references > SKILL.md > LLM。
"""

from __future__ import annotations

import os

SKILL_NAME = "code-review-p"
ENGINE_NAME = "crp"
ENTRY_FILENAME = "crp.py"

# --------------------------------------------------------------------------- #
# 运行目录(被审仓库之外 —— 只读纪律 + 不污染 Phase 0 自身依赖的 ?? 检测)
# --------------------------------------------------------------------------- #
STATE_FILENAME = "state.json"
LOGS_SUBDIR = "logs"    # 每子命令日志(协议块缺失时读尾部 50 行)
IN_SUBDIR = "in"        # LLM 提交物(overlay.json / brief.md / candidates-*.json …)
WORK_SUBDIR = "work"    # 引擎产出给 LLM 的输入(gap digest / 验证批次)
OUT_SUBDIR = "out"      # 报告与台账导出

# 状态根目录: CRP_HOME > (Windows: %LOCALAPPDATA%) > XDG_STATE_HOME > ~/.local/state
CRP_HOME_ENV = "CRP_HOME"
# 轮次上限环境覆盖(钳制 [MAX_ROUNDS_MIN, MAX_ROUNDS_MAX])
CRP_MAX_ROUNDS_ENV = "CRP_MAX_ROUNDS"

RUN_ID_PREFIX = "crp"
RUN_ID_RANDOM_HEX = 4

# --------------------------------------------------------------------------- #
# NEXT_STEP 协议
# --------------------------------------------------------------------------- #
NEXT_STEP_PROTOCOL_VERSION = "1.0"
NEXT_STEP_BEGIN_MARKER = ":::NEXT_STEP_BEGIN:::"
NEXT_STEP_END_MARKER = ":::NEXT_STEP_END:::"

# status 取值
NEXT_STEP_STATUSES = ("success", "empty", "failed", "needs_input")
# next_step.type 取值(do_work 替代 jaut 的 write_code —— 本技能的 LLM 工作是异构的:
# brief/overlay/候选/裁决/报告细节, 而非仅写代码)
NEXT_STEP_TYPES = ("run_script", "do_work", "ask_user", "finish", "abort")

# 退出码契约(SKILL.md §协议)
EXIT_OK = 0        # 步骤干净完成(含 finish)
EXIT_CONTINUE = 1  # 等待 LLM/用户工作(do_work/ask_user/继续流转 —— 正常信号非失败)
EXIT_ERROR = 2     # 脚本执行错误(git 失败/内部错误)
EXIT_STATE = 3     # 状态/提交错误(state 缺失/参数缺失/错序提交)

# --------------------------------------------------------------------------- #
# 模式参数(路由矩阵测试锁死)
# --------------------------------------------------------------------------- #
MODES = ("quick", "standard", "deep")
DEFAULT_MODE = "standard"

# 12 角度 + T(brief §Targeted checks 的执行者; 非 angles.md 目录角度)
FULL_ANGLE_KEYS = ("A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "T")
# quick 精度子集(SKILL.md Phase 2); K 仅在 overlay 红线非空时追加(由引擎判定)
QUICK_ANGLE_KEYS = ("A", "B", "C")
GAP_ANGLE_KEY = "GAP"

KEEP_NONE = "none"                                # quick: 无验证轮, 不按裁决过滤
KEEP_CONFIRMED_P01 = "confirmed_p01"              # standard: CONFIRMED + P0/P1 PLAUSIBLE
KEEP_CONFIRMED_PLAUSIBLE = "confirmed_plausible"  # deep: CONFIRMED + PLAUSIBLE

MODE_PARAMS: dict[str, dict] = {
    "quick": {"max_rounds": 1, "dry_required": 0, "keep": KEEP_NONE, "cap": 4},
    "standard": {"max_rounds": 2, "dry_required": 1, "keep": KEEP_CONFIRMED_P01, "cap": 10},
    "deep": {"max_rounds": 4, "dry_required": 2, "keep": KEEP_CONFIRMED_PLAUSIBLE, "cap": 20},
}
MAX_ROUNDS_MIN = 1
MAX_ROUNDS_MAX = 5


def mode_params(mode: str) -> dict:
    """模式的固化参数(角度集之外的: 轮次/收敛/保留阈值/上限)。"""
    base = dict(MODE_PARAMS[mode])
    raw = os.environ.get(CRP_MAX_ROUNDS_ENV)
    if raw:
        try:
            base["max_rounds"] = max(MAX_ROUNDS_MIN, min(int(raw), MAX_ROUNDS_MAX))
        except (TypeError, ValueError):
            pass
    return base


# --------------------------------------------------------------------------- #
# 判定阈值(calibration.md 的机器强制部分)
# --------------------------------------------------------------------------- #
SEVERITIES = ("P0", "P1", "P2", "P3")
HIGH_SEVERITIES = ("P0", "P1")
VERDICTS = ("CONFIRMED", "PLAUSIBLE", "REFUTED")
LENSES = ("correctness", "reproduction", "security")
CONFIDENCE_FLOOR = 0.5  # 任何模式不上榜(calibration)

# 排序: 严重度 -> 类别 -> 置信度降序
CATEGORY_RANK = {
    "correctness": 0, "security": 1, "cleanup": 2, "reuse": 2,
    "simplification": 2, "efficiency": 2, "altitude": 3, "conventions": 3,
}
CATEGORY_RANK_DEFAULT = 4

# --------------------------------------------------------------------------- #
# 去重(与 workflows/deep-review.js 语义逐点对齐, tests 奇偶校验锁死)
# --------------------------------------------------------------------------- #
DEDUP_LINE_BAND = 3   # 同 file 行号带
OVERLAP_CAP = 3       # 摘要 token 重叠数上限(半数取 min(|a|,|b|), 上限 3)

# --------------------------------------------------------------------------- #
# fan-out 启发式(standard 模式)
# --------------------------------------------------------------------------- #
FANOUT_FILE_THRESHOLD = 8
FANOUT_LINE_THRESHOLD = 800
STANDARD_FANOUT_ANGLES = ("B", "C", "E", "L", "T")


def default_max_agents(n_angles: int) -> int:
    """载体 1 预算默认值: 随 fan-out 宽度扩展(v1.4.1 #12)。"""
    return max(45, 8 * max(1, n_angles))


# --------------------------------------------------------------------------- #
# 提交上限(拒收不截断 —— 拒绝消息带序号与上限)
# --------------------------------------------------------------------------- #
MAX_CANDIDATES_PER_FILE = 200
MAX_CANDIDATES_PER_ANGLE = 8   # 单 finder 上限(deep-review.js 契约); 超出计 dropped
MAX_SUMMARY_CHARS = 500
MAX_SCENARIO_CHARS = 2000
MAX_EVIDENCE_CHARS = 4000
MAX_VOTES_PER_FILE = 40
VERIFY_BATCH_SIZE = 30         # 单个 do_work 验证批次的候选数上限
GAP_DIGEST_MAX_ENTRIES = 50    # gap digest 逐条列出上限, 之后按文件卷展

# --------------------------------------------------------------------------- #
# git(Phase 0 阶梯)
# --------------------------------------------------------------------------- #
GIT_TIMEOUT_SECONDS = 30
EMPTY_TREE_HASH = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
DEFAULT_BRANCH_CANDIDATES = ("origin/main", "origin/master", "main", "master")

# --------------------------------------------------------------------------- #
# overlay(references/project-config.md)
# --------------------------------------------------------------------------- #
OVERLAY_CANDIDATE_PATHS = (".claude/code-review-p.md", "code-review-p.md")
HEURISTIC_GATE_FILES = ("Makefile", "makefile", "GNUmakefile", "justfile", "package.json")
HEURISTIC_GATE_DIR = ".github/workflows"
HEURISTIC_GATE_PATTERN = r"check|lint|test|verify|migration"  # re.IGNORECASE

# --------------------------------------------------------------------------- #
# brief(六节契约; 语言中立校验)
# --------------------------------------------------------------------------- #
BRIEF_MIN_SECTIONS = 6    # markdown ## 节数下限(六节; 区域 brief 可更多)
BRIEF_SOFT_LINE_LIMIT = 30  # 超限仅警告(advisory)

# --------------------------------------------------------------------------- #
# 报告
# --------------------------------------------------------------------------- #
REPORT_TABLE_COLUMNS = ("#", "Location", "Sev", "Finding")

# --------------------------------------------------------------------------- #
# state.json schema 版本(顺序迁移; 每次结构变更递增并追加迁移函数)
# --------------------------------------------------------------------------- #
STATE_SCHEMA_VERSION = 1

# phase 取值(引擎状态机)
PHASES = ("scope", "overlay", "gates", "brief", "carrier", "sweep",
          "verify", "report", "fix", "fix_review", "done", "aborted")
# cursor.awaiting 取值(提交种类 + answer)
AWAITING_KINDS = ("overlay", "brief", "candidates", "verdicts", "details",
                  "carrier", "fix_outcomes", "answer")
ROUND_KINDS = ("sweep", "gap", "fix_review")

# ask_user key 取值
QUESTION_KEYS = ("scope", "gates", "drift", "run")
# resume option 枚举(next-step.schema.json 同步)
RESUME_OPTIONS = ("approve_all", "skip_all", "choice", "new_scope",
                  "resume_old_scope", "resume_run", "terminate")

# --------------------------------------------------------------------------- #
# 日志格式
# --------------------------------------------------------------------------- #
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
LOGGER_NAMESPACE = "crp"
