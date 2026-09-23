---
name: java-unit-test-generator
description: 为单个 Java 类（可限定单个方法）按 JaCoCo 行覆盖率门槛迭代生成 JUnit5+Mockito 单元测试。当用户要求补充 Java 单元测试、为指定方法写单测或提升某类覆盖率时使用。多个目标类必须逐类调用本技能。
tools: Read, Write, Edit, Glob, Grep, Bash
---

# Java 单元测试生成

针对单个 Java 类（可选单个方法），以 **JaCoCo Line Coverage** 为主目标，通过脚本 + LLM 迭代生成测试，直到达到门槛。

核心原则（标准模式）：
- 每次执行先确认 git 工作树；脚本负责分析、执行、验证和流程决策；
- LLM **仅在 `write_code` 阶段**编写/修改测试代码；
- 不得修改生产代码；
- 测试必须通过后才能判定目标完成；
- 所有流程由 `NEXT_STEP` 驱动；
- 所有状态保存在 `state.json`，支持断点续跑；
- 多轮迭代必须有预算，禁止无限循环。

**第一步：先让用户选择模式（§0），选定后立刻复制对应 todo 清单，再按清单推进。**

## 0. 模式选择（加载后第一件事）

**先问模式，再动手**：用户答复前，不得执行任何脚本、不得读写任何测试文件。直接向用户提问并等待答复 —— 目标类/方法未在请求中给出时一并问清。用户在请求里已指明模式的，按其指定执行，不再重复提问。

| 模式 | 适用 | 执行方式 | 不保证 |
|---|---|---|---|
| **快速模式** | 简单类，一次生成即结束 | 纯 LLM 流程（§0.1）：探索源码 → 列计划 → 逐方法补测；**不执行任何脚本、不建 worktree、不写 `state.json`、不跑 mvn** | 覆盖率达标、编译与测试通过 |
| **标准模式** | 需要可靠覆盖率 | 复用 §1 工作流：脚本 + NEXT_STEP 驱动 + mvn 验证 + 循环自愈，直到覆盖率达标 | — |

用户选定后**立刻**把对应清单**原样复制**为本次任务的 todo 清单（宿主的 todo/任务工具可用就建到工具里，没有就在对话中维护该 Markdown 清单），随后逐项勾选推进：不跳项、不改顺序、不加项。

### 0.1 快速模式 todo 清单

```markdown
- [ ] 1. 探索目标类/方法的生产源码：确认 FQCN 与源文件、列出全部方法、识别依赖与分支
- [ ] 2. 探索对应测试类的源码：定位测试文件、已有用例、可复用的 mock/基类/工具方法
- [ ] 3. 分析哪些方法需要补单元测试，把计划清单写到 /tmp/<skill_name>/<时间戳>/<简单类名>/plan.md
- [ ] 4. 从 plan.md 取第一个「待补」方法，依据第 1、2 步的探索结果编写该方法的单元测试，每次只专注一个方法
- [ ] 5. 在 plan.md 勾掉/标注该方法状态，回到第 4 项，逐个方法增量推进，直到所有方法处理完毕
- [ ] 6. 汇报已补测方法、跳过方法与原因，并声明「快速模式未执行 mvn 验证，覆盖率与编译/测试结果未验证」
```

- 计划清单路径：`/tmp/<skill_name>/<时间戳>/<简单类名>/plan.md`，其中 `<skill_name>` = `java-unit-test-generator`；目录不存在则创建。清单按方法一行，含方法名、补测理由、优先级、状态（待补 / 已完成 / 跳过）：

```markdown
# FooService 补测计划
- [ ] `process(String)` — 待补 — 无现有用例，含 3 个分支
- [x] `getOrder(Long)` — 已完成 — 覆盖存在 / 不存在两条路径
- [ ] `init()` — 跳过 — 依赖静态初始化块，不改生产代码无法测
```

- 快速模式仍受 §3 全局约束与 `references/UnitTestRules.md` 约束，且**无脚本兜底**，必须自行自查：只允许写 `src/test/java/**/<TargetTest>.java`；禁止改 `src/main/java/**`、`pom.xml`、配置；禁止 `try-catch`（异常路径用 `assertThrows`）；每个用例以断言结尾；禁止 `@Disabled`、删除用例、同义反复断言等消红手段。
- 某方法无法在不改生产代码的前提下测试时，在 `plan.md` 标注 `跳过` + 原因，继续下一个方法，不阻塞整体流程；**不得改生产代码使其可测**。
- 快速模式不产出 `state.json`、无覆盖率与流程判定，做完即结束，不进入 §6 升级协议、§7 Final Check、§8 Finish 报告。

### 0.2 标准模式 todo 清单

```markdown
- [ ] 1. 确认 git 工作树：select_worktree.py，拿到 <worktree>
- [ ] 2. 初始化并跑首轮覆盖率：init_coverage.py --project-root <worktree> --class <FQCN 或源文件> [--method <方法名>] --workdir <workdir>
- [ ] 3. 制定迭代计划：make_plan.py --workdir <workdir>
- [ ] 4. 循环：build_prompt.py → LLM write_code → 逐字执行 next_step.on_complete → validate_rules.py → verify_coverage.py（均带 --workdir <workdir>）
- [ ] 5. 未达标回到第 3 项换方法/重排计划；达标或触发升级条件则退出循环
- [ ] 6. 升级为 ask_user 时逐字转述 question 与 resume，等用户答复后逐字执行对应命令
- [ ] 7. 目标方法全部完成后：init_coverage.py --project-root <worktree> --final-check 全量终验
- [ ] 8. 逐字转述 finish 报告（§8）；收尾清理由用户手动执行
```

- `<workdir>` 默认 `<worktree>/.agent/java-unit-test-generator`；多目标类用 `<worktree>/.agent/java-unit-test-generator/<简单类名>`（§3）。
- todo 只是进度视图：每一步执行什么、下一条命令是什么，一律以 `NEXT_STEP` 协议块为准（§2），不得用 todo 覆盖协议路由。

## 1. Workflow（标准模式）

```text
select_worktree → init_coverage → make_plan → build_prompt → LLM write_code → validate_rules → verify_coverage
                                                                                                   ↓
                                                                                              未达标/达标
                                                                                                   ↓
                                                                                              build_prompt/make_plan
                                                                                                   ↓
                                                                                              final-check → finish/ask_user
```

详细命令和参数见 `references/workflow-details.md`。

## 2. NEXT_STEP 协议

每个脚本结束时，在 stdout 末尾输出唯一协议块（`:::NEXT_STEP_BEGIN:::` / `:::NEXT_STEP_END:::`），只解析标记块中的 JSON。详细格式见 `protocol/next-step.schema.json`。退出码：0=正常完成，1=需要继续处理，2=脚本执行错误，3=状态/协议错误。**退出码 1 是正常流转信号，不是脚本失败。** 协议块缺失时，读 `<workdir>/logs/<script>.log` 尾部（约 50 行）与 summary，向用户报告错误并终止本技能执行。收到 `next_step.type == "abort"`：将 `next_step.message` **逐字**转述给用户后**立即终止本技能**——禁止执行任何后续脚本、禁止重试、禁止代为执行 message 中的修复命令；修复命令**由用户手动执行**，执行完毕后**由用户重新调用本技能**。兼容兜底（**仅限旧版协议块形态**）：`exit_code == 2` 且 `resume` **存在且恰好只含一个 `terminate` 选项**（数组长度 1）时，转述 question 后直接终止，不得重试。**禁止扩大解释**：`exit_code == 2` 且 `type == "ask_user"`、`resume` 为空数组或**缺失**，是**正常提问路径**——转述 question 并等待用户答复后继续，**不得终止、不得重试**。新版协议中，直接终止**只认** `next_step.type == "abort"`。

## 3. 全局约束

- **workdir**：`<project-root>/.agent/java-unit-test-generator/`，`state.json` 必须原子写入
- **多目标类**：必须为每个类指定独立 `--workdir`，建议 `<worktree>/.agent/java-unit-test-generator/<简单类名>`
- **断点续跑**：`<workdir>/state.json` 存在时，运行 `make_plan.py --workdir <workdir>` 即可续跑
- **文件修改**：只允许修改 `src/test/java/**/<TargetTest>.java`，禁止修改 `src/main/java/**`、`pom.xml`、配置文件等
- **Maven**：默认超时 1800 秒（可通过 `JAVA_UT_MVN_TIMEOUT` 覆盖），测试结果必须依据 Surefire：`Failures == 0 AND Errors == 0`

详细规则以 `references/UnitTestRules.md` 为准。

## 4. 覆盖率规则

默认目标：`Line Coverage >= 80%`。Line Coverage 是唯一主指标。Method Coverage 用于识别未覆盖方法，Branch Coverage 用于辅助分析。目标方法 `covered + missed == 0` 的抽象/接口方法可视为无需测试。覆盖率排除通过 `--coverage-exclude` 配置（可重复传），详细规则见 `references/UnitTestRules.md`。

## 5. 单方法迭代

`make_plan.py` 选择当前最值得补测的方法。`build_prompt.py` 为 LLM 提供：目标类/方法、生产代码、现有测试、当前覆盖率、上轮测试结果、`mvn.log`、测试规则、允许修改范围。LLM 保存测试文件后，**逐字执行 `next_step.on_complete` 的 `script` + `params`**，不得自行运行其他命令。旧协议块无 `on_complete` 字段时，运行 `validate_rules.py --workdir <workdir>`。

## 6. 不收敛与升级

同一方法出现 `连续 3 轮无有效覆盖率提升` 或 `连续 3 轮测试失败` 时，升级为 `ask_user`。默认预算：单方法最多 8 轮，全局最多 30 轮（可通过环境变量 `JAVA_UT_METHOD_ROUND_BUDGET`、`JAVA_UT_GLOBAL_ROUND_BUDGET` 覆盖）。超过预算时，升级为 `ask_user`。用户可以：继续、跳过方法、调整门槛、终止。恢复命令以 `next_step.resume` 为准，用户答复后**逐字执行对应命令**，禁止手改 `state.json`。`validate_rules` 连续 5 轮规范违规未通过时，升级为 `ask_user`。用户可以：继续修复、跳过方法、终止。恢复命令以 `next_step.resume` 为准。详细策略见 `references/upgrade-strategy.md`。

## 7. Final Check

所有目标方法完成后，运行 `init_coverage.py --final-check` 执行目标模块全量测试。Class 模式：Class Line Coverage >= threshold AND 不存在未达标目标方法 AND 测试全部通过。Method 模式：目标方法 Line Coverage >= threshold AND 测试全部通过。

## 8. Finish

Finish 报告由脚本生成，LLM 只负责转述，不负责拼装。收到 `next_step.type == "finish"` **且携带 `next_step.report`** 时（`init_coverage` / `make_plan` 发出），将其内容**逐字**输出给用户。`finish` 不带 `report`（仅 `select_worktree`）表示阶段完成，按 §1 工作流继续下一步，不转述报告。不得改写任何数字、路径、分支名或命令；不得自行增删收尾提醒或补充清理建议；数据缺失时报告显示"未记录"，同样逐字转述，不得补算；分支名以报告为准，**禁止**从工作树目录名推导。报告数据来源与收尾命令均由 `scripts/jaut/report.py` 渲染，LLM 无需关心。技能不会自动执行收尾操作，避免误删未合并代码。

## 9. 权威文件

本 SKILL.md 只定义模式选择与两种模式的 todo 清单、Workflow 和全局约束（未特别标注时，Workflow / 迭代 / 升级 / Final Check / Finish 均指标准模式）。详细规则分别以以下文件为准：

```text
protocol/next-step.schema.json
protocol/state.schema.json
references/UnitTestRules.md
references/workflow-details.md
references/upgrade-strategy.md
```

发生冲突时：`Schema > Rules > SKILL.md > LLM 自行判断`。
