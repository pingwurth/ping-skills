---
kind: build_system
name: 多 Skill 聚合仓库的构建与安装体系
category: build_system
scope:
    - '**'
source_files:
    - install.sh
    - batch-unit-test-generator/scripts/jacoco/fast-single-cov.sh
    - java-unit-test-generator/scripts/jacoco/fast-single-cov.sh
    - batch-unit-test-generator/scripts/jaut/maven.py
    - sensitive-log-review/scripts/main.py
    - sensitive-log-review/scripts/common/git_utils.py
    - sensitive-log-review/scripts/checkstyle/checkstyle-13.7.0-all.jar.sha256
---

## 1. 整体方法
本仓库是一个「Agent Skill 集合」，本身不包含 Java/Python 项目的源码编译产物，而是提供一组可被 LLM（Claude/Qwen/Qoder/OpenCode）调用的脚本技能。因此其“构建系统”的核心不是传统意义上的 `make`/`gradle`/`Dockerfile`，而是围绕 **install.sh** 的安装分发机制、各 Skill 内嵌的 Maven/JaCoCo 执行脚本、以及 Python 编排脚本构成的轻量级流水线。

- 顶层入口：`install.sh`，将仓库根目录下的每个子目录（即一个 Skill）复制到目标项目的 `.<tool>/skills/<skill-name>`，并可选清理测试文件、按工具差异处理 SKILL.md 中的 `tools:` 行。
- 单元测试生成类 Skill（`java-unit-test-generator`、`batch-unit-test-generator`）通过内嵌的 `scripts/jacoco/fast-single-cov.sh` 调用 `mvn test-compile surefire:test -DargLine="-javaagent:…"` 驱动 JaCoCo 覆盖率收集，再由 Python `jaut/maven.py`、`surefire.py`、`jacoco.py` 解析报告。
- 敏感日志审查 Skill（`sensitive-log-review`）以 `scripts/main.py` 为编排器，用 `ThreadPoolExecutor` 并行运行 4 条链路（步骤 1–7），最后生成 HTML 报告；依赖本地 `checkstyle-13.7.0-all.jar`（带 `.sha256` 校验基线）和 git。
- 埋点分析 Skill（`sensors-analyze`）仅含 Python 脚本与 JSON schema，无独立构建流程。

## 2. 关键文件
- `install.sh`：Skill 安装器，支持参数 `qwen|qoder|opencode|claude|all` 及交互式选择。
- `batch-unit-test-generator/scripts/jacoco/fast-single-cov.sh`：单类覆盖率采集 shell 封装。
- `java-unit-test-generator/scripts/jacoco/fast-single-cov.sh`：同上，单测生成场景使用。
- `batch-unit-test-generator/scripts/jaut/maven.py`：Maven 命令构建、JaCoCo/Surefire 报告清理、pom.xml 探测（是否配置 jacoco-maven-plugin、是否多模块）。
- `sensitive-log-review/scripts/main.py`：审查流水线编排、HTML 报告生成、门禁退出码契约（0/1/2）。
- `sensitive-log-review/scripts/common/git_utils.py`：git 调用统一封装，分支名白名单校验，防止选项注入。
- `sensitive-log-review/scripts/checkstyle/checkstyle-13.7.0-all.jar` + `.sha256`：二进制依赖与完整性校验。
- 各 Skill 的 `protocol/*.schema.json`：NEXT_STEP 协议状态/下一步约束的 JSON Schema。

## 3. 架构与约定
- **Skill 即目录**：仓库根目录下每个非隐藏子目录就是一个 Skill，由 `install.sh` 的 `discover_skills()` 自动发现（排除 `agents` 等）。新增 Skill 只需在根下新建目录并放入 `SKILL.md`。
- **安装后结构**：目标项目形如 `.<tool>/skills/<skill-name>/`，`agents/` 单独复制到 `.<tool>/agents/`。安装时自动删除 `tests/`、`__pycache__`、`.pytest_cache`、`test.log` 等非必要文件。
- **工具差异化**：`claude`、`opencode` 安装时会删除 SKILL.md 中首个 `tools:` 行；`qwen`、`qoder` 保留该行，体现不同 Agent 对元数据的消费方式不同。
- **Maven/JaCoCo 约定**：
  - 单测生成脚本默认向上查找 `pom.xml` 定位项目根；若指定 `--project-root` 则必须存在 `pom.xml`。
  - 覆盖率采集固定输出到 `<module>/target/site/jacoco/{index.html,jacoco.xml,jacoco.csv}` 与 `<module>/target/jacoco.exec`。
  - 强制 `-DforkCount=1` 并通过绝对路径 `-javaagent:<abs>/jacocoagent.jar` 注入，避免 Surefire fork 进程找不到 agent。
  - 每次执行前清理旧 exec 与报告，防止读取历史结果（见 `maven.py` 注释与 `fast-single-cov.sh` 的 `rm -f $EXEC_FILE`）。
- **安全约定**：所有外部命令调用均走 `subprocess.run([...], check=True)` 或显式检查返回码；git 分支名经 `BRANCH_RE = r"^(?!-)[A-Za-z0-9][\w./@-]{0,199}$"` 校验，拒绝 `..` 与以 `-` 开头的引用，防止选项注入。
- **退出码契约**：`main.py` 明确定义 0=通过、1=触发门禁、2=执行错误；`fast-single-cov.sh` 在缺失参数、找不到 `pom.xml`、Surefire 未执行测试、JaCoCo 报告生成失败时均 `exit 1`。

## 4. 约束与规则（来自代码与文档）
- `install.sh` 要求目标目录存在且可写；若传入目录不存在会询问是否创建。
- 安装时若目标已存在同名 skill，会交互式询问覆盖（默认不覆盖）。
- `fast-single-cov.sh` 要求目标工程必须是 Maven 多模块或单模块项目（存在 `pom.xml`），否则直接报错退出。
- `maven.py` 规定：若 pom 已配置 `jacoco-maven-plugin`，则使用 `mvn test jacoco:report`；否则通过完整坐标挂载 `prepare-agent` + `report`；多模块且指定模块时必须加 `-pl <module> -am`。
- `git_utils.py` 强制所有 git 命令以 `git -C <repo> ...` 形式调用，禁止从任意工作目录产生歧义；分支名长度 ≤200，仅允许字母数字开头，后续可含 `\w./@-`。
- `checkstyle-13.7.0-all.jar` 必须与同目录 `.sha256` 一致，`--doctor` 模式会校验该哈希，不一致则记录 FAIL。
- 仓库没有 `Makefile`、`Dockerfile`、CI YAML、`setup.py`/`pyproject.toml`、`requirements.txt` 等标准构建清单；依赖通过 `#!/usr/bin/env python3` 与 `#!/bin/bash` shebang 声明运行时环境，实际依赖（Java、Maven、git、Python ≥3.10）需由宿主环境提供。
- 测试框架为 pytest（各 Skill 下 `tests/` + `conftest.py` + `.pytest_cache`），但无集中式测试入口，由各 Skill 独立运行。

## 5. 结论
该仓库的“构建系统”本质是 **Shell 安装器 + Python 编排脚本 + 内嵌 Maven/JaCoCo shell 包装** 的组合：`install.sh` 负责分发，各 Skill 内部自包含运行所需的最小脚本集，通过约定好的目录结构与退出码契约协同工作。它不生产可分发的二进制包，也不维护统一的依赖清单，而是把“构建”的责任下沉到被安装的目标项目中（Maven 负责编译，Python 脚本负责编排与报告）。