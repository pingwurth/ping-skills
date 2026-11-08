# pwiki 第三轮 max 代码审查报告(2026-11-09)

> 审查对象:第二轮回修后的同一工作区改动 —— 未跟踪的整个 `pwiki/` skill(约 15k 行)+ `.gitignore`/`AGENTS.md`/`CLAUDE.md`/`install.sh` 修改 + 已暂存的 `sensors-analyze/.repowise/*` 生成库删除(该删除本身判定正确)。
> 基线:185 个测试全绿(审查前)。
> 方法:10 个 finder 角度(按模块组分 3 组行扫、跨文件、Python 陷阱、适配器保真、复用、化简、效率、架构层、约定)+ 被删行为审计 + gap sweep(补 6 条,含 tests/templates/yamlite 角落)→ 80 个原始候选 → 去重后 **73 条存活发现**;逐条独立验证投票:**71 CONFIRMED / 2 PLAUSIBLE / 0 refuted**(另有 1 条预验证淘汰:BM25 每查询重建,系 qwiki 对齐的有意取舍)。
> 下方数组即严重度排序(数组顺序即编号 #1 起):**#1–13 = P0 崩溃/数据丢失/永久破坏;#14–30 = P1 validate 放行后崩溃 + 协议/稿件丢失;#31–52 = P2 静默错误行为;#53–73 = P3 清理(重复/死代码/浪费/文档契约)**。

```json
[
  {
    "file": "pwiki/scripts/pwiki_lib/gates.py",
    "line": 561,
    "summary": "fix_off_by_one calls int(a) with a=None for any plain [x](file://path) citation (CITE_RE's #L group is optional), crashing every export path with TypeError",
    "failure_scenario": "Page content containing a plain file:// link to an existing file → compose_export_content → fix_off_by_one raises TypeError; cmd_finish only catches OSError (engine.py:869), so `pwiki finish` dies with a bare traceback and the run stays stuck in LINING. Reproduced. Reachable even from the engine's own output: rebuild_cite_block emits plain links (gates.py:106) and D18's harvester emits them for a=None, which then feed fix_off_by_one in validate --fix. Also on publish_run, write, export db_to_file, reconcile rename, validate --content --fix paths."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/gates.py",
    "line": 624,
    "summary": "validate --content --fix writes the injection-stripped content back to the published file, permanently deleting the export layer's injected <cite> block and numbered TOC from concise-style published trees",
    "failure_scenario": "Concise wiki page whose published file = H1 + <cite> + body + injected 目录, with one section lacking 章节来源 (only a warning): `pwiki validate --content --fix` strips the layers at gates.py:615-616, appends the source block, and writes the stripped variant at 620-625 — reproduced: <cite> and 目录 vanish from the published file; no re-compose exists in this path (compose_export_content is only called from publish.py), so the tree stays degraded until a full re-export from DB."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/reconcile.py",
    "line": 133,
    "summary": "_apply_title_rename applies a D22 proposal without the sibling-title-collision guard validate_plan enforces, so a rename equal to a sibling's title overwrites the sibling's published file and persists an invalid plan",
    "failure_scenario": "Root siblings 支付模块(id=pay) and 结算(id=pay2); writer's D22 proposes renaming pay2 to 支付模块: assign_paths derives pay2's new path = pay's exact path (planner.py:461-465 hard-errors this exact condition as 兄弟标题冲突), _move_published_file unlinks/writes it (reconcile.py:111-113), two page rows now share one path, and save_plan persists the plan that begin/update will reject until hand-fixed — silent loss of one page's content."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/publish.py",
    "line": 739,
    "summary": "export file_to_db decides to strip <cite>/TOC from the style preset alone while compose_export_content's injection gate also honors plan-level export_style, so under concise + export_style: current an author-written cite block / 目录 section is silently deleted from the DB on import",
    "failure_scenario": "Plan sets export_style: current (concise): compose injects nothing (publish.py:125 checks both keys), so a page legitimately carries its own <cite> block; after any hand edit, `pwiki export --direction file_to_db` hits publish.py:739's preset-only check, strip_injected_layers removes the cite block and the whole 目录/Contents section, and save_page_content persists the stripped text over the DB original — reproduced; permanent silent data loss."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/gates.py",
    "line": 621,
    "summary": "The --fix pipeline runs fix_section_sources (D18) before fix_off_by_one (D23), so the harvester copies not-yet-clamped line numbers into the new 章节来源 block where the clamp guard can never reach them — validate --content --fix never converges",
    "failure_scenario": "Concise page cites [a.py](file://a.py#L5) on a 4-line a.py in a section lacking 章节来源: after --fix the file contains '**章节来源** - [a.py:5-5](file://a.py#L5-L5)' while the body link was clamped to #L4; fix_off_by_one's guard (a_i <= n, gates.py:567) skips 5>4, so every subsequent --fix run reports citation/off-by-one and exits EXIT_GATE(2) forever — reproduced end-to-end, including export file_to_db absorbing the broken citation into the DB."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/reconcile.py",
    "line": 152,
    "summary": "D22 rename cascade composes each moved file against a partially-updated DB, leaving dead internal .md links in parent and all-but-last children that the engine's own validator then reports as errors",
    "failure_scenario": "Rename 父→新父 with children c1,c2: the parent's export is composed while c1/c2 rows still hold old paths (its child links rewrite to ../父/c1.md), each child composed before the next moves, and nothing re-composes after the loop (only indexes refresh) — reproduced: parent exports a link to the soon-to-be-unlinked path; validate --content then flags links/internal: 不解析 on the tree reconcile itself just produced."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/reconcile.py",
    "line": 141,
    "summary": "_apply_title_rename lacks the active-run guard its sibling _apply_card_conflict has (reconcile.py:216-218), so a mid-run D22 rename is applied and then silently reverted when the active run finishes",
    "failure_scenario": "Writer submits a title_mismatch mid-run; operator runs `pwiki reconcile --all` while jobs are in flight: the rename applies immediately (row retitled, file moved), but finish's publish INSERT OR REPLACEs the still-queued job's old-title row, re-emits the old-path file, and prunes the new-path file as a moved-out orphan — the rename is fully lost with exit 0 and no mention."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/publish.py",
    "line": 219,
    "summary": "publish_run's kept-branch merge copies only title/parent_id/ordinal/path into the published row, so plan changes to article_type, slug, module, kind and card_kind never reach the DB or indexes while snapshot.check_item compares exactly type/slug",
    "failure_scenario": "Published page gains `type: deep_dive` + `slug: pay` via hand edit: every `pwiki update` flags catalog_changed, finish 'moves' it, but the kept merge (publish.py:220-232) keeps the old row fields — update re-flags the same page forever, spawning a 0-job INCREMENT run each time; repowiki-metadata.json/_index.yaml keep stale type/slug indefinitely."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/gates.py",
    "line": 660,
    "summary": "validate --content's internal-link check is fence-blind and does not percent-decode %20, while publish.normalize_links deliberately rewrites spaced links to %20 — the validator flags the engine's own freshly-published output and exits 2",
    "failure_scenario": "Two sibling pages titled 'Data Layer.md'/'State Management.md' (safe_filename preserves spaces): finish exports links as [数据层](Data%20Layer.md) (publish.py:86), validate computes target '…/Data%20Layer.md' with a literal %20 that matches no real path (no unquote anywhere in pwiki_lib) → links/internal error → exit 2 on a perfectly valid published tree, so SKILL.md's '验证有错不得宣称成功' release gate fails every time. Reproduced end-to-end."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/scan.py",
    "line": 117,
    "summary": "cite_lines counts newlines in only the first 1,000,000 bytes (MAX_TEXT_BYTES, scan.py:95), so citations into lines beyond 1MB are falsely rejected as out-of-range hard errors",
    "failure_scenario": "A 3.2MB tracked file with 400k real lines reports 125000 lines (verified numerically); a writer's correct [big.sql#L200000-L200010](file://big.sql#L200000-L200010) citation gets 'citation/range: 行范围越界 (文件 125000 行)' — a non-auto-fixable hard error at submit and LINING that permanently blocks valid pages, and fix_off_by_one clamps against the bogus count."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/gates.py",
    "line": 567,
    "summary": "fix_off_by_one's clamp guard requires a_i <= n, so a citation with a==b==n+1 (#L5-L5 on a 4-line file) is classified citation/off-by-one (auto-fixable) by validate_citations but never clamped — a broken line reference ships with every gate silent",
    "failure_scenario": "Writer submits [x](file://a.py#L5-L5) for a 4-line a.py: validate_citations reports off-by-one (classifier gates.py:83-86 has no constraint on a), cmd_finish filters is_auto_fixable errors assuming publish clamps (engine.py:838-839), but fix_off_by_one returns it unchanged (a_i=5 > n=4) — reproduced; the published file carries a link to line 5 of a 4-line file."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/planner.py",
    "line": 804,
    "summary": "The seed/reset replan route rebuilds plan['pages'] from scaffold_pages() alone, silently dropping every module_sheet page — the exact loss the other two routes guard against and document (themes.py:1893-1896: dropped sheets get orphan-pruned by finish)",
    "failure_scenario": "User runs the documented `pwiki plan --reset`: all module_sheet pages vanish from the plan; the next begin queues no sheet jobs and the next finish unpublishes their rows and unlinks the published knowledge/<module>/*.md files as moved-out orphans — published content destroyed by a replan command."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/yamlite.py",
    "line": 322,
    "summary": "yaml_load filters out every line whose text is '---' before parsing, silently dropping bare '---' lines inside human-written block scalars in violation of the module's own '绝不静默丢行' contract",
    "failure_scenario": "Human writes 'prompt: |\n  step one\n  ---\n  step two' in wiki_plan.yaml: yaml_load returns 'step one\nstep two' (reproduced: {'prompt': 'step one\\nstep two'}); any later plan-saving command (modules, reconcile) persists the truncated prompt permanently."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/db.py",
    "line": 446,
    "summary": "An empty `ordinal:` in a hand-edited plan parses to None and passes validate_plan (which never type-checks ordinal), then crashes begin/update/finish with bare int(None) TypeErrors at four coercion sites",
    "failure_scenario": "`ordinal:` left valueless in wiki_plan.yaml → `pwiki begin` → queue_jobs → int(item.get('ordinal', 0)) — the .get default is NOT applied because the key exists — TypeError traceback inside the queue transaction (db.py:446); same crash at publish.py:231, snapshot.py:242-243, qoderplan.py:71/77 (cards too: setdefault at db.py:424 doesn't replace explicit None). All four sites reproduced."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/db.py",
    "line": 450,
    "summary": "`source_files:`/`modules:` null in a plan item is stored as the JSON string 'null' (truthy), bypassing every `json.loads(x or '[]')` guard, and crashes next/submit with TypeError",
    "failure_scenario": "Plan page with source_files: null passes validate (planner.py:309 uses `or []`), begin stores source_files='null'; `pwiki next` does ', '.join(json.loads('null')=None) → TypeError (engine.py:211-212); `pwiki submit` iterates json.loads('null') → TypeError (engine.py:463) — the surrounding except only catches SystemExit. Reproduced end-to-end on a real WikiDB; _job_payload also emits \"source_files\": null to --json orchestrators."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/planner.py",
    "line": 293,
    "summary": "validate_plan's title check `str(p.get(\"title\", \"\")).strip()` turns None into truthy 'None', so a null title passes the gate and begin crashes in safe_filename with a TypeError",
    "failure_scenario": "`- id: auth\\n  title:` (empty YAML value → None via yamlite.py:139-140) → validate reports nothing → begin → page_path_for → safe_filename(None) → re.sub on None → unhandled TypeError (reproduced; same for cards via card_path_for and for children of null-titled ancestors at planner.py:148); cli.py has no catch-all, so the human-edit surface's gate lets through a raw traceback."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/digest.py",
    "line": 56,
    "summary": "build_digest joins depends_on assuming string entries while validate_plan explicitly accepts dict entries ({path: ...}), so a validate-passing plan crashes `pwiki digest`",
    "failure_scenario": "Hand-edited wiki_plan.yaml with depends_on: [{path: user-service}] passes validate (planner.py:244-245 normalizes dep.get('path')); the documented pipeline step `pwiki digest` then raises TypeError: sequence item 0: expected str instance, dict found — reproduced; all writer preparation blocked."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/planner.py",
    "line": 745,
    "summary": "expand_sheets indexes plan[\"pages\"] with a hard key while every other accessor tolerates a missing 'pages' key, so a hand-trimmed plan crashes `pwiki plan --sheets` with a KeyError traceback",
    "failure_scenario": "wiki_plan.yaml written by `pwiki modules` then hand-trimmed to remove the pages: line (modules remain): plan --sheets passes the existing-id computation (plan_pages returns []) and hits KeyError: 'pages' on the first sheet — reproduced; raw traceback on the sole human-editable file's recovery path instead of a die() message."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/themes.py",
    "line": 167,
    "summary": "load_overrides guards YAML errors and tabs with loud warnings but calls aliases_raw.items()/maps.items() with no shape check, so a list-valued title_aliases crashes plan --themes with AttributeError instead of the documented 'overrides 全部忽略' fallback",
    "failure_scenario": "pwiki.yaml with `title_aliases:\\n  - foo` (a natural YAML list form for the documented key): yaml_load returns a list without raising, `({} or ['foo']).items()` raises AttributeError: 'list' object has no attribute 'items' — reproduced; the whole themes route is unusable until the file is fixed, defeating the docstring's loud-warning contract (themes.py:139-143)."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/themes.py",
    "line": 1234,
    "summary": "profile from config.json is hard-indexed into PROFILES/PROFILE_BUDGETS/PROFILE_TARGETS/dep_limit without validation (only the argparse --profile flag is checked), so a typo crashes plan --themes with a raw KeyError while validate/layers silently fall back",
    "failure_scenario": "config.json contains \"profile\": \"standrd\": `pwiki plan --themes` dies with KeyError: 'standrd' (themes.py:1234-1236, 1733) — reproduced; the same typo in validate/plan_limits silently runs with the 135 default (planner.py:282), hiding the inconsistency instead of surfacing it."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/engine.py",
    "line": 510,
    "summary": "cmd_submit decides the no-token completion branch from the job snapshot fetched before gate_item's seconds of file I/O, and the UPDATE's WHERE has no state or token re-check (db.py:560), so a stale resubmit clobbers another writer's fresh claim",
    "failure_scenario": "Writer A's resubmit (job pending after a gate reject) passes the guards on the stale row; during gate_item, writer B's next claims the job (attempts=2, new token); A's stale snapshot still says not-processing so engine.py:508-511 executes the no-token UPDATE setting completed and clearing B's claim (db.py:568) — B's later submit fails token match and orphan-saves; exactly the parallel-writer clobber the lease system exists to prevent."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/engine.py",
    "line": 91,
    "summary": "begin --force cancels the run and releases claims without the in-flight-lease guard regenerate/cancel/pause all enforce (c040), so a concurrently submitting writer's draft is neither accepted nor orphan-saved",
    "failure_scenario": "Writer mid-submit while the operator runs `pwiki begin --force`: run flips to CANCELLED → the writer's submit dies at engine.py:398 '无活动 run' BEFORE reading its draft (stdin variants lose the piped content entirely, no orphan-save, no run_log entry) — the exact draft-loss class c040's guards were added to prevent, reachable from the force-begin side with no submit-side flag."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/engine.py",
    "line": 402,
    "summary": "The LINING state guard dies before the draft is read (read at :410), contradicting the adjacent '先读稿再进状态守卫…稿件不丢' comment, so stdin submissions during a finish's LINING window are discarded with no orphan-save",
    "failure_scenario": "finish is in its long LINING pass while a slow writer pipes its draft: `cat page.md | pwiki submit auth --file -` dies 'run 正在 LINING' (exit 3) at :402 before sys.stdin.read() → the piped content is consumed and lost, work/orphan-submits/ never receives it; only the completed-guard (not the LINING/无活动run/未知job guards) was moved after the read."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/engine.py",
    "line": 294,
    "summary": "cmd_next prints a human 'status: …' line to stdout before the JSON on the in_flight and complete paths, breaking the machine-readable next --json contract exactly at the completion-detection step",
    "failure_scenario": "Orchestrator polling `pwiki next --json | json.loads(stdout)`: while jobs are claimable stdout is pure JSON (claim path, :316-320), but once jobs go in_flight/complete stdout becomes 'status: N 个 job 在写…\\n{…}' → json.loads raises at precisely the transition the polling exists to detect; SKILL.md documents next --json for parallel fan-out."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/engine.py",
    "line": 168,
    "summary": "_job_payload hardcodes \"state\": \"DOCUMENT_GENERATING\" instead of run[\"state\"], so next --json misreports the run state during DOCUMENT_FAILED recovery — the exact scenario the state exists for",
    "failure_scenario": "finish fails LINING → run set DOCUMENT_FAILED (still active, jobs reset pending) → operator's orchestrator polls next --json to re-dispatch → payload reports DOCUMENT_GENERATING while the DB row says DOCUMENT_FAILED (it even reads run[\"generation_type\"] two lines below); any automation branching or auditing on run state gets the wrong value."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/publish.py",
    "line": 671,
    "summary": "cmd_delete --all commits unpublish_pages(pid, []) BEFORE the file sweep and the sweep's os.unlink is bare (only rmdir is guarded), so one unreadable file leaves the DB claiming full unpublish with a half-deleted tree and a raw traceback",
    "failure_scenario": "One EBUSY/EACCES file during `pwiki delete --all`: DB transaction already committed (page rows deleted), the bare os.unlink at :671 raises mid-loop, cli.py has no handler → bare traceback, half-deleted tree remains, success line never prints; shutil.rmtree(base, ignore_errors=True) is the stdlib exact-fit (same hand-rolled pattern at :280-286 and :517-521)."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/publish.py",
    "line": 735,
    "summary": "export file_to_db (publish.py:735), validate --content (gates.py:609) and digest --extra (digest.py:115) read hand-editable files with strict UTF-8 and no guard — the c048 tolerance fix covered only submit/write inputs",
    "failure_scenario": "User hand-edits a published page in a GBK/Latin-1 editor (one stray byte suffices): `pwiki export --direction file_to_db` raises UnicodeDecodeError partway through the loop after earlier pages were already absorbed (partially-applied state); validate --content crashes instead of reporting the page — while cmd_write in the same file dies cleanly via its except UnicodeDecodeError (publish.py:623-628), showing the intended pattern."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/publish.py",
    "line": 254,
    "summary": "The tmp+fsync+os.replace atomic write exists only in planner.save_plan (c045); the per-page publish write and validate --fix's rewrite of published files use plain open(path,'w') truncation",
    "failure_scenario": "A kill or ENOSPC during publish.py:254-255 or gates.py:624-625 leaves a truncated/zero-byte published .md whose DB row still says completed with the old content_hash (needs_review checks only file existence, publish.py:291-292) — a half file silently ships on every later step; grep confirms os.replace/fsync appear only in planner.py, and the sibling engines (jaut/crp state) apply the guarantee to every durable artifact."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/engine.py",
    "line": 106,
    "summary": "begin --missing with no prior run dies with EXIT_GATE(2) although it is a call-order precondition miss, contradicting the project's own exit-code contract (EXIT_PRECOND=3 for 无活动 run per c086 and config.py:33)",
    "failure_scenario": "Fresh workspace, user runs `pwiki begin --missing` first: exit 2, which CI/orchestration reads as a content-gate failure (门禁拒绝) rather than a misuse error — the sibling '无活动 run' die at :398 and --require-git at :98 both use EXIT_PRECOND; no test pins 2 for this path."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/cli.py",
    "line": 249,
    "summary": "_resolve_handler catches ImportError around the handler-module import, so an ImportError raised inside a landed module (typo'd internal import, broken partial install) is swallowed and misreported as \"命令 'X' 尚未实现\" exit 3",
    "failure_scenario": "A future edit adds `from . import gatez` (typo) to gates.py: every `pwiki validate --content` prints '命令 validate 尚未实现(按阶段落地…)' and exits 3 instead of showing the ModuleNotFoundError — the except cannot distinguish missing-module from in-module failure; engine.py:855-856 documents removing this exact anti-pattern elsewhere ('只会把真实导入错误伪装成…')."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/themes.py",
    "line": 1672,
    "summary": "to_plan_pages assigns ordinals after sorting by ASCENDING priority, so the highest-priority page (overview, priority 1000) gets the largest ordinal — inverting order in every ordinal-ascending consumer",
    "failure_scenario": "`pwiki plan --themes` on any repo: root ordinals come out module-deep=0 … overview=4 (reproduced); pages --tree (publish.py:576), the plan.md review tree (themes.py:2003), the Qoder catalogue (qoderplan.py:79) and writer claim order (db.py:463/506 ORDER BY ordinal) all show 项目概述 last and claim the least-important pages first — opposite of scaffold_pages' overview ordinal 0."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/planner.py",
    "line": 592,
    "summary": "scaffold_pages computes max_domains = max_top_articles - ordinal without reserving slots for the overview page and the 其他模块 overflow page, so whenever overflow triggers the generated plan has 21 roots and fails its own validate",
    "failure_scenario": "Repo with 18+ significant top-level modules and setup/dev signals: `pwiki plan --seed` produces 21 roots > max_top_articles=20 (reproduced: 21 roots, ordinal arithmetic verified), `pwiki validate` reports '21 个顶层 > max_top_articles' EXIT_GATE and begin is blocked — violating planner.py:661's stated rule that engine-generated plans pass their own validate."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/themes.py",
    "line": 1769,
    "summary": "The PROVIDER_TITLES card loop matches provider keys ('hf', 'mock', …) as bare substrings of lowercased whole file paths with no boundary/exclusion gating, fabricating external-dependency cards for integrations that don't exist",
    "failure_scenario": "Any JS repo with __mocks__/ or *.mock.ts, or a path like src/pathfinder.ts containing 'hf': the loop finds hits (themes.py:1769) and queues a 'Mock 测试提供商' / 'Hugging Face 推理代理' knowledge card citing test/mock files as production evidence — no later gate catches it (add_card checks only path existence; validate_plan checks only id/title/kind), so a writer job instructs the model to document a nonexistent integration."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/planner.py",
    "line": 258,
    "summary": "The unknown-parent check unions page ids and card ids, so a page whose parent is a card id passes validation but is silently re-rooted, invisible in the plan review tree, and exempt from root-count limits",
    "failure_scenario": "wiki_plan.yaml page with parent: card-build-system: validate reports no error (reproduced); page_path_for's by_page lookup misses the parent so the page publishes at content top level, render_plan_md never emits it (children of card ids aren't recursed), root-cap accounting skips it, and begin queues it with parent_id = the card id — a plan-shape error the gate exists to catch passes silently."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/publish.py",
    "line": 384,
    "summary": "wiki_catalogs rows use slug-space ids but carry parent_id in raw page_id space, so when a parent's slug differs from its page_id the child catalog is orphaned on Qoder import — the exact failure class cid_of was added to fix for relations",
    "failure_scenario": "Hand-edited plan with parent page_id 'svc', slug 'services-slug': _write_indexes emits {'id':'services-slug'} and a child {'id':'ch','parent_id':'svc'} — reproduced; no catalog with id 'svc' exists so the subtree orphans on import, while cat_relations 10 lines below correctly converts via cid_of; the exporter's own comment (publish.py:322-324) states this id-space mismatch means 'slug≠id 的子树导入即失联'."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/qoderplan.py",
    "line": 78,
    "summary": "qoder-plan emit never validates the source plan, so a page with a dangling parent id is neither a root nor reachable by emit_level's DFS — it and its whole subtree are silently omitted from the emitted Qoder plan with exit 0",
    "failure_scenario": "Hand-edited plan with parent: overvie (typo) on 架构 plus its 5 children: `pwiki qoder-plan` writes the other 40 pages, prints '(40 documents; parent 回溯序已保证)' and exits 0 (verified: _check_caps inspects only emitted documents) — the IDE imports a plan missing the entire subtree with no warning, while begin/validate would have caught the typo."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/snapshot.py",
    "line": 223,
    "summary": "Directory-valued scope/source_files entries are expanded to individual files when hashed into the baseline but compared as raw strings in check_item's staleness set, so a scope-only card's tracking set can never intersect changed files — hashed but never compared",
    "failure_scenario": "Card with scope: [src/payment/] and no module: files under src/payment/ are hashed into the baseline (add_scope→expand_scope), but watch = {'src/payment'} ∪ cited never intersects changed FILE paths (cards have no module fallback); renaming the payment logic changes 20 tracked files yet update reports '0 stale items' and the card silently keeps documenting the old code."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/snapshot.py",
    "line": 316,
    "summary": "update's card detection `is_card_kind(kind) or (\"scope\" in job and \"kind\" not in job)` routes a kindless page that has scope: to card_path_for (knowledge tree) while begin/page_path_for put the same item in the content tree — one plan item flip-flops between trees by entry point",
    "failure_scenario": "Hand-edited plan adds {id: custom, title: 自定义, scope: [src/]} (validate accepts kindless pages, planner.py:295): begin queues zh/content/自定义.md; after a source change update queues knowledge/zh/自定义/自定义.md; finish publishes the card path, replaces the page row's path and prunes the content-tree file as moved-out — validate --content then reports coverage/page against the plan path; the legacy disjunct can only ever fire for pages (cards must carry kind), so it protects no real legacy case."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/snapshot.py",
    "line": 270,
    "summary": "[PLAUSIBLE] The style-drift recheck skips non-page kinds with a literal tuple while the gate mechanism styles cards differently per style (concise card=0 line-citations, deep card>=3), so a concise→deep switch never requeues cards",
    "failure_scenario": "Plan style flips concise→deep: update requeues pages/sheets only (kind not in ('page','module_sheet'): continue); concise-era cards persist in the published tree and fail `validate --content` (which gates plan cards at lining severity) with no repair path short of a full begin or unrelated source changes. Verifier confirmed the skip and the per-kind gate asymmetry; finish itself succeeds (it gates only the active run's jobs), so the symptom is persistent validator failures, not a finish block."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/publish.py",
    "line": 702,
    "summary": "cmd_export db_to_file resolves the effective style from the LAST RUN and never consults the plan, while finish/write/update are plan-first — the crash-recovery re-render flips the whole tree under a stale style",
    "failure_scenario": "User edits plan style concise→deep then runs `pwiki export --direction db_to_file` (before any finish): the path whose own comment claims '与 finish 同口径' re-renders every page under concise (injecting <cite>+TOC into all of them), globally flipping the wiki's export form while finish/write would use the new style — the plan is even loaded at :705-709 for export_style, just after style was computed."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/config.py",
    "line": 149,
    "summary": "CITE_RE's path group ([^)\\s#]+) cannot represent repo paths containing spaces: a raw-space link is invisible to every gate, and a %20-encoded link is hard-rejected citation/path — files with spaced names can never be validly cited",
    "failure_scenario": "Repo contains 'docs/my notes.md': [x](file://docs/my%20notes.md#L1-L5) fails os.path.isfile('docs/my%20notes.md') → hard error 引用路径不存在 at submit (reproduced; citation/path is neither lenient-droppable nor auto-fixable); with a raw space the regex never matches, so the link is uncounted, unvalidated, and absent from the rebuilt <cite> block and snapshot tracking set."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/gates.py",
    "line": 246,
    "summary": "validate_citations/extract_citations run on raw fence-inclusive content (the fence-stripped prose is used only for shape checks), so citations inside example code blocks are validated as real and counted toward D16 minimums — inconsistent with the fence-aware iter_h2/fix_section_sources",
    "failure_scenario": "(a) A fenced example '[e](file://no/such/file.py#L1-L2)' hard-rejects the page at submit with citation/path though it is illustrative content (reproduced); (b) fence-only citations satisfy deep's knowledge_citations card>=3 minimum (reproduced: count uses extract_citations(content) with no strip_code_fences) — the D12 anti-fabrication gate is satisfiable by fabricated examples."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/gates.py",
    "line": 281,
    "summary": "The schema/toc gate matches only 目录|Table of Contents while the export layer's TOC_SECTION_RE also matches '## Contents' (and TOC_TITLE['en'] IS 'Contents'), so an English writer's legitimate Contents section passes submit and is then silently deleted from the published file",
    "failure_scenario": "English-locale concise page with a writer-authored '## Contents' section: no gate ever warns (regex mismatch reproduced); compose_export_content's i18n.TOC_SECTION_RE.sub('') deletes the entire section body from the exported file — writer content destroyed with every gate silent."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/engine.py",
    "line": 517,
    "summary": "cmd_submit's summary extraction hand-rolls fence skipping (paragraph must start with '```') instead of calling gates.strip_code_fences, so a non-blank-line-isolated fence leaks example code into the 240-char digest summary shown to later writers",
    "failure_scenario": "First paragraph '示例：\\n```python\\ndef f(): …\\n```' passes the startswith('```') blind spot → the stored summary becomes '示例： ```python def f()…' (strip-first would yield '示例：'); engine.py:244-248 injects each completed child's summary into the parent page's prompt, so phantom fenced content propagates to later writers — the same phantom-content class iter_h2 was written to eliminate."
  },
  {
    "file": "pwiki/templates/module-stack.md",
    "line": 1,
    "summary": "All five module-sheet templates hardcode Chinese H1 titles while sheet job titles are localized via i18n.SHEET_DISPLAY, so a writer following the shipped template in any non-zh wiki is hard-rejected by the engine's own schema/h1 gate",
    "failure_scenario": "en-locale plan (sheet title 'Tech Stack' from sheet_display('stack','en')) + writer submits templates/module-stack.md verbatim ('# 技术栈…') → submit hard error \"schema/h1: H1 '技术栈' != 计划标题 'Tech Stack'\" (reproduced) — every sheet following the shipped templates fails in an en wiki despite full en i18n support."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/yamlite.py",
    "line": 195,
    "summary": "_YLine.indent counts only spaces, so tab-indented lines parse as indent 0 and silently re-nest instead of erroring — bypassing the misindented-continuation guard and the module's never-silently-restructure contract for wiki_plan.yaml",
    "failure_scenario": "Human edits wiki_plan.yaml with 'wiki:\\n\\ttitle: Repo\\n\\tstyle: deep': yaml_load returns {'wiki': None, 'title': 'Repo', 'style': 'deep'} (reproduced) — nested keys escape to top level with no error; 'modules:\\n\\t- id: src\\n\\t  title: Core' loses the module's title and injects a phantom top-level 'title: Core'. The only tab guard (themes.py:155) covers pwiki.yaml alone; load_plan has none."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/yamlite.py",
    "line": 312,
    "summary": "Duplicate mapping keys silently overwrite (last wins) in yaml_load, dropping the earlier value without any error on the designated human-edit surface",
    "failure_scenario": "A page entry accidentally carries two 'prompt:' lines (copy-paste edit): yaml_load keeps only the second (reproduced: {'title': 'second'} for a doubled title) — the first prompt silently vanishes with exit 0, and snapshot's prompt-change detection then compares against the surviving twin; contradicts the module's documented '拿不准就报错, 绝不静默丢行/吞字段' principle."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/yamlite.py",
    "line": 55,
    "summary": "_yaml_emit has no branch for an empty dict, so {} is emitted (and hand-written 'evidence: {}' is parsed) as the quoted string \"{}\" — a silent type flip on round-trip",
    "failure_scenario": "yaml_dump({'a': {}}) → 'a: \"{}\"' and reload returns the string '{}' (reproduced); a hand-written 'evidence: {}' in a plan module entry loads as a str, which db.py:681 json.dumps as a JSON string into the module registry — consumers expecting the dict shape (m['evidence'].get('files', 0)) get AttributeError/str instead."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/yamlite.py",
    "line": 183,
    "summary": "YAML anchors/aliases are not rejected but parse as literal plain strings ('&b [x]' / '*b'), so a DRY-style alias in wiki_plan.yaml degrades to a bogus scope string that _as_list turns into an empty list — the item is silently tracked by nothing",
    "failure_scenario": "'scope: *default_scope' becomes the string '*default_scope' (reproduced); snapshot._as_list's json.loads fails → [], so the module's source changes never mark it stale and every update reports noop — no error anywhere, despite the module's stated contract to fail explicitly on anything it is unsure about."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/publish.py",
    "line": 357,
    "summary": "[PLAUSIBLE] The tolerant JSON-string-column decode exists as snapshot._as_list but _write_indexes and update's check_item call bare json.loads on the same columns, so a malformed row that gates swallows crashes finish/update",
    "failure_scenario": "The same corrupt/legacy page row that gates.gate_item decodes to [] (guarded, gates.py:220-231) raises json.JSONDecodeError at publish.py:357-360/390/476-480 or snapshot.py:226 — bare ValueError traceback from finish/update. No in-tool writer can produce such a row (page rows come from json.dumps), so the trigger needs external DB corruption or a hand-edited legacy DB rather than any in-tool flow."
  },
  {
    "file": "install.sh",
    "line": 183,
    "summary": "The reworked install strip list adds .pwiki but not .repowise/.claude, while sensors-analyze/ contains an 812K .repowise/ runtime-state dir and .claude/settings.local.json on disk — cp -r ships them into every target project (same bug class the .pwiki entry just fixed, c007)",
    "failure_scenario": "`./install.sh claude sensors-analyze <proj>`: line 175's cp -r copies sensors-analyze/.repowise/wiki.db (712KB) + shm/wal + hook-sessions/ and .claude/settings.local.json into <proj>/.claude/skills/sensors-analyze/ — verified on disk; the diff's own comment states the goal ('运行态不装进目标项目') and this very change stages deletion of the tracked copies of those same files."
  },
  {
    "file": "pwiki/tests/test_review_fixes2.py",
    "line": 574,
    "summary": "The '--fix 不注栅栏' regression guard in test_fenced_headings_are_not_sections is vacuous: the fence_part slice re-finds the opening fence, the .replace() strips exactly the text searched for, and n==0 on this fixture so the escape always fires",
    "failure_scenario": "Reproduced: fix_section_sources(bad) returns n=0 and fence_part == '```' (contains no 章节来源), so the assertion passes unconditionally — even a regression that injects **章节来源** blocks inside code fences cannot fail this test; the guard it claims to provide does not exist, leaving the actual D18 fence behavior untested."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/engine.py",
    "line": 140,
    "summary": "The 3-line card-job normalization (dict(item) + source_files-fallback-to-scope + posix(card_path_for)) is copy-pasted at four queue entry points, and snapshot's copy has already grown a legacy heuristic the others lack",
    "failure_scenario": "engine.py:140-142 (begin), engine.py:693-696 (regenerate), snapshot.py:317-320 (update, plus the 'scope in job and kind not in job' disjunct), reconcile.py:233-235 (card conflict) — db.queue_jobs centralized kind/card_kind/ordinal but not this trio; the next producer copies the ladder and forgets the fallback or path rule, queueing cards with empty sources or wrong-namespace paths (engine.py:690-691 documents a past path='' failure). One planner.card_job() helper beside card_path_for covers all four."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/engine.py",
    "line": 534,
    "summary": "cmd_submit opens and JSON-parses the writer --report file twice with different channel enumerations: _rep_summary keeps only exact keys while _ingest_report also accepts aliases",
    "failure_scenario": "A writer emitting the alias spelling files_read_beyond (or title_mismatches/card_conflicts) gets its channels logged to run_log but silently omitted from the job-row report persisted via set_job_report (engine.py:545) — two parsers of one file, each maintaining its own key list; parse once into a normalized dict and feed both consumers."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/publish.py",
    "line": 328,
    "summary": "_write_indexes emits every WIKI_ITEM PARENT_CHILD edge twice — once in the relations loop (:327-335) and again identically in the cat_relations loop (:398-405), merged at :408",
    "failure_scenario": "Both loops filter kind=='page' with parent_id and compute source/target via cid_of, so each parent-child edge appears verbatim twice in repowiki-metadata.json (verified in the test's own fixture); the only test checks id-space, not uniqueness — doubled edges ship to Qoder import and any edge-shape edit must be made in both blocks or they drift."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/modules.py",
    "line": 75,
    "summary": "Two same-named humanize() title builders duplicate the word-casing rule while diverging on preprocessing — modules.py checks TITLE_MAP and basenames, themes.py strips STRIP_EXTS and checks no map",
    "failure_scenario": "Input 'client': module route titles it 客户端 (TITLE_MAP) while theme route titles it Client; 'main.py' → 'Main.py' vs 'Main' — the same directory takes different page titles depending on route, splitting one concept into differently-titled pages; themes.py:188-193 (slugify) shows the intended thin-wrapper pattern after exactly this drift class bit once already."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/modules.py",
    "line": 700,
    "summary": "seed_depends_on hand-rolls a bounded utf-8 read that is a third, drifted copy of corpus.read_text (errors='replace' vs the canonical errors='ignore', own 200_000 limit)",
    "failure_scenario": "scan.py:145-148 already delegates to corpus.read_text whose docstring says '唯一实现见 corpus.read_text(两份逐字拷贝曾各自漂移)'; this copy's replace-policy lets U+FFFD chars flow into _imported_names/_pom_dep_artifacts, and any future fix to the canonical reader (size cap, decode policy) will not reach the pom/import dependency scan — replace the three lines with corpus.read_text(full, 200_000)."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/planner.py",
    "line": 234,
    "summary": "validate_plan contains three near-identical path-existence check blocks (module scope :234-243, page scope :330-338, card source_files :356-364) with drifted wording and predicates",
    "failure_scenario": "The severity rule and out-of-repo ternary are restated three times with wording already drifted ('路径不存在' vs '目录不存在' vs '不存在') and an extra divergence the finder missed: module/card use os.path.exists while page scope uses os.path.isdir — a semantics fix to the c049 handling lands in one block and modules/pages/cards get different plan-gate behavior for the same bad path; one _check_scope_paths helper covers all three."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/publish.py",
    "line": 504,
    "summary": "_write_indexes derives module identity three different ways in one function — _mkey (path or module_id), kindex (path or dir), _module.yaml (dir or module_id) — despite the comment at :451-452 claiming the rule is computed once",
    "failure_scenario": "For module auth (to_plan_modules emits path='auth', dir='auth/'), knowledge/_index.yaml records module_path 'auth' while the sibling _module.yaml — the file Qoder's synchronizeWiki absorbs — records 'auth/' (verified emission at modules.py:566-570): two spellings of one module across the two Qoder-facing registries, so any consumer keying by module_path sees two nodes; one module_path_of(m) helper as the comment already promises."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/themes.py",
    "line": 1901,
    "summary": "cmd_plan_themes duplicates the merge-manual-fields + keep-manual-leftovers logic for pages (:1901-1912) and cards (:1919-1930), plus a third partial copy on the reset path",
    "failure_scenario": "The c027 'never silently drop hand-edits' contract lives in two structurally identical copies (same _MANUAL_FIELDS loop, same old-by-id/new-ids bookkeeping — verified); changing which fields count as manual, or the keep rule, for one kind but not the other makes pages and cards diverge in what a --themes rerun preserves; one merge_items(old, new, manual_fields, keep_pred) helper serves both."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/db.py",
    "line": 295,
    "summary": "create_run is dead product code — every product caller uses create_run_guarded — and its INSERT+run_log body is duplicated verbatim inside the guarded twin",
    "failure_scenario": "Product callers are engine.py:121/682, snapshot.py:300, reconcile.py:222 (verified by grep); bare create_run is invoked only from tests, so a schema change to the run INSERT applied to the live twin leaves the dead one stale and the suite green on the stale path — delete it and point tests at create_run_guarded, or extract the shared insert into one helper."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/gates.py",
    "line": 290,
    "summary": "Inside the else branch of `if not p.get(\"cite_block_required\")`, line 290 re-tests the same key — always True there since preset() returns one of two dicts that both define it; nearby :410-411 an `if …: pass / elif` shape reads as a missing branch",
    "failure_scenario": "No third state can reach the inner test (verified against styles.py:61-62), so the guard is dead weight that invites future edits to wedge checks between the two tests as if the inner one were meaningful; delete the inner if and dedent, and invert the 410 test to a plain conjunction."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/scan.py",
    "line": 664,
    "summary": "Three call sites still inline .replace(os.sep, '/') instead of calling util.posix — including one in expand_scope whose file branch calls posix() nine lines later in the same function",
    "failure_scenario": "scan.py:664-665 (directory branch of expand_scope; file branch at :677 uses posix()), publish.py:76, gates.py:665 — all three operands are already str so swapping is behavior-identical (verified); if the normalization rule ever evolves (collapse './', duplicate slashes) these silently keep the old spelling and path comparisons (gates.py:666 vs publish.py:77 membership tests) start disagreeing on the same link."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/snapshot.py",
    "line": 112,
    "summary": "snapshot_run hashes the full tracked-file set at every finish without passing the baseline, leaving the size+mtime shortcut that snapshot_hashes already implements unused on the finish path",
    "failure_scenario": "Every `pwiki finish` — including an INCREMENT that changed 3 pages — re-reads and re-hashes all tracked files (~200MB on a 10k-file repo per the docstring's own numbers) though the DB row read at :116 already holds size/mtime/sha; cmd_update demonstrates the fix (:174-176 passes baseline=) — hoist the file_hashes read above :112 and pass it, making unchanged files cost one stat()."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/snapshot.py",
    "line": 203,
    "summary": "cmd_update os.walks every module scope tree twice per update (once inside snapshot_hashes, once building mod_files) and runs extract_citations twice per published page",
    "failure_scenario": "On a monorepo with ~10k in-scope files, each update performs two full directory walks of the same trees plus a duplicate regex scan of every page's content (:95 and :224) — expand each scope once into a {module: files} map, feed it to both consumers."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/reconcile.py",
    "line": 103,
    "summary": "_move_published_file builds its all_paths set from published_pages (SELECT * dragging every content blob) although db.published_paths exists for exactly this lightweight consumer — and it runs per moved file in a rename cascade",
    "failure_scenario": "A D22 rename with descendants re-pulls all ~135 published rows with 10-30KB bodies (~4MB) per moved file just to build a set of path strings; one published_paths(pid) call (or hoisting it before the move loop) is a drop-in per the helper's own docstring."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/publish.py",
    "line": 204,
    "summary": "finish reads the full jobs table twice (engine.py:833 for LINING, then publish_run) — SELECT * with content/report blobs each time",
    "failure_scenario": "Per finish on a 135-job run, ~2-4MB of job bodies are materialized redundantly seconds apart; snapshot_run already accepts pages= for exactly this reason (engine.py:868) — fetch once in cmd_finish and pass the rows to publish_run the same way."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/util.py",
    "line": 40,
    "summary": "join_confined recomputes os.path.realpath(repo) (plus realpath of the target) on every call though the repo root is constant for the process",
    "failure_scenario": "Per-citation at submit and LINING, per tracked file in snapshot, per source file in publish — easily 50k+ calls per run, each a two-realpath syscall chain (~10 lstat per path); memoize the root realpath per repo (or resolve once on Ctx) — halves the syscalls in the hottest helper."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/engine.py",
    "line": 107,
    "summary": "begin --missing builds the published-id set from published_pages (SELECT * with all content blobs) when only page_id values are needed",
    "failure_scenario": "Every recovery run pulls the whole published corpus (~135 x 10-30KB) out of SQLite to construct a set of ids; db.published_paths is the existing precedent for a slim set-returning twin (SELECT page_id)."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/db.py",
    "line": 537,
    "summary": "leased_jobs does SELECT * (content/report blobs per leased row) but all four callers (status/pause/cancel/regenerate) read only page_id and lease_expires_at",
    "failure_scenario": "`pwiki status` — polled repeatedly while writers work — drags each in-flight job's full body just to print ids and lease times; jobs_meta/JOB_META_COLS is the file's own documented precedent for metadata consumers (bounded to in-flight rows, so the saving per call is small — pattern violation more than hot cost); select only the meta columns."
  },
  {
    "file": "CLAUDE.md",
    "line": 7,
    "summary": "The edited line claims 'A collection of six agent skills … each top-level directory with a SKILL.md is a self-contained skill' but the repo has 7 such directories — claude-review/ is tracked, carries valid frontmatter, and is installed by discover_skills",
    "failure_scenario": "By the sentence's own counting rule there are 7 (batch-unit-test-generator, claude-review, code-review-p, java-unit-test-generator, pwiki, sensitive-log-review, sensors-analyze — verified by listing and by running discover_skills): `./install.sh all` installs 7 skills while both governing docs say six and neither skill table lists claude-review; the five→six edit in this change perpetuates the false count."
  },
  {
    "file": "AGENTS.md",
    "line": 3,
    "summary": "Same false 'six agent skills' claim in the sibling governing doc, edited by this change",
    "failure_scenario": "AGENTS.md:3 states six; the Project Structure table (:7-14) lists 6 and omits claude-review, so the count claim and the installable set (7, verified) disagree — a contributor following AGENTS.md will not know claude-review exists or that install.sh will install it."
  },
  {
    "file": "CLAUDE.md",
    "line": 24,
    "summary": "CLAUDE.md's description of install.sh's strip list is stale: install.sh (modified in this same change) now also strips .pwiki, which the doc's enumerated list omits",
    "failure_scenario": "Repo rule (CLAUDE.md:76: 'keep them in sync with script behavior') broken by the change itself: the doc says install.sh 'strips tests/, __pycache__/, .pytest_cache/, test.log, docs/, and workflows/ for non-claude targets' while install.sh:183-185 now also strips .pwiki — future readers auditing what install removes get an incomplete list."
  }
]
```

## 回修备注(整理者注,非审查发现)

- **关联分组,宜一并修**:#1/#5/#11 同落 fix_off_by_one / --fix 管线(None 崩溃、D18→D23 顺序、clamp 遗漏);#9(P0 %20 校验)与 #41(P2 CITE_RE 空格)同根;#14–20 为 wiki_plan.yaml/pwiki.yaml 手工编辑面"validate 放行后崩溃"簇,可在 validate_plan / load_overrides 收口;#46–49 为 yamlite 簇(含 #13 P0 的块标量丢行)。
- **与前轮部分保留决策可能重叠**:P3 的 #63(scan.py:664 内联 posix 归一)与 #65(snapshot.py:203 双 walk/双扫描)对应第二轮回修记录"#53 仅前缀集中、#54 保留二遍 walk"(见 2026-11-08-code-review-max-round2.md)——动手前先核对是否维持原决策;#65 本次还包含每页双 extract_citations,是新内容。
- **范围内非 pwiki 代码的发现**:#51 install.sh 漏剥 `.repowise`/`.claude`(与本次已暂存删除 sensors-analyze/.repowise 同一主旨);#71/#72 两份治理文档"六个 skill"实为 7 个(claude-review 未入表);#73 CLAUDE.md 的 install 剥离清单漏 `.pwiki`。
- **两处 PLAUSIBLE**:#39(风格漂移不重排卡片)、#50(裸 json.loads,需外部 DB 损坏才触发)。

---

## P0 回修记录(2026-11-09,#1–#13 全修)

改动文件:`gates.py` / `publish.py` / `reconcile.py` / `planner.py` / `scan.py` / `yamlite.py`;
回归测试 `tests/test_review_fixes3.py`(13 例,`test_p0_1` … `test_p0_13`);
全套 **198 绿**(185 存量无回归 + 13 新增)。

| # | 位置 | 修法 |
|---|---|---|
| 1 | gates.fix_off_by_one | repl 顶部 `a is None` 早退 —— 纯 file:// 链接(CITE_RE #L 组可选)无行号可钳,曾 `int(None)` 崩掉所有导出路径 |
| 2 | gates.validate_content | ①剥除判定换成 `publish.export_injects`(且仅 kind=='page';卡/sheet 从不注入也不剥)②--fix 写回走 `compose_export_content` 重合成,不再把剥掉注入物的原稿口径写回 |
| 3 | reconcile._apply_title_rename | 改名前加兄弟标题冲突守卫(validate_plan 同规,同父 kind=='page' 同名 → rejected),不再覆盖兄弟发布文件/持久化非法 plan |
| 4 | publish.export/file_to_db | 新增 `export_injects(ctx, style, plan)` 单一判定(preset export=='legacy' 且 plan 级 export_style=='legacy'),compose 与 file_to_db 剥除同判 —— current 模式下作者手写 cite/目录不再被剥 |
| 5 | gates.validate_content | --fix 管线换序:先 D23 钳位再 D18 收割,未钳行号不再被抄进 **章节来源** 块 |
| 6 | reconcile._apply_title_rename | 两阶段:先把全部受影响 DB 行改到新路径,再统一落盘搬文件 —— 父页不再对着半更新的 DB 合成死链 |
| 7 | reconcile._apply_title_rename | 加 `_apply_card_conflict` 同款活跃 run 守卫 → retry 保留 pending,不再被 finish 静默回滚 |
| 8 | publish.publish_run kept 合并 | 目录字段随 plan 全量同步:kind(归一行形态)/card_kind/module/sheet/article_type/type→article_type/slug —— update 不再 0-job INCREMENT 死循环 |
| 9 | gates.validate_content 内链 pass | ①`urllib.parse.unquote` 解码 %20(normalize_links 自己编码的口径)②栅栏内示例链接不参与校验(与改写侧同口径);真断链仍报 |
| 10 | scan.cite_lines | 全量分块计数(1MB 块),不再按 MAX_TEXT_BYTES 截断 —— >1MB 文件行号引用不再误判越界;末行无换行补 1 口径不变 |
| 11 | gates.fix_off_by_one | b-branch 守卫 `a_i <= n` → `a_i <= n+1` 且 `a2 = min(a_i, n)` —— a==b==n+1 的整范围差一真的被钳 |
| 12 | planner.cmd_plan seed/reset | module_sheet 页随注册表保留(`kept_sheets` 并入重建 pages,themes 路线同款守卫)—— reset 不再丢 sheet、finish 不再孤儿剪除知识树 |
| 13 | yamlite | '---' 不再全局过滤:顶层文档首/尾分隔符显式跳过,中途出现由 `_parse_block` 边界 break + unparsed 检查**显式报错**(多文档不支持),块标量体内(缩进更深)由块收集器原样收进体 |

附带修正(随 #2/#4 顺带收敛,非独立条目):validate --content 曾对**卡片/sheet** 也按预设剥注入物 —— 卡/sheet 的发布文件从不含注入层,该剥除本身就是错口径,现统一为 `kind=='page' and export_injects(...)`。

待回修:P1 #14–30(17)、P2 #31–52(22)、P3 #53–73(21)。

---

## P1 回修记录(2026-11-09,#14–#30 全修)

改动文件:`util.py`(新增 `as_int`/`atomic_write`)/`db.py`/`engine.py`/`planner.py`/`digest.py`/`themes.py`/`qoderplan.py`/`snapshot.py`/`publish.py`/`gates.py`/`reconcile.py`/`cli.py`;新增回归 14 例(`test_p1_*`),另两条存量测试随契约更新(`test_begin_missing_leaves_no_stray_run` #29 退出码 2→3、`test_next_in_flight_vs_complete` #24 stdout 纯 JSON)。全套 **212 绿**。

| # | 修法 |
|---|---|
| 14 | `util.as_int` 兜底五处 int 位点(db `_insert_job`/queue_jobs 卡片 ordinal(setdefault 不替换显式 None)/publish kept 合并/snapshot `check_item`/qoderplan emit×2);validate_plan 新增 ordinal 类型检查(错误级) |
| 15 | 入库归一:`_insert_job` 四个 JSON 列表列 `or []`(null 不再存成 'null');消费侧 `engine._json_list` 容错解码兜旧库(_job_payload/_fmt_job/submit 的 modules 迭代) |
| 16 | validate_plan 页/卡 title 检查改 `isinstance(str)`(None 不再被 str(None)='None' 洗成真值) |
| 17 | digest 模块表 depends_on 归一 `{path:...}` 形态再 join |
| 18 | expand_sheets 用 `plan.setdefault("pages", [])`,缺 pages 键不再 KeyError |
| 19 | load_overrides 对 title_maps/title_aliases 加形状检查(非映射 → 大声警告 + 该键忽略,兑现自述契约) |
| 20 | cmd_plan_themes 校验 config profile(不在 PROFILES → die EXIT_GATE 列出可选值);validate_plan 对未知 profile 出警告(不再静默 135) |
| 21 | submit 在 gate_item 后**重读行**再决定令牌与去向:门禁期间被重认领(state=processing 且 attempts>1)→ 孤儿稿 + 拒绝;拒绝/完成两路径共用该重读状态,不再拿入场快照走无 token UPDATE 顶掉在写 writer |
| 22 | begin --force 取消 run 的在写窗口由 23 的"先读稿"兜住(强撤语义与 cancel --force 一致: 在制稿件走孤儿稿) |
| 23 | cmd_submit 读稿移到**一切状态守卫之前**:无活动 run / LINING / 未知 job 三守卫全部先保稿再挡(run 消失时审计走 manual 频道),stdin 交稿不再蒸发 |
| 24 | cmd_next 人类状态行 `--json` 时走 stderr(`_say` 助手)—— stdout 恒纯 JSON |
| 25 | `_job_payload` 的 run.state 取真值(不再硬编码 DOCUMENT_GENERATING) |
| 26 | delete --all 先清文件树(`shutil.rmtree(ignore_errors=True)`)再清 DB;删不净给 note,不再半删态+裸 traceback |
| 27 | export file_to_db / validate --content / digest --extra 三处读手编文件加 UnicodeDecodeError 守卫(die EXIT_GATE 带文件路径,c048 口径) |
| 28 | `util.atomic_write` 统一原子落盘:publish_run 页写/cmd_write/db_to_file/gates --fix 回写/reconcile 改名落盘/submit 知识文件提前落盘/planner.save_plan(原 c045 内联版收编) |
| 29 | begin --missing 无旧 run 退出码 2→EXIT_PRECOND(3) |
| 30 | `_resolve_handler` 区分"模块缺失"(→None 诚实 P3)与"模块内部 ImportError"(按 `e.name` 判定,原样抛) |

待回修:P2 #31–52(22)、P3 #53–73(21)。

---

## P2 回修记录(2026-11-09,#31–#52 全修)

改动文件:`config.py`/`i18n.py`/`gates.py`/`planner.py`/`themes.py`/`qoderplan.py`/`snapshot.py`/`publish.py`/`engine.py`/`yamlite.py`/`util.py`/模板×5/根 `install.sh`;新增回归 18 例(`test_p2_*`),一条存量测试重写(#52 恒真空转断言 → 栅栏逐字节不变 + 三真节注入)。全套 **230 绿**。

| # | 修法 |
|---|---|
| 31 | to_plan_pages 同父内按 priority **降序**发号(overview→0 号), 树视图/认领/目录序不再倒挂 |
| 32 | scaffold_pages 的 domain 名额为"其他模块"溢出页预留 1 个根位 —— 溢出触发时 ≤ max_top_articles, 自产计划过自家 validate(25 模块合成夹具锁定) |
| 33 | provider 证据改 `provider_hits()`: 词边界匹配 + 测试/mock 路径排除(__mocks__/.spec./pathfinder 含 'hf' 不再触发) |
| 34 | 页的 parent 合法集只含**页** id(parent 指卡 → validate 错误, 不再静默重根) |
| 35 | wiki_catalogs 的 parent_id 换算到 slug 空间(cid_of), slug≠id 子树导入不再失联 |
| 36 | qoder-plan emit 对 DFS 够不着的悬空 parent 子树 die EXIT_GATE(不再静默少导出 exit 0) |
| 37 | update 的陈旧对账新增 `expand_dirs`: 目录型 source/scope 按展开文件对账(与基线哈希同口径, 补尾斜杠);scope-only 卡随目录内变更 stale |
| 38 | 删除 update 排队的遗留析取("有 scope 无 kind"按卡)—— 无 kind 页恒走 content 树, 不再按入口在两棵树间漂移 |
| 39 | 风格漂移复检纳入卡片(kind 元组 +card)—— concise→deep 后旧卡会重排 |
| 40 | export db_to_file 的 style 改 plan 优先(与 finish/write/update 同口径), 自愈渲染不再按 last_run 回旧风格 |
| 41 | CITE_RE 路径组允许空格;extract_citations 统一 unquote 解码(%20 与裸空格同归真实路径), 发射侧 `_md_href` 编码 %20 —— 带空格仓内文件可合法引用且全链路口径一致 |
| 42 | 引用真实性/D16 计数全部改用**剥栅栏**正文(gate_item/rebuild_cite_block/_gate_card/_gate_sheet/快照跟踪集);fix_off_by_one 钳位同样跳过栅栏 —— 示例引用不再被硬拒、也不再凑行级下限 |
| 43 | 门禁的目录标题行与剥除侧同源(i18n.TOC_HEADING_RE, 含 'Contents')—— 英文目录节过门即被提示, 不再发布时静默删除 |
| 44 | submit 摘要提取先 `strip_code_fences` 再取首段 —— 栅栏贴着首段时示例代码不再漏进 240 字摘要 |
| 45 | 五张速查表模板 H1 改 `# {{TITLE}}` 占位(与 page.md 对齐), 非 zh wiki 照模板写不再必挂 schema/h1 |
| 46 | yaml_load 对缩进含 Tab 的行显式 ValueError(不再按缩进 0 静默重排嵌套) |
| 47 | 映射重复键显式 ValueError(不再静默 last-wins) |
| 48 | 发射侧空 dict 出 `key: {}`(列表项内同), 解析侧 `{}` 回空 dict —— 往返不再类型翻转 |
| 49 | 锚点/别名(`&x`/`*x`)显式 ValueError(不再降级成假 scope 字符串导致零跟踪) |
| 50 | `util.json_list` 单一容错解码;_write_indexes 全部裸 json.loads 换装, update 的 old_srcs 走 `_as_list` —— 坏行不再让 finish/update 裸崩 |
| 51 | install.sh 剥离清单补 `.repowise`/`.claude`(运行态与本地配置不再装进目标项目;bash -n 验证) |
| 52 | test_fenced_headings_are_not_sections 的"--fix 不注栅栏"断言重写为真实不变量(旧断言恒真空转: 切片重找开栅栏 + replace 删掉搜索串 + n 恒 0) |

待回修:P3 #53–73(21)。

---

## P3 回修记录(2026-11-09,#53–#73 全修 —— 本轮 73 条全部落位)

改动文件:`planner.py`/`engine.py`/`snapshot.py`/`reconcile.py`/`publish.py`/`themes.py`/`modules.py`/`db.py`/`gates.py`/`util.py`/`scan.py`/根 `CLAUDE.md`/`AGENTS.md`;新增回归 5 例(`test_p3_*`),其余为行为保持重构(全套锁定)。全套 **235 绿**。

| # | 修法 |
|---|---|
| 53 | 新增 `planner.card_job()`(source_files 回退 scope + knowledge 路径),begin/regenerate/update/reconcile 四个排队入口统一调用 |
| 54 | `_load_report` 单次解析喂两个消费方;job 行报告并入别名通道(files_read_beyond 等) |
| 55 | PARENT_CHILD 边只由 cat_relations 一处发(两循环同条件重复发射收敛) |
| 56 | humanize 单一实现收敛到 modules(basename→TITLE_MAP→剥 STRIP_EXTS→分词),themes 改薄封装 —— 'client'/'main.py' 两路同题 |
| 57 | seed_depends_on 的第三份手抄有界读换 `corpus.read_text`(解码口径归一) |
| 58 | 新增 `_check_repo_path()`,module scope/page scope/card 源三段手抄块收敛(谓词 exists/isdir 与措辞统一) |
| 59 | `module_path_of()` 单一推导 —— `_index.yaml` 与 `_module.yaml` 不再一个记 'auth' 一个记 'auth/'(键形分叉测试锁定) |
| 60 | 新增 `themes.merge_manual()`,pages/cards/reset 三份合并手抄收敛 |
| 61 | `db._insert_run` 共享 INSERT(create_run/create_run_guarded 共用;create_run 明确为测试/单进程直建入口,产品调用方全走守卫式) |
| 62 | gates 死代码清理:else 分支内恒真的 `cite_block_required` 再测删除;`if…pass/elif` 死形状改单条件 |
| 63 | scan/publish/gates 三处残留内联 `.replace(os.sep,'/')` 收敛到 `util.posix`(与第二轮 ~22 处收敛同批遗留) |
| 64 | `snapshot_run` 传 baseline —— finish 路径未变文件走 size+mtime 短路(与 update 同口径) |
| 65 | **维持第二轮"第二遍 os.walk 保留"决策**(stat 代价小);本轮只去重 `extract_citations`(每页引用集单算一次,check_item 复用) |
| 66 | `_move_published_file` 改 `published_paths` 轻量孪生,改名级联取一次路径集共用(不再每搬一个文件全表拖稿体) |
| 67 | `publish_run(jobs=)` 直传 —— finish 的整队 SELECT * 从两次降一次 |
| 68 | `join_confined` 的 repo realpath 进程级记忆化(最热助手, 曾一次运行 5 万+ 次重复 realpath 链) |
| 69 | 新增 `db.published_ids()`(begin --missing 不再为 id 集合拖整库稿体) |
| 70 | `leased_jobs` 只取 page_id/lease_expires_at(消费方只读这两列) |
| 71/72 | CLAUDE.md/AGENTS.md:six→**seven**,`claude-review` 补入两处技能表 |
| 73 | CLAUDE.md 的 install.sh 剥离描述补运行态目录(.pwiki/、.repowise/、.claude/) |

## 全轮总结

- 第三轮 73 条(P0 13 / P1 17 / P2 22 / P3 21)**全部回修**;185 → **235 绿**(+50 回归: test_review_fixes3.py 45 例 + 随契约更新/重写的存量 3 处 + test_yamlite 等零散)。
- 两条随契约更新的存量测试:#29 退出码 2→3、#24 next --json stdout 纯 JSON;一条重写:#52 恒真空转断言。
- 决策延续:#65 的第二遍 walk 维持第二轮"保留"决策,仅去重正则扫描。
