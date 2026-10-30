# 验收清单（预登记）— code-review-p v2.0 协议引擎 @ ping-skills `7ddfd3a..c52de81`

> 登记时间：运行之前（2026-10-30）。目标 = v2.0 提交自身（44 文件，+6351/−411，
> 引擎 `scripts/crp/` + 协议 schema + 测试 + 文档同步）。
> 执行者：主会话（引擎构建者本人 —— 与 v1.2 的"干净子代理"不同，如实记录；
> 判断质量信号因此降权，流转/协议判据不降权）。运行命令：
> `crp.py start --mode deep --target 7ddfd3a..c52de81`，载体 1（Workflow 脚本）。
> 基线：HEAD=c52de81，porcelain 干净；验收后 porcelain 必须仍干净（B1）。

## A. 协议流转判据（v2.0 核心 —— 本轮验收的主要对象）

| # | 判据 | 证据源 | 通过条件 | critical |
|---|---|---|---|---|
| A0 | 引擎接管范围：range 目标解析、stats 计数 | start 协议块 | scope_command=`git diff 7ddfd3a..c52de81`；files=44（untracked 累积为空）；stats insertions/deletions 与 git 一致 | ✓ |
| A1 | 协议零偏离：执行者只逐字执行协议块 | 执行者操作记录 | 无绕过 do_work/submit 直接改状态或手改 state.json 的操作；任何偏离如实记入"预期偏差" | ✓ |
| A2 | 载体强制留痕：carrier 提交先于任何扫描工作 | state.submissions 顺序 | submissions 中 carrier 条目先于 candidates/carrier1 | ✓ |
| A3 | journal 摄取：transcript dir 回灌，台账在 Python 重算 | state + 报告 parity_notes | `submit --kind carrier1 --file <transcript-dir 文件>` 成功；parity_notes 为空或可见（不静默） | ✓ |
| A4 | 账目精确：报告每个数字出自台账 | out/report.md vs out/ledger.json | rounds/candidates/dupDropped/refuted/capped-out 与 ledger.json 完全一致；表中 finding 与 kept 名单一一对应 | ✓ |
| A5 | 报告契约：引擎渲染表格/Run 行/Details 六字段/Summary 居末 | 报告全文 | 逐字段在位；报告数字未被执行者改写 | ✓ |
| A6 | 判定链强制：floor/keep/cap/quorum 由引擎执行 | ledger.json 抽检 | 抽 3 条：drop_reason 与规则一致（below_floor/mode_threshold/refuted）；P0/P1 票数≥2 | ✓ |
| A7 | 中断防线（演练前置已过，此处引用） | 本仓验收报告 §kill 演练 | runs 发现 / resume 重入 / SIGKILL 持锁后引擎存活 —— 三项已演练通过 | ✗ |

## B. 只读纪律（critical，任一失败=验收失败）

| # | 判据 | 证据源 | 通过条件 |
|---|---|---|---|
| B1 | git delta | `git status --porcelain` | 与基线一致（验收产物 docs/ 报告为预期新增，登记于运行结束时刻） |
| B2 | 引擎零写入被审仓库 | 运行目录位置 | 全部状态在 `<XDG state>/code-review-p/`，被审仓库无 `.agent/` 等写入 |
| B3 | 无写语义命令 | workflow agents + 执行者 | pytest 可跑（read-only）；无 format/fix/install 类命令 |

## C. 质量抽检（信号层 —— 执行者为构建者，降权记录）

| # | 判据 | 通过条件 |
|---|---|---|
| C1 | 抽 ≤3 条 CONFIRMED 复核 | 引用行真实存在；failure scenario 逻辑成立 |
| C2 | 抽 1 条 REFUTED 复核 | 反驳引用确实驳倒原候选 |

## D. 成本记录（记录层）

agent 总数（预算 104）、rounds、token 量级、墙钟。

## 预期偏差（如实记录）

- 执行者=构建者：brief/验证带先验知识，C 层信号降权；A/B 层不受影响。
- 评审对象含执行者刚写的代码 —— finder 可能在测试/文档上产出低价值候选。
- 载体 1 的 workflow 内部自跑 gap 轮（设计如此），引擎侧不再开 gap。
