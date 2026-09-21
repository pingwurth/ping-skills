"""测试规范硬校验引擎(规则 c / d)。

设计目标: 可扩展。每条硬规则实现统一的 Rule 协议并注册到 HARD_RULES,
新增硬规则 = 新增一个 Rule 子类并加入注册表, validate_rules 入口脚本无需改动。

规则来源 references/UnitTestRules.md:
    c 禁止异常捕获(catch 违规 / 裸 try 违规 / 无 catch 的 try-with-resources 放行)
    d 不允许修改非测试类(git 对比基线, src/main/java 不得有基线外变更)
软约束 a / b / e 只在提示词层约束, 不在此机器判定。

规则 c 实现说明:
    机器判定基于正则匹配 try/catch 关键字, 配合 strip_comments_and_strings
    净化注释/字符串/文本块。经测试, 该方案能正确处理以下边缘情况:
    - 标识符含 "try"/"catch" 子串(如 retryCount、catchException)
    - 注解中的 try(如 @Retryable)
    - 泛型中的 Try(如 List<Try<String>>)
    - 字符串/注释/文本块中的 try-catch
    - Lambda 表达式中的 try-catch
    - 嵌套的 try-catch
    已知局限性: 对于极其复杂的 Java 语法结构(如动态生成的代码), 正则方案可能
    存在边缘情况。如需更高可靠性, 可考虑引入 Java 解析器, 但会增加依赖复杂性。
    当前方案已满足实际使用需求。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable

from .gitops import file_content_hash, git_changed_paths, git_deleted_main_source_paths, is_main_source_path

_CATCH_RE = re.compile(r"\bcatch\b")
_TRY_RE = re.compile(r"\btry\b")
_NON_SPACE_RE = re.compile(r"\S")


@dataclass(frozen=True)
class Violation:
    """单条违规。line=0 表示文件级违规(如规则 d)。"""

    rule_id: str
    line: int
    message: str
    path: Optional[str] = None

    def render(self) -> str:
        """渲染为面向 LLM 的一行违规描述。"""
        loc = f" [行 {self.line}]" if self.line else ""
        return f"规则 {self.rule_id}{loc}: {self.message}"


@dataclass
class RuleContext:
    """规则校验上下文(纯数据)。

    stripped_source : 已剥离注释/字符串的测试源码(规则 c 在其上扫描, 行号与原文件一致)
    project_root    : 项目根(规则 d 需要); 为 None 时跳过 git 类规则
    git_baseline    : init 阶段快照的 src/main/java 既有变更及内容哈希(规则 d 豁免依据)
                      格式: {相对路径: 文件内容 SHA-256 哈希}; 空哈希字符串表示无哈希信息(旧格式兼容)
    """

    stripped_source: str = ""
    project_root: Optional[str] = None
    git_baseline: dict[str, str] = field(default_factory=dict)


@runtime_checkable
class Rule(Protocol):
    """硬规则协议: 一个 id + 一个 check。"""

    id: str

    def check(self, ctx: RuleContext) -> list[Violation]:
        ...


class NoTryCatchRule:
    """规则 c: 禁止 catch 与裸 try; 放行不带 catch 的 try-with-resources。"""

    id = "c"

    def check(self, ctx: RuleContext) -> list[Violation]:
        violations: list[Violation] = []
        content = ctx.stripped_source
        for match in _CATCH_RE.finditer(content):
            violations.append(Violation(
                rule_id=self.id,
                line=content[:match.start()].count("\n") + 1,
                message="检测到 catch 异常处理块, 违反规则 c; 异常验证请用 assertThrows",
            ))
        violations.extend(self._check_bare_try(content))
        violations.sort(key=lambda v: v.line)
        return violations

    def _check_bare_try(self, content: str) -> list[Violation]:
        """裸 try 违规; try 后首个非空字符为 '(' 视为 try-with-resources, 跳过资源括号区间。"""
        found: list[Violation] = []
        pos = 0
        while True:
            match = _TRY_RE.search(content, pos)
            if match is None:
                break
            pos = match.end()
            lineno = content[:match.start()].count("\n") + 1
            rest = content[pos:]
            nxt_match = _NON_SPACE_RE.search(rest)
            nxt = nxt_match.group(0) if nxt_match else ""
            if nxt == "(":
                # try-with-resources: 跳过资源声明括号区间继续扫描
                depth = 0
                for i in range(nxt_match.end() - 1, len(rest)):
                    if rest[i] == "(":
                        depth += 1
                    elif rest[i] == ")":
                        depth -= 1
                        if depth == 0:
                            pos += i + 1
                            break
                continue
            found.append(Violation(
                rule_id=self.id, line=lineno,
                message="检测到裸 try 块, 违反规则 c; 异常验证请用 assertThrows, "
                        "不带 catch 的 try-with-resources 允许",
            ))
        return found


class NoMainSourceEditRule:
    """规则 d: git status 对比基线, src/main/java 下不得有基线之外的新增变更或删除。

    基线豁免逻辑:
    1. 路径不在基线中 -> 违规(新增变更)
    2. 路径在基线中但哈希为空 -> 豁免(旧格式兼容, 无哈希信息)
    3. 路径在基线中且哈希匹配 -> 豁免(文件未被修改)
    4. 路径在基线中但哈希不匹配 -> 违规(文件已被用户修改, LLM 不应再修改)
    """

    id = "d"

    def check(self, ctx: RuleContext) -> list[Violation]:
        if not ctx.project_root:
            return []
        paths = git_changed_paths(ctx.project_root)
        if paths is None:
            return []  # git 不可用/非仓库: 跳过该检查
        current = {p for p in paths if is_main_source_path(p)}
        baseline_paths = set(ctx.git_baseline.keys())
        new = current - baseline_paths
        violations = [Violation(rule_id=self.id, line=0, path=p,
                                message=f"src/main/java 下文件被修改: {p}(违反规则 d, 请还原)")
                      for p in sorted(new)]
        # 检查基线中但内容已被修改的文件
        for p in sorted(current & baseline_paths):
            baseline_hash = ctx.git_baseline.get(p, "")
            # 空哈希表示旧格式兼容, 无哈希信息, 豁免
            if not baseline_hash:
                continue
            # 计算当前文件哈希, 与基线哈希比较
            full_path = Path(ctx.project_root) / p
            current_hash = file_content_hash(full_path)
            if current_hash is not None and current_hash != baseline_hash:
                violations.append(Violation(
                    rule_id=self.id, line=0, path=p,
                    message=f"src/main/java 下文件已被用户修改: {p}(违反规则 d, 文件内容与基线不一致, "
                            f"请还原或使用 --acknowledge 豁免)"))
        # 检测删除的主源码文件(P0-3 修复: 删除同样属于违规)
        deleted = git_deleted_main_source_paths(ctx.project_root)
        if deleted:
            violations.extend(
                Violation(rule_id=self.id, line=0, path=p,
                          message=f"src/main/java 下文件被删除: {p}(违反规则 d, 禁止删除用户已有文件)")
                for p in sorted(deleted)
            )
        return violations


# 硬规则注册表: 新增硬规则只需实现 Rule 协议并加入此列表
HARD_RULES: list[Rule] = [NoTryCatchRule(), NoMainSourceEditRule()]


def run_hard_rules(ctx: RuleContext, rules: Optional[list[Rule]] = None) -> list[Violation]:
    """按注册顺序执行全部硬规则, 汇总违规(规则 c 内部已按行号排序)。"""
    active = rules if rules is not None else HARD_RULES
    violations: list[Violation] = []
    for rule in active:
        violations.extend(rule.check(ctx))
    return violations
