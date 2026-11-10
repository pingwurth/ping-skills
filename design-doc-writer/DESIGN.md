# DESIGN.md — 技法来源与设计决策（内部文档，非用户契约）

## 技法来源（Source → Taken → Changed）

| 来源 | 取了什么 | 改了什么 |
|---|---|---|
| mattpocock `grilling`（~/.agents/skills/grilling） | 设计树 + frontier 轮次制；每题编号+推荐答案+`---` 分隔；事实自查/决策问用户；frontier 清零后还需用户确认 shared understanding | frontier 从"模型当场判断"改为 dd.py 依 depends_on 纯图算法计算；问题落盘进台账（原版只活在对话里，"Every other grilling skill leaves the session in your head"） |
| mattpocock `domain-modeling`（grill-with-docs 的实际落盘方） | ADR 三重准入门（难逆转/无上下文会困惑/真实权衡）；NNNN-slug 编号；"一个 ADR 可以只有一段"；CONTEXT.md 术语表格式（`**术语**:` + `_Avoid_:`） | ADR 收进本技能的 docs/adr/ 与决策表联动（check 做 ADR↔文档三向对账）；术语表并入需求分析文档一节而非独立 CONTEXT.md（设计文档特性天然单特性作用域） |
| Meta muse-code `grill-and-record`（泄露存档） | Draft/Final 两态；已定决策立即落盘不攒批；未决显式标注；仅用户显式接受才转 Final；中断保全绝不自动回滚；遵循目标库既有文档位置 | Draft→Final 变为 Draft→Confirmed 且只能走 `dd.py confirm --because`（脚本门禁 + 审计留痕）；落盘对象从决策记录扩为两份文档 |
| Hermes `grill-me` v2.0.0 | 提问维度三分支（Understanding/Technical/Edge）+ Pitfalls 清单 | 扩为 12 个 dimension 枚举（+ui/data-model/api/compat/cache/nonfunc/release/ops），每个带跳过条件，与 q-add 校验、manifest 章节联动 |
| 本仓库 `sensors-analyze` | 轻协议风格：stdout 末行唯一 `next_step:`、日志走 --log、退出码契约（1=正常流转信号）、占位符禁令、人工确认关卡、`.agent/<skill>/` 工作目录、utils 的 stderr 重定向模式 | 无 — 协议风格整体沿用 |

grill-with-docs 原版"一行委托另一个 skill"的架构被其官方文档记载为不可靠（skill 委托 skill 常加载失败），故本技能把全部关键指令内联。

## 设计决策

- **单一 ledger.json 而非多文件**：问题/决策/阶段/文档状态强关联，单文件原子写最简单可恢复；ADR 与文档是用户交付物独立成文件，台账只存分配路径不存文件真值（存在性由 check 看盘）。
- **enabled 而非 required**：章节清单用 enabled 开关（manifest 可翻转），门禁只校验启用章节——项目形态差异（有无 UI/缓存/灰度）靠开关表达，不靠多套模板。
- **H2 精确标题匹配**：章节完整性门禁以 `MANIFEST_DEFAULTS` 常量为单一事实源 + `test_templates.py` 断言模板文件同步，双保险防漂移；不用 HTML 注释标记（不污染用户文档）。
- **design 确认零 open 零 deferred（严格）**：延期问题要么答掉、要么裁剪进 IN/OUT（范围也是决策）。详细设计里藏未决决策是"谎言工厂"——写下的每个行为都没有隐藏分支。
- **frontier 动态计算、round 只是快照**：q-add 时记结构深度（排序展示用）；frontier 每次实时按 status 算（重答/延期会改变可问集合但 round 不变）。SKILL.md 已注明两者语义差异。
- **mermaid 只做围栏配平 + 类型白名单 + 复杂度 WARN**：stdlib 无 mermaid 解析器；语法级校验接 mmdc 留待后续（非 stdlib 排除首版）。
- **kb.primary 是默认建议不是强制**：偏好序 codegraph>repowise>pwiki>wiki>graphify-out 只是缺省，SKILL.md 明示 LLM 可按问题性质改用任一可用源。
- **增量修订模式未实现但未堵死**：ledger.version、history 事件流、docs.status（可扩 stale）、questions（可扩 superseded_by）均为扩展点；首版只支持全新编写。
- **命名/码值规范走 references 与调研面而非门禁**：markdown 表格里的标识符无法与目标库惯例可靠机械比对，误报会侵蚀门禁可信度，故 check 不校验命名，三件套代替——references/coding-standards.md 基线（write-time 指引 + SKILL.md 硬约束 9）、kb-scan 规范文档探测/登记（kb.standards_docs 入台账可断点续跑，手动项重扫保留；不参与 primary 评选——规范是通读型输入不是检索引擎）、question-dimensions 把命名惯例定性为"先查的事实"。

## 已知边界

- confirm 无法验证"用户真的说过确认"——与 sensors-analyze 的 `# CONFIRMED` 同信任级别：靠 SKILL.md 硬约束 + `--because` 审计留痕。
- openwiki 可用性只能靠 `kb-scan --openwiki` 自报（脚本无法探测 MCP），漏报只影响台账记录完整性，不挡流程。
- 复杂度估算是粗估（按箭头与端点 token 计数），只用于 WARN 提醒拆图。
