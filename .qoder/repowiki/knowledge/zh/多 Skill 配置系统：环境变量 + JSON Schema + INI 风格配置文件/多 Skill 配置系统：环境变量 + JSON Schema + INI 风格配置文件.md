---
kind: configuration_system
name: 多 Skill 配置系统：环境变量 + JSON Schema + INI 风格配置文件
category: configuration_system
scope:
    - '**'
source_files:
    - batch-unit-test-generator/scripts/jaut/config.py
    - java-unit-test-generator/scripts/jaut/config.py
    - sensitive-log-review/scripts/common/pojo_config.py
    - sensitive-log-review/scripts/rules/pojo.config
    - sensitive-log-review/scripts/rules/log-scanner.json
    - sensors-analyze/sensors.schema.json
    - sensors-analyze/.claude/settings.local.json
---

## 1. 总体方案

本仓库包含四个独立 Python Skill（批量/单类 Java 单元测试生成、敏感日志审查、埋点分析），每个 Skill 自有一套配置加载机制，没有跨 Skill 的统一配置框架。整体采用三种互补方式组合：

- **运行时参数**：通过 `os.environ` 读取环境变量覆盖默认阈值/超时/预算等运行期行为。
- **项目级数据文件**：以 JSON Schema 或简易 INI 风格文本文件声明规则与元数据，由脚本在目标项目根目录下查找并解析。
- **模块内常量集中管理**：所有硬编码阈值、协议枚举、路径模板集中在 `config.py` 或专用配置模块中，作为单一事实源供其他模块只读引用。

## 2. 关键文件与位置

| Skill | 核心配置位置 | 说明 |
|---|---|---|
| batch-unit-test-generator | `scripts/jaut/config.py` | 全局常量与配置（覆盖率阈值、迭代预算、Maven 超时、NEXT_STEP 协议、工作树命名等） |
| java-unit-test-generator | `scripts/jaut/config.py` | 同上，但暴露 `method_round_budget()` / `global_round_budget()` 两个可被环境变量覆盖的预算函数 |
| sensitive-log-review | `scripts/common/pojo_config.py` + `scripts/rules/pojo.config` + `scripts/rules/log-scanner.json` | POJO 目录/后缀判定、日志扫描器开关 |
| sensors-analyze | `sensors.schema.json` + `.claude/settings.local.json` | LLM 输出 JSON Schema；Claude 权限配置 |

## 3. 架构与设计约定

### 3.1 环境变量覆盖模式（JAut 双 Skill）

`config.py` 将“默认值 + 环境变量名”成对定义，并通过**函数**而非模块级变量暴露给调用方，以便测试期动态改写 `os.environ`：

- `mvn_timeout()` → 读取 `JAVA_UT_MVN_TIMEOUT`，非法值回退 `_DEFAULT_MVN_TIMEOUT = 1800`。
- `batch_class_round_budget()` → 读取 `JAVA_UT_BATCH_CLASS_ROUND_BUDGET`，用于批量模式的类级总预算。
- `method_round_budget()` / `global_round_budget()` → 分别读取 `JAVA_UT_METHOD_ROUND_BUDGET` / `JAVA_UT_GLOBAL_ROUND_BUDGET`。

该模式在 `tests/test_config.py` 中被显式验证：空字符串会命中 `or` 分支从而回退到默认值。

### 3.2 项目级 INI 风格配置文件（sensitive-log-review）

`sensitive-log-review/scripts/common/pojo_config.py` 提供唯一解析器 `load_pojo_config(config_path)`，支持 `[pojo-dir]` 与 `[pojo-file]` 两个节，每行一个条目。约定：

- 缺失文件或空节时回退到内置 `DEFAULT_DIRS` / `DEFAULT_SUFFIXES`。
- 目录名与文件名后缀统一转小写后匹配。
- 文件判定限定在 `src/main/java` 之后的路径段。
- 配套数据文件位于 `scripts/rules/pojo.config`，由多个脚本共享。

### 3.3 JSON Schema 驱动的数据契约（sensors-analyze）

`sensors.schema.json` 使用 JSON Schema draft-07 严格约束 LLM 产出的埋点分析报告结构，包括 `meta`、`paradigms`、`entryFunction.kind`（wrapper/sdk_direct/directive/decorator/hook/autoTrack_config）、`callChainStep.data_flow.action`（source/transform/pass/merge/drop）等枚举字段。下游 Python 脚本据此提取入口函数、定位调用点，再由 LLM 逐条确认上报字段。

此外 `.claude/settings.local.json` 仅声明 Claude Agent 的 `permissions.allow`（如 `WebSearch`），属于工具侧配置，非业务配置。

### 3.4 模块内集中常量（JAut 双 Skill）

`scripts/jaut/config.py` 是“单一事实源”，注释明确声明“其余模块只读引用，禁止散落字面量”。涵盖：

- 路径与工作目录：`STATE_FILENAME`、`COVERAGE_FILENAME`、`WORKDIR_PARTS = (".agent", "java-unit-test-generator")`、`PRUNE_DIRS`。
- NEXT_STEP 协议版本、起止标记、status/type 枚举、退出码契约（EXIT_OK=0, EXIT_CONTINUE=1, EXIT_ERROR=2, EXIT_STATE=3）。
- 覆盖率阈值 `DEFAULT_THRESHOLD = 80.0`、`DEFAULT_JACOCO_VERSION = "0.8.12"`、抽象方法覆盖率视为达标 `ABSTRACT_METHOD_COVERAGE = 100.0`。
- 迭代预算：`NO_IMPROVEMENT_ROUNDS = 3`、`TEST_FAIL_STREAK_ROUNDS = 3`、`METHOD_ROUND_BUDGET = 8`、`GLOBAL_ROUND_BUDGET = 30`、`RESUME_GRANT_ROUNDS = 3`、`VALIDATE_FAIL_STREAK_LIMIT = 5`。
- Maven 执行：`MVN_ALWAYS_FLAGS` 强制带 `-Dmaven.test.failure.ignore=true` 以保证失败轮仍产出覆盖率报告。
- Git worktree 命名规范：`.worktrees` 容器后缀、`.worktree` 单个目录前缀、禁用字符 `<>:"/\|?*`。
- state.json schema 版本 `STATE_SCHEMA_VERSION = 1`，注释要求每次结构变更递增并追加迁移函数。

## 4. 约定与约束

- **行为准绳为 SKILL.md**：`config.py` 头部注释明确“与其冲突时以 SKILL.md 为准”，即文档优先于代码默认值。
- **环境变量命名空间**：JAut 相关变量统一以 `JAVA_UT_` 前缀（`JAVA_UT_MVN_TIMEOUT`、`JAVA_UT_METHOD_ROUND_BUDGET`、`JAVA_UT_GLOBAL_ROUND_BUDGET`、`JAVA_UT_BATCH_CLASS_ROUND_BUDGET`），避免与其他工具污染。
- **非法值安全回退**：所有 `os.environ.get(...)` 取值均包裹 try/except `(TypeError, ValueError)`，非法值直接回退默认值，不抛异常中断流程。
- **配置文件缺失容错**：`pojo_config.load_pojo_config` 在文件不存在或解析出错时回退到内置默认集合，保证扫描不会因配置缺失而失败。
- **JSON Schema 强校验**：`sensors.schema.json` 使用 `additionalProperties: false` 与 `required` 字段，确保 LLM 输出结构可被下游脚本稳定消费。
- **状态文件版本化**：`state.json` 通过 `STATE_SCHEMA_VERSION` 顺序迁移，每次结构变更需递增版本号并追加迁移函数。
- **工作树命名约束**：worktree 名称禁止包含 `<>:"/\|?*` 等 shell/文件系统危险字符，防止跨平台兼容问题。
- **Maven 必带参数**：`MVN_ALWAYS_FLAGS` 强制忽略测试失败，使覆盖率收集不受断言失败影响，由 surefire 做唯一成败判定。

## 5. 各 Skill 差异速览

| Skill | 配置来源 | 覆盖方式 | 持久化格式 |
|---|---|---|---|
| batch-unit-test-generator | `scripts/jaut/config.py` | 环境变量 `JAVA_UT_*` | `state.json` / `batch_state.json`（受 schema 版本控制） |
| java-unit-test-generator | `scripts/jaut/config.py` | 环境变量 `JAVA_UT_*` | `state.json`（受 schema 版本控制） |
| sensitive-log-review | `scripts/rules/pojo.config` + `log-scanner.json` | 修改配置文件 | INI 风格文本 + JSON |
| sensors-analyze | `sensors.schema.json` + `.claude/settings.local.json` | 修改 Schema / Claude 设置 | JSON Schema + JSON |

总体而言，该仓库未引入第三方配置库（如 pydantic-settings、dynaconf、python-dotenv），而是基于标准库 `os.environ` 与轻量级自定义解析器实现，强调“默认值 + 环境变量覆盖 + 项目级配置文件”三层叠加，且每个 Skill 保持配置边界清晰互不干扰。