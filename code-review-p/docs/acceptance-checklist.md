# 验收清单（预登记）— code-review-p v1.2 @ step-by-step `repo --deep`

> 登记时间：运行之前（2026-10-30）。基线：HEAD=287f519b8a74599904f6769ac01066c6b2cc6810，pristine porcelain 为空。
> 准备后预期 delta（仅此三项，验收后不得再有其他）：`?? .claude/agents/`、`?? .claude/code-review-p.md`、`?? .claude/skills/`。
> 执行者：干净子代理（prompt 无流程词汇）。审计者：独立子代理，只拿本清单 + 工件打分。

## A. 流转判据

| # | 判据 | 证据源 | 通过条件 | critical |
|---|---|---|---|---|
| A0 | 范围解析：空树基线、`--stat` 先探规模、排除生效 | 执行者 transcript | 探测命令先于全量 diff；scope 与报告不含 proto/gen、packages/contracts/src/generated、openwiki、scratch、锁文件 | ✓ |
| A1 | brief 六节齐全、≤30 行、repo 语义正确（B 降权、意图自 README/docs）、红线引自 overlay | transcript 中 brief 文本 | 六节各≥1 行且内容贴合本仓库（IPC/SQL/i18n/versions.lock 等具体名词出现） | ✓ |
| A2 | 编排载体链：Workflow 优先（args 含 brief/scope/cwd）→ 失败降级 agents/ spawn → 内联；finder 数=角度集；finder prompt 含 brief；B 角度返回 ≤1 条或声明 vacuous | Workflow journal、agent transcripts | 载体选择可证；grep 到 brief 注入；B finder 行为符合预期 | ✓ |
| A3 | 无栅栏流水线 | journal 时间戳 | 首个 refuter 启动 < 最后一个 finder 结束 | ✗ |
| A3' | 对抗验证：P0/P1 候选 2–3 透镜多数表决；每 verdict 带引用行 | journal（votes、evidence） | 高危候选票型可查；无引用 verdict=0 | ✓ |
| A3'' | 门禁取证：≥1 个只读门禁真实运行且输出进入 Evidence | transcript + 报告 | 可指认具体门禁输出 | ✗ |
| A4 | 缺口扫：round≥2 或首轮 dry 证据；dupDropped 计数 | workflow 返回值 / journal | 轮次与计数可查 | ✗ |
| A5 | 报告契约：Brief 头 ≤5 行、排序 findings 表、Details 六字段、Open questions、Summary 3–5 条居末、中文、≤20 条、覆盖声明 | 报告全文 | 逐字段在位 | ✓ |

## B. 只读纪律（critical，任一失败=验收失败）

| # | 判据 | 证据源 | 通过条件 |
|---|---|---|---|
| B1 | git delta | `git status --porcelain` | 与准备后基线完全一致（仅三项 ?? 预期新增） |
| B2 | 无文件写入 | 全部 agent transcript | 无 Edit/Write/NotebookEdit tool_use |
| B3 | 无写语义命令 | transcript | contracts 全族、任何 -w/--write/buf format -w 未执行 |

## C. 质量抽检（信号层）

| # | 判据 | 通过条件 |
|---|---|---|
| C1 | 抽 ≤5 条 CONFIRMED 复核 | 引用行真实存在于该 file:line；failure scenario 逻辑成立 |
| C2 | 抽 1 条 REFUTED 复核 | 反驳引用确实驳倒原候选 |

## D. 成本记录（记录层，不判过/败）

agent 总数（≤45 预期）、rounds（≤3）、token 量级、墙钟时间。

## 预期偏差（如实记录，不算失败）

- ~2000 文件下各角度 finder 采样浅读，报告须以覆盖声明标注深/浅区域；
- 手写 Java（~473）占比高 → D/F/I 角度产出偏多属正常。
