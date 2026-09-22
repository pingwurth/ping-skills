---
kind: error_handling
name: Python 脚本错误处理：结构化错误码、StepError 协议与分片失败记录
category: error_handling
scope:
    - '**'
source_files:
    - sensitive-log-review/scripts/common/errors.py
    - sensitive-log-review/scripts/common/failures.py
    - sensitive-log-review/scripts/main.py
    - batch-unit-test-generator/scripts/jaut/cli.py
    - batch-unit-test-generator/scripts/jaut/logutil.py
    - java-unit-test-generator/scripts/jaut/logutil.py
    - sensitive-log-review/scripts/check_log_print.py
---

## 1. 整体方案

本仓库包含多个 Python 工具（敏感日志审查、Java 单元测试生成等），每个工具都是独立可执行的 CLI，并通过统一的错误处理约定对外暴露退出码与结构化提示。核心思路是：**环境/参数类错误用统一错误码枚举 + 修复建议模板输出到 stderr；业务步骤失败通过子进程返回码传递；并发流水线用“分片文件”聚合失败记录**。

## 2. 关键文件与职责

- `sensitive-log-review/scripts/common/errors.py`：集中定义 `ErrorCode` 枚举（E001~E007）及 `format_error` / `print_error`，为所有脚本提供一致的错误文案格式（错误码 + 位置 + 详情 + 分步修复建议），并明确“不改变既有异常控制流、退出码契约保持 2、纯文本无 emoji”。
- `sensitive-log-review/scripts/common/failures.py`：实现 `FailureRecorder` 分片写入机制——每个检查步骤覆盖写自己的 `failed-files.<step>.part`，由 `main.py` 在全部步骤结束后调用 `merge_failure_shards` 合并为 `failed-files.list`，行格式 `{步骤标识}\t{文件路径}\t{原因摘要}`，并对写入失败做降级告警。
- `sensitive-log-review/scripts/main.py`：编排层，定义退出码契约（0=通过、1=触发门禁、2=执行错误），四链路并行执行后汇总计数、生成 HTML 报告，并在 `__main__` 中捕获 `KeyboardInterrupt`（130）、`BrokenPipeError`（0）和未预期异常（2）。
- `batch-unit-test-generator/scripts/jaut/cli.py`：定义 `StepError` 异常类，区分 `state_error`（exit 3，状态/协议缺失）与 `exec_error`（exit 2，环境/操作失败），所有入口脚本通过 `run_cli` 统一捕获并转换为 NEXT_STEP 协议的 `Decision` 块输出。
- `java-unit-test-generator/scripts/jaut/logutil.py` 与 `batch-unit-test-generator/scripts/jaut/logutil.py`：将日志仅写入 `<workdir>/logs/<script>.log`，禁止传播到根 logger，确保 stdout 只承载 NEXT_STEP 协议块，避免错误信息污染协议解析。
- `sensitive-log-review/scripts/check_log_print.py` 等子脚本：遵循“stdout 末尾输出 `SUMMARY: ...` 键值对”的约定，供 main.py 通过正则 `parse_summary` 解析计数。

## 3. 架构与约定

### 3.1 退出码契约
- **敏感日志审查**（`main.py` 注释明确）：0=通过，1=触发门禁（violation/sensitive 等计数 > 0），2=执行错误（关键步骤失败）。步骤 3/@ToString 与步骤 6/POJO 注释失败仅记警告，不影响退出码 2 判定。
- **单元测试生成器**（`cli.py` 注释对齐 SKILL.md §2 Exit Code）：StepError.state_error → exit 3（状态/协议错误），StepError.exec_error → exit 2（执行错误），内部未捕获异常统一转 failed + ask_user(exit 2)。

### 3.2 错误分类与呈现
- 环境/依赖/参数错误 → `common.errors.print_error(ErrorCode, location, detail)`，输出形如 `错误[E00X]: 标题\n  位置: ...\n  详情: ...\n  修复建议:\n    1. ...` 的结构化文本到 stderr。
- 业务步骤失败 → 子进程返回非 0 码，编排层读取 stdout 中的 `SUMMARY:` 行解析计数，再根据 `--fail-on` 决定退出码 1。
- 文件读取/解析失败 → 通过 `FailureRecorder.record` 写入分片，最终汇总到 `failed-files.list`，默认不计入门禁统计，可通过 `--strict-mode` 升级为退出码 2。

### 3.3 并发安全设计
- 四链路并行（`ThreadPoolExecutor(max_workers=4)`）+ 每个步骤独立子进程，失败记录采用“各步骤独立分片 + 事后合并”的方案，避免多进程同时追加同一文件的行撕裂问题。
- `clear_failure_shards` 在每次运行前清理旧分片，保证幂等。

### 3.4 日志隔离
- 单元测试生成器使用 `setup_logger(script_name, workdir)` 创建 FileHandler-only 的 logger，`propagate=False` 防止日志混入 stdout，确保 LLM 能正确解析 NEXT_STEP 协议块。
- 模块级兜底 `module_logger()` 在工具函数未显式 setup 时也能落盘到临时目录的 `common.log`。

## 4. 约束与规则

- **错误码固定契约**：`errors.py` 文档声明“这些错误的退出码契约保持不变（仍为 2）”，新增 ErrorCode 不得破坏既有调用方行为。
- **CI 友好**：错误输出不使用 emoji，保持纯文本，便于 CI 日志检索。
- **协议优先**：单元测试生成器的 `run_cli` 强制所有异常最终转为 `Decision` 协议块输出，外部只能通过 exit code 判断成功/失败。
- **失败记录可观测**：当失败文件数超过已处理文件总数 10% 时，main.py 会打印醒目告警，提示检查结果可能不完整。
- **严格模式开关**：`--strict-mode` 允许将“读取/解析失败文件”从仅统计升级为退出码 2，用于 CI 门禁收紧。
- **诊断模式**：`--doctor` 只做环境检查（Python/java/git/checkstyle jar SHA256/规则文件/词典文件/输出目录可写性），全部通过返回 0，任一 FAIL 返回 2。

## 5. 与其他模块的差异

- `sensors-analyze` 与 `batch-unit-test-generator` 的其他脚本（如 `batch_diff.py`、`batch_init.py`）直接 raise 自定义 `WorktreeError` / `ValueError` 并由各自入口捕获，没有共享 `errors.py` 错误码体系，说明该错误码系统目前仅在 `sensitive-log-review` 内统一复用。
- 单元测试生成器两套代码（batch / java 单测）的 `logutil.py` 完全一致，体现跨模块的日志隔离约定被复制推广。
