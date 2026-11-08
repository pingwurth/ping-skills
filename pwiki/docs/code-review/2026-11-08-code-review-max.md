# pwiki 代码审查报告（/code-review max）

- **日期**：2026-11-08
- **范围**：未提交的工作区改动 —— `AGENTS.md`（+4 行）与全新 `pwiki/` skill（`pwiki_lib/` 21 模块约 9.6k 行、测试 2.1k 行、docs/templates）
- **口径**：`--max-findings all` 全量上报（想回默认上限，下次传 `--max-findings default`）
- **验证方式**：21 个库模块全部通读；93 条测试全绿；标注【已复现】的条目均经端到端实跑确认（regenerate 卡片崩溃由三个 agent 独立复现），其余经逐行读码 / 定向 grep / 复现脚本确认。流程：10 个查找角度（1 个因 API 限流改为内联完成）→ 对抗验证（复现 + 通读）→ 补漏扫描（补进 5 条，含 P0 第 3 条）。唯一被排除的怀疑：install.sh 动态发现 skill，`./install.sh claude pwiki` 可用。
- **回修状态（2026-11-08 当日完成）**：63 条中 **62 条全修**；#62 部分修（themes 提示词已按风格参数化，layers/卡片提示词仍中文单语，留待 en-locale 需求出现时一并处理）。审查外新发现 1 条一并修复：snapshot.py `sys` 未导入（plan 校验失败路径 NameError）。回归测试 +17（`tests/test_review_fixes.py`），全套 **110 绿**。
- **统计**：共 **63** 条 —— A 数据丢失/卡死 3 · B 高危正确性 13 · C 一般正确性/契约 17 · D repowiki 保证差距 6 · E 性能 8 · F 死代码/重复/漂移 16。

## 根因簇：卡片 kind 模型不一致（建议回修先落此刀）

计划侧卡片以 `kind=<卡片种类>`（如 `external_dependency`）入计划，而引擎侧（engine / gates / publish）处处期待字面量 `kind='card'`。一条根因同时解释：
#2（update 卡片误路由 + `pwiki next` 崩溃）、#5（`validate --content` 卡片全误报）、#9（卡片改名永不落地 + 每次 update 全量 catalog_changed 噪音）。修好这一处，B 组至少三条随之消解。

## 回修建议顺序

1. 两件零风险小事：根 `.gitignore` 补 `.pwiki/`（#31，提交前必做）；修 SKILL.md `qoder-plan --emit` 文档（#23），顺带改掉 AGENTS.md 里"旧目录原样保留"的错误注（#30）。
2. 卡片 kind 根因（#2 / #5 / #9 一族）。
3. A 组剩余：#1 regenerate 缺 path、#3 kept 规则数据丢失。
4. B 组 13 条 → C 组按需 → D 组（要么补回保证，要么改 AGENTS.md 的"覆盖全部功能"口径）。
5. E 性能 8 条 + F 清理 16 条可合并为一轮。

---

## A. 数据丢失 / 引擎卡死（P0，3 条）✅ 已修

### 1. regenerate 卡片缺 path → finish 崩溃且每次复崩【已复现】
`pwiki/scripts/pwiki_lib/engine.py:559`

cmd_regenerate's card branch builds the job item without a 'path' (cmd_begin:141, snapshot.py:282 and reconcile.py:138 all call card_path_for), so a regenerated card is queued with path='' and finish crashes, wedging the wiki.

**触发**：Active run, `pwiki regenerate <card-id>` → `pwiki next` shows path='' (reproduced) → submit → `pwiki finish` → unhandled IsADirectoryError opening `<root>` at publish.py:215, exit 1 (reproduced). Worse, publish_page already INSERTed the card's page row with path='', so every later finish re-crashes; recovery requires hand-editing wiki.db, which the skill forbids.

### 2. update 的卡片判定永不命中 → next 崩溃 / 卡片按页面误发布【已复现】
`pwiki/scripts/pwiki_lib/snapshot.py:273`

cmd_update's card test `job.get("kind") == "card" or ("scope" in job and "kind" not in job)` never matches plan cards (their kind is the card kind like 'external_dependency'), so stale cards are queued as pseudo-pages.

**触发**：Touch a card's source file, `pwiki update` → card queued with kind='external_dependency', card_kind='' and a content-tree path (reproduced); plain `pwiki next` then crashes with KeyError('external_dependency') in _fmt_job's kind_label dict (engine.py:197, reproduced); via --json the card is gated by PAGE gates (schema/h1, schema/frontmatter — cannot pass) and would be published under content/ instead of knowledge/.

### 3. 重跑失败的页面被 finish 连坐删除（数据丢失）
`pwiki/scripts/pwiki_lib/publish.py:202`

publish_run's kept rule (published ∩ plan − this run's job set) drops the last good version of any page whose re-run job failed or was skipped: unpublish_pages deletes its DB row and the prune loop deletes its .md file.

**触发**：Fully published wiki; `pwiki update` queues 1 stale page; writer reports failure → `pwiki fail <id>` → `pwiki finish` → the page is in job_ids (excluded from kept) and not completed (absent from kept_ids) → its previously published row is DELETEd and its file removed from disk — the only good copy of that page is destroyed because a rewrite attempt failed.

## B. 高危正确性（13 条）✅ 已修

### 4. sqlite Row 任务的 D15 引用带宽全错【已复现（agent）】
`pwiki/scripts/pwiki_lib/gates.py:277`

gate_item builds cite_band_for's input via an `item if isinstance(item, dict) else {...}` fallback that hardcodes source_files=[] and reads 'type' (job rows store 'article_type'), so every sqlite3.Row job gets the wrong D15 citation band at submit and finish LINING.

**触发**：Deep-style thin-leaf page (2 planned sources) with 50 citations: lining evaluates band 'standard' (60-90) instead of thin_leaf (40-70) → size/citations error → page bounced to pending, run DOCUMENT_FAILED (agent-reproduced); an overview page with 100 citations inside arch_ai_api (80-120) is likewise bounced at 100 > 90. Only validate --content (dict items) applies the right band, so the two validators disagree.

### 5. validate --content 对卡片全误报【已复现（agent）】
`pwiki/scripts/pwiki_lib/gates.py:475`

validate_content neutralizes its own kind_hint: `item2.setdefault("kind", "card")` is a no-op on plan cards whose kind is already the card kind, so gate_item dispatches every card to the PAGE gates.

**触发**：Any wiki planned with cards (--themes always emits them): `pwiki validate --content` reports 'schema/h1: 应恰有一个 H1, 实得 0' and 'schema/frontmatter: 页面不允许 frontmatter' (plus deep-style cite-block/line/mermaid errors) for every card — exit 2 and needs_review listing all cards (agent-reproduced: a valid card with 3 line-level citations drew 10 errors), even though submit/finish accepted them.

### 6. concise 风格下 validate --content 全面误报引擎自身产出【已复现（agent）】
`pwiki/scripts/pwiki_lib/gates.py:472`

validate_content re-gates the exported file from disk at phase='lining', but for concise/legacy style that file contains the publish layer's own injected `<cite>` block and ## 目录, which the lining gates classify as errors (gates.py:213-218).

**触发**：`pwiki init --style concise` → begin → submit → finish (COMPLETED), then `pwiki validate --content` → every published page errors 'schema/toc: 不要写目录节 —— 发布层会注入' and 'schema/cite: 不要写 `<cite>` 块' → exit 2 on output the engine itself just produced (agent-reproduced end-to-end); the post-publish validator is unusable for concise wikis.

### 7. 风格回退口径不一致 → 一次 update 触发全库重排【已验证（agent）】
`pwiki/scripts/pwiki_lib/snapshot.py:182`

cmd_update resolves style as `plan.get("style") or config.DEFAULT_STYLE` (deep) while begin/submit/finish use `plan.get("style") or ctx.style`, so a plan that lost its style key is deep for update but concise for the run that built the wiki.

**触发**：Hand-trim wiki_plan.yaml's style: key with config.json style=concise (validate only warns): `pwiki update` computes plan_style=deep → style_drift=True → every published concise page re-gated under deep lining (400-520 lines, cite_block_required, D15/D16 all fail) and queued as style_changed for full rewrite (agent-verified: both pages queued style='deep') — a whole-wiki regeneration triggered by a fallback mismatch.

### 8. sheet 提示词与闸门用了两个风格来源【已验证】
`pwiki/scripts/pwiki_lib/planner.py:774`

expand_sheets takes its prompts from ctx.style (config.json, planner.py:717) while begin stamps job style from plan['style'] (engine.py:77) and gates judge by the job stamp — sheet prompts and their judges can be two different styles.

**触发**：plan style=concise, config style=deep (validate only warns '以 plan 为准'): `pwiki plan --sheets standard` writes deep prompts demanding '至少 1 条行级 file:// 引用（D16）' into the plan (verified on disk), begin stamps concise, and finish's concise sheet gate hard-errors any file:// link ('sheet/links: concise 速查表不写 file:// 链接') — a sheet written exactly to its own prompt is LINING-rejected; the inverse pairing fails D16 the same way.

### 9. 卡片改名永不落地 + 每次 update 全量 catalog_changed【已验证】
`pwiki/scripts/pwiki_lib/publish.py:174`

publish_run's kept-republish path builds plan_by_id only from pages_plan, so published cards are republished verbatim — card renames in the plan never land — and update's check_item (snapshot.py:211-217) compares published ordinal 950 vs plan cards' 0, flagging every card catalog_changed on every update.

**触发**：Rename a card title in wiki_plan.yaml, `pwiki update` reports it moved, `pwiki finish` republishes the OLD title/path (verified: rename never appears); with nothing changed at all, `pwiki update` still reports all N cards 'catalog_changed', skips the noop branch and creates an empty INCREMENT run (0 jobs) every time — permanent false signal masking real card drift.

### 10. 重跑 plan --themes 丢弃全部 module_sheet → finish 剪掉已发布 sheet
`pwiki/scripts/pwiki_lib/themes.py:1866`

cmd_plan_themes replaces the whole page set (`plan["pages"] = result["pages"]`) and to_plan_pages emits only kind='page' entries, silently dropping previously expanded module_sheet pages — unlike the layers route which explicitly preserves them (layers.py:330).

**触发**：Run `pwiki plan --sheets standard` then re-run `pwiki plan --themes` (profile change, re-scan): all module sheets vanish behind one 'replaced pages: N dropped' line; the next begin queues no sheets (no knowledge-first ordering, no sheet gates), and the next finish's unconditional prune deletes the already-published sheet .md files as out-of-plan orphans.

### 11. begin --missing 留下 0 任务的活动 run
`pwiki/scripts/pwiki_lib/engine.py:112`

cmd_begin --missing calls create_run (line 112) before knowing the queue: both the `die("没有可恢复的旧 run")` path (118-120) and the 'nothing missing' early return (127-129) leave a committed active run with zero jobs.

**触发**：`pwiki begin --missing` on a workspace with no prior run → exits 2 but has created an active DIRECTORY_GENERATING run; the next plain `pwiki begin` takes the 'run 已在跑, 续用' branch (0/0 jobs) instead of starting a full queue, and `pwiki next` says everything is terminal — the user must know to `pwiki cancel` or `begin --force` to unstick; the 'nothing missing' path likewise strands a bogus active run that finish will 'complete' with 0 jobs.

### 12. --prune 文档写的是开关、实为无条件删除
`pwiki/scripts/pwiki_lib/publish.py:221`

The orphan-.md deletion loop runs unconditionally — the `prune` parameter accepted by publish_run (line 156, wired from `finish --prune`) is never read — while cli.py:179 documents --prune as '删除计划外孤儿页'.

**触发**：User hand-drops a custom note at `<root>/zh/content/部署备注.md` (or keeps pages from an earlier plan): the next `pwiki finish`, with or without --prune, silently deletes it — the documented opt-in flag is a no-op and deletion is the always-on default.

### 13. 同名兄弟页面路径碰撞 → 静默互相覆盖
`pwiki/scripts/pwiki_lib/planner.py:425`

Duplicate sibling page titles are only a validate warning (cards' titles are not checked at all) while file paths are derived from titles (page_path_for / card_path_for), so equal titles map to the identical .md path and publish silently overwrites one page with the other.

**触发**：Hand-edited wiki_plan.yaml (the documented human-edit surface) gives two children of one parent the title 「配置管理」 → validate warns but begin runs, finish publishes both jobs to the same derived path; the second write silently replaces the first page's file while both rows report published — no gate fires. Two cards sharing a title collide the same way with zero warning.

### 14. 依赖卡片 slug 碰撞 → 引擎生成的计划自身过不了 validate
`pwiki/scripts/pwiki_lib/planner.py:619`

seed_cards builds external-dependency card ids as f"dep-{slugify(name)}" where slugify collapses distinct dependency names (scoped and dashed forms) onto the same slug, producing duplicate card ids in a freshly seeded plan.

**触发**：package.json with dependencies `{"@foo/bar": "1", "foo-bar": "2"}` → both slugify to "foo-bar" → two cards with id "dep-foo-bar" → the next `pwiki validate`/`pwiki begin` fails with '重复 page/card id' on an engine-generated plan the user never touched, blocking the run until ids are fixed by hand.

### 15. set_job_state 认领检查 check-then-act 竞态 → 静默丢稿
`pwiki/scripts/pwiki_lib/db.py:511`

set_job_state's claim check is check-then-act: match_claim is a separate SELECT, and the UPDATE's rowcount is never verified, so a token that changed between the two statements yields a silent no-op that still returns True.

**触发**：Writer A's 30-min lease expires while it works; writer B's `pwiki next` reclaims and re-claims the job; if A's submit runs match_claim just before B's commit and its UPDATE after, the UPDATE matches 0 rows yet ok=True → engine prints 'submitted', A's manuscript is neither stored nor orphan-saved (the orphan path only fires on ok is False), and B later overwrites the page from scratch. Narrow window, but the parallel-writer design this module explicitly targets makes it reachable.

### 16. partition 并发首建 → IntegrityError 裸崩
`pwiki/scripts/pwiki_lib/db.py:278`

partition() INSERTs without OR IGNORE / ON CONFLICT despite the UNIQUE(branch, locale) constraint, so two first-time begins racing on a fresh workspace crash one of them.

**触发**：Two agents run `pwiki begin` simultaneously on a workspace with no partition row yet: both SELECT (nothing), both INSERT; the loser gets an unhandled sqlite3.IntegrityError traceback instead of the graceful '续用' path.

## C. 一般正确性 / 契约缺口（17 条）✅ 已修

### 17. file_to_db 无差别剥 cite 块，deep 风格内容被毁
`pwiki/scripts/pwiki_lib/publish.py:654`

export file_to_db strips `<cite>`/TOC from every kind=='page' file regardless of style, but in deep style the cite block is writer-authored canonical content (cite_block_required), not a legacy injection.

**触发**：Deep wiki, user hand-edits a page file and runs `pwiki export --direction file_to_db` → _strip_injected deletes the cite block from the DB copy; the next finish/db_to_file rewrites the file without it, and lining/validate then flag 'schema/cite: deep 页必须自带 `<cite>` 块' against content the engine itself destroyed.

### 18. finish --force / validate --lenient --fix 是死旗标
`pwiki/scripts/pwiki_lib/cli.py:178`

Registered-but-unread flags: `finish --force` is never consulted (engine.py:698 checks only lenient; publish_run's force parameter is unused), and `validate --lenient`/`--fix` are ignored on the plan-validation path (cmd_validate reads only warn_missing).

**触发**：LINING rejects a page → user runs `pwiki finish --force` expecting to force through (submit's error message advertises '--force 强过') → identical rejection, exit 2 again; `pwiki validate --lenient` on a failing plan produces byte-identical errors to plain validate — the user believes a relaxation was applied when nothing changed.

### 19. 单行 off-by-one 引用永不修复；发布层钳位不存在
`pwiki/scripts/pwiki_lib/gates.py:435`

fix_off_by_one returns early when the citation has no explicit end (`if not b`), so single-line off-by-one cites are reported as repairable but never fixed; meanwhile engine.py:696-697 filters 'citation/off-by-one' out of lining on the comment '在发布层钳' — but no publish-layer clamp exists (fix_off_by_one's only caller is validate_content).

**触发**：Writer cites `[x](file://a.py#L5)` on a 4-line file: submit warns, lining ignores it by design, publish exports it unchanged → the shipped page carries #L5 pointing past EOF; only a separate `pwiki validate --content --fix` run repairs range citations — and even that never repairs the single-line form.

### 20. %2520 链接修复计数假报、内容不变【已复现】
`pwiki/scripts/pwiki_lib/publish.py:63`

normalize_links' %2520 double-encoding repair never rewrites anything: line 64 mutates the local `raw` to the %20 form but `label` still holds the original match text, so `label.replace(f"]({raw})", ...)` searches for the mutated string and never matches — while `fixed += 1` was already counted.

**触发**：Writer emits `[see](dir/a%2520b.md)` for the published file `dir/a b.md`: publish reports '1 links normalized' but the exported page still contains the broken a%2520b.md link (reproduced: output unchanged, fixed=1); non-.md links containing %2520 also count as fixed with zero change, inflating the metric.

### 21. 重复 module id 检查是死代码【已验证】
`pwiki/scripts/pwiki_lib/planner.py:212`

The duplicate-module-id check is dead code: `list(mod_ids).count(i)` counts occurrences inside a list built from the already-deduplicated set mod_ids, so every count is 1 (the page/card check at line 243 correctly counts the original list).

**触发**：Hand-edit wiki_plan.yaml with two modules both `id: user-service` (copy-paste slip): `pwiki validate` reports no '重复 module id' error (verified), begin proceeds, and modules_by_id/plan merging silently go last-wins — sheets, coverage and anchoring resolve against an arbitrary one of the two entries.

### 22. *.egg-info glob 永不匹配
`pwiki/scripts/pwiki_lib/scan.py:30`

IGNORE_DIRS contains the glob entry "*.egg-info" but the os.walk filter (line 430) is exact-name membership, so the pattern can never match a real directory like foo.egg-info.

**触发**：Scan a repo containing pkg/foo.egg-info/ (created by `pip install -e .`): the walker descends into it, its files inflate inventory counts/languages/lines and dir maps, and modules.decompose can promote foo.egg-info as a module or evidence dir despite the set's clear intent to exclude it.

### 23. SKILL.md 把 --emit 写成裸旗标 → 照抄必 exit 2【已复现】
`pwiki/SKILL.md:117`

SKILL.md documents `pwiki qoder-plan --emit` as a bare flag ('--emit 从 plan 生成 .qoder/repowiki/wiki_plan.yaml'), but cli.py:214-216 defines --emit (and --lint) as value-taking arguments.

**触发**：Following SKILL.md verbatim: `python3 scripts/pwiki.py qoder-plan --emit` → argparse aborts 'expected one argument' with exit 2 (run and confirmed) — which is also pwiki's documented 'gate rejection' code, so a CI driver misreads a doc-induced usage error as a validation failure; the working invocations (bare command, or `--emit <path>`) appear nowhere in SKILL.md.

### 24. 标题改名不重算子代路径与索引
`pwiki/scripts/pwiki_lib/reconcile.py:85`

_apply_title_rename updates only the renamed page's row and file: children's derived paths (page_path_for walks parent titles), their DB rows, on-disk files, and all indexes (_index.yaml / repowiki-metadata.json) are left stale — though the docstring promises '重算索引'.

**触发**：Reconcile-apply a D22 rename of a page with children: the renamed page moves, but every child's file stays at the old parent-title path while its DB row and the catalog metadata keep old titles/paths until the next finish republishes everything; until then `pwiki read/write` of a child targets the new path while the file lives at the old one.

### 25. H1 重写的 regex 替换反斜杠炸弹
`pwiki/scripts/pwiki_lib/reconcile.py:92`

The H1 rewrite builds the re.sub replacement from the user-supplied title (f"# {proposed}"), so a proposed title containing a backslash is interpreted as a regex replacement escape.

**触发**：A writer report proposes a title containing '\1' or a trailing backslash (e.g. a path-like name): re.sub raises 'bad escape' or splices group text into the H1 — the reconcile command crashes or corrupts the heading instead of using a lambda replacement.

### 26. style_drift 在空 style 时恒为 False
`pwiki/scripts/pwiki_lib/snapshot.py:183`

style_drift is computed as `plan_style != (published and next(iter(published.values()))["style"] or plan_style)` — an empty published style falls through the `or` to plan_style, making the comparison always False, and the representative is an arbitrary first row.

**触发**：A published page with style='' (e.g. regenerated under a plan lacking a style key, or legacy data) as the first row: switching the plan's style afterwards never triggers style-drift re-checks — stale-style pages are silently kept, defeating the documented '风格漂移: 只排队不过关项' behavior.

### 27. 根模块查找落空（'' vs '/'）
`pwiki/scripts/pwiki_lib/layers.py:244`

materialize_layers keys the module map by `str(m.get("path") or "").rstrip("/")` (root becomes '') but looks up a["modules"][0] which apply_layer_result normalized to '/', so root-scoped articles miss the root module entirely.

**触发**：`plan --layers` route: the overview article (modules: ["/"]) gets module='' and source_files ["//"] instead of the root module's scope (README, manifests) — a weird but passing dir entry that leaves the project overview with no real sources.

### 28. 计划级 export_style 是死旋钮
`pwiki/scripts/pwiki_lib/publish.py:102`

compose_export_content consults only ctx.config['export_style'], while the plan-level export_style field written by planner.py:752 and modules.py:767 is never read anywhere — a dead knob on the documented manual-editing surface.

**触发**：User sets `export_style: current` in wiki_plan.yaml expecting as-written export (the field round-trips through plan saves): concise exports still get `<cite>` and a numbered 目录 injected at every finish because the dispatch reads only config.json — the plan value is silently ignored with no warning.

### 29. '[EOF' 字面量关停全部链接归一化
`pwiki/scripts/pwiki_lib/publish.py:51`

`if "[EOF" in content: return content, 0` disables ALL link normalization for any page containing the literal substring '[EOF' anywhere — including a code sample that merely demonstrates an EOF marker.

**触发**：A page whose code block documents a protocol token '[EOF' keeps every broken relative link the normalizer would have fixed, silently, with no report.

### 30. AGENTS.md 新注事实性错误 + 旧 skill 触发词仍在
`AGENTS.md:16`

The added note is factually wrong for this repo: '旧目录原样保留供对照回归' — no wiki-plan-gen/, qwiki/ or repowiki/ directories exist anywhere in this repo or its git history (they live only outside version control in ~/Downloads and ~/.claude|~/.agents/skills), and '覆盖…全部功能' overstates parity given the dropped gates below (D 组).

**触发**：An agent following AGENTS.md to diff pwiki against its predecessors, or to avoid '往旧三目录加功能', finds no such directories; meanwhile the installed ~/.claude/skills/repowiki skill still carries live trigger phrases overlapping pwiki/SKILL.md, so a wiki request can still route to the deprecated engine — and repo CLAUDE.md still says 'five agent skills' with no pwiki row.

### 31. .pwiki/wiki.db 随仓库分发（未 ignore、install 不剥）
`pwiki/.pwiki/wiki.db`

A 94KB runtime sqlite database ships inside the untracked skill tree: .pwiki/ is not covered by the root .gitignore (which lists .codegraph/.repowise/.agent but not .pwiki) and install.sh's strip list (tests/__pycache__/.pytest_cache/test.log/docs/workflows) does not remove it.

**触发**：`git add pwiki/` commits a dev-run wiki.db (binary, churns every use), and `./install.sh claude pwiki` copies it into target projects as .claude/skills/pwiki/.pwiki/wiki.db — stale state from the skill's own repo leaks into every install.

### 32. 输出层单语硬编码，en wiki 被注入中文
`pwiki/scripts/pwiki_lib/i18n.py:68`

The locale tables for emitted content (SECTION_SOURCES, DIAGRAM_SOURCES, TOC_TITLE) have zero consumers: gates.py:87 hardcodes CITE_BLOCK_TITLE["zh"], gates.py:412 injects the literal '**章节来源**', publish.py:110 injects literal '## 目录' — emission is monolingual while the matching regexes are bilingual.

**触发**：An `--locale en` wiki gets Chinese '**本文引用的文件**', '**章节来源**' (from validate --fix) and '## 目录' injected into English pages, and the bilingual gate regexes accept them — mixed-language output passes all gates; SHEET_PROMPTS likewise only has zh entries, so en sheet prompts fall back to Chinese. Renaming a header in i18n.py changes nothing (emitters don't read it), producing pages that fail their own section-source gate.

### 33. 风格语义 7 处内联判定，preset 键无人读
`pwiki/scripts/pwiki_lib/gates.py:211`

Style semantics are re-derived inline at 7 sites (`style == "concise"` at 211; `strict and style == "deep"` at 259/266/283/295; `p is preset("concise")` identity at 361; publish.py:103) while 6 STYLE_PRESETS keys (label, cite_line_ratio, min_sections, toc_required, sheets, export) are never read — the 'single source of truth' is bypassed by its own consumers.

**触发**：Adding a third style touches config, styles, i18n and 7 hardcoded comparisons; any concise-like style silently gets deep treatment. Already drifted: `export_style: current` + concise makes publish.py:103 skip injection while gates.py:216 still errors on a writer-provided `<cite>` block — the same 'who owns the cite block' policy encoded twice under two different keys, in contradiction.

## D. 与 repowiki 的保证差距（6 条）✅ 已修（断链/越scope/纯文档/卡frontmatter补回, cite_ratio 降警告, 去试点化）

### 34. 卡片 frontmatter 闸门较 repowiki 弱化
`pwiki/scripts/pwiki_lib/gates.py:339`

The card frontmatter gate checks only kind and name equality, while pwiki's own card templates (templates/card-system.md, card-business-term.md) emit slug/category/scope/source_files keys — and the superseded repowiki validator additionally errored on missing keys, kind outside CARD_KINDS, and source_files entries not existing on disk.

**触发**：Writer submits a card whose frontmatter carries `source_files: [deleted/path.py]` and a category value that is not one of the 8 kinds: submit and LINING both pass (kind and name match the plan), publish ships a card whose declared evidence cannot be verified — the template contract and the old skill's guarantees are no longer enforced anywhere.

### 35. 断链只警告不断败
`pwiki/scripts/pwiki_lib/gates.py:511`

Broken internal .md links are only a warning in `validate --content` and are never checked at submit or LINING, while the superseded repowiki validator failed validation on them.

**触发**：Writer emits `[配置加载](../配置系统/概要.md)` pointing at a nonexistent page: finish publishes the run as COMPLETED and `validate --content` exits 0 with one warn line — broken navigation ships under the same 'verified' contract under which repowiki would have failed the build.

### 36. 引用越出 scope 无检查
`pwiki/scripts/pwiki_lib/gates.py:188`

No gate compares cited files against the item's declared source_files/scope: the superseded repowiki validator flagged pages citing >10 files outside their listed sources (citation/extra), keeping plan scope and sibling-overlap guarantees honest.

**触发**：A page scoped to services/auth/ cites 40 files from unrelated modules: every citation path validates, all bands pass, and the page ships — the plan's scope assignments and D13 sibling-overlap trimming are silently undone by out-of-scope citations no engine path ever checks.

### 37. 纯文档证据无检查
`pwiki/scripts/pwiki_lib/gates.py:263`

No docs-only evidence check: repowiki warned when a page had fewer than 2 citations to non-.md code files, because documentation cannot evidence code behavior.

**触发**：A deep-style page cites 8 existing README/doc files with valid line ranges: paths exist, bands met, cite block consistent → every gate passes and the page ships with an entire evidence chain of documentation prose rather than code.

### 38. cite_line_ratio 不变量被丢弃
`pwiki/scripts/pwiki_lib/styles.py:26`

The citation line-range ratio invariant (qwiki ARTICLE_CITE_LINE_RATIO=0.85, enforced at LINING) is dropped in both presets (`cite_line_ratio: None`) — the comment marks it deliberate, but it contradicts AGENTS.md's '覆盖…全部功能' claim.

**触发**：A concise page carries 60 citations of which only 10 include #L1-L5 ranges: qwiki LINING rejected it; pwiki accepts it in both styles, so line-less file:// citations that pin no evidence are indistinguishable from verified ones in every gate.

### 39. 试点词汇硬编码在共享 pack 里
`pwiki/scripts/pwiki_lib/themes.py:742`

The module docstring claims full de-pilot-ization, but pilot-specific vocabulary remains hardcoded in shared pack code: the desktop pack's window leaves list the ping-english-assistant window 'orb' (line 742), and the core pack's conditional titles '训练会话状态机'/'字幕解析器'/'句子级播放器' (779-787) key on training/subtitle/player keywords.

**触发**：Any repo whose core matches 'training'/'session' or has subtitle/player-like files gets pages titled with a language-learning app's vocabulary, with no pwiki.yaml way to un-see it; every such fix is a code edit in the 545-line build_packs instead of a data edit in the analyzed repo's overrides file.

## E. 性能（8 条）✅ 已修

### 40. update 全量重哈希（不比对 size+mtime）
`pwiki/scripts/pwiki_lib/snapshot.py:46`

snapshot_hashes unconditionally sha256-hashes every tracked file on every run although it records size+mtime and the file_hash table stores them — the columns are never compared; a stat-only quick check against the baseline would skip unchanged files.

**触发**：On a repo with 10k tracked files, every `pwiki update` re-reads and hashes all 10k contents (~200MB+ I/O) even when 0 files changed; comparing (size, mtime) first reduces a no-change update to 10k stat() calls plus hashing only the changed files.

### 41. validate_citations 无 memoization，一轮 ~5400 次整读
`pwiki/scripts/pwiki_lib/gates.py:63`

validate_citations calls count_file_lines per citation occurrence with no memoization, and the same validation re-runs at submit, again at finish LINING, and again in publish's rebuild_cite_block — each pass fully re-reads every cited file (up to 1MB each).

**触发**：A 100-page wiki citing ~12 distinct files per page: one run performs ~1800 full file reads at submit + ~1800 at lining + up to 1800 more at publish (~5400 reads); a file cited 5 times in one page is read 5 times within a single validate_citations call — a (path, mtime) memo cuts it to one read per distinct file.

### 42. ctx.partition/commit 不缓存 → 一轮 ~400 次 git 子进程
`pwiki/scripts/pwiki_lib/config.py:233`

Ctx.branch/commit/partition are uncached properties: every partition() access spawns a `git rev-parse` subprocess and issues an UPDATE partition SET last_accessed_at; every ctx.commit access spawns another git.

**触发**：cmd_submit calls ctx.partition() up to 3 times (engine.py:364, 327, 469) = 3 git spawns + 3 redundant DB writes per submit; cmd_finish triggers ~5-6 git spawns — across a 135-job loop that is ~400 avoidable fork/execs per run. Cache branch/commit/partition on the instance.

### 43. next --batch 每认领一单重复全量扫描 + 计划重解析
`pwiki/scripts/pwiki_lib/engine.py:300`

cmd_next's batch loop calls ctx.db.jobs(run_id) (SELECT * including every job's multi-KB content blob) once per claimed job, and _job_payload/_fmt_job additionally run pending_counts() and a full load_plan() re-parse per claimed job.

**触发**：`next --batch 8` on a 135-job run executes 8 identical full page_job scans (~1MB+ each), 8 GROUP BY counts and 8 YAML plan parses per CLI call — repeated every round — versus 1 of each with a hoisted variable.

### 44. submit 为一行日志二次全量校验引用
`pwiki/scripts/pwiki_lib/engine.py:484`

cmd_submit runs gates.validate_citations a second time (re-reading every cited file) solely to print 'N files cited', when gate_item 70 lines earlier already computed the identical cited set.

**触发**：Every submitted page pays one extra full re-read of all its cited files for a log line: a page citing 15 files costs ~450KB redundant I/O per submit, ~2000 avoidable reads over a 135-job run.

### 45. _source_hashes 跨页不去重 + finish 内重复哈希
`pwiki/scripts/pwiki_lib/publish.py:488`

_source_hashes re-hashes each source file once per page that lists it (no cross-page dedup), and snapshot_run then re-hashes the exact same tracked set again in the same finish.

**触发**：On a 100-page wiki where each source feeds ~2.5 pages, _write_generation_state performs ~1000 sha256 full-content reads (~400 distinct files), then snapshot_run re-reads the same ~400 — ~1400 hashes per finish versus ~400 with one shared per-run cache.

### 46. path_match 每次重规范化，plan --themes ~10M 次调用
`pwiki/scripts/pwiki_lib/corpus.py:141`

path_match re-lowercases and re-normalizes the full path string on every (file × pattern) check, and Corpus.collect runs ~30-60 times per `plan --themes` pass, each rescanning all N files against ~27 exclusion + K inclusion patterns and re-running the exclusion match a second time over the union.

**触发**：On a 10k-file repo, `plan --themes` performs roughly 10k × ~40 passes × ~30 patterns ≈ 10M+ path_match calls, each allocating a fresh lowered copy of the path; normalizing once per file and dropping the duplicate exclusion pass cuts the constant factor ~30×.

### 47. refresh_baseline 每次全表删写 file_hash
`pwiki/scripts/pwiki_lib/snapshot.py:108`

refresh_baseline_for loads the entire file_hash baseline and set_file_hashes rewrites the whole table (DELETE-all + executemany of every row) just to refresh one page's ~10 tracked files after a single `pwiki write`.

**触发**：Every manual `pwiki write` on a wiki tracking 10k files deletes and reinserts all 10k file_hash rows when only ~10 changed — 1000× the necessary row churn, repeated for each page fixed during a review pass; UPSERT only the refreshed paths.

## F. 死代码 / 重复 / 漂移（16 条）✅ 已修（#62 部分）

### 48. cmd_next 死计算 progress_pct
`pwiki/scripts/pwiki_lib/engine.py:289`

cmd_next computes progress_pct into `pct` and never uses it (only cmd_status consumes the value) — dead computation on every claim.

**触发**：Pure dead code: a reader assumes the printed status reflects the computed progress; it does not (the value is discarded).

### 49. 被拒提交后的续租是必然 no-op
`pwiki/scripts/pwiki_lib/engine.py:436`

After a rejected submit sets the job back to pending, set_job_state has already cleared claim_token/lease, so the following extend_lease(token) matches zero rows — the call is a no-op and the comment's intent (writer keeps working) is dead.

**触发**：Every rejected submit executes a lease extension that cannot fire; harmless at runtime but it documents a guarantee ('rejected submit keeps the lease') the code does not deliver — the job is claimable by anyone immediately.

### 50. UPDATE page 持久化块三处复制粘贴
`pwiki/scripts/pwiki_lib/publish.py:570`

The raw 'UPDATE page SET content=?, content_hash=?, updated_at=?' persistence block is copy-pasted 3× (publish.py:570-574 cmd_write, publish.py:655-659 cmd_export, reconcile.py:94-98), each redoing the sha256/now_iso stamping that db.py otherwise encapsulates.

**触发**：If the page table gains a column or the stamping changes (schema v4), only sites found by grep get fixed — a missed one writes rows whose content_hash no longer matches content, and snapshot's baseline-diff then reports permanent false 'sources_changed' staleness.

### 51. gates 自带第二份 count_file_lines + 死 hasattr
`pwiki/scripts/pwiki_lib/gates.py:36`

gates.count_file_lines re-implements scan.count_lines under the same 'D23 口径' name, and its `config.MAX_TEXT_BYTES if hasattr(config, ...)` guard is dead — the constant lives in scan.py:84, so the hasattr is always False and the 1MB cap is a duplicated magic literal.

**触发**：If D23 semantics or the 1MB cap change in scan, citation validation keeps clamping to the old line count: a file legitimately counted by the scanner validates as out-of-range in gates, and off-by-one fixes write wrong line numbers into exported pages.

### 52. _iso_plus_minutes 与 now_iso 的承重重复
`pwiki/scripts/pwiki_lib/db.py:216`

_iso_plus_minutes re-implements config.now_iso's millisecond format string; the duplication is load-bearing because claim_jobs compares lease_expires_at < now as strings, which only works while both sites emit byte-identical lexicographically-ordered timestamps.

**触发**：If now_iso is ever changed (6-digit micros, '+00:00' offset), lease expiry never compares true → dead writers' jobs are never reclaimed (stuck 'processing') or every lease looks instantly expired (double-claim of the same page by two writers).

### 53. TOC 剥除正则两份重复 + 'Contents' 拼法漏网
`pwiki/scripts/pwiki_lib/publish.py:112`

The injected-ToC removal regex is duplicated verbatim within publish.py (112-113 compose_export_content, 130-131 _strip_injected) and the insert-after-H1 splice re-implements gates.rebuild_cite_block's fallback — while both only match 目录|Table of Contents and miss i18n's third spelling 'Contents'.

**触发**：Update one literal and not the other: hand-edited re-imported files keep a stale TOC heading in the DB forever, and the next publish injects a second numbered TOC under it; the 'Contents' variant already escapes stripping today.

### 54. themes 第二个 slugify + 死导入
`pwiki/scripts/pwiki_lib/themes.py:180`

themes.slugify hand-rolls a second slugifier next to util.slugify — and themes.py:36 imports util's as pslugify and never uses it (dead import proving the intended consolidation never happened); themes.dirname/_posix likewise duplicate modules.posix/dirname.

**触发**：Two slug rules (util collapses runs via regex with caller fallback; themes loops per-char with an md5 fallback): the same module path can produce different ids/slugs on the two planner routes, and update's catalog_changed detection can then flag every theme page as moved after a plan round-trip through the other slugifier.

### 55. 概述类豁免关键词两份重复
`pwiki/scripts/pwiki_lib/gates.py:397`

The overview-ish section exemption keyword tuple ("概述","介绍","简介","引言","目录","结论","Overview","Introduction","Conclusion") is duplicated verbatim between gate_item (line 305) and fix_section_sources (line 397).

**触发**：A one-sided edit (adding '摘要'/'Summary' to the gate only) creates a state validate --fix cannot repair: the gate reports sources/section errors for sections the fixer deliberately skips, so D18 --fix writes nothing while finish's LINING keeps rejecting the page.

### 56. scan.read_text 与 corpus.read_text 逐字重复
`pwiki/scripts/pwiki_lib/scan.py:107`

scan.read_text duplicates corpus.read_text verbatim (same open/errors='ignore'/read(limit)/except-OSError body) with only the default limit differing (200k vs 300k).

**触发**：Two copies of the same file-reader with an observable divergence: a fix to one (UnicodeDecodeError handling, symlink behavior) silently misses the other — manifest/README/Makefile reads and corpus reads behave differently for the same file.

### 57. add_job / get_run / _rowget 死 API + 误导性 docstring
`pwiki/scripts/pwiki_lib/db.py:378`

WikiDB.add_job, WikiDB.get_run and db._rowget have zero callers anywhere (scripts or tests) — _rowget's docstring even claims '全库统一走这里' while gates.rowget and publish._row_like are the helpers actually used; same-pattern orphans: themes._posix, modules.is_under, corpus.build_corpus, planner._plan_review_path, i18n.sheet_names.

**触发**：~40 lines of dead API invite future callers onto an untested path (add_job queues with depth=0, silently breaking the kind_rank/depth ordering claim_jobs exists to provide), and _rowget's false 'single entry point' docstring misleads the next fix into routing through it.

### 58. config 8 个"单一事实源"常量无人读，active_run 另行硬编码
`pwiki/scripts/pwiki_lib/config.py:49`

Eight constants in the module declaring itself '单一事实源' have zero readers — RUN_STATES, ENTRY_FILENAME, RUN_KINDS, RUN_OPERATIONS, GENERATION_TYPES, REBUILD_REASONS, DOC_EXTS, LAYOUT_DIR_BASENAMES — and db.active_run re-hardcodes the state list in SQL instead of deriving it from them.

**触发**：Adding a run state to RUN_STATES (the 'single source') changes nothing — active_run's hardcoded IN (...) is what runs, so the lists silently diverge; the other unused tuples are noise that buries the real contract constants (EXIT_*, STYLES, PAGE_STATES).

### 59. build_packs 证据规格双写
`pwiki/scripts/pwiki_lib/themes.py:1046`

build_packs writes each gated pack's evidence spec twice — the troubleshooting probe (1046-1052) repeats its pack dict's patterns/docs/grep verbatim (1057-1060), and the deploy probe (906-908) vs pack (912-913) repeats them with two unexplained deltas.

**触发**：Someone extending the troubleshooting pack edits the pack's patterns but not the probe's (or vice versa): the ≥2 gate is then evaluated against a different evidence set than the one the pack later collects, and the deploy deltas ('deploy' vs 'readme', 'production' dropped) make intentional vs accidental indistinguishable.

### 60. add_scope 不可达嵌套
`pwiki/scripts/pwiki_lib/snapshot.py:57`

add_scope's nested `if rel and "*" not in rel: pass` is provably unreachable — the enclosing branch already requires `not rel or "*" in rel`; collapse lines 54-59 to a plain early return.

**触发**：Dead nesting in the hottest function of the incremental path suggests a removed special case readers will puzzle over or 'restore', and obscures the real rule (glob scopes are skipped) behind a conditional that reads like it does something.

### 61. 章节策略两套高度并存，themes 路线无视风格
`pwiki/scripts/pwiki_lib/themes.py:122`

Per-article-type section policy lives at two altitudes: themes.REQUIRED_SECTIONS (five identical copies of the same 9-item repowiki list keyed by kind, stamped on every theme page at line 279) vs config.ARTICLE_TYPE_SECTIONS (counts 4-5, enforced by gates.py:248 when required_sections is absent) — and the themes route is entirely style-blind.

**触发**：`pwiki init --style concise` + `plan --themes` produces pages carrying deep 9-section requirements and deep-register prompts that concise gates then fight (or writers fill, blowing the 3300-word cap); the five identical lists make per-kind adjustment impossible without forking data, and nothing asserts the two tables agree.

### 62. 提示词散落四处，只有 module sheet 走 (style×locale) 表
`pwiki/scripts/pwiki_lib/themes.py:288`

Writer prompts live in four separate mechanisms: i18n.SHEET_PROMPTS is the only (style × locale)-parameterized table and covers only module sheets; themes.prompt_for (288-301), the card prompts (planner.py:640-673), and layers.py:255 are hardcoded Chinese with a fixed deep register.

**触发**：A concise or en-locale wiki gets sheet prompts in the right register but every card/theme/layer page prompted in Chinese with deep-shape demands ('至少用两张图' conflicts with concise's 0-8 diagram band); adding a style or locale means editing i18n.py for sheets and then hunting three other files.

### 63. cite_band 用包名元组硬编码分类，已与 kind 漂移
`pwiki/scripts/pwiki_lib/styles.py:71`

cite_band_for classifies pages into D15 bands via a hardcoded pack-name tuple ("architecture", "ai", "api") plus an atype=='overview' special case, even though themes.py already assigns each pack a kind — and the classification has already drifted (the 'api' pack's kind is "cross").

**触发**：Adding a pack that should get the 80-120 band, or renaming 'ai', requires knowing to edit a name tuple inside the style-preset module; miss it and the new pack's pages are silently held to standard 60-90 and fail lining with 'size/citations' errors no planner output can explain.
