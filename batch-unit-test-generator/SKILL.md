---
name: batch-unit-test-generator
description: 批量为分支差异涉及的多个 Java 类生成 JUnit5+Mockito 单元测试。当用户要求批量补单测、为分支差异类批量写单测或提升多个类覆盖率时使用。一次 install + 一次全量 mvn 产出基线，逐类委派子代理迭代，最后批量终验。
tools: Read, Write, Edit, Glob, Grep, Bash
---

# 批量 Java 单元测试生成

针对分支差异涉及的**多个 Java 类**，以 JaCoCo Line Coverage 为主目标，批量生成 JUnit5+Mockito 单元测试，直到达到门槛。

核心原则（标准模式）：

- 工作树选择仅一次，整个批次共享同一工作树。
- mvn install 仅主流程一次，全量 mvn 测试仅基线和终验各一次。
- 类间严格串行，batch_next 原子认领保证无重复委派。
- 子代理负责单类迭代循环（make_plan → build_prompt → write_code → validate_rules → verify_coverage）。
- 预算耗尽/不收敛自动跳过当前方法并记录原因（不再询问用户），类级总预算默认 30 轮。
- 大类自动拆分方法组（pending ≥ 30 方法或 diff ≥ 300 行），每组 ≤ 10 方法独立委派，探索模式预算翻倍。
- 所有流程由 NEXT_STEP 协议驱动，支持双层断点续跑。

---

**第一步：先让用户选择模式（§0），选定后立刻复制对应 todo 清单，再按清单推进。**

## 0. 模式选择（加载后第一件事）

**先问模式，再动手**：用户答复前，不得执行任何脚本、不得读写任何测试文件。直接向用户二选一提问并等待答复 —— 目标范围未在请求中给出时一并问清。用户在请求里已指明模式的，按其指定执行，不再重复提问。

| 模式 | 适用 | 执行方式 | 不保证 |
|---|---|---|---|
| **快速模式** | 一批简单类，铺一遍即可，不做覆盖率把关 | 保留 `select_worktree` + `batch_diff`（拿候选类清单 + 用户确认范围/门槛），**跳过 `batch_init` / `batch_next` / `batch_update` / `batch_finish`**；**不使用子代理**，主流程自己逐类串行按 §0.1 的单类五步编写；**不跑任何 mvn** | 覆盖率达标、编译与测试通过 |
| **标准模式** | 需要可靠覆盖率与批量终验 | 复用 §1 工作流：脚本 + 子代理委派 + mvn 基线与终验 + 循环自愈，直到覆盖率达标 | — |

用户选定后**立刻**把对应清单**原样复制**为本次任务的 todo 清单（宿主的 todo/任务工具可用就建到工具里，没有就在对话中维护该 Markdown 清单），随后逐项勾选推进：不跳项、不改顺序、不加项。

### 0.1 快速模式 todo 清单

```markdown
- [ ] 1. 确认 git 工作树：select_worktree.py，拿到 <worktree>
- [ ] 2. 差异分析与范围确认：batch_diff.py --project-root <worktree> → 转述候选类清单，等用户确认范围（--all / --top N / --classes <FQCN,...>；快速模式不看门槛，无覆盖率目标）
- [ ] 3. 逐类串行处理，一次只处理一个类（不使用子代理），对每个类重复第 4~8 项
- [ ] 4. 探索该类/方法的生产源码：确认 FQCN 与源文件、列出全部方法、识别依赖与分支
- [ ] 5. 探索对应测试类的源码：定位测试文件、已有用例、可复用的 mock/基类/工具方法
- [ ] 6. 分析哪些方法需要补单元测试，把计划清单写到 /tmp/<skill_name>/<时间戳>/<简单类名>/plan.md
- [ ] 7. 从 plan.md 取第一个「待补」方法，依据第 4、5 项的探索结果编写该方法的单元测试，每次只专注一个方法
- [ ] 8. 在 plan.md 勾掉/标注该方法状态，回到第 7 项逐个方法增量推进，直到该类所有方法处理完毕；再回到第 3 项处理下一个类
- [ ] 9. 全部类处理完后汇报：各类已补测方法、跳过的方法/类与原因，并声明「快速模式未执行 mvn 验证，覆盖率与编译/测试结果未验证」
```

- 计划清单路径：`/tmp/<skill_name>/<时间戳>/<简单类名>/plan.md`，其中 `<skill_name>` = `batch-unit-test-generator`，`<时间戳>` = 本次批量运行开始时间（`YYYYMMDD-HHMMSS`，同一次运行的所有类共用同一时间戳目录）；目录不存在则创建。每个类的清单按方法一行，含方法名、补测理由、优先级、状态（待补 / 已完成 / 跳过）：

```markdown
# FooService 补测计划
- [ ] `process(String)` — 待补 — 无现有用例，含 3 个分支
- [x] `getOrder(Long)` — 已完成 — 覆盖存在 / 不存在两条路径
- [ ] `init()` — 跳过 — 依赖静态初始化块，不改生产代码无法测
```

- 快速模式仍受 §4 全局约束与 `references/UnitTestRules.md` 约束，且**无脚本兜底**，必须自行自查：只允许写 `src/test/java/**/<TargetTest>.java`；禁止改 `src/main/java/**`、`pom.xml`、配置；禁止 `try-catch`（异常路径用 `assertThrows`）；每个用例以断言结尾；禁止 `@Disabled`、删除用例、同义反复断言等消红手段。
- 某方法无法在不改生产代码的前提下测试时，在 `plan.md` 标注 `跳过` + 原因，继续下一个方法；整个类都不宜快速处理时标注该类 `跳过` + 原因，继续下一个类，不阻塞整批；**不得改生产代码使其可测**。
- 快速模式不跑 `batch_init`，因此**没有基线覆盖率**：类范围一律以 `batch_diff` 候选清单 + 用户确认结果为准，不得用覆盖率数字筛选类，也不得宣称任何覆盖率增量。
- 快速模式不产出 `state.json` / `batch_state.json`，无类内迭代、自动跳过与批量终验，做完即结束：不进入 §3.2 预算与跳过、§5 子代理委派、§6 断点续跑、§7 批量终验与最终报告（§3.1 范围确认门禁仍需走）。

### 0.2 标准模式 todo 清单

```markdown
- [ ] 1. 确认 git 工作树：select_worktree.py，拿到 <worktree>
- [ ] 2. 差异分析：batch_diff.py --project-root <worktree>
- [ ] 3. 范围与门槛确认门禁：转述候选类清单，等用户确认范围（--all / --top N / --classes）与门槛
- [ ] 4. 批量基线：batch_init.py --project-root <worktree> --classes <已确认范围> --threshold <门槛>
- [ ] 5. 循环认领与委派：batch_next.py 认领 pending 类 → 委派子代理 batch-class-writer 跑类内迭代 → 子代理交付报告 → batch_update.py 落账 → 尚有剩余类则回到本项
- [ ] 6. 收到 ask_user 时逐字转述 question 与 resume，等用户答复后逐字执行对应命令(预算耗尽已改为自动跳过, 此门禁仅用于范围确认与异常中断)
- [ ] 7. 全部类 done/skipped/failed/unmet/unverified 后：batch_finish.py --project-root <worktree> 全量终验（出现 recheck 类则回到第 5 项重新委派；unmet 为未达标终态，不再重验；unverified 为环境不可信待复核，非终态——环境修复后重跑本步即可重新终验）
- [ ] 8. 逐字转述 finish 最终报告（§7）；收尾清理由用户手动执行
```

- `<workdir>` 默认 `<worktree>/.agent/batch-unit-test-generator/`，各类独立子目录 `classes/<简单类名>/`（§4）。
- 跑 mvn 的脚本（`batch_init` / `batch_finish`）按 §4 用 `run_in_background: true` 执行，等待脚本完成通知，禁止手动轮询或杀进程。
- todo 只是进度视图：每一步执行什么、下一条命令是什么，一律以 `NEXT_STEP` 协议块为准（§2），不得用 todo 覆盖协议路由。

## 1. Workflow（标准模式）

```text
select_worktree (拷贝复用, 整个批次仅一次)
      ↓
batch_diff (差异分析+过滤 → 候选类清单 → ask_user 确认门禁)
      ↓  用户确认范围(--all/--top N/--classes)与门槛
batch_init (install 一次 + 全量 mvn 一次 → 各类基线 state.json + batch_state.json)
      ↓
batch_next (原子认领下一 pending 类 → 输出子代理委派要件)
      ↓
子代理 batch-class-writer: make_plan → build_prompt → write_code
      → validate_rules → verify_coverage 迭代循环
      (batch_mode: 队列空→类内finish; 预算耗尽→自动跳过并记录 skip_reason)
      ↓  子代理输出交付报告
batch_update (读类 state.json 落账: done/skipped/failed + 覆盖率 before→after)
      ↓  有剩余 → batch_next 循环; 无剩余 → batch_finish
batch_finish (一次全量 mvn → 批量终验 → 有补测空间的不达标类重入队 recheck → 其余不达标类终止为 unmet → 环境不可信类标 unverified 待复核 → 最终报告 finish)
      ↓  recheck 类 → batch_next 重新委派; unmet 类 → 未达标终态(不再重验); unverified 类 → 非终态, 环境修复后重跑 batch_finish 复核
```

### 脚本调用

```text
# 第一步(标准模式): 工作树确认
python scripts/select_worktree.py <当前工作目录> [--list | --choice N | --new [名称] | --clear-history] [--base REF] [--force]
# resume 的 params 以 <当前工作目录> 开头可逐字执行; --clear-history 先预检(base ref/分支检出/目标路径,
# 失败则历史保留), 清理会永久删除历史树未提交内容(分支保留); 已有分支 + --base 会强制重置该分支
# 拿到 worktree 后自动复制主工程 .codegraph 并执行 codegraph sync(失败 abort); 拿到 <worktree> 后:

# 第二步: 差异分析
python scripts/batch_diff.py --project-root <worktree> [--target <分支>] [--diff-mode auto|three|two] [--exclude-glob PATTERN ...] [--sort-policy module-coverage|coverage|diff-size] [--workdir <dir>]

# 第三步: 批量基线(用户确认范围与门槛后)
python scripts/batch_init.py --project-root <worktree> --classes all|top:N|FQCN,... --threshold 80 [--target <分支>] [--coverage-exclude PATTERN ...] [--jacoco-version V] [--workdir <dir>]

# 第四步: 认领委派(循环)
python scripts/batch_next.py --project-root <worktree> [--reset-claim <FQCN>] [--workdir <dir>]
# → 子代理按委派要件执行类内迭代 → 子代理输出交付报告

# 第五步: 落账(每类子代理返回后)
python scripts/batch_update.py --project-root <worktree> [--fqcn <FQCN>] [--skip-class <FQCN> --reason <text>] [--mark-failed <FQCN> --reason <text>] [--workdir <dir>]

# 第六步: 批量终验(所有类 done/skipped 后)
python scripts/batch_finish.py --project-root <worktree> [--skip-mvn] [--workdir <dir>]
```

> **注**：所有脚本均支持 `--workdir` 参数用于指定工作目录（默认为 `<project-root>/.agent/batch-unit-test-generator/`）。详细参数说明请运行 `python scripts/<脚本名>.py --help`。

---

## 2. NEXT_STEP 协议

脚本与调用方之间的唯一契约。Schema 见 `protocol/next-step.schema.json`。

- 协议块以 `:::NEXT_STEP_BEGIN:::` / `:::NEXT_STEP_END:::` 标记包裹的 JSON 输出。
- `exit_code` 契约：0 = 正常完成；1 = 需继续处理（未达标等）；2 = 执行错误；3 = 状态/协议错误。
- `next_step.type`：`run_script` / `write_code` / `ask_user` / `finish` / `abort`。
- `next_step.type == "abort"`：将 `message` **逐字**转述给用户后**立即终止本技能**——禁止执行任何后续脚本、禁止重试、禁止代为执行 message 中的修复命令；修复命令**由用户手动执行**，执行完毕后**由用户重新调用本技能**。兼容兜底（**仅限旧版协议块形态**）：`exit_code == 2` 且 `resume` **存在且恰好只含一个 `terminate` 选项**（数组长度 1）时，转述 question 后直接终止，不得重试。**禁止扩大解释**：`exit_code == 2` 且 `type == "ask_user"`、`resume` 为空数组或**缺失**，是**正常提问路径**——转述 question 并等待用户答复后继续，**不得终止、不得重试**。新版协议中，直接终止**只认** `next_step.type == "abort"`。

---

## 3. 人工决策门禁

### 3.1 确认门禁（batch_diff 输出 ask_user）

差异分析完成后，呈现候选类清单，**用户必须确认范围与门槛**后才进入批量基线：

- **全部**：`--classes all --threshold 80`
- **前 N 个**：`--classes top:N --threshold 80`
- **指定类**：`--classes com.foo.A,com.foo.B --threshold 80`

差异为空或过滤后无候选类 → 直接 `finish`，不触发门禁。

### 3.2 预算耗尽与自动跳过

预算/不收敛不再询问用户，两个层级都自动跳过并记录原因。

#### 3.2.1 类内自动跳过（verify_coverage / validate_rules 触发）

子代理类内迭代中，以下触发点**一律自动跳过当前方法**（子代理无感，不穿透主流程）：

- 单方法迭代达上限（`METHOD_ROUND_BUDGET`，默认 8 轮）
- 全局迭代达上限（`GLOBAL_ROUND_BUDGET`，默认 30 轮）→ 跳过**全部**未达标方法
- 连续 `TEST_FAIL_STREAK_ROUNDS`(3) 轮测试失败
- 连续 `NO_IMPROVEMENT_ROUNDS`(3) 轮覆盖率无提升
- `validate_rules` 连续 `VALIDATE_FAIL_STREAK_LIMIT`(5) 轮规范违规未通过

跳过动作：方法状态写 `skipped`，原因写入 `state.json` 的 `methods[].skip_reason`，随后回
`make_plan.py` 推进下一方法；队列清空则由 `decide_after_plan` 输出类内完成报告收尾。

**类级预算**：`class_round_used >= BATCH_CLASS_ROUND_BUDGET`（默认 30 轮，大类探索模式翻倍）
时，`verify_coverage.py` 在**执行 mvn 之前**预检查并跳过全部未达标方法，避免浪费一轮全量构建。

**编译失败自愈机制**：`verify_coverage.py` 检测到编译错误时，会记录本轮观测（轮次 +1）并自动路由到 `write_code` 修复，而不是跳过或升级到用户。编译失败不计入测试失败轨迹，仅用于触发自愈流程。

#### 3.2.2 批量级人工门禁（batch_update 触发，仅异常中断）

类内迭代正常结束后不会再产生类级 `ask_user`。仅当子代理**异常中断**（mvn 环境/依赖失败 exit 2、
用户主动中止等）且类内仍有 pending 方法时，`batch_update.py` 才转述中断原因请用户决策：

1. **继续委派**：重新委派该类继续迭代；
2. **跳过该类**（`--skip-class <FQCN> --reason <text>`）；
3. **终止批量**。

### 3.3 跳类决策

用户可在 `batch_update` 阶段跳过类：`--skip-class <FQCN> --reason <text>`。

---

## 4. 全局约束

- **工作目录**：`<worktree>/.agent/batch-unit-test-generator/`，各类独立子目录 `classes/<简单类名>/`。
- **串行执行**：类间严格串行，`batch_next` 原子认领保证无重复委派。
- **文件修改边界**：仅允许修改测试类文件（`src/test/java` 下），禁止修改 `src/main/java`、pom.xml、配置文件。
- **mvn 超时**：环境变量 `JAVA_UT_MVN_TIMEOUT`（默认 1800 秒）；Bash 工具前台阻塞 `timeout=1800000`（30 分钟上限），超时自动转后台轮询。
- **mvn install 仅一次**：`batch_init` 执行一次 install；`verify_coverage` 定向 mvn 不带 `-am`（依赖已预装）。
- **全量 mvn 仅两次**：`batch_init` 一次基线 + `batch_finish` 一次终验。
- **batch_state.json 原子写**：文件锁 + 临时文件 + os.replace。

### 覆盖率排除模式

`batch_init.py` 支持两种覆盖率排除模式：

1. **显式排除**：通过 `--coverage-exclude PATTERN` 参数指定排除模式（可重复）。
2. **启发式排除**：自动检测源码中的 Lombok/MapStruct 注解并排除生成的类。

**Lombok 启发式排除规则**：
- `@Builder` → 排除 `*$Builder`
- `@Value` → 排除 `*$Value`
- `@Data` → 排除 `*$Data`（保守排除）

**MapStruct 启发式排除规则**：
- `@Mapper` → 排除 `*$MapperImpl`

### 测试类收集策略

`batch_init.py` 和 `batch_finish.py` 按以下规则收集测试类列表：

1. **搜索规则**：在 `src/test/java` 同包目录下搜索以下命名模式的测试类：
   - `{SimpleClassName}Test.java`
   - `{SimpleClassName}Tests.java`
   - `{SimpleClassName}IT.java`（集成测试）
2. **去重逻辑**：收集后自动去重，避免重复执行。
3. **按需执行**：仅执行与目标类相关的测试类，减少 mvn 执行时间。

### 大类配置参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `LARGE_CLASS_DIFF_THRESHOLD` | 300 | diff_add 行数 ≥ 此值视为大类 |
| `LARGE_CLASS_METHOD_THRESHOLD` | 30 | pending 方法数 ≥ 此值视为大类 |
| `METHOD_GROUP_SIZE` | 10 | 每组方法数上限 |
| `EXPLORATION_MODE_MULTIPLIER` | 2 | 探索模式预算倍数（`BATCH_CLASS_ROUND_BUDGET × 此值`） |
| `BATCH_CLASS_ROUND_BUDGET` | 30 | 类级总预算（单类全部迭代轮次包干上限），可通过 `JAVA_UT_BATCH_CLASS_ROUND_BUDGET` 环境变量覆盖 |

### Maven 长时间执行

mvn 执行可能超过 30 分钟（大项目）。对于运行 mvn 的脚本（`init_coverage.py`、`verify_coverage.py`、`batch_init.py`、`batch_finish.py`）：

1. **必须使用 `run_in_background: true`** 执行，避免 Bash 超时中断
2. 脚本会输出进度信息到 stdout（每 30 秒一行 `[mvn-progress]`），证明进程仍然存活
3. 脚本完成时会自动通知 Agent
4. 读取输出文件获取 NEXT_STEP 协议块

**进度监控：**

脚本运行期间会向 stdout 输出进度行：

```text
[mvn-progress] running... 30s elapsed / 1800s timeout
[mvn-progress] running... 60s elapsed / 1800s timeout
```

同时在 `<workdir>/mvn_progress.json` 写入实时状态，可通过 `cat` 检查：

```json
{"status": "running", "pid": 12345, "elapsed_seconds": 300, "timeout_seconds": 1800}
```

**禁止行为：**

- 手动轮询进程状态
- 检查日志文件判断进程是否存活
- 主动杀死 mvn 进程
- 在脚本完成前重启执行

**超时处理：**

如果 mvn 真正超时（超过 `JAVA_UT_MVN_TIMEOUT`），脚本会：
- 输出明确的超时错误
- 将 `mvn.log` 附在 artifacts 中
- 路由到 `ask_user` 请求用户决策

---

## 5. 子代理委派契约摘要

`batch_next` 输出 `ask_user` 型委派指令，主流程据此委派子代理 `batch-class-writer`（定义见 `agents/batch-class-writer.md`）：

- **输入要件**：worktree 绝对路径 + FQCN + 类 workdir 绝对路径 + scripts 目录绝对路径 + 门槛 + 类级剩余预算 + 认领序号。
- **子代理职责**：从 `make_plan.py --workdir <类workdir>` 开始，严格按 NEXT_STEP 协议驱动类内循环。
- **命令白名单**：仅 `make_plan/build_prompt/validate_rules/verify_coverage` 四脚本 + `on_complete` 验证命令。
- **失控防护**：禁止 batch_*、裸 mvn、git 写操作、修改 src/main。
- 完整契约细节见 `references/delegation.md`，子代理定义见 `agents/batch-class-writer.md`。

### 5.1 大类方法组拆分

当类满足大类条件（`diff_add ≥ 300` 或 `pending 方法数 ≥ 30`）时，自动按每组 ≤ 10 个方法拆分，探索模式类级预算翻倍（`BATCH_CLASS_ROUND_BUDGET × 2`）。完整流程见 `references/delegation.md §4` 和 `references/diff-analysis.md §7`。

---

## 6. 断点续跑

双层恢复机制：

- **批次级**：`batch_state.json` 记录各类状态（pending/in_progress/done/skipped/failed/recheck/unmet/unverified，unmet 为未达标终态，unverified 为环境不可信待复核的非终态）。中断后 `batch_next` 直接续跑（已有 in_progress 直接重新输出委派要件）。
- **类级**：`classes/<类名>/state.json` 记录类内迭代进度。子代理从 `make_plan` 续跑（state.json 存在则自动恢复游标）。
- **僵死复位**：`batch_next --reset-claim <FQCN>` 将僵死 in_progress 复位为 pending。
- **类内跳过恢复**：被自动跳过的方法为终态，用 `make_plan.py --unskip <方法名[,方法名]>`（状态改回 pending，并重开全局与类级预算窗口，可配 `--grant-rounds N`；同名重载会一并恢复）；`--grant-rounds` / `--set-threshold` 都不能复活 skipped 方法。

---

## 7. 完成标准与最终交付

- **类内完成**：所有方法 done/skipped → 子代理输出交付报告（含类内完成报告）。
- **批量终验**：`batch_finish` 一次全量 mvn → 每个活跃类（done/recheck/unverified）刷新覆盖率与测试 → 按类判定（覆盖率达标 + 结果可信 + 失败用例不在本类）→ 确认 done；否则 → 有补测空间（存在 pending 方法）→ recheck 重入队；无补测空间 → unmet 未达标终态（记录 skip_reason 并入最终报告）；环境不可信（surefire 报告缺失/目录清理失败）且无补测空间 → unverified 待复核（非终态，环境修复后重跑 `batch_finish` 重新终验）。终态类（skipped/unmet/failed）不参与复核，只进最终报告；全部类均为终态时跳过 mvn 直接出报告。整批 `tests_green` 是聚合值，别的类红灯不会把本类打成 unmet；本类失败归因覆盖该类全部测试类命名变体（`{Simple}Test`/`{Simple}Tests`/`{Simple}IT`），既有 `FooTests` 的失败不会漏算成"其他类的失败"。
- **最终报告**：全部确认 → `finish` 携带 `render_batch_finish_report`（各类 before→after、达标/跳过/失败/未达标清单、总轮次、总自动跳过方法数、终验结果、worktree 清理提醒），LLM 逐字转述。

---

## 8. 权威文件优先级

1. 本 `SKILL.md`（技能行为准绳）；
2. `protocol/next-step.schema.json`（协议 Schema）；
3. `references/UnitTestRules.md`（测试规则）；
4. `references/diff-analysis.md`（差异过滤/排序规则细节）；
5. `references/delegation.md`（子代理委派与失控防护完整契约）；
6. `agents/batch-class-writer.md`（子代理定义）。

本 `SKILL.md` 中的 Workflow / 门禁 / 委派 / 终验 / 报告均指**标准模式**；快速模式见 §0.1，仅保留 `select_worktree` + `batch_diff`，不使用子代理、不跑 mvn。

与 `SKILL.md` 冲突时以 `SKILL.md` 为准。
