# 代码审查提示词（复核稿）

> **使用说明**：本文档是拟派发给 CodeReview 子代理的完整审查提示词。请复核审查方案是否符合要求；确认或修订后，以本文档全文（去掉本说明块）作为派发提示词执行审查。当前状态：**待复核，未开审**。

---

## 1. 审查目标

- **范围**：当前工作区全部未提交改动（uncommitted changes），含已修改（M）与未跟踪（??）文件。
- **基线**：HEAD = `7c5ba9f feat(realtime): 增加 realtime-gateway 服务及相关接口定义`（分支 `main`）。
- **排除**：`scratch/review/archive-ui-复核截图/` 为验收证据材料，不作审查对象，仅可作交叉参照。
- **审查角色定位**：专职代码审查，只报告不修复，每条结论须有文件行号级证据。

## 2. 改动背景（审查者必读）

本次改动为「计划归档（Plan Archive）」功能竖切，横跨 Rust 内核、IPC 契约、前端与测试四层：

| 层 | 文件 |
|---|---|
| Rust 内核（新增） | `apps/desktop/src-tauri/src/plan_archive/`（mod/codec/format/jobs/markdown/pictures/preflight 七个模块）、`commands/archive.rs`、`db/archive.rs` |
| Rust 内核（修改） | `lib.rs`（命令注册）、`commands/mod.rs`、`commands/session.rs`、`db/mod.rs`、`db/repo.rs`、`db/statements.rs`、`engine/engine.rs`、`auth/mod.rs`、`assets.rs` |
| IPC 契约 | `packages/ipc-contract/rust/src/archive.rs`（契约真源，新增）、`packages/ipc-contract/src/generated/archive.ts`（ts-rs 生成物，新增）、`rust/src/lib.rs`、`rust/src/bin/ipc-gen.rs`、`src/index.ts`、`src/session.ts` |
| 前端 | `store/archive.ts`（新增）、`views/ArchivePanel.tsx`（新增，516 行）、`views/Library.tsx`、`views/NewPlan.tsx`、`components/stepImages.tsx`、`ipc/transport.ts`、`styles/main.css`、`i18n/locales/zh-CN.ts`、`i18n/locales/en-US.ts` |
| 测试 | `tests/browser/specs/archive.spec.mjs`（新增，300 行）、`tests/browser/mock-ipc.ts`、`tests/browser/mock-state.ts` |
| 依赖与配置 | `apps/desktop/src-tauri/Cargo.toml`、`Cargo.lock`、`infra/versions.lock.yaml` |
| 文档 | `TODO.txt` |

## 3. 审查维度与检查清单

三个维度必须同时覆盖：**完整性（需求覆盖）、正确性（逻辑/安全/错误处理/边界）、影响（回归风险/契约漂移）**。

### 3.1 完整性（需求覆盖）

- [ ] 归档闭环完整：发起归档 → preflight 预检 → 后台任务（jobs）→ 产物生成（markdown/pictures/format/codec）→ 进度/状态可查询 → 失败路径可恢复或可重试，链路无断点。
- [ ] IPC 三端一致：Rust 命令在 `lib.rs` invoke_handler 注册、`transport.ts` 暴露、前端调用点使用，三者命令名与载荷形状一致。
- [ ] i18n 双语同步：所有新增 UI 文案在 `zh-CN.ts` 与 `en-US.ts` 同时补齐，无硬编码中文/英文串。
- [ ] 测试覆盖：`archive.spec.mjs` 覆盖归档主路径与至少一条失败路径；`mock-ipc.ts`/`mock-state.ts` 与真实契约形状同步（mock 漂移会导致 E2E 假绿）。
- [ ] `TODO.txt` 中归档相关条目的状态描述与实际交付一致（不夸大、不提前勾选）。

### 3.2 正确性（逻辑/安全/错误处理/边界）

**Rust 侧：**
- [ ] 数值减法一律使用 `checked_sub` 等防御式 API（项目规范）；可失败路径无 `unwrap()`/`expect()`（仅启动期装配允许）。
- [ ] `db/archive.rs`：SQL 全部经 `db/statements.rs` 白名单；全量参数化绑定，无字符串拼接 SQL；读写事务边界正确（归档写入与源数据读取的一致性）。
- [ ] `plan_archive/jobs.rs`：后台任务可取消、进度上报、panic 隔离（spawn 后的 join/错误传播），任务状态落库防进程中断后僵死任务。
- [ ] `plan_archive/pictures.rs`：导出路径/文件名来自用户数据时的路径遍历防护（`..`、绝对路径、symlink）；目标已存在时的覆盖策略明确。
- [ ] `engine/engine.rs` 改动不破坏 Plan/Run 状态迁移不变量：归档后的 plan 状态、禁止重复归档的守卫、归档进行中删除/修改 plan 的并发竞态。
- [ ] `markdown.rs`/`codec.rs`：内容转义正确（步骤文本含 Markdown 特殊字符、换行、图片引用时不破坏产物结构）。

**TS 侧：**
- [ ] `store/archive.ts`：重复请求竞态（连点归档）、组件卸载后 setState、错误态与加载态互斥，Zustand 用法与既有 store 惯例一致。
- [ ] `ArchivePanel.tsx`：空态/加载态/错误态三态齐备；列表 key 稳定；a11y（键盘可达、aria 标注）符合项目既有验收口径。
- [ ] `transport.ts` 改动对既有命令零破坏（只增不改语义）。

**边界值：**
- [ ] 空计划（零步骤）、超长标题/特殊字符文件名、磁盘写失败、产物目录不可写、归档目标路径已存在。

### 3.3 影响（回归风险/契约漂移）

- [ ] **契约漂移**：`generated/archive.ts` 的形状与 `ipc-contract/rust/src/archive.rs` 契约真源一致，且确为 `make ipc-gen` 产物（无手写痕迹）；`index.ts`/`session.ts` 导出改动不影响既有导入方。
- [ ] **生成物保护**：`generated/` 与 proto `gen/` 下无手写修改。
- [ ] **依赖版本锁**：`Cargo.toml` 新增依赖已同步登记 `infra/versions.lock.yaml`（全组件版本单点锁），版本与锁文件不冲突。
- [ ] **迁移冻结**：`db/mod.rs` 若新增 SQLite 迁移，已发布迁移未被修改（`make migration-check` 口径），旧库升级路径可走通。
- [ ] **回归面**：`Library.tsx`/`NewPlan.tsx`/`stepImages.tsx`/`main.css` 改动不破坏既有页面渲染；`db/statements.rs`/`repo.rs` 变更不影响既有 DAO 行为。

## 4. 项目规范红线（命中即按对应严重度上报，不得降级）

| # | 红线 | 严重度 |
|---|---|---|
| R1 | IPC DTO/类型禁止手写：`packages/ipc-contract/src/generated/archive.ts` 必须由 `make ipc-gen` 生成（真源为 Rust 契约定义），发现手写痕迹 | Critical |
| R2 | 契约生成物保护：任何 `generated/`、proto `gen/` 下手改 | Critical |
| R3 | Rust 数值减法未用 `checked_sub` 系防御 API | Critical（可导致 panic/下溢处）/ Warning（纯展示层） |
| R4 | rusqlite 语句绕过 `statements.rs` 白名单，或 SQL 拼接 | Critical |
| R5 | UI 文案非口语化/非动作导向，或 i18n 双语缺失 | Warning |
| R6 | 前端为绕过 lint 使用 `void` 表达式滥用或解绑方法引用（`unbound-method`） | Warning |
| R7 | TS 可选参数冗余类型标注（如 `foo?: T \| undefined`） | Suggestion |
| R8 | 新增三方依赖未登记 `infra/versions.lock.yaml` | Warning |
| R9 | 文档（TODO.txt/交付说明）状态描述与实际不符 | Warning |
| R10 | UI 组件 a11y 缺失（键盘不可达、无 aria） | Warning |

## 5. 验证命令（审查取证用，只读/构建产物级写入）

> 审查过程中可运行以下命令取证；**不得**修改任何源文件、不得运行带写入语义的生成命令（如直接 `make ipc-gen` 后不还原）。

```bash
make generated-check                 # 生成物存在性与一致性门禁（含 IPC 契约漂移）
make test-rust                       # Rust 内核单元 + 黄金样例测试
make sync-fields-check               # Plan/Run 字段显式同步/本地/派生分类覆盖（engine 改动相关）
make migration-check                 # 迁移冻结门禁（db/mod.rs 有迁移改动时必跑）
cd apps/desktop && pnpm lint         # eslint --max-warnings 0 + stylelint
cd apps/desktop && pnpm build        # tsc --noEmit
cd apps/desktop && pnpm exec playwright test tests/browser/specs/archive.spec.mjs   # 归档 E2E 套件（窄化）
```

## 6. 输出格式（审查报告）

- 按严重度分三节，顺序固定：
  1. **Critical Issues (MUST FIX)** — 阻断性：契约漂移、数据损坏、panic、安全漏洞、红线 R1–R4。
  2. **Warnings (SHOULD FIX)** — 应修：错误处理缺失、规范红线 R5/R6/R8–R10、测试覆盖缺口。
  3. **Suggestions (CONSIDER)** — 可选：冗余类型标注、命名与结构改进。
- 每条 finding 固定结构：
  - 一行标题；
  - 位置链接 `[file#Lstart-Lend](/绝对路径)`；
  - 问题简述（为何错、触发条件）；
  - 修复建议：简单修复直接给代码块，复杂修复给方向性方案。
- 报告末尾输出 **Summary of Changes**（3–5 条要点，概括改动实质与总体风险判断）。
- 零 finding 时输出 **PASSED** + Summary of Changes。
- 报告语言：中文；代码标识符保留原文。

## 7. 审查纪律

1. **只报告，不修复**：不改动任何源文件、配置与测试。
2. **证据优先**：每条 finding 必须落到具体文件与行号，不得凭模式臆测；无法定位证据的疑点降级为 Suggestion 并注明"待确认"。
3. **不报风格问题**：格式、命名风格等由 lint/format 工具覆盖的项不作为 finding。
4. **验收截图仅作参照**：`scratch/review/archive-ui-复核截图/` 用于交叉核对 UI 行为，本身不是审查对象。
5. **区分新增与既有**：既有代码的历史问题仅在本次改动使其恶化时上报，并注明"既有问题，本次恶化"。
