---
kind: logging_system
name: Python 脚本日志体系：基于 stdlib logging 的文件化输出与 stdout 协议隔离
category: logging_system
scope:
    - '**'
source_files:
    - batch-unit-test-generator/scripts/jaut/logutil.py
    - java-unit-test-generator/scripts/jaut/logutil.py
    - batch-unit-test-generator/scripts/jaut/config.py
    - java-unit-test-generator/scripts/jaut/config.py
    - sensitive-log-review/scripts/main.py
---

## 1. 使用的系统/框架

仓库中所有 Python 脚本统一使用 Python 标准库 `logging`，未引入第三方日志框架（如 loguru、structlog）。日志通过自定义的 `logutil.py` 集中初始化，将每个脚本的运行日志写入工作目录下的文件，stdout 仅用于输出 NEXT_STEP 协议块和显式计划摘要，从而保证 LLM 主进程能稳定解析子脚本的标准输出。

## 2. 关键文件

- `batch-unit-test-generator/scripts/jaut/logutil.py`：批量版 JAut 的日志工具，提供 `setup_logger`、`get_logger`、`module_logger`。
- `java-unit-test-generator/scripts/jaut/logutil.py`：单测版 JAut 的日志工具，实现与批量版完全一致的接口。
- `batch-unit-test-generator/scripts/jaut/config.py`：定义 `LOG_FORMAT`、`LOG_DATE_FORMAT`、`LOGGER_NAMESPACE = "java_ut"`、`LOGS_SUBDIR = "logs"`。
- `java-unit-test-generator/scripts/jaut/config.py`：同上，两个模块各自维护一份相同语义的配置。
- `sensitive-log-review/scripts/main.py`：敏感信息审查流水线编排层，不使用 `logging`，而是通过 `print_error` 输出结构化错误并通过 `subprocess.run(..., capture_output=True)` 收集子步骤 stdout/stderr，最终生成 HTML 报告。
- 各入口脚本（`batch_diff.py`、`batch_init.py`、`batch_next.py`、`batch_update.py`、`build_prompt.py`、`jaut/cli.py`）均通过 `from jaut.logutil import setup_logger` 获取 logger。

## 3. 架构与约定

### 3.1 命名空间与 Logger 树
- 所有 logger 名称以 `config.LOGGER_NAMESPACE`（值为 `java_ut`）为根，按 `java_ut.<script_name>.<workdir>` 形式组织。例如 `java_ut.batch_init.<项目路径>`。
- `setup_logger` 内部缓存 `(script_name, workdir)` 键对应的 logger，避免重复创建 handler。
- 每个 logger 设置 `propagate = False`，禁止消息向根 logger 传播，确保 stdout 不被日志污染。

### 3.2 输出目标：文件而非控制台
- `setup_logger` 在 `<workdir>/logs/<script_name>.log` 下创建 `FileHandler`，以 `mode="w"` 覆盖写入，每次运行只保留最近一次日志。
- 格式由 `config.LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"` 和 `LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"` 统一控制。
- 当模块级函数在未调用 `setup_logger` 时直接写日志，`module_logger()` 会回退到临时目录 `/tmp/batch-unit-test-generator/logs/common.log`（批量版）或 `/tmp/java-unit-test-generator/logs/common.log`（单测版），并追加写入。

### 3.3 stdout 协议隔离
- 文档注释明确声明："日志仅写 <workdir>/logs/<script>.log(FileHandler-only, 不挂控制台 handler), 以保证 stdout 只出现 NEXT_STEP 协议块与显式计划摘要"。
- 因此所有业务逻辑不应直接 `print` 调试信息；CLI 入口仅在必要时打印协议标记和摘要。

### 3.4 敏感信息审查工具的差异
`sensitive-log-review` 没有统一的 `logging` 基础设施：
- 编排层 `main.py` 通过 `subprocess.run(cmd, capture_output=True)` 启动子步骤脚本，捕获其 stdout/stderr。
- 子步骤脚本通过 `common.errors.print_error` 输出结构化错误（带 ErrorCode），并以退出码区分：0=通过、1=触发门禁、2=执行错误。
- 最终汇总结果写入 JSON/文本产物并由 `generate_html_report` 渲染为 HTML 报告，而不是写入日志文件。

## 4. 约定与约束

| 约定 | 说明 | 依据 |
|---|---|---|
| 统一通过 `setup_logger(script_name, workdir)` 初始化日志 | 禁止在各模块内直接 `logging.getLogger(...)` 并自行配置 handler | `logutil.py` 是唯一入口，且缓存机制要求复用同一实例 |
| 日志级别设为 `DEBUG` | 便于排查问题，生产环境可通过调整 handler 过滤 | `setup_logger` 中 `logger.setLevel(logging.DEBUG)` |
| 禁止向根 logger 传播 | 防止日志混入 stdout，破坏 NEXT_STEP 协议解析 | `logger.propagate = False` |
| 日志目录固定为 `<workdir>/logs/` | 文件名来自 `config.LOGS_SUBDIR` | `config.py` 常量 |
| 日志格式固定为 `时间 [级别] 消息` | 日期格式 `%Y-%m-%d %H:%M:%S` | `config.LOG_FORMAT` / `LOG_DATE_FORMAT` |
| 命名空间固定为 `java_ut` | 所有 JAut 相关 logger 共享该根命名空间 | `config.LOGGER_NAMESPACE` |
| 子脚本 stdout 仅承载协议数据 | 调试/业务信息一律走文件日志 | `logutil.py` 顶部 docstring |
| 敏感信息审查工具使用退出码 + 结构化错误 + HTML 报告 | 不使用 `logging`，通过 `print_error` 和产物文件传递结果 | `main.py` 编排逻辑 |

## 5. 观察到的模式

- 两个 JAut 模块（批量版与单测版）的 `logutil.py` 几乎完全一致，体现跨子项目的日志实现复用。
- 日志仅作为辅助诊断手段，核心状态通过 `state.json`、覆盖率 JSON、HTML 报告等持久化产物传递，符合 Agent Skill 的“可恢复、可观测”设计。
- 不存在结构化日志字段（如 JSON 行）、不存在多 sink（仅文件）、不存在日志轮转策略——属于轻量级脚本级日志方案。