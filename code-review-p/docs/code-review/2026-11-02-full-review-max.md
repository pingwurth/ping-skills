# code-review-p 全面审查报告(/code-review max)

- **日期**:2026-11-02
- **审查对象**:`code-review-p/` 全目录(v2.0 协议引擎,基线 commit `2aa2313`)
- **方法**:10 个发现角度(A–E 正确性 + Reuse/Simplification/Efficiency + Altitude + Conventions)并行扫描 → 去重出候选 → 7 个验证批次逐条三态裁决(CONFIRMED/PLAUSIBLE/REFUTED)→ 补漏扫描追加 5 条 → 1 条候选被验证驳回未列入
- **结果**:**64 条经验证的发现**(56 CONFIRMED / 8 PLAUSIBLE)
  (注:审查代理的 prose 自称 57+5=62;对交付 JSON 逐条清点实为 59+5=64,PLAUSIBLE 标记 8 条 —— 以本文件 JSON 为准)
- **测试基线**:技能自带 136 个测试全部通过 —— 以下发现均无现有测试覆盖(多处正是被 e2e fabricator 的兜底逻辑掩盖)

## 优先回修建议(P1 级 6 条)

> **2026-11-02 已全部回修**(与 6 条回归测试一起,142 测试全绿;红灯验证:还原原版代码后 6 条新测试全部失败)。修复口径:
> 1. overlay 应用一律先 brief(SKILL.md §4/gates_question 契约:门禁确认在 brief 之后);answer gates 对旧状态保留 brief 未建防御分支
> 2. sweep_work 的 carrier1 分支排除 fix_review 轮(复审轮固定常规 candidates 提交)
> 3. cmd_answer 顶部错序守卫(scope=gates=drift 各自的合法性条件),拒绝时 exit-3 交回 resume;载体 1 提交补 idem 幂等键(同轮同 journal 字节重投吸收)
> 4. drift new_scope 后重评 overlay 信任:降级时排除项挂起、auto 批复回落 unset(fail-closed),仍可信时对新范围重应用排除项
> 5. validate_votes_file 查 (id,lens) 唯一性(文件内 + 与已入账票跨文件)
> 6. cli.main 在 handler 之前解析 run-dir 并 setup_logger;setup_logger 把 FileHandler 挂到命名空间根,module_logger 告警落 run-dir;start 在 _place_state 内接日志

> **第二批(P2 状态机/生命周期 8 条)同日回修**:①逃逸口 ask 落 pending_question,resume 遇 escape_hatch 重问(不再静默空审)②start --run-dir 指向已有运行防覆盖询问(--force 逃生)③drift new_scope 重导全空走逃逸口重问 ④resolve_run_dir 增加 include_done 回落,done 后裸 report/status/resume 可达(resume 重发 finish 块)⑤cmd_report 拒绝 phase=report(details 未提交时不再空渲染终结审查)⑥status 的 kept 改实时台账口径 ⑦carrier 的 max_agents/max_concurrent 真正生效(decisions.effective_limits,与计划取 min 只收紧)⑧_validate_carrier 排除 bool。

> **第三批(P3 范围/门禁 7 条)同日回修**:①resolve_scope 目标分类在原始调用目录与顶层各试一次,path 目标归一为顶层相对(子目录相对路径不再误判 branch 落逃逸口)②untracked_in_scope 归一 './' 前缀与尾斜杠 ③apply_exclusions 同步过滤 untracked/raw_files(trusted 排除项对未跟踪文件同样 out of scope)④_file_insertions 二进制(NUL 探测)/超 1MB 按 0 计(config.UNTRACKED_INSERTIONS_MAX_BYTES)⑤range 目标三点 '…' 优先拆分(head 不再记成 '.feature')⑥Makefile 门禁正则排除 ':=' 变量赋值 ⑦justfile 配方生成 `just <name>` 而非 `make <name>`。

| # | 位置 | 缺陷 |
|---|---|---|
| 1 | `scripts/crp/handlers.py:637` | 门禁确认在 brief 之前触发,answer --key gates 无条件把 phase 置 carrier → brief 阶段被整体跳过(keep 阈值回退 KEEP_NONE、angles 空、02-brief.md 永不生成) |
| 2 | `scripts/crp/decisions.py:304` | fix_review 阶段仍向载体 1 下发 carrier1 do_work,而 submit 只认 phase=='sweep' → 逐字执行协议即无限 exit-3 死循环 |
| 3 | `scripts/crp/handlers.py:616` | answer 子命令不校验 pending_question 与当前 phase —— 陈旧 ask_user 恢复命令可在任意时刻重放,把状态机瞬间传送到别的相位 |
| 4 | `scripts/crp/handlers.py:650` | drift 答复 new_scope 重导范围后从不重评 overlay 信任 —— 被新 diff 触及的 overlay 仍按 trusted 处理(A3/A4 信任边界被 drift 分支绕开) |
| 5 | `scripts/crp/verify.py:143` | validate_votes_file 不查 (id,lens) 唯一性、不限当前批次 —— 重复透镜票双计,可把 REFUTED 翻成 CONFIRMED |
| 6 | `scripts/crp/cli.py:143` | setup_logger 在 handler 执行完之后才调用 —— run-dir 日志恒空,协议块缺失恢复步骤永远读到 0 字节 |

## 主报告(59 条;代理 prose 记 57,以 JSON 清点为准)

```json
[
  {
    "file": "code-review-p/scripts/crp/handlers.py",
    "line": 637,
    "summary": "门禁确认在 brief 之前触发,而 answer --key gates 无条件把 phase 置为 carrier,导致 brief 阶段被整体跳过(实证复现)。",
    "failure_scenario": "任何有启发式门禁候选的仓库(Makefile 带 check/test 目标且无 overlay —— 极常见)或带非写门禁的不可信 overlay:start → overlay 提交 → _apply_overlay(handlers.py:392)置 phase=gates → ask_user → 用户 approve_all 后 phase 直接跳 carrier;state.brief 恒为 {}、sweep_plan 恒为 {}(只有 _apply_brief 构建)→ keep 阈值回退 KEEP_NONE(standard/deep 的 PLAUSIBLE P0/P1 全被丢弃)、angles=[]、cap 回退 10、in/02-brief.md 永不生成;test_c057 因 Driver 自动批准且 fabricate 对空 angles 兜底 ['A'] 而掩盖此缺陷。修复:answer gates 应在 brief 未完成时转 phase=brief(或 overlay 应用不得先于 brief 进 gates)。"
  },
  {
    "file": "code-review-p/scripts/crp/decisions.py",
    "line": 304,
    "summary": "fix_review 阶段仍向载体 1 发 carrier1(Workflow) do_work,但 submit 的合法性检查要求 phase=='sweep',逐字执行协议即无限 exit-3 死循环(实证复现)。",
    "failure_scenario": "选了 Workflow 载体并带 --fix 的运行:fix-outcomes 提交后 phase=fix_review,state.carrier 仍为 1(无任何代码重置它)→ resume 下发 '以 scriptPath 调 deep-review.js … submit --kind carrier1' → handlers.py:285 判 phase!='sweep' 拒收 '错序提交: 期待 candidates, 收到 carrier1' → resume 再发同样指令,循环 24+ 次不收敛;LLM 逐字执行协议永远无法满足引擎。"
  },
  {
    "file": "code-review-p/scripts/crp/handlers.py",
    "line": 616,
    "summary": "answer 子命令不校验 state.pending_question 与当前 phase —— 陈旧 ask_user 恢复命令可在任意时刻重放,把状态机瞬间传送到别的相位(实证复现)。",
    "failure_scenario": "运行进行到 phase=verify/awaiting=verdicts 时,重放此前记录的 'answer --run-dir D --key gates --choice approve_all'(429 中断后会话恢复时重贴)→ phase 被重置为 carrier,验证队列搁浅,被迫重跑扫描轮并重复计数;重放 'answer --key drift --choice resume_old_scope' 会把全部候选标 stale。其它一切输入通道都有错序自愈守卫,唯独 answer 没有。另外 carrier1 提交记录无 idem 键(handlers.py:331 sha256 恒空)绕开共享幂等管线:一旦状态被重放传回 sweep,同一 journal 再喂入即双重入账(total_seen/dup 翻倍、votes 重复 append、假 parity 注记)。"
  },
  {
    "file": "code-review-p/scripts/crp/handlers.py",
    "line": 650,
    "summary": "drift 答复 new_scope 重导范围后从不重新评估 overlay 信任(trust_of/_init_overlay_state 均未调用),已被新 diff 触及的 overlay 仍按 trusted 处理,其只读门禁保持 approval=auto、排除项照常应用。",
    "failure_scenario": "运行启动时 overlay 未被触及 → trusted(只读门禁 auto);中断期间第三方提交改写了 .claude/code-review-p.md(新增门禁/排除项),HEAD 前移触发 drift ask;用户选 new_scope → resolve_scope 的 diff_files 已含该 overlay,但 state.overlay.trust 仍是 'trusted' → Phase 3 把攻击者注入的 'make check' 当已批准门禁运行取证,攻击者的排除项也被应用(overlay.py:163-164)—— 正是 project-config.md 信任边界(A3/A4)要挡的攻击,被 drift 分支绕开。"
  },
  {
    "file": "code-review-p/scripts/crp/verify.py",
    "line": 143,
    "summary": "validate_votes_file 不查 (id,lens) 唯一性、也不限当前批次(任意 pending id 皆可),apply_votes 照单全收 —— 重复透镜票在法定人数里双计,可把 REFUTED 翻成 CONFIRMED(实证复现)。",
    "failure_scenario": "P0/P1 候选应三透镜各一票(submissions.md:103);一份 verdicts 文件携带 [C(correctness), C(correctness 重复), R(reproduction), R(security)] 校验零错误(实证:validate errors: []),聚合得 2C ≥ ceil(4/2) → CONFIRMED,而真实三透镜 1C+2R 应为 REFUTED —— 被反驳的 finding 反进入报告。"
  },
  {
    "file": "code-review-p/scripts/crp/cli.py",
    "line": 143,
    "summary": "setup_logger 在 handler 执行完之后才调用(且是全库唯一调用点),处理期间一切 module_logger 告警落入共享 /tmp 兜底日志,<run-dir>/logs/<cmd>.log 每次都是空文件(实证复现)。",
    "failure_scenario": "untrusted overlay 排除项挂起告警、state.json 损坏告警、journal 匹配丢弃告警全部写进 tempfile.gettempdir()/code-review-p/logs/common.log(实证:与其它无关运行/测试的告警混在同一文件),而 SKILL.md:60-62 规定的协议块缺失恢复步骤 '读 <run-dir>/logs/<command>.log 尾部' 永远读到 0 字节(实证:submit.log size 0);handler 崩溃时 ectx.run_dir=None 连空文件都不建。"
  },
  {
    "file": "code-review-p/scripts/crp/verify.py",
    "line": 35,
    "summary": "高危候选恰 2 票 1C+1R 的对峙平局聚合为 CONFIRMED(1 >= (2+1)//2),违反三处文档写明的 '平局 = 不确定 → PLAUSIBLE' 规则;deep-review.js:199 同公式同错(锁死测试只覆盖 3 票平局)。",
    "failure_scenario": "载体 1 路径:某透镜 refuter 被 429 杀死后 journal 只剩 2 票(correctness=REFUTED 引证守卫, reproduction=CONFIRMED)→ journal._finalize_all 无透镜完备性检查直接聚合(inline 路径必须集齐 3 透镜故不可达)→ 对峙平局标 CONFIRMED 并按 CONFIRMED 排序报告,断言了设计定义为不确定的确定性;verify.py 模块 docstring、DESIGN.md、JS 注释三处均写平局落 PLAUSIBLE。"
  },
  {
    "file": "code-review-p/scripts/crp/handlers.py",
    "line": 649,
    "summary": "drift 答复 new_scope 直接采纳 resolve_scope 结果而不检查 escape_hatch(对比 scope-answer 分支 handlers.py:605 会重新询问),范围全空时静默空审。",
    "failure_scenario": "分支被合并后 HEAD 前移触发 drift ask,用户选 '重导范围(保留台账)' → 新范围全空(escape_hatch 置位、diff_files=[])→ 无任何 ask,decide_next 按 phase=sweep 在零文件范围上继续扫描 —— 正是 SKILL.md 'it never silently reviews nothing' 禁止的静默空审。"
  },
  {
    "file": "code-review-p/scripts/crp/scope.py",
    "line": 294,
    "summary": "resolve_scope 先把 cwd 重定向到仓库顶层再 classify_target,子目录启动(c025 已支持)时相对路径目标被误判为分支,落入全空逃逸口(实证复现)。",
    "failure_scenario": "cd src/deep && crp.py start --target mod.py:重定根后 (root/'mod.py').exists() 为 False、ls-files 列的是 src/deep/mod.py → kind='branch' → git diff main...mod.py rc=1 → 逃逸口 ask('所有 diff 源都为空'),用户点名的文件审查意图丢失;同一文件从顶层用 src/deep/mod.py 则正常。"
  },
  {
    "file": "code-review-p/scripts/crp/ledger.py",
    "line": 220,
    "summary": "Python 去重把 status='dup' 的台账行也当锚点(传递去重),deep-review.js 只把非 dup 候选推入 seen(非传递)—— 同一候选链两载体分类不同,共享 fixture 不覆盖链式情形。",
    "failure_scenario": "三 finder 报同一缺陷行号渐进漂移 10/12/14:JS seen=[X@10],Z@14 带 外(|14-10|=4)记 fresh 并返回为 finding;Python 台账 Z 命中 dup 行 Y@12(带内 2)标 dup → 引擎报告漏掉 JS 已返回的 finding,Z 的 refuter 票因 _match_candidate 跳过 dup 行而丢失,parity 注记 '去重语义需人工对照' 每次触发(fixture 仅单锚点用例)。"
  },
  {
    "file": "code-review-p/scripts/crp/handlers.py",
    "line": 196,
    "summary": "start 的范围逃逸口 ask_user 不落 pending_question、phase 停在 overlay,进程死亡后一条普通 resume 即绕过 '禁止静默空审' 门禁继续空范围审查(实证复现)。",
    "failure_scenario": "非 git 目录或全梯级为空的仓库:start 返回逃逸口 ask 后 429 中断;重启用 resume --run-dir D(实证:返回 overlay do_work,pending_question=None,scope.escape_hatch 仍在)→ decide_next 只看 phase 不看 escape_hatch/pending_question → overlay→brief→sweep 在空范围上跑完,候选对着空 diff 收割。"
  },
  {
    "file": "code-review-p/scripts/crp/handlers.py",
    "line": 472,
    "summary": "carrier 提交的 max_agents/max_concurrent 字段被校验并写入 state.carrier 后再无任何消费 —— sweep/verify 指令与载体 1 workflow 参数全部只读 sweep_plan,文档承诺的载体级并发覆盖是死旋钮。",
    "failure_scenario": "限流环境按 submissions.md:58-59 提交 {\"carrier\":1,\"reason\":\"429 storm\",\"max_concurrent\":1} 被接受入库,decisions.py:311 仍传 concurrency:<plan 值 4> 给 deep-review.js、decisions.py:301 仍指示载体 2 '同时在飞 ≤ 4' —— 该字段要防的 429 风暴照旧发生;只有启动前设 CRP_MAX_CONCURRENT 环境变量才有效。"
  },
  {
    "file": "code-review-p/scripts/crp/journal.py",
    "line": 125,
    "summary": "载体 1 摄取只从 journal 中实际出现的 finder 记录构建 coverage,无任何与 sweep_plan 角度或 JS 侧 {ran:false} 注册的对账 —— 死 finder 的覆盖空洞在引擎台账里完全不可见。",
    "failure_scenario": "finder E 初次与重试均被限流杀死 → journal.jsonl 无其记录(null 结果分类 unknown)→ 导出 ledger.json 的 coverage.angles 无 'E' 键,与从未规划该角度不可区分;deep-review.js:295 专门注册的 {ran:false} 覆盖空洞(v1.4.1 #10)只活在被丢弃的 workflow 返回值里,_parity_notes 也只对总数 —— 'no silent caps/coverage holes' 契约被最严重的降级形态违反且不上报告。"
  },
  {
    "file": "code-review-p/scripts/crp/journal.py",
    "line": 153,
    "summary": "refuter 证据在两条摄取路径都被截到 400 字,而模块 docstring 把截断限定于压缩 journal 兜底、config.MAX_EVIDENCE_CHARS/inline 路径允许 4000 —— transcript 模式同票不同载体证据量实质不同。",
    "failure_scenario": "载体 1 transcript 运行:某 CONFIRMED 票带 1500 字引证原文(journal.jsonl 记录的是 raw 返回,JS 只截自己的 journalVerdicts 副本)→ 引擎截到 400 字半句引文入台账与报告 Evidence 字段;同一票走 inline/载体 2 保留 4000 全文 —— 同一 finding、不同载体、报告证据实质不同。"
  },
  {
    "file": "code-review-p/scripts/crp/report.py",
    "line": 52,
    "summary": "parity_notes(载体内部分歧注记)从不进报告:render/run_ledger_line 不读该键,'分歧记入报告 parity 注记(可见,不静默)' 的文档承诺落空。",
    "failure_scenario": "载体 1 运行发生真实去重/计数分歧 → _parity_notes 追加到 state.accounting['parity_notes'] → 报告 Run: 行只渲染 rounds/candidates/dupDropped/refuted/capped-out,注记只能翻 out/ledger.json 才看得见 —— 违反 submissions.md:147 与 journal.py:245 docstring 的 '上报告' 承诺。"
  },
  {
    "file": "code-review-p/scripts/crp/journal.py",
    "line": 168,
    "summary": "quick 模式选载体 1 时 workflow 无模式概念照常全量对抗验证(每 P0/P1 三透镜烧预算),引擎随后 finalize_without_verify 把全部裁决丢弃 —— 成本照付、结论弃用、报告谎称未验证。",
    "failure_scenario": "crp.py start --mode quick 且 Workflow 可用 → deep-review.js 跑完整 refute 流水(3 lenses × P0/P1,吃 maxAgents 预算)→ journal 摄取遇 mode=='quick' 走 floor-only:被 refuter 带引证 REFUTED 的候选照 kept,报告标 'UNVERIFIED (quick — no verify pass)' 而 votes 就躺在台账里 —— quick 的成本上限被载体吹穿,test_c023 只锁了 floor-only 语义未锁这份浪费。"
  },
  {
    "file": "code-review-p/scripts/crp/handlers.py",
    "line": 219,
    "summary": "start --run-dir 指向既有未完成运行时,防覆盖守卫被显式跳过(守卫仅在无 --run-dir 时生效),_place_state 直接用全新 state 覆写其 state.json —— 中断运行的台账/选票/coverage 无提示损毁。",
    "failure_scenario": "中断的 deep 运行有 60 候选与选票;操作者(或丢失协议块后恢复的 LLM)按 '--run-dir D 继续这个目录' 的直觉执行 crp.py start --run-dir D --mode standard → ensure_layout+save 立即以 phase=overlay 的新状态替换旧 state.json,且守卫自己的理由串就写着 '防止中断运行被新跑覆盖';无任何文档许可此覆盖。"
  },
  {
    "file": "code-review-p/scripts/crp/ledger.py",
    "line": 209,
    "summary": "ingest_candidates 对 item['coverage'] 取 .get('ran') 前无形状检查,而 validate_candidates_file 不校验 coverage 字段 —— coverage:null 通过校验后在锁内 AttributeError,exit-2 内部错误而非 exit-3 校验拒收(实证复现)。",
    "failure_scenario": "LLM 写 {\"items\":[{\"angle\":\"A\",\"coverage\":null,\"candidates\":[...]}]}(自然的 '无覆盖信息' 编码):validate 返回 [](实证),apply 抛 'NoneType' object has no attribute 'get'(实证)→ cli 兜底转 exit-2 '脚本内部错误' 无可操作校验信息,违反 submissions.md '任何字段不合法, 整份拒收(exit 3)' 的全有或全无契约。"
  },
  {
    "file": "code-review-p/scripts/crp/ledger.py",
    "line": 219,
    "summary": "候选路径归一的 root 取 state.repo['cwd'] —— _new_state 存的是原始启动目录(可能是子目录)而非仓库顶层,子目录启动时绝对路径被错误剥前缀,同一缺陷以两种拼写入账(PLAUSIBLE:需 finder 回显绝对路径)。",
    "failure_scenario": "从 /repo/src 启动(c025 支持):repo.cwd='/repo/src';finder 回显绝对路径 /repo/src/a.py(归一化正是为此存在)→ norm_file 剥成 'a.py',另一角度的相对提交是 'src/a.py' → find_dup 比较文件字符串不相等 → 同一缺陷两条 fresh,烧 cap 位,报告出现仓库根不存在的路径 'a.py'(机制单元实证:norm_file('/repo/src/a.py','/repo/src')='a.py';触发依赖 finder 行为)。"
  },
  {
    "file": "code-review-p/scripts/crp/journal.py",
    "line": 151,
    "summary": "载体 1 的 refuter 记录完全绕过 validate_votes_file:lens 缺省补 'correctness',任意 lens/severity 字符串原样入票并计入 REFUTED 多数、传播进 severity_final(PLAUSIBLE:需 schema 约束失效才会出现非枚举值)。",
    "failure_scenario": "journal 返回带 lens:'cross-check' 或 severity:'P9' 且 verdict REFUTED:_classify 只查 verdict 枚举,票原样 append(实证可构造)→ 聚合计入反驳多数杀候选、P9 进 severity_final 与报告;同一票 inline/载体 2 会被整份拒收('lens 必须是 correctness/reproduction/security')—— 载体间对'什么算有效杀死票'不等价;触发取决于 Workflow 宿主对 VERDICT_SCHEMA 的枚举是否只是建议。"
  },
  {
    "file": "code-review-p/scripts/crp/journal.py",
    "line": 99,
    "summary": "_match_candidate 要求带内匹配唯一(len(banded)==1),歧义回显静默丢票 —— 仅 /tmp 兜底日志一行告警,无 parity 注记无账目计数(PLAUSIBLE:需回显摘要同时重叠两个同址候选)。",
    "failure_scenario": "两个 finder 按规程报同一 file:line ±1 的不同机制候选(皆 fresh);某 refuter 回显行号漂移、摘要与两者都重叠 → 返回 None → 决定性的 REFUTED 票被丢弃(journal.py:147-149),候选留 PLAUSIBLE 再被 standard keep 阈值丢弃 —— 载体 1 杀得掉的 finding 在载体 2/3 活着;配合 K10,那条告警还落在无人看的 /tmp 日志里。"
  },
  {
    "file": "code-review-p/scripts/crp/handlers.py",
    "line": 311,
    "summary": "carrier1 --file 模式的内容嗅探(读文件 + lines[0])位于 line 318 的 try 之前:空文件 IndexError、不可读/非 UTF-8 文件 OSError/UnicodeDecodeError 全部逃出设计好的 exec_error 重试询问,落进 cli 兜底 exit-2(实证复现)。",
    "failure_scenario": "LLM 先建好 output 文件、Workflow 运行在写入路径前报错 → 逐字执行 submit 指令提交 0 字节文件 → '脚本内部错误: list index out of range'(实证 rc=2),丢失 handlers.py:326-328 设计的 '检查 transcript 目录后重试' 引导(同退码 2 同路由,仅分类与引导损失)。"
  },
  {
    "file": "code-review-p/scripts/crp/handlers.py",
    "line": 359,
    "summary": "kind=='brief' 的 strict UTF-8 解码在任何 try 之外(JSON 分支 363-366 显式捕获 UnicodeDecodeError),非 UTF-8 brief 得到 exit-2 内部错误而非其它种类同情况的 exit-3 状态错误(实证复现)。",
    "failure_scenario": "写盘上下文按 Windows 默认 GBK/Latin-1 保存 02-brief.md 后提交 → raw.decode('utf-8', errors='strict') 裸抛 → '脚本内部错误: utf-8 codec can't decode…'(实证 rc=2),与 JSON 路径同类错误的 exit-3 处理不一致,LLM 得不到 '非法提交, 修正后重交' 的协议引导。"
  },
  {
    "file": "code-review-p/scripts/crp/scope.py",
    "line": 174,
    "summary": "untracked_in_scope 用未归一的 target 原文比对 porcelain 路径(仅 rstrip('/')):'./src' 形式的路径目标零命中 untracked,显式累积要覆盖的洞被重新打开(实证复现)。",
    "failure_scenario": "仓库有未跟踪 src/new.py、目标写 './src'(classify→path,git diff -- ./src 正常):u=='./src' or u.startswith('./src/') 对 'src/new.py' 永假(实证:[] vs 'src' 时 ['src/new.py'])→ 新文件静默离开审查范围与 stats.files;norm_file/_matches_exclusion 都剥 './',唯独此处不剥。"
  },
  {
    "file": "code-review-p/scripts/crp/scope.py",
    "line": 402,
    "summary": "apply_exclusions 只过滤 diff_files,scope['untracked']/['raw_files'] 保留被排除路径且 stats.files 与 raw_files 脱钩 —— trusted overlay 的排除承诺 'out of scope entirely' 对 untracked 文件不成立(实证复现)。",
    "failure_scenario": "trusted overlay 排除 'gen/',仓库另有未跟踪 gen/g_new.py:diff_files 剔除后 scope['untracked'] 仍含它(实证),brief_work(decisions.py:180-181)把 untracked 全量交给 brief 当审查目标 —— 被排除路径继续被审,project-config.md:104 承诺被打破(仅 trusted overlay 可达,untrusted 本就挂起)。"
  },
  {
    "file": "code-review-p/scripts/crp/overlay.py",
    "line": 76,
    "summary": "Makefile 启发式门禁正则 ^([A-Za-z0-9_.-]+): 同时命中变量赋值行(NAME:= 无空格形式),变量名含 check/test/lint/verify 即产出 'make <VAR>' 假门禁候选(实证复现)。",
    "failure_scenario": "Makefile 含 'TESTS:= test_a test_b' 或 'CHECK_FLAGS:= -Wall'(实证:产出 'make TESTS'/'make CHECK_FLAGS')→ A4 批量询问把非目标列为门禁;approve_all 后 verify_work 告知 refuter 该只读门禁覆盖 finding,make 报 'No rule to make target' → finding 落 'unverified by gate';project-config.md 说扫的是 'Makefile/justfile targets'。"
  },
  {
    "file": "code-review-p/scripts/crp/report.py",
    "line": 104,
    "summary": "findings 表格把 ≤500 字任意内容的候选 summary 原样插进 markdown 表行,不转义 '|' 与换行 —— 含 '||' 或换行的 summary 使引擎渲染的表格列数错乱/断行(实证复现)。",
    "failure_scenario": "kept summary 为 'if (a || b) inverted'(实证渲染 '| 1 | src/a.py:1 | P1 | use x || y in guard |' —— 4 列表出现 5 列)或含换行直接拆行;该表是 SKILL.md 要求逐字转述的引擎产物,同一段错乱文本被冻进 state.report.text 并由 crp.py report 重渲染。"
  },
  {
    "file": "code-review-p/workflows/deep-review.js",
    "line": 271,
    "summary": "gap 循环条件硬编码 dry < 2(deep 的 dry_required),而引擎按模式持有 dry_required 且载体 1 do_work 只传 maxRounds/maxAgents/concurrency —— 模式收敛语义在 JS 里私有复刻,无 parity 锁。",
    "failure_scenario": "standard 模式(dry_required=1)选载体 1 且 CRP_MAX_ROUNDS 覆盖到 ≥3(钳制 [1,5]):workflow 按自己的 dry<2 多跑一整轮 gap,烧掉模式预算外的 finder/refuter,journal 摄取记 rounds=3 而扫描计划承诺 1 个 dry 轮即收敛;即使默认 maxRounds=2,首轮全 dry 也会多触发一次 JS gap 轮。改 MODE_PARAMS 或加模式永远不会同步到 JS 副本。"
  },
  {
    "file": "code-review-p/scripts/crp/state.py",
    "line": 79,
    "summary": "CRP_HOME 分支直接返回 Path(env) 不追加 /code-review-p,而 LOCALAPPDATA/XDG 分支都追加;SKILL.md:65 与 state.py 自身 docstring 写的布局是 <state-home>/code-review-p/<repo-slug>/<run-id>/ 且 'CRP_HOME overrides'。",
    "failure_scenario": "按 SKILL.md 设 CRP_HOME 的用户/agent 到 $CRP_HOME/code-review-p/ 下找运行目录、out/report、logs/<command>.log(协议块缺失恢复步骤)一无所获 —— 实际在 $CRP_HOME/<repo-slug>/;tests/test_state.py:27 断言现状(不追加),契约冲突真实存在,修文档或修代码皆可,但目前两份文档与代码不一致。"
  },
  {
    "file": "code-review-p/scripts/crp/handlers.py",
    "line": 460,
    "summary": "_validate_carrier 接受布尔值当 max_agents/max_concurrent(isinstance(True,int) 为真且 True>=1),对照 ledger.py:125/138 对 line/confidence 显式排除 bool —— 同库双重标准,{'max_concurrent': true} 过校验入库。",
    "failure_scenario": "carrier.json {\"carrier\":1,\"reason\":\"…\",\"max_concurrent\":true,\"max_agents\":true} 通过校验并写进 state.carrier 与导出台账,协议承诺的正整数位上是布尔(语义 1);因 K9 该字段暂无下游消费者,危害潜伏 —— 一旦接通即成真错。"
  },
  {
    "file": "code-review-p/scripts/crp/handlers.py",
    "line": 718,
    "summary": "cmd_status 的 kept 读 acc['kept_after_thresholds'],唯一写入点是 report.render(report.py:79)—— verify 阶段候选已 kept=True 时 status 仍报 kept=0,直到 details/渲染才对。",
    "failure_scenario": "standard 运行验证轮中查询 crp.py status:metrics 显示 kept=0 尽管多数候选已终裁入榜 —— 监控/断点排查时误判运行无产出(最终报告本身计算正确,纯中间态口径失真)。"
  },
  {
    "file": "code-review-p/scripts/crp/scope.py",
    "line": 342,
    "summary": "range 目标用 target.partition('..') 拆 base/head:git 合法的三点对称差 'main...feature' 记录 head='.feature','a..b..c' 记录 head='b..c' —— 无效 ref 写进 scope 元数据(PLAUSIBLE:全库无 scope.head 消费者,损害止于 state.json/台账导出中的错误元数据)。",
    "failure_scenario": "crp.py start --target 'main...feature':diff 与 scope_command 用原始 target 工作正常,但 scope['head']='.feature' 持久化进 state.json 与 out/ledger.json,任何未来消费者(报告锚点/漂移比较)拿到垃圾 ref。"
  },
  {
    "file": "code-review-p/scripts/crp/journal.py",
    "line": 178,
    "summary": "P2/P3 被单票升 P0/P1 的规则在载体 1 摄取路径是一份降级副本(改写 severity 后立即按现有票聚合,无补透镜通道),与 verify.apply_votes 的'退回复核其余透镜'机制分流 —— 同输入不同载体不同终裁(PLAUSIBLE:预算耗尽/死亡 refuter 角落才分叉,正常情况 workflow 自己补了透镜)。",
    "failure_scenario": "升级规则任何改动须同时编辑 verify.py:161-172 与 journal.py:178-185 两处,已发生行为分叉:载体 1 升级后单票<法定 → PLAUSIBLE(保守),载体 2/3 补透镜再终裁可 CONFIRMED;SKILL.md:174 'sent back for the remaining lenses' 的引擎承诺在 journal 路径靠 workflow 内部行为间接成立,预算断档即破。"
  },
  {
    "file": "code-review-p/scripts/crp/handlers.py",
    "line": 633,
    "summary": "gates 逐条赋值 'choice' 只认 b=='1',其余任意 token('2'/'yes'/'true')静默按 denied 处理,无 token 校验(PLAUSIBLE:问题文本与 resume note 已写明 0/1 格式,且方向 fail-closed)。",
    "failure_scenario": "用户答复 '1,0,yes' → 第三个门禁被静默 denied(不会误放行),验证阶段该门禁报 'unverified by gate';因结果方向安全且格式有文档,属低危输入校验松散 —— 但静默语义化而非报错仍违背全有或全无的提交纪律。"
  },
  {
    "file": "code-review-p/scripts/crp/config.py",
    "line": 203,
    "summary": "六个协议常量零消费者:ENGINE_NAME/REPORT_TABLE_COLUMNS/PHASES/AWAITING_KINDS/ROUND_KINDS/RESUME_OPTIONS(grep 全技能仅定义处命中);AWAITING_KINDS 还带着过期值 'fix_outcomes'(下划线)而引擎实际写 'fix-outcomes'(handlers.py:547)—— 证明从未被校验;REPORT_TABLE_COLUMNS 双重死(report.py:101 硬编码表头)。",
    "failure_scenario": "config.py 头部宣称 '单一事实源, 禁止散落字面量',读者却把这些元组当活的状态机契约信任;维护成本:下次改 phase/awaiting/提交种类时要同步一份没人读的清单,漏改零告警(死代码,应删或真正启用校验)。"
  },
  {
    "file": "code-review-p/workflows/deep-review.js",
    "line": 344,
    "summary": "JS 硬编码置信地板 0.5(:344)、severity/category 排序表(:342-343)、deep cap 20(:357)、单角度 cap 8(:315-318)、钳制 [1,5]/[1,16](:104,:143)复刻 config.py 对应常量,且无任何 parity 锁(fixture 只锁 dedup 与 schema maxLength)。",
    "failure_scenario": "调高 CONFIDENCE_FLOOR 或 CATEGORY_RANK:引擎报告按新值保留/排序,JS 返回的 findings 列表仍按 0.5/旧序过滤排序 —— 操作者读 workflow 输出与读最终报告看到两套口径;config 改动静默失联到 JS 副本(应让引擎下发必填 args 或删掉 JS 侧过滤仅返回 raw journal)。"
  },
  {
    "file": "code-review-p/scripts/crp/journal.py",
    "line": 193,
    "summary": "_account_rounds 是 verify.finalize_round 之外的第二套轮次记账实现且语义已漂移:把最终 kept 总量覆写进每一条 round_history(引擎路径是各轮快照)、独立重算 dry —— 同一字段两载体含义不同。",
    "failure_scenario": "ledger.json 的 round_history['kept_after_verify'] 载体 2/3 是逐轮快照、载体 1 是总量复读;任何按轮比较 kept 的消费方、或 dry 定义的未来改动,都要双处同步 —— 正是 v2 回修要关闭的'计数口径'缺陷类。"
  },
  {
    "file": "code-review-p/scripts/crp/decisions.py",
    "line": 225,
    "summary": "旧 state.json 兼容靠四处散落的逐读点 or-回退(decisions.py:225/294/356、handlers.py:472),而 state.py:169 专建的 _MIGRATIONS 迁移注册表空置、STATE_SCHEMA_VERSION 停在 1 —— 为此建的机制被绕开。",
    "failure_scenario": "每个新 sweep_plan 字段都诱惑再加一处内联回退,漏一处读点就让旧运行恢复后在那一处静默用默认值(如未来 keep_floor 在 carrier_work 生效、在 verify 阈值检查落默认)—— 同一运行恢复前后 kept 集合不同;应有 v1→v2 迁移在加载时回填。"
  },
  {
    "file": "code-review-p/scripts/crp/scope.py",
    "line": 186,
    "summary": "单次 start 把同一 `git status --porcelain -z -uall` 跑 2-3 次(阶梯入口 untracked_files、命中 rung 的 accumulate_working_tree、_new_state 的 current_signature;路径目标再加 resolve_scope:302 一次)—— 应在阶梯入口取一份快照贯穿使用。",
    "failure_scenario": "10 万文件 monorepo 每次全树状态扫描 1-3 秒:落在 rung 3/4 的 start 连做三次同扫描(约 2-6 秒纯重复子进程),每次 answer scope/drift new_scope 重导范围再付一遍。"
  },
  {
    "file": "code-review-p/scripts/crp/state.py",
    "line": 139,
    "summary": "list_runs 对每个运行完整 json.load 其多 MB 的 state.json 只为取 5 个标量(mode/phase/target/done/updated_at);被 runs 命令、start 未完成检查、每次无 --run-dir 的 submit/resume/report(resolve_run_dir)反复调用。",
    "failure_scenario": "约 10 个完成的 deep 运行后每个 state.json 3-5MB(候选+选票+内嵌报告全文):一条 `crp.py runs` 解析 30-50MB JSON 只为打几行列表;每条省略 --run-dir 的 submit 都重扫一遍 —— 应在 save() 时落一份轻量 meta 边车。"
  },
  {
    "file": "code-review-p/scripts/crp/scope.py",
    "line": 221,
    "summary": "每个命中的阶梯 rung 都把同一 git diff 跑两遍:_diff_files(--name-only)随后 _numstat 完全相同参数(198+205、221+232、239+252、256+263、335+346、361+377);一条 `git diff --numstat` 同时给文件清单(第三列)与增删行数。",
    "failure_scenario": "大树上每次 git diff 100-500ms:每次 start/重导范围对最贵的 Phase 0 git 操作做纯双倍功;路径受限 rung 3/4 的 _numstat 还漏掉 -- target 限制(另一处统计口径不一致)。"
  },
  {
    "file": "code-review-p/scripts/crp/scope.py",
    "line": 51,
    "summary": "classify_target 为一次成员判断物化整个 tracked 索引:`git ls-files` 全量 stdout → splitlines() 列表;`git ls-files --error-unmatch -- <target>` 直接回答。",
    "failure_scenario": "30 万文件仓库 `start --target src/foo.py`:10-20MB stdout 过管道 + 30 万元素列表只为 `target in list`,每次路径目标 start 白付秒级延迟与瞬时内存。"
  },
  {
    "file": "code-review-p/scripts/crp/handlers.py",
    "line": 66,
    "summary": "_run_ask 在 cmd_start:187 算完未完成列表的一行之后立刻重算同一 list_runs(全量重解析每个 state.json)—— 应把已过滤列表传进去。",
    "failure_scenario": "10 个历史运行 × 4MB state.json:存在未完成运行的 start 一次调用把约 40MB 状态 JSON 连续解析两遍,命令主导成本翻倍。"
  },
  {
    "file": "code-review-p/scripts/crp/journal.py",
    "line": 88,
    "summary": "_match_candidate 对每条 refuter 记录线性走全部候选并对每个同文件带内条目重算 tokens_of(≤500 字摘要),无文件索引无 token 缓存 —— O(记录×候选) 加重复分词。",
    "failure_scenario": "200-finding deep 审查的载体 1 摄取(5MB journal、约 400 台账条目、600 条 verdict):约 24 万次字典探测加数千次重复分词;按归一文件建一次索引 + 摄取时缓存 token 集,一遍即完(JS isDup 是 parity 锁定孪生,改需两侧同步)。"
  },
  {
    "file": "code-review-p/scripts/crp/journal.py",
    "line": 126,
    "summary": "coverage 角度字典在 journal.py:125-126 与 ledger.py:207-208 两处初始化且形状已分叉(journal 版多 'dropped_invalid':0 且 ran 默认 True,ledger 版无该键 ran 默认 False)—— 同一字段按载体两种形状。",
    "failure_scenario": "经 submit --kind candidates 建的 coverage 条目没有 dropped_invalid 键,经载体 1 journal 建的有;coverage 的任何消费方(export_ledger、invalid 计数、未来报告渲染)都要兼容两种形状,新增计数桶要改两处字面量 —— 漏一处该载体静默报 0;应由 ledger 的一个共享 helper 拥有形状。"
  },
  {
    "file": "code-review-p/scripts/crp/scope.py",
    "line": 187,
    "summary": "accumulate_working_tree 手解 porcelain -z(rec.startswith('??'))重新实现了 gitops.porcelain_z(处理 R/C orig_path 记录的结构化解析)—— 同一命令两套解析。",
    "failure_scenario": "porcelain_z 的修复(引号路径、R/C 记录)到不了阶梯的 has_wt 判断;此处甚至把 R/C 记录后随的 orig_path 片段误读为独立记录(因 R 本身已蕴含 has_wt 而无害)—— 防御性正确靠巧合,应直接调 porcelain_z 判 e['xy'] != '??'。"
  },
  {
    "file": "code-review-p/scripts/crp/scope.py",
    "line": 88,
    "summary": "_pr_diff_files 为 gh 手抄了一份 gitops.run 的子进程封装(capture/text/encoding/errors/timeout/except→失败)逐字相同 —— 只差二进制名与返回约定。",
    "failure_scenario": "c012 式修复(显式 utf-8/replace 防非 UTF-8 locale 崩、超时策略)维护在 gitops.run,副本在 scope 的 gh rung 静默停在旧行为;参数化 gitops.run 接受 argv 即消除。"
  },
  {
    "file": "code-review-p/tests/test_workflow_script.py",
    "line": 255,
    "summary": "aggregateVerdict 九例矩阵在 test_workflow_script.py:255-266 与 test_aggregate.py:12-22 各自手抄维护(后者 docstring 自认 '移植 JS 矩阵'),而 dedup 奇偶校验已有共享 fixtures/dedup_cases.json 模式 —— 聚合契约双份手工维护。",
    "failure_scenario": "新增边界用例(如 2v2 含 PLAUSIBLE 弃权、高危 4 票平局)只进一个文件时,另一实现(Python aggregate_verdict 或 JS aggregateVerdict)对该用例零测试,两引擎可以带绿色测试漂移 —— 矩阵应入共享 fixture 双侧断言(注意:K4 的平局缺陷恰是两矩阵共同漏掉的用例)。"
  },
  {
    "file": "code-review-p/scripts/crp/decisions.py",
    "line": 471,
    "summary": "fix_work 逐字重建 report_work(decisions.py:447-449)的 kept 上下文 comprehension([{id,severity,location,summary}]),_gap_digest(256-258)再建第三份 kept 清单 —— 形状三处定义。",
    "failure_scenario": "协议依赖的 kept 上下文形状在两个相同 comprehension 里定义:加字段(如 drop_reason 或 evidence)只改一份时,--fix 阶段对着与 details 阶段不同的候选视图编造 outcomes,只测 report_work 的测试无感;抽一个 kept_context(kept) helper。"
  },
  {
    "file": "code-review-p/tests/test_e2e.py",
    "line": 25,
    "summary": "parse_block 在 test_e2e.py:24-28 与 test_cli.py:29-34 重复,且 e2e 副本丢掉了 test_cli 有的 '恰一个 NEXT_STEP_END 标记' 断言(PLAUSIBLE:BEGIN 计数断言仍在,丢的断言只掩盖游离/尾部 END 标记情形)。",
    "failure_scenario": "解析器双份维护:下次标记或退出码契约变更要修两处,只记得一处的那份保住不变量;e2e 流转若发出游离 END 标记,现行副本解析首块照常通过 —— 移入 conftest.py 共享并保留双计数断言。"
  },
  {
    "file": "code-review-p/scripts/crp/state.py",
    "line": 241,
    "summary": "c009 同进程 flock 自死锁的修复是调用点一条约定注释('锁内不得再调 cmd_resume'),StateStore.locked 每次获锁新开 fd、无重入守卫 —— 下一处 '应用后调别的 cmd_* 重路由' 的写法即复现死锁。",
    "failure_scenario": "任何未来 answer key/handler 按路由模式的直觉写法(先 apply 再调 cmd_resume —— c009 当年正是这么发生的)在同一进程内对新 fd 请求 LOCK_EX,对自己已持的锁阻塞挂死;测试只盖住已修的那一条路径;应在 locked() 内按 run-dir 记录本进程持锁状态,重入变 no-op 或显式报错。"
  },
  {
    "file": "code-review-p/scripts/crp/handlers.py",
    "line": 257,
    "summary": "_KIND_FILE_HINT 七项 kind→人读提示字典全技能零读者(仅定义处命中)。",
    "failure_scenario": "下次增改提交种类或措辞时这份没人消费的表静默漂移;维护者还要分辨它与 cli.py choices 谁是权威 —— 删除。"
  },
  {
    "file": "code-review-p/scripts/crp/models.py",
    "line": 239,
    "summary": "State.candidate_by_id 零调用者(仅 ledger.py:197 docstring 提及);真实查找全部内联 comprehension(decisions.py:346、verify.py:135/211)。删除方法或让三处改用它。",
    "failure_scenario": "同一台账两套查找习惯并存(O(n) 方法 + 三处手搓字典);id 形状未来变化(如前缀调整)要改四处,死方法还给人'查找已集中'的假象。"
  },
  {
    "file": "code-review-p/scripts/crp/state.py",
    "line": 206,
    "summary": "work/ 子目录机制只写不读:config.WORK_SUBDIR 被 create_run_dir/ensure_layout mkdir,两个访问器(StateStore.work_dir、RouteCtx.work_dir)存在,但没有任何代码在 work/ 下写过或读过文件 —— gap digest 与验证批次实际内联在协议 context 里交付。",
    "failure_scenario": "每次运行创建一个永远为空的目录,两个死访问器的 docstring 还宣称 '引擎产出给 LLM 的输入在这里',让下一个贡献者去找从未写过的 digest 文件;删子目录与双访问器(test_state.py:49 的 is_dir 断言随之删除)。"
  },
  {
    "file": "code-review-p/scripts/crp/ledger.py",
    "line": 245,
    "summary": "持久化占位字段无人读:每条候选带 'rank': None(rank_findings 排序后从不写回)、state.brief 带 sha256:''/sections_ok:True 常量、carrier1 提交记录带 sha256:''。",
    "failure_scenario": "state.json / ledger.json 的消费方(及未来报告代码)合理假定 rank 是真实排名、brief.sha256 标识 brief —— 恒为 null/空;数百条台账行各背一个死键,删字段或在值存在的那一点写入。"
  },
  {
    "file": "code-review-p/scripts/crp/ledger.py",
    "line": 186,
    "summary": "IngestResult.errors 从未 append;VoteOutcome.errors 只在被 validate_votes_file 弄成不可达的分支填充(verify.py:141)且无读者(handlers 只用 .finalized/.upgraded)。",
    "failure_scenario": "两个 dataclass 宣告一条永不触发的错误通道:未来调用方真遇摄取问题时会把 outcome.errors==[] 当 '没出事' 信任,而实际信号在校验器的先应用后全量拒收契约里;删除两个字段。"
  },
  {
    "file": "code-review-p/scripts/crp/models.py",
    "line": 117,
    "summary": "Decision.next_type + _ROUTE_TO_NEXT_TYPE 与 transitions.build_next_step 的真实分发是第二套并行 Route→type 映射;唯一读者是 tests/test_decisions.py。",
    "failure_scenario": "测试断言的映射不是生产使用的映射:若 build_next_step 错发某 Route 的 type,next_type 断言保持绿色而协议块发错类型;删属性与映射表,测试改断言 transitions 实际构建的 next_step 字典。"
  },
  {
    "file": "code-review-p/scripts/crp/models.py",
    "line": 131,
    "summary": "cursor 字段 lens_requests 除 _default_cursor 字面量外零读零写(仅两个测试夹具复刻形状)。",
    "failure_scenario": "升级补透镜的真实状态存于 pending_verify 条目的 lenses 字段(verify.py:170),lens_requests 是残留占位 —— 读者误以为存在另一条升级通道;删除。"
  },
  {
    "file": "code-review-p/scripts/crp/journal.py",
    "line": 222,
    "summary": "parity 原始总数不变式把 Python 的超帽丢弃(py_overcap)计入 py_raw,而 deep-review.js 在计数前就 cap 到 8(:318 raw.slice(0,8),totalSeen/dupDropped 均不含被丢者)、transcript journal 却携带未截断的 raw 返回 —— finder 返回 >8 即确定性触发假警报。",
    "failure_scenario": "某 finder 返回 10 个候选(CANDIDATES_SCHEMA 无 maxItems):JS 计数按前 8,Python transcript 摄取重新 cap 并记 dropped=2 → js_raw(8 基)≠py_raw(10 基)→ 报告注记 '原始候选总数不一致, 存在未记账的丢弃(人工查…)' 指控 workflow,而丢弃其实完整记在 coverage.dropped;有 invalid 丢弃的解释分支(:231)却没有超帽丢弃的解释分支。"
  }
]
```

## 补漏扫描追加(5 条:实证 4 + 代码确证 1)

```json
[
  {
    "file": "code-review-p/scripts/crp/handlers.py",
    "line": 683,
    "summary": "cmd_report 放行 phase='report'(details 尚未提交),而其报错文本自称 'done 后幂等重渲染' —— 提前调用会用空 details 渲染占位报告并以 finish/exit-0 终结整个审查,details 契约被整体绕过(代码确证)。",
    "failure_scenario": "引擎正等待 details 提交(phase='report', awaiting='details')时调用 crp.py report --run-dir D:details = state.report.get('details') or {} 为空 → render() 产出 '(未声明)'/'(缺失 —— LLM 未声明)'/'(未给出)' 的占位报告覆写 out/report.md,协议块 type=finish exit 0 → 调用方按 SKILL.md §1 逐字输出并停止;validate_details_file 的全部闸门(summary_bullets 3-5 条、repo 模式 coverage 必填、入榜 details 必有)从未执行,state.report 还把占位文本记录为运行报告。"
  },
  {
    "file": "code-review-p/scripts/crp/state.py",
    "line": 159,
    "summary": "resolve_run_dir 只返回未完成运行(not entry.get('done')),唯一运行完成后裸形式的 report/status/resume 全部无法再解析它 —— 文档展示的 done 后幂等重渲染面不传 --run-dir 不可达(实证确证)。",
    "failure_scenario": "运行到达 phase=done 后按 SKILL.md §2 的裸形式执行 `crp.py report`:cmd_report → _resolve_entry_point → resolve_run_dir 跳过 done 运行返回 None → exit-3 '未找到可续跑运行 —— 先执行 crp.py start'(而该命令自己的守卫文本写着 '此命令用于 done 后幂等重渲染');裸 `status` 报 '当前仓库无运行记录'(runs 却仍列出该运行),裸 `resume` 报错而非重发 done 相位的 finish 块与逐字报告 —— 用户被告知重新 start 才能看到已完成的报告。"
  },
  {
    "file": "code-review-p/scripts/crp/scope.py",
    "line": 113,
    "summary": "_file_insertions 的 docstring 承诺 '二进制/超大文件计 0' 但没有任何二进制或大小检查(实证确证:无任何守卫)—— 未跟踪的二进制/巨型工件把原始行数灌进 stats 并进入 diff_files。",
    "failure_scenario": "工作树里有未跟踪的 50MB 构建产物(编译二进制或单行巨型 bundle):_finish_scope 把它的行数计入 stats.insertions → 轻松越过 FANOUT_LINE_THRESHOLD(800),standard 运行被静默收窄到热点角度集 B/C/E/L/T;该二进制路径同时进 scope.diff_files/brief 上下文交给 finder '审阅' —— fan-out 决策与范围统计建立在垃圾数据上,docstring 承诺的按 0 计完全未实现。"
  },
  {
    "file": "code-review-p/scripts/crp/overlay.py",
    "line": 76,
    "summary": "启发式门禁提取把 Makefile 目标正则同样应用于 justfile(config.HEURISTIC_GATE_FILES 含 justfile),且命中一律加 'make ' 前缀 —— just 仓库登记的门禁命令根本不存在(实证确证:justfile 仓库产出 ['make check'])。",
    "failure_scenario": "只有 justfile(配方 check:/lint:)而无 Makefile 的仓库:heuristic_gates() 返回 ['make check', 'make lint'],经 A4 批量询问获批后 Phase 3(及 --fix 第 3 步'重跑覆盖该条的门禁')执行 `make check` → 'No targets specified and no makefile found'(或误跑父目录的陈旧 Makefile)→ 依赖门禁的 finding 全记 'unverified by gate',just 仓库的修复永远无法计为 fixed;应为 justfile 生成 `just <name>`。"
  },
  {
    "file": "code-review-p/scripts/crp/report.py",
    "line": 99,
    "summary": "render() 的零发现分支提前返回,不输出 'Open questions / assumptions' 节 —— LLM 按 details 契约提交的 open_questions 在最需要 Caveat 的场景被静默丢弃,与有发现路径(:120)不对称(PLAUSIBLE:calibration.md 的 clean-verdict 模板本就只列 Residual risks/Testing gaps,可能是有意取舍,但 submissions.md 的 details 契约把 open_questions 列为提交散文)。",
    "failure_scenario": "零 kept finding 的运行,LLM 提交含 open_questions('门禁 Y 被拒,无法验证 X')的 details(实证:validate 接受;实证:渲染产物中既无 'Open questions' 节也无该字符串)→ 报告只有 'No significant issues found / Residual risks / Testing gaps / Summary' —— 调用方只能逐字转述引擎报告,未决疑点恰好在此场景全部丢失;修复:提前返回前补同一节(或在校验层明确豁免并同步文档)。"
  }
]
```

## 备注

- 验证阶段驳回 1 条候选:"carrier1 重投导致双重入账" 在真实 CLI 路径上被错序门禁拦下(成功摄取后 phase=report,重投得 exit-3 自愈纠正),故未列入;其残余面(提交记录无 idem 键绕开共享幂等管线)已并入主报告中 answer 重放一条(handlers.py:616)的 failure_scenario。
- 主报告生成时 gap-sweep 子代理超时未归;随后返回,5 条新增经逐条验证全部成立,追加于此。
