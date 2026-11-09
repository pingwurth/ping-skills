# design-doc-writer max 审查报告（2026-11-09）

> **回修状态（2026-11-09 当日）**：39/39 全部回修，分四批提交：
>
> | 批次 | 提交 | 覆盖 |
> |---|---|---|
> | ① 门禁完整性/死锁逃逸/假 FAIL | `e2868a3` | A1–A9、B1–B4、C1–C3（E3 并入 A2 逐行替换） |
> | ② 协议契约与健壮性 | `db6478b` | D1–D6、E1、E2、E4、E5 |
> | ③ 测试缺口 | `e1ea9a9` | F1、F2 |
> | ④ 清理与效率 | `79b55bd` | G1–G10 |
>
> 每批附回归测试（test_gate_hardening / test_contract / test_cleanup 及既有套件改造），
> 回修后套件 **126 全绿**（基线 83 + 新增 43）。

- **范围**：`design-doc-writer/` 全目录（提交 `4d576b6`，工作树干净；基线 83 测试全绿）
- **方法**：10 个查找角度 + 差距扫描；原始候选约 58 条 → 去重后 39 条
- **验证强度**：22 条在 /tmp 草稿仓库或进程内以真实 CLI 复现验证，其余经代码/文档事实核验；无 PLAUSIBLE 保留项
- **模式**：`--max-findings all`（全量列出）
- **行号**：均基于回修前的 `scripts/dd.py`（4d576b6）——下文行号对应审查时点代码

结论概览：

- (a) 门禁完整性漏洞——围栏代码块使章节/状态行门禁失明、全角冒号标记不可见、```` ```Mermaid ````绕过白名单、intake 阶段可直接 confirm 绕过整个访谈；
- (b) 无退路死锁——过期 ADR 分配、confirm 状态行同步失败后无法回退、finish 把延期问题封进 done；
- (c) 合法文档被假 FAIL 挡住 confirm——%% mermaid 指令、`待定` 子串、ADR 目录年份；
- (d) 协议契约违反——setup_logging 原始 traceback 退出 1、脚本自身 next_step 给出非法阶段转移、修复指导循环。

---

## A. 门禁完整性（不该确认的被放行）— 9 条

### A1. `scripts/dd.py:431` 章节门禁不感知围栏代码块（last-match-wins）
- **摘要**：章节门禁的标题提取不感知围栏代码块且取“最后出现者胜”，必需 H2 只要出现在围栏内（如引用的模板骨架），「章节缺失」与「章节为空」两项检查都被满足。
- **失效场景**：实测——分析文档真实「## 术语表」为空、末尾 ```markdown 围栏里含 `## 术语表`，`check --doc analysis` 输出「合计：PASS 0 / WARN 0 / FAIL 0」，`confirm` 成功，交付了强制章节为空的文档。

### A2. `scripts/dd.py:510` 状态行门禁不感知围栏，confirm 会改写围栏内示例
- **摘要**：门禁 ⑦ 对全文做首次 `STATUS_LINE_RE.search` 且不感知围栏；cmd_confirm 的 `sub(count=1)` 同样不感知——围栏内 `状态: Draft` 被当作满足门禁，confirm 随即改写围栏内的行，文档本身始终没有真实状态行。
- **失效场景**：实测端到端——文档在 ```markdown 围栏内引用 doc-templates 骨架（内含 状态: Draft），H1 下无真实状态行 → check exit 0 且零检查项；confirm 成功，dd.py:1053 的 sub 把围栏内行改写为 状态: Confirmed（损坏引用示例）；ledger=confirmed，后续所有检查靠围栏内 Confirmed 行保持通过。

### A3. `scripts/dd.py:97` 全角冒号标记 [未决：]/[延期：] 对门禁不可见
- **摘要**：UNRESOLVED_RE/DEFERRED_RE 只接受半角冒号，而 STATUS_LINE_RE 刻意同时接受 `[:：]`——中文输入法自然的全角标记既造成假 FAIL 也造成静默绕过。
- **失效场景**：实测双向——(1) Q001 已延期、文档写 `- [延期：Q001]`（全角）→ check FAIL「台账延期但文档未标注：Q001」且差异清单为空；(2) 全部已答但文档残留过期 `[未决：Q001]`（全角）→ 未决标记门禁失效，check exit 0、confirm 成功，交付文档断言“未决”而台账为已答——正是该门禁要阻止的谎言；design 的 deferred 零容忍 FAIL 同样被全角形式绕过。

### A4. `scripts/dd.py:353` ```Mermaid / ``` mermaid / 四反引号围栏被静默跳过
- **摘要**：_mermaid_blocks 只把 strip 后恰为 ```mermaid 的围栏认作 mermaid 块，大小写/空格/四反引号变体被跳过——类型白名单与复杂度检查被绕过，而围栏平衡 ② 仍把它们计为平衡。
- **失效场景**：实测——含 ```Mermaid 围栏 `pie` 图的文档零 mermaid-type FAIL，check exit 0、confirm 放行，被禁图型在门禁无察觉下通过，交付渲染时才暴露。

### A5. `scripts/dd.py:1022` confirm/finish 不校验 ledger 阶段，访谈可被整体绕过
- **摘要**：cmd_confirm 与 cmd_finish 从不校验 ledger 阶段（PHASE_EDGES 只有 `phase` 子命令查），analyzing→grilling→frontier 轮次全可跳过：phase=intake、questions=[] 也能 confirmed→done。
- **失效场景**：实测——全新 ledger（phase=intake, questions=[]）→ 写两份完整文档 → `confirm --doc analysis --because x`、`confirm --doc design --because x`、`finish` 全部 exit 0，phase=done——零问题、零轮次，全程未触发「非法状态流转」exit 2。

### A6. `scripts/dd.py:565` confirm design 不复检已确认的 analysis
- **摘要**：run_checks(which='design')——confirm --doc design 实际跑的路径——从不重跑 check_doc('analysis')，design-prereq 只信过期的 ledger 状态位，确认后劣化的 analysis 永不阻挡 design 确认。
- **失效场景**：实测——confirm analysis → phase design-doc → SKILL.md 阶段 3 明确允许的增量编辑删掉「术语表」并加 [未决:Q001]/TODO → `check --doc design` 与 `confirm --doc design` 都通过 → finish 交付了两份 Confirmed 文档，其中分析文档并发违反章节缺失、未决标记、占位符门禁。

### A7. `scripts/dd.py:934` manifest 无 phase=done / confirmed 守卫
- **摘要**：cmd_manifest 缺 q-add/q-answer 拥有的 done 终态守卫（也无任何 confirmed-doc 守卫），可在交付后裁剪已完成流程的章节清单，追溯削弱确认时依据的门禁。
- **失效场景**：实测——finish 后（phase=done，两份文档已在完整清单下确认）`manifest --disable tdd,db,nonfunc --reason 随便裁` exit 0 重写 ledger；check（done 阶段推断 'all'）对缺 TDD/DB/非功能章节的设计文档通过；test_done_phase_rejects_writes 只盖住 q-add/q-answer。

### A8. `scripts/dd.py:1066` finish 不重跑 run_checks，延期问题可被封进 done
- **摘要**：cmd_finish 只复查两个 docs.status 位而不重跑 run_checks，且 q-add / q-answer --defer 在 design-doc 阶段仍合法——finish 可以 phase=done 之姿包裹未决/延期问题。
- **失效场景**：实测——confirm design（此时门禁强制零未决/零延期）→ `q-add`（design-doc 中允许）+ `q-answer Q002 --defer`（允许）→ `finish` exit 0、phase=done 且 Q002 延期；此后 q-answer exit 2「phase=done 已终态」、PHASE_EDGES["done"] 为空，无任何命令可修复——「design confirmed ⇒ 零延期」不变量在终态被打破。

### A9. `references/doc-templates.md:8` 承诺替换的省略号 `……` 不在 PLACEHOLDERS
- **摘要**：doc-templates.md 承诺「所有示例省略号 `……` 落笔时必须替换为实质内容」，与门禁强制的占位符禁令并列在同一要点，但 `……` 不在 PLACEHOLDERS 中——遗留的模板省略号行通过所有门禁。
- **失效场景**：LLM 逐字复制骨架的 IN/OUT 行「| …… | …… |」（或决策表 Q002 | …… 行）→ check 无占位符命中 → exit 0 → 带 `……` 占位内容的文档被 Confirmed 交付，静默违反同一要点里被视为“强制执行”的要求。

## B. 无退路死锁 / 状态损坏 — 4 条

### B1. `scripts/dd.py:293` next_adr_number 只看文件系统，两个 --adr 都分到 0001
- **摘要**：编号只从文件系统导出、从不查已分配给 ledger 的路径；在写入第一个 ADR 文件之前（写文件只是建议性 next_step），两个带 --adr 的 q-answer 都会被分到 0001。
- **失效场景**：实测——两个已答的三重门决策背靠背 → ledger 记 Q001→docs/adr/0001-cache.md、Q002→docs/adr/0001-auth.md；check ⑧ 的 adr_files 以 "0001" 为共同键，单条 ADR-0001 引用同时满足两者审计，confirm/finish 通过（编号已撞）；若用户只按首个 next_step 写了第一个文件，Q002 永久 FAIL adr-file、要求一个不存在的“第二个 0001 文件”。

### B2. `scripts/dd.py:852` q-answer 不清 adr、无 --no-adr：过期 ADR 分配无退路
- **摘要**：q-answer 从不清 q["adr"] 也无 --no-adr 选项；ADR 分配后紧随的 --defer 或重新回答会留下 adr.needed=true 与过期路径，被删除或矛盾的 ADR 文件造成永久 adr-file FAIL，任何脚本命令都无法逃出。
- **失效场景**：实测——`q-answer Q001 --answer A --adr --adr-slug export` 后决策反转（`--answer B` 不带 --adr，或 `--defer`）→ 用户删除矛盾的 docs/adr/0001-export.md → check 永久 FAIL「adr-file: Q001 分配了 ADR 但文件不存在」；--defer 只清 answer/answered_at，重答不带 --adr 保留旧路径，唯一退路是被硬性规则 3 禁止的手改 ledger；被延期但已分配 ADR 的问题在 frontier/status 仍计「ADR 已分配」。

### B3. `scripts/dd.py:1060` 状态行同步失败被吞仍报成功
- **摘要**：状态行同步的 OSError 只被作为日志警告吞掉，随后仍无条件打印「状态行已同步为 Confirmed」并 exit 0——ledger 显示 confirmed 而文档仍是 Draft。
- **失效场景**：实测端到端——文档 chmod 444 → `confirm --doc analysis --because ok` exit 0、打印已同步，文件仍 状态: Draft；下一次 check FAIL status-line「状态行为 Draft，台账为 confirmed」，其提示「勿手改漂移」把工具静默吞掉的同步失败归咎于用户。

### B4. `scripts/dd.py:1055` 无 confirmed→draft 回退路径；confirm 非原子改文档
- **摘要**：对任何文档/ledger 状态不匹配都无重置或恢复路径；且 confirm 在 save_ledger 之前用普通非原子 write_text 修改交付文档——同步失败即把流程逼进只能靠被禁手改自救的死锁。
- **失效场景**：实测（重开死锁）——confirm analysis → 合法 `phase grilling` 回边 → 作者按模板惯例重写 状态: Draft → check 永久 FAIL status-line，`confirm --doc analysis` 被拒「门禁未过，文档保持 draft」（ledger 却是 confirmed），无子命令能重置 docs.*.status 或把该行回同步为 Draft。另一故障面：1055 行崩溃或 ENOSPC（在 1059 save_ledger 之前）留下 文档 Confirmed/ledger draft；普通 write_text 可因截断使交付物减半而 ledger 已记 confirmed（save_ledger 自身的 tmp+fsync+os.replace 模式没有用在文档写入上）。

## C. 合法文档被假 FAIL 挡住 confirm — 3 条

### C1. `scripts/dd.py:365` _mermaid_type 不跳过 %% 注释 / %%{init} 指令
- **摘要**：取第一个非空行的首 token 作图类型，不跳过 `%%` 注释或 `%%{init:...}%%` 指令（_complexity 已跳过），合法白名单图被假 FAIL mermaid-type，永久阻止 confirm。
- **失效场景**：实测——```mermaid 块以 `%%{init: {'theme':'dark'}}%%`（或任意 %% 注释行）开头再 `stateDiagram-v2` → 「[FAIL] mermaid-type: 图类型 `%%{init:` 不在白名单」，check exit 1、confirm 被拒；提示「按 references/mermaid-uml.md 改型」指向错误修复方向（同族 bug 在 -v2 后缀案例出现过，已有回归锁定，此处漏防）。

### C2. `scripts/dd.py:580` ADR 审计用未锚定 (\d{4})- 重解析路径：目录年份冒充编号
- **摘要**：run_checks 的全局 ADR 审计对分配路径做 `re.search(r"(\d{4})-")`，年份相似的 adrs-dir 片段会替代真实编号，完全正确的文档/docset 产生假 FAIL adr-ref；同一区域两套解析——check_doc ⑧b 解析文件名用的是锚定的 ADR_FILE_RE。
- **失效场景**：实测——`init --adrs-dir docs/2024-adr` → q-answer --adr 分配 docs/2024-adr/0001-async.md → 文件已写入、ADR-0001 已被决策表引用 → `check --doc analysis` FAIL「ADR-2024（Q001）未被任何决策表引用」（引用的 0001 明明存在），confirm 被假门禁卡死。

### C3. `scripts/dd.py:503` 占位符裸子串匹配误伤正常中文/标识符
- **摘要**：占位符检测对整个文档文本做裸子串匹配，含“待定”二字的相邻正常中文（等待定时、期待定制）或嵌入标识符的 TODO 会假 FAIL placeholder 并阻止 confirm。
- **失效场景**：实测——分析文档含「系统等待定时任务完成后回调」→ 「[FAIL] placeholder: 存在占位符残留：待定」exit 1，confirm 被拒，作者被迫改写合规内容；英文「……/TODOS」同理。

## D. 协议契约违反 — 6 条

### D1. `scripts/dd.py:1174` setup_logging 在 try 外：IO 错误原始 traceback 退出 1
- **摘要**：setup_logging 在 main() 的 try 块之外调用，其 OSError（仓库只读、--log 指向目录、父目录不可创建）以原始 traceback 退出 1 且无 next_step 行——违反 IO 错误 exit 2 的约定，而退出码 1 被文档定义为「正常流转信号，非失败」。
- **失效场景**：实测——只读仓库上执行 `dd.py status --repo <readonly>`，或 `--log /some/existing-dir` → 未捕获的 PermissionError/IsADirectoryError traceback，exit 1，无 错误: 行、无 next_step: ——协议驱动方会把 1 读作正常流转信号且拿不到任何恢复指令。

### D2. `scripts/dd.py:135` status 探针在任意路径（含拼写错误路径）创建 .agent/ 树
- **摘要**：mkdir(parents=True) + FileHandler 创建先于任何子命令与台账校验，文档定位为“只读阶段 0 探测”的 `dd.py status` 会在所指路径（包括不存在的错拼路径）里生成 .agent/design-doc-writer/dd.log。
- **失效场景**：实测——`dd.py status --repo /tmp/<typo>` 在日志 setup 期间以 parents=true 创建 /tmp/<typo>/.agent/design-doc-writer/dd.log 然后才退出「台账不存在」；SKILL.md 阶段 0 要求每次会话探测目标仓库 → 每个无 ledger（或拼错）的仓库在决定是否开工之前就被塞进一棵 .agent/ 目录树——纯存在性检查带副作用，在错拼位置留下垃圾目录。

### D3. `scripts/dd.py:909` next_step 不感知阶段：脚本自己的建议命令会 exit 2
- **摘要**：frontier 的终止分支与 confirm 的成功分支所规定的阶段转移会被 PHASE_EDGES 自身拒绝（exit 2）；confirm 的三个人写自动推进特例只对精确的当前阶段等价性触发，回流后卡死。
- **失效场景**：实测——零问题的 phase=analyzing（或合法 grilling→analyzing 回边后）frontier exit 0 并建议「确认后 dd.py phase analysis-doc」→ 执行即 exit 2「非法阶段转移：analyzing → analysis-doc」；design 确认后仍处 analysis-doc 时 confirm 把阶段留在 analyzing，其 next_step `dd.py phase design-doc` 同样 exit 2；design-doc/done 中 frontier 仍建议 phase analysis-doc（非法）。照脚本字面指令行事的 Agent 必撞 exit 2 并被迫即兴编恢复链。

### D4. `scripts/dd.py:801` q-add@intake 的修复指导循环
- **摘要**：q-add 在 phase=intake 的修复指导（「先 dd.py kb-scan 并做事实调研」）是循环的：kb-scan 从不改阶段，按脚本自身 next_step（权威高于 SKILL.md）行事的 Agent 会陷入无限循环；唯一解锁 `dd.py phase analyzing` 只记录在权威链更低的 SKILL.md 阶段 2。
- **失效场景**：实测——q-add@intake → exit 2 + 该 next_step → kb-scan 成功、阶段仍 intake → q-add 同样 exit 2，循环。

### D5. `scripts/dd.py:1170` argparse 错误：stdout 全空，未记录的协议偏差
- **摘要**：SKILL.md 承诺「所有子命令 stdout = 摘要 + 末行唯一一条 next_step:」，但 argparse 级错误（--shape 拼错、缺 --feature/--because）stdout 全空、usage 走 stderr、exit 2——真实存在但未记录的偏差。
- **失效场景**：`dd.py init --feature x --shape mobile`（拼错）→ argparse usage 到 stderr、exit 2、stdout 空；只读 stdout 的消费者什么都看不到，也无 next_step 可恢复。（该排序是刻意的——1168 行注释保证 usage 可见——但偏差未写进协议/退出码表。）

### D6. `scripts/dd.py:141` 日志双句柄互相覆盖：stderr 诊断被静默销毁
- **摘要**：日志文件被两个独立句柄持有——FileHandler(mode='w'，维护自己的偏移) 与重定向 stderr 的 open(...,'a')——EOF 处追加的 stderr 写入会被后续 FileHandler 在过期偏移处的写覆盖，stderr 诊断从 console 与日志双双丢失。
- **失效场景**：进程内实测——LOG.info → 写一行 sys.stderr → 再 LOG.info，日志文件只含三条干净 INFO 行，stderr 突发消失；中途发 Python warning 或异常打印的库在两个 sink 都不可见且无任何信号。更便宜/安全的做法：单句柄（sys.stderr = fh.stream，或 os.dup2）。

## E. 健壮性 / 误导性报错 — 5 条

### E1. `scripts/dd.py:188` validate_ledger 清单不全：部分损坏被误报为「引擎 bug」
- **摘要**：validate_ledger 是手工维护的清单，漏掉随后被无守卫解引用的字段——docs.*.path、manifest.sections[] 项形状、kb、question.round/adr 形状、history 条目形状——部分损坏的 ledger 能通过加载再以 KeyError/TypeError 崩溃，被报成「未预期异常…这是 bug 请回报」。
- **失效场景**：实测——从 ledger.json 删 docs.analysis.path（或把 manifest.sections 变字符串、删 kb）→ validate_ledger 通过 → status（指定的断点恢复入口）与 check/confirm 以 exit 2「错误: 未预期异常：'path'…这是 bug 请回报」崩溃——绕过设计的「台账结构非法…备份后重新 init」损坏通道，把手动编辑误导成工具 bug 报告。

### E2. `scripts/dd.py:421` 只捕获 OSError：非 UTF-8 文件走 bug 通道
- **摘要**：文档/ledger/批处理读取只捕获 OSError，而 read_text(encoding='utf-8') 对非 UTF-8 输入抛 UnicodeDecodeError（ValueError）——GBK 保存的文件走通用「这是 bug」exit 2 路径，而非 FAIL 项或指定损坏通道。
- **失效场景**：实测——Windows 编辑器保存的 GBK 分析文档 → `check --doc analysis` exit 2「错误: 未预期异常：'utf-8' codec can't decode byte 0xd0 …（详见日志）」；`q-add --batch` 读取（815 行）把格式错误的用户输入误标为引擎 bug；ledger 读取（241）与 refs_union 读取（552）会中止整个 run_checks。

### E3. `scripts/dd.py:1053` STATUS_LINE_RE 的 \s*$ 吞掉状态行后的空行
- **摘要**：MULTILINE 下 \s*$ 可消耗尾随换行（回溯使 $ 匹配到下一个 \n 之前），confirm 的 sub(count=1) 会删掉状态行后的空白分隔行，静默重排用户已签核的文档。
- **失效场景**：实测——`状态: Draft\n\n## 背景与目标` 在 confirm 后变为 `状态: Confirmed\n## 背景与目标`（\n\n\n → \n），每次确认/重新确认都吃掉已确认文档状态行下的段落空行。

### E4. `scripts/dd.py:620` _agent_ignored 漏 /.agent 与 **/.agent 语义
- **摘要**：把每行 .gitignore strip().rstrip('/') 后与字符串 '.agent' 精确比较，漏掉按 git 语义等价的 `/.agent` 与 `**/.agent` 模式，init 仍提示添加已经存在的覆盖。
- **失效场景**：实测——.gitignore 含 `/.agent` 或 `**/.agent` 的仓库（两者都忽略 .agent/）仍收到「提醒：.agent/ 尚未被 .gitignore 覆盖，建议添加」。

### E5. `SKILL.md:131` 命令速查行照抄即 exit 2
- **摘要**：速查行 `dd.py manifest --enable ui,cache | --disable release --reason "<t>"` 记录了仅 --enable 的调用，但 cmd_manifest 即使只给 --enable 也要求 --reason 必填（dd.py:951），完全照抄速查会 exit 2。
- **失效场景**：LLM 照快速参考跑精确的 `dd.py manifest --enable ui,cache` → exit 2（参数错）而非预期的章节切换；只有阶段 5 的更完整形式揭示 --reason 对每次调用都是必填。

## F. 测试缺口 — 2 条

### F1. `tests/test_cli.py:18` 旗舰 e2e 前半程不断言退出码
- **摘要**：e2e 第一个循环只断言末行 next_step（从不 assert 退出码），而 DDError 路径同样以 next_step 行结尾——流程前半部分的状态机回归可以穿过测试套件。
- **失效场景**：若 frontier / q-answer / phase analysis-doc 回归成 exit 2（如 analyzing→analysis-doc 非法转移 bug），test_every_subcommand_prints_next_step_last_line 仍通过：DDError 打印 错误: … 后跟 next_step: 行，末行断言对失败同样成立；只有稍后的 check/confirm/finish 循环 assert code==0——前半段回归不受 83 个测试保护。

### F2. `tests/test_templates.py:13` 模板↔清单锁单向且子串级
- **摘要**：doc-templates.md 与 MANIFEST_DEFAULTS 的「严格同文」锁是单向且基于子串的——只查每个清单标题是否出现在模板中、从不反向，且 `"## x" in text` 会被 H3 `### x` 满足；conftest.py 的 ANALYSIS_H2S/DESIGN_H2S 是第三份副本。
- **失效场景**：在 references/doc-templates.md 新增或重构一个章节为 H3（不动 dd.py）套件保持绿色——LLM 照模板把该章节写进每个文档，而门禁既不要求也不检查它是否为空——永久静默无门禁章节；应解析两份围栏骨架并与 MANIFEST_DEFAULTS 断言双向集合相等。

## G. 清理 / 效率 — 10 条

### G1. `scripts/dd.py:409` check_doc 死参 + 文档被读两遍
refs_union 参数从未在 check_doc 函数体中被引用（死参数，还误导读者以为 check_doc 参与全局 ADR 对账——实际在 run_checks:585）；同一个 check 把每个文档完整读两遍（refs_union 预处理 + check_doc 内部）、ADR 目录每文档扫一遍、question_stats 在每文档循环内重算。修法：文本读一次传入，stats 提到循环外。

### G2. `scripts/dd.py:521` ADR 目录扫描实现两份
iterdir + ADR_FILE_RE.match 建 num→file 映射的块同时存在于 296-301（next_adr_number）与 521-526（check_doc ⑧），每次 check 跑两遍；文件名约定变更时一份被修、另一份漂移（编号冲突或假 adr-ref FAIL）。提取 scan_adr_files() 助手，next_adr_number 表达为其 max+1。

### G3. `scripts/dd.py:770` 六个只写 ledger 字段；--facts 存了取不回
project.repo_root / project.created_at / kb.note / kb.scanned_at / manifest.updated_at 全仓库只写不读（repo_root 在仓库移动后过期且从不被信任，kb.note 是 kb.primary 的纯函数，三个时间戳复制 history[] 已审计内容）；question.facts 按 references/question-dimensions.md「结果记 --facts」存入，但 frontier/status 从不显示——LLM 必须手读 script-only ledger 才能拿回自己的事实。

### G4. `scripts/dd.py:1011` PASS 级从不发出
全文件零 Item("PASS", ...)，cmd_check 永远打印「合计：PASS 0 / …」，SKILL.md 阶段 4 承诺的逐项 PASS/FAIL/WARN 未兑现——阅读者看不到哪些章节/检查被验证为 OK，且易把「PASS 0」误读为未验证任何内容。

### G5. `scripts/dd.py:812` 静默丢弃 flag 组合
--batch 时 q-add 静默丢 --dimension/--title/--body/--recommendation/--depends-on/--facts；q-answer 静默忽略不带 --defer 的 --reason 与不带 --adr 的 --adr-slug。实测 `--answer 决策 --reason 说明` 记录答案丢原因、零警告。补三个短互斥守卫（对齐 --answer/--defer 互斥检查）让无效组合 exit 2 大声失败。

### G6. `scripts/dd.py:774` 每问全量 DFS 防环 O(B·(N+B))
_add_one_question 对每个添加的问题跑完整 _assert_acyclic DFS + q_by_id 重建 + next_qid 正则扫描，而依赖已验证只引用现存 ID——新节点不可能成环（773 行注释自认）。200 问 --batch 在 200 问 ledger 上跑约 10⁵-10⁶ 次操作防一个结构性不可能的条件；graphlib.TopologicalSorter({...}).prepare() 一次迭代替代，其 CycleError 还带完整环路径（手工 DFS 丢弃了），深依赖链上还避开 RecursionError。

### G7. `scripts/dd.py:107` 日志块近乎逐字复制 sensors-analyze/utils.py 且已分叉
50 行日志/stderr 重定向块是 sensors-analyze/scripts/utils.py 的近乎逐字复制，丢掉了同源的 next_step() 与 original_stderr() 助手并内联自己的 next_step 打印——sensors 的 next_step() 以 flush=True 打印，dd.py:1189 内联 print 无 flush，同一「单尾 next_step 行」契约两种形状；抽到 design-doc-writer/scripts/utils.py 镜像模块面（setup_logging/next_step/original_stderr），下一个同源修复成为文件 diff 而非重嵌入。

### G8. `scripts/dd.py:890` frontier/status 复制粘贴同一概要块
cmd_frontier 与 cmd_status 重复相同的标题行、统计行与 frontier/blocked 列表（统计行由两个不同的 f-string 切分构建）；摘要格式任何变更要改两处，单边编辑让两个断点恢复入口命令对同一 ledger 报告不同。抽 _print_overview(ledger, frontier, blocked)。

### G9. `scripts/dd.py:907` frontier「理论上不会走到」的 open>0 分支是死代码
compute_frontier 把每个开放问题划入 frontier 或 blocked（悬空依赖在分类前已过滤），较早的 return 必先触发；死分支迫使读者每次重新推导分区论证，其模糊回退会掩盖 compute_frontier 契约中的真实 bug。删除或改为 assert。

### G10. `tests/test_cli.py:7` 与专用套件重复覆盖
e2e next_step 测试走 test_confirm.py::test_full_chain_confirm_design_then_finish 同一条 confirm-analysis→phase→confirm-design→finish 链；test_check_exit_codes 复刻 test_check_gate.py 的通过/未决对——每个门禁行为变更要动 2-3 处站点并重复整套 doc-fixture 写入；test_check_gate.py 的 answer_all 助手带从未使用的 repo 参数。test_cli 收敛到其声明的契约（next_step 末行、退出码分类、--repo/--log 位置）。

---

## 建议回修顺序（已按此执行，见顶部回修状态）

1. **A + B + C（正确性核心）**：门禁感知围栏/全角冒号/围栏变体、阶段守卫、finish 复检、ADR 编号与分配生命周期、confirm 原子性与失败处理、三处假 FAIL；
2. **D + E（协议契约与健壮性）**：setup_logging 进 try、探针无副作用、next_step 阶段感知、UnicodeDecodeError 通道、validate_ledger 补全、速查行修正；
3. **F（测试锁）**：e2e 断言退出码、模板↔清单双向锁；
4. **G（清理）**：可按条独立处理。

每批回修后跑 `cd design-doc-writer && python3 -m pytest tests/`（83 基线）+ 新增回归测试。
