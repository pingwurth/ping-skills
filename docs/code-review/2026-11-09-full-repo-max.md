# 全仓库 max 审查报告（2026-11-09）

> **回修状态（2026-11-09 当日完成）**：52 条中 **48 修复 / 3 跳过（F26、F28 结构性重构留待后续，F37 记录在案的有意权衡）/ 1 部分修复（F33：进程内批量化已做，跨进程合并按发现原文允许不做）**。终验：java 553 绿 · batch 654 绿 · sensitive-log-review 273+5s 绿 · sensors-analyze 50 绿 · code-review-p 221 绿（node JS 侧实跑）· pwiki 318 绿；install.sh 四条实证场景 + tar 预排除复制清单比对通过。分组归属：F01/F10/F11/F17/F18/F21/F23/F24/F25/F29/F40/F41/F46/F48/F49/F50/F51 + install.sh F02/F19/F20/F22 由主线修复；sensitive-log-review 11 条、sensors-analyze 5 条、code-review-p 8 条、pwiki 4 条由并行代理修复后主线复验。



- **范围**：整仓 —— 8 个 skill + `install.sh` + 根级文件（dev @ acd3c27）
- **流水线**：10 查找角度 → 56 原始候选 → 去重 → 23 个验证代理 + 主审对照源码直验 → 驳回 4 → gap sweep 补扫 +4
- **结果**：**52 个发现**（50 确认 / 2 合理），其中 **12 个有实证复现**
- **完整细节**（summary + failure_scenario 原文）：[2026-11-09-full-repo-max.json](2026-11-09-full-repo-max.json)

## 分布

| 位置 | 数量 |
|---|---|
| sensitive-log-review | 11 |
| code-review-p | 11 |
| pwiki | 6 |
| install.sh（根） | 5 |
| java-unit-test-generator（含 jaut 共享） | 6 |
| batch-unit-test-generator | 4 |
| sensors-analyze | 5 |
| claude-review | 1 |
| code-review | 1 |
| 根级（CLAUDE.md / .gitignore） | 2 |

分层：🔴 实证复现 bug ×12 ｜ 🟠 确认 bug ×11 ｜ 🟡 潜在/结构性 ×17 ｜ ⚡ 性能 ×7 ｜ ⚪ 卫生/死代码 ×5

## 🔴 实证复现的正确性 bug（12）

| # | 位置 | 一句话 |
|---|---|---|
| F03 | `sensitive-log-review/scripts/java_log_scanner.py:117` | 字符字面量 `'('`/`')'` 损坏括号深度 → 后续所有日志语句被吞，敏感泄漏 0 上报 |
| F05 | `sensitive-log-review/scripts/common/git_utils.py:68` | git 解析无 `-z`/`quotepath=false`，中文路径 C-quote 后 exists() 为假 → 整个文件从两个 gate 静默剔除 |
| F09 | `sensitive-log-review/scripts/main.py:806` | warn-only 步骤（tostring/miss_comments）扫描器失败不进 critical_errors → `--fail-on tostring` 在没跑的检查上"干净"通过 |
| F11 | `batch-unit-test-generator/scripts/batch_diff.py:282` | numstat 大括号重命名形式与 --name-only 永不匹配 → +0/-0；`replace("\\","/")` 进一步损坏 CJK 路径转义 |
| F12 | `sensors-analyze/scripts/locate_call_sites.py:74` | rg 调用缺 `encoding='utf-8'` → C locale 下 UnicodeDecodeError，整步"执行失败"；cp936 下落乱码进 call_sites.json |
| F13 | `sensors-analyze/scripts/extract_entries.py:42` | `_rg` 遇 rg exit 2（LLM 写的 lookahead）静默返回 [] → 虚假 audit_issues=True 逐出用户回步骤 1；还缺 `_RG_EXCLUDE_ARGS` |
| F14 | `sensors-analyze/scripts/review_report.py:284` | 步骤 5.5 原地 "w" 重写 sensors.csv 无备份，截断畸形行溢出值 → 人工修复证据被销毁且截断值被归档固化 |
| F15 | `code-review-p/scripts/crp/handlers.py:391` | `--transcript-dir` 模式 args.file=None → `Path(None)` TypeError 逃过捕获，变通用 exit-2 |
| F18 | `batch-unit-test-generator/scripts/jacoco/fast-single-cov.sh:147` | 9e0d621 删了 `set -e`/mvn 退出码检查 → surefire 插件解析失败落入误导性 argLine 分支并触发注定再失败的全 mvn 回退 |
| F19 | `install.sh:69` | 空白/未知工具参数 → 零选择无守卫、子 shell exit 1 被吞 → 打印"✅ 安装完成"退出 0，什么都没装 |
| F20 | `install.sh:178` | 覆盖提示 `read </dev/tty` 缺 227 行同款无-tty 加固 → CI/无 tty 重装直接中止 |
| F22 | `install.sh:196` | strip 清单漏掉 skill 根开发件 → sensors-analyze 的 `问题.txt`、`trigger-eval.json` 被原样装进用户项目 |

## 🟠 已确认 bug（未复现，11）

| # | 位置 | 一句话 |
|---|---|---|
| F01 | `batch-unit-test-generator/scripts/make_plan.py:318` | e7dff8b 的五变体测试类探测只修了 java 副本；batch make_plan 仍硬编码 `{S}Test` → 建重复测试类、测错类、覆盖率误判 pending |
| F02 | `install.sh:240` | opencode 的 tools 剥离 find 扫整个共享 `agents/` 目录 → 误删用户自己 agent 的 tools 白名单 |
| F04 | `code-review-p/workflows/deep-review.js:235` | 59925ab 后 approval='denied' 从不传进载体提示，执行器按来源而非批准定键 → 被拒 gate 仍被执行（与 SKILL.md 矛盾） |
| F06 | `pwiki/scripts/pwiki_lib/snapshot.py:335` | cmd_update 样式漂移分支 `pid` 未绑定（推导式不泄漏）→ NameError，update 当场崩 |
| F07 | `sensitive-log-review/scripts/main.py:205` | 步骤 2/5/6 只认 rc==2，信号杀死（rc=-9）算成功 → "门禁通过"且计数 0 |
| F08 | `code-review-p/scripts/crp/scope.py:318` | 仅-untracked scope 存 `scope_command=""`，deep-review.js 硬拒空 scope → carrier1 死路 |
| F10 | `java-unit-test-generator/references/UnitTestRules.md:159` | 规则 e 仍硬编码 `<类名>Test.java`（两副本同文），与引擎五变体采用相矛盾 → 守规 LLM 建重复骨架文件 |
| F16 | `code-review-p/scripts/crp/scope.py:264` | 两个 PR rung 插入/删除恒传 (0,0) → 行数扇出规则对 PR scope 失效，账本报 0 行 |
| F17 | `batch-unit-test-generator/scripts/batch_finish.py:283` | "recheck 自愈"不存在：make_plan exit 3、batch_init 拒跑、子 agent 禁跑 init_coverage → 空转一整轮后 failed |
| F21 | `claude-review/SKILL.md:45` | Phase 0 看不见 untracked、硬编码 main 回退、任意 HEAD~1 → 全新文件审查静默空转（code-review 已修的同款 v1.0 病） |
| F23 | `CLAUDE.md:62` | sensitive-log-review/README.md（496 行主文档）被无关 commit abd3992 删掉未恢复，CLAUDE.md 指针悬空 |

## 🟡 潜在/结构性债务（17）

| # | 位置 | 一句话 |
|---|---|---|
| F24 | `jaut/state.py:148`（两副本） | `.locked()` 裸 flock 无重入/超时防护 —— crp 副本因"实测自锁挂死"已修，jaut×2 未跟 |
| F25 | `java…/verify_coverage.py:102` | java 副本内联回退不备份 fast-single-cov 失败日志；回退判据现存 3 处且已不一致 |
| F26 | `crp/journal.py:232` | 裁决聚合三处并行实现（verify.py / journal.py / deep-review.js），历史上已两次同错；无 node 时 parity 测试跳过 |
| F27 | `crp/handlers.py:705` | 陈旧答案防护需四套按键公式（P10 已咬两次）；根修应是单一 save/emit 点记 pending_question |
| F28 | `crp/decisions.py:326` | carrier-1 契约散落三个模块的字面量检查（已产出过一个协议级 bug），应收敛为 capability 记录 |
| F29 | `code-review/references/calibration.md:53` | 与 code-review-p 的近重复副本漂移成矛盾：无 whole-file 豁免 → 同输入两个 skill 结论相反 |
| F30 | `pwiki/layers.py:279` | 图层提示硬编码 deep 预设事实而非从 STYLE_PRESETS 派生（历史上已咬过一次整批 LINING 拒绝） |
| F31 | `sensitive-log-review/scripts/main.py:80` | GATE_KEYS/counts/warn-only/SUMMARY 键四向字符串同步，无测试锁定，失配即永久读 0 |
| F39 | `crp/decisions.py:221` | max_concurrent 缺省逻辑在两函数重复（今日字符相同，改动一处即失同步） |
| F40 | `crp/protocol.py:1` | 协议块发射器第三份近同副本（枚举已分歧），发射修复需三处手工同步 |
| F41 | `jaut/jacoco.py:60` | `_tag_endswith` 定义 4 次；`report_paths` 同名异签名（tuple vs Path）导入陷阱 |
| F42 | `sensors-analyze/scripts/report_manager.py:33` | CSV 表头 6 个列表字面量硬编码于 3 个文件，SKILL.md 让人加的 manual_note 列不在其中 |
| F45 | `sensitive-log-review/scripts/main.py:198` | 七个 step runner 93% 相同的复制粘贴，rc 契约差异埋在各副本里（已不一致，见 F07/F09） |
| F48 | `jaut/maven.py:365` | java 副本 docstring 说 FQCN、调用方全传简单名；batch 孪生已改对 —— 双副本漂移再犯 |
| F52 | `crp/gitops.py:69` | porcelain_z 内联 subprocess 不走 run_argv（今日对齐，未来漂移风险） |

## ⚡ 性能（7）

| # | 位置 | 一句话 |
|---|---|---|
| F32 | `crp/ledger.py:124` | find_dup 在提交锁内对每候选×每账本行重复 tokenize；token_cache 模式同包已有 |
| F33 | `git_utils.py:106` | 每文件一个 `git diff` 子进程 ×2 个扫描器 = 2N 顺序 fork；一次树级 diff 按 +++ 切分即可 |
| F34 | `check_log_print.py:58` | match_changed_files O(changed×list_lines) 子串扫描（两脚本同款，4 个调用点） |
| F35 | `java_log_scanner.py:201` | 多词短语检查 50k 行 × 81 死迭代 + 不必要的 regex-sub/lower 拷贝 |
| F36 | `pwiki/publish.py:276` | publish_run 每行一个 fsync 事务（含未变行）；文件树侧已有"未变不落盘"，DB 侧没做 |
| F37 | `pwiki/search.py:67` | 每次 search 重 tokenize 全部页面（记录在案的有意权衡，成本真实） |
| F38 | `sensors-analyze/locate_call_sites.py:357` | 每模式一次全树 rg（10-40 次顺序扫描），可合并为单次多 -e |

## ⚪ 卫生/死代码（5）

| # | 位置 | 一句话 |
|---|---|---|
| F43 | `git_utils.py:36` | 全仓四个 git 包装器中唯一无超时的 —— CI gate 可能永久挂起而非 exit 2 |
| F44 | `check_pojo_comments.py:124` | checkstyle JVM 子进程无超时（同上类） |
| F46 | `java…/scripts/_p0_3_verify.py` | 零引用的一次性回归脚本随安装外发给每个用户项目 |
| F47 | `pwiki/db.py:233` | db_meta 只写不读，schema_version 暗示不存在的迁移门 |
| F49 | `install.sh:188` | 先 cp -r 后 prune：双倍 I/O 且每个新运行时目录都会先泄漏再补丁；应预排除复制 + include-list |
| F50 | `.gitignore:231` | `.qoder/repowiki/` 下 125 个生成文件仍被跟踪（同 8202b55 .repowise 事故类），修法 `git rm -r --cached .qoder` |
| F51 | `pwiki/DEVELOPERS.md:60` | 工作区未提交地删了全部 5 份 pwiki/docs/code-review 报告，DEVELOPERS.md 与 4 个测试 docstring 指针悬空 —— **提交前需决定：恢复文件或一并清指针** |
