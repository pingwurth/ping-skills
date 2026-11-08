# DEVELOPERS.md — 维护 pwiki 引擎

改引擎前先读 SKILL.md(行为契约); 冲突时权威链:
**styles.py 预设 / 门禁行为 > references/ > SKILL.md > LLM 判断**。

## 代码地图(pwiki_lib/)

| 模块 | 职责 | 移植来源 |
|---|---|---|
| `config.py` | 常量单一事实源 + Ctx 工作区解析(SUPPRESS 双位旗标) | qwiki 45-207/919-980 |
| `util.py` | git/die/info/哈希/文件名 | qwiki |
| `yamlite.py` | 往返 YAML 子集(**勿简化** — 18 实战 bug 的 1-3 号修复在此) | qwiki 258-494 原样 |
| `db.py` | SQLite v3 + 事务认领(claim/lease) + glossary/reconcile 表 | qwiki 497-912 + 新 |
| `i18n.py` | locale 表: sheet 身份=slug, 显示名/提示词查表 | qwiki 中文表参数化 |
| `scan.py` | 确定性清单 + expand_scope | repowiki scan_repo 全量 |
| `modules.py` | 模块分解 + D1 断言 + 注册表转换 + depends_on 播种(aliases 数据化) | repowiki detect_modules + qwiki seed_depends_on |
| `corpus.py` | 证据池原语 + D4/D12 分层 + D13 裁剪 | repowiki plan_wiki 62-495 |
| `themes.py` | 18 主题包(数据化)+ check gate + 卡片规划 + plan.md 评审门 | repowiki plan_wiki 498-2044 |
| `planner.py` | plan v3 IO/校验/种子/路径映射/进度 + cmd_plan/validate | qwiki plan 半边 |
| `layers.py` | Qoder 分层递归规划器 | qwiki 2390-2693 |
| `digest.py` | 共享摘要 + 活术语表(--extra 走 D11) | qwiki+repowiki digest |
| `styles.py` | STYLE_PRESETS 唯一真源(文档同源测试锁定) | 新 |
| `gates.py` | 双严重度门禁 + D18/D23 修复 + 发布树验证器 | qwiki 2082-2281 + repowiki validate_wiki |
| `engine.py` | begin/next/submit/fail/skip/regenerate/pause/status/finish | qwiki cmd_* + 认领 |
| `publish.py` | DB→文件 + 索引 + Qoder 元数据 + 链接规范化 + 导入剥注入 | qwiki 3487-3763 + repowiki render |
| `snapshot.py` | 快照/陈旧矩阵/风格漂移/基线刷新 | qwiki cmd_update + 修复 |
| `search.py` | BM25(title×3/headings×2/中文单字/短语加成) | qwiki 原样 |
| `reconcile.py` | D22 改名零 LLM / D7 卡片重写排队 | orchestration 散文 → 代码 |
| `qoderplan.py` | Qoder wiki_plan.yaml emit/lint(字节vs字符上限) | wiki-plan-gen 契约 |
| `cli.py` | argparse 树 + 所有权表派发(改命令不改这里) | 新 |

分层规则: db.py 唯一开 SQLite; 全部状态变更走短 BEGIN IMMEDIATE;
wiki_plan.yaml 唯一人工面; work/ 可再生; 门禁在 submit 不改内容,
注入只在导出层且永不写回 DB(bug10 自毒防线)。

## D 教训保留地图(改动对应模块时先看它保住了什么)

D1 模块覆盖断言(modules.decompose 退出非零) · D2 services 包(≥3 服务模块)
· D3 路径锚定目录(themes) · D4 生成物排除(corpus.is_generated)
· D5 每 sheet 模块目录一份 _module.yaml(publish; Qoder 导出元数据, 引擎 validate 按 plan kind 区分 sheet/卡, 不读此文件) · D6 近邻源修复(themes)
· D7 卡片冲突(reconcile) · D8 骨架声明vs现状(modules+themes 提示子句)
· D9 瘦叶补种(themes.make_leaves) · D10 fail-fast(cli 前置)
· D11 digest 覆写/--extra(digest) · D12 证据分层+theme_declined(corpus+themes)
· D13 兄弟分歧四件套(corpus) · D14 负面断言双验证(writer 报告必填)
· D15 引用分档(styles.cites_band) · D16 知识层引用(styles→gates)
· D17 渲染先于校验(publish 先写 _module.yaml 再 validate --content)
· D18 机械章节来源修复(gates.fix_section_sources) · D19 typo 守卫(publish._d19_guard)
· D20 AI 目录非产品源(scan.IGNORE_DIRS) · D21 pack 锚定(themes, 框架例外=aliases 目标)
· D22 title_check(writer 报告→reconcile) · D23 行数口径+差一钳位(gates)
· D24 arch-notes 须验证(digest 证据规则节)。

qwiki 18 实战 bug 的落点: yamlite(1-3) · planner.page_path_for(4 嵌套序)
· snapshot(5 untrack≠delete) · modules._pom_dep_artifacts(6 注释) /
_imported_names(7 多段) / aliases(8 多形态) · cli._add_common(9 SUPPRESS)
· publish compose_export_content(10 LINING 不落库注入) · planner.module_dir_name
(11 同名目录回退) · gates.strip_code_fences(12) / lint_mermaid(13 哨兵)
· gates.rowget(14) · publish 孤儿 _module.yaml 清理(15) · engine finish 计数取
发布层(16) · scan marker 可见性(17) · publish.normalize_links(18)。

## 2026-11-08 max 审查回修(63 条, 报告文件已随 2026-11 清理移除)

三条 P0 与一个根因簇: regenerate 双分支补派生 path; 卡片判定统一走
`config.is_card_kind()`(计划侧 kind=<种类> / 行侧 kind='card' 两形态);
publish 的 kept 规则改为"已发布 ∩ 计划 − 本次重跑完成"—— 重跑失败保留
上一版, `--prune` 才清扫计划外孤儿。其余: 门禁 Row/dict 归一(meta)、
i18n 发射集中(TOC_SECTION_RE 含 Contents)、preset 键接管风格内联比较
(size_hard/export/sheets)、ctx 缓存(branch/commit/partition/file_sha)、
cite_lines 记忆化、active_run 集合从 config.ACTIVE_RUN_STATES 派生。
回归测试: tests/test_review_fixes.py(17 例)。

## 并发模型(本引擎的头号修复)

- `claim_jobs`: 单 BEGIN IMMEDIATE 事务内先回收过期租约再原子认领
  (UPDATE…WHERE rowid=(SELECT…LIMIT 1)); 并发 next 串行化在写锁上。
- submit/fail/skip 带 `--claim` 令牌匹配; 失配 → 孤儿稿存
  work/orphan-submits/ + run_log, exit 3。set_job_state 的 UPDATE 带
  claim_token 条件并核 rowcount —— 令牌在检查与写入之间被换走也如实报失配。
- 省略 --claim = 单写便利模式, **仅限首次认领(attempts=1)**: 重认领后
  行内令牌可能属于别的 writer, 省略即拒(稿件存孤儿稿) —— 过期 writer
  曾借便利模式顶掉在写 writer 的稿。
- regenerate 取消旧 run 前先挡: 有"已完成未发布"的其他 job 时要求 --force
  (搁置量如实播报+入 run_log)。fail/skip 对 completed 同样要 --force。
- pause/cancel/regenerate 在有租约时拒绝(--force 强撤, 在制稿件走孤儿稿)。
- 建 run 走 create_run_guarded: 检查+插入同一 BEGIN IMMEDIATE —— 并发
  begin 的输家被吸收, 不再叠出双活动 run。
- 回收只发生在下一次 claim 里, 永不定时触发。

## 测试

```bash
cd pwiki && python3 -m pytest tests/ -q     # 全套(~95)
python3 -m pytest tests/test_db.py::test_claim_atomic_no_duplicate_under_threads -q
```

口径: tests/gitrepo.py 造 fixture(行数已知便于精确引用);
test_e2e 含并行 writer(线程)、增量、重风格、崩溃自愈;
test_styles 断言 references/validation.md ⊆ STYLE_PRESETS(文档防漂移)。
修 bug 必须带回归测试(qwiki DEVELOPERS 纪律的延续)。

## 已知取舍

- BM25 每查询全语料现算, 无持久索引(刻意的离线简单性)。
- 单 locale/分区; 字符串已参数化但非多语言并行生成。
- 卡片 scope 的全仓 glob('**')不进跟踪集(会整仓哈希); 具体文件仍覆盖。
- snapshot 与 qwiki DB 不互迁(新 root 新库)。
