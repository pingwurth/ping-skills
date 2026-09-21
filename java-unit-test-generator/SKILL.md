---
name: java-unit-test-generator
description: 为单个 Java 类（可限定单个方法）按 JaCoCo 行覆盖率门槛迭代生成 JUnit5+Mockito 单元测试。当用户要求补充 Java 单元测试、为指定方法写单测或提升某类覆盖率时使用。多个目标类必须逐类调用本技能。
tools: Read, Write, Edit, Glob, Grep, Bash
---

# Java 单元测试生成

针对单个 Java 类（可选单个方法），以 **JaCoCo Line Coverage** 为主目标，通过脚本 + LLM 迭代生成测试，直到达到门槛。核心原则：每次执行先确认 git 工作树；脚本负责分析、执行、验证和流程决策；LLM **仅在 `write_code` 阶段**编写/修改测试代码；不得修改生产代码；测试必须通过后才能判定目标完成；所有流程由 `NEXT_STEP` 驱动；所有状态保存在 `state.json`，支持断点续跑；多轮迭代必须有预算，禁止无限循环。

## 1. Workflow

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

每个脚本结束时，在 stdout 末尾输出唯一协议块（`:::NEXT_STEP_BEGIN:::` / `:::NEXT_STEP_END:::`），只解析标记块中的 JSON。详细格式见 `protocol/next-step.schema.json`。退出码：0=正常完成，1=需要继续处理，2=脚本执行错误，3=状态/协议错误。**退出码 1 是正常流转信号，不是脚本失败。** 协议块缺失时，读 `<workdir>/logs/<script>.log` 尾部（约 50 行）与 summary，向用户报告错误并终止本技能执行。

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

本 SKILL.md 只定义 Workflow 和全局约束。详细规则分别以以下文件为准：

```text
protocol/next-step.schema.json
protocol/state.schema.json
references/UnitTestRules.md
references/workflow-details.md
references/upgrade-strategy.md
```

发生冲突时：`Schema > Rules > SKILL.md > LLM 自行判断`。
