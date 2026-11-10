# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Repo Is

A collection of nine **agent skills** (Claude Code / qwen / qoder / opencode) installed into *other* projects. There is no application here — each top-level directory with a `SKILL.md` is a self-contained skill: prompt/workflow docs (`SKILL.md`, `references/`, `protocol/`), Python driver scripts (`scripts/`), and pytest suites (`tests/`). Docs and comments are largely in Chinese.

| Skill | Purpose |
|---|---|
| `java-unit-test-generator` | Iteratively generate JUnit5+Mockito tests for **one** Java class (optionally one method) until a JaCoCo line-coverage threshold is met |
| `batch-unit-test-generator` | Same engine, batched over **many** classes from a branch diff; delegates each class to the `batch-class-writer` subagent (`agents/batch-class-writer.md`) |
| `sensitive-log-review` | Audit Java diffs for sensitive data leaking into logs (banking/finance rules); produces an HTML report and CI gate exit codes |
| `sensors-analyze` | Inventory/verify analytics tracking calls (埋点) in arbitrary codebases → `sensors.csv`. See `sensors-analyze/CLAUDE.md` for its detailed constraints |
| `code-review` | Lightweight multi-angle diff review (working tree / branch / commit range / PR): candidate sweep → adversarial verify → gap sweep → ranked findings; quick/standard/deep + `--fix`. Prompt-only (no engine scripts) — the simple predecessor of `code-review-p` |
| `claude-review` | Multi-angle, evidence-verified code review of branch diffs / working-tree changes / PRs / paths; outputs a severity-ranked JSON defect list |
| `code-review-p` | Review any diff (working tree / branch / PR / path / whole repo) for real bugs: targeted brief → 12-angle sweep → adversarial verify → gap sweep → ranked findings. Protocol-driven (NEXT_STEP) since v2.0: single entry `scripts/crp.py` owns scope/ledger/dedup/verify-aggregation/accounting/report; quick/standard/deep modes; parallel fan-out (Workflow script journal ingest + agents + inline) |
| `pwiki` | Unified repo-wiki engine (supersedes wiki-plan-gen/qwiki/repowiki): plan→digest→generate→publish lifecycle with theme/layer/seed planners, per-style citation gates (concise/deep), parallel-safe job claiming, snapshot increments, BM25 search, Qoder-IDE plan emit/lint |
| `design-doc-writer` | Interview-driven requirement analysis → detailed design docs (功能概述/UI 交互/核心逻辑/接口/DB/缓存/非功能/TDD 用例, mermaid-gated, 不堆代码): question ledger + frontier 轮次提问 (grilling-style, 每题带推荐答案), Draft/Confirmed 状态机, ADR 三重门, KB-aware 事实调研 (codegraph/repowise/pwiki/openwiki, 无则降级读码), 命名/码值规范 (coding-standards 基线 + kb-scan 规范文档探测/`--standards` 登记), 断点续跑 via `scripts/dd.py` |

## Commands

```bash
# Install skills into a target project (interactive without args)
./install.sh <claude|qwen|qoder|opencode|all> [<skill>[,<skill>...]] [<project-dir>]
# → copies to <project>/.<tool>/skills/<name> (and agents/ → <project>/.<tool>/agents/,
#   asking before overwriting an existing agent file), strips tests/, __pycache__/,
#   .pytest_cache/, test.log, docs/, runtime-state dirs (.pwiki/, .repowise/, .claude/),
#   and workflows/ for non-claude targets;
#   strips the SKILL.md frontmatter `tools:` line for claude/opencode, and agents/*.md's
#   for opencode only (claude/qwen/qoder agents keep their tools line)

# Run tests — from inside a skill directory (there is no root-level runner)
cd java-unit-test-generator   # or batch-unit-test-generator / sensitive-log-review / sensors-analyze / code-review-p / pwiki / design-doc-writer
python3 -m pytest tests/                      # full suite
python3 -m pytest tests/test_cli.py           # one file
python3 -m pytest tests/test_cli.py::test_x   # one test
```

Scripts are Python stdlib-only (pytest is needed only for tests). Each `tests/conftest.py` puts that skill's `scripts/` on `sys.path`. `sensors-analyze`'s suite (37 regression tests: `tests/test_p0..p3_regressions.py`, `tests/test_artifact_dir.py`) covers its 2026-10-12 fix round; `code-review-p`'s suite includes the protocol-engine tests (scope ladder, ledger dedup parity vs the JS workflow script, routing matrix, e2e protocol driver) and gates its Workflow script through node (skipped when node is absent). `claude-review` has no tests/ directory.

Per-skill CLI entry points accept `--help` for full flags (e.g. `python scripts/main.py --help` in sensitive-log-review, `python scripts/select_worktree.py --help` in the unit-test skills).

## Architecture

### NEXT_STEP protocol (java/batch unit-test skills)

Scripts drive the workflow; the LLM only writes test code at designated steps and otherwise executes whatever the protocol says **verbatim**:

- Each script ends stdout with a `:::NEXT_STEP_BEGIN:::` / `:::NEXT_STEP_END:::` JSON block (`protocol/next-step.schema.json`). Exit codes: 0 = done, 1 = continue (normal flow signal, not failure), 2 = script error, 3 = state/protocol error.
- `next_step.type` is one of `run_script` / `write_code` / `ask_user` / `finish` / `abort`. On `write_code`, run the `on_complete` script from the protocol block afterwards.
- State lives in `<workdir>/state.json` (atomic writes); workdir is `<worktree>/.agent/<skill-name>/` (batch: per-class subdirs under `classes/`). Resume via `make_plan.py --workdir <workdir>`.
- Authority on conflict: **Schema > Rules (`references/UnitTestRules.md`) > SKILL.md > LLM judgment**.
- Typical loop: `select_worktree → init_coverage → make_plan → build_prompt → LLM write_code → validate_rules → verify_coverage` (batch wraps this with `batch_diff → batch_init → batch_next → batch_update → batch_finish`).
- Hard boundary: only `src/test/java/**` test files may be modified — never production code, `pom.xml`, or config. LLM must never hand-edit `state.json`.

### Shared `jaut` engine — duplicated and diverged

Both unit-test skills carry a copy of the engine at `scripts/jaut/` (cli, state machine, maven/surefire/jacoco integration, worktree, protocol, report…), plus near-identical test suites. **The two copies have diverged** — batch adds `lockutil.py`, the batch-mode layer (class-level budget `class_round_budget`/`class_round_bonus`/`class_budget_exhausted` on `State`, `skip_all_pending`, per-class `unmet` terminal state, method-group splitting), and its own variants of `config/decisions/maven/proc/...`; `references/UnitTestRules.md` also differs. Budget exhaustion no longer asks the user in either copy: it auto-skips the method/class and records `skip_reason` (`make_plan.py --unskip` is the only way to revive a skipped method). When changing shared logic, diff both copies and run both suites; do not assume a fix applies to one side only.

### sensitive-log-review

Pipeline of sub-scanners (`java_log_scanner`, `check_log_print`, `check_tostring_annotation`, `analyze_sensitive_fields`, …) orchestrated by `scripts/main.py`, with shared helpers in `scripts/common/` and rule/dictionary config in `scripts/rules/` + `scripts/dictionary/`. Exit-code contract for CI: 0 = pass, 1 = gate hit (`--fail-on` categories, default `violation,sensitive`), 2 = execution error. `--doctor` runs environment checks only. Full user docs live in its `README.md` and `SKILL.md`.

### sensors-analyze

LLM/Python split pipeline (LLM produces `sensors.json` + traces CSV rows; deterministic scripts extract entries, locate call sites, validate reports). Its scripts print a single `next_step:` line on stdout and logs to `--log`. Read `sensors-analyze/CLAUDE.md` before working on it — especially: never modify the target codebase, no dynamic placeholders in CSV values, and `entry.txt` needs a human `# CONFIRMED` marker.

### pwiki

Single CLI `scripts/pwiki.py` + `scripts/pwiki_lib/` (21 modules). Lifecycle: plan (`wiki_plan.yaml` v3, the sole human-edit surface) → `begin` queues knowledge-first jobs (module_sheet → card → pages bottom-up) → writers claim via `next` (single BEGIN IMMEDIATE + leases + claim tokens; token mismatch = orphan-save, never data loss) and `submit` through per-style gates (concise/deep presets in `styles.py`; submit warns, finish LINING hard-fails) → `finish` = LINING → publish → snapshot; `update` queues only stale items. Card model: plan cards carry `kind=<card_kind>` (e.g. `external_dependency`) while job/page rows carry `kind='card'` — card checks go through `config.is_card_kind()`, never a bare `==` compare. State lives in `<root>/wiki.db` (default root `<repo>/.pwiki`; root `.gitignore` covers `.pwiki` — never commit it). Run tests with `cd pwiki && python3 -m pytest tests/`.

### code-review-p

Protocol-driven review skill (v2.0): `scripts/crp.py` (single entry, `scripts/crp/` package) drives the run via the jaut-style NEXT_STEP protocol — scope ladder / overlay trust / candidate ledger / dedup / verdict aggregation / keep-thresholds / gap convergence / report rendering are all engine-owned; the LLM submits judgment work (`do_work` → `submit --kind …`). State lives outside the reviewed repo (XDG state home, `CRP_HOME` overridable); the ledger is the resume state (idempotent resubmission, drift asks). Three carriers with mandatory pre-recorded choice: `workflows/deep-review.js` (Workflow tool; results ingested from the native transcript `journal.jsonl` via `submit --kind carrier1`, compact-journal fallback) → `agents/review-finder|refuter.md` spawns → inline. Untrusted-overlay exclusions are suspended (A3); gate confirmation is one batched ask_user (A4). JS/Python dedup parity is locked by `tests/fixtures/dedup_cases.json` asserted in both suites.

### design-doc-writer

Interview-driven design-doc skill: single CLI `scripts/dd.py` (init/kb-scan/q-add/q-answer/frontier/phase/manifest/status/check/confirm/finish) under the sensors-analyze light protocol (stdout summary + single trailing `next_step:` line, `--log`, exit 0/1/2 with 1 = normal flow signal). Question ledger at `<repo>/.agent/design-doc-writer/ledger.json` is script-only (LLM never hand-edits); frontier = open questions whose depends_on are all answered (pure graph computation); docs go draft→confirmed only through `confirm --because` after the check gate (section manifest, mermaid type whitelist, unresolved/deferred-marker reconciliation, ADR three-way audit); design confirmation requires zero open/zero deferred. The target repo's code stays read-only (docs + `docs/adr/` + `.agent/` only); kb-scan prefers `.codegraph/`/`.repowise/`/`.pwiki/`/`.wiki/`/`graphify-out/` for fact-finding, falling back to direct code reading, and also auto-detects standards docs (filename matched against `STANDARDS_NAME_RE` in repo root + docs root) with `--standards <path>` manual registration — both recorded in `kb.standards_docs` (auto/manual, manual survives rescans; never affects `primary`). Naming/码值 compliance is write-time guidance (references/coding-standards.md baseline, 硬约束 9, target-repo conventions win), deliberately NOT a check-gate item.

## Conventions

- Every skill's `SKILL.md` frontmatter carries `name` + `description` (trigger text); `tools:` is a qoder/qwen field that `install.sh` strips for other targets.
- Skill READMEs / `SKILL.md` / `references/` are the user-facing contract — keep them in sync with script behavior (flags, exit codes, protocol fields).
