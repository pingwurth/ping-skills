# pwiki 第四轮 max 代码审查报告(2026-11-09)

> 审查对象:第三轮回修后的同一工作区改动 —— 未跟踪的整个 `pwiki/` skill(约 11.2k 行,全部为新代码)+ `install.sh`/`.gitignore`/`CLAUDE.md`/`AGENTS.md` 修改 + 已暂存的 `sensors-analyze/.repowise/*` 生成库删除。
> 基线:235 个测试全绿(审查前)。`install.sh` 重构与文档更新本身验证干净(empty-xargs、`.claude` strip、多行 frontmatter 下的 tools 行剥离、gitignore 覆盖均经实证检查)。
> 方法:10 个 finder 角度(行扫拆分为 13 个 agent;Python 陷阱角度因限流改为主线程内联)→ 去重 → 逐条三态验证(9 个 verifier agent,约 30 条经执行复现而非仅阅读)→ gap sweep(7 条候选全部 CONFIRMED,其中 4 条经复现)→ sweep 验证。3 条候选被驳回淘汰:gates.py 行归一化三处重复(三处变换方向各异)、snapshot.py 裸 `'card'` 跳过字面量(所有入队路径都已归一化 kind,今日不可达)、注册文件非原子写的加重项。
> 存活:**77 条(73 CONFIRMED / 4 PLAUSIBLE)**;数组顺序即严重度排序(#1 起):**前 25 条为正确性 bug(静默数据丢失、计划自毁、update 不收敛、竞态);中段为退出码/契约漂移与 yamlite 手编面拒识;尾段为清理项(复用/死代码/效率/文档)**。标 "(plausible)" 的 4 条为机制属实、触发条件存疑或属潜伏。

```json
[
  {
    "file": "pwiki/scripts/pwiki_lib/modules.py",
    "line": 777,
    "summary": "`pwiki modules --reset` replaces the ENTIRE plan (pages: [], cards: [], aliases, limits, style all wiped) although its help text advertises only re-deriving the modules: registry, destroying the sole human-edit surface.",
    "failure_scenario": "User with a hand-edited 100-page plan runs `pwiki modules --reset` expecting a registry refresh → wiki_plan.yaml is overwritten with pages=[]/cards=[] → all hand edits irrecoverably lost; begin then dies ('pages 与 cards 均为空'), and after re-planning with new ids the next finish's unpublish_pages + file-prune deletes the old published rows and files."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/publish.py",
    "line": 283,
    "summary": "The publish loop has no path-collision detection while validate_plan compares raw titles and page_path_for routes through safe_filename, so sibling titles differing only in characters safe_filename strips collide on one filename and the second write silently overwrites the first page.",
    "failure_scenario": "Siblings '什么是Config?' and '什么是Config*' both normalize to '什么是Config' → identical paths, no UNIQUE(path) on the page table → second atomic_write overwrites the first page's file; _index.yaml lists both ids; one completed page's content is silently absent from the published tree."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/reconcile.py",
    "line": 118,
    "summary": "_move_published_file writes the new file then os.unlink(old_full) with no old_path==new_path guard, and _apply_title_rename queues the move unconditionally, so a rename whose derived path is unchanged deletes the published file it just wrote.",
    "failure_scenario": "D22 title_mismatch on a module_sheet (sheet paths derive from module dir + display, not title) or titles that safe_filename maps identically ('配置?'→'配置*') → atomic_write(same path) then os.unlink removes the just-written file; DB row still says published at that path; reconcile reports '1 files moved' while the exported tree silently loses the page."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/engine.py",
    "line": 881,
    "summary": "finish's pending-check and its all_jobs snapshot have a TOCTOU window against a late submit on a failed job: submit's LINING guard reads run state once, its final writes carry no run-state condition, so a job can flip to completed after finish snapshotted it and publish consumes the stale snapshot.",
    "failure_scenario": "Reproduced deterministically: job X failed; writer resubmits while finish runs → finish reads counts (X terminal), sets LINING, snapshots X as failed; submit (guard passed pre-LINING) completes X; finish validates/publishes only the old snapshot and marks the run COMPLETED/PARTLY_COMPLETED → wiki reported complete but page X never published (published_page returns None)."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/layers.py",
    "line": 360,
    "summary": "`plan --layers commit` wholesale replaces plan[\"pages\"] (keeping only module_sheet pages), silently discarding hand-added pages and hand edits with no --reset flag and no merge_manual — the silent rebuild that PLAN_HEADER's '重建只经 --reset' promise and the themes route's c027 merge both forbid.",
    "failure_scenario": "User hand-adds pages or curates titles/prompts in wiki_plan.yaml, then finishes layered planning → commit overwrites the whole content tree with only 'N article pages + M module sheets' printed; previously published hand pages become orphans pruned at the next finish, nothing mentions the dropped pages."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/snapshot.py",
    "line": 85,
    "summary": "add_scope passes the raw scope string to expand_scope, whose directory branch requires a trailing '/', so a hand-edited slash-less directory scope silently tracks zero files in both the snapshot baseline and update's changed-set — and the codebase's own s2+\"/\" workaround is applied only in expand_dirs, not here or mod_files (scan.py:687 isfile branch).",
    "failure_scenario": "Reproduced: hand-edit a module's scope from 'src/' to 'src' → validate passes (planner checks only os.path.exists) → expand_scope('src') collects 0 entries vs 22 with the slash → `pwiki update` reports 'no update required (noop)' forever; every page sourced only from that scope stays silently stale."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/gates.py",
    "line": 634,
    "summary": "validate_content's one() early-returns for a planned page missing from disk before the only place entries are recorded (report_pages.append / errors_total.extend), so the constructed 'coverage/page: 计划页未落盘' error is silently dropped.",
    "failure_scenario": "Reproduced: plan page whose zh/content/X.md is absent (interrupted publish, pruned tree, path typo) → `pwiki validate --content` prints '0 错误, 0 警告, needs_review 0 页' and exits 0 — the validator green-lights a tree that is missing pages."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/planner.py",
    "line": 830,
    "summary": "The `plan --reset/--seed` rebuild dict preserves title/repo_summary/export_style/aliases/modules/placeholders but silently drops hand-edited limits, author_pages, and author_notes — even though the old limits are consumed to size the scaffold and then not saved.",
    "failure_scenario": "Reproduced: hand-set limits {max_sub_per_parent: 12} → `pwiki plan --reset` emits 12 subs per domain using the old limits, saves a plan with no limits key → `pwiki validate` immediately fails the same pages against the default cap 8; author_pages/author_notes vanish (qoder-plan emit loses notes, layers init loses its whitelist)."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/layers.py",
    "line": 279,
    "summary": "materialize_layers writes a style-agnostic prompt that forbids a top citation list ('不要写目录、结论或顶部引用清单'), but deep — the DEFAULT_STYLE — requires a self-supplied <cite> block as a LINING hard error, so a default-style layers-planned run fails wholesale at finish.",
    "failure_scenario": "`pwiki init` (style=deep default) → plan --layers init/apply/commit → begin → writers obey their job prompt → every page lacks <cite> → `pwiki finish` rejects every job with 'schema/cite: deep 页必须自带 <cite> 块' → run DOCUMENT_FAILED; the layers route only works under concise."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/themes.py",
    "line": 151,
    "summary": "KIND_TO_TYPE maps kind 'feature' to 'deep_dive' (a LEAF_TYPE) but the features section builds feature-guide parents WITH children, so themes-generated plans fail their own validate leaf rule and begin is blocked.",
    "failure_scenario": "Reproduced: web repo with pages/*.tsx (desktop_ev<3) → plan --themes emits feature-guide (type deep_dive) with children → `pwiki validate` errors \"type 'deep_dive' 按设计是叶子却有 1 个子篇\" → begin blocked; plan --themes itself exits 0 because its own check gate never tests leaf-with-children."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/themes.py",
    "line": 352,
    "summary": "The deep architecture prompt tells writers '至少用两张图' (at least two diagrams) while the deep preset's mermaid band is (5,8) with size_hard=True, so 2-4 prompt-compliant diagrams are LINING hard errors — the exact prompt/gate mismatch the codebase already fixed once for concise (docstring at :330).",
    "failure_scenario": "deep plan via --themes; writer of an 架构设计 page follows the prompt and embeds exactly 2 mermaid diagrams → submit warns only, finish appends 'size/mermaid: 2 图 (目标 5-8)' as an error → page rejected, run DOCUMENT_FAILED."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/engine.py",
    "line": 889,
    "summary": "finish re-resolves the gate/publish style from the CURRENT plan (plan > run > config), bypassing the per-job style stamp submit uses (job['style'] > run > config), so a plan style edit between begin and finish makes submit-time and finish-time gates disagree and mass-rejects a run.",
    "failure_scenario": "begin under concise (jobs stamped, prompts ask short pages); operator edits wiki_plan.yaml style: deep mid-run; writers submit and pass → finish re-reads the plan and runs LINING with the deep preset on every job (400-520 lines, cite band, D16) → mass 'lining rejected' DOCUMENT_FAILED, and the tree would compose under deep export the pages were never written for."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/planner.py",
    "line": 309,
    "summary": "The hard content-page budget check uses raw PROFILE_BUDGETS and ignores the documented plan `limits.max_total_articles` override, while the parallel tree check at :406 honors it — the override can lower but never raise the cap, leaving validate dead with no plan-side remedy.",
    "failure_scenario": "Reproduced: hand-edit limits {max_total_articles: 160}, grow to 140 content pages → plan_limits reports 160 in effect and the tree check passes, but :309 errors \"140 内容页超出 profile 'standard' 预算 135\" → validate exits 2, begin blocked; only config profile change or deleting pages works."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/gates.py",
    "line": 123,
    "summary": "rebuild_cite_block anchors on a raw `^# ` search and replaces the first raw <cite> occurrence without fence-awareness (unlike every other gate in the file), so concise pages whose body starts with a code fence get the regenerated cite block — and then the TOC — injected INSIDE the fence.",
    "failure_scenario": "Reproduced: concise page starting '```yaml\\n# app config comment\\n…```' before the real H1 → composed export contains the <cite> block inside the yaml fence and the H1 bare; no gate flags it (schema/h1 strips fences, schema/fence only matches leading ```markdown), and the published page ships a corrupted example fence."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/engine.py",
    "line": 203,
    "summary": "_fmt_job (human-readable `pwiki next`) omits the job's required_sections that _job_payload (--json) carries, and the schema/sections gate is submit-silent (strict-only), so text-mode writers never see the plan-mandated section list their pages are hard-graded on at LINING.",
    "failure_scenario": "Reproduced: deep is default and stamps the 9-section template on every page; a writer using plain `pwiki next` gets prompt/objective/modules/sources but no section list, writes fewer H2 sections, passes submit with zero warnings → finish LINING rejects: 'schema/sections: 计划要求 9 节, 实得 6' — whole batches die after all writing is done."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/snapshot.py",
    "line": 265,
    "summary": "check_item detects plan-item staleness with a hand-enumerated tuple (sources ∩ changed + prompt + title/parent/ordinal/type/slug), omitting required_sections, modules, pack_key, and objective — and the stored plan_hash is never compared anywhere — so edits to those plan fields trigger no stale reason.",
    "failure_scenario": "Add required_sections or change a page's modules in wiki_plan.yaml → `pwiki update` reports 0 stale, 'no update required (noop)', the page is never re-queued; the new requirements surface only as permanent validate --content errors (only manual `pwiki regenerate <id>` repairs)."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/snapshot.py",
    "line": 276,
    "summary": "check_item builds the after-tuple with str(item.get(\"type\"))/str(item.get(\"slug\")) which turn explicit YAML nulls into the string 'None' while published rows normalize to '', producing a catalog_changed flag that never converges.",
    "failure_scenario": "Reproduced: hand-edit a plan page with empty `type:` (validate_plan skips null-typed pages) → every `pwiki update` marks the page catalog_changed → 0-job INCREMENT run every time, all files rewritten each cycle, finish's kept-merge writes '' back and the next update mismatches 'None' vs '' again — endless churn."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/planner.py",
    "line": 285,
    "summary": "page_ids for the unknown-parent gate is built from ALL pages including module_sheet entries, so a content page parented to a sheet id passes validate — then `pwiki qoder-plan` dies ('parent 悬空、子树未导出') because it filters sheets out of by_id, and roots/min_roots accounting miscounts.",
    "failure_scenario": "Reproduced end-to-end: plan with page parented to a module_sheet id → validate_plan returns zero errors; qoder-plan raises SystemExit 2 with the dangling-parent message; in strict mode the miscounted roots fire a misleading min_roots error instead."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/config.py",
    "line": 120,
    "summary": "WIDE_CITE_PACK_KEYS = {architecture, ai, api} omits the 'microservices' aggregation pack (themes.py:1247, kind=architecture, instantiated at ≥3 services/* modules), violating the set's own '新增聚合包要同步这里' contract, so its deep pages gate at the standard 60-90 cite band instead of wide 80-120.",
    "failure_scenario": "deep wiki over a microservice repo (40+ services): the 微服务集群 parent or cluster child pages cite 91-120 files as designed → cite_band_for returns (60,90) → strict+size_hard LINING hard error 'size/citations: 100 条引用 (本类分档 60-90)' on pages whose citations are exactly the intended wide-band shape."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/publish.py",
    "line": 58,
    "summary": "normalize_links builds its fallback target map by bare basename ({basename: p}), so pages sharing a filename across subtrees (legal — only same-parent titles are validated) collapse to one entry and broken relative links are silently rewritten to the wrong subtree's page.",
    "failure_scenario": "Reproduced: 架构/部署.md and 运维/部署.md coexist; a writer's '../部署.md' slip from 架构/深入.md resolves via the basename fallback to 运维/部署.md depending on set-iteration order — the wrong subtree's page — and validate --content accepts it because the wrong target does exist."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/snapshot.py",
    "line": 196,
    "summary": "cmd_update --from-commit consumes `git diff --name-only` without `-c core.quotepath=off`, so non-ASCII paths arrive C-quoted/octal-escaped and never match the UTF-8 tracked-set keys — every CJK path in the from-commit pass is a silent no-op.",
    "failure_scenario": "Reproduced: page cites 服务/组件.md; from_commit predates a change whose content later equals the refreshed hash baseline → git emits \"src/\\347\\273...md\", the `p in current` filter drops it, info even prints '0 在跟踪集' → stale page ships while the ASCII half of the same diff is caught."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/corpus.py",
    "line": 119,
    "summary": "trim_sibling_overlap records its audit entries via b.get(\"path\")/a.get(\"path\"), but all three call sites pass Builder page dicts before assign_paths ever sets a path — every sibling_trims entry stores page: null, blinding the D13 human review gate.",
    "failure_scenario": "Reproduced: two pack children sharing >65% sources → trim fires → work/themes-report.json carries {\"page\": null, \"detail\": \"dropped x.py shared with None\"} and plan.md's D13 section prints '- `None` dropped …' — the reviewer cannot see which page lost which sources."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/gates.py",
    "line": 84,
    "summary": "validate_citations adds the cited path to the returned set BEFORE the os.path.isfile check, and kept pages are never re-gated at finish, so rebuild_cite_block regenerates exported cite blocks including nonexistent files.",
    "failure_scenario": "A kept concise page whose cited source was deleted between runs → compose_export_content regenerates the <cite> block from the set including the dead path → the freshly published tree carries a '[deleted.py](file://deleted.py)' dead link and finish exits 0 (only a manual validate --content would flag the body)."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/publish.py",
    "line": 715,
    "summary": "cmd_delete for a single page unpublishes the DB row and unlinks the file but never rewrites _index.yaml, knowledge/_index.yaml, repowiki-metadata.json, or generation-state.json, leaving phantom entries pointing at a missing file in the published indexes (the Qoder import surface).",
    "failure_scenario": "`pwiki delete <page_id>` after a finish → the .md is gone but content/_index.yaml's pages list and repowiki-metadata.json's wiki_items/page_ids still list the page until some later finish rewrites them; the --all branch rmtree's the indexes, showing the single-page branch is the oversight."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/gates.py",
    "line": 683,
    "summary": "validate --content trusts the plan's stored path keys, which are only refreshed by assign_paths at plan-write time (never by begin/finish/hand edits) while every other flow derives identity on the fly via page_path_for — the validator reads the unmaintained source of truth.",
    "failure_scenario": "Hand-edit a title (the documented workflow) → begin/finish publish at the newly derived path and prune the old file → validate --content reads the stale plan path and reports 'coverage/page: 计划页未落盘' for every renamed page; conversely a plan whose path keys were never assigned validates zero pages and exits 0."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/engine.py",
    "line": 933,
    "summary": "finish's publish/snapshot try block catches only OSError and the LINING gate loop sits outside any try with no outer handler in cli.main, so any other exception (sqlite3.OperationalError 'database is locked', non-OSError from gates) escapes as a bare traceback leaving the run in LINING.",
    "failure_scenario": "Reproduced: DB write lock held past busy_timeout during finish → uncaught sqlite3.OperationalError, run stuck LINING; per the c037 guard every concurrent submit is force-orphaned (exit 3, drafts to work/orphan-submits/) until a human reruns finish. util.as_int-style targeted handling exists for this crash class elsewhere but not here."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/engine.py",
    "line": 98,
    "summary": "begin --force cancels any active run with no LINING guard and finish's terminal write (:942) unconditionally sets state=final with no WHERE-state recheck, so a finish mid-LINING when force-cancelled later resurrects the CANCELLED run to COMPLETED/PARTLY_COMPLETED and stamps the partition baseline mid-flight of the new run.",
    "failure_scenario": "Reproduced: finish paused in LINING; `begin --force` visibly cancels the run and starts a new one; finish resumes → cancelled run flips to PARTLY_COMPLETED and partition.baseline_run_id is stamped with the cancelled run's id while the new run is DOCUMENT_GENERATING — run table and baseline now describe a run that was supposedly cancelled."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/engine.py",
    "line": 805,
    "summary": "cmd_cancel calls release_all_claims BEFORE update_run(CANCELLED) — the opposite order to cmd_regenerate (:736-737) and begin --force — so a concurrent `next` in the window claims a job on a run that is then cancelled.",
    "failure_scenario": "Reproduced by injecting claim_jobs into the two-statement gap: cancel exits 0 with a job stranded state='processing' on the CANCELLED run; the dispatched writer's later submit exits 3 and its finished page lands in work/orphan-submits/."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/publish.py",
    "line": 258,
    "summary": "publish_run's kept-merge does int(item.get(\"ordinal\", p[\"ordinal\"])) on the plan re-read at finish without re-validating the plan, so an explicit YAML `ordinal:` null defeats the .get default and crashes mid-finish (util.as_int exists for exactly this crash class and is used at the other sites).",
    "failure_scenario": "Reproduced: begin validates the plan, run completes; user hand-adds `ordinal:` (empty) on a kept published page → `pwiki finish` → int(None) TypeError traceback, run left in LINING with no diagnostic (begin's ordinal guard never runs at finish)."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/config.py",
    "line": 231,
    "summary": "Ctx.load() guards config.json only against OSError/ValueError (syntax/IO), so a valid-JSON non-dict top level makes self.config a str/list and the first .get() crashes with a raw AttributeError instead of the diagnosable path c044 built for hand-edit errors.",
    "failure_scenario": "Reproduced: hand-edit .pwiki/config.json to `\"deep\"` or a JSON array → json.load succeeds → AttributeError: 'list' object has no attribute 'get' at :231, bare traceback, exit 1."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/engine.py",
    "line": 301,
    "summary": "cmd_next does int(ctx.config.get(\"lease_minutes\", ...)) with no coercion guard on the human-editable config.json, so a null or non-numeric value crashes every next invocation.",
    "failure_scenario": "Reproduced: config.json with \"lease_minutes\": null → every `pwiki next` dies with TypeError before claiming anything — all parallel writers stall until the config is hand-fixed, with no die()-style diagnostic (util.as_int is the codebase's own helper for this)."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/layers.py",
    "line": 356,
    "summary": "layers commit materializes and saves the plan with no validation — apply_layer_result checks per-article shape only, budget/max_top/max_sub appear solely as prompt advice — so commit exits 0 writing a plan its own `pwiki validate` rejects (the themes route gates before saving).",
    "failure_scenario": "LLM layer rounds accumulate 150 articles (no budget gate at apply) → `plan --layers commit` saves and prints success → `pwiki validate` errors '150 内容页超出 profile standard 预算 135' → begin blocked; recovery requires hand-editing work/plan-layers.json or re-running init, which discards all planning."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/layers.py",
    "line": 315,
    "summary": "`plan --layers init` unconditionally overwrites work/plan-layers.json — interactive session state the module's own docstring distinguishes from regenerable cache — silently discarding accumulated applied layers, and _load_layers swallows corrupted JSON as 'no layer state', steering the user into the overwrite.",
    "failure_scenario": "Three rounds of applied <result> outlines (15 articles) collected; user re-runs `--layers init` out of habit → state reset to {articles: []} with no warning; same total loss if the JSON is truncated mid-save, since the parse error is swallowed."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/themes.py",
    "line": 1943,
    "summary": "merge_manual for `plan --themes` preserves only _MANUAL_FIELDS=('title','prompt') on same-id pages, silently reverting hand edits to source_files/scope/objective/parent/ordinal — while the plan.md review gate this same command prints tells the user to adjust wiki_plan.yaml '标题/合并/来源' (sources).",
    "failure_scenario": "User curates a page's source_files per the review prompt, re-runs `pwiki plan --themes` after a repo change → same-id regenerated page keeps only title/prompt; the hand-picked 来源 and objective are overwritten with no warning in the diff report, violating the sole-human-edit-surface contract."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/engine.py",
    "line": 546,
    "summary": "(plausible) The omitted---claim convenience path adopts the row's current claim_token whenever state=='processing' and attempts==1, but attempts==1 is also another writer's FIRST claim, so a submitter who never claimed can consume that writer's lease.",
    "failure_scenario": "Reproduced in isolation: writer A claims (attempts=1, token T); writer B (never claimed, off the documented token-forwarding protocol) submits without --claim → token T adopted, A's content completes the job; A's own-token submit then dies exit 3 with her page orphaned. Requires an orchestrator that dispatches without forwarding tokens — not a path the engine itself forces."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/cli.py",
    "line": 264,
    "summary": "argparse usage errors exit with code 2, colliding with the documented EXIT_GATE=2 contract ('退出码 0/1/2(门禁)/3(前置)'), so CLI misuse is indistinguishable from a content-gate rejection.",
    "failure_scenario": "`pwiki submit page-1 --fil page.md` (typo for --file) → argparse prints usage, exits 2; an orchestrator keying on the documented contract reads 2 as 'gate rejected (content problem)' and retries/escalates content instead of surfacing the typo."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/layers.py",
    "line": 323,
    "summary": "layers preconditions exit with EXIT_GATE(2) — 'no layer state' (:323) and '--layers apply 需要 --file' (:330) — while engine and qoder-plan classify identical 'prerequisite missing / supply the input' conditions as EXIT_PRECOND(3) per the c086 contract.",
    "failure_scenario": "`pwiki plan --layers apply` before init (or without --file) → exit 2, which the documented contract means content-gate failure → the orchestrator retries the apply or reports a plan defect instead of performing the init step the message asks for."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/planner.py",
    "line": 869,
    "summary": "`plan --aliases <path>` opens and yaml-parses the file with no error handling — a missing/typo'd path is a bare FileNotFoundError traceback and malformed YAML an uncaught ValueError, breaking the exit-code contract every sibling input-file path (submit/write/lint, all c086-labeled) honors.",
    "failure_scenario": "Verified by execution: `pwiki plan --aliases aliases-typo.yaml` (absent) → Python stack dump, exit 1 — never the clean exit-3 an orchestrator keys on to mean 'supply the input file'; a YAML scalar result also lands unvalidated into plan[\"aliases\"]."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/layers.py",
    "line": 331,
    "summary": "`plan --layers apply --file <path>` reads the result file with an unguarded open(), crashing with a traceback on a missing file while every sibling input-file path was fixed to die(EXIT_PRECOND).",
    "failure_scenario": "Verified by execution: `pwiki plan --layers apply --of parent --file /tmp/nope.json` (writer saved elsewhere / typo) → FileNotFoundError traceback, exit 1, instead of a one-line exit-3 error."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/engine.py",
    "line": 354,
    "summary": "_load_report returns None silently when the --report path doesn't exist (only parse failures get an info() line), so a typo'd report filename drops the writer's whole D22/D7/uncertainties channel while submit exits 0 — and the DB row records the phantom report_file as if attached.",
    "failure_scenario": "Reproduced: `submit p1 --file p.md --claim T --report report-typo.json` → only the usual gate warnings, exit 0, reconcile queue empty; row shows {\"report_file\": \".../report-typo.json\"} — the audit trail claims a report was ingested that was never read."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/digest.py",
    "line": 122,
    "summary": "cmd_digest skips each --extra file that isn't on disk with no warning, so D11's 'early notes must go via --extra' silently produces a digest without them.",
    "failure_scenario": "Reproduced: `pwiki digest --extra arch-notes-typo.md` → prints 'digest: … (N chars)', exit 0, no trace of the notes in work/repo-notes.md; all subsequent writers get context missing the architecture notes and nothing in the output mentions the drop."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/gates.py",
    "line": 374,
    "summary": "The size/cited-files upper bound is appended to warnings unconditionally, while references/validation.md:45-48 lists size/cited-files among the 风格错误 that deep upgrades to error at LINING and every sibling size check (page/words/mermaid/citations) upgrades both bounds under strict+size_hard.",
    "failure_scenario": "A deep page citing 15 distinct files passes LINING with a warning and publishes, while the published doc's 错误码一览 promises error-level rejection (target 6-13) — anyone relying on validation.md to predict finish outcomes is wrong for the over-bound case; test_styles locks numbers only, not severity, which is why the drift survived."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/engine.py",
    "line": 904,
    "summary": "--lenient has two meanings: submit drops only LENIENT_DROP_PREFIXES classes ('引用真实性与卡片 frontmatter 永不放宽', gates.py:60-63), but finish (:904) and cmd_write (publish.py:667) treat it as a full bypass of all LINING errors, aliased to --force.",
    "failure_scenario": "Reproduced: a page citing a nonexistent path yields a non-droppable lining error 'citation/path: 引用路径不存在: nope.py', yet `pwiki finish --lenient` skips it and publishes — precisely the error classes the lenient contract promises to keep; submit's own rejection message pointing users to --lenient steers them into the far broader finish semantics."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/publish.py",
    "line": 660,
    "summary": "cmd_write gates and renders under the page row's stamped style (`p[\"style\"] or ctx.style`), the only site of five that skips plan-level style (finish :889, export :740, update :215, validate --content :623 all resolve plan-first), so write judges identical content under a different preset than validate/update/finish after a plan style flip.",
    "failure_scenario": "Wiki published under concise, plan style flipped to deep: `pwiki write pg-1 fix.md` passes under the stale concise preset and publishes content that `pwiki validate --content` immediately rejects under deep (exit 2) — write succeeds where the engine's own validator fails its output (and the reverse rejection direction holds after a deep→concise flip)."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/db.py",
    "line": 230,
    "summary": "PRAGMA busy_timeout=5000 overrides (lowers) the 10.0s timeout passed to sqlite3.connect at :225, so BEGIN IMMEDIATE in claim_jobs/create_run_guarded gives up after 5s under write contention — the two lines contradict each other.",
    "failure_scenario": "Verified empirically: fresh connection reads busy_timeout=10000 after connect(timeout=10.0) and 5000 after the PRAGMA → parallel writers' next/submit raise 'database is locked' after 5s instead of the configured 10s, crashing that invocation instead of waiting out a finish's multi-statement transaction."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/yamlite.py",
    "line": 247,
    "summary": "List-of-maps item continuation absorption stops at any comment line (text != '' after comment-strip), so a '#' comment inside a sequence item makes the item's remaining fields fall out of the block and yaml_load fails 'unparsed line' on valid YAML — only this construct treats comments as block boundaries (plain/nested mappings skip them).",
    "failure_scenario": "Reproduced: '  - id: a\\n    title: A\\n    # note\\n    parent: \"\"' → ValueError 'unparsed line: parent' — the sole human-edit surface rejects a legal comment inside a module/page entry, and the error points at the field after the comment."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/yamlite.py",
    "line": 148,
    "summary": "_parse_scalar converts true/false but not YAML-1.1 yes/no/on/off (which the emitter itself quotes as boolean-ambiguous on write), so hand-written 'no'/'off' come back as truthy strings with no type validation anywhere.",
    "failure_scenario": "Reproduced: hand-write 'skeleton: no' meaning not-a-placeholder → parsed as truthy string \"no\" → planner.py:474 `if m.get(\"skeleton\"): continue` exempts the module from the D-coverage hard rule — the exact opposite of the writer's intent, silently."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/yamlite.py",
    "line": 187,
    "summary": "Flow-list splitting tracks quote state but not bracket nesting, so a nested inline list is silently shredded into string fragments instead of erroring, violating the module's own '拿不准就报错, 绝不静默' contract.",
    "failure_scenario": "Reproduced: hand-written 'a: [[b, c], d]' → {'a': ['[b', 'c]', 'd']} — a scope/depends_on entry intended as a nested list silently becomes three bogus scalar strings that downstream consumers treat as real paths/ids."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/yamlite.py",
    "line": 310,
    "summary": "Block-scalar dedent takes min indent over ALL nonblank body lines including shallower comment lines, so a comment between key and body indent silently re-indents the whole block.",
    "failure_scenario": "Reproduced: 'prompt: |\\n      line1\\n    # note\\n      line2' → value becomes '  line1\\n# note\\n  line2' — body lines silently lose relative indentation (a shallow comment anywhere shrinks every line's leading spaces), no error raised."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/modules.py",
    "line": 563,
    "summary": "id_of maps BOTH the workspace root (path '') and any top-level directory literally named 'root' to module id 'root', producing two registry entries sharing one id that modules_by_id last-wins conflates.",
    "failure_scenario": "Reproduced: repo with a top-level root/ dir (≥4 files) → to_plan_modules emits two id='root' rows; modules_by_id collapses them (sheet paths, digest table, cmd_modules' old_by_id merge cross-contaminate); begin's duplicate-id check turns it into a confusing '重复 module id' refusal — after `pwiki modules` already wrote the broken plan silently."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/publish.py",
    "line": 575,
    "summary": "(plausible) _source_hashes uses raw json.loads(p[\"source_files\"] or \"[]\") instead of the util.json_list the same module imports and uses at :384/:417 — the exact expression util.json_list's docstring documents as the historical 'null' TypeError bug.",
    "failure_scenario": "A row storing 'null' (truthy) makes json.loads return None → 'for s in None' TypeError crashes finish/publish's generation-state write while every other site degrades to []. No current writer can store 'null' (db.py:456 normalizes), so this is latent defense-in-depth — a pre-fix legacy DB row keeps it above refuted."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/layers.py",
    "line": 44,
    "summary": "_save_layers writes work/plan-layers.json — interactive planning session state, not regenerable cache per the module's own docstring — with a plain truncating open() instead of util.atomic_write, and _load_layers swallows the resulting corruption.",
    "failure_scenario": "A crash/ENOSPC mid-save truncates the file → _load_layers's except returns None → 'no layer state' steers the operator into init, which overwrites the salvageable outlines — the whole layered-planning conversation restarts; every sibling cache write (digest/scan/themes/engine init) shares the pattern but only layers is session state."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/publish.py",
    "line": 461,
    "summary": "Index/registry writes (repowiki-metadata.json :461, _index.yaml :474/:518, _module.yaml :542, generation-state.json :568) use plain open(path,'w') although the same module imports and uses util.atomic_write for pages/knowledge/rename, violating atomic_write's 'planner.save_plan 之外的落盘点统一走这里' contract.",
    "failure_scenario": "A crash or ENOSPC mid-publish leaves truncated index registries and a half-written generation-state.json next to atomically-intact page files — external Qoder-side consumers read inconsistent registries until the next finish rewrites them (update's own baseline is the DB, so staleness detection itself is unaffected)."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/digest.py",
    "line": 133,
    "summary": "cmd_digest writes work/repo-notes.md — the shared context every `next` points writers at — with a plain truncating open() instead of util.atomic_write.",
    "failure_scenario": "Mid-run `pwiki digest` re-run (legal during DOCUMENT_GENERATING) is killed after truncation → repo-notes.md holds half the digest; every subsequent `pwiki next` silently hands writers the truncated module table/page directory/glossary and pages are planned against it with no error until someone regenerates."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/corpus.py",
    "line": 313,
    "summary": "(plausible) Corpus.collect's memoization key omits the spec keys 'min', 'commands', and 'readme' which _collect consumes to change results, so two same-key calls with different specs return the first call's cached list.",
    "failure_scenario": "Latent today — every current spec that varies min is collected with pool (not memoized) and all memoized calls have unique patterns — but it goes live the moment a pack reuses another's patterns+exclude+grep+docs while differing in min/commands/readme, silently missing the grep backfill or command-manifest evidence."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/search.py",
    "line": 70,
    "summary": "(plausible) cmd_search rebuilds the whole BM25 corpus (per-page field tokenization + tf + df) from scratch on every invocation with no persisted index — a documented deliberate tradeoff ('每次查询对全语料现算'), so a design note more than accidental waste.",
    "failure_scenario": "Measured 37-147ms per rebuild for 180 concise pages (re-paid every query; deep 20-40KB pages extrapolate to ~0.5-0.8s) — fine for interactive use, but an orchestrator doing repeated retrieval during update runs pays visible latency each call; a DB-persisted corpus refreshed at finish is the fix if search ever becomes a hot path."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/snapshot.py",
    "line": 31,
    "summary": "_as_list (:31) and gates.py's three inline decodes (:231-242, :313-317) re-implement util.json_list with identical semantics, although util.json_list's docstring declares itself the single implementation for exactly the publish/snapshot/gate consumer set (and gates.py already imports util).",
    "failure_scenario": "Four decode paths for the same DB JSON columns: the next bad-row-shape fix lands in util.json_list and misses the copies, so gate verdicts and finish/update row consumers drift apart on the same data — the drift class the shared helper was created to eliminate."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/qoderplan.py",
    "line": 152,
    "summary": "_has_cjk re-declares the CJK char class '[一-鿿]' that already exists as search._CJK_RE (search.py:18), with the same range embedded a third time in publish._md_anchor (publish.py:35).",
    "failure_scenario": "Three independent definitions of what counts as CJK: widening the range (e.g. extension blocks) in one copy makes qoderplan byte-cap lint, BM25 tokenization, and anchor generation disagree — a title counted as CJK by search but not qoderplan gets mis-estimated in byte-truncation warnings."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/themes.py",
    "line": 329,
    "summary": "prompt_for builds a zh-only (style × kind) page-prompt table via nested if-branches that bypasses the i18n.SHEET_PROMPTS (style, locale, kind) mechanism, and Builder stores self.locale (:252) but never reads it — two parallel prompt systems for one concept.",
    "failure_scenario": "An --locale en run gets English sheet prompts via i18n.sheet_prompt but Chinese page prompts from prompt_for; every new style or locale knob must be edited in both table systems or the page and sheet prompt vocabularies drift apart — re-creating the 'prompt-written page rejected by its own gate' failure the style parameter was added to fix."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/engine.py",
    "line": 424,
    "summary": "The ~14-line submission-read block (stdin '-' / abspath / missing-file die / UnicodeDecodeError die / strip+newline) is duplicated verbatim in publish.cmd_write (publish.py:646-659) with already-drifted message wording.",
    "failure_scenario": "The c048/c086 fixes had to be applied twice; the next encoding or exit-code fix lands in one copy only, so submit and write reject the same bad file differently — one util.read_submission() helper covers both."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/themes.py",
    "line": 1700,
    "summary": "report['check']['theme_declined'] duplicates the top-level report['theme_declined'] (same builder list stored twice in one report dict); every consumer reads only the top-level key.",
    "failure_scenario": "Two sources of truth inside one JSON artifact: a future edit to one copy (filtering/reordering declined entries for plan.md) leaves the other stale, and any consumer picking the wrong key sees a different decline list — pure delete of the nested copy."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/themes.py",
    "line": 513,
    "summary": "The 13-language hand_langs tuple is written out twice in the same file — inline in make_leaves' seed filter (:513-516) and as the local hand_langs in run_themes' backfill (:1523-1524), identical order.",
    "failure_scenario": "Adding a language to one filter but not the other silently changes which files count as legal seed evidence vs legal backfill evidence, and the scope/hit gate then fails pages for seeds the planner itself planted — the drift class the codebase's own STRIP_EXTS comments warn about."
  },
  {
    "file": "pwiki/tests/test_deep_review_fixes.py",
    "line": 26,
    "summary": "_ctx/_cli are byte-identical copies of test_review_fixes.py:24-32, while test_review_fixes2.py:31 already demonstrates the established import-them pattern (natural shared home: tests/conftest.py beside gitrepo.py).",
    "failure_scenario": "A Ctx or cli.main signature change must be replicated in every copy; a missed copy fails only that suite, and fixture fixes get applied to some suites but not others."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/db.py",
    "line": 711,
    "summary": "set_file_hashes has zero callers anywhere (scripts and tests) — it is the old full-table DELETE+rewrite path that snapshot.py:118-123 deliberately retired (10k-row churn per 3-page increment).",
    "failure_scenario": "Dead weight that re-opens a fixed bug: a future caller reaching for the obvious-looking 'set all hashes' API silently reintroduces the regression the upsert/delete pair was built to eliminate."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/db.py",
    "line": 533,
    "summary": "extend_lease has no production caller (only tests invoke it; engine.py:555 documents the call was removed as a no-op), and its docstring still claims 'submit 成功路径顺延租约' — directly contradicting references/parallel-writing.md:63 ('submit 不顺延').",
    "failure_scenario": "A maintainer extending submit reads the docstring, concludes lease extension is intended behavior, and re-wires the call — silently reintroducing the removed behavior while the shipped user doc still promises no extension."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/db.py",
    "line": 765,
    "summary": "glossary_set_definition is a write API with no production writer (digest.py:8 states there is no CLI entry); the only insert path hardcodes definition='' and digest's definition-rendering branch can never fire in production.",
    "failure_scenario": "Half-built machinery: 10 lines of dead DB API plus a glossary schema path implying engine-managed definitions; maintainers debug rendering that nothing can ever populate."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/db.py",
    "line": 314,
    "summary": "create_run is the unguarded twin of create_run_guarded (docstring: '产品调用方全部走 create_run_guarded'); every remaining executable caller is a test.",
    "failure_scenario": "An innocent future product caller importing the shorter name reintroduces the c036 double-active-run race the guarded variant was built to prevent; two public entry points must both track every schema change."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/db.py",
    "line": 59,
    "summary": "Write-only columns: run.config_json and run.target_commit are inserted on every run and partition.baseline_generator_version set on every baseline, but no code in scripts/ or tests/ ever reads any of them.",
    "failure_scenario": "Dead schema state that reads as meaningful: baseline_generator_version looks like a migration/version gate but nothing checks it, so a future schema change that should be gated on it silently isn't; every INSERT/UPDATE carries parameters that exist only to be stored."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/engine.py",
    "line": 327,
    "summary": "cmd_next parses the full wiki_plan.yaml to build plan_pages_ before the --json fork, but _job_payload never consumes it — measured ~24ms per parse on a 180-page plan, paid by every `next --json` call in exactly the mode orchestrators drive (135+ wasted parses per run).",
    "failure_scenario": "A full run driven via `next --json` burns ~4-15s of pure redundant yamlite parsing (re-paid every INCREMENT run); _fmt_job already has the lazy fallback (:232-237), so moving the pre-parse into the non-json branch is behavior-preserving."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/engine.py",
    "line": 499,
    "summary": "cmd_submit unconditionally parses wiki_plan.yaml to resolve module titles even for jobs with no modules — all cards (12-60 per run) never carry a module, so the parse (~24-80ms each) is provably discarded.",
    "failure_scenario": "~1-2.5s of wasted parsing across a 30-card run; guard the load_plan call on job['modules']/job['module'] being non-empty (the surrounding except already tolerates a missing plan)."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/planner.py",
    "line": 800,
    "summary": "cmd_plan parses wiki_plan.yaml once for the style probe and then every branch (themes/seed/incremental/layers) re-parses the same file — one pure duplicate parse per invocation.",
    "failure_scenario": "Lowest-impact of the parse findings (interactive one-shot command, ~24-80ms), but a pure duplicate: reuse the probe's parse and pass it down."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/publish.py",
    "line": 283,
    "summary": "publish_run re-composes and atomic_writes (tmp + fsync + rename) every published page on every finish including unchanged kept pages — no comparison against existing content anywhere in the loop.",
    "failure_scenario": "A 3-page INCREMENT finish on a 180-page wiki issues ~180 fsync+rename pairs of identical bytes (~1-3s and SSD churn per finish, repeated on every no-change incremental run); comparing composed bytes to the existing file before writing costs one read instead."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/snapshot.py",
    "line": 84,
    "summary": "add_scope allocates fresh tmp/seen per call with no memoization across pages, so every page's source_files entry re-walks the same directory (O(pages × per-page dir size)) in snapshot_hashes, called by every finish and update.",
    "failure_scenario": "A module dir of ~500 files cited by 20 pages is os.walked 20 times with join_confined realpath + stat per file (~10k redundant file visits per invocation); a scope-string → file-set memo, as cmd_update's mod_files already does for modules, walks each distinct dir once."
  },
  {
    "file": "pwiki/scripts/pwiki_lib/snapshot.py",
    "line": 207,
    "summary": "cmd_update re-walks the tracked tree 2-3 extra times: mod_files re-expands every module scope that snapshot_hashes just expanded, and check_item's expand_dirs walks each unchanged item's source dirs twice more (srcs + old_srcs) — nothing is shared between the passes.",
    "failure_scenario": "On a 10k-file repo each `pwiki update` performs ~2-3 full directory traversals of the tracked tree (tens of thousands of redundant readdir/stat/realpath calls), seconds on cold cache — and update is the command users run repeatedly as the repo drifts."
  },
  {
    "file": "pwiki/SKILL.md",
    "line": 50,
    "summary": "The `export` command (cli.py:228 → cmd_export: db_to_file self-heal / file_to_db absorb of hand edits) and `begin --missing` (engine.py:107, the resume path) are implemented and wired but appear nowhere in SKILL.md's command contract (nor DEVELOPERS.md or references/).",
    "failure_scenario": "A publish failure leaves the tree half-written — the exact scenario cmd_finish's error message tells the user to fix — but an agent following SKILL.md top to bottom finds no documented way to re-render files from DB or absorb hand edits; the recovery surfaces are undiscoverable from the user contract."
  },
  {
    "file": "AGENTS.md",
    "line": 30,
    "summary": "The test-runner comment lists only sensitive-log-review / java-unit-test-generator / batch-unit-test-generator / code-review-p, omitting pwiki, while the parallel CLAUDE.md comment was updated in this same change to add it.",
    "failure_scenario": "A developer or agent following AGENTS.md after a shared change runs only the four listed suites; pwiki's 235-test suite is skipped and a pwiki regression ships unnoticed — the two governance docs give contradictory guidance about which skills have runnable suites."
  },
  {
    "file": "AGENTS.md",
    "line": 48,
    "summary": "'`sensors-analyze` has no suite' is now incomplete: claude-review, added to this diff's skill table, also has no tests/ directory.",
    "failure_scenario": "A contributor reads the seven-skill table plus the no-suite note, concludes every other listed skill has a runnable suite, runs `cd claude-review && python3 -m pytest tests/` and gets pytest 'file or directory not found: tests/'."
  }
]
```

---

## 回修记录(2026-11-09,五批全部落位,235 → 296 全绿,+61 回归,tests/test_review_fixes4.py)

### 批1 P0(#1-#12;另 #15/#25/#34/#44 提前落地)

- **#1** `modules --reset` 只重导注册表: 计划其余人工编辑面(pages/cards/aliases/limits/style)原样保留,仅无 plan 文件时才建骨架。
- **#2** 三道闸: ①`assign_paths` 尾部 `_die_on_path_collision`(所有计划写入路由统一拦截);②validate_plan 兄弟/卡片标题比对改按 `safe_filename` 归一后文件名;③publish_run 写循环前对行路径碰撞 die(EXIT_GATE)。
- **#3** `_move_published_file` 同路径守卫: 写新=写旧时不再 unlink(路径归一后同名的改名/module_sheet 改名不再自删)。
- **#4** 新增 `db.complete_job_guarded`: submit 终写收进单 BEGIN IMMEDIATE(核 run 状态再写 completed),与 finish 的 LINING 置位串行化 —— 迟到 submit 返回 `busy` 走孤儿稿,不再落进快照之后。
- **#5** `plan --layers commit` 走 merge_manual(同 id 保留手工字段+手编页保留),不再整树替换;与 themes 共用 `planner.MANUAL_FIELDS`。
- **#6** 根修 `expand_scope`: 目录判定看文件系统而非尾斜杠('src' 与 'src/' 同义),覆盖 add_scope/mod_files 全部调用方。
- **#7** validate_content 的缺页错误走与正常路径相同的入账(report_pages/errors_total),不再 early-return 吞掉。
- **#8** `plan --reset` 重建字典保留 limits/author_pages/author_notes。
- **#9** layers 提示词按风格分口径: deep 提示自带 <cite> 与 5-8 图;concise 维持禁顶部清单。
- **#10** `to_plan_pages` 按 parenthood 决定型: 带子篇的页绝不落 LEAF_TYPES(feature 父篇 → domain)。
- **#11** themes architecture 深文提示词从"至少两张图"改为硬带 5-8 张(与 mermaid 门禁一致)。
- **#12** finish LINING 与发布合成按每行盖章(job['style'] > run > plan > config,与 submit 同序);publish 合成循环逐行取 `p['style']`。
- **#15** `_fmt_job` 人类行补 required_sections 节单(schema/sections 在 submit 是静默的)。
- **#25** validate --content 三处路径消费(受检循环/内链 pass/--fix 合成)全部改当场派生(page_path_for/card_path_for),不再读未维护的 plan path 键。
- **#34** MANUAL_FIELDS 扩到 source_files/scope/objective/parent/ordinal/type/slug(themes 与 layers 共用)。
- **#44** cmd_write 风格取当前 plan(与 validate/update/export 同口径)并把行盖章刷新成生效值。

### 批2 P1(#16-#24)

- **#16** check_item 对账元组扩到 modules/objective/required_sections/pack_key(计划契约字段改动触发 catalog_changed)。
- **#17** `str(None)` 归一(`or ""`): 显式 `type:`/`title:` 空值不再引发永不收敛的 catalog_changed。
- **#18** validate 拒绝 parent 指向 module_sheet(qoder-plan 不再悬空 die)。
- **#19** WIDE_CITE_PACK_KEYS 补 `microservices`。
- **#20** normalize_links 裸文件名兜底只在全树唯一时启用;跨子树同名不改写(留给 links/internal 报错)。
- **#21** from-commit 的 git diff 加 `-c core.quotepath=off`(CJK 路径差异不再被八进制转义吞掉)。
- **#22** trim_sibling_overlap 审计记 id/title(不再 page: null)。
- **#23** validate_citations 只把真实存在的文件加入 cited 集(再生的 <cite> 块不再包含死路径)。
- **#24** cmd_delete 单页后重写全部索引+_write_generation_state;**并挖出更重的反转 bug**: `unpublish_pages` 入参是保留名单,单页删除曾误传 `[page_id]` = 删光其余全部 DB 行 —— 新增 `db.unpublish_page` 单行删除。

### 批3 P2(#26-#45;#52/#54 提前落地)

- **#26** finish 的 LINING 循环与发布/快照都进 try: 非 OSError 异常(gate 爆炸/锁竞争)落 DOCUMENT_FAILED+EXIT_ENV,不再裸奔卡 LINING。
- **#27** begin --force 拒绝 LINING 中的 run;finish 终态写入带 `WHERE state='LINING'` 复核,不复活已取消 run。
- **#28** cancel 先置 CANCELLED 再释放认领(与 regenerate/begin --force 同序,无 stranded processing)。
- **#29** kept-merge 的 ordinal 走 as_int(finish 路径无 validate,手编空值不再 int(None) 崩)。
- **#30** Ctx.load 对合法 JSON 但顶层非映射报可诊断警告并按默认配置运行。
- **#31** lease_minutes 走 as_int。
- **#32** layers commit 保存前过自家 validate_plan(超预算/超深不落盘)。
- **#33** layers init 对非空会话状态要显式 --reset;_load_layers 对损坏 JSON 显式 die(不再吞成无状态引导覆盖)。
- **#35**(plausible)省略 --claim 采纳行内令牌时必留 `claim_adopted` 审计行 —— 设计语义保留,误用可回溯。
- **#36** argparse 用法错误从 exit 2(撞 EXIT_GATE)归 3(cli.main 拦截);存量测试两条随契约更新。
- **#37** layers 前置条件(无状态/缺 --file/文件不在)统一 EXIT_PRECOND。
- **#38** plan --aliases 缺文件/坏 YAML/顶层非列表干净报错(3/2)。
- **#39** layers apply --file 的 open 容错 die(3)。
- **#40** _load_report 对缺失文件出声(stderr),且只有真读到的报告才记 report_file(不再记 phantom)。
- **#41** digest --extra 缺文件 die(3)。
- **#42** size/cited-files 上界与 page/words/citations 同走 strict+size_hard 升级(与 validation.md 一致)。
- **#43** --lenient 语义统一为"只放宽 H1/无引用两类"(submit/finish/write 三处同源 LENIENT_DROP_PREFIXES);全放必须 --force。存量测试两条按新契约更新(test_e2e deep 全周期改用 --force;coverage_gaps 坏稿 lenient 仍拒/force 放行)。
- **#45** PRAGMA busy_timeout=10000(与 connect(timeout=10.0) 同刻度)。
- **#52** _save_layers 原子写;**#54** digest 落盘原子写(随批3)。

### 批4 P2(#46-#54 余项)

- **#46** 序列条目续行吸收连同注释行一起收(_parse_block 跳过 text==""),条目中段 '#' 注释不再让余下字段掉出块。
- **#47** _parse_scalar 认 YAML 1.1 布尔 yes/no/on/off(发射侧本就按歧义词加引号,往返精确)。
- **#48** 行内列表拆分带括号深度;未配平显式 ValueError(不再撕成碎片标量)。
- **#49** 块标量去缩进锚定内容行(裸 '#' 行不锚定);逐行 cut min(strip_n, own) —— 浅注释不再重排整块。
- **#50** 顶层 root/ 目录改发 module id `root-dir`(不再与工作区根共享 'root')。
- **#51**(plausible) _source_hashes 改 util.json_list('null' 列兜底)。
- **#53** repowiki-metadata/_index×2/_module/generation-state 五处索引写全部 atomic_write(源级 lint 测试锁死)。

### 批5 P3(#55-#77)

- **#55**(plausible) Corpus.collect 记忆化键补 min/commands/readme。
- **#56**(plausible)BM25 每查询重建 —— **维持文档化取舍**(交互式使用 37-147ms 可接受;search 成为热路径时再持久化)。
- **#57** snapshot._as_list 与 gates 三处内联解码统一 util.json_list(_as_list 降为带历史注释的别名)。
- **#58** CJK 字符类单一来源 util.CJK_RE(search/qoderplan/publish 三处收敛;qoderplan 后续被并发工作重写,源级断言锁"不再自带类定义")。
- **#59** 页面提示词收进 i18n:`page_prompt(style × locale × kind)` 双语表,themes.prompt_for 薄封装;Builder.locale 全调用点透传(不再是存而不读的字段)。
- **#60** submit/write 的 14 行读稿块抽 util.read_submission(缺文件 3/坏 UTF-8 2,单一措辞)。
- **#61** report['check']['theme_declined'] 嵌套副本删除(只存顶层一份)。
- **#62** 13 语言 HAND_LANGS 单一定义(种子过滤与回填共用)。
- **#63** _ctx/_cli 收进 tests/conftest.py(五份拷贝收敛,源级 re-export 保持兼容)。
- **#64/#65/#66** set_file_hashes / extend_lease / glossary_set_definition 三个死 API 删除(测试随删;parallel-writing.md 的"submit 不顺延"契约与删除一致)。
- **#67** create_run 保留为测试直建通道(docstring 已声明产品走 guarded)。
- **#68** 只写不读死列(run.config_json / run.target_commit / partition.baseline_generator_version)退出 SCHEMA 与 INSERT/UPDATE(旧库多余列有默认值,不迁移也无碍)。
- **#69** cmd_next 的 plan 解析移进人类行(--json 路径零解析)。
- **#70** cmd_submit 仅带模块的 job 才解析计划(卡片整类跳过)。
- **#71** cmd_plan 探测解析一次,各分支复用。
- **#72** 发布先比后写(未变页面不再 fsync+rename;3 页增量不再全树重写)。
- **#73/#74** expand_scope 进程级 (repo, scope) 展开缓存(键归一尾斜杠),update/finish 的 3-5 遍重复 walk 收敛为 1 遍。
- **#75** SKILL.md 补 `begin --missing` 恢复跑与 `export --direction db_to_file|file_to_db` 自愈/吸收两个恢复面。
- **#76** 随并发提交 105d85f 已满足(AGENTS.md/CLAUDE.md 测试清单均已含 pwiki),本轮无需再改。
- **#77** 两份治理文档补注 claude-review 无 tests/ 目录。

### 回修中的额外发现

- **unpublish 保留名单反转**(#24 回修时实测发现,比原发现更重): 单页 delete 曾把其余全部发布行从 DB 删光(文件却在),一次 delete 后整树成"幻影索引+DB 缺行"。已修+回归锁定。
- **并发会话**: 回修期间另一会话在同仓库工作(提交 105d85f install.sh 三连;重写 pwiki qoderplan.py 的字节帽 lint)。pwiki 侧仅 qoderplan 受其影响,本轮 #58 断言改为源级约束避开;其间一次全套 6 败为瞬时干扰,复跑即绿。

### 验证

`cd pwiki && python3 -m pytest tests/` — **296 passed**(基线 235 + 61 回归,含 2 条存量测试随 lenient/退出码契约更新)。
