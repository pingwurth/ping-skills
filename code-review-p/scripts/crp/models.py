"""类型化领域模型。

以 dataclass 取代散落的裸 dict / 字符串键:
    - 决策产物 Decision 与逻辑路由 Route(do_work 携带 WorkSpec 语义字段)
    - 持久化状态 State(to_dict/from_dict, 对缺失键容错)

嵌套结构(scope/overlay/sweep_plan/候选台账条目等)保持 JSON 形状的 dict ——
它们是数据而非逻辑; 决策层(decisions)自行定义小型冻结上下文, 不直接咀嚼 State。

models 位于 L0, 仅依赖标准库与 config, 禁止反向依赖上层模块。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from . import config


# --------------------------------------------------------------------------- #
# 枚举
# --------------------------------------------------------------------------- #
class Route(str, Enum):
    """决策产物的逻辑路由; 由 transitions(L4) 解析为具体 next_step。

    RESUME 为 run_script(回到 crp.py resume 路由器); 其余分别对应
    do_work / finish / ask_user / abort。
    """

    RESUME = "resume"
    DO_WORK = "do_work"
    FINISH = "finish"
    ASK_USER = "ask_user"
    # 立即终止本技能(question 作为 message 转述给用户, 无 resume)
    ABORT = "abort"


# next_step.type 由 Route 决定
_ROUTE_TO_NEXT_TYPE = {
    Route.RESUME: "run_script",
    Route.DO_WORK: "do_work",
    Route.FINISH: "finish",
    Route.ASK_USER: "ask_user",
    Route.ABORT: "abort",
}


def next_type_of(route: Route) -> str:
    return _ROUTE_TO_NEXT_TYPE[route]


# --------------------------------------------------------------------------- #
# 决策产物
# --------------------------------------------------------------------------- #
@dataclass
class ResumeOption:
    """ask_user 升级中单个用户选项的恢复动作(语义级, 决策层不解析路径)。

    script 为 scripts/ 下的文件名(本引擎恒为 crp.py); params 携带完整增量参数
    (含 --run-dir/--key/--choice 等, transitions 不再自动前缀)。空串 script 表示
    无命令(terminate)。需用户提供值时以占位符(如 TARGET)标注并写 note 说明。
    """

    option: str      # approve_all/skip_all/choice/new_scope/resume_old_scope/resume_run/terminate
    label: str       # 人读选项名(与 question 中的选项一致)
    script: str = ""
    params: list[str] = field(default_factory=list)
    note: str = ""


@dataclass
class NextCommand:
    """do_work 完成后调用方应逐字执行的提交命令(语义级, 决策层不解析路径)。

    script 为 scripts/ 下的文件名(恒为 crp.py); params 携带完整参数
    (含 --run-dir 与 --kind/--file), 调用方逐字执行, 不得增删参数。
    """

    script: str
    params: list[str] = field(default_factory=list)


@dataclass
class Decision:
    """决策核心(decisions)的唯一产物, 纯数据、不含路径解析。

    由 transitions 依 route 生成具体 next_step, protocol 组装为 NEXT_STEP 负载。
    artifacts 每项形如 {"path": <绝对路径>, "kind": <类型>}。
    """

    status: str
    exit_code: int
    summary: str
    route: Route
    reason: str
    # do_work 路由: LLM 判断类工作的完整说明
    instructions: Optional[str] = None
    context: dict = field(default_factory=dict)  # 紧凑内联上下文(角度表/digest/计数)
    output: Optional[str] = None                 # LLM 应写入的输出文件(绝对路径)
    shape: Optional[str] = None                  # 提交文件形状说明(指向 references/submissions.md + 最小示例)
    submit: Optional[NextCommand] = None         # 写完 output 后逐字执行的提交命令
    # ask_user / abort 路由
    question: Optional[str] = None
    resume: list[ResumeOption] = field(default_factory=list)
    # finish 路由
    deliverables: list[str] = field(default_factory=list)
    report: Optional[str] = None                 # 引擎渲染的完整报告(逐字透传, 不得改数字)
    # 通用
    artifacts: list[dict] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    # run_script 路由: 决策自带的完整命令参数(非空时优先于 transitions 默认组装)
    route_params: list[str] = field(default_factory=list)

    @property
    def next_type(self) -> str:
        return next_type_of(self.route)


# --------------------------------------------------------------------------- #
# 持久化状态(state.json)
# --------------------------------------------------------------------------- #
def _default_cursor() -> dict:
    return {
        "round": 0,               # 已执行扫描轮数(v1.4.1 #11: 只计已执行)
        "round_kind": "sweep",    # sweep | gap | fix_review
        "awaiting": "",           # 当前期待的提交种类(AWAITING_KINDS 之一或空)
        "pending_angles": [],     # 本轮尚未回报的角度键
        "pending_verify": [],     # 待验证 [{id, lenses:[...]}]
        "lens_requests": [],      # 升级为 P0/P1 需补透镜的候选 id
        "dry_streak": 0,          # 连续零新增轮数
    }


def _default_accounting() -> dict:
    return {
        "rounds": 0, "total_seen": 0, "dup_dropped": 0, "refuted": 0,
        "below_floor": 0, "kept_after_thresholds": 0, "capped_out": 0,
        "round_history": [],      # [{round, kind, fresh, dup, kept_after_verify, dry}]
        "parity_notes": [],       # 载体 1 重算与 JS 内部计数的可见分歧
    }


@dataclass
class State:
    """state.json 的类型化表示。

    嵌套部分保持 dict(JSON 形状数据); from_dict 对缺失键填默认值,
    因此可安全加载旧版本 state.json。
    """

    schema_version: int = config.STATE_SCHEMA_VERSION
    run_id: str = ""
    created_at: str = ""
    updated_at: str = ""
    # 仓库定位
    repo: dict = field(default_factory=dict)  # {cwd, slug, head, is_git}
    # 运行参数
    mode: str = config.DEFAULT_MODE
    fix_requested: bool = False
    target: Optional[str] = None               # 原始 --target(未解析)
    # 状态机
    phase: str = "scope"                       # PHASES 之一
    cursor: dict = field(default_factory=_default_cursor)
    # Phase 0 产物
    scope: dict = field(default_factory=dict)
    # overlay(project-config)
    overlay: dict = field(default_factory=dict)
    # Phase 1 产物
    brief: dict = field(default_factory=dict)  # {file, sha256, line_count, sections_ok, warnings}
    # 扫描计划
    sweep_plan: dict = field(default_factory=dict)
    # 载体留痕(强制: 任何扫描工作开始前提交)
    carrier: dict = field(default_factory=dict)
    # 候选台账 = 曾见一切(含 dup/refuted), ledger 模块维护条目形状
    candidates: list[dict] = field(default_factory=list)
    # coverage 记账
    coverage: dict = field(default_factory=dict)  # {angles: {key: {ran,raw,fresh,dropped}}, declared}
    # 运行账目(报告 Run: 行的唯一事实源)
    accounting: dict = field(default_factory=_default_accounting)
    # 报告
    report: dict = field(default_factory=dict)  # {file, rendered_at, details}
    # --fix
    fix: dict = field(default_factory=dict)     # {outcomes:[], re_review:{}}
    # 提交幂等账本
    submissions: list[dict] = field(default_factory=list)  # [{seq,kind,file,sha256,applied,note}]
    # 未决 ask_user
    pending_question: Optional[dict] = None    # {key, question}
    # 中断计数(每次 resume 且存在未完成工作时 +1)
    interrupted: dict = field(default_factory=dict)  # {count, last_at}

    # ------------------------------------------------------------------ #
    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "repo": dict(self.repo),
            "mode": self.mode,
            "fix_requested": self.fix_requested,
            "target": self.target,
            "phase": self.phase,
            "cursor": dict(self.cursor),
            "scope": dict(self.scope),
            "overlay": dict(self.overlay),
            "brief": dict(self.brief),
            "sweep_plan": dict(self.sweep_plan),
            "carrier": dict(self.carrier),
            "candidates": list(self.candidates),
            "coverage": dict(self.coverage),
            "accounting": dict(self.accounting),
            "report": dict(self.report),
            "fix": dict(self.fix),
            "submissions": list(self.submissions),
            "pending_question": self.pending_question,
            "interrupted": dict(self.interrupted),
        }

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "State":
        """由 raw dict 构造, 缺失键填默认值(兼容旧状态文件)。"""
        if not data:
            return cls()
        known = cls().to_dict()
        kwargs: dict[str, Any] = {}
        for key, default in known.items():
            if key in data:
                value = data[key]
                # 列表/字典深拷贝, 避免与 raw 共享可变结构
                kwargs[key] = (list(value) if isinstance(value, list)
                               else dict(value) if isinstance(value, dict) else value)
        return cls(**kwargs)

    # ------------------------------------------------------------------ #
    # 便捷访问(只读视图; 决策层仍以冻结上下文为准)
    # ------------------------------------------------------------------ #
    def candidate_by_id(self, cid: str) -> Optional[dict]:
        for c in self.candidates:
            if c.get("id") == cid:
                return c
        return None

    @property
    def is_done(self) -> bool:
        return self.phase in ("done", "aborted")
