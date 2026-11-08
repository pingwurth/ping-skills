# sensitive-log-review 敏感信息日志审查工具使用说明

面向 Java 项目的敏感信息日志审查流水线，审查变更代码是否存在可能将敏感信息输出到日志的问题，特别针对银行金融业场景。审查依据 **JR/T 0171-2020《个人金融信息保护技术规范》** 的敏感数据分类分级标准（C2/C3 分级）。

---

## 1. 功能介绍和用途说明

### 1.1 功能定位

本工具是一条自动化审查流水线，围绕"敏感数据不落日志"这一核心目标，从五个维度对 Java 代码进行检查，并最终生成 HTML 报告与 CI 门禁判定结果。

典型使用场景：

- **代码审查**：PR/MR 合并前的敏感信息检查
- **安全合规**：银行金融业监管合规性验证
- **开发阶段**：开发过程中实时检查敏感数据泄露风险
- **CI/CD 集成**：自动化流水线中的质量门禁（依赖退出码契约与 `--run-id` 并行隔离）

### 1.2 五大检查维度

| 维度 | 执行脚本 | 说明 |
|------|---------|------|
| 日志输出合规 | `java_log_scanner.py` + `check_log_print.py` | 扫描日志语句中的敏感词与 JSON 序列化输出，再比对变更文件是否命中违规清单 |
| @ToString 注解规范 | `check_tostring_annotation.py` | 确保 POJO 类使用 `@ToString(of = {...})` 显式控制 toString 输出字段 |
| 字段命名规范 | `extract_java_fields.py` | 提取 POJO 字段，基于英文词典做驼峰拆分校验，输出不规范字段与可能敏感字段 |
| 敏感字段分析 | `check_sensitive_word.py` + `analyze_sensitive_fields.py` | 7a 规则初筛（确定性正则规则，可作 CI 门禁）；7b 语义复核由 AI 编排层执行（理解注释语义，补漏判，不计入门禁） |
| POJO 字段注释完整性 | `check_pojo_comments.py` | 基于 Checkstyle 13.7.0 检查变更 POJO 类字段的文档注释完整性 |

### 1.3 执行流程

`main.py` 作为编排层，使用 `ThreadPoolExecutor(max_workers=4)` 将八个步骤组织为**四条并行链路**，汇合后统一生成报告与门禁判定：

```text
                       ┌─ 链路A: 步骤1 扫描日志输出 ──→ 步骤2 变更文件日志检查
                       │
 准备变更清单 ─────────┼─ 链路B: 步骤3 @ToString 注解检查
 (--changed-only 模式) │
                       ├─ 链路C: 步骤4 提取Java字段 ──→ 步骤5 敏感词变更检查
                       │
                       └─ 链路D: 步骤6 POJO注释检查、步骤7 深度敏感性分析
                       ↓
              (四链路汇合)
                       ↓
          步骤8: 生成 HTML 报告 + 门禁判定（--fail-on）
```

流程要点：

- 默认 `--changed-only` 模式：先通过 `git diff` 计算相对目标分支的变更 Java 文件清单（`changed-java.files` / `changed-pojo.files`），各步骤仅扫描变更文件，速度快
- `--full-scan` 显式回退全量扫描（所有 Java 文件，速度较慢）
- 步骤 1/2/4/5/7/8 为关键步骤，失败即退出码 2；步骤 3/6 失败仅警告
- 步骤 7 的 7b（LLM 语义复核）无脚本，由 AI 执行体在 Skill 编排层按 `SKILL.md` 说明执行，结论以 `[LLM]` 前缀追加到 `analyze-sensitize.result`，**不计入门禁统计**

---

## 2. 安装和配置步骤

### 2.1 环境要求

| 依赖 | 版本要求 | 用途 |
|------|---------|------|
| Python | 3.10+ | 运行所有脚本（代码使用 `X \| None` 类型标注语法） |
| Java Runtime | 可运行 jar 即可 | 运行 Checkstyle 13.7.0（POJO 注释检查） |
| Git | 2.0+ | 获取代码变更（所有 git 调用经 `git -C <repo>` 贯通） |
| pytest | 任意较新版本 | 仅开发/运行单测需要，生产运行**无第三方 Python 依赖** |

Windows 下建议设置 `PYTHONIOENCODING=utf-8`，避免控制台中文输出乱码：

```powershell
# PowerShell
$env:PYTHONIOENCODING = "utf-8"
```

```bat
:: CMD
set PYTHONIOENCODING=utf-8
```

```bash
# Linux / macOS
export PYTHONIOENCODING=utf-8
```

编码健壮性说明：脚本读取文件与子进程输出统一采用 `errors='replace'` 解码（原 `errors='ignore'` 已修复），无法解码的字节以占位符显示而不再被静默丢弃，避免漏扫含乱码行的敏感日志。

### 2.2 部署位置

本 skill 部署在项目内的 `.qoder/skills/sensitive-log-review/` 目录：

```text
.qoder/skills/sensitive-log-review/
├── SKILL.md                 # skill 说明（AI 编排层入口）
├── README.md                # 本文档
├── scripts/
│   ├── main.py              # 主编排脚本
│   ├── java_log_scanner.py  # 步骤1 日志扫描
│   ├── check_log_print.py   # 步骤2 变更日志检查
│   ├── check_tostring_annotation.py  # 步骤3
│   ├── extract_java_fields.py        # 步骤4
│   ├── check_sensitive_word.py       # 步骤5
│   ├── check_pojo_comments.py        # 步骤6
│   ├── analyze_sensitive_fields.py   # 步骤7a
│   ├── common/              # 公共模块（paths/text/git_utils/pojo_config/dictionary/java_lexer）
│   ├── dictionary/          # 分层词典文件
│   ├── rules/               # 规则配置
│   └── checkstyle/          # checkstyle-13.7.0-all.jar + pojo-field-doc.xml
└── tests/                   # pytest 单测
```

无需 pip 安装任何第三方包，克隆仓库后即可直接运行。

### 2.3 输出目录优先级

所有审查产物统一写入输出目录，解析优先级（见 `common/paths.py`）：

1. `-o/--output-dir` 命令行参数（最高优先级）
2. 环境变量 `SLR_OUTPUT_DIR`
3. 系统临时目录下的 `sensitive-log-review` 子目录
   - Windows：`%LOCALAPPDATA%\Temp\sensitive-log-review`
   - Linux/macOS：`/tmp/sensitive-log-review`

目录不存在时自动创建（含父目录）。CI 并行场景可用 `--run-id <id>` 在输出目录下追加隔离子目录。

---

## 3. 使用方法和命令示例

### 3.1 基本用法

在 Java 项目根目录（git 仓库根）下执行：

```bash
# 默认变更检查：对比 master 分支，仅扫描变更的 Java 文件
python .qoder/skills/sensitive-log-review/scripts/main.py -r .

# 对比指定分支的变更
python .qoder/skills/sensitive-log-review/scripts/main.py -r . --branch develop

# 全量扫描（所有 Java 文件，速度较慢）
python .qoder/skills/sensitive-log-review/scripts/main.py -r . --full-scan

# 指定输出目录（CI 场景推荐）
python .qoder/skills/sensitive-log-review/scripts/main.py -r . -o ./review-output

# CI 并行隔离：不同流水线用不同 run-id，产物互不覆盖
python .qoder/skills/sensitive-log-review/scripts/main.py -r . -o ./review-output --run-id build-1024

# 自定义门禁：命名不规范也拦截
python .qoder/skills/sensitive-log-review/scripts/main.py -r . --fail-on violation,sensitive,unqualified

# 关闭门禁（仅生成报告，始终返回 0）
python .qoder/skills/sensitive-log-review/scripts/main.py -r . --fail-on ""

# 详细输出模式（打印各子脚本的命令与标准输出/错误）
python .qoder/skills/sensitive-log-review/scripts/main.py -r . -v

# 环境诊断（只检查环境不执行审查，适合首次部署/CI 排障）
python .qoder/skills/sensitive-log-review/scripts/main.py --doctor -r .
```

说明：

- 脚本支持**从任意位置调用**，通过 `-r/--root` 指定项目根目录即可，但该目录**必须是 git 仓库**（内部经 `git rev-parse --git-dir` 校验）
- `-r` 建议总是显式传入（如 `-r .`），否则默认以当前工作目录为项目根，从其他目录调用时会扫错位置导致结果为空

### 3.2 退出码契约

| 退出码 | 含义 | 说明 |
|-------|------|------|
| 0 | 通过 | 审查完成且未触发门禁 |
| 1 | 门禁拦截 | 审查完成，但命中 `--fail-on` 指定类别（默认 `violation,sensitive`） |
| 2 | 执行错误 | 环境/参数/依赖错误（非 git 仓库、非法分支名、非法 run-id、规则文件损坏、关键步骤失败等） |

### 3.3 CI 集成示例

**Jenkins（Declarative Pipeline）：**

```groovy
stage('Sensitive Log Review') {
    steps {
        script {
            def rc = sh(
                script: '''
                    export PYTHONIOENCODING=utf-8
                    python .qoder/skills/sensitive-log-review/scripts/main.py \
                        -r . -b origin/master \
                        -o ./review-output --run-id "${BUILD_TAG}" \
                        --fail-on violation,sensitive
                ''',
                returnStatus: true
            )
            if (rc == 1) { error '敏感信息门禁拦截，请查看 HTML 报告' }
            if (rc == 2) { error '审查执行错误，请检查环境与参数' }
        }
    }
    post {
        always {
            archiveArtifacts artifacts: 'review-output/**', allowEmptyArchive: true
        }
    }
}
```

**GitHub Actions：**

```yaml
jobs:
  sensitive-log-review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0          # 需要完整历史用于 git diff
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - uses: actions/setup-java@v4
        with:
          distribution: temurin
          java-version: '17'
      - name: Run review
        env:
          PYTHONIOENCODING: utf-8
        run: |
          python .qoder/skills/sensitive-log-review/scripts/main.py \
            -r . -b origin/${{ github.base_ref || 'master' }} \
            -o ./review-output --run-id "${{ github.run_id }}"
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: sensitive-log-review-report
          path: review-output/
```

---

## 4. 参数说明和配置文件解释

### 4.1 main.py 参数表

| 参数 | 简写 | 默认值 | 说明 |
|------|------|--------|------|
| `--branch` | `-b` | `master` | 目标分支名称，作为 `git diff` 的对比基准；分支名经安全校验（不允许 `-` 开头、不允许含 `..`） |
| `--root` | `-r` | `.`（当前目录） | 项目根目录 / git 仓库目录，必须是 git 仓库 |
| `--output-dir` | `-o` | 无（回退环境变量或临时目录） | 审查产物输出目录，优先级最高 |
| `--run-id` | 无 | 无 | 并行运行隔离子目录名，仅允许 `[A-Za-z0-9._-]` 且需以字母数字开头，最长 64 字符（防路径穿越） |
| `--fail-on` | 无 | `violation,sensitive` | 门禁计数项，逗号分隔；可选值：`violation` / `sensitive` / `unqualified` / `tostring` / `analyze` / `miss_comments` / `low`；传空串 `""` 关闭门禁 |
| `--changed-only` | 无 | 开启（默认） | 仅扫描相对目标分支变更的 Java 文件 |
| `--full-scan` | 无 | 关闭 | 全量扫描，覆盖 `--changed-only` |
| `--strict-mode` | 无 | 关闭 | 严格模式：存在读取/解析失败文件（`failedFiles > 0`）时以退出码 2 结束；默认仅统计与展示，不影响退出码 |
| `--doctor` | 无 | 关闭 | 环境诊断模式：只逐项检查运行环境（Python/java/git/jar 校验/规则与词典文件/输出目录等）不执行审查；全部通过退出码 0，任一失败退出码 2；与其他执行参数同时传入时 doctor 优先 |
| `--verbose` | `-v` | 关闭 | 详细输出模式，打印各子脚本命令及输出 |

门禁计数项含义：

| 计数项 | 来源步骤 | 含义 |
|--------|---------|------|
| `violation` | 步骤2 | 变更文件命中日志违规清单条数 |
| `sensitive` | 步骤5 | 变更文件中高置信敏感字段数 |
| `unqualified` | 步骤5 | 变更文件中命名不规范字段数 |
| `tostring` | 步骤3 | @ToString 注解缺失类数 |
| `analyze` | 步骤7 | 深度分析命中数（不含 `[LLM]` 行） |
| `miss_comments` | 步骤6 | POJO 字段注释问题数 |
| `low` | 步骤2+5 | 低置信提示总数（`[LOW]` 标记） |

### 4.2 规则配置文件（scripts/rules/）

#### rules/log-scanner.json

日志扫描器（步骤1）的检测配置：

```json
{
  "logger_names": ["log", "logger", "LOG", "LOGGER", "LogHelper", "LogUtil"],
  "detect_system_out": false,
  "detect_print_stack_trace": false
}
```

- `logger_names`：识别哪些变量名视为日志对象（如 `log.info(...)`）
- `detect_system_out`：是否检测 `System.out` 输出（默认关闭，噪音大）
- `detect_print_stack_trace`：是否检测 `printStackTrace()`（默认关闭，噪音大）

#### rules/pojo.config

POJO 文件判定的统一配置（INI 风格两节，由 `common/pojo_config.py` 唯一解析）：

- `[pojo-dir]`：目录名命中清单（如 `dto`、`vo`、`entity`、`model`、`reqvo`、`respvo` 等）
- `[pojo-file]`：文件名后缀命中清单（如 `dto.java`、`vo.java`、`entity.java`、`properties.java` 等，忽略大小写）

**POJO 判定规则** = 文件位于 `src/main/java` 内 **且**（所在目录名命中 `[pojo-dir]` **或** 文件名后缀命中 `[pojo-file]`）。

#### rules/sensitive-field-rules.json

7a 规则初筛（步骤7）使用的结构化敏感字段规则，源自 JR/T 0171-2020。JSON 数组，每条规则包含：

| 字段 | 说明 |
|------|------|
| `category` | 数据类别（如 身份鉴别信息、生物识别信息、金融账户信息） |
| `level` | 敏感级别（C3 / C2 / C3-C2 混合） |
| `patterns` | 正则模式数组，编译时统一加 `re.IGNORECASE`（忽略大小写） |
| `reason` | 命中时输出的原因描述 |

匹配语义：对每个字段按规则顺序匹配，**首次命中即止**（`break`），一个字段最多产生一条命中记录。内置七大类别：身份鉴别信息（C3）、生物识别信息（C3）、金融账户信息（C3/C2）、财产与信用信息（C2/C3）、基本资料（C2/C3）、交易与行为信息（C2）、密钥与证书（C3）。

#### rules/sensitive-data-classification.md

JR/T 0171-2020 完整分类清单，供 **7b LLM 语义复核**参考使用，**不是机器执行规则**（7a 只读 `sensitive-field-rules.json`）。避免在多处复制维护清单导致漂移。

### 4.3 词典文件（scripts/dictionary/）

敏感词判定采用**四层优先级**（由 `common/dictionary.py` 统一加载）：

```text
whitelist（整名豁免） > blacklist（整名必拦） > core（高置信） > extended（低置信）
```

| 文件 | 层级 | 说明 |
|------|------|------|
| `sensitive-words.whitelist` | 第1层 | 整名白名单，命中即豁免敏感判定（忽略大小写整名匹配），如 `cardType`、`logId` |
| `sensitive-words.blacklist` | 第2层 | 整名黑名单，命中即判 SENSITIVE（最高严重级），如 `pwd`、`smsCode`、`idCard` |
| `sensitive-core.txt` | 第3层 | 高置信敏感词（文件共 93 行，有效词条 81 个），字段名驼峰/下划线拆分后单词精确匹配，命中即违规 |
| `sensitive-extended.txt` | 第4层 | 低置信敏感词（文件共 230 行，有效词条 214 个），命中仅标记 `[LOW]`，默认不触发门禁 |
| `en_US-large.txt` | 辅助 | 英文词典（约 16.9 万词），用于字段命名规范检查的驼峰拆分验证 |
| `en_US.whitelist` | 辅助 | 技术缩写豁免（`uid`/`dto`/`vo` 等），命中视为合格命名 |

**词典文件头规范**：每个词典文件头部含元信息注释，其中版本头 `# version: YYYY-MM-DD.N` 在每次修改时须递增（同日多次修改递增 `.N` 序号）。

**词典维护原则**：

1. 新增高置信敏感词（密码、生物识别、卡号类词根）→ `sensitive-core.txt`
2. 新增低置信/场景词 → `sensitive-extended.txt`（命中仅提示，不触发门禁）
3. 确认误报 → `sensitive-words.whitelist`（整名豁免）
4. 确认必拦 → `sensitive-words.blacklist`（整名必拦）
5. 驼峰拆分误报的技术缩写 → `en_US.whitelist`
6. **禁止**向 core 添加 `code`/`name`/`type` 等泛用词（单独出现噪音极大），这类词只能放 extended

### 4.4 产物文件清单

全部位于输出目录：

| 文件 | 产出步骤 | 内容与格式 |
|------|---------|-----------|
| `log-print-ok.list` | 步骤1 | 合规日志，`{绝对路径}#{行号}: {日志内容}` |
| `log-print-violation.list` | 步骤1 | 违规日志，`{绝对路径}#{行号} [TAGS] {日志内容}`，TAGS 如 `JSON_LOG` / `SENSITIVE:cardno` / `LOW:phone` |
| `miss-tostring-annotation.list` | 步骤3 | @ToString 缺失清单，`{绝对路径}#{行号}: {原因}` |
| `all-fields.list` | 步骤4 | 全部字段，`{绝对路径}#{行号}: {字段名}` |
| `unqualified.fields` | 步骤4 | 命名不规范字段（全量审计） |
| `maybe-sensitive.fields` | 步骤4 | 可能敏感字段（全量审计） |
| `sensitive.results` | 步骤5 | 变更文件敏感字段，`[SENSITIVE] {绝对路径}#{行号}: {字段名}` |
| `unqualified.results` | 步骤5 | 变更文件不规范字段，`[UNQUALIFIED] {绝对路径}#{行号}: {字段名}` |
| `pojo-changed.list` | 步骤6 | 变更 POJO 文件绝对路径清单（每行一个路径） |
| `pojo-miss-comments.txt` | 步骤6 | Checkstyle 输出格式的注释问题清单 |
| `analyze-sensitize.result` | 步骤7 | 深度分析结果，`{file_path}#{field_name}: {reason} (line {line_no})`；7b 复核行以 `[LLM]` 开头 |
| `failed-files.list` | 各步骤汇总 | 读取/解析失败的文件清单，`{步骤标识}\t{文件路径}\t{原因摘要}`（不计入 `--fail-on` 门禁口径，可用 `--strict-mode` 拦截） |
| `sensitive-log-review-report.html` | 步骤8 | HTML 汇总报告（长列表折叠、内容 HTML 转义，含失败文件区块） |

---

## 5. 常见问题和解决方案（FAQ）

### 5.1 控制台中文输出乱码

**现象**：Windows 下运行时中文显示为乱码或抛 `UnicodeEncodeError`。

**解决**：设置 `PYTHONIOENCODING=utf-8`（见 2.1 节三种平台的设置方式），CI 中在环境变量里全局配置。

### 5.2 报错"不是 git 仓库"

**现象**：`错误: 不是 git 仓库: <路径>`，退出码 2。

**原因**：`-r/--root` 指向的目录不是 git 仓库根（或未传 `-r`，默认取了当前工作目录）。

**解决**：显式传 `-r <仓库根目录>`，如在仓库根下执行时传 `-r .`。脚本可从任意位置调用，但 `-r` 必须指向包含 `.git` 的仓库目录。

### 5.3 找不到 java 命令

**现象**：步骤6（POJO 注释检查）失败，提示找不到 `java`。

**原因**：Checkstyle 通过 `java -jar checkstyle-13.7.0-all.jar` 运行，依赖 Java Runtime。

**解决**：安装 JRE/JDK 并确保 `java` 在 `PATH` 中。注意步骤6 失败仅产生警告，不会导致整体退出码 2。

### 5.4 变更检查没有任何结果

**现象**：`变更文件: java=0 个`，各清单为空。

**原因**：默认对比基准是 `master` 分支。若当前分支基于其他分支开发，或 CI 浅克隆缺少目标分支引用，`git diff` 结果为空。

**解决**：

- 用 `--branch <目标分支>` 指定正确的对比基准（CI 中常用 `origin/master`、`origin/develop`）
- CI 检出时使用完整历史（如 GitHub Actions 的 `fetch-depth: 0`）
- 确实需要检查全部代码时使用 `--full-scan`

### 5.5 误报处理（正常字段被判为敏感）

**流程**：

1. 确认字段确实非敏感（如 `cardType` 只是卡类型枚举）
2. 将**完整字段名**加入 `scripts/dictionary/sensitive-words.whitelist`（忽略大小写整名匹配）
3. 递增该文件头部 `# version:` 版本号
4. 重新运行验证

若是命名规范检查误报（技术缩写被判不规范），将缩写加入 `en_US.whitelist`。

### 5.6 漏报处理（敏感字段未被识别）

**流程**：

1. 高置信词根（拆分后可精确匹配的单词，如某类卡号词根）→ 追加到 `sensitive-core.txt`
2. 必须拦截的完整字段名 → 追加到 `sensitive-words.blacklist`
3. 泛用但值得提示的词 → `sensitive-extended.txt`（仅 `[LOW]` 提示）
4. 递增对应文件版本头后重新运行验证
5. 结构性规则类漏报（如新增数据类别）→ 修改 `rules/sensitive-field-rules.json` 增加 `patterns`

### 5.7 CI 中门禁失败（退出码 1）如何排查

1. 查看控制台末尾的 `门禁触发: xxx=N` 行，确定命中的类别与数量
2. 打开输出目录中的 HTML 报告或对应产物文件（如 `log-print-violation.list`、`sensitive.results`）定位具体文件与行号
3. 真实问题：修复代码（脱敏、删除日志、加 `@ToString(of = {...})` 等）
4. 确认误报：按 5.5 走白名单豁免流程
5. 如需调整拦截口径：修改 `--fail-on`（如仅拦日志违规用 `--fail-on violation`；临时放行用 `--fail-on ""`，不推荐长期使用）

### 5.8 run-id 非法字符错误

**现象**：`非法 run-id: 'xxx'（仅允许字母数字/._-，且不以符号开头）`，退出码 2。

**原因**：`--run-id` 仅允许 `[A-Za-z0-9._-]`，且首字符必须是字母或数字，最长 64 字符（防路径穿越）。

**解决**：清洗 CI 变量后再传入，如把 `feature/xxx` 中的 `/` 替换为 `-`：

```bash
RUN_ID=$(echo "${BUILD_TAG}" | tr -c 'A-Za-z0-9._-' '-')
```

### 5.9 Windows 路径反斜杠问题

**现象**：传参含反斜杠路径时行为异常，或产物中路径分隔符不一致。

**说明**：脚本内部统一做路径规范化（`common/text.py` 的 `normalize_file_path`），输出目录解析跨平台。建议：

- PowerShell 中路径含空格时加引号：`-r "C:\my project\repo"`
- 也可以直接使用正斜杠：`-r C:/devops/repo`，Python 均可识别
- 避免在参数值结尾放置单个反斜杠加引号（如 `"C:\repo\"`），PowerShell 会将 `\"` 解析为转义引号

### 5.10 jar SHA256 校验告警

**现象**：运行 POJO 注释检查时提示 Checkstyle jar 完整性校验失败。

**原因**：`checkstyle-13.7.0-all.jar` 受 SHA256 基线保护，jar 被替换、损坏或版本升级后未同步更新基线时触发。

**解决**：

- 若非预期改动：从可信源重新获取 jar 文件
- 若是有意升级 Checkstyle 版本：同步更新脚本中登记的 SHA256 基线值，并回归单测（`python -m pytest tests -q`）

### 5.11 如何用 --doctor 排查环境问题

**适用场景**：首次部署、CI 环境变更、或遇到退出码 2 不确定原因时。

```bash
python .qoder/skills/sensitive-log-review/scripts/main.py --doctor -r .
```

诊断项目逐项以 `[OK]` / `[FAIL]` / `[WARN]` 标记输出，覆盖：Python 版本、java/git 可用性、`-r` 目录是否为 git 仓库、checkstyle jar 存在性与 SHA256 校验、4 个规则文件、7 个词典文件、输出目录可写性、`PYTHONIOENCODING` 设置。全部通过退出码 0，任一 `[FAIL]` 退出码 2（`[WARN]` 不计入失败）。按 `[FAIL]` 项的提示逐一修复后重新诊断即可。

另外，常见错误已统一升级为结构化提示（错误码 E001-E007 + 分步修复建议，输出到 stderr），如 `E001` 非 git 仓库、`E004` 规则 JSON 损坏、`E007` jar 校验不符，退出码契约保持不变（仍为 2）。

### 5.12 failedFiles 警告的含义与处理

**现象**：控制台末尾输出 `failedFiles: N`（N > 0），或出现“失败文件数超过已处理文件总数的 10%”醒目告警。

**含义**：部分文件在读取/解析阶段失败（如编码异常、权限不足），这些文件未被检查，结果可能不完整。失败详情见输出目录的 `failed-files.list`（格式 `{步骤标识}\t{文件路径}\t{原因摘要}`）与 HTML 报告的“失败文件”区块。

**处理**：

1. 打开 `failed-files.list` 定位具体文件与原因，修复后重新运行
2. 失败文件默认**不计入** `--fail-on` 门禁统计，不影响退出码
3. CI 中需严格拦截时加 `--strict-mode`：存在失败文件即以退出码 2 结束

---

## 附：快速参考

```bash
# 最常用命令（仓库根目录下）
export PYTHONIOENCODING=utf-8   # Windows PowerShell: $env:PYTHONIOENCODING="utf-8"
python .qoder/skills/sensitive-log-review/scripts/main.py -r . -b master -o ./review-output

# 结果查看
#   门禁与计数     -> 控制台末尾输出
#   完整报告       -> ./review-output/sensitive-log-review-report.html
#   具体违规明细   -> ./review-output/*.list / *.results / *.fields
```
