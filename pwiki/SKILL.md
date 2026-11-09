---
name: pwiki
description: >-
  Unified repo-wiki engine superseding wiki-plan-gen + qwiki + repowiki —
  plan→digest→generate→publish lifecycle with theme-pack and layered
  planners, module registry + knowledge layer, per-style citation gates,
  snapshot-based incremental updates, parallel-safe job claiming, and
  searchable published wiki. Fully offline. Use for "pwiki", "repo wiki",
  "生成/更新仓库 wiki", "知识库文档", "wiki 检索", or durable evidence-cited
  repository documentation.
---

# pwiki — 统一仓库 wiki 引擎

融合三个前身: **qwiki** 的引擎/生命周期/门禁/快照增量/检索,
**repowiki** 的确定性主题规划器与内容验证器, **wiki-plan-gen** 的 Qoder
IDE 计划文件契约。完全本地离线: 零网络、零遥测, 唯一"模型"是执行本 skill
的 agent; 所有确定性工作(状态机/门禁/快照/增量/检索)在 CLI 里。

```
pwiki() { python3 "<skill目录>/scripts/pwiki.py" "$@"; }
```

默认工作区 `<repo>/.pwiki`(`--root .qoder/repowiki` 得 Qoder-IDE 可导入
布局); locale `zh`; profile `standard`(≤135 页); 风格 `deep`
(`init --style concise` 切换)。所有命令接受 `--repo/--root/--locale`
(子命令前后均可)。退出码 0/1/2(门禁)/3(前置)。

## 生命周期(你驱动的循环)

```
pwiki init --style concise|deep        # 工作区 + config.json + wiki.db
pwiki scan                             # 确定性清单 → work/inventory.json
pwiki modules                          # 模块分解 + D1 覆盖断言 → plan+DB
pwiki plan --themes | --seed | --layers …   # 三路由之一(见下)
pwiki plan --sheets standard --deps    # 速查表 + depends_on 播种
pwiki digest                           # 共享摘要(活术语表从 DB 再生)
pwiki validate                         # 计划门禁(挡 begin)
pwiki begin                            # run + knowledge-first job 队列
pwiki next [--batch N] [--json]        # 原子认领(并行安全)
#   …读 sources/digest/knowledge → 写页 → submit…
pwiki submit <id> --file PAGE.md --summary "…" --key-topics "a,b" \
    [--claim <token>] [--report report.json]
pwiki finish [--lenient|--force]       # LINING → 发布 → 快照(--prune 清孤儿)
#   --lenient 只放宽 H1/无引用两类(与 submit 同契约); --force 全放
pwiki search "鉴权中间件"              # BM25 检索已发布 wiki
```

增量: `pwiki update`(哈希/模块/prompt/目录/风格漂移 → 只排队 stale 项;
树移动零 LLM 重发布; noop 干净退出)。恢复与修复面:
`pwiki begin --missing`(恢复跑 —— 只排队未发布且未跳过的项, kept 规则
原样重发布已发布项)、`pwiki export --direction db_to_file`(发布失败/
树半写后按 DB 重渲染全树)/ `--direction file_to_db`(吸收对手工发布文件
的编辑, 注入层剥除后入库)。发布后编辑:
`pwiki read <id>` / `write <id> --file F.md` / `delete` / `pages --tree`。
和解: `pwiki reconcile --all`(D22 改名零 LLM / D7 卡片重写)。
Qoder IDE: `pwiki qoder-plan`(生成)/ `--emit <path>` / `--lint <path>`(契约见下)。

### plan 三路由

- `--themes`(deep 默认): repowiki 证据驱动主题包(18 包, D12 分层证据门,
  theme_declined 带原因); 出 `work/plan.md` **人工评审门** —— 先给用户
  看再写正文。合并进 plan 并出 diff 报告, 手工字段(repo_summary/limits/
  aliases/author_*/modules)不动。
- `--seed`(concise 默认): qwiki 确定性多种子(overview+getting_started+
  developer_guide+每顶层模块一 domain)。
- `--layers init|apply|commit`: Qoder 分层递归规划(单 `<result>` 应答;
  一次一层; `need_further_planning` 递归)。

计划文件 `wiki_plan.yaml`(v3) 是**唯一人工编辑面** —— 引擎绝不静默覆盖
手工编辑(重建用 `--reset`); `work/` 是可再生缓存。

### 风格档位

plan 级 `style: concise | deep`, begin 时盖章到每个 job; 门禁按此执行
(qwiki 双严重度: submit 警告 / LINING 硬错):
- **concise** = Qoder 短页口径: 无行数下限、sheet 1-3 句零链接、发布层
  注入 `<cite>`+目录。
- **deep** = repowiki 长文口径: 400-520 行、引用分档 40-120(D15)、
  章节来源/图表来源错误级、知识层引用强制(D16)、writer 自带 cite 块。
尺寸表见 `references/validation.md`(与 styles.py 同源, 测试锁定)。

## 非协商规则

1. **先读后写** — 条目只能引用你实际读过的文件(声明源 + 至多 3 个)。
2. **证据强制** — `file://` 引用带真实行范围; 负面断言要 ls AND grep
   双重验证并把命令输出贴进报告(D14)。不编造 API/文件/行为。
3. **不绕门禁** — 不手改 wiki.db、不 `--force` 过 validate、不写计划外
   条目。被拒 submit 回 pending 保 checkpoint —— 修稿重交。
4. **一条目一主题**, 兄弟标题互异, 深度 ≤4; 兄弟源重叠 >65% 会被裁(D13)。
5. **骨架目录**(D8): README 规划一律标「声明」, 与「实现现状」分开,
   不写成已交付行为。
6. **知识优先** — 页面的模块速查表是首要来源; 读源码为取准行号与验证。
7. **中文行文、标识符原文**; 不写生成物(点名生成器即可)、不泄密钥。
8. **并行写作**走 `next --json` + `submit --claim`(令牌失配=孤儿稿,
   稿件不丢)。job 被重新认领过(attempts>1)后 `--claim` 必传 —— 省略即拒,
   防过期 writer 顶掉在写 writer; 详见 `references/parallel-writing.md`。
9. **writer 报告通道**: 标题勘误(D22)/卡片冲突(D7)走 `--report` JSON,
   由 `reconcile` 落地; 额外读的文件(files_read_beyond_sources)与
   不确定点(uncertainties)同文件上 报, 入 run_log 审计; writer 永不自行改名。

## 产物契约

```
<root>/
├── wiki_plan.yaml            # 唯一人工编辑面(v3)
├── config.json · wiki.db     # 配置 · 引擎状态(SQLite)
├── work/                     # 可再生缓存(inventory/plan.md/themes-report/…)
├── <locale>/content/…        # 页面树(父页=同名目录)
│   └── _index.yaml
├── knowledge/<locale>/
│   ├── _index.yaml           # 模块树(root 键 "/")
│   ├── <模块>/_module.yaml · {概述,架构设计,技术栈,编码规范,特殊配置与命令}.md
│   └── <卡片>/<卡片>.md      # YAML frontmatter, 8 类
└── <locale>/meta/{repowiki-metadata.json, generation-state.json}
```

`repowiki-metadata.json` 带 Qoder 台账(wiki_catalogs/wiki_items/
optimized_catalog/uuid5 页 ID)—— `--root .qoder/repowiki` 时可直接被
IDE 的 synchronizeWiki 吸收。

## Qoder IDE 兼容(qoder-plan)

`pwiki qoder-plan` 从 plan v3 生成 Qoder v1 格式的 wiki_plan.yaml,
默认落在 `<root>/qoder/repowiki/`(c060: 不再写分析仓库工作树);
`--emit <path>` 指定输出路径(两 flag 都带值)。
`--lint <path>` 按反编译契约预检(wiki-plan-gen/MECHANICS.md): 7 个截断上限
(title/goal/note 按**字节**、hints/template 按**字符** —— CJK 标题 ≈33
字即触顶)、parent 只能回溯解析、重复 title 在 parent 之后裁决丢后者、
拼错 parent 静默降顶层。

## 参考地图

| 需要 | 读 |
|---|---|
| 页面/速查表/卡片写作契约(双风格) | `references/writing-guide.md` |
| 门禁与尺寸表(与代码同源) | `references/validation.md` |
| 并行 fan-out 与 writer 报告 | `references/parallel-writing.md` |
| deep 页/卡模板 | `templates/` |
| 维护引擎本身 | `DEVELOPERS.md` |

## 汇报

跑完后报告: 各 kind 页数(pages/sheets/cards)、failed/skipped、lining
结果(needs_review 不隐藏)、发布路径、快照 id 与 commit、模块数、
`pwiki search` 用法。验证有错不得宣称成功。
