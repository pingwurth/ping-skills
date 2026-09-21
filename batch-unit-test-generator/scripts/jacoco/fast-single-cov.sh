#!/bin/bash
set -euo pipefail

# ==============================================================================
# 用法: ./fast-single-cov.sh [选项] <模块名> <目标类全限定名> <测试类全限定名>
#
# 选项:
#   --project-root <path>  指定项目根目录(默认自动向上查找 pom.xml)
#   --no-am                跳过依赖模块编译
#
# 注: 所有日志由 Python 调用方通过 stdout 管道捕获到 log_file。
#
# 示例:
#   ./fast-single-cov.sh --project-root /path/to/project user-service com.example.Foo com.example.FooTest
#   ./fast-single-cov.sh user-service com.example.Foo com.example.FooTest
# ==============================================================================

# 脚本所在目录
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" &>/dev/null && pwd)

# 临时文件管理：在脚本退出时（包括错误退出）自动清理
MVN_LOG=""
cleanup() {
    [ -n "$MVN_LOG" ] && rm -f "$MVN_LOG"
}
trap cleanup EXIT

# ---- 正式解析命名参数 ----
NO_AM=false
EXPLICIT_PROJECT_ROOT=""
POSITIONAL=()

while [ $# -gt 0 ]; do
    case "$1" in
        --project-root)
            EXPLICIT_PROJECT_ROOT="$2"
            shift 2
            ;;
        --no-am)
            NO_AM=true
            shift
            ;;
        *)
            POSITIONAL+=("$1")
            shift
            ;;
    esac
done

# 还原位置参数
set -- "${POSITIONAL[@]}"

# 模块名、目标类、测试类
MODULE_DIR=$1
TARGET_CLASS=$2
TARGET_TEST=$3

# 确定项目根目录: 优先使用显式指定, 否则自动查找
if [ -n "$EXPLICIT_PROJECT_ROOT" ]; then
    if [ ! -d "$EXPLICIT_PROJECT_ROOT" ]; then
        echo "❌ 错误: 指定的项目根目录不存在: $EXPLICIT_PROJECT_ROOT"
        exit 1
    fi
    if [ ! -f "$EXPLICIT_PROJECT_ROOT/pom.xml" ]; then
        echo "❌ 错误: 指定的项目根目录下没有 pom.xml: $EXPLICIT_PROJECT_ROOT"
        exit 1
    fi
    PROJECT_ROOT="$EXPLICIT_PROJECT_ROOT"
else
    CURRENT_DIR=$(pwd)
    PROJECT_ROOT="$CURRENT_DIR"
    while [ "$PROJECT_ROOT" != "/" ] && [ ! -f "$PROJECT_ROOT/pom.xml" ]; do
        PROJECT_ROOT=$(dirname "$PROJECT_ROOT")
    done
    if [ ! -f "$PROJECT_ROOT/pom.xml" ]; then
        echo "❌ 错误: 找不到项目根目录（未找到 pom.xml）"
        exit 1
    fi
fi
cd "$PROJECT_ROOT" || exit 1
echo "📁 项目根目录: $PROJECT_ROOT"

# 1. 参数校验
if [ -z "$MODULE_DIR" ] || [ -z "$TARGET_CLASS" ] || [ -z "$TARGET_TEST" ]; then
    echo "❌ 错误: 缺少参数！"
    echo "用法: $0 <模块名> <目标类> <测试类> [--no-am]"
    exit 1
fi

ABS_MODULE_DIR="$PROJECT_ROOT/$MODULE_DIR"
if [ ! -d "$ABS_MODULE_DIR" ]; then
    echo "❌ 错误: 模块目录 '$MODULE_DIR' 不存在！"
    exit 1
fi

# 2. 依赖包和生成报告的绝对路径
JACOCO_AGENT="$SCRIPT_DIR/lib/jacocoagent.jar"
JACOCO_CLI="$SCRIPT_DIR/lib/jacococli.jar"
EXEC_FILE="$ABS_MODULE_DIR/target/jacoco.exec"
REPORT_DIR="$ABS_MODULE_DIR/target/site/jacoco"
XML_FILE="$REPORT_DIR/jacoco.xml"
CSV_FILE="$REPORT_DIR/jacoco.csv"
JSON_FILE="$ABS_MODULE_DIR/target/state.json"

# 检查工具是否存在
if [ ! -f "$JACOCO_AGENT" ] || [ ! -f "$JACOCO_CLI" ]; then
    echo "❌ 错误: 找不到 jacocoagent.jar 和 jacococli.jar"
    exit 1
fi

# 清理旧的 exec 文件，防止干扰
rm -f "$EXEC_FILE"

echo "========================================================"
echo "🚀 目标模块: $MODULE_DIR (绝对路径: $ABS_MODULE_DIR)"
echo "🎯 目标类:   $TARGET_CLASS"
echo "🧪 测试类:   $TARGET_TEST"
echo "📂 Exec预期: $EXEC_FILE"
echo "========================================================"

# 3. 编译模块及其依赖
echo "🔨 [1/4] 编译模块及依赖..."
AM_FLAG="-am"
if [ "$NO_AM" = true ]; then
    AM_FLAG=""
    echo "ℹ️  --no-am 模式: 跳过依赖模块编译"
fi
mvn test-compile -pl "$MODULE_DIR" $AM_FLAG -q
if [ $? -ne 0 ]; then echo "❌ 编译失败！"; exit 1; fi

# 4. 运行测试并收集覆盖率数据
echo "🏃 [2/4] 运行测试并收集数据..."
# 🌟 核心修复：使用绝对路径的 agent，并强制 forkCount=1 确保 JVM 启动
ARG_LINE="-javaagent:$JACOCO_AGENT=destfile=$EXEC_FILE,includes=$TARGET_CLASS"

# 先写临时文件(避免管道缓冲问题), 完成后再输出到 stdout 供 Python run_command 捕获
MVN_LOG=$(mktemp)
set +e
mvn test-compile surefire:test \
  -pl "$MODULE_DIR" \
  -Dtest="$TARGET_TEST" \
  -DargLine="$ARG_LINE" \
  -DforkCount=1 \
  --batch-mode > "$MVN_LOG" 2>&1
MVN_RC=$?
set -e

# 将 Maven 完整输出送到 stdout, 确保 Python 侧 log_file 能捕获
cat "$MVN_LOG"

# 检查 mvn 退出码
if [ $MVN_RC -ne 0 ]; then
    echo "❌ mvn 执行失败(退出码 $MVN_RC)。"
    rm -f "$MVN_LOG"
    exit 1
fi

# 检查是否提示 "No tests were executed"
if grep -q "No tests were executed" "$MVN_LOG"; then
    echo "❌ 致命错误: Surefire 没有找到或执行任何测试！"
    echo "💡 请检查测试类名 '$TARGET_TEST' 是否正确，或者该类中是否有 @Test 方法。"
    rm -f "$MVN_LOG"
    exit 1
fi

# 检查 exec 文件是否生成
if [ ! -f "$EXEC_FILE" ]; then
    echo "❌ 致命错误: 测试跑完了，但依然未找到覆盖率数据文件 $EXEC_FILE"
    echo "💡 可能的原因："
    echo "   1. 你的 pom.xml 中 maven-surefire-plugin 写死了 <argLine>，导致命令行的 -DargLine 被忽略。"
    echo "   2. 测试代码中调用了 System.exit() 导致 JVM 异常退出，JaCoCo 没来得及写文件。"
    echo "---------------- Maven 日志最后 20 行 ----------------"
    tail -n 20 "$MVN_LOG"
    rm -f "$MVN_LOG"
    exit 1
fi
rm -f "$MVN_LOG"

# 5. 生成 HTML、XML 和 CSV 报告
echo "📊 [3/4] 生成 HTML、XML 和 CSV 报告..."
mkdir -p "$REPORT_DIR"
java -jar "$JACOCO_CLI" report "$EXEC_FILE" \
  --classfiles "$ABS_MODULE_DIR/target/classes" \
  --sourcefiles "$ABS_MODULE_DIR/src/main/java" \
  --html "$REPORT_DIR" \
  --xml "$XML_FILE" \
  --csv "$CSV_FILE"

if [ $? -ne 0 ]; then
    echo "❌ 报告生成失败！"
    exit 1
fi

echo "========================================================"
echo "✅ 全部完成！"
echo "📂 HTML 报告: $REPORT_DIR/index.html"
echo "📂 XML  报告: $XML_FILE"
echo "📂 CSV  报告: $CSV_FILE"
echo "========================================================"
