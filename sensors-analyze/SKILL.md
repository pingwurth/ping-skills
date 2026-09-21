---
name: sensors-analyze
description: 审查代码库里已有的埋点（analytics tracking / 数据上报）实现。用于：盘点 / 梳理 / 核对 / 反推代码中所有埋点调用——找出每个 sensors.track、trackEvent、gio.track、v-track、reportEvent、useTrack 等调用点，从功能页逐层追到 SDK，确认每条埋点最终上报了哪些字段（page_name、biz_id、first_biz_id/second_biz_id、first_biz_name/second_biz_name、event 等），产出可复核的清单或 CSV。典型场景：埋点治理第一步摸清现状、需求文档丢了从代码反推字段、确认每个页面 page_name/biz_id 填得对不对、神策/GrowingIO/自研封装混用要逐条核对、多端（Vue/React/小程序/Spring）埋点范式不一要系统梳理。只要意图是审查已存在的埋点实现就触发，即使用户没说"分析"二字。不要触发于：从零开发或修改埋点 SDK 本身、给 SDK 新增事件类型、Sentry 错误监控、A/B 测试、IoT 传感器固件、通用代码调用图/架构分析、日志链路梳理、GPS 轨迹追踪、或对已采集 CSV 做汇总统计。
---

# 埋点分析 skill

本 skill 把一个代码库的埋点实现从「整体摸清」推进到「逐条确认真上报了什么」。核心思路：先用 LLM 做静态分析摸清埋点开发范式与调用链路并产出结构化 `sensors.json`，再用确定性的 Python 脚本做"提取入口 → 定位调用点 → 校验"这种机器擅长的活，把"逐条追踪字段取值"这种需要语义理解的活交还给 LLM。这样把人和模型各自擅长的环节分开，每一步都有可复核的中间产物，避免 LLM 一次性分析整个仓库时遗漏或臆造。

## 产物文件（统一放在一个工作目录，默认代码库根或指定目录）

| 文件 | 由谁产生 | 作用 |
|---|---|---|
| `sensors.json` | 第 1 步 LLM | 埋点范式 / 调用链路 / SDK API 总览（必须符合 `sensors.schema.json`） |
| `entry.txt` | 第 2 步脚本生成候选 → 人工复核定稿 | 每行一个正则，供第 3 步定位调用点 |
| `call_sites.json` | 第 3 步脚本 | 所有埋点调用点 `{file:start-end}` + 片段 |
| `sensors.csv` | 第 4 步 LLM 逐条追加 | 最终上报字段记录 |
| `check_report.txt` | 第 5 步脚本 | 一致性与非空校验报告 |
| `review_report.txt` | 第 5.5 步脚本 | 预审报告（结构校验 + 字面量比对） |
| `.agent/sensors-analyze/reports/sensors_*.csv` | 第 6 步脚本归档 | 带时间戳的 CSV 归档版本 |
| `.agent/sensors-analyze/reports/diff_report.txt` | 第 6 步脚本生成 | 版本差异报告（增删改） |

CSV 表头固定为：
```
page_name,first_biz_id,second_biz_id,first_biz_name,second_biz_name,event,file,illegal_reason
```
其中 `file` 格式为 `文件名:start,end`（如 `src/components/Home.vue:42,58`）。`illegal_reason` 由第 5.5 步脚本追加，多个原因用 `; ` 分隔。

## 前置调研（每次分析前简要做一次）

在分析一个陌生代码库前，先快速判断埋点技术栈，这决定了 `sensors.json` 里范式枚举的颗粒度。常见线索：
- `package.json` / `go.mod` / `build.gradle` / `Podfile` / `pubspec.yaml` 里是否出现 `sa-sdk-javascript`、`sensorsdata`、`gio`、` GrowingIO`、`@sentry`、自研 `@/sensors` 包名等。
- 是否存在全局注入的 `sensors`、`track`、`report`、`v-track` 指令、`@Track` 注解、`useTrack` hook。
- 六个目标字段 `page_name / first_biz_id / second_biz_id / first_biz_name / second_biz_name / event` 是**业务自定义事件属性**，不是任何公开 SDK 的内置字段——它们一定是在调用 `track()` 时作为 properties 传入的，所以只能靠追踪调用链路才能确认取值。这点要在分析时牢记。（`event` 字段允许为空。）

主流范式参考（调研结论，不是硬约束）：
- **代码埋点**：开发手动调 `track()` / 自研封装函数。控制精准、可带业务参数，但需发版。
- **全埋点 / 无埋点**：SDK 配置 `quick('autoTrack')` / `autotrack`，自动采集点击/曝光。无法自动带业务字段。
- **可视化埋点 / 圈选**：业务在界面上圈选元素，配置动态下发；代码里通常只有 SDK 初始化和圈选配置加载。
- **声明式埋点**：通过模板指令（`v-track`）、组合式 hook（`useTrack`）、注解 / 装饰器（`@TrackEvent`）把埋点声明挂在元素或方法上，由统一拦截层调用 SDK。

---

## 第 1 步：LLM 静态分析 → 生成 sensors.json

目标：摸清代码库里**每一种埋点开发范式**。同一种 SDK 可能被多种范式使用（比如既有 `v-track` 指令又有直接 `sensors.track` 调用），要分别列为不同 paradigm。

### 必须覆盖的内容（schema 要求）
- `meta`：`project_path`、`language`、`analyzed_at`（ISO 8601）、`sdk_summary`、`global_sdk_apis`（全库去重的 SDK API 汇总）。
- `paradigms[]`，每条：
  - `id`（kebab-case 唯一）、`name`、`category`（`code` / `autotrack` / `visual` / `declarative` / `hybrid`）。
  - `reference_location`：一个代表性的 `file:start_line[-end_line]`，作为该范式的开发参考示例。
  - `call_chain[]`：从**功能页**（最贴近用户交互的一层，order=1）逐层到 **SDK API**，每层含 `layer / symbol / location / description`。
  - `call_chain[].data_flow[]`：显式标注目标字段在这一层如何流转——`action`（`source` 产生 / `transform` 变换 / `pass` 透传 / `merge` 合并 / `drop` 丢弃）、`from`、`to`、`value_hint`。这一栏是第 4 步追字段取值的锚点，务必写清字段从哪个来源（路由 meta、配置常量、父组件 prop、SDK 全局属性等）流到哪个 API 参数。
  - `entry_functions[]`：埋点追踪的入口函数 / 标识。每条含 `name`、`kind`（`wrapper` / `sdk_direct` / `directive` / `decorator` / `hook` / `autoTrack_config`）、`grep_pattern`（**正则，供第 3 步定位调用点**）、`signature`、`location`。
  - `sdk_apis[]`：该范式最终调到的 SDK API，含 `name / signature / purpose / parameters[] / usage_location`。`parameters[].description` 若承载目标字段（page_name/biz_id/event 等）必须显式指出。

### 写 grep_pattern 的要点
正则要匹配该入口在代码里的**实际调用形式**，且误报率低。给几类范例：
- 函数调用：`\btrackEvent\s*\(`
- SDK 直调：`\bsensors\.track\s*\(`
- 模板指令：`v-track(?:-[\w-]+)?\s*=\s*["']`
- 装饰器：`@TrackEvent\b`
- 全埋点配置：`\bquick\(\s*['"]autoTrack['"]`

写完后用 schema 校验再输出。

### 校验
```bash
python3 -c "import json,jsonschema; jsonschema.validate(json.load(open('sensors.json')),json.load(open('sensors.schema.json'))); print('schema OK')"
```
`extract_entries.py` 在加载 `sensors.json` 时也会自动用 `sensors.schema.json` 校验，schema 不符会直接报错中止。建议先单独跑上面这行确认 schema 通过，再进入第 2 步。进入第 2 步前，确认范式枚举完整（同库通常 1~4 种）。

### 完整性自审（必须执行，不可省略）

Step 1 容易漏掉名字带 "init"/"config"/"setup" 的函数（如 `sensorsdataInit`），它们同时承担 SDK 初始化和埋点触发的双重角色。为防止遗漏：

1. **初始化函数必查**：搜索代码库里所有含 `sensors`/`track`/`report`/`init` 的函数定义。逐一判断：若该函数体内存在 `sensors.track`、`sensors.quick('autoTrack')`、或任何数据上报调用，则必须列为 `entry_function`，`kind` 设为 `autoTrack_config` 或 `wrapper`。

2. **SDK API 反向覆盖验证**：列出 Step 1 中识别到的所有 SDK API（`global_sdk_apis` 和 `paradigms[].sdk_apis[]`），反向搜索这些 API 在代码中的所有调用点，检查是否每条调用链路都已被某个 paradigm 的 `entry_function` 覆盖。未被覆盖的调用点 → 补充 `entry_function` 或新建 paradigm。

3. **命名歧义排查**：对每个疑似"初始化"但实际触发埋点的函数，必须在 `entry_function.note` 中说明"该函数同时承担 SDK 初始化和埋点触发，不能仅归类为基础设施"。

进入第 2 步前，确认 `sensors.json` 已通过 schema 校验、且范式枚举完整（同库通常 1~4 种）。

---

## 第 2 步：提取入口函数候选 → 人工复核 → entry.txt

```bash
python3 scripts/extract_entries.py --sensors sensors.json --out entry.txt
```

脚本会从 `paradigms[].entry_functions` 提取每个 `grep_pattern`，连同范式名 / kind / 出处位置写成带注释的 `entry.txt`。**这一步必须人工复核**——LLM 写的正则可能漏配或误配（比如把 `trackEvent` 写成了会命中 `trackEventHandler` 的形式，或漏掉了某个范式）。复核规则：
- 误报：删掉该行，或在行首加 `#` 注释掉。
- 漏报：新增一行正则。**同时检查 `sensors.json` 是否缺少对应的 entry_function，缺少则回 Step 1 补充。**
- 正则需匹配实际调用形式。
- 复核完成后 `entry.txt` 里**非 `#` 开头、非空行**就是第 3 步要用的正则集合。

**确认标记**：复核完成后，在 `entry.txt` 任意位置添加一行：
```
# CONFIRMED
```
这是进入第 3 步的**硬性前置条件**。`locate_call_sites.py` 会检查此标记，未确认则拒绝执行。

复核是质量关键，不要跳过。

---

## 第 3 步：定位所有埋点调用点 → call_sites.json

```bash
python3 scripts/locate_call_sites.py --entry entry.txt --root <代码库根> --sensors sensors.json --out call_sites.json
```

脚本优先用 `ripgrep`（无则回退纯 Python）按 `entry.txt` 的正则搜索代码库，对每个命中点：
- 用括号平衡扫描确定多行调用的结束行，产出 `file:start_line-end_line`。
- 按位置去重，同一位置被多个正则命中会合并到 `patterns` 列表。
- 附带 `snippet` 片段便于第 4 步阅读。
- **自动排除封装实现**：脚本会读 `sensors.json` 里 `kind` 为 `wrapper` / `hook` 的入口，按花括号平衡算出其函数体范围，落在这些函数体内的命中（如 wrapper 内部转发到 `sensors.track` 的那一行）会被排除——它们是封装 plumbing，不是独立业务埋点。若 `sensors.json` 不在同目录，用 `--sensors` 指明路径。

输出 `call_sites.json`（数组，每项有 `id / file / start_line / end_line / location / patterns / snippet`）。

> 若命中数为 0 或异常少：大概率是 `entry.txt` 正则不对，回到第 2 步修正；少数情况是代码用动态调用（`sensors[name]()`），需在 `entry.txt` 补动态调用的正则。

---

## 第 4 步：LLM 逐条追踪 → 追加写 sensors.csv

逐条读取 `call_sites.json`，对每一条调用点：
1. 用 Read 工具读该 `file:start_line-end_line` 的实际代码（必要时扩展读上下文：被调函数的定义、它再调的下一层、传入字段的来源）。
2. 沿 `sensors.json` 里该范式声明的 `call_chain` 与 `data_flow` 追踪，确认最终报到 SDK 的六个字段取值。
3. 判定取值来源类别：
   - 字面量（直接写在调用处）→ 直接填入 CSV。
   - 配置常量 / 路由 meta / 全局注册属性 → 读取常量定义或配置文件，填入具体值。
   - 父组件 prop / 函数参数 / 运行时上下文变量 → **必须反向追踪所有调用者**，确定每个调用者传入的具体值。参见下方「动态引用追踪规则」。
   - 链路中 `drop`（字段未上报）→ CSV 对应格留空，但要在分析说明里写清。
4. 向 `sensors.csv` **追加**一行（或当同一调用点有多组取值时追加多行）。首次写要先写表头。

### 动态引用追踪规则（核心要求）

**绝对禁止修改目标代码库。** 本工具是只读分析，不得在目标代码中添加日志、插桩、临时变量或任何修改。这是最高优先级约束。

**CSV 中每个字段必须是具体值，禁止出现 `<动态:...>`、`<变量:...>` 等占位符。** 但若字段确实无法通过静态分析确定（如来自运行时 API、数据库查询），允许留空——第 5 步会标记为 WARN（警告），不会导致 FAIL。

当某个字段在调用点处是变量（函数参数、prop、上下文属性等），必须执行完整的反向追踪：

1. **定位变量来源**：找到该变量在函数签名中的位置（如 `function trackPage(props)` 中的 `props.page_name`）。
2. **追踪所有调用者**：搜索该函数在整个代码库中的所有调用点（用 grep 搜索函数名），逐一读取调用处传入的值。
3. **确定具体值**：
   - 若调用者传入字面量 → 直接取值。
   - 若调用者传入的仍是变量 → 继续沿调用链反向追踪，直到确定具体值（字面量、常量、配置项、路由 meta 等可静态确定的值）。
   - 若调用者通过对象字面量传入（如 `{ page_name: 'home' }`）→ 取对象字面量中对应字段的值。
   - 若调用者通过展开运算符（如 `{ ...defaultConfig, ...override }`）→ 分别读取两个来源，合并后确定取值。
4. **一调用点一记录**：如果同一个 call_site 被多个上游调用者调用，且传入的字段值不同，则该 call_site 对应**多条 CSV 记录**，每条记录对应一组具体的字段值。`file` 列保持相同（都指向同一个 call_site 位置）。
5. **多值汇聚场景**：若一个函数被 N 个页面调用，每个页面传不同的 `page_name`，则为该函数内的调用点生成 N 条 CSV 记录。
6. **完全无法确定的值**：当字段值来自外部输入（如用户输入、远程 API 返回、数据库查询）或调用链过深无法静态追踪时：
   - 该字段**留空**（不填任何值，不填占位符）
   - 在分析说明中记录该字段无法静态解析的原因和已追踪到的调用链路
   - 第 5 步校验会将此类字段标记为 WARN（警告），不会导致 FAIL
   - **绝对不要因此修改目标代码库**（如添加日志或插桩），这是只读分析工具

### CSV 行格式（列顺序固定）
```
page_name,first_biz_id,second_biz_id,first_biz_name,second_biz_name,event,file
```
第 4 步写入时不含 `illegal_reason` 列（由第 5.5 步脚本自动追加）。

- `file` 用 `相对路径:start,end` 格式（如 `src/Home.vue:42,58`），start / end 与 `call_sites.json` 一致。
- `page_name / first_biz_id / second_biz_id / first_biz_name / second_biz_name` 五项第 5 步会校验非空且不允许包含 `<动态` 等占位符。追踪不到确值时留空，留空会在第 5 步产生 WARN（警告），不会导致 FAIL。但应尽最大努力追踪，留空是最后手段。`event` 字段允许为空。
- 字段值含逗号时用双引号包裹；写入时用 `csv` 模块或保证转义正确。
- 注意：因为同一 call_site 可能对应多条 CSV 记录，所以 CSV 行数可能 ≥ call_sites 数量。第 5 步会校验 CSV 行数 ≥ call_sites 数量（而非严格相等）。

逐条处理建议用 TodoWrite 跟踪进度，避免漏条。处理完全部调用点后进入第 5 步。

---

## 第 5 步：一致性校验 → check_report.txt

```bash
python3 scripts/check_report.py --sites call_sites.json --csv sensors.csv --out check_report.txt
```

校验分两级：
- **ERROR（结构性错误 → FAIL）**：
  1. 记录数充足：`CSV 数据行数 >= len(call_sites)`（同一调用点因多组取值可产生多条记录）。CSV 行数 < call_sites 数量说明第 4 步漏写了调用点。
  2. CSV 每行的 `file`（格式 `文件名:start,end`）能在 `call_sites.json` 中找到对应。
  3. **禁止动态占位符**：所有字段值不得包含 `<动态`、`<变量`、`<unknown`、`<dynamic` 等占位标记。发现则判 FAIL。
- **WARNING（追踪未完成 → WARN）**：
  4. `page_name / first_biz_id / second_biz_id / first_biz_name / second_biz_name` 五字段为空。第 5 步会自动输出 `resolution_report.csv` 列出所有空字段及其位置，供人工复核。

报告末尾输出 `PASS`、`WARN` 或 `FAIL`。FAIL 时按提示修正（补写漏掉的 CSV 行、追踪动态引用为具体值、回退第 1/2 步修正范式与正则）后重跑第 5 步。WARN 时查看 `resolution_report.csv`，对未解析字段进行人工补全或确认后重跑。

---

## 第 5.5 步：脚本预审 → 追加 illegal_reason 列

```bash
python3 scripts/review_report.py --csv sensors.csv --sites call_sites.json --root <代码库根> --out review_report.txt
```

脚本读取 `sensors.csv`，在每行末尾追加 `illegal_reason` 列，并执行以下预审检查：

### 结构校验（纯脚本）
1. **必填字段空值**：`page_name / first_biz_id / second_biz_id / first_biz_name / second_biz_name` 为空 → 记录 `"{field}为空"`
2. **文件存在性**：`file` 字段指向的文件不存在 → 记录 `"文件{path}不存在"`
3. **行号有效性**：行号超出文件范围 → 记录 `"行号范围{start}-{end}无效"`
4. **调用点对应**：CSV 行的 `file` 在 `call_sites.json` 中无对应 → 记录 `"在call_sites.json中无对应调用点"`

### 代码级字面量比对
5. **字段值无字面量匹配**：读取 `file` 指向的代码片段，提取字符串字面量；若 CSV 已填值在代码中完全找不到匹配 → 记录 `"{field}='{val}'在代码中无字面量匹配"`
6. **可从字面量推断**：必填字段为空但代码中有明确字面量 → 记录 `"{field}可从代码字面量'{literal}'推断"`

### 输出
- 覆写 `sensors.csv`（每行追加 `illegal_reason` 列，多个原因用 `; ` 分隔）
- 输出 `review_report.txt`（预审摘要：逐行列出问题，附统计）

退出码：
- 0 = 无问题（所有 `illegal_reason` 为空）
- 1 = 执行异常
- 2 = 有问题需 LLM 复核（有待标记的行）

脚本仅做标记，**不做字段值修改**。标记结果供第 5.6 步 LLM 复核使用。

---

## 第 5.6 步：LLM 最终复核

对 `sensors.csv` 中 `illegal_reason` 非空的行，逐条执行深入分析：

1. **读取代码**：用 Read 工具读 `file` 字段指向的代码，扩展上下文（被调函数定义、调用者、上游传参）。
2. **调用链追踪**：沿 `sensors.json` 里该范式的 `call_chain` 与 `data_flow` 追踪字段来源，结合第 5.5 步脚本标记的线索（如"可从字面量推断"）。
3. **验证与更正**：
   - 确认或更正 `page_name`、`first_biz_id`、`second_biz_id`、`first_biz_name`、`second_biz_name`、`event` 的值。
   - 若脚本标记"字段值在代码中无字面量匹配"，检查是否因字段值来自配置常量/路由 meta/全局属性（而非直接字面量），确认后保留正确值。
   - 若脚本标记"可从代码字面量推断"，读取代码确认具体值后填入。
   - 若确实无法解析（运行时 API、数据库查询、用户输入），保留空值，在 `illegal_reason` 中写明最终原因（如 `"page_name来自路由meta，运行时动态获取"`）。
4. **清空已解决的原因**：字段经复核确认正确后，清除 `illegal_reason` 中对应的原因项。若所有原因均已解决，清空 `illegal_reason` 为空字符串。
5. **覆写 `sensors.csv`**。

**注意**：第 5.6 步是 LLM 操作，不需要运行额外脚本。LLM 直接读取 CSV 和代码，修正后覆写 CSV。

---

## 第 6 步：报告归档与版本比对

```bash
python3 scripts/report_manager.py --csv sensors.csv
```

脚本执行以下操作：
1. **归档**：将 `sensors.csv` 复制到 `.agent/sensors-analyze/reports/sensors_YYYYMMDD_HHmmss.csv`（带时间戳）
2. **比对**：自动查找上一次归档的 CSV，逐行比对差异
3. **输出差异报告**：写入 `.agent/sensors-analyze/reports/diff_report.txt`

差异类型：
- **新增**：本次有但上次没有的行
- **删除**：上次有但本次没有的行
- **修改**：两边都有但字段值不同的行

退出码：
- 0 = 无差异或首次归档
- 1 = 执行异常
- 2 = 存在差异需人工复核

可选参数：
- `--reports-dir <path>`：自定义报告目录（默认 `.agent/sensors-analyze/reports/`）
- `--diff-only`：仅比对，不执行归档
- `--out <path>`：自定义差异报告输出路径

**人工复核**：当脚本报告差异时，查看 `diff_report.txt` 确认：
- 新增行是否是本次分析新增的调用点
- 删除行是否是误删或代码已移除
- 修改行是否是字段值追踪更准确了

---

## 关键注意事项

- **禁止动态占位符，允许合理留空**：CSV 中不得出现 `<动态:来源>`、`<变量:xxx>`、`<unknown>` 等占位标记。当字段值是变量时，必须尽最大努力沿调用链反向追踪到所有调用者，确定每个调用者传入的具体值，一个调用者对应一条记录。若确实无法静态解析（如值来自运行时 API、数据库查询、用户输入），该字段留空，第 5 步会产生 WARN 警告。**绝对不要为了解析字段值而修改目标代码库。** 这是本工具的核心质量要求。
- **一调用点多记录**：同一个 call_site 被多个上游以不同值调用时，CSV 中会产生多条记录（同一 `file` 列，不同的字段值）。第 5 步校验规则是 CSV 行数 ≥ call_sites 数量，而非严格相等。
- **范式颗粒度**：同库同一 SDK 若被两种以上方式调用（指令 + 直调），要拆成多个 paradigm，否则第 3 步正则会漏。
- **全埋点 / 可视化埋点**：调用点不在业务代码里，而在 SDK 初始化与配置处；`entry_functions` 用 `autoTrack_config` kind，`grep_pattern` 匹配初始化配置项即可。
- **data_flow 要诚实**：写得出就写，写不出就留空数组，不要臆造来源——第 4 步会依赖它，臆造会导致字段追错。
- **人工复核不可省**：第 2 步是唯一的人工关卡，正则错会污染第 3 步全部结果。
- **可重入**：第 4 步追加写 CSV 前，若 `sensors.csv` 已存在且有旧数据，应先清空或备份，避免与本次调用点对不齐导致第 5 步 FAIL。
- **第 5.5/5.6 步复核流程**：第 5 步通过后，第 5.5 步脚本自动添加 `illegal_reason` 列并做结构校验和字面量比对。第 5.6 步 LLM 对 `illegal_reason` 非空的行做深入代码分析，验证/更正字段值，清空已解决的原因。两步配合实现"脚本能做的脚本做，脚本做不了的 LLM 做"的分工。
