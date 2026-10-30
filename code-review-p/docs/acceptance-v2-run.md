# 验收报告 — code-review-p v2.0 协议引擎 @ ping-skills `7ddfd3a..c52de81`（自食验收）

日期：2026-10-30。清单（预登记）：`docs/acceptance-v2-checklist.md`。
执行者：主会话（引擎构建者本人 —— 非干净子代理，C 层信号降权，A/B 层不降权；如实记录）。
运行：`crp.py start --mode deep --target 7ddfd3a..c52de81`，载体 1（Workflow 脚本 + journal 回灌）。
Run ID：`crp-20261030-133633-6304`（`<XDG state>/code-review-p/ping-skills-d53ce8a1/`）。

## 一、总体结论：**通过（附回修清单）**

- **A 协议流转 7 条：7 ✓**。引擎端到端驱动全流程零手工干预；报告每个数字与台账精确一致；
  判定链（floor/keep/cap/quorum）抽查全部合规。
- **B 只读纪律 3 条：3 ✓**。被审仓库 git delta 仅预登记的验收文档（`?? docs/acceptance-v2-checklist.md`）；
  引擎全部状态在 XDG state（仓库外零写入）；无写语义命令执行（pytest 只读门禁 115 passed 由
  finder 引用为证据）。
- **C 质量抽检：抽 3 条 CONFIRMED 成立**（c057 overlay 死锁经 finder 沙箱实证复现；c017
  连字符错配逐链引证核对；c049 队列覆盖丢失对照 grep 核对）。REFUTED 2 条（c032/c037）引用可查。
- **审查产出：7 条 P0/P1 + 13 条 P2/P3 入榜（45 kept，cap 20，截断 25）** —— 审查对象是引擎自身，
  发现的全部是真实缺陷，含两条 P0。**这正是自食验收的目的：v1 时代这些缺陷同样存在，
  但没有任何机制保证它们被系统性找出。**

## 二、判据明细

| # | 判定 | 证据 |
|---|---|---|
| A0 范围 | ✓ | start 块 scope_command=`git diff 7ddfd3a..c52de81`；45 文件=44 提交+1 untracked（验收清单文件被正确累积）；stats 6397/411 与 git numstat+行数吻合 |
| A1 协议零偏离 | ✓ | 执行者只执行协议块命令（submissions 序列 brief→carrier→carrier1→details 与协议路由一一对应）；无手改 state.json；无绕过提交 |
| A2 载体留痕 | ✓ | `state.submissions` 中 carrier(2) 先于 carrier1(3)；carrier 记录含 reason 与 max_agents |
| A3 journal 摄取 | ✓✳ | transcript dir 单行文件提交成功；台账在 Python 重算（64/12 对 JS 内部 66/30 —— 分歧见"观察项"）；parity_notes 为空系 transcript 模式无对照计数路径（设计如此，非静默篡改） |
| A4 账目精确 | ✓ | 报告 Run 行 rounds=1 · candidates=64 · dupDropped=12 · refuted=2 · capped-out=25 与 out/ledger.json 逐字段一致；表 20 行=cap |
| A5 报告契约 | ✓ | 引擎渲染表格/Run 行/Details 六字段/Open questions/Summary 居末；数字未被执行者改写 |
| A6 判定链 | ✓ | below_floor 5 条全部 conf<0.5 且 CONFIRMED；refuted 2 条均带引用；P0/P1 中 7 条 3 票 CONFIRMED、c049 单票（refuter 429 阵亡→预算兜底 PLAUSIBLE，deep 合法保留）、c057 零票（见观察项） |
| A7 中断防线 | ✓ | 前置演练：中断后 `runs` 发现+`resume` 重入（interrupted.count=1）；持锁进程 SIGKILL(exit 137)后引擎存活续跑 |
| B1 git delta | ✓ | 仅 `?? code-review-p/docs/acceptance-v2-checklist.md`（预登记）；验收报告本身为运行后预期产物 |
| B2 仓库零写入 | ✓ | 全部状态/提交物在 `<XDG state>/code-review-p/`；workflow agents 工具面只读（1898 次工具调用无写仓） |
| B3 无写语义命令 | ✓ | 无 format/fix/install 执行 |

## 三、运行账目与成本

- 协议调用：start → submit brief → submit carrier → submit carrier1(transcript) → submit details → finish，共 5 次脚本调用 + 1 次 Workflow（103 agents：96 成功 / 7 个 429 阵亡）。
- Workflow：rounds=1（预算 103/104 耗尽，gap 轮被预留检查正确阻止——降级已记录）；66 候选/30 dup（JS 口径）。
- 引擎重算：64 入账（52 fresh + 12 dup）/ 45 kept / 20 入榜 / 25 截断。
- 成本：5.53M subagent tokens，墙钟约 58 分钟，1898 次工具调用。

## 四、发现清单（回修对象；全部有引证，细节见 out/ledger.json）

**P0（2）**
1. **c057 overlay 阶段死锁**：全代码库无路径把 `cursor.awaiting` 设为 `"overlay"`（cmd_start/cmd_answer/_new_state 均留空）→ 有 overlay 或启发式门禁的目标仓库在第二个协议步即进入无限 exit-3 循环；且 `apply_overlay` 不推进 phase。finder 沙箱实证复现。**根因：测试夹具仓库无一含 Makefile/overlay（覆盖盲区）。**
2. **c025 子目录调用下的 overlay 信任误判**：`overlay.discover(原始 cwd)` 而 scope 路径顶层相对 → 子目录植入的 overlay 被判 trusted（A3/A4 被绕过：排除项被应用、自declare 的只读门禁得 auto）。

**P1（5）**
3. **c017** `fix_outcomes`(下划线) vs `fix-outcomes`(连字符)：--fix 流程必然死循环，无 CLI 逃生口。
4. **c049** 分角度多次提交只把**最后一次**的 fresh 入验证队列（`build_verify_queue` 覆盖而非累加）——早前提交的候选静默消失于报告。
5. **c018** journal 摄取不应用升级规则（refuter 升 P0/P1 后仍按 finder 严重性单票终裁）。
6. **c051** refuter 票据回配只做 file+line 精确匹配（无 ±3 带/摘要重叠）——**本次运行 c057 零票即其现场发作**（票据匹配落在 dup 副本上）。
7. **c023** quick+carrier1 语义分叉（`_finalize_all` 对 quick 也走 mode 阈值而非 floor-only）。

**P2 主题（13 条，摘要）**：answer run 键锁内调用（c009）；commit_log 取值时序（c020）；`lstrip("./")` 字符集语义（c022）；cmd_answer 复制 overlay 初始化丢分支（c001）；scope.md 受限阶梯次序描述与实现不符（c052）；--fix 零测试覆盖（c043）；幂等键语义未文档化（c041）；argparse 错误不经协议块（c038）；退出码契约仅一断言（c046）；损坏 state 的 CLI 级契约未测（c048）；journal 夹具非真实形状（c042）；gitops locale 解码（c012）；package.json 解析无类型防御（c015）。

## 五、观察项（不计缺陷，回修时对齐）

1. **JS/Python 台账计数口径分歧**（66/30 vs 64/12）：两侧去重顺序与票据回配语义（c051）共同所致；transcript 模式无 parity 对照路径，分歧未上报告。回修后应以共享夹具+transcript 模式 parity 注记锁死。
2. workflow 预算耗尽未跑 gap 轮（设计内降级，coverage 记账 E 角度 ran:false）。
3. 7 个 agent 429 阵亡的处理全部符合契约（finder→coverage hole；refuter→单票/零票 PLAUSIBLE 保留或 inline 补证缺失）。

## 六、回修与遗留

**回修已当日完成**（同会话）：c057/c025/c001（overlay 阶段三连：`_init_overlay_state`
共享初始化 + awaiting="overlay" + apply 后推进 phase + 顶层发现）、c017（fix-outcomes
统一连字符）、c049（验证队列累加语义）、c018（journal 升级走高危 quorum）、c051（票据
回配 ±3 带 + 摘要重叠）、c023（quick+carrier1 走 floor-only）、c009（answer run 不再
锁内入锁）、c020（commit_log 取值时序）、c022（前缀剥离语义）。每条配回归测试 8 个
（有 Makefile 仓库 e2e、trusted overlay 排除 e2e、子目录调用、--fix 全链路 e2e、队列
累加、quick+wf、journal 升级、answer run 防死锁），套件 115 → 123 全绿。

遗留（下轮）：P2 其余（c038 argparse 不经协议块、c043/c046/c048 契约测试缺口、
c041 幂等键文档、c052 scope.md 次序描述、c012 locale 解码、c015 解析防御）与
观察项 1（JS/Python 计数口径 —— c051 修复后需复跑对照）；P3 由 ledger.json 存档。

## 附：证据指针

- 运行目录：`<XDG state>/code-review-p/ping-skills-d53ce8a1/crp-20261030-133633-6304/`
  （state.json / out/report.md / out/ledger.json / in/ 全部提交物）
- Workflow：Run ID `wf_4e0a4596-728`，transcript `…/subagents/workflows/wf_4e0a4596-728/journal.jsonl`
- kill 演练：隔离 CRP_HOME=/tmp/crp-m8-drill（已清理），过程记录见验收会话
