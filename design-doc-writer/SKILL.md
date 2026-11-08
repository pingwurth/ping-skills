---
name: design-doc-writer
description: 编写需求分析与详细设计文档。用于：根据用户需求/知识库/代码库深度调研后产出设计文档——功能概述、UI 与页面交互、核心逻辑、接口、数据库、缓存、非功能性设计、TDD 测试用例；先以问题台账按 frontier 轮次向用户逐轮提问（每题带推荐答案），用户决策后落盘需求分析文档与详细设计文档（复杂逻辑用 mermaid UML 表达，不堆砌代码），支持中断续跑。典型触发："写设计文档"、"详细设计"、"概要设计"、"需求分析文档"、"出技术方案"、"方案设计"、"design doc"。不要触发于：直接写代码实现、代码评审（用 code-review-p）、生成仓库 wiki（用 pwiki）、单元测试生成、修改既有文档错别字等轻量编辑。
---

# 设计文档编写 skill

一句话：**LLM 负责调研、提问、写文档；`scripts/dd.py` 负责问题台账、frontier 计算、文档门禁、原子落盘；用户是唯一决策者与确认者。** 流程 = 深度分析 → grilling 轮次提问逼出全部决策 → 需求分析文档落盘并确认 → 详细设计文档落盘并确认。

## 权威优先级

冲突时以高者为准：**dd.py 实际行为（退出码/门禁结果） > `references/` > 本 SKILL.md > 自行判断**。脚本拒绝的推进不要绕过；发现文档与脚本行为不一致时，以脚本为准并向用户报告。

## 产物文件（目标库下）

| 路径 | 由谁产生 | 说明 |
|---|---|---|
| `<repo>/.agent/design-doc-writer/ledger.json` | dd.py | 问题台账/阶段/文档状态，**禁止手编**，只能经 dd.py 子命令修改 |
| `<repo>/.agent/design-doc-writer/dd.log` | dd.py | 每次调用的详细日志 |
| `docs/design/<feature>/requirements-analysis.md` | LLM | 需求分析文档（默认路径，init 可覆盖；尊重目标库既有文档目录惯例） |
| `docs/design/<feature>/design.md` | LLM | 详细设计文档 |
| `docs/adr/NNNN-<slug>.md` | LLM | ADR-lite（编号与路径由 dd.py 在 q-answer --adr 时分配） |

## 硬约束（全程有效，逐条编号）

1. **绝不修改目标库代码**。只允许写：两份文档、`docs/adr/` 下 ADR 文件、`.agent/design-doc-writer/` 下台账与日志。
2. **Draft → Confirmed 只有一个通道**：用户明确确认后执行 `dd.py confirm --doc <d> --because "<用户确认原话摘要>"`。门禁未过、frontier 未清、用户未表态，禁止推进。
3. **台账只经 dd.py 子命令修改**，任何情况下不得手编 ledger.json。
4. **事实自己查，决策问用户**：能从代码库/知识库/文档查到的事实绝不问用户；需要用户拍板的决策绝不替用户决定。每个问题都必须附推荐答案。
5. **文档不堆砌代码**：接口设计用表格 + 一行签名；数据库设计用表结构表格 + erDiagram；禁止整段实现代码；伪代码仅算法必要处且 ≤10 行并附说明。
6. **未决必须显式标注**：文档中以 `[未决:Qxxx]` / `[延期:Qxxx]` 标注（check 门禁校验与台账对账），不得静默假设。
7. **中断恢复一切从 `dd.py status` 开始**：有台账即按其 phase 续跑；**绝不重新 init、绝不回滚或删除已落盘内容**。
8. **ADR 三重门**：只有同时满足"难逆转 / 无上下文会困惑 / 真实权衡"的决策才写 ADR（q-answer --adr）；一个 ADR 可以只有一段。

## 阶段 0 — 恢复检查（每次会话首先执行）

```bash
python3 <skill>/scripts/dd.py status --repo <repo>
```

- 有台账 → 按 phase 续跑（各阶段的下一步指令见 status 输出的 next_step）。
- 无台账（exit 2）→ 全新流程，从阶段 1 开始。

## 阶段 1 — intake（收集输入）

| | |
|---|---|
| LLM 做 | 收集三类输入：①需求描述（文档/口头）②知识库位置 ③目标代码库路径；用户未提供且不可自查的信息**问用户**（包括期望的文档落盘位置——尊重目标库既有文档惯例，含糊时给推荐项再问） |
| 脚本做 | `dd.py init --feature <slug> [--shape web\|api\|backend\|library] [--analysis-doc ..] [--design-doc ..] [--adrs-dir ..]` 建台账（shape 预置章节清单；台账已存在则进入恢复模式，绝不重置） |
| 问用户 | 需求来源与目标、期望文档位置、项目形态（web/api/backend/library） |
| 禁止推进 | 需求目标仍空白时不得进入分析 |

## 阶段 2 — 深度分析与问题识别（analyzing）

| | |
|---|---|
| LLM 做 | 先 `dd.py kb-scan`（可带 `--openwiki` 自报 MCP 可用性）；按台账 kb.primary 优先用知识库做事实调研（codegraph→codegraph_explore；repowise→search_codebase/get_answer；pwiki→pwiki 检索；wiki→openwiki MCP 或直读目录内 markdown；graphify→图查询），**primary 只是默认建议，可按问题性质改用任一可用源**；无知识库则降级直接读码。对照 `references/question-dimensions.md` 逐维度识别问题 |
| 脚本做 | 先 `dd.py phase analyzing` 进入分析阶段；`dd.py q-add --dimension <d> --title <t> --body <b> --recommendation <r> [--depends-on Q001,Q002] [--facts <自查事实>]`（支持 `--batch questions.json` 批量；analyzing 阶段首个 q-add 自动进入 grilling） |
| 问用户 | 不问——本阶段产出问题，不作答 |
| 禁止推进 | 未做过任何事实调研就提问（先查再问）；q-add 期间禁止写正式文档 |

## 阶段 3 — grilling 轮次提问（grilling）

| | |
|---|---|
| LLM 做 | `dd.py frontier` 取本轮问题 → **一轮全问**（下方格式）→ 等用户答复 → 逐条 `dd.py q-answer <id> --answer "<决策>"`（三重门全中才加 `--adr --adr-slug <ascii>`，脚本会分配 `docs/adr/NNNN-slug.md` 路径，随后**立即**按 `references/doc-templates.md` 的 ADR 模板写该文件）或 `q-answer <id> --defer --reason "<原因>"` → 再 `frontier`，循环到 exit 0 → 向用户复述理解、求 shared-understanding 确认（**frontier 清零不等于完成**） |
| 脚本做 | frontier = 前置依赖全部 answered 的 open 问题（纯图算法）；被阻塞问题及其阻塞源一并打印；被延期依赖阻塞的单独提示 |
| 问用户 | 每轮整组问题一次问完，用户按编号作答；事实类疑问不问用户，自己去查（查到后作为 --facts 记录） |
| 禁止推进 | frontier 未清零且用户未确认理解前，不得进入 analysis-doc |

提问轮格式（grilling 原格式，题与题之间用 `---` 分隔）：

```markdown
❓ **Q006** - **主列表分页还是无限滚动？**: 数据量与交互频率的考量……（可多段、可含选项）

➡️ 推荐分页：复用现有 List 组件，理由一句

---

❓ **Q007** - **……**: ……
```

后续阶段发现新问题：`dd.py q-add` 补录 + `dd.py phase grilling` 回流处理；**已写文档内容不回滚**，答完后增量更新受影响章节。

## 阶段 4 — 需求分析文档（analysis-doc）

| | |
|---|---|
| LLM 做 | 按 `references/doc-templates.md` 骨架写分析文档：H1 下第二行写 `状态: Draft`；六节齐全；决策表引用 ADR-NNNN；延期问题在"未决与延期"节 `[延期:Qxxx]` 显式标注 |
| 脚本做 | `dd.py check --doc analysis` 逐项 PASS/FAIL/WARN + 修复指引，迭代到全 PASS（exit 0）；confirm 成功时脚本自动把文档状态行同步为 `状态: Confirmed` |
| 问用户 | check 全过后，请用户审阅并**明确确认**；确认后 `dd.py confirm --doc analysis --because "<原话摘要>"` |
| 禁止推进 | check 未过或用户未确认时，confirm 会被脚本拒绝（文档保持 draft） |

## 阶段 5 — 详细设计文档（design-doc）

| | |
|---|---|
| LLM 做 | `dd.py phase design-doc`（analysis 未 confirmed 会被拦截）→ 按模板写设计文档：章节以台账 manifest 为准（`dd.py manifest --enable/--disable <id> --reason ".."` 可裁剪）；核心逻辑按子域拆小节、每节一张 mermaid（选型见 `references/mermaid-uml.md`）；接口=表格+一行签名；DB=表结构+erDiagram；TDD 用例表（TC-xxx，Given/When/Then，覆盖核心路径+边界+异常，建议覆盖非功能场景） |
| 脚本做 | `dd.py check --doc design`（含前置：analysis 已确认、台账零 open 零 deferred）迭代到全 PASS |
| 问用户 | check 全过后请用户审阅确认 |
| 禁止推进 | 设计文档禁整段实现代码；门禁未过禁 confirm |

## 阶段 6 — 终确认与收尾

```bash
python3 <skill>/scripts/dd.py confirm --doc design --because "<用户确认原话摘要>"
python3 <skill>/scripts/dd.py finish
```

finish 校验双文档 confirmed 后置 phase=done 并打印交付摘要。最后向用户**逐文件汇报**全部产出路径与 Draft/Confirmed 状态（两份文档、每个 ADR、台账位置）；提醒台账在 `.agent/` 下不随仓库提交。

## 脚本退出码总表（全部子命令一致）

| 退出码 | 含义 | 处置 |
|---|---|---|
| 0 | 完成 / 门禁全 PASS | 按 next_step 推进 |
| 1 | **正常流转信号，非失败**（frontier 有待办 / check 有 FAIL / confirm 被拒 / finish 未双确认） | 按 next_step 修复后重试 |
| 2 | 执行异常（参数错 / 台账缺失或损坏 / 非法状态流转 / IO 错误） | 读错误信息修正；台账问题只能用子命令修复 |

所有子命令 stdout = 人类可读摘要 + **末行唯一一条** `next_step: <指令>`；详细日志写 `--log`（默认 `<repo>/.agent/design-doc-writer/dd.log`）。

## 命令速查

```bash
dd.py init --feature <slug> [--shape web|api|backend|library] [--analysis-doc p] [--design-doc p] [--adrs-dir p]
dd.py kb-scan [--openwiki]
dd.py q-add --dimension <d> --title <t> --body <b> --recommendation <r> [--depends-on Q001] [--facts <t>] [--batch questions.json]
dd.py q-answer <QID> --answer "<决策>" [--adr --adr-slug <ascii>] | <QID> --defer --reason "<原因>"
dd.py frontier                       # exit 1 = 还有待办
dd.py phase <intake|analyzing|grilling|analysis-doc|design-doc|done>
dd.py manifest --enable ui,cache | --disable release --reason "<t>"
dd.py status                         # 中断恢复入口
dd.py check [--doc analysis|design|all]
dd.py confirm --doc <analysis|design> --because "<用户确认原话摘要>"
dd.py finish
# 全局参数：--repo <path>（默认 CWD）、--log <path>；子命令前后均可传
```

dimension 枚举（与 references/question-dimensions.md 一致）：
`goal` `scope` `technical` `edge` `ui` `data-model` `api` `compat` `cache` `nonfunc` `release` `ops`
