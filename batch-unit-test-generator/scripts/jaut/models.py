"""类型化领域模型。

以 dataclass 取代旧脚本中散落的裸 dict / 字符串键, 提供:
    - 覆盖率原语 coverage_rate(covered+missed==0 视为达标)
    - 方法/类覆盖率、失败用例、surefire 结果(fail-closed 单点 is_green)
    - 决策产物 Decision 与逻辑路由 Route
    - 持久化状态 State(to_dict/from_dict, 对缺失键容错, 兼容旧 state.json)

models 位于 L0, 仅依赖标准库与 config, 禁止反向依赖上层模块。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from . import config


# --------------------------------------------------------------------------- #
# 覆盖率原语
# --------------------------------------------------------------------------- #
def coverage_rate(covered: int, missed: int) -> float:
    """行覆盖率百分比; covered+missed==0(抽象/接口方法)视为达标 100.0。"""
    total = covered + missed
    if total == 0:
        return config.ABSTRACT_METHOD_COVERAGE
    return covered * 100.0 / total


# --------------------------------------------------------------------------- #
# 枚举
# --------------------------------------------------------------------------- #
class MethodStatus(str, Enum):
    """方法测试状态。取值为字符串, 直接可 JSON 序列化。"""

    PENDING = config.STATUS_PENDING
    DONE = config.STATUS_DONE
    SKIPPED = config.STATUS_SKIPPED


class Route(str, Enum):
    """决策产物的逻辑路由; 由 transitions(L4) 解析为具体 next_step。

    前四个为 run_script, 后三个分别对应 write_code/finish/ask_user。
    """

    MAKE_PLAN = "make_plan"
    BUILD_PROMPT = "build_prompt"
    VERIFY_COVERAGE = "verify_coverage"
    FINAL_CHECK = "final_check"
    BATCH_NEXT = "batch_next"
    BATCH_FINISH = "batch_finish"
    WRITE_CODE = "write_code"
    FINISH = "finish"
    ASK_USER = "ask_user"


# next_step.type 由 Route 决定
_ROUTE_TO_NEXT_TYPE = {
    Route.MAKE_PLAN: "run_script",
    Route.BUILD_PROMPT: "run_script",
    Route.VERIFY_COVERAGE: "run_script",
    Route.FINAL_CHECK: "run_script",
    Route.BATCH_NEXT: "run_script",
    Route.BATCH_FINISH: "run_script",
    Route.WRITE_CODE: "write_code",
    Route.FINISH: "finish",
    Route.ASK_USER: "ask_user",
}


def next_type_of(route: Route) -> str:
    return _ROUTE_TO_NEXT_TYPE[route]


# --------------------------------------------------------------------------- #
# 覆盖率模型
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class MethodKey:
    """方法唯一键 = name + desc(JaCoCo 语义)。"""

    name: str
    desc: str

    def label(self) -> str:
        """人读标签: name(desc), 用于日志与提示。"""
        return f"{self.name}{self.desc}"

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "desc": self.desc}

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> Optional["MethodKey"]:
        if not data:
            return None
        return cls(name=data.get("name", ""), desc=data.get("desc", ""))


@dataclass
class TestOutcome:
    """单轮测试结果(仅记录判定 streak 所需的最小信息)。"""

    __test__ = False  # 防止 pytest 将 Test* 类误当作测试用例收集

    failures: int = 0
    errors: int = 0

    @property
    def failed(self) -> bool:
        return self.failures + self.errors > 0

    def to_dict(self) -> dict[str, int]:
        return {"failures": self.failures, "errors": self.errors}

    @classmethod
    def from_dict(cls, data: dict) -> "TestOutcome":
        return cls(failures=int(data.get("failures", 0)), errors=int(data.get("errors", 0)))


@dataclass
class MethodCoverage:
    """单个方法的覆盖率与迭代轨迹。"""

    key: MethodKey
    covered: int = 0
    missed: int = 0
    status: MethodStatus = MethodStatus.PENDING
    round_rates: list[float] = field(default_factory=list)
    round_test_results: list[TestOutcome] = field(default_factory=list)
    # init 基线快照(finish 报告的 before 值); 旧 state.json 无此字段, 报告降级显示"未记录"
    initial_rate: Optional[float] = None

    @property
    def rate(self) -> float:
        return coverage_rate(self.covered, self.missed)

    @property
    def is_abstract(self) -> bool:
        """covered+missed==0 的抽象/接口方法, 无需测试。"""
        return self.covered + self.missed == 0

    def coverage_met(self, threshold: float) -> bool:
        return self.is_abstract or self.rate >= threshold

    def reset_trajectory(self) -> None:
        """升级后复位轨迹: 用户选择继续时获得全新窗口, 不因陈旧轨迹立即再升级。"""
        self.round_rates = []
        self.round_test_results = []

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "name": self.key.name,
            "desc": self.key.desc,
            "covered": self.covered,
            "missed": self.missed,
            "status": self.status.value,
            "round_rates": list(self.round_rates),
        }
        if self.initial_rate is not None:
            data["initial_rate"] = round(self.initial_rate, 2)
        if self.round_test_results:
            data["round_test_results"] = [r.to_dict() for r in self.round_test_results]
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "MethodCoverage":
        initial = data.get("initial_rate")
        return cls(
            key=MethodKey(name=data.get("name", ""), desc=data.get("desc", "")),
            covered=int(data.get("covered", 0)),
            missed=int(data.get("missed", 0)),
            status=MethodStatus(data.get("status", config.STATUS_PENDING)),
            round_rates=list(data.get("round_rates", []) or []),
            round_test_results=[TestOutcome.from_dict(r) for r in data.get("round_test_results", []) or []],
            initial_rate=float(initial) if initial is not None else None,
        )


@dataclass
class ClassCoverage:
    """类级行覆盖率(含内部类聚合)。"""

    covered: int = 0
    missed: int = 0
    rate: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"covered": self.covered, "missed": self.missed, "rate": round(self.rate, 2)}

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "ClassCoverage":
        data = data or {}
        return cls(
            covered=int(data.get("covered", 0)),
            missed=int(data.get("missed", 0)),
            rate=float(data.get("rate", 0.0)),
        )

    @classmethod
    def of(cls, covered: int, missed: int) -> "ClassCoverage":
        return cls(covered=covered, missed=missed, rate=coverage_rate(covered, missed))


# --------------------------------------------------------------------------- #
# surefire 测试结果
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class FailedCase:
    """单个失败/错误用例。"""

    class_name: str
    method: str
    type: str
    message: str

    @property
    def is_assertion(self) -> bool:
        return bool(self.type) and any(
            self.type.startswith(p) for p in config.ASSERTION_FAILURE_TYPE_PREFIXES
        )

    def summary_line(self) -> str:
        """一行摘要: 类简名#方法名 异常简名: message 首行(fail_line 单点实现)。"""
        cls_simple = self.class_name.rsplit(".", 1)[-1]
        etype = self.type.rsplit(".", 1)[-1] if self.type else "Unknown"
        first = (self.message or "").strip().splitlines()
        msg = first[0].strip() if first else ""
        return f"{cls_simple}#{self.method} {etype}: {msg}"

    def to_dict(self) -> dict[str, str]:
        return {"class": self.class_name, "method": self.method,
                "type": self.type, "message": self.message}

    @classmethod
    def from_dict(cls, data: dict) -> "FailedCase":
        return cls(class_name=data.get("class", ""), method=data.get("method", ""),
                   type=data.get("type", "") or "", message=data.get("message", "") or "")


@dataclass
class TestResult:
    """surefire-reports 解析产物。"""

    __test__ = False  # 防止 pytest 将 Test* 类误当作测试用例收集

    tests: int = 0
    failures: int = 0
    errors: int = 0
    skipped: int = 0
    failed_cases: list[FailedCase] = field(default_factory=list)
    assertion_failures: int = 0
    report_found: bool = False
    parse_errors: int = 0

    def is_green(self, uncleaned_dirs: bool = False) -> bool:
        """fail-closed 唯一实现: 报告缺失/不可解析/清理失败/存在失败用例一律按不绿计。"""
        return (self.report_found
                and self.parse_errors == 0
                and self.failures == 0
                and self.errors == 0
                and not self.failed_cases
                and not uncleaned_dirs)

    def fail_lines(self) -> list[str]:
        return [fc.summary_line() for fc in self.failed_cases]

    def failure_count_for_trajectory(self, uncleaned_dirs: bool = False) -> int:
        """计算本轮失败计数, 供 decisions.test_failure_streak 判定连续失败。

        纯计算函数, 返回用于记录到 round_test_results 的数值。
        存在真实失败/错误时返回 max(failures, 1), 确保 errors-only 轮也计入;
        否则若本轮仍不绿(报告缺失/不可解析/清理失败)至少记 1, 避免被
        test_failure_streak 误判为绿轮。
        """
        if self.failures + self.errors > 0:
            return max(self.failures, 1)
        return 0 if self.is_green(uncleaned_dirs) else 1


# --------------------------------------------------------------------------- #
# 决策产物
# --------------------------------------------------------------------------- #
@dataclass
class ResumeOption:
    """ask_user 升级中单个用户选项的恢复动作(语义级, 决策层不解析路径)。

    script 为 scripts/ 下的文件名; 空串表示无命令(终止)。params 不含 --workdir
    (transitions 统一前缀); 需用户提供值时以占位符(如 "N")标注并写 note 说明。
    """

    option: str      # 选项标识: continue/custom/choice/clear_history/skip_method/adjust_threshold/retry/terminate
    label: str       # 人读选项名(与 question 中的选项一致)
    script: str = ""                       # 恢复脚本文件名; 空 = 无命令(终止)
    params: list[str] = field(default_factory=list)
    note: str = ""    # 需用户补充值(如新门槛 N)或动作说明


@dataclass
class NextCommand:
    """write_code 完成后调用方应逐字执行的命令(语义级, 决策层不解析路径)。

    script 为 scripts/ 下的文件名; params 不含 --workdir(transitions 统一前缀)。
    决策不携带 on_complete 时, transitions 注入默认值 validate_rules.py。
    """

    script: str
    params: list[str] = field(default_factory=list)


@dataclass
class Decision:
    """决策核心(decisions)的唯一产物, 纯数据、不含路径解析。

    由 transitions 依 route 生成具体 next_step, protocol 组装为 NEXT_STEP 负载。
    artifacts 每项形如 {"path": <绝对路径>, "kind": <类型>}; 路径由决策上下文
    (workdir/project_root 等纯值)拼出, 决策函数本身不做 I/O。
    """

    status: str
    exit_code: int
    summary: str
    route: Route
    reason: str
    instructions: Optional[str] = None
    question: Optional[str] = None
    deliverables: list[str] = field(default_factory=list)
    artifacts: list[dict] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    # finish 路由时携带的完整收尾报告(由 report.render_finish_report 生成, 调用方逐字转述)
    report: Optional[str] = None
    # ask_user 路由时携带的用户选项恢复指引(升级后用户决策的落地通道)
    resume: list[ResumeOption] = field(default_factory=list)
    # write_code 路由时携带的"完成后命令"(调用方保存测试文件后逐字执行);
    # 为 None 时 transitions 注入默认 validate_rules.py --workdir <wd>
    on_complete: Optional[NextCommand] = None
    # 需由入口脚本落地到 state 的动作(决策本身不修改状态):
    mark_done: bool = False          # 当前方法达标, 标记 status=done
    reset_trajectory: bool = False   # 升级后复位轨迹, 用户继续时获得全新窗口
    # batch_mode 下预算内自动继续(子代理无感); 耗尽时 False 穿透 ask_user
    auto_grant: bool = False

    @property
    def next_type(self) -> str:
        return next_type_of(self.route)


# --------------------------------------------------------------------------- #
# 持久化状态
# --------------------------------------------------------------------------- #
@dataclass
class State:
    """state.json 的类型化表示。

    字段沿用旧脚本以兼容断点续跑, 并新增 global_iteration 支撑 SKILL §6 全局预算。
    from_dict 对缺失键填默认值, 因此可安全加载旧版本 state.json。
    """

    schema_version: int = config.STATE_SCHEMA_VERSION
    project_root: str = ""
    workdir: str = ""
    threshold: float = config.DEFAULT_THRESHOLD
    target_class: str = ""
    target_method: Optional[str] = None
    source_file: Optional[str] = None
    module: str = "."
    jacoco_version: str = config.DEFAULT_JACOCO_VERSION
    # 覆盖率排除模式(fnmatch, 完整类名含 '$' 内部类, * 跨包段); init 经 --coverage-exclude
    # 写入, 终验未显式传入时沿用 init 轮取值, verify 每轮解析时读取
    coverage_excludes: list[str] = field(default_factory=list)
    mvn_log: str = ""
    # git 基线: {相对路径: 文件内容 SHA-256 哈希}; 旧 state.json 为 list[str], from_dict 兼容
    git_baseline: dict[str, str] = field(default_factory=dict)

    # 当前方法的迭代轮次(切换方法时清零)
    iteration: int = 0
    # 全局迭代轮次(永不重置), 支撑全局预算
    global_iteration: int = 0
    # 预算追加窗口: 用户 ask_user 选择"继续"后经 make_plan --grant-rounds 落地
    method_round_bonus: int = 0    # 单方法预算追加(切换方法时清零)
    global_round_bonus: int = 0    # 全局预算追加(终验延续)
    # validate_rules 连续违规计数(通过/切换方法/用户决策落地时清零, 达上限升级)
    validate_fail_streak: int = 0

    current_method: Optional[MethodKey] = None
    class_coverage: ClassCoverage = field(default_factory=ClassCoverage)
    methods: list[MethodCoverage] = field(default_factory=list)

    coverage_history: list[dict] = field(default_factory=list)
    test_history: list[dict] = field(default_factory=list)

    final_checked: bool = False
    final_check_fail_streak: int = 0

    # 工作树所在分支(init 阶段经 git rev-parse 查询; detached HEAD / git 不可用为 None)
    worktree_branch: Optional[str] = None
    # 达标轮(init 即达标或终验通过)的 surefire 汇总, finish 报告数据源
    final_test_summary: Optional[dict] = None

    test_class_file: str = ""
    test_class_simple: str = ""
    plan: Optional[dict] = None

    # JaCoCo 配置探测缓存: {result: bool, pom_mtimes: {path: mtime}}
    jacoco_config_cache: Optional[dict] = None

    # 批量模式专用(batch-unit-test-generator 新技能均为新建, from_dict 缺省兜底)
    batch_mode: bool = False         # 是否处于批量模式(影响 decide_after_plan / decide_after_verify 路由)
    class_round_used: int = 0        # 类级已耗轮次(供升级判定, 每轮 verify 后递增)
    exploration_mode: bool = False   # 探索模式(大类拆分时预算翻倍)

    # -- 便捷访问 ---------------------------------------------------------- #
    def find_method(self, key: MethodKey) -> Optional[MethodCoverage]:
        """根据方法键查找方法覆盖率条目; 未找到返回 None。"""
        for m in self.methods:
            if m.key == key:
                return m
        return None

    def pending_methods(self) -> list[MethodCoverage]:
        """返回所有状态为 pending 的方法列表。"""
        return [m for m in self.methods if m.status == MethodStatus.PENDING]

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "schema_version": self.schema_version,
            "project_root": self.project_root,
            "workdir": self.workdir,
            "threshold": self.threshold,
            "target_class": self.target_class,
            "target_method": self.target_method,
            "source_file": self.source_file,
            "module": self.module,
            "jacoco_version": self.jacoco_version,
            "mvn_log": self.mvn_log,
            "git_baseline": dict(self.git_baseline),
            "iteration": self.iteration,
            "global_iteration": self.global_iteration,
            "method_round_bonus": self.method_round_bonus,
            "global_round_bonus": self.global_round_bonus,
            "validate_fail_streak": self.validate_fail_streak,
            "current_method": self.current_method.to_dict() if self.current_method else None,
            "class_coverage": self.class_coverage.to_dict(),
            "methods": [m.to_dict() for m in self.methods],
            "coverage_history": list(self.coverage_history),
            "test_history": list(self.test_history),
            "final_checked": self.final_checked,
            "final_check_fail_streak": self.final_check_fail_streak,
        }
        # 可选字段: 仅在已赋值时写出, 与旧脚本保持一致的 state.json 形态
        if self.worktree_branch is not None:
            data["worktree_branch"] = self.worktree_branch
        if self.final_test_summary is not None:
            data["final_test_summary"] = dict(self.final_test_summary)
        if self.coverage_excludes:
            data["coverage_excludes"] = list(self.coverage_excludes)
        if self.test_class_file:
            data["test_class_file"] = self.test_class_file
        if self.test_class_simple:
            data["test_class_simple"] = self.test_class_simple
        if self.plan is not None:
            data["plan"] = self.plan
        if self.jacoco_config_cache is not None:
            data["jacoco_config_cache"] = self.jacoco_config_cache
        if self.batch_mode:
            data["batch_mode"] = self.batch_mode
            data["class_round_used"] = self.class_round_used
            if self.exploration_mode:
                data["exploration_mode"] = self.exploration_mode
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "State":
        # 向后兼容: 旧 state.json 中 git_baseline 为 list[str], 转换为 dict[str, str]
        raw_baseline = data.get("git_baseline", {})
        if isinstance(raw_baseline, list):
            # 旧格式: list[str], 无哈希信息, 转为空哈希字典(升级后首次运行时会补充)
            git_baseline = {p: "" for p in raw_baseline}
        else:
            git_baseline = dict(raw_baseline or {})
        return cls(
            schema_version=int(data.get("schema_version", 1)),
            project_root=data.get("project_root", ""),
            workdir=data.get("workdir", ""),
            threshold=float(data.get("threshold", config.DEFAULT_THRESHOLD)),
            target_class=data.get("target_class", ""),
            target_method=data.get("target_method"),
            source_file=data.get("source_file"),
            module=data.get("module", "."),
            jacoco_version=data.get("jacoco_version", config.DEFAULT_JACOCO_VERSION),
            coverage_excludes=list(data.get("coverage_excludes", []) or []),
            mvn_log=data.get("mvn_log", ""),
            git_baseline=git_baseline,
            iteration=int(data.get("iteration", 0)),
            global_iteration=int(data.get("global_iteration", 0)),
            method_round_bonus=int(data.get("method_round_bonus", 0)),
            global_round_bonus=int(data.get("global_round_bonus", 0)),
            validate_fail_streak=int(data.get("validate_fail_streak", 0)),
            current_method=MethodKey.from_dict(data.get("current_method")),
            class_coverage=ClassCoverage.from_dict(data.get("class_coverage")),
            methods=[MethodCoverage.from_dict(m) for m in data.get("methods", []) or []],
            coverage_history=list(data.get("coverage_history", []) or []),
            test_history=list(data.get("test_history", []) or []),
            final_checked=bool(data.get("final_checked", False)),
            final_check_fail_streak=int(data.get("final_check_fail_streak", 0)),
            worktree_branch=data.get("worktree_branch"),
            final_test_summary=data.get("final_test_summary"),
            test_class_file=data.get("test_class_file", ""),
            test_class_simple=data.get("test_class_simple", ""),
            plan=data.get("plan"),
            jacoco_config_cache=data.get("jacoco_config_cache"),
            batch_mode=bool(data.get("batch_mode", False)),
            class_round_used=int(data.get("class_round_used", 0)),
            exploration_mode=bool(data.get("exploration_mode", False)),
        )


# --------------------------------------------------------------------------- #
# 批量计划条目与总态(batch-unit-test-generator)
# --------------------------------------------------------------------------- #
_BATCH_CLASS_STATUSES = ("pending", "in_progress", "done", "skipped", "failed", "recheck")


@dataclass
class BatchClassEntry:
    """单个类的批量计划条目。"""

    fqcn: str
    simple_name: str
    source_file: str
    module: str
    workdir: str
    status: str = "pending"
    diff_add: int = 0
    diff_del: int = 0
    baseline_rate: float = 0.0
    final_rate: Optional[float] = None
    has_existing_tests: bool = False
    attempts: int = 0
    escalations: int = 0
    rounds_used: int = 0
    skip_reason: Optional[str] = None
    claimed_at: Optional[str] = None
    # --- 大类拆分 ---
    method_count: int = 0                      # pending 方法总数(batch_init 写入)
    method_groups: list[list[str]] = field(default_factory=list)  # 方法名分组
    current_group: int = 0                     # 当前正在处理的组索引
    completed_groups: list[int] = field(default_factory=list)     # 已完成的组索引列表

    @property
    def has_method_groups(self) -> bool:
        """是否已拆分方法组(大类标记)。"""
        return bool(self.method_groups)

    @property
    def all_groups_done(self) -> bool:
        """所有方法组是否已完成。"""
        return (self.has_method_groups
                and len(self.completed_groups) >= len(self.method_groups))

    def current_group_methods(self) -> list[str]:
        """返回当前组的方法名列表; 未分组时返回空列表。"""
        if not self.has_method_groups or self.current_group >= len(self.method_groups):
            return []
        return list(self.method_groups[self.current_group])

    def advance_group(self) -> bool:
        """标记当前组完成并推进到下一组; 返回是否还有未完成的组。"""
        if not self.has_method_groups or self.current_group >= len(self.method_groups):
            return False
        if self.current_group not in self.completed_groups:
            self.completed_groups.append(self.current_group)
        self.current_group += 1
        return self.current_group < len(self.method_groups)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "fqcn": self.fqcn,
            "simple_name": self.simple_name,
            "source_file": self.source_file,
            "module": self.module,
            "workdir": self.workdir,
            "status": self.status,
            "diff_add": self.diff_add,
            "diff_del": self.diff_del,
            "baseline_rate": round(self.baseline_rate, 2),
            "has_existing_tests": self.has_existing_tests,
            "attempts": self.attempts,
            "escalations": self.escalations,
            "rounds_used": self.rounds_used,
        }
        if self.final_rate is not None:
            data["final_rate"] = round(self.final_rate, 2)
        if self.skip_reason is not None:
            data["skip_reason"] = self.skip_reason
        if self.claimed_at is not None:
            data["claimed_at"] = self.claimed_at
        # 大类拆分字段: 仅在有方法组时写出
        if self.method_count:
            data["method_count"] = self.method_count
        if self.method_groups:
            data["method_groups"] = [list(g) for g in self.method_groups]
            data["current_group"] = self.current_group
            data["completed_groups"] = list(self.completed_groups)
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "BatchClassEntry":
        return cls(
            fqcn=data.get("fqcn", ""),
            simple_name=data.get("simple_name", ""),
            source_file=data.get("source_file", ""),
            module=data.get("module", "."),
            workdir=data.get("workdir", ""),
            status=data.get("status", "pending"),
            diff_add=int(data.get("diff_add", 0)),
            diff_del=int(data.get("diff_del", 0)),
            baseline_rate=float(data.get("baseline_rate", 0.0)),
            final_rate=float(data["final_rate"]) if data.get("final_rate") is not None else None,
            has_existing_tests=bool(data.get("has_existing_tests", False)),
            attempts=int(data.get("attempts", 0)),
            escalations=int(data.get("escalations", 0)),
            rounds_used=int(data.get("rounds_used", 0)),
            skip_reason=data.get("skip_reason"),
            claimed_at=data.get("claimed_at"),
            method_count=int(data.get("method_count", 0)),
            method_groups=[list(g) for g in data.get("method_groups", []) or []],
            current_group=int(data.get("current_group", 0)),
            completed_groups=list(data.get("completed_groups", []) or []),
        )


@dataclass
class BatchState:
    """批量计划总态(batch_state.json)。"""

    schema_version: int = 1
    project_root: str = ""
    target_branch: str = ""
    diff_mode: str = "three"
    threshold: float = 80.0
    sort_policy: str = "module-coverage"
    created_at: str = ""
    classes: list[BatchClassEntry] = field(default_factory=list)
    initial_tests: dict = field(default_factory=lambda: {"tests": 0, "failures": 0, "errors": 0})

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "project_root": self.project_root,
            "target_branch": self.target_branch,
            "diff_mode": self.diff_mode,
            "threshold": self.threshold,
            "sort_policy": self.sort_policy,
            "created_at": self.created_at,
            "classes": [c.to_dict() for c in self.classes],
            "initial_tests": dict(self.initial_tests),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "BatchState":
        return cls(
            schema_version=int(data.get("schema_version", 1)),
            project_root=data.get("project_root", ""),
            target_branch=data.get("target_branch", ""),
            diff_mode=data.get("diff_mode", "three"),
            threshold=float(data.get("threshold", 80.0)),
            sort_policy=data.get("sort_policy", "module-coverage"),
            created_at=data.get("created_at", ""),
            classes=[BatchClassEntry.from_dict(c) for c in data.get("classes", []) or []],
            initial_tests=data.get("initial_tests", {"tests": 0, "failures": 0, "errors": 0}),
        )

    def find_entry(self, fqcn: str) -> Optional[BatchClassEntry]:
        """按 FQCN 查找条目; 未找到返回 None。"""
        for c in self.classes:
            if c.fqcn == fqcn:
                return c
        return None

    def next_pending(self) -> Optional[BatchClassEntry]:
        """返回排序后首个 pending 或 recheck 条目; 无则 None。"""
        for c in self.classes:
            if c.status in ("pending", "recheck"):
                return c
        return None

    def all_done(self) -> bool:
        """所有类均已 done/skipped/failed(无 pending/in_progress/recheck)。"""
        return all(c.status in ("done", "skipped", "failed") for c in self.classes)
