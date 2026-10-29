# 技能运行报告原文（验收附录）

> 执行者封面语（非报告正文）："全部 15 个 refuter 裁决到齐（3 个因 429 中断的已由我内联补证），10 个 finder 全部回收，Phase 4 补漏（format-proto-check 红灯 + 未接 CI 为我亲测新发现）。去重、排序完成，以下为最终审查报告全文。"
> 运行环境注记（验收方记录）：执行者曾因账户级 API 429 中断一次，由验收方恢复并转发 4 份孤儿 finder 结果；本报告为续跑后产出。

---

## Review: repo --deep —— step-by-step 全仓审计（baseline=空树，2157 个纳入文件 / 约 32.4 万行，排除 overlay 声明的 proto/gen、contracts/src/generated、openwiki、scratch、锁文件）

Brief: 多语言 monorepo（Tauri 桌面 + 21 微服务 + 契约管线）。本审查按区域 fan-out 10 个 finder + 15 个对抗 refuter（P0/P1 双视角），叠加 overlay 红线 R1–R10 与 7 个只读门禁实测。重点闭环：同步链路幂等回放、冲突解决、billing 账本守恒、identity 会话权威、社区发布链、离线交付契约。门禁结果：generated-check ✓ / migration-check ✓（40 冻结）/ proto-check ✓ / lint-proto ✓ / desktop pnpm lint ✓ / i18n 双语键齐 ✓ / **format-proto-check ✗**。

| # | Location | Sev | Finding |
|---|----------|-----|---------|
| 1 | services/content-service/.../SyncPushService.java:327 | P1 | 幂等回放把 DEPENDENCY_PENDING 回执重放成 ALREADY_APPLIED(null rev)，客户端 ack 丢编辑、shadow base 归零 |
| 2 | services/content-service/.../SyncConflictService.java:170 | P1 | 冲突解决烧掉 stream_rev 不写 change 行，对端拉取游标永久自旋，设备被困至 180 天后 410 全量 rebase |
| 3 | services/community-service/internal/store/discover.go:34 | P1 | discover 过滤+游标 SQL 占位符错绑，语言/领域筛选的第 2 页起必报 SQL 错误 |
| 4 | services/bff-admin/src/pages.ts:83 | P1 | 审核队列把攻击者可控标题拼进 innerHTML，存储 XSS 劫持操作员全权 API |
| 5 | apps/desktop/src-tauri/src/lib.rs:347 | P1 | 悬浮球关闭分支只 pause 不 exit，进程变持有 token 的后台僵尸 |
| 6 | infra/offline-bundle/build.sh:20 | P1 | 离线交付包钉 dragonfly v2.0.0（实为 redis-server 7.4），违背版本锁与选型决策 |
| 7 | services/billing-service/.../BillingWorkflow.java:94 | P2 | attemptPayment 丢弃乐观迁移 Optional 结果，并发双击穿透 PSP 双扣 |
| 8 | services/billing-service/.../BillingStore.java:154 | P2 | postJournal/postReversal 无事务（javadoc 谎称"同事务"），中途失败永久破坏借贷守恒且无恢复路径 |
| 9 | services/identity-service/.../SessionV2Service.java:156 | P2 | v2 introspect 不校验 user.isActive()，user 为 null 时还跳过 epoch 校验 |
| 10 | services/identity-service/.../IdentityV2Service.java:793 | P2 | revokeSession 的 step-up grant 是可选项，被盗 access token 即可跨设备登出 |
| 11 | services/creator-service/.../ApplicationRestController.java:209 | P2 | supplement/appeal 的 creator_id 取请求体而非 X-User-Id，主体注入 |
| 12 | services/rating-service/.../CommunityAcquisitionClient.java:102 | P2 | 5 分钟 resolve 缓存仅按 acquisitionId 键，跨用户投毒让伪造质量证据计入 |
| 13 | services/creator-service/.../ApplicationWorkflow.java:210 | P2 | adjustEligibility 以 List.of() 覆写 service_scope，resume 后激活但权益清零 |
| 14 | services/creator-service/.../ApplicationStore.java:44 | P2 | rs.wasNull() 串列：trial_domain 的空值判定套在 amount/concurrency 上 |
| 15 | services/rating-service/.../MetricStore.java:171 | P2 | deadLetter 置 'failed' 不推 next_attempt_at，findDue 又选 'failed'，4xx 语义拒绝变 15s 无限重投 |
| 16 | services/rating-service/.../EvidenceService.java:85 | P2 | duplicate 早退绕过标记阶段，自愈承诺落空，合法证据永久漏计 |
| 17 | services/community-service/internal/handler/handler.go:1122 | P2 | `fresh, _ :=` 吞错后解引用，DB 抖动即 nil-deref panic |
| 18 | services/bff-desktop/src/clients/contentClient.ts:77 | P2 | resolveUserId 把 identity 一切非 200（含 503）映射为 401，上游抖动引发全员登出 |
| 19 | services/api-gateway/config/apisix.yaml:7 | P2 | 网关仅路由 /v1/*，BFF 全部 /v2 面不可达；悬空 upstream_id + 伪 websocket 插件 |
| 20 | infra/versions.lock.yaml:110 | P2 | R8 红线：ml-worker 全部依赖与 ai-orchestrator/fastify 等 TS 运行时依赖未登记版本锁 |

### Details

**[P1] 幂等回放吞掉待依赖操作——`services/content-service/src/main/java/com/sbs/content/application/SyncPushService.java:327`**（execution 孪生 `:325-333` 同构）
- **Problem:** `handleReplay` 只比对载荷 hash 即返回 `OpReceipt.alreadyApplied(op, computedHash, existing.resultingRev(), ...)`，从不读 `existing.outcome()`；`isIdempotentReplay` 为纯 hash 相等（`SyncReceipt.java:41-43`）。
- **Failure scenario:** 前驱未到的 op 收到 DEPENDENCY_PENDING 回执（`dependencyPendingAndRecord` 以同 hash、null revs 落库，`SyncPushService.java:614-619`）；客户端 5 秒后重试同 op_id（`store.rs:826-827` 排定重试，`claim_push_batch` 重推）→ hash 匹配 → 服务端回 `ALREADY_APPLIED, entity_rev=null` → Rust T3 把 `Applied | AlreadyApplied` 合并处理：`let entity_rev = receipt.entity_rev.unwrap_or(0)`，ack outbox、清 dirty、`advance_shadow_base` 无守卫 upsert 把 base 覆写成 0（`apps/desktop/src-tauri/src/sync/store.rs:777-794`）。用户编辑静默永久丢失且 shadow 损坏。proto 已有 `original_status` 字段（`sync.proto:75`）专用于消歧，push 路径却把塌缩后的状态写进去；同服务的 receipts/query 映射器倒是正确回放原结果。
- **Evidence:** 双端引证如上（refuter 与本人独立核对）；IT 覆盖缺口：`SyncContentFlowIT` S03 只回放 APPLIED、S06 断言首次 PENDING 后从不重试。
- **Fix:** `handleReplay` 按 `existing.outcome()` 分支回放原状态（至少正确填 `original_status`）；客户端对 null rev 的 ALREADY_APPLIED 拒绝推进 base。
- **Confidence:** 0.9 · **Verdict:** CONFIRMED

**[P1] 冲突解决烧 rev 不写 change 行，拉取永久自旋——`services/content-service/.../SyncConflictService.java:170`**（execution 孪生 `:170` 同构；`DeletionSagaService.java:284` GC 清墓碑同机制、触发更稀）
- **Problem:** `resolve()` 只有 `syncHeads.incrementCommittedRev(ownerId)`，类根本未注入 SyncChangeRepository；全服务唯一 `syncChanges.save` 在 `SyncPushService.java:624`。
- **Failure scenario:** rev N+1 无 change 行。对端拉取 `hasMore = truncated || lastRev < highWatermark`（`SyncPullService.java:151-183`），游标钉在 (N, N+1] 空窗口且 **H=N+1 烤进游标**——之后任何人 push N+2 也在窗外，被困设备永不自愈；游标每轮重签发使 7 天过期永不触发；唯一出口是 180 天保留期 GC 抬 `min_retained_rev` 后 410 全量 rebase。KEEP_REMOTE 纯标记也触发；KEEP_LOCAL/MERGED 的新 revision 更是任何设备都拉不到。客户端只有 `MAX_PULL_PAGES=32` 页帽（`engine.rs:41`）说明作者已知自旋却未治本。对照组：`requestSoftDelete` 的 increment 就正确配了 `syncChanges.save`（:132→:158）。
- **Evidence:** refuter 全链引证 + 构造注入清单确认无补偿写者；resolve IT 断言了 rev 递增却不断言 change 行存在。
- **Fix:** 冲突解决路径按 softDelete 模式补 change 信封；或 `hasMore/lastRev` 计入非 change 写者消耗的 rev。
- **Confidence:** 0.9 · **Verdict:** CONFIRMED

**[P1] discover 过滤分页占位符错绑——`services/community-service/internal/store/discover.go:34`**
- **Problem:** 筛选参数先 append（language→$2 / domain→$2,$3），keyset 分支却硬编码 `$2,$3`（newest，:41）与 `$2…$3,$4`（most_acquired，:34-35）；quality 分支用 `itoa(len(args))` 动态编号（:109-110）证明这是遗漏而非口径。
- **Failure scenario:** `GET /internal/v1/discover?sort=newest&language=zh&cursor=<服务端签发>`：args=[q,"zh",pubAt,id,limit]，SQL 同时用 $2 当 text（`p.form->>'language' = $2`）和 timestamptz（`(p.published_at,p.id) < ($2,$3)`）→ PG 报 "inconsistent types deduced for parameter $2"；most_acquired 则 "operator does not exist: integer < text"。`Normalize` 反而**强制**带筛选翻页必须回传同筛选（`sort.go:51-53`），即过滤列表第 2 页起 100% 失败，经 BFF 发现页暴露给用户。
- **Evidence:** 本人逐行读 15-48 行引证；refuter 确认 `pg_integration_test.go:723-766` 恰好构造了该组合却只跑 `Normalize()` 不执行 SQL，handler 测试跑在 Go 切片假 store 上——结构性测不出 SQL 绑定错误。
- **Fix:** 两分支改用 `itoa(len(args))` 动态编号（照抄 quality/筛选分支）。
- **Confidence:** 0.95 · **Verdict:** CONFIRMED

**[P1] 管理台存储 XSS——`services/bff-admin/src/pages.ts:83`**
- **Problem:** `tr.innerHTML='…<td>'+(c.title||'')+'</td>…'` 原样内插攻击者可控的社区计划标题，全文件无 escape 助手。
- **Failure scenario:** 攻击者发布标题为 `<img src=x onerror=fetch('/admin/v1/moderation/cases?status=open')…>`（约 140-160 字符，全链仅校验非空+≤256 rune：community `publication.go:153-158` 无字符集限制；content `extractTitle` 原样透传；moderation `openCase` 只验 UUID；BFF 逐字节转发）→ 操作员打开队列即同源执行，持其 `sbs_admin` 会话（HttpOnly+SameSite=Strict 对同源脚本无效；决策端点只查 `requireSession`，无 CSRF token/自定义头；无 CSP）批量自动放行/下架、翻转 kill-switch（普通键立即生效，`flagpolicy_test.go:136`）。
- **Evidence:** refuter 从 content→community→moderation→BFF→sink 全链逐跳引证零转义；apisix ip 白名单不构成缓解（请求源自白名单网内操作员浏览器本体）。
- **Fix:** 标题列与 flag 表字段改 `textContent` 或加 `esc()`。
- **Confidence:** 0.9 · **Verdict:** CONFIRMED

**[P1] 悬浮球关闭不退出——`apps/desktop/src-tauri/src/lib.rs:347`**
- **Problem:** orb CloseRequested 非确认分支只调 `state.with(|engine| engine.pause_on_exit())`，无 `app.exit(0)`、无 `prevent_close`；注释却写"正常退出"。
- **Failure scenario:** 四窗口启动即创建（tauri.conf 全 `visible:false`），main 的关闭被 `prevent_close + hide()` 接住（:318-322）永不销毁；tauri-runtime-wry 2.12.1 仅在窗口表**清空**时发 `ExitRequested`（`lib.rs:4256-4271`），销毁 orb 后仍剩 3 项 → 事件循环继续跑：autosync、auth 静默刷新、quality 队列、archive reaper 全部活着，持 token 后台同步；Wayland 下托盘可能装不上（lib.rs:304 非致命）→ 无任何可见表面的僵尸进程。仓库自己的契约（CLAUDE.md:59 "关掉它才退出"）被打破；全仓仅托盘菜单与 confirm_leave 两处 `app.exit(0)`。
- **Evidence:** refuter 引用 tauri 2.12.1 源码级机制；触发面为 WM 发起的关闭（Alt+F4 等，orb 无装饰窗无内置关闭钮）——已注明该条件。
- **Fix:** 非确认分支显式 `app.exit(0)`（或监听 orb Destroyed 后调用 exit）。
- **Confidence:** 0.85 · **Verdict:** CONFIRMED

**[P1] 离线交付包钉错 Dragonfly 镜像——`infra/offline-bundle/build.sh:20`、`infra/compose/docker-compose.offline.yml:17`**
- **Problem:** `IMAGES+=(postgres:18 dragonflydb/dragonfly:v2.0.0 …)` 与离线 compose 的 `${HARBOR_REGISTRY}/library/dragonfly:v2.0.0`，而版本锁与开发 compose 已纠正为 v1.38.0。
- **Failure scenario:** CI 每次 main 都构建该包（ci.yml:301 `make offline-bundle`，含镜像 tar 与 compose 文件），交付到涉密内网后 `docker compose -f docker-compose.yml -f docker-compose.offline.yml up` 用 v2.0.0 覆盖回正确值。仓库自己的运行时验证记录（versions.lock.yaml:71-72、release-blockers.yaml:214）写明该镜像"已无 dragonfly 二进制（入口变 redis-server 7.4）"，且 7 个服务 startup 依赖该端点健康探针——交付物里是许可与选型意图（例外②：为避 Redis AGPLv3/SSPL 才选 Dragonfly BSL）都不对的引擎；git 考古证明纠偏 commit `2be84ce` 改了 build.sh 却漏了镜像行、完全没碰 offline.yml（非有意例外）。
- **Evidence:** 双 refuter 独立 CONFIRMED（复现视角认为 P2、影响视角 P1，取 P1：这是 CI 实产、以 versions.lock.yaml:4 为交付单位的正式交付物）；两行修复。
- **Fix:** 两处改 v1.38.0 对齐版本锁。
- **Confidence:** 0.95 · **Verdict:** CONFIRMED

**[P2] billing 双击竞态双扣——`services/billing-service/.../BillingWorkflow.java:94`**
- **Problem:** `store.transitionPayment(orderId, order.paymentState(), ATTEMPTING)` 的 `Optional<OrderRow>` 返回值被丢弃，违背 BillingStore:99 "非法迁移 0 行由调用方判"的契约。
- **Failure scenario:** 并发双 attemptPayment 都读到 pending、都过 requireTransition、第二个 UPDATE 0 行被无视，双双以不同 UUID pspKey 调 PSP、双 insertAttempt（psp 幂等键唯一但键不同）。当前 `BILLING_PSP_MODE:stub` 无真钱损失（但留下重复 attempt 行）；V4-4D 真适配器接入即双扣。
- **Evidence:** 本人内联逐行引证（billing correctness refuter 因 429 失败，由我补证）。
- **Fix:** 检查 Optional，空即抛 FAILED_PRECONDITION。
- **Confidence:** 0.85 · **Verdict:** CONFIRMED

**[P2] 账本无事务 + 状态机无恢复路径——`services/billing-service/.../BillingStore.java:154`**
- **Problem:** `postJournal`/`postReversal` 无 @Transactional（全类仅 :100/:110/:243 三处），javadoc(:149) 却称"journal＋lines 同事务"；workflow 层亦零事务。
- **Failure scenario:** ① journals 落库后循环插 lines 中途连接断 → 只贷不借的 journal 永久违反 Σdr=Σcr（触发器禁 UPDATE/DELETE、business_key 唯一挡住重投，无修复路径）；② PSP 抛错后订单卡 ATTEMPTING——attemptPayment 只接受 pending/failed 起、resolveUnknown 只接受 unknown，无 API 出路；③ paid 迁移提交后崩 → PAID 无账，且无再投入口。
- **Evidence:** 本人内联引证 @Transactional 分布与方法体。
- **Fix:** postJournal/postReversal 加事务；为 ATTEMPTING 提供对账恢复入口。
- **Confidence:** 0.85 · **Verdict:** CONFIRMED

**[P2] v2 introspect 不验账号状态——`services/identity-service/.../SessionV2Service.java:156`**
- **Problem:** 只查 `session.isActive()` 与 epoch；无 `user.isActive()`（v1 TokenService.resolve 有），且 `user != null &&` 条件使 user 缺失时 epoch 校验被跳过。
- **Failure scenario:** 账号被置 locked/deleted 而 epoch 未动（isActive() 正为此存在）：15 分钟 TTL 内 introspect 恒 active=true，rotateSession 持续铸新 token——锁定账号不失访问；遗留 token 行的已删用户同样放行。
- **Evidence:** 本人引证方法体（无 isActive 调用、user 判空条件在引文中）。
- **Fix:** 对齐 v1：校验 user.isActive()，user 缺失即拒。
- **Confidence:** 0.8 · **Verdict:** CONFIRMED

**[P2] 跨设备撤销绕过 step-up——`services/identity-service/.../IdentityV2Service.java:793`**
- **Problem:** `if (stepUpGrant != null && !stepUpGrant.isBlank())` 才 consumeGrant，撤销无条件继续；javadoc(:786) 称"跨设备撤销，需 step-up grant"。
- **Failure scenario:** 持被盗 15 分钟 access token 者直接 `POST /internal/v2/sessions/revoke` 清空受害者其余会话（免重认证的 logout-everywhere DoS）。
- **Evidence:** 本人引证 793-804 行。
- **Fix:** grant 缺失即 FAILED_PRECONDITION。
- **Confidence:** 0.85 · **Verdict:** CONFIRMED

**[P2] creator 主体自报注入——`services/creator-service/.../ApplicationRestController.java:209`**
- **Problem:** `case "supplement" -> ok(wf.submitSupplement(appId, str(body, "creator_id"), …))`，appeal/eligibility 同型；处理器签名里的 X-User-Id 被无视，违背本文件"客户端自报不可入"规则。
- **Failure scenario:** 知道 application_id 与属主 creator_id 的调用方（BFF 转发体字段可控）以受害者名义提交补证/申诉，`row.creatorId().equals(creatorId)` 校验因攻击者恰好报对 id 而通过。
- **Evidence:** 本人引证 switch 分支。
- **Fix:** creator_id 一律取 token 解析出的 X-User-Id。
- **Confidence:** 0.75 · **Verdict:** CONFIRMED

**[P2] 质量证据跨用户缓存投毒——`services/rating-service/.../CommunityAcquisitionClient.java:102`**
- **Problem:** `cache.put(acquisitionId, …)` 键不含 userId，而 community 侧 resolve 按 owner 鉴权。
- **Failure scenario:** 用户 A resolve 成功（RESOLVED 入缓存）；5 分钟内用户 B 引用同 acquisition 提交质量事件——拿到 A 的 RESOLVED 投影而非 403→unverifiable_source 排除，伪造事件计入该计划公共质量指标（反向：攻击者的 403 缓存让真属主被错排 5 分钟）。
- **Evidence:** 本人引证缓存写入行。
- **Fix:** 键改 (acquisitionId, userId)。
- **Confidence:** 0.8 · **Verdict:** CONFIRMED

**[P2] 资格调整清空服务范围——`services/creator-service/.../ApplicationWorkflow.java:210`**
- **Problem:** `store.upsertEligibility(creatorId, target, List.of(), …)`——每次 adjust（含 resume）都以空 scope 覆写。
- **Failure scenario:** 批准时授 scope=['paid_plans','consulting']；suspend→resume 后 upsert 把 scope 换成 []：客户端 `state==ACTIVE` 解锁通过、scope 门控功能静默全失。
- **Evidence:** 本人引证 210-212 行。
- **Fix:** upsert 保留既有 scope（或按动作显式传参）。
- **Confidence:** 0.8 · **Verdict:** CONFIRMED

**[P2] wasNull 串列读错试用参数——`services/creator-service/.../ApplicationStore.java:44`**
- **Problem:** `rs.getBoolean("manual_trial"), rs.getString("trial_domain"), rs.wasNull() ? null : amount, rs.wasNull() ? null : concurrency`——wasNull 反映的是最后读的 trial_domain。
- **Failure scenario:** trial_domain=NULL 但 trial_amount_minor=100 的行：amount 被误报 null；反之 manual_trial 行的 NULL amount 被报 0——Admin 队列/作者状态/toProto 输出错误试用额度（付费试用显示为免费 0）。
- **Evidence:** 本人逐行引证。
- **Fix:** 各列 get 后立即取本列 wasNull。
- **Confidence:** 0.9 · **Verdict:** CONFIRMED

**[P2] 死信队列无限重投——`services/rating-service/.../MetricStore.java:171`**
- **Problem:** `deadLetterOutbox` 只置 `state='failed'` 不推 next_attempt_at；`findDueOutbox`（:145）却选 `state IN ('pending','failed') AND next_attempt_at <= now()`。
- **Failure scenario:** community 对某快照回 400 → 死信（next_attempt_at 留在过去）→ 每 15s 周期重新捞出→重 POST→再 400→再死信：违背"4xx 不再重试"注释的零退避死循环，持续骚扰上游与 warn 日志（moderation 的 relay 只选 pending，正确对照组）。
- **Evidence:** 本人引证两段 SQL。
- **Fix:** 死信行推 next_attempt_at 至无穷远或单独 state。
- **Confidence:** 0.85 · **Verdict:** CONFIRMED

**[P2] 证据去重吞自愈——`services/rating-service/.../EvidenceService.java:85`**
- **Problem:** duplicate 分支在标记阶段（:88-142，唯一 `updateEvidenceRow` 调用在 :140）之前早退返回 "duplicate"；`RatingStore.java:141-144` javadoc 承诺"退避重试后走 duplicate 同 hash 路径重进本方法自愈补标"从未兑现，且无任何 reconciler。
- **Failure scenario:** 插入（plan_id=NULL、risk_state='received'）与标记两事务之间进程死亡/DB 失败/社区 200 但坏 UUID（:108 抛 IllegalArgumentException，已提交）→ 重试命中 duplicate 早退 → 行永久无标记、plan_id 永远 NULL；Rust 客户端把 duplicate 当终态收讫（quality.rs:128）。实害：合法事件静默漏计出作者漏斗（公共指标污染一说被驳：`plan_id = ?` 不匹配 NULL，funnelCounts 反而看不见它）。
- **Evidence:** refuter 全链引证 + `updateEvidenceRow` 全仓唯一调用点 grep。
- **Fix:** duplicate 分支按当前行状态补跑标记，或后台补标任务。
- **Confidence:** 0.85 · **Verdict:** CONFIRMED

**[P2] 吞错解引用 panic——`services/community-service/internal/handler/handler.go:1122`**
- **Problem:** `fresh, _ := h.Store.FindQuestion(r.Context(), q.ID)` 后直接 `fresh.Status`。
- **Failure scenario:** INSERT 提交后回读遇瞬时 DB 错误（池耗尽/重启）→ (nil, err) → handler goroutine nil-deref panic，请求裸 500/连接复位而非 JSON 错误体。
- **Evidence:** 本人引证 1122-1126 行。
- **Fix:** 错误分支返回 domain 错误信封。
- **Confidence:** 0.85 · **Verdict:** CONFIRMED

**[P2] 上游 5xx 伪装 401 引发登出风暴——`services/bff-desktop/src/clients/contentClient.ts:77`**
- **Problem:** `if (r.status !== 200 || …) throw …{statusCode: 401}`——identity 返回 503（fetch 成功、HTTP 5xx，不走 IdentityUnavailableError 抛出路径）也被归为令牌无效；文件头注释自己写着"绝不伪装 401"。
- **Failure scenario:** identity 库挂而服务在：所有 `/v2/assets`、`/v2/sync/*`、`/v2/community`、`/v2/creator`、`/v1/consent` 全回 401 → 桌面端判定凭证失效，把每个在线用户登出。
- **Evidence:** 本人引证 77-79 行与文件头不变量注释。
- **Fix:** 仅 401/403 映射 401；5xx 上抛 503 SERVICE_DEGRADED。
- **Confidence:** 0.85 · **Verdict:** CONFIRMED

**[P2] 网关配置三重缺陷——`services/api-gateway/config/apisix.yaml:7`**
- **Problem:** ① 仅 `uri: /v1/*` 路由 bff-desktop，BFF 实际注册的 `/v2/auth|me|me/assets|sync|community|creator` 全部不可达（openapi 同源声明 /v1 与 /v2）；② `upstream_id: bff-desktop`（:17）引用的是 route id 而非 Upstream 对象（配置目录无 upstreams 文件）；③ `websocket: true`（:50）不是 APISIX 插件（应为 upstream 级 `enable_websocket`）。另 compose 把**目录**挂到 apisix.yaml 文件路径且未配 standalone、无 etcd——配置根本不会加载。
- **Failure scenario:** 任何网关前置环境 /v2 全 404；standalone 模式下悬空引用/未知插件在加载期即拒。realtime-gateway README(:35,:74) 声称 WS 经 APISIX chash 粘性——文档与现实不符（R9）。当前无环境走网关（dev 直连 127.0.0.1:8080），故 P2 而非 P1。
- **Evidence:** refuter 全文引证 + bff server.ts 路由清单对照。
- **Fix:** 补 /v2 路由、改用真实 upstream 定义、去伪插件、修挂载。
- **Confidence:** 0.85 · **Verdict:** CONFIRMED

**[P2] 版本锁大面积缺登（R8 红线）——`infra/versions.lock.yaml:110`**
- **Problem:** ai 节仅 litellm/pydantic/vllm/ollama 四项；ml-worker 的 pandas/scikit-learn/datasketch/pymysql 与 ai-orchestrator 的 fastapi/uvicorn/asyncpg/redis/opentelemetry-* 全部未登记；client 节缺 fastify、@grpc/grpc-js、@sentry/browser、dompurify、echarts、i18next、zustand、@tauri-apps/plugin-updater 等运行时依赖。
- **Failure scenario:** versions.lock.yaml:4 "私有化交付以本文件锁定的组合版本为交付单位"——整个服务栈与 TS 运行面（含安全相关的 dompurify、updater 插件）浮动于 ^ 区间，交付"组合版本快照"不可复现、升级无锁文件评审。
- **Evidence:** 本人 grep 核对两 pyproject 与 lock 内容（`bcprov` 等亦无命中）。
- **Fix:** 补登；identity build.gradle.kts:40 硬编码 bcprov-jdk18on:1.86 同步改 `locked()`。
- **Confidence:** 0.9 · **Verdict:** CONFIRMED

### 未入榜已确认项（受 deep 模式 20 条上限截断，均为已验证事实）

- identity `IdentityV2Endpoints.java:291` 游标用原始 limit 判定而服务层封顶 50（`limit<=0||>100?50`）→ 默认/大 limit 下安全审计历史被静默截断（P2）
- BFF `ratingClient/creatorClient` 与 bff-admin `upstreamFetch` 无 `AbortSignal.timeout`（其余四个 client 均有 3-10s）→ 挂起上游无限占连接（P2）
- bff-desktop openapi↔代码双向漂移：spec `/v2/assets/*`、wechat、imports、snapshot-ack vs 代码 `/v2/me/assets/*`、`/v2/sync/execution/imports`；发布表单 10 字段未入 spec（P2）
- `RATING_INTERNAL_TOKEN ?? ""` fail-open 且无启动守卫（其余四个 token 均有守卫）（P2）
- Rust sync 模块约 38 条业务库 SQL 绕过 statements.rs 白名单（R4 字面命中，全部参数化无注入；statements.rs:6 "全集就在这个文件里"过度声明且无门禁审计）（P2）
- compose 纳入四个登录档骨架服务（trust/search/notification/job-worker 无 datasource 必 crash-loop，文档诚实标注骨架故降级）与 ml-worker（`CMD python -m app.main` 而 app/ 仅 .gitkeep，离线栈同样起不来）（P2）
- 前端 session store 无 epoch 守卫（execution.ts 有）→ 换号瞬间跨账号投影泄漏（P2）；三个模态无焦点陷阱（R10 a11y）（P2）；`window.rs:548` 未检查 i32 减法（R3 展示层）（P2）
- archive：reaper 不查 running 即过期（P2）、fsync 持双锁（P2）、DB 失败后 Failed 不可重提交（P2）
- ai-orchestrator：真实 provider 接入后事件循环被 `worker.join(2s)` 阻塞 + 超时线程泄漏（PLAUSIBLE，现为启发式替身）；`build_prompt` 在 try 外 `json.loads` 违反"不抛出"契约 → 500（P3）
- 门禁类：`make format-proto-check` 当前红（billing.proto、creator/application.proto 非规范格式）且未接 CI（本人实测）；`pytest --exitzero services/ml-worker` 吞整层退出码（P3）；`locked()` 助手五处复制已现漂移（P3）

### 双向裁决后剔除（REFUTED，附理由）

- billing gRPC 无鉴权（未部署、无调用方、全仓无 gRPC 门禁先例——留 P3 死配置 `billing.internal-token`）
- identity gRPC `INTERNAL_TOKEN` 死常量
- 云写服务端不设闸（三处文档明载"服务端不抛"的既定决策，留 P3 纵深建议）
- DEC-10 热翻转（覆盖的是非冻结常量）
- job-worker gRPC 依赖"矛盾"（README 自洽）

### Open questions / assumptions

- billing 三项（#7/#8）的资金影响以 `BILLING_PSP_MODE=stub` 为现状前提；V4-4D 真适配器落地前无双扣实害，届时 #7 升 P1。
- #5 的触发依赖 WM 发起的关闭（无装饰悬浮球无内置关闭钮）；若目标平台从不产生该事件则降级，但 Alt+F4 常见。
- 两个 refuter 因 API 429 中断（billing 正确性、discover 复现），相关结论已由本人内联引证补齐，非未验证。
- realtime `Subscribe` 返回 `ctx.Err()`（非 ctx 退出时为 nil）依赖 go-redis 内建重连永不关 Channel 的假设成立，未能独立证实——未列 findings。

### Summary

- 这是全仓审计而非增量 diff review：六个 P1 集中在三条主线——同步引擎的幂等/冲突解决语义（#1/#2，静默丢单据与设备永久卡死，最重）、社区发布链的过滤分页与管理台 XSS（#3/#4）、交付物与客户端生命周期（#5/#6）。
- money 侧 billing（#7/#8）与身份侧 identity/creator/rating（#9-#16）共 10 个 P2 已确认，多数修法是一两行的守卫/键/事务补齐；当前 PSP 为 stub、多数触发需并发或上游异常，属"上线前必修"而非"线上在烧"。
- 工程基本面扎实：7 个只读门禁 6 绿，SQL 全参数化，i18n 双语齐，迁移冻结有效，文档对骨架服务状态诚实；主要系统性弱点是**声明与实现的缝隙**（白名单"全集"、网关配置、版本锁覆盖、Nacos 约定）——这类缝隙恰是门禁没盯的地方。
- 残余风险：E2E/集成缺口被假 store 掩盖（discover SQL 绑定、resolve 后拉取均无测试钉住）；未跑任何构建/测试门禁以外的运行时验证（write-semantics 门禁按 overlay 禁令一律未执行）。
- 建议修复顺序：#1/#2（同步语义）→ #4（XSS）→ #3 → #6（两行）→ #5 → 其余 P2 按表序。
