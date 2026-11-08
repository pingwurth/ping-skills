# pwiki 第二轮 max 代码审查报告(2026-11-08 晚)

> 审查对象:工作区改动(.gitignore/AGENTS.md/CLAUDE.md — 干净)+ 整个未跟踪的 `pwiki/` skill。
> 基线:110 个测试全绿(审查前)。10 个 finder 角度 + 13 组验证 + 2 轮 gap sweep;66 个原始候选去重后 **67 条存活发现**(40 correctness — 37 条探针确认 / 3 条潜伏 PLAUSIBLE,27 条清理/卫生),按下文顺序即严重度排序。
> 无候选被彻底驳回;若干 gate 类发现被现有测试锁死 —— 按 repo CLAUDE.md,references/validation.md 的文档契约为准,按"代码 vs 文档缺陷"上报。

All sweep candidates are now verified (probe-backed from the sweep agents, plus my direct line-level reads for S3/S4/S5, layers.py:22/242, and scan.py). The review is complete: 10 finder angles, 12 grouped verification passes, 2 sweep passes — 66 raw candidates deduped to 67 surviving findings (40 correctness + 27 cleanup/hygiene), of which 37 correctness findings carry executed e2e/unit probes. No candidate was refuted outright; 3 are latent-by-design PLAUSIBLE convention violations.

Reporting every finding, as you chose last time with `--max-findings all` — pass `--max-findings default` to go back to the usual limit.

Scope: the committed range diff is empty; reviewed the working-tree edits (.gitignore/AGENTS.md/CLAUDE.md — clean, docs verified in sync) plus the entire untracked `pwiki/` skill. Test suite baseline: 110 passed. Files were last touched 21:45–21:47 (pre-review), and no reviewer modified the tree.

```json
[
  {
    "file": "pwiki/scripts/pwiki_lib/reconcile.py",
    "line": 83,
    "summary": "D22 title-rename derives the old file location from the plan YAML's stale path keys instead of the published DB rows, so reconcile updates the DB path, prints \"文件 A→B\", yet never moves or writes any file.",
    "failure_scenario": "e2e-proven: hand-edit a title in wiki_plan.yaml (the sole documented edit surface), run begin/update (jobs+publish derive fresh paths into the DB; the plan is never re-saved), then `pwiki reconcile --all` → DB path points at zh/content/新标题.md which does not exist on disk, the real .md stays at its old location (later swept as an out-of-plan orphan by --prune), and _index.yaml + repowiki-metadata.json index the nonexistent file. engine.py:104-105 skips the move silently when the plan path is stale; moved.append at :111 sits outside the isfile guard, so the summary reports a move that never happened."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/snapshot.py",
    "line": 96,
    "summary": "snapshot_hashes' cards loop iterates c['scope']/c['source_files'] directly, but on published page rows those columns are JSON strings (json.dumps at queue time, db.py:381/386, copied verbatim by publish_page), so the loop walks the string character by character and card scopes never enter the hash baseline.",
    "failure_scenario": "spy-probe confirmed expand_scope receives calls ['[','\"','s','r','c',...]. Variant A (root-level source_files like README.md): baseline tracks only those files while cmd_update's current pass (fed from PLAN cards with real lists, snapshot.py:166-167) tracks the full scope → `changed` contains src/config/*.py on EVERY update with zero edits — eternal churn; the card itself is never marked stale by its own scope edits. Variant B (any '/' inside the walked string): expand_scope('/') walks os.path.join(repo,'') → the ENTIRE repo becomes the baseline."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/engine.py",
    "line": 556,
    "summary": "cmd_regenerate silently CANCELS the entire active run and releases all claims to redo one item, stranding every other queued job and every completed-but-unpublished job, with no announcement — while reconcile._apply_card_conflict (reconcile.py:171) refuses the same situation.",
    "failure_scenario": "e2e-proven: 9-job run with 2 completed-unpublished jobs → `pwiki regenerate <id>` exits 0 printing only the new run line; old run CANCELLED, the 2 writers' finished content unreachable by any finish (publish works off the dead run), live writers' leases released (their submits divert to orphan-submits). Recovery requires cancelling the regenerate run then `begin --missing`, which re-queues everything as pending — full LLM rewrite of already-completed work."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/reconcile.py",
    "line": 50,
    "summary": "Every processed reconcile item is marked state 'applied' even when the handler refused (page/card not in plan, active run exists) — the schema's 'rejected' state is never written, so refused D22/D7 items silently vanish from the pending queue.",
    "failure_scenario": "e2e-proven: `reconcile --all` on a card_conflict for a card absent from the plan prints \"拒绝\" yet the row becomes applied and reconcile_pending is empty. Worst case is the active-run refusal (:172): the user is told to finish/cancel first and reconcile later, but the item has already been resolved away — the writer-reported conflict is lost unless the operator caught the one-line message."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/yamlite.py",
    "line": 240,
    "summary": "Lines starting with '#' inside a hand-written '|' block scalar are silently deleted: _YLine comment-strips every line including block bodies (:160→:104) and the blank-line filter (:240) drops them before the block branch ever sees them.",
    "failure_scenario": "probe-confirmed: yaml_load(\"prompt: |\\n  # 必须包含的标题\\n  ## 性能指标\\n  正文\\n\") → prompt == '正文' — both markdown-heading lines vanish with no error. wiki_plan.yaml is the documented sole human-edit surface and prompts containing markdown headings are exactly the expected hand-edit; the writer LLM never sees the required structure and no gate fires."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/yamlite.py",
    "line": 189,
    "summary": "A misindented continuation line inside a '- key: v' list item is swallowed into the virtual-line buffer but never parsed (indent != base+2) and consumed via i=j, bypassing the 'unparsed line must error' guard (:249-250), so fields are silently dropped.",
    "failure_scenario": "probe-confirmed: yaml_load('pages:\\n - id: a\\n  title: A\\n') → {'pages': [{'id': 'a'}]} — title gone, no error. A dropped title is caught later by validate, but a dropped parent/prompt/scope/ordinal degrades silently: an indent typo on a parent: line silently re-parents the page to top level and reshapes the published tree."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/publish.py",
    "line": 109,
    "summary": "compose_export_content returns early for kind != 'page', so fix_off_by_one citation clamping and normalize_links never run for cards and module_sheets, while engine.cmd_finish (engine.py:704-705) suppresses the 'citation/off-by-one' LINING error for ALL kinds with a comment claiming publish clamps it.",
    "failure_scenario": "e2e-proven: deep card citing [docs/three.txt:4](file://docs/three.txt#L4) where the file has 3 lines passes submit (warning), passes finish (error suppressed), run COMPLETED, and the published card ships the broken #L4 anchor verbatim — the identical citation on a page is clamped to #L3. Bonus asymmetry: cmd_write (:601-607) does NOT suppress the error, so manually re-writing the same card is gate-rejected for something finish shipped."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/planner.py",
    "line": 262,
    "summary": "PROFILE_BUDGETS is consumed with two incompatible accountings: themes.py trims content pages to exactly 135 with no sheet headroom, expand_sheets (planner.py:696-723) appends 3 module_sheet pages per module on top, and validate_plan counts everything against the same 135 — the SKILL.md-documented sequence self-blocks.",
    "failure_scenario": "e2e-proven (synthetic 53-module repo): `plan --themes` → 79 content pages; `plan --sheets standard --deps` → +159 = 238 pages → `pwiki validate`/`begin`/`update` all die EXIT_GATE \"238 pages 超出 profile standard 预算 135\". Minimal trigger: any repo where themes reaches its own target (~105 pages — the trim loop at themes.py:1397 exists because large repos exceed it) with ≥11 modules. plan limits: cannot override — :262 reads PROFILE_BUDGETS directly."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/scan.py",
    "line": 345,
    "summary": "detect_commands() reads the Makefile via the manifest's repo-relative path (scan_repo overwrote the absolute path with rel at :481 before the :591 call), so read_text() resolves it against the process CWD instead of the scanned repo.",
    "failure_scenario": "mechanically confirmed by probe: `pwiki scan --repo /abs/other/repo` from a CWD containing a different Makefile → inventory.json commands contain 'make wrongbuild' parsed from the WRONG file; from a Makefile-less CWD the repo's real build/test targets silently vanish. Wrong commands propagate into digest's 构建命令 section and getting_started planning."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/engine.py",
    "line": 385,
    "summary": "When --claim is omitted, submit adopts whatever claim_token is currently in the row (the only guard is `supplied is not None`), so a stale writer whose lease was reclaimed silently completes the job under a different parallel writer's live token; fail/skip (:515/:530) share the pattern with no --claim flag at all.",
    "failure_scenario": "Writer A claims X, stalls past the 30-min lease; sibling B reclaims X with a new token; A finally runs `pwiki submit X --file old.md` without --claim → engine takes B's token from the row, match_claim is never called, A's STALE draft is marked completed and published; B's later `submit --claim <B-token>` fails into the orphan pile. Docs mandate --claim for parallel writers but nothing enforces it — one forgotten flag defeats the whole parallel-safety contract and the older draft wins."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/qoderplan.py",
    "line": 97,
    "summary": "Emit truncates each document's title to the byte cap (~33 CJK chars) but writes the parent's FULL untruncated title into children's parent: field (:96 parent_title from the raw plan title), so for any over-cap parent the emitted file's parent reference matches no document.",
    "failure_scenario": "probe-confirmed on real _emit: 40-CJK-char parent + child → parent doc title emitted truncated to 33 chars, child carries parent: <full 40 chars>; _emit's own _check_caps warns \"不匹配任何 title — 静默降级顶层\" yet returns EXIT_OK, and --lint on the emitted file exits 2. Per the module's own decompiled contract the real Qoder loader demotes such children to top level — the IDE tree silently loses structure while emit reports success."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/qoderplan.py",
    "line": 61,
    "summary": "Emit's page filter `kind=='page' and p.get(\"type\")` silently drops pages lacking `type`, and no validator ever requires it (planner.py:381 validates only the typed subset; load_plan doesn't validate), so untyped pages and their whole subtrees vanish from the Qoder plan with exit 0 and no warning.",
    "failure_scenario": "probe-confirmed: hand-edited plan r1(type=domain) → m1(kind=page, type removed) → c1(type=deep_dive, parent=m1) passes validate (0 errors) and begin (all pages queued regardless of type), then `pwiki qoder-plan` emits exactly 1 document — m1 filtered out and c1 vanishes with it (its parent never enters the emit recursion), reporting only \"(1 documents)\"."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/yamlite.py",
    "line": 128,
    "summary": "Emitter/parser numeric asymmetry: scalar strings 'nan'/'inf'/'NaN'/'Infinity'/'1_000' are dumped UNQUOTED (ASCII-only _NUM_RE misses them) but _parse_scalar reads them via int()/float(), changing type on round-trip and crashing string consumers.",
    "failure_scenario": "probe-confirmed round-trips: 'nan'→float nan, '1_000'→int 1000. Full crash chain reproduced: card title 'nan' (a real npm package name) → save_plan dumps `title: nan` bare → next load gives float nan → planner.card_path_for → util.safe_filename → config.ILLEGAL_FN.sub('-', nan) raises TypeError: expected string or bytes-like object, got 'float' — unhandled traceback at plan load. Also silently corrupts dup-title/slug checks (nan != 'nan')."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/reconcile.py",
    "line": 109,
    "summary": "_apply_title_rename writes moved files from raw DB content (H1-rewritten) instead of routing through compose_export_content, so for legacy export the moved page loses the engine-injected <cite> block and numbered TOC that exist only in the export layer (children at :131 likewise).",
    "failure_scenario": "e2e-proven: concise wiki, published page carries injected <cite>+目录; after `reconcile --all` the moved file has H1 rewritten but probe prints 'has <cite>: False, has TOC: False' and DB content == moved file (raw). Deep/current styles still lose normalize_links/fix_off_by_one on the moved file. The export tree is inconsistent until a full finish rewrites every page."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/engine.py",
    "line": 516,
    "summary": "cmd_fail/cmd_skip on an unknown page_id report success: set_job_state's no-token branch UPDATEs 0 rows but returns True unconditionally (db.py:496-501), so the command prints \"marked X failed\", writes a bogus run_log audit row, and exits 0 — unlike cmd_submit which pre-checks job existence (:367-368).",
    "failure_scenario": "e2e-proven: `pwiki fail no-such-page --error boom` on an active run → exit 0, \"marked no-such-page failed: boom\", no row changed, and run_log contains a page_failed event for a job that never existed. An orchestration script driving fail/skip from a stale job list (e.g. after a cancelled+re-begun run) believes items were recorded; finish later dies on the still-pending real jobs."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/engine.py",
    "line": 282,
    "summary": "cmd_next reports \"全部 N 个 job 已终态\" / JSON status \"complete\" whenever claim_jobs returns empty (no PENDING jobs), never checking summary['processing'] — while live leases are outstanding; finish then dies EXIT_PRECOND for in-flight jobs.",
    "failure_scenario": "e2e-proven: 9 jobs all claimed (processing, live leases, pending=0) → `pwiki next --json` prints status \"complete\" with its own summary showing processing:9; a polling driver per references/parallel-writing.md:29 (\"loop next until 全部终态\") stops dispatching and calls finish → \"error: 仍有 0 pending / 9 in-flight job\" exit 3. The status line is factually wrong and misleads both humans and automation."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/gates.py",
    "line": 559,
    "summary": "`validate --content --fix` re-gates the fixed page at phase='submit' (strict=False) instead of the original lining severity, so once any mechanical fix applies, every remaining style error is downgraded to a warning and the command exits 0.",
    "failure_scenario": "e2e-proven: page with one fixable off-by-one citation plus a ## 结论 section → without --fix: exit 2, errors=2; with --fix: the citation is clamped then the re-gate demotes schema/wrapup to a warning → exit 0, \"0 错误\". validation.md:67-68 promises \"errors 非零退出(exit 2), --fix 先机械修复再复查\" — the 复查 silently runs lenient, so an under-length/deep-invalid wiki ships as \"validated\" whenever it also contains one fixable citation."
  },
  {
    "file": "pwiki/references/parallel-writing.md",
    "line": 63,
    "summary": "The documented lease protocol \"租约默认 30 分钟; submit 会顺延\" does not exist in code: db.extend_lease (db.py:449, docstring \"submit 成功路径顺延租约\") has zero production callers (engine.py:432 removed it as a恒为 0 行 no-op), and submit never touches lease expiry.",
    "failure_scenario": "runtime-proven: writer A claims a card, lease backdated past expiry, sibling B's next reclaims the job with a new token, A's `submit --claim <old>` → exit 3 with the draft diverted to work/orphan-submits and the job possibly double-written — exactly the 互踩 the \"submit 会顺延\" promise exists to prevent. Anyone writing long pages (>30 min) per the documented recipe hits this. Also verified: glossary_set_definition (db.py:648) is test-only dead code while digest.py:8 prose mentions it as the manual path."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/gates.py",
    "line": 291,
    "summary": "size/words is the only size gate not routed through the preset's size_hard key AND it applies ×1.1 to a preset value that already is max_article_words×1.1 (styles.py:21 stores 3300), so the effective cap is 3630 and concise pages hard-fail LINING for an over-limit that the docs call warn-only.",
    "failure_scenario": "probe-confirmed both facets: a 3508-word page produces NO finding (dead zone 3300-3630 above the documented ≈3300 cap); at 3638 words with style=concise (size_hard=False, \"软执行\" per validation.md:18 and gates.py's own header) the finding lands in ERRORS at phase='lining' → finish bounces the run to DOCUMENT_FAILED exit 2, while every sibling gate (page_lines :285, mermaid :278, band :299, cited_files :311) honors size_hard. No test covers size/words."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/util.py",
    "line": 16,
    "summary": "util.git() runs subprocess with text=True (locale decoding) and no encoding='utf-8', and UnicodeDecodeError (a ValueError) escapes the `except (OSError, subprocess.SubprocessError)` at :19 — the exact bug class the repo's other three skills already fixed in their git wrappers.",
    "failure_scenario": "reproduced live: a repo with a non-ASCII branch name (git rev-parse --abbrev-ref HEAD, scan.py:553 — fires with DEFAULT git config, no quotepath needed) under LC_ALL=C → raw UnicodeDecodeError escapes the wrapper and crashes `pwiki scan`/`update`; non-UTF-8 preferred encodings (Windows GBK/cp1252) are the standing precondition, and non-ASCII filenames with core.quotepath=false trigger it too. crp/gitops.py, slr git_utils.py and jaut proc.py all carry encoding=\"utf-8\" — this 4th private copy diverges from its own lineage."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/layers.py",
    "line": 242,
    "summary": "materialize_layers hardcodes config.LIMIT_DEFAULTS['max_depth'] (4) for its depth loop while apply_layer_result enforces the plan-overridable plan_limits() value, so articles legitimately accepted at depth >4 are silently dropped at commit.",
    "failure_scenario": "wiki_plan.yaml sets limits: {max_depth: 5} (planner documents plan limits override profile defaults); `--layers apply` accepts a depth-5 child (allowed by plan_limits at the apply gate), layer status reports planning complete, then `--layers commit` runs range(0, 4+1) and skips every depth-5 article: its page never enters plan['pages'], its modules get no covering article (violating the rules' hard gate \"每个模块路径必须至少出现在一篇\"), and the operator sees \"plan rewritten: N pages\" with no warning."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/engine.py",
    "line": 108,
    "summary": "begin --missing computes the skip set only from the LAST run's jobs (ctx.db.jobs(last[\"id\"])), so after any interleaved regenerate or update INCREMENT run becomes the last run, deliberately skipped pages are no longer excluded and get resurrected into the queue.",
    "failure_scenario": "Full run: page X skipped with a reason, rest published; user runs `pwiki regenerate Y` (single-job run, finishes, is now last); weeks later `pwiki begin --missing` → X is neither published nor in the last run's job set → re-queued, forcing writers to redo a page that was explicitly skipped — violating the documented \"只排队未发布且未跳过的项\" recovery semantics."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/reconcile.py",
    "line": 35,
    "summary": "`int(apply_id)` inside the todo-filter comprehension is uncaught, so `pwiki reconcile --apply <non-numeric>` raises a raw ValueError traceback (exit 1) whenever at least one pending row exists — cli.py declares --apply without type=int.",
    "failure_scenario": "e2e-proven: one pending reconcile item + `--apply xyz` → \"ValueError: invalid literal for int() with base 10: 'xyz'\" stack trace, breaking the documented 0/1/2/3 exit-code contract; with an empty queue the comprehension short-circuits and no crash — state-dependent. Same class at qoderplan.py:238 (below)."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/qoderplan.py",
    "line": 238,
    "summary": "_lint calls doc.get('version') without checking yaml_load returned a dict (yamlite passes list-topped documents through unchanged, yamlite.py:251), so a list-topped YAML file crashes with AttributeError.",
    "failure_scenario": "probe-confirmed: `pwiki qoder-plan --lint` on a file whose top level is a YAML list → AttributeError: 'list' object has no attribute 'get' raw traceback instead of a clean parse error. The guard exists in planner.load_plan (planner.py:51-52 isinstance check) — it is simply missing here."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/engine.py",
    "line": 514,
    "summary": "cmd_fail/cmd_skip have no completed-state guard: a COMPLETED job can be demoted to failed/skipped with no --force and no confirmation, unlike submit which requires --force for completed jobs (:369-371).",
    "failure_scenario": "e2e-proven: completed job (claim_token cleared) → `pwiki fail <completed-id> --error x` → exit 0, state now failed. One typo'd page_id in a fail loop silently drops finished work from the publish set, degrades the run to PARTLY_COMPLETED with a phantom failure, and strands the content until a regenerate."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/layers.py",
    "line": 48,
    "summary": "parse_result_block's ValueError/JSONDecodeError are uncaught in the `--layers apply` path (no try/except around apply_layer_result, layers.py:312), and the FENCE_JSON_RE fallback is greedy (\\{.*\\} vs RESULT_RE's lazy .*?), so it merges multiple fenced JSON blocks into one unparseable span.",
    "failure_scenario": "`pwiki plan --layers apply --file out.txt` where the writer's answer lacks a <result> block, or contains two fenced JSON blocks (e.g. a package.json excerpt before the real result): the greedy match spans from the first '{' to the last '}' including the embedded fences → json.loads raises inside cmd_plan_layers → raw traceback exit 1, instead of the clean die(..., EXIT_GATE) every other apply validation produces."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/gates.py",
    "line": 346,
    "summary": "The sources/section gate (:346), fix_section_sources (:443), and the legacy TOC injection (publish.py:124) enumerate '## ' headings on RAW content while all other shape checks use fence-stripped prose (:165/:253), so '## ' lines inside fenced code blocks count as analysis sections.",
    "failure_scenario": "probe-confirmed both directions: a deep page whose 2 real sections carry 章节来源 plus one fenced example containing '## 安装示例'/'## 用法示例' → LINING ERROR \"2 个分析节缺 **章节来源**\" (phantom sections change the count) → finish DOCUMENT_FAILED; conversely 3 source-less sections + a fenced 'correct example' page pass silently. --fix is worse: the injected **章节来源** block lands INSIDE the code fence (probe shows it between ```python and the phantom heading), corrupting the example; legacy TOC gains phantom entries with anchors into fences. tests/test_gates.py:201 asserts fence-awareness as the intended contract — these three call sites bypass it."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/gates.py",
    "line": 579,
    "summary": "validate_content's internal-link resolver builds all_paths from PLAN PAGES only (cards excluded — they live in plan['cards'] published under knowledge/<locale>/), so every correct relative link from a page to a card this engine published is flagged as a broken link, error-level.",
    "failure_scenario": "e2e-proven: appending the CORRECT link '[构建系统](../../knowledge/zh/构建系统/构建系统.md)' to a published page → \"error: links/internal ... 不解析\" exit 2 on a fully correct tree. Aggravating: publish-side normalize_links builds ITS all_paths from DB rows INCLUDING cards and actively rewrites writer basename links into exactly that card path — the engine emits links its own validator then rejects, blocking the documented \"已验证\" contract."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/gates.py",
    "line": 587,
    "summary": "The internal-link regex `\\((?!file://|http)([^)#]+\\.md)\\)` requires ')' immediately after '.md', so anchored relative links ([x](page.md#section)) are never validated — and publish.normalize_links (raw.endswith('.md')) never rewrites them either.",
    "failure_scenario": "e2e-proven: appending '[断链锚点](../不存在的页XYZ.md#核心机制)' to a published page → `validate --content` exit 0, 0 errors; the identical link without the anchor exits 2 (locked by test). The two writer-relative-link error classes normalize_links exists to fix, plus any typo in an anchored link, ship silently in the published tree."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/publish.py",
    "line": 612,
    "summary": "cmd_write (:612) and cmd_export db_to_file (:666) call compose_export_content without the plan kwarg that publish_run passes (:242), falling back to config.json's export_style — so the plan-level export_style override is honored at finish but ignored by every later manual/heal path.",
    "failure_scenario": "e2e-proven: plan export_style=current, config legacy → after finish the page has no <cite>/TOC; one `pwiki write <id> --file fix.md` or crash-heal `export db_to_file` re-renders the SAME DB content with legacy injection (<cite>: True, TOC: True); db_to_file re-renders ALL pages — the published tree flip-flops between two renderings of identical content. (file_to_db absorption is guarded by _strip_injected; the confirmed harm is the rendering divergence.)"
  },
  {
    "file": "pwiki/scripts/pwiki_lib/publish.py",
    "line": 384,
    "summary": "_write_indexes emits wiki_catalogs entries keyed by slug (:360) but child entries and cat_relations edges reference the parent's page_id (:370, :384) — two id spaces in one document, and nothing upstream enforces slug == page_id.",
    "failure_scenario": "e2e-proven: hand-edited plan giving domain 'services' slug 'services_slugx' (validate only enforces slug uniqueness) → emitted repowiki-metadata.json contains PARENT_CHILD edges whose source_id matches no wiki_catalogs id and a child catalog entry with parent_id pointing at a nonexistent id → the Qoder-imported wiki tree loses all parent-child structure for that subtree."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/gates.py",
    "line": 232,
    "summary": "schema/toc, schema/cite and schema/wrapup are appended '(errors if strict else warnings)' with no style branch, making them LINING hard errors for CONCISE too, while references/validation.md:45-46 classifies them as \"concise 保持警告\".",
    "failure_scenario": "probe-confirmed: concise page with a hand-written ## 目录 or ## 结论 → submit warns, finish LINING makes schema/wrapup an ERROR → run DOCUMENT_FAILED exit 2, though the documented contract says concise keeps these as warnings that never block publishing (both plausible writer mistakes; the writing-guide only says \"不要写\"). Note tests/test_gates.py:67 locks the buggy behavior — docs are the contract per repo CLAUDE.md."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/gates.py",
    "line": 274,
    "summary": "lint_mermaid results are extended into errors unconditionally (:273-274) and sheet/links likewise (:418-419), so a mermaid typo or a file:// link in a concise sheet rejects at SUBMIT — contradicting gates.py's own docstring (:157-158) and validation.md:9-10 (\"submit 阶段…形状与尺寸全警告\").",
    "failure_scenario": "probe-confirmed: concise SUBMIT with a 'bogusDiagram TB' block → errors [\"mermaid/type…\"] → submit rejected exit 2, job bounced to pending; same for a concise module_sheet containing one file:// link. validation.md's error-code table lists neither mermaid/* nor sheet/links as submit-rejecting; the module's own header comment at engine.py:414 states shape issues must pass at submit."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/gates.py",
    "line": 405,
    "summary": "The D16 knowledge-citation gate counts ALL file:// citations (whole-file links with no #L range included — config.CITE_RE makes the line part optional) while its own message says \"行级引用\", so validation.md's \"卡 ≥3 行级\" floor is not enforced.",
    "failure_scenario": "probe-confirmed: deep card citing src/a.py three times WITHOUT #L → n_cites=3 ≥ 3 → LINING errors empty; a card with zero line-level evidence publishes as validated although validation.md:24 and writing-guide.md:50 demand ≥3 LINE-level citations (and the gate's message text claims that's what it checked). Same counting in the sheet D16 gate (:427-430); the only line-level check (cite_line_ratio :338) is page-only."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/yamlite.py",
    "line": 250,
    "summary": "Standard same-indent block lists ('source_files:\\n- README.md' — legal YAML and PyYAML's default dump style) make yaml_load raise 'unparsed line', and gates.parse_frontmatter swallows the ValueError, so a correctly frontmatter'd card is rejected with the misleading \"卡片必须带 YAML frontmatter\".",
    "failure_scenario": "probe-confirmed: card frontmatter '---\\nkind: build_system\\nsource_files:\\n- README.md\\n---' → parse_frontmatter returns ({}, original) → `pwiki submit <card>` dies \"knowledge/frontmatter: 卡片必须带 YAML frontmatter\" although the frontmatter is present and valid YAML; the same style in a hand-edited wiki_plan.yaml fails load_plan with \"解析失败: unparsed line\". The emitter always indents deeper, so machine-written files are safe — humans and PyYAML round-trips are not."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/yamlite.py",
    "line": 104,
    "summary": "_strip_comment toggles quote state on every bare apostrophe with no pairing check, so a scalar containing an odd number of apostrophes (don't, it's) keeps the trailing YAML comment welded into the value.",
    "failure_scenario": "probe-confirmed: yaml_load(\"title: don't worry # TODO\\nnext: 1\\n\") → title == \"don't worry # TODO\" — real YAML yields \"don't worry\". The comment text silently enters the title (and thus H1 gate comparisons, derived file paths, qoder-plan parent matching) on the documented human-edit surface; the emitter quotes '#'-containing strings so machine-written files are unaffected."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/yamlite.py",
    "line": 135,
    "summary": "Multi-line flow lists parse as the plain string '[' inside list items or with a dangling opener (_parse_scalar handles only single-line '[...]'), silently degrading list fields instead of raising.",
    "failure_scenario": "probe-confirmed in the reachable wiki_plan.yaml shape: 'pages:\\n - id: a\\n   scope: [\\n       \"src/x.py\",\\n   ]' → {'pages': [{'id': 'a', 'scope': '['}]}. Downstream: gates.py:255-259 json.loads fails → required_sections silently [] (section floor disabled, no error); scope fields produce confusing validate errors. (Top-level flow lists fail loudly with 'unparsed line' — the silent variant needs the list-item or dangling-opener shape.)"
  },
  {
    "file": "pwiki/scripts/pwiki_lib/themes.py",
    "line": 145,
    "summary": "load_overrides swallows ValueError from pwiki.yaml and returns empty title_maps/title_aliases with no warning — and tab-indented files do not even raise (yamlite counts only spaces, so children collapse to indent 0 and title_maps parses as None→{}), so overrides are silently discarded.",
    "failure_scenario": "probe-confirmed: tab-indented pwiki.yaml → load_overrides returns empty maps, zero output. All pilot-mapped page titles revert to collision-suffixed forms, published paths change, and the next update flags every affected page stale and rewrites them — all invisible. The discarded maps feed title lookups at themes.py:328/714/750/772 which drive page_path_for/card_path_for."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/scan.py",
    "line": 619,
    "summary": "expand_scope's directory branch filters only ignored directory NAMES — despite the docstring promising \"忽略 DEFAULT_IGNORES 与二进制\", every file including binaries enters the entries set that snapshot.py and modules.py seed_depends_on consume.",
    "failure_scenario": "probe-confirmed: scope 'src/' yields src/logo.png and src/data.sqlite in the tracked set. Any commit touching a tracked binary (asset re-export) marks the covering page's watch∩changed → `pwiki update` queues full LLM regeneration of that module's pages with zero source-code change — churn contradicting the function's documented contract; depends_on likewise records 'dependencies' on binary files."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/i18n.py",
    "line": 156,
    "summary": "SHEET_PROMPTS defines only a 'zh' table per style (i18n.py:128-131) while SHEET_DISPLAY and CARD_KIND_DISPLAY fully support 'en', so sheet_prompt() falls back to Chinese prompts for any non-zh locale.",
    "failure_scenario": "probe-confirmed: sheet_prompt('overview','concise','en','Payment Gateway') → \"用 1-3 句话说明模块…\" (Chinese). `pwiki init --locale en` + `plan --sheets` creates module_sheet jobs with Chinese instructions → Chinese sheet bodies inside an English content tree, despite i18n.py existing precisely to localize this surface."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/themes.py",
    "line": 158,
    "summary": "themes.humanize's extension-strip tuple has drifted from the real extension sets (missing .mjs/.cjs/.kts/.mts/.rb/.php/.cs/.cpp… present in config.SOURCE_EXTS and scan.LANG_BY_EXT), so extensions leak into page titles and ids, and same-base files split into two pages.",
    "failure_scenario": "probe-confirmed: humanize('utils.mjs') → 'Utils.mjs' (vs 'utils.js' → 'Utils'); slugify → 'utils-mjs'. Via the mode:'files' leaves used by shipped themes (themes.py:707-771 → :351-352, :476-477), utils.js and utils.mjs group into two pages ('…-utils' and '…-utils-mjs') where a matching tuple yields one. The same drifted tuple is duplicated verbatim in corpus.stem_of (:237) — both copies drifted, neither fixed."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/scan.py",
    "line": 473,
    "summary": "Symlink handling is asymmetric: directory symlinks are never descended (os.walk default followlinks=False) while file symlinks that resolve are kept, so a module reachable only through a dir symlink is invisible and file symlinks double-count or externalize content.",
    "failure_scenario": "Monorepo pattern `services -> pkgs/services` (or `src -> ../shared/src`): the entire module is absent from inventory.json — files, tree_text, languages, module decomposition all miss it with no warning. Conversely a file symlink 'latest.py -> src/v1/latest.py' yields a second inventory entry for the same content (lines/bytes double-counted in stats and lang_lines), and an out-of-tree symlink pulls external source into inventory under a bogus repo-relative path."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/scan.py",
    "line": 469,
    "summary": "The max-files cap is checked only once per walked directory before the per-name loop, so a single large flat directory overshoots the cap without bound.",
    "failure_scenario": "`pwiki scan --max-files 50000` against a repo containing one flat directory of 80,000 files (artifact dumps, generated snapshots — exactly the repos that need the cap): all 80,000 are walked, line-counted and manifest-parsed, doubling memory and runtime; the cap should break inside the name loop."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/scan.py",
    "line": 467,
    "summary": "extra_ignore compares path-style entries against bare directory names (d not in extra_ignore), so path ignores never match and bare names over-match at every tree level — and the CLI never passes it at all (cmd_scan :656 calls scan_repo without extra_ignore; cli.py defines only --max-files).",
    "failure_scenario": "Any future caller passing 'src/generated' gets it silently ignored (compared against bare 'generated'); passing 'generated' prunes EVERY directory of that name tree-wide. Today the parameter is dead from the CLI — either wire a flag with relative-path semantics or document bare-name behavior."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/engine.py",
    "line": 478,
    "summary": "The knowledge-first early-flush gates on bare literals job[\"kind\"] in (\"module_sheet\", \"card\") instead of config.is_card_kind() — the exact forbidden compare class (config.py:106-111) whose past drift already caused one misrouting incident.",
    "failure_scenario": "PLAUSIBLE (latent, all four queue producers normalize today): a leaked plan card_kind skips the knowledge/<locale>/ flush at submit AND mis-ranks (db.py:359 KIND_RANK.get(kind, 3) puts it after pages), silently breaking the knowledge-first ordering the engine promises — the failure the code comment at snapshot.py:288-290 documents as having actually happened before."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/publish.py",
    "line": 435,
    "summary": "knowledge/_index.yaml's card list filters published rows with bare p[\"kind\"] == \"card\" while publish.py:213 checks the identical row source via config.is_card_kind() — two sites in one file disagreeing about the row invariant.",
    "failure_scenario": "PLAUSIBLE (latent — rows are normalized at all four queue sites today): any future writer that passes plan items through un-normalized publishes fine (gates accept via is_card_kind) but is silently omitted from kindex[\"cards\"] — Qoder IDE import loses knowledge cards and search/index consumers see an empty card registry with no error anywhere. This is the precise recurrence pattern the repo CLAUDE.md rule was written after."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/snapshot.py",
    "line": 108,
    "summary": "snapshot_run builds its card set with the same bare p[\"kind\"] == \"card\" on published rows (convention violation; see also publish.py:435).",
    "failure_scenario": "PLAUSIBLE (latent, same normalization argument): a card_kind row drops out of the snapshot card set — its scope dirs leave the baseline (they were already effectively dead per the JSON-string bug at :96, which masks this one today). Route both sites through config.is_card_kind() or guarantee normalization at the db.queue_jobs choke point."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/corpus.py",
    "line": 292,
    "summary": "doc_hits filters a pool with `f in self._docs` where _docs is a LIST — O(pool×docs) dict-equality per call (measured 227ms at 10k files × 1.7k docs vs 0.5ms for a path-set); corpus.collect passes the full file pool for every spec with a \"docs\" key and themes.py defines 23 such specs.",
    "failure_scenario": "`pwiki plan --themes` burns ~3s at 10k files (~17s at the 50k max-files cap) on pure membership tests. Fix: precompute a set of doc paths in Corpus.__init__ (note: set(self._docs) won't work as-is — elements are unhashable dicts; key by path)."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/snapshot.py",
    "line": 111,
    "summary": "snapshot_run always calls set_file_hashes which DELETEs the whole file_hash table and re-INSERTs every row, although db.upsert_file_hashes exists (db.py:617, docstring: \"全表删写曾让单页修正产生 10k 行 churn\") precisely to avoid this.",
    "failure_scenario": "A 3-page incremental finish on a 10k-file tracking set rewrites all 10k rows (~1MB row + WAL churn) when a handful of paths changed — the finish path never uses the diff/upsert the write path already got."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/db.py",
    "line": 391,
    "summary": "jobs() does SELECT * dragging each job's content (full page markdown) and report columns, while its flagged consumers (engine.cmd_next :291, begin --missing :108) never read them — the comment at engine.py:289 even acknowledges the drag.",
    "failure_scenario": "Every `pwiki next` materializes ~135 rows × 10-30KB of markdown from sqlite (one 135-job run ≈ 135 next calls ≈ hundreds of MB of dead row reads); a metadata-only column list makes next O(rows) instead of O(total markdown bytes)."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/publish.py",
    "line": 205,
    "summary": "ctx.db.published_pages(pid) — a SELECT * including every content blob — runs 3× inside publish_run (:201, :205, :235), a 4th time in snapshot_run (snapshot.py:106) and a 5th in cmd_finish (engine.py:743); :201 and engine.py:743 are pure waste (path strings / kind counts only).",
    "failure_scenario": "Five full page-table reads per `pwiki finish` (135 pages × ~15KB ≈ 10MB of redundant sqlite reads + 5× Row materialization), including 3-page incremental updates where nothing else changed; fetch once and pass the list through."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/db.py",
    "line": 357,
    "summary": "The plan-card→job normalization + card-job dict literal ({id,title,kind:'card',card_kind,ordinal:950,prompt,source_files}) is copy-pasted at four producers (engine.py:131, engine.py:565, snapshot.py:291, reconcile.py:180) with no normalization at the db.queue_jobs choke point — and snapshot's copy already omits ordinal and re-implements card_kind resolution.",
    "failure_scenario": "The same class of copy already diverged once (snapshot.py:288-290 comment: bare == 'card' never matched, stale cards queued as pseudo-pages, next KeyError'd). Any fifth queue site or new card-job field must be replicated in four files; normalize once in queue_jobs (item kind in CARD_KINDS → kind='card', card_kind=kind) via one shared helper next to card_path_for."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/gates.py",
    "line": 200,
    "summary": "Gate relaxation is decided by parsing message-string prefixes at three sites ('citation/off-by-one' filtered at gates.py:200, engine.py:705, snapshot.py:258; --lenient filters 'schema/h1'/'citation/none' prefixes at engine.py:424), and --lenient already means two different contracts (selective code-drop at submit vs full bypass at cmd_write/finish).",
    "failure_scenario": "Rewording a gate code ('schema/h1:' → 'schema/H1:') silently stops --lenient and the clamp filters from matching it; and `pwiki write --lenient` publishes content with broken citation paths while submit --lenient never relaxes truthfulness — same flag, two contracts, depending on the command. Root fix: gate_item returns structured findings (code, severity class, relaxable) and one filter owns phase/lenient semantics."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/snapshot.py",
    "line": 166,
    "summary": "cmd_update re-queries the entire page table (content blobs included) for snapshot_hashes at :166 when the identical rows are already in the `published` dict built at :155, and :184-189 re-expand every module scope right after snapshot_hashes walked the same scopes (:75-83).",
    "failure_scenario": "Per `pwiki update`: one duplicate full page-table read plus a second full os.walk/stat pass over the whole tracked set (10k files at the cap) whose results mod_files could reuse."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/themes.py",
    "line": 504,
    "summary": "instantiate_pack re-collects each pack's evidence with the exact spec build_packs just probed (:673, :683-693, :758, :783, :959, :1058) and run_themes re-probes P_DESKTOP again at :1264 — Corpus.collect has zero memoization.",
    "failure_scenario": "P_FRONTEND is swept 3× and P_DESKTOP 3× per `pwiki plan --themes` (~20 packs ≈ 20 redundant full-repo pattern sweeps; measured ~20ms each at 10k files, ~100ms at the 50k cap) — on top of the doc_hits cost in the same command. Memoize collect by (patterns, exclude)."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/gates.py",
    "line": 584,
    "summary": "validate_content's internal-links pass re-opens and re-reads every published page file that the one() pass just read (:542-543) — no content is carried between the passes.",
    "failure_scenario": "Every `pwiki validate --content` reads all ~135 published .md files twice; with --fix it re-reads a third time after the rewrite. Carry the content dict from the first pass."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/publish.py",
    "line": 611,
    "summary": "cmd_write builds all_paths via {q[\"path\"] for q in ctx.db.published_pages(pid)} — a full SELECT * hauling every content blob just to collect path strings; no path-only page query exists in db.py.",
    "failure_scenario": "Every single-page manual correction reads ~135 rows × ~15KB of markdown to extract one string per row; `SELECT path FROM page WHERE partition_id=?` is ~1000× less data."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/engine.py",
    "line": 129,
    "summary": "~22 call sites inline `.replace(os.sep, \"/\")` instead of calling modules.posix() (engine.py:129,136,570,574; gates.py:570-589; publish.py:74,83,216,219; planner.py:157,159; scan.py:475,622,634; snapshot.py:301,304; reconcile.py:185; corpus.py:148,157).",
    "failure_scenario": "The path-normalization rule lives in 22 places plus the helper; when the rule needs a real change (collapse './', duplicate slashes, backslash input) someone fixes posix() and the inline copies silently keep the old behavior — the exact drift scan.py:146's comment says was already hit once with read_text. planner already normalizes at production time, so each downstream re-normalization is redundant defensive copying."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/themes.py",
    "line": 180,
    "summary": "themes.dirname() duplicates modules.dirname() (modules.py:67) — same contract (parent dir of a posix path, '' for top level), two implementations (rsplit vs os.path.dirname), ~15 live call sites in themes vs 5 in modules, no cross-import.",
    "failure_scenario": "A fix applied to only one copy (e.g. trailing-slash or './'-prefix handling) makes themes' page-by-dir buckets disagree with modules' module-by-dir buckets for the same directory, silently splitting one module's evidence across two groups. Hoist one copy into corpus.py or util.py."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/db.py",
    "line": 146,
    "summary": "page.needs_review is never written or read (the runtime needs_review is a returned list, never persisted; the column stays default ''), and content_hash is written in three places but read by nothing outside one assert-nonempty test.",
    "failure_scenario": "Dead schema state bloats every page/page_job write (publish_page lists 27 columns to copy a hash nobody compares) and invites divergence: a code path updating content without updating content_hash creates a row whose stored hash lies, with no consumer ever there to catch it. Drop the columns or land a reader."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/planner.py",
    "line": 744,
    "summary": "Five try/except ImportError blocks with '在 P3/P5 落地' stage messages (planner.py:745, 753, 813, 844; engine.py:722) guard imports of modules that all ship in the same package — install.sh copies whole skills and never strips individual modules, so the branches are unreachable scaffolding.",
    "failure_scenario": "If a module ever fails to import for a real reason (syntax error, partial copy), the user sees 'plan --layers 在 P3 落地' instead of the actual ImportError; every new cross-module call site copies another 5-line guard. cli.py _resolve_handler already owns the generic not-implemented fallback."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/i18n.py",
    "line": 74,
    "summary": "DIAGRAM_SOURCES has zero code consumers — gates.py:150 hardcodes both spellings in its own compiled regex and gates.py:362 re-inlines the identical pattern as a fresh re.findall instead of len(DIAGRAM_SOURCES_RE.findall(content)).",
    "failure_scenario": "A third marker spelling added to i18n.DIAGRAM_SOURCES changes nothing while editors believe they localized the gate — the drift class this file's header says it exists to prevent (SECTION_SOURCES/CITE_BLOCK_TITLE got wired up; this table was left behind). Delete the dict and reuse the compiled regex."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/styles.py",
    "line": 33,
    "summary": "Preset keys 'label', 'min_sections' and 'toc_required' (:19/:38, :33-34, :34-55) have zero readers anywhere in scripts/tests/references — gates.py and publish.py read every other key.",
    "failure_scenario": "The preset table is documented as the single gate-parameter source of truth; 'min_sections' sounds load-bearing (the real floor comes from config.ARTICLE_TYPE_SECTIONS/required_sections) so the next tuning change lands on a knob connected to nothing."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/modules.py",
    "line": 265,
    "summary": "The local `fixed` dict re-declares 8 of 9 entries verbatim from TITLE_MAP (applied two lines above); only 'services' differs (后端服务 vs 服务层).",
    "failure_scenario": "Two title tables for the same top-level dirs invite drift: a reword in TITLE_MAP silently doesn't take effect (or vice versa). Caveat from verification: the naive fix (set TITLE_MAP['services']='后端服务', delete fixed) is NOT behavior-identical — fixed matches full paths and beats the README-title override, while TITLE_MAP matches base names only; the safe fix must preserve the override order and full-path scoping."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/publish.py",
    "line": 450,
    "summary": "_write_indexes computes the same root-relative module path expression ('/' if module_id=='root' else path or module_id or '') three separate times (:437-440, :451-452, :459-461) even though id_path already holds that exact mapping (children VALUES at :444-445 already route through it).",
    "failure_scenario": "When the root-path rule changes, fixing two of the three copies silently produces a knowledge index whose module keys don't match its children map — orphaned children entries in _index.yaml. Compute id_path[m['module_id']] once and key off it."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/themes.py",
    "line": 260,
    "summary": "The required_sections fallback default branches on a `self.style != \"concise\"` literal inside ThemeBuilder instead of a styles.py preset key — the preset table exists precisely to own style differences (its own docstring: \"单一事实源\").",
    "failure_scenario": "A third style/preset silently inherits deep's REQUIRED_SECTIONS nine-section template (or concise's empty default) based on a negative literal match, producing plans that gates — reading the real preset — then reject or under-gate. Add a preset key (e.g. required_sections_fallback)."
  },
  {
    "file": "install.sh",
    "line": 179,
    "summary": "install_skill strips only test.log/__pycache__/tests/.pytest_cache/docs (and workflows/ for non-claude) after cp -r, so the dogfooded runtime state pwiki/.pwiki/wiki.db (and any work/ artifacts) ships into every install target.",
    "failure_scenario": "`./install.sh claude pwiki <project>` copies the skill's own 165-line wiki.db into <project>/.claude/skills/pwiki/.pwiki/ — stale engine state from this repo rides along (git ignores it via the root .gitignore, but install copies from the working tree, not git). Delete pwiki/.pwiki before committing and/or add it to the strip list."
  }
]
```

Notes on process and confidence: every CONFIRMED correctness finding above carries either an executed e2e/unit probe against the actual modules or a verifier's line-level proof; the three PLAUSIBLE items (engine.py:478, publish.py:435, snapshot.py:108) are latent bare-`== "card"` compares that violate the repo's stated is_card_kind convention but have no live trigger today. Several gate findings are locked in by existing tests asserting the buggy severity — per repo CLAUDE.md the references/validation.md contract governs, so those are reported as code-vs-doc defects. Cleanup findings are ranked by measured/estimated cost; correctness outranks cleanup throughout. The conventions angle returned no CLAUDE.md violations, and the edited .gitignore/AGENTS.md/CLAUDE.md prose checked out accurate (six skills, dynamic discovery in install.sh, .pwiki ignore pattern covers nested paths).

---

## 验证附录(sweep verifier 独立复核)

sweep verifier 独立投票:S1–S5 全部 CONFIRMED,无翻案、无新增、无驳回。三条精确化补充:

1. **S3(engine.py:385 无 --claim 的 submit)** — 比正文描述更糟:被顶掉的在写 writer B 随后的 `submit --claim <token>` 连 orphan-save 都拿不到;它死在 completed-state guard(engine.py:369-371,EXIT_PRECOND),草稿不保留 —— `_orphan_save` 只在 token 不匹配时触达。过期 writer 的草稿获胜,在写 writer 的草稿直接丢失(除非本地文件还在)。
2. **S4(engine.py:108 `begin --missing` 跳过集)** — 现实触发路径是 `pwiki update`(产生 INCREMENT run 成为 last_run);regenerate 变体要求其在飞行中执行过,之后 regenerate run 同样成为 last run。两条路径都会复活被显式跳过的页面。
3. **S2(scan.py:619 二进制入 scope)** — 修复已半就位:`BINARY_EXT` 在 scan.py:74-80 已存在,只是没在 `expand_scope` 内应用;S1 方面,`corpus.read_text` 在 OSError 时返回 `""` 正是无 Makefile 的 CWD 静默退化而非报错的原因。

## 置信度说明

每条 CONFIRMED correctness 发现都带有对实际模块执行的 e2e/unit 探针或验证者的行级证明;三条 PLAUSIBLE(engine.py:478、publish.py:435、snapshot.py:108)是违反 is_card_kind 约定但今日无活跃触发的潜伏裸比较。约定角度未发现 CLAUDE.md 违例;.gitignore/AGENTS.md/CLAUDE.md 的改动文字核实准确。


---

## 回修记录(2026-11-09,五批全部完成)

批次与覆盖(测试 110→185, 全绿):

- **B1 生命周期/数据丢失**(#1 D22 旧位置取 DB 发布行+落盘走 compose / #2 快照
  卡片 JSON 字符串解析 / #3 regenerate 搁置守卫+播报 / #4 reconcile
  applied-rejected-retry 三态 / #10 重认领后 submit 必带 --claim+孤儿稿全覆盖 /
  #16 next in_flight / #22 跳过集看全 run / #23 --apply 净错 / #25 fail/skip
  completed 守卫;#15 复核时已修)
- **B2 yamlite 解析器**(块标量 '#' 行保留 / 错缩进续行报错 / 撇号不焊注释 /
  同缩进块列表 / 跨行流式列表报错 / '' 转义 / #38 overrides 告警 / #41
  STRIP_EXTS 单一事实源;#13 nan/1_000 复核确认 c003 已修)
- **B3 gate/文档契约**(compose 全 kind 钳位+链接规范化 / --fix 复查按 lining /
  词上限去二次 ×1.1 且走 size_hard / toc|cite|wrapup 与 mermaid|sheet 形状类
  按文档归警告 / D16 行级计数 / 章节枚举与 --fix 栅栏感知 / 内链目标集含卡片 +
  锚点链接校验 / write|export 带 plan 级 export_style / catalog 边统一 id 空间;
  随之更新 validation.md、parallel-writing.md(租约真相+--claim 契约)、SKILL.md)
- **B4 scan/qoder/layers/杂项**(预算只数内容页 / Makefile 按 repo 解析 / qoder
  parent 截断+无 type 页 / lint 列表顶层净错 / layers 多栅栏解析+净门禁错+
  plan 深度 / util.git UTF-8 / 二进制与符号链接不入集 / max-files 内层断 /
  --ignore 路径语义 / i18n 英文提示词表 / 三处裸 == "card" 收敛)
- **B5 性能与清理**(doc_hits 集合化 / collect 记忆化 / 快照哈希增量 upsert /
  jobs_meta / published_paths+kind_counts / finish 全库只读两次 / 卡片 job
  规范化收敛 queue_jobs / lenient 前缀单一来源 / validate --content 单次读 /
  posix/dirname 单一定义(~22 内联收敛) / 死键与死表清理 / ImportError 守卫
  移除 / needs_review 死列删除+content_hash 落读者 / install.sh 剥离 .pwiki)

**部分处理**:#53 前缀集中到 gates 单一来源(未做结构化 findings 大改);
#54 第二遍 os.walk 保留(stat 代价小, 仅去掉重复全表 SELECT);#64 复核确认
第一轮已修(c077), 顺手清了 TITLE_MAP 重复键。

**事故披露**: B5 的 posix() 机械替换中, 一个按行括号计数的"修复"脚本误伤了
55 行合法续行 —— 依据脚本自身的输出日志逐行逆向恢复, 全套 185 测试通过
(含 42 条本轮新增回归)后确认无残留。
