---
kind: dependency_management
name: 无包管理器依赖声明，第三方二进制以本地 JAR + SHA256 形式随仓分发
category: dependency_management
scope:
    - '**'
source_files:
    - install.sh
    - batch-unit-test-generator/scripts/jacoco/lib/README.md
    - sensitive-log-review/scripts/checkstyle/checkstyle-13.7.0-all.jar.sha256
    - batch-unit-test-generator/scripts/jaut/maven.py
    - batch-unit-test-generator/scripts/batch_diff.py
    - batch-unit-test-generator/scripts/jacoco/fast-single-cov.sh
---

## 1. 整体方式
本仓库是四个面向 Java/任意代码库的 Agent Skill（Python 脚本集合）聚合仓库，**未使用任何语言级包管理器**来声明 Python 或 Java 依赖：
- 根目录及各 skill 子目录均不存在 `requirements.txt`、`pyproject.toml`、`setup.py`、`Pipfile`、`go.mod`、`package.json`、`pom.xml`、`build.gradle` 等依赖清单。
- 所有 Python 脚本仅 import 标准库（`os`、`sys`、`json`、`xml.etree.ElementTree`、`re`、`pathlib`、`subprocess`、`logging`、`urllib`、`yaml`、`toml`、`configparser` 等），未见对 PyPI 第三方包的 `import` 语句。
- 安装与分发由根目录 `install.sh` 完成：它把每个 skill 目录原样复制到目标项目的 `.$tool/skills/<skill>`（`$tool ∈ {claude, qwen, qoder, opencode}`），并清理 `tests`、`__pycache__`、`.pytest_cache`、`test.log` 等非必要文件；对于非 qoder/qwen 工具，还会删除 SKILL.md 及 agents 下 `.md` 中的首行 `tools:` 配置。因此该仓库本身不向宿主项目引入任何运行时依赖。

## 2. 关键文件与位置
- `install.sh`：唯一的发布/安装入口，负责发现 skill、选择工具、复制文件、裁剪无关内容。
- `batch-unit-test-generator/scripts/jacoco/lib/README.md`：说明 JaCoCo 两个 JAR（`jacocoagent.jar`、`jacococli.jar`）应放置的位置、版本约定（与 `config.DEFAULT_JACOCO_VERSION` 保持一致，当前 `0.8.12`）、下载命令以及“缺失时调用方降级到通过 `jacoco-maven-plugin` 注入 agent”的策略。
- `sensitive-log-review/scripts/checkstyle/checkstyle-13.7.0-all.jar.sha256`：存放 `checkstyle-13.7.0-all.jar` 的 SHA256 校验值，用于验证随仓分发的二进制完整性。
- `batch-unit-test-generator/scripts/jaut/maven.py`、`batch-unit-test-generator/scripts/batch_diff.py`、`scripts/jacoco/fast-single-cov.sh`：在运行期解析目标项目的 `pom.xml`（向上查找最近含 `pom.xml` 的目录作为模块根），读取 `jacoco-maven-plugin` 的 excludes 配置，属于“消费宿主项目依赖”而非“声明自身依赖”。

## 3. 架构与约定
- **零外部运行时依赖**：Python 侧完全依赖标准库，避免在宿主环境中安装 pip 包。
- **Java 工具以预编译 JAR 随仓分发**：JaCoCo 的 agent/cli JAR 和 Checkstyle 的 all-in-one JAR 直接放入仓库（或通过 README/sha256 约束其来源与版本），由脚本在执行时直接使用，而不是通过 Maven/Gradle 拉取。
- **版本锚定策略**：JaCoCo 版本由 `config.DEFAULT_JACOCO_VERSION` 与 `scripts/jacoco/lib/README.md` 共同约定；Checkstyle 固定为 `13.7.0`，并以 `.sha256` 文件绑定校验和。
- **降级机制**：当本地 JaCoCo JAR 缺失时，`run_fast_single_cov` 返回失败，调用方自动回退到通过 `jacoco-maven-plugin` 注入 agent 的方式执行测试，从而保证即使缺少本地二进制也能工作。
- **宿主项目依赖不被修改**：多个规则文档（如 `references/UnitTestRules.md`、`CLAUDE.md`、`AGENTS.md`）明确禁止 LLM 修改宿主项目的 `pom.xml`、业务代码或 `state.json`，脚本也仅读取而不写入这些文件。

## 4. 约定与约束
- Python 依赖：仓库内未声明任何 Python 第三方依赖，所有脚本仅使用标准库；若未来需要引入第三方包，当前仓库结构未提供统一的依赖清单或虚拟环境管理约定。
- Java 依赖：本仓库不持有宿主项目的 `pom.xml`/`gradle` 构建文件，也不对其进行修改；仅读取其中的 jacoco excludes 等配置。
- 二进制工件：JaCoCo JAR 与 Checkstyle JAR 以本地文件形式随仓分发，并通过 SHA256（checkstyle）或 README 版本约定（jacoco）进行版本控制；缺失时走降级路径。
- 安装约束：`install.sh` 要求目标目录存在且可写，会交互式确认覆盖已存在的同名 skill；仅复制 skill 目录内容，不会在宿主项目中创建 `requirements.txt`、`pyproject.toml` 等依赖文件。
- 安全/完整性：Checkstyle 的 `.sha256` 文件提供了最小化的完整性校验；JaCoCo 则依赖 README 中指向 Maven Central 的官方下载地址。

综上，该仓库的依赖管理策略是：**Python 侧零依赖（仅标准库），Java 侧工具以预编译 JAR + SHA256/README 版本约定随仓分发，宿主项目的 Maven/Gradle 依赖由脚本只读解析且不修改**。