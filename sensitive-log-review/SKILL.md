---
name: sensitive-log-review
description: 审查变更代码是否存在可能输出敏感信息到日志的问题。扫描Java日志输出、@ToString注解、字段命名和POJO注释，基于银行金融业敏感数据分类标准进行深度分析，生成HTML报告。用于代码审查、安全合规检查、敏感数据防护。
---

# 敏感信息日志审查

审查变更代码是否存在可能输出敏感信息到日志的问题，特别针对银行金融业场景。

## 快速开始

在Java项目根目录下执行：

```bash
# 审查相对于master分支的变更（默认仅扫描变更文件，快速模式）
python scripts/main.py

# 审查相对于指定分支的变更
python scripts/main.py --branch develop

# 全量扫描（所有Java文件，速度较慢）
python scripts/main.py --full-scan

# 指定输出目录（CI场景）
python scripts/main.py -o ./review-output

# 环境诊断（只检查环境不执行审查，适合首次部署/CI 排障）
python scripts/main.py --doctor -r .
```

## 退出码契约

`main.py` 及各子脚本遵循统一退出码约定，CI 可直接依赖：

| 退出码 | 含义 | 说明 |
|-------|------|------|
| 0 | 通过 | 审查完成且未触发门禁 |
| 1 | 门禁拦截 | 审查完成，但命中 `--fail-on` 指定的类别（默认 `violation,sensitive`） |
| 2 | 执行错误 | 环境/参数/依赖错误（非 git 仓库、规则文件损坏、jar 校验失败等） |

门禁类别（与 main.py 的 GATE_KEYS 一致，共 7 项）：`violation`（日志违规）、`sensitive`（敏感字段）、
`unqualified`（命名不规范）、`tostring`（@ToString 缺失）、`analyze`（深度分析命中）、
`miss_comments`（POJO 注释问题）、`low`（低置信提示总数）。可用 `--fail-on violation,sensitive` 自定义，
`--fail-on ""` 关闭门禁（始终返回 0）。

`low` 的来源：步骤2（变更文件日志检查）与步骤5（敏感词检查）中带 `[LOW]` 标签的记录总数，
默认不纳入 `--fail-on`，可显式加入（如 `--fail-on violation,sensitive,low`）。

各子脚本独立执行时：正常完成返回 0，执行错误返回 2；判定类脚本（check_log_print 等）发现违规返回 1。

补充参数：

- `--strict-mode`（默认关闭）：存在读取/解析失败文件（`failedFiles > 0`）时以退出码 2 结束；默认仅统计展示，失败文件不计入 `--fail-on` 门禁口径。失败文件汇总写入 `failed-files.list`（行格式 `{步骤标识}\t{文件路径}\t{原因摘要}`，见下文「输出格式」）
- `--doctor`（默认关闭）：环境诊断模式，只逐项检查运行环境（Python/java/git/jar 校验/规则与词典文件/输出目录等）不执行审查，以 `[OK]`/`[FAIL]`/`[WARN]` 标记输出；全部通过退出码 0，任一失败退出码 2；与其他执行参数同时传入时 doctor 优先

常见错误以结构化提示输出到 stderr（错误码 E001-E007 + 分步修复建议，见 `common/errors.py`），退出码契约不变（仍为 2）。

## 输出目录策略

所有审查产物统一写入输出目录，**跨平台**（不再依赖 `/tmp`）：

1. `-o/--output-dir` 命令行参数（最高优先级）
2. 环境变量 `SLR_OUTPUT_DIR`
3. 系统临时目录 `tempfile.gettempdir()/sensitive-log-review/`
   （Windows 下为 `%LOCALAPPDATA%\Temp\sensitive-log-review`）

目录自动创建。CI 并行场景可用 `--run-id <id>` 追加隔离子目录
（仅允许 `[A-Za-z0-9._-]`，防路径穿越）。

## 审查流程

### 第一阶段：日志输出合规性检查

1. **扫描日志输出** - 检测日志中的敏感词和JSON序列化
   ```bash
   python scripts/java_log_scanner.py <项目根目录> [--files 变更清单] [-o 输出目录]
   ```
   输出：`log-print-ok.list`（合规）、`log-print-violation.list`（违规，带命中原因 tags）
   日志变量名可配置：`rules/log-scanner.json` 的 `logger_names`（默认含 log/logger/LOG/LOGGER 等）

2. **检查变更文件日志** - 验证变更文件是否在日志清单中
   ```bash
   python scripts/check_log_print.py -b <目标分支> --repo <项目根目录> [-o 输出目录]
   ```
   退出码：仅当变更文件命中**违规**清单时返回 1（命中合规清单不拦截）。
   违规 tags 含 `LOW:` 前缀的低置信命中默认不拦截，`--fail-on-low` 可打开。

### 第二阶段：@ToString注解检查

3. **检查@ToString注解** - 确保POJO类正确使用`@ToString(of = {...})`
   ```bash
   python scripts/check_tostring_annotation.py <项目根目录> [--files 变更清单] [-o 输出目录]
   ```
   输出：`miss-tostring-annotation.list`
   仅检查 `class` 类型；`interface`/`enum` 一律跳过；
   `record` 默认跳过，可用 `--record-policy warn` 降级为警告。

### 第三阶段：字段敏感性分析

4. **提取Java字段** - 从POJO类中提取字段信息
   ```bash
   python scripts/extract_java_fields.py <项目根目录> [--files 变更清单] [-o 输出目录]
   ```
   输出：`all-fields.list`、`unqualified.fields`（命名不规范）、`maybe-sensitive.fields`（可能敏感）

5. **检查敏感词** - 验证变更文件中的敏感字段
   ```bash
   python scripts/check_sensitive_word.py -b <目标分支> --repo <项目根目录> [-o 输出目录]
   ```
   输出：`sensitive.results`、`unqualified.results`

### 第四阶段：POJO注释检查

6. **检查POJO注释** - 验证POJO字段文档注释完整性
   ```bash
   python scripts/check_pojo_comments.py -b <目标分支> --repo <项目根目录> [-o 输出目录]
   ```
   输出：`pojo-changed.list`、`pojo-miss-comments.txt`

### 第五阶段：深度敏感性分析（两层互补）

字段敏感性分析分两层：**7a 规则初筛**（确定性，可 CI 门禁）+ **7b LLM 语义复核**（补漏判）。
两层互补不可互替：7a 快且可复现，但只匹配字段名，无法识别拼音命名/无意义命名背后的注释语义；
7b 能理解注释与上下文，但非确定、有成本，不适合作为唯一门禁。

7a. **规则初筛** - 基于结构化正则规则的确定性分析
   ```bash
   python scripts/analyze_sensitive_fields.py [-i pojo-changed.list] [-o 输出目录]
   ```
   - 读取 `pojo-changed.list` 中的变更 POJO 文件
   - 用词法分析器提取字段（支持多行声明/record 组件/枚举常量）
   - 对照 `rules/sensitive-field-rules.json`（源自 JR/T 0171-2020）判定敏感级别
   - 输出：`analyze-sensitize.result`

7b. **LLM 语义复核**（**AI 执行体必做步骤，无脚本**）

   > **⚠️ 本步骤没有 Python 脚本，必须由你（AI）在步骤 7a 执行完毕后立即手动完成。不得跳过。**

   **执行条件**：`pojo-changed.list` 非空时执行；全量扫描（`--full-scan`）文件数过多时仅对 7a 未命中的前 50 个文件抽样。若 `pojo-changed.list` 为空则跳过本步骤。

   **执行步骤**：
   1. 读取输出目录下的 `pojo-changed.list`，获取变更 POJO 文件路径清单
   2. 逐个读取每个 POJO 文件的**全文**（含字段注释）
   3. 对照 `rules/sensitive-data-classification.md` 分类清单，识别 7a 规则初筛的盲区：
      - 拼音命名：`mima`/`kahao` 等，注释表明是密码/卡号
      - 无意义命名：`field1`/`data` 等，注释表明是 CVN/身份证号等
      - 语义缩写：`kh`/`sfz` 等，注释表明是卡号/身份证
   4. 将复核结论**追加**到 `analyze-sensitize.result` 文件末尾，行首标注 `[LLM]`：
      `[LLM] {file}#{field}: {数据子类} - {级别}（依据注释: {注释摘要}）`

   **约束**：7b 结果供人工审查参考，不计入 `--fail-on` 门禁（`main.py` 统计时自动排除 `[LLM]` 前缀行）

### 第六阶段：报告生成

8. **生成HTML报告** - 汇总所有审查结果（`sensitive-log-review-report.html`）
   完整数据列表默认折叠在 `<details>` 区块中；报告中所有动态内容经 HTML 转义。

## 脚本依赖

### 外部依赖

- **Java Runtime** - 用于运行Checkstyle
- **Python 3.10+** - 运行所有脚本（使用 `X | None` 类型标注）
- **Git** - 获取代码变更（所有 git 调用经 `git -C <repo>` 贯通，分支名经注入校验）
- **pytest** - 运行单测（仅开发需要）

### 内部依赖文件

```
scripts/
├── common/                  # 公共模块（所有脚本共享，禁止重复实现）
│   ├── paths.py             # 输出目录策略 + 产物文件名常量
│   ├── text.py              # 字段名拆分 / 清单行解析
│   ├── git_utils.py         # git -C 贯通 / 分支名校验 / 变更行缓存
│   ├── pojo_config.py       # POJO 识别统一配置（pojo.config 唯一解析器）
│   ├── dictionary.py        # 分层词典加载与判定
│   ├── errors.py            # 统一错误码（E001-E007）与修复建议模板
│   ├── failures.py          # 失败文件分片记录与汇总（failed-files.list）
│   └── java_lexer.py        # Java 词法分析器（字段/类/注解提取）
├── dictionary/              # 词典文件（分层）
│   ├── sensitive-core.txt       # 高置信敏感词（命中即违规）
│   ├── sensitive-extended.txt   # 低置信敏感词（[LOW] 标记，默认不拦截）
│   ├── sensitive-words.blacklist  # 整名黑名单（最高优先级）
│   ├── sensitive-words.whitelist  # 整名白名单（豁免）
│   ├── en_US-large.txt          # 英文词典（驼峰拆分验证）
│   └── en_US.whitelist          # 英文缩写白名单（uid/dto/vo 等）
├── rules/                   # 规则配置
│   ├── pojo.config              # POJO目录配置
│   ├── log-scanner.json         # 日志变量名/检测开关配置
│   ├── sensitive-field-rules.json  # 结构化敏感字段规则（7a 规则初筛）
│   └── sensitive-data-classification.md  # JR/T 0171 分类清单（7b LLM 语义复核参考）
├── checkstyle/              # Checkstyle工具
│   ├── checkstyle-13.7.0-all.jar
│   └── pojo-field-doc.xml
└── tests/                   # pytest 单测（conftest.py 自动注入 scripts 到 sys.path）
```

## 词典维护

词典采用**四层优先级**：whitelist（整名豁免）> blacklist（整名命中，最高严重级）>
core（高置信，命中即违规）> extended（低置信，仅 `[LOW]` 标记）。

维护原则：

1. **新增高置信敏感词** → `sensitive-core.txt`（如密码、生物识别、卡号类词根）
2. **新增低置信/场景词** → `sensitive-extended.txt`（命中仅提示，不触发门禁）
3. **确认误报** → `sensitive-words.whitelist`（整名豁免，如 cardType/logId）
4. **确认必拦** → `sensitive-words.blacklist`（整名必拦，如 pwd/smsCode）
5. **驼峰拆分误报** → `en_US.whitelist`（技术缩写，如 dto/vo/grpc）

每个词典文件头部含版本头（`# version: YYYY-MM-DD.N`），修改时递增。
不要向 core 添加泛用词（如 code/name/key 单独出现噪音极大），这类词放 extended。

## 敏感数据分类标准

审查依据 JR/T 0171-2020 分类分级，本文不复制清单（避免多处维护漂移）：

- 机器执行规则（7a 初筛）→ `rules/sensitive-field-rules.json`
- 完整分类清单（7b LLM 复核参考）→ `rules/sensitive-data-classification.md`

## 输出格式

### 中间产物格式（均位于输出目录）

```
log-print-ok.list          # 格式: {绝对路径}#{行号}: {日志内容}
log-print-violation.list   # 格式: {绝对路径}#{行号} [TAGS] {日志内容}
                           #   TAGS 示例: JSON_LOG / SENSITIVE:cardno / LOW:phone
miss-tostring-annotation.list  # 格式: {绝对路径}#{行号}: {原因}
all-fields.list            # 格式: {绝对路径}#{行号}: {字段名}
sensitive.results          # 格式: [SENSITIVE] {绝对路径}#{行号}: {字段名}
unqualified.results        # 格式: [UNQUALIFIED] {绝对路径}#{行号}: {字段名}
pojo-changed.list          # 格式: {绝对路径}
pojo-miss-comments.txt     # Checkstyle输出格式
failed-files.list          # 格式: {步骤标识}\t{文件路径}\t{原因摘要}
                           #   读取/解析失败文件汇总，默认不计入门禁；
                           #   开启 --strict-mode 且存在失败文件时以退出码 2 结束
```

### 最终分析结果格式

```
analyze-sensitize.result   # 格式: {file_path}#{field_name}: {reason} (line {line_no})
```

示例：
```
/path/to/UserDTO.java#password: 身份鉴别信息 - 账户登录密码（C3级别） (line 12)
/path/to/UserDTO.java#cardNo: 金融账户信息 - 银行卡号（C3/C2级别） (line 15)
```

## HTML报告内容

生成的HTML报告包含以下部分：

1. **审查摘要** - 总体审查结果统计
2. **日志合规性** - 日志中的敏感词和JSON序列化问题（含命中原因 tags）
3. **@ToString注解** - 缺失或不规范的@ToString注解
4. **字段敏感性** - 可能包含敏感数据的字段列表
5. **POJO注释** - 缺少文档注释的字段
6. **详细分析** - 基于敏感数据分类的深度分析结果

长列表默认折叠（`<details>`），报告底部标注产物目录路径。

## 开发与测试

```bash
# 运行单测（覆盖 common 包全模块与各检查脚本，用例数以 pytest 实际输出为准）
python -m pytest scripts/../tests -q
# 或在 skill 根目录
python -m pytest tests -q
```

## 使用场景

- **代码审查** - PR/MR合并前的敏感信息检查
- **安全合规** - 银金融业监管要求合规性验证
- **开发阶段** - 开发过程中实时检查敏感数据泄露风险
- **CI/CD集成** - 自动化流水线中的质量门禁（依赖退出码契约 + `--run-id` 并行隔离）

## 注意事项

1. 所有脚本支持从任意目录执行：通过 `--repo` 指定 git 仓库根，产物目录用 `-o` 指定
2. 输出目录跨平台自动解析，Windows 无需 WSL 或手动建目录
3. 敏感字段判定规则在 `rules/sensitive-field-rules.json`，可按项目需求调整
4. Checkstyle检查需要Java Runtime环境；jar 完整性由 SHA256 基线保护
5. 词典定制见上文「词典维护」章节，修改后递增版本头
