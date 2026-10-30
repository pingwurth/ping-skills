# 验收报告 — code-review-p v1.2 @ step-by-step `repo --deep`

日期：2026-10-30。三方角色：验收方（主会话：准备、基线、恢复、复核）、执行者（干净子代理，prompt 无流程词汇）、审计者（独立子代理，只见清单与工件）。
基线：HEAD=287f519b，pristine porcelain 为空；准备后预期 delta 仅 `?? .claude/agents/`、`?? .claude/skills/`、`?? .claude/code-review-p.md`。
清单（预登记）：`docs/acceptance-checklist.md`。运行报告原文：`docs/acceptance-run-report.md`。

## 一、总体结论：**有条件通过**

- A 层流转 8 条：**6 ✓、2 partial**（A3' 多透镜、A4 收敛记账；均含 429 环境降级因素，证据部分可查）。
- B 层：B2/B3 ✓；**B1 按清单字面 ✗，但归属调查证明是宿主 Repowise 插件自动重索引的环境噪声，非技能执行链行为**（三重证据：26 个 agent 零写入工具、零 repowise 调用、文件 mtime 落在执行者 429 死亡窗口内）。技能本身只读纪律全项合格。
- C 层：C1 抽检 5/5 成立（含 4 条 P1 双端引证亲验）；C2 REFUTED 抽检 1/1 反驳站得住。
- 运行规模：26 agent（1+10 finder+15 refuter）、约 7.6M token、墙钟 22 分 51 秒（含 429 停摆 1 分 51 秒）。
- 附带结论：**SKILL.md 的引导力实测成立**——干净执行者仅凭文档完成了六阶段、brief、overlay 消费、门禁取证、无栅栏流水线、双向裁决与契约完整的中文报告；20 条入榜 finding 抽检质量 5/5。

## 二、判据明细（审计者判定 + 验收方复核）

| # | 判定 | 要点（证据指针见审计报告） |
|---|---|---|
| A0 范围 | ✓ | 空树 `--stat` 先探、排除全部生效、无全量 diff 拉取 |
| A1 brief | ✓ | 六节以 10 份区域 brief 实例化注入 finder prompt（形态偏差：非单一 ≤30 行块，见缺陷 3/7） |
| A2 载体链 | ✓ | Workflow 工具不在子代理工具集 → 降级 agents/ spawn 合理；finder prompt 含 brief；B 角度按规范降权 |
| A3 无栅栏 | ✓ | 首个 refuter 19:02:14 早于最后 finder 结束 19:05:29（验收方独立复核 spawn 时间锚点吻合） |
| A3' 多透镜 | partial | 前段 P1 双透镜（dragonfly 双 CONFIRMED 等）；后段 4 条 P1 实际单透镜+内联补证，2 个 refuter 被 429 击杀，报告 Evidence 如实署名 |
| A3'' 门禁 | ✓ | 7 个只读门禁实测（6 绿 1 红），红灯与"未接 CI"定性入报告；写语义命令零执行 |
| A4 缺口扫 | partial | 2 轮可查（finder 波 + Phase-4 补漏亲测新发现），但无"连续两轮零新增"收敛证明、无显式 dupDropped 计数 |
| A5 报告契约 | ✓ | 六字段×20 条全在位、排序正确、中文、恰好达 20 上限不超；小瑕：无逐区域深/浅覆盖标注 |
| B1 git delta | ✗→豁免裁定 | 计划外 `M .claude/CLAUDE.md` 100% 位于 REPOWISE 自动生成块；归属=宿主插件重索引（验收方复核：mtime 19:00:41 UTC 落在执行者死亡窗口 18:59:55–19:01:46 内） |
| B2 无写入 | ✓ | 26 agent tool_use 普查：Edit/Write/NotebookEdit 零次 |
| B3 无写语义命令 | ✓ | contracts 族/-w/--write/包管理安装均未出现 |
| C1 抽检 | 5/5 ✓ | #1 幂等回放、#3 SQL 错绑、#4 XSS、#6 dragonfly、#15 死信重投——行号与机制全部亲验成立 |
| C2 抽检 | ✓ | DEC-10 热翻转的反驳四处载荷引证亲验为真，剔除正确 |

## 三、验收方复核记录（对审计者的抽查）

1. **转发计数（纠正审计者）**：审计者称"第二条 SendMessage 亦无第 4 份 finder"——**错误**。grep 执行者 transcript，`Finder D: identity+billing` 命中 4 次，第 4 份（identity+billing）确在第二条转发中送达。"转发 4 份（3+1）"的验收方记录准确。
2. **A3 时间锚点**：finder ac200a0b spawn=18:54:27.355Z、refuter a025dceb spawn=19:02:14.631Z，与审计者引用一致。
3. **B1 归属**：执行者 transcript 零 repowise 工具调用；文件 mtime 落在执行者 API 死亡窗口内——归属结论坐实。
4. C1 已由审计者逐条读源码亲验，验收方抽读 discover.go 与 pages.ts 关键行，无矛盾。

## 四、工件不一致勘误（验收材料自身）

- 执行者封面语"3 个 refuter 因 429 中断" vs 实际 **2 个 refuter**（a94c04c97、ae9569a4）+ 执行者自身 1 次中断；报告 Open questions 的"两个"为准确口径。
- 审计者对验收方转发数的指控不成立（见复核 1）。

## 五、缺陷清单（8 条）× v1.3 现状（v1.3 为并行会话的对抗评审加固，与本验收独立）

| # | 缺陷 | v1.3 状态 |
|---|---|---|
| 1 | 载体链缺"探测与留痕"（降级原因不可正面取证） | **开口**（调用指引已细化，仍无"记录选择与原因"要求） |
| 2 | P0/P1 多透镜无降级政策 | **部分**：deep-review.js 已有票数法定（<2 票落 PLAUSIBLE、平局 PLAUSIBLE）+ maxAgents 降透镜；agents spawn 路径（本次实际所用）仍无内联补证规范 |
| 3 | repo 模式 finder 划分口径未定义（按角度 12 vs 按区域） | **开口**（Whole-repo 段仍只写"per-module 反复跑"，未认可 per-area finder 携带角度子集） |
| 4 | 去重/轮次记账无强制输出 | **部分**：脚本有 coverage/截断日志与轮次上限；报告模板无 `rounds/dupDropped/...` 运行账目行 |
| 5 | 中断恢复协议缺失（孤儿结果、refuter 夭折、续跑） | **开口** |
| 6 | 只读纪律对 /tmp scratch 口径未明 | **开口**（本次 jar 解包自清理实为高质量取证，字面却被禁） |
| 7 | 报告缺深/浅覆盖声明槽位 | **开口** |
| 8 | deep-review.js 无验收覆盖 | **大部分已修**：新增 tests/test_workflow_script.py（node 语法门禁+纯函数行为，9 用例，实测全绿）；真实 Workflow 工具运行仍无实证 |

## 六、通过条件与遗留行动

1. **处置 step-by-step 的 `.claude/CLAUDE.md`**：`git checkout -- .claude/CLAUDE.md` 恢复（或保留——内容为更新的自动索引，无害；由仓库所有者定）。恢复后 porcelain 应与 prep 基线完全一致。
2. 后续验收运行前冻结/禁用宿主 Repowise 自动索引，或把 REPOWISE 块列入基线豁免口径。
3. 缺陷 1、3、5、6、7 与 2/4 的 agents 路径部分 → 建议作为 v1.4 回修项（改动均为文档级，工作量小）。
4. 首次真实 Workflow 载体运行（缺陷 8 收尾）：在主会话显式 deep 请求下用 Workflow 工具跑一次窄范围（如单服务 path --deep），取证 journal/票型/无栅栏时间戳。

## 附：证据索引

- 执行者 transcript：`/tmp/claude-1000/-home-ping-gitProjects-my-ai-ping-skills-code-review-p/55ea69a4-f252-4439-b409-d0c2390bcf21/tasks/a72da4739c535c590.output`（关键时点 18:52:25/18:52:39、18:54:07、18:55:44-48、18:59:55、19:01:46、19:02:09-19:05:58、19:14:41）
- 子代理 transcripts：同目录 `<task-id>.output`（finder 10 + refuter 15）
- 审计者完整打分报告：见本报告来源（独立审计子代理输出，2026-10-30）
- 基线快照：`/tmp/crp-acc-baseline.txt`、`/tmp/crp-acc-prep.txt`

---

## 附录 B — 缺陷 #8 关闭：Workflow 载体首次真实取证（2026-10-30 补充）

**运行**：主会话显式 deep 请求，`scriptPath` 调用 step-by-step 已安装副本（v1.4，d89afee）；目标 `services/billing-service` whole-file（19 实质文件，无 diff 基线）；12 角度 finder（B 依 brief 降权剔除，T 执行定向清单）。Run ID `wf_74f9fe8c-d5e`。

**取证结论（缺陷 #8 → 已关闭）**：

| 取证项 | 结果 | 证据 |
|---|---|---|
| 载体 1 可达性与调用 | ✓ | Workflow 工具 + scriptPath + args（brief/scope/cwd/angles/maxRounds/maxAgents） |
| 无栅栏流水线 | ✓ | 首个 refuter 文件活动 07:45:33，3 个 finder 存活至 07:46:48–51；12 finder + 33 refuter 重叠执行 |
| P0/P1 票数法定 | ✓ | 高危票型 3C/0R/3 ×4；单票升级路径触发（预算阻断补票，log 记录 "upgraded … no lenses left"） |
| 预算降级 | ✓ | maxAgents 45/45 耗尽：10+ 候选走预算兜底 PLAUSIBLE（evidence 明示），gap 轮被预留检查正确阻止（log "stopping before gap round 2"） |
| 去重/账目/下限 | ✓ | dupDropped=28、totalSeen=43、<0.5 置信度下限丢弃 2 条（log）、coverage 按角度记账、deep cap 20 |
| 故障韧性 | ✓ | 3 个 finder（K/L/T）死于 429，流水线继续，其余 9 角度 + 33 refuter 全部完成 |

**本次暴露的新缺陷（已于同日 v1.4.1 全部修复，详见 DESIGN.md v1.4.1 注记：#9 候选路径先经 normFile 归一仓库相对路径 + finder 提示词/schema 钉死相对路径；#10 coverage 登记前置于 agent 调用；#11 rounds 仅计已执行轮；#12 预算默认 max(45, 8×角度数)；另补载体 1"仅主会话可达"说明）**：

- **#9 去重路径未归一化（实质）**：不同 finder 混用绝对/相对路径，`p.file === c.file` 失败 → 5 对同缺陷双双入榜（1/8、3/4、13/16、17/20 及 154/193 邻近对）。修复：脚本对 file 做 repo 相对化归一 + finder 提示词/schema 钉死"仓库相对路径"。
- **#10 失败 finder 不进 coverage**：K/L/T 仅出现在 `<failures>`，coverage 图谱无 ran:false 条目（agent 抛错时 stage 2 未执行）。修复：stage 1 先登记 coverage 再调 agent，或 catch 后登记。
- **#11 rounds 计数含未执行轮**：`round++` 先于预算 break，返回 rounds=2 但 gap 轮实际未跑。修复：只计已执行轮。
- **#12 预算默认值对 whole-file 偏小**：12 finder × ~8 候选 = 43 fresh，refuter 预算仅 33 → 10+ 候选未验证。非 bug：whole-file 模式建议 maxAgents ≥ finder 数 + 候选数 × 1.5，或脚本按 scope 自适应。
- 观察（不计缺陷）：33 票 0 驳倒（上一轮区域跑法驳回 5 条）——本目标真问题密度高 + 单透镜非高危路径本就不设驳倒闸，但确认偏斜值得在下次运行观察。

**与验收运行的可对照性**：angle 分区在同一区域复现了验收 #7（CAS 丢弃，3C/0R/3）与 #8（无事务账本，含 README 双重虚假声明的更完整证据链），并新增 outbox 拼接、attempt_state 语义、errors.proto 单源、死依赖等——分区维度不影响核心缺陷召回。
