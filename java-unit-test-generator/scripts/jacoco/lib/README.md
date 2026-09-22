# JaCoCo 工具 JAR

本目录需要放置以下两个文件，供 `fast-single-cov.sh` 使用：

| 文件 | 用途 | 下载地址 |
|------|------|----------|
| `jacocoagent.jar` | Java agent，挂载到被测 JVM 采集覆盖率执行数据 | [Maven Central](https://repo1.maven.org/maven2/org/jacoco/org.jacoco.agent/) |
| `jacococli.jar` | CLI 工具，将 `.exec` 数据转换为 HTML/XML/CSV 报告 | [Maven Central](https://repo1.maven.org/maven2/org/jacoco/org.jacoco.cli/) |

## 版本

与 `config.DEFAULT_JACOCO_VERSION` 保持一致（当前 `0.8.12`）。

## 快速下载

```bash
JACOCO_VERSION=0.8.12
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

curl -L -o "$SCRIPT_DIR/jacocoagent.jar" \
  "https://repo1.maven.org/maven2/org/jacoco/org.jacoco.agent/${JACOCO_VERSION}/org.jacoco.agent-${JACOCO_VERSION}-runtime.jar"

curl -L -o "$SCRIPT_DIR/jacococli.jar" \
  "https://repo1.maven.org/maven2/org/jacoco/org.jacoco.cli/${JACOCO_VERSION}/org.jacoco.cli-${JACOCO_VERSION}-nodeps.jar"
```

> **注意**: `jacocoagent.jar` 必须是 **runtime** 版本（含 agent 入口），`jacococli.jar` 必须是 **nodeps** 版本（含全部依赖可独立运行）。

## 降级机制

若本目录 JAR 缺失，`run_fast_single_cov` 会返回失败，调用方自动降级到 `run_mvn_test_with_jacoco`（通过 `jacoco-maven-plugin` 自动注入 agent，无需本地 JAR）。
