---
name: batch-unit-test-generator
description: 批量为分支差异涉及的多个 Java 类生成 JUnit5+Mockito 单元测试。当用户要求批量补单测、为分支差异类批量写单测或提升多个类覆盖率时使用。一次 install + 一次全量 mvn 产出基线，逐类委派子代理迭代，最后批量终验。
tools: Read, Write, Edit, Glob, Grep, Bash
---

# 批量 Java 单元测试生成

针对分支差异涉及的**多个 Java 类**，以 JaCoCo Line Coverage 为主目标，批量生成 JUnit5+Mockito 单元测试，直到达到门槛。

核心原则：

- 工作树选择仅一次，整个批次共享同一工作树。
- mvn install 仅主流程一次，全量 mvn 测试仅基线和终验各一次。
- 类间严格串行，batch_next 原子认领保证无重复委派。
- 子代理负责单类迭代循环（make_plan → build_prompt → write_code → validate_rules → verify_coverage）。
- 升级点在类级总预算（默认 30 轮）内自动继续，耗尽才穿透用户。
- 大类自动拆分方法组（pending ≥ 30 方法或 diff ≥ 300 行），每组 ≤ 10 方法独立委派，探索模式预算翻倍。
- 所有流程由 NEXT_STEP 协议驱动，支持双层断点续跑。

---

## 1. Workflow

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
      (batch_mode: 队列空→类内finish; 升级点预算内自动继续, 耗尽才穿透)
      ↓  子代理输出交付报告
batch_update (读类 state.json 落账: done/skipped/failed + 覆盖率 before→after)
      ↓  有剩余 → batch_next 循环; 无剩余 → batch_finish
batch_finish (一次全量 mvn → 批量终验 → 不达标类重入队 recheck → 最终报告 finish)
      ↓  recheck 类 → batch_next 重新委派
```

### 脚本调用

```text
# 第一步: 工作树确认
python scripts/select_worktree.py <当前工作目录> [--choice N | --new [名称] | --clear-history] [--base REF] [--force]
# resume 的 params 以 <当前工作目录> 开头可逐字执行; --clear-history 先预检(base ref/分支检出/目标路径,
# 失败则历史保留), 清理会永久删除历史树未提交内容(分支保留); 已有分支 + --base 会强制重置该分支
# 拿到 <worktree> 后:

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
- `next_step.type`：`run_script` / `write_code` / `ask_user` / `finish`。

---

## 3. 人工决策门禁

### 3.1 确认门禁（batch_diff 输出 ask_user）

差异分析完成后，呈现候选类清单，**用户必须确认范围与门槛**后才进入批量基线：

- **全部**：`--classes all --threshold 80`
- **前 N 个**：`--classes top:N --threshold 80`
- **指定类**：`--classes com.foo.A,com.foo.B --threshold 80`

差异为空或过滤后无候选类 → 直接 `finish`，不触发门禁。

### 3.2 升级穿透

升级穿透分为两个层级：**类内升级穿透**和**批量级升级穿透**。

#### 3.2.1 类内升级穿透（verify_coverage / validate_rules 触发）

子代理类内迭代中，升级点（连续失败/无提升/预算耗尽/连续规范违规）在 `BATCH_CLASS_ROUND_BUDGET`（默认 30 轮）内**自动继续**，子代理无感。预算耗尽时穿透为 `ask_user`，用户四选项：

1. **继续**（`make_plan.py --grant-rounds 3`）：追加预算窗口继续迭代；
2. **跳过该方法**（`make_plan.py --skip-current`）；
3. **调整门槛**（`make_plan.py --set-threshold N`）；
4. **终止**。

**编译失败自愈机制**：`verify_coverage.py` 检测到编译错误时，会记录本轮观测（轮次 +1）并自动路由到 `write_code` 修复，而不是升级到用户。编译失败不计入测试失败轨迹，仅用于触发自愈流程。

**批量模式自动继续机制**：当 `batch_mode=True` 且 `class_round_used < BATCH_CLASS_ROUND_BUDGET` 时，升级点（单方法预算耗尽/全局预算耗尽/连续测试失败/连续无提升/连续规范违规）会自动继续，而不是穿透到用户。自动继续时：
- 自动追加预算窗口（`method_round_bonus += RESUME_GRANT_ROUNDS`，默认 3 轮）
- 复位当前方法轨迹
- 清零 validate_fail_streak
- 子代理无感继续迭代

#### 3.2.2 批量级升级穿透（batch_update 触发）

当子代理输出交付报告但类内仍有 pending 方法时，`batch_update.py` 会推断升级原因并转述给用户，用户三选项：

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

当类满足大类条件（`diff_add ≥ 300` 或 `pending 方法数 ≥ 30`）时，自动按每组 ≤ 10 个方法拆分，探索模式预算翻倍。完整流程见 `references/delegation.md §4` 和 `references/diff-analysis.md §7`。

---

## 6. 断点续跑

双层恢复机制：

- **批次级**：`batch_state.json` 记录各类状态（pending/in_progress/done/skipped/failed/recheck）。中断后 `batch_next` 直接续跑（已有 in_progress 直接重新输出委派要件）。
- **类级**：`classes/<类名>/state.json` 记录类内迭代进度。子代理从 `make_plan` 续跑（state.json 存在则自动恢复游标）。
- **僵死复位**：`batch_next --reset-claim <FQCN>` 将僵死 in_progress 复位为 pending。

---

## 7. 完成标准与最终交付

- **类内完成**：所有方法 done/skipped → 子代理输出交付报告（含类内完成报告）。
- **批量终验**：`batch_finish` 一次全量 mvn → 每个非 skipped 类刷新覆盖率与测试 → 达标且测试绿 → 确认 done；否则 → recheck 重入队。
- **最终报告**：全部确认 → `finish` 携带 `render_batch_finish_report`（各类 before→after、达标/跳过/失败清单、总轮次、终验结果、worktree 清理提醒），LLM 逐字转述。

---

## 8. 权威文件优先级

1. 本 `SKILL.md`（技能行为准绳）；
2. `protocol/next-step.schema.json`（协议 Schema）；
3. `references/UnitTestRules.md`（测试规则）；
4. `references/diff-analysis.md`（差异过滤/排序规则细节）；
5. `references/delegation.md`（子代理委派与失控防护完整契约）；
6. `agents/batch-class-writer.md`（子代理定义）。

与 `SKILL.md` 冲突时以 `SKILL.md` 为准。
