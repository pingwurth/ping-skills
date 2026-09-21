"""LLM 编写提示词组装(build_prompt 阶段)。

PromptBuilder 按分节组装提示词, 每节为独立方法, 便于阅读与扩展:
    标题/门槛 -> 规则文档引用 -> 目标类源文件 -> mvn.log 提示(第2轮起)
    -> 测试类不存在时的骨架要求 -> 当前方法源码 -> 连带 private 方法源码 -> 编写要求

引用 references/UnitTestRules.md 的绝对路径而不复述规则内容; 目标方法及其调用的
本类 private 方法源码由 javasrc 静态抽取(宁多勿漏)。
"""

from __future__ import annotations

from pathlib import Path

from . import config
from .javasrc import arity_from_desc, extract_with_helpers, strip_comments_and_strings
from .models import State


class PromptBuilder:
    """按 state 组装单方法测试编写提示词。"""

    def __init__(self, rules_file: Path) -> None:
        self.rules_file = rules_file

    def build(self, state: State, workdir: Path) -> str:
        current = state.current_method
        if current is None:
            raise ValueError("state.current_method 缺失, 无法组装提示词")
        round_no = state.iteration + 1
        rate = self._current_rate(state)

        source = self._read_source(state)
        arity = arity_from_desc(current.desc)
        main_blocks, helper_blocks = (
            extract_with_helpers(source, current.name, arity) if source else ([], []))

        test_file = state.test_class_file
        test_exists = bool(test_file) and Path(test_file).is_file()
        pkg = state.target_class.rpartition(".")[0]
        test_simple = state.test_class_simple or (state.target_class.rsplit(".", 1)[-1] + "Test")

        lines: list[str] = []
        lines += self._section_header(state, current, round_no, rate)
        lines += self._section_rules()
        lines += self._section_paths(state, test_file)
        lines += self._section_class_skeleton(state)
        if state.iteration >= 1:
            lines += self._section_mvn_log(state, workdir)
            lines += self._section_last_failures(state)
        if test_exists:
            lines += self._section_existing_tests(test_file)
        else:
            lines += self._section_skeleton(pkg, test_simple)
        if main_blocks:
            lines += self._section_code("当前方法源码:", main_blocks)
        if helper_blocks:
            lines += self._section_code(
                "该方法调用的本类 private 方法(按规则 a 需连带覆盖, 通过公有入口间接测试):",
                helper_blocks)
        lines += self._section_requirements(test_file)
        return "\n".join(lines)

    # -- 各分节 ------------------------------------------------------------- #
    def _current_rate(self, state: State) -> float:
        entry = state.find_method(state.current_method)  # type: ignore[arg-type]
        return entry.rate if entry else 0.0

    def _read_source(self, state: State) -> str:
        if not state.source_file:
            return ""
        return Path(state.source_file).read_text(encoding="utf-8", errors="replace")

    def _section_header(self, state: State, current, round_no: int, rate: float) -> list[str]:
        return [
            f"为 Java 类 {state.target_class} 的方法 {current.name}{current.desc} "
            f"编写/完善 JUnit5 + Mockito 单元测试(第 {round_no} 轮)。",
            f"目标: 该方法行覆盖率 ≥ {state.threshold}%(当前 {rate:.1f}%)。",
            "",
        ]

    def _section_rules(self) -> list[str]:
        return [
            f"编码规范完整文档(建议阅读): {self.rules_file}",
            "",
            "**必须遵守的关键规则(摘要)**:",
            "- 规则 a: 被测方法调用的本类 private 方法必须一并覆盖",
            "- 规则 b: 非本类依赖一律用 Mockito mock，禁止 @SpringBootTest 等容器测试；",
            "  strict stubs 下只 stub 实际执行到的调用，未用 stub 删除或改 lenient()",
            "- 规则 c: 禁止 try/catch 异常捕获，异常场景用 assertThrows",
            "- 规则 d: 只能修改测试文件，不得修改 src/main/java 下的任何文件",
            "- 规则 f: 测试必须真实通过；每个 @Test 以断言结尾且断言独立预期值，",
            "  禁止抄实际值/删除失败用例/@Disabled 跳过",
            "",
        ]

    def _section_paths(self, state: State, test_file: str) -> list[str]:
        return [
            f"目标类源文件: {state.source_file}",
            f"测试类文件: {test_file}",
        ]

    def _section_class_skeleton(self, state: State) -> list[str]:
        """抽取目标类的字段、构造器、import 等结构信息，帮助 LLM 理解类全貌。"""
        if not state.source_file:
            return []
        try:
            source = Path(state.source_file).read_text(encoding="utf-8", errors="replace")
        except (OSError, IOError):
            return []
        if not source:
            return []
        # 去掉注释和字符串以便提取结构
        stripped = strip_comments_and_strings(source)
        lines = ["", "目标类结构摘要(字段/构造器):", "```java"]
        # 提取 import 块
        import re
        imports = re.findall(r'^import\s+.*?;$', stripped, re.MULTILINE)
        if imports:
            lines.extend(imports[:20])  # 最多显示 20 个 import
        # 提取类声明和字段(到第一个方法定义之前)
        method_pattern = re.compile(
            r'^\s*(?:public|protected|private|static|final|abstract|synchronized|native|default|strictfp|volatile|transient|\s)+[\w<>\[\],\s]+\s+\w+\s*\(',
            re.MULTILINE
        )
        match = method_pattern.search(stripped)
        class_start = re.search(r'\b(?:class|interface|enum)\s+\w+', stripped)
        if class_start:
            end_pos = match.start() if match else min(class_start.start() + 1000, len(stripped))
            class_decl = stripped[class_start.start():end_pos].strip()
            # 限制长度
            if len(class_decl) > 1500:
                class_decl = class_decl[:1500] + "\n... (已截断)"
            lines.append(class_decl)
        lines.append("```")
        return lines

    def _section_mvn_log(self, state: State, workdir: Path) -> list[str]:
        mvn_log = state.mvn_log or str(workdir / config.MVN_LOG_FILENAME)
        return ["", f"上一轮未达标。请先阅读最近一次 mvn 执行日志定位问题, 再修改测试: {mvn_log}",
                "日志可能很大, 先用 grep 定位 [ERROR]、BUILD FAILURE、FAILURE! 与测试失败摘要, 不要整读文件。"]

    def _section_skeleton(self, pkg: str, test_simple: str) -> list[str]:
        return [
            "",
            "测试类尚不存在, 请先创建, 骨架要求:",
            f"  - 包名 {pkg}, 类名 {test_simple}, 路径如上;",
            "  - JUnit5(org.junit.jupiter.api.Test) + Mockito(@ExtendWith(MockitoExtension.class));",
            "  - 依赖对象全部 @Mock 注入, 被测类用 @InjectMocks 或手工装配。",
        ]

    def _section_existing_tests(self, test_file: str) -> list[str]:
        """第2轮起，读取并显示现有测试文件内容，帮助 LLM 了解已写测试。"""
        if not test_file or not Path(test_file).is_file():
            return []
        content = Path(test_file).read_text(encoding="utf-8", errors="replace")
        # 只取前 2000 字符，避免提示词过长
        truncated = content[:2000]
        suffix = "\n... (文件过长已截断)" if len(content) > 2000 else ""
        return [
            "",
            "现有测试文件内容:",
            "```java",
            truncated + suffix,
            "```",
        ]

    def _section_last_failures(self, state: State) -> list[str]:
        """显示上轮测试失败详情，帮助 LLM 针对性修复。"""
        if not state.test_history:
            return []
        # 取最近一轮失败记录
        last_failure = state.test_history[-1]
        failures = last_failure.get("failed", [])
        compile_errors = last_failure.get("compile_errors", [])
        if not failures and not compile_errors:
            return []
        lines = ["", "上轮测试失败详情:"]
        if failures:
            lines.append("失败用例:")
            for fail in failures[:5]:  # 最多显示 5 个
                lines.append(f"  - {fail}")
        if compile_errors:
            lines.append("编译错误:")
            for err in compile_errors[:5]:
                lines.append(f"  - {err}")
        return lines

    def _section_code(self, title: str, blocks: list[str]) -> list[str]:
        return ["", title, "```java", *[b.strip("\n") for b in blocks], "```"]

    def _section_requirements(self, test_file: str) -> list[str]:
        return [
            "",
            "编写要求: 只针对上述方法补充或修改测试方法; 每个 @Test 方法以断言结尾且断言为独立预期值"
            "(禁止抄实际值/assertEquals(x,x)/删除失败用例/@Disabled 跳过消红); "
            "禁止 try/catch 异常捕获(异常场景用 assertThrows); "
            "被测类的一切非本类依赖一律用 Mockito mock, 不做真实调用, "
            "禁止 @SpringBootTest 等容器测试(规则 b); "
            f"只能修改 {test_file} 这一个文件, "
            "不得修改 src/main/java 下的任何文件。完成后保存测试文件即可。",
        ]


def default_rules_file(scripts_dir: Path) -> Path:
    """规则文档绝对路径: <scripts 上级>/references/UnitTestRules.md。"""
    return (Path(scripts_dir).parent / config.REFERENCES_DIRNAME
            / config.UNIT_TEST_RULES_FILENAME)
