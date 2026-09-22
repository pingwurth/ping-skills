# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Repo Is

A collection of four **agent skills** (Claude Code / qwen / qoder / opencode) installed into *other* projects. There is no application here — each top-level directory with a `SKILL.md` is a self-contained skill: prompt/workflow docs (`SKILL.md`, `references/`, `protocol/`), Python driver scripts (`scripts/`), and pytest suites (`tests/`). Docs and comments are largely in Chinese.

| Skill | Purpose |
|---|---|
| `java-unit-test-generator` | Iteratively generate JUnit5+Mockito tests for **one** Java class (optionally one method) until a JaCoCo line-coverage threshold is met |
| `batch-unit-test-generator` | Same engine, batched over **many** classes from a branch diff; delegates each class to the `batch-class-writer` subagent (`agents/batch-class-writer.md`) |
| `sensitive-log-review` | Audit Java diffs for sensitive data leaking into logs (banking/finance rules); produces an HTML report and CI gate exit codes |
| `sensors-analyze` | Inventory/verify analytics tracking calls (埋点) in arbitrary codebases → `sensors.csv`. See `sensors-analyze/CLAUDE.md` for its detailed constraints |

## Commands

```bash
# Install skills into a target project (interactive without args)
./install.sh <claude|qwen|qoder|opencode|all> [<skill>[,<skill>...]] [<project-dir>]
# → copies to <project>/.<tool>/skills/<name> (and agents/ → <project>/.<tool>/agents/),
#   strips tests/, __pycache__/, .pytest_cache/, test.log,
#   and strips the frontmatter `tools:` line for claude/opencode (kept for qoder/qwen)

# Run tests — from inside a skill directory (there is no root-level runner)
cd java-unit-test-generator   # or batch-unit-test-generator / sensitive-log-review
python3 -m pytest tests/                      # full suite
python3 -m pytest tests/test_cli.py           # one file
python3 -m pytest tests/test_cli.py::test_x   # one test
```

Scripts are Python stdlib-only (pytest is needed only for tests). Each `tests/conftest.py` puts that skill's `scripts/` on `sys.path`. `sensors-analyze` has no test suite.

Per-skill CLI entry points accept `--help` for full flags (e.g. `python scripts/main.py --help` in sensitive-log-review, `python scripts/select_worktree.py --help` in the unit-test skills).

## Architecture

### NEXT_STEP protocol (java/batch unit-test skills)

Scripts drive the workflow; the LLM only writes test code at designated steps and otherwise executes whatever the protocol says **verbatim**:

- Each script ends stdout with a `:::NEXT_STEP_BEGIN:::` / `:::NEXT_STEP_END:::` JSON block (`protocol/next-step.schema.json`). Exit codes: 0 = done, 1 = continue (normal flow signal, not failure), 2 = script error, 3 = state/protocol error.
- `next_step.type` is one of `run_script` / `write_code` / `ask_user` / `finish`. On `write_code`, run the `on_complete` script from the protocol block afterwards.
- State lives in `<workdir>/state.json` (atomic writes); workdir is `<worktree>/.agent/<skill-name>/` (batch: per-class subdirs under `classes/`). Resume via `make_plan.py --workdir <workdir>`.
- Authority on conflict: **Schema > Rules (`references/UnitTestRules.md`) > SKILL.md > LLM judgment**.
- Typical loop: `select_worktree → init_coverage → make_plan → build_prompt → LLM write_code → validate_rules → verify_coverage` (batch wraps this with `batch_diff → batch_init → batch_next → batch_update → batch_finish`).
- Hard boundary: only `src/test/java/**` test files may be modified — never production code, `pom.xml`, or config. LLM must never hand-edit `state.json`.

### Shared `jaut` engine — duplicated and diverged

Both unit-test skills carry a copy of the engine at `scripts/jaut/` (cli, state machine, maven/surefire/jacoco integration, worktree, protocol, report…), plus near-identical test suites. **The two copies have diverged** — batch adds `lockutil.py`, batch_mode auto-continue/escalation logic, and its own variants of `config/decisions/maven/proc/...`; `references/UnitTestRules.md` also differs. When changing shared logic, diff both copies and run both suites; do not assume a fix applies to one side only.

### sensitive-log-review

Pipeline of sub-scanners (`java_log_scanner`, `check_log_print`, `check_tostring_annotation`, `analyze_sensitive_fields`, …) orchestrated by `scripts/main.py`, with shared helpers in `scripts/common/` and rule/dictionary config in `scripts/rules/` + `scripts/dictionary/`. Exit-code contract for CI: 0 = pass, 1 = gate hit (`--fail-on` categories, default `violation,sensitive`), 2 = execution error. `--doctor` runs environment checks only. Full user docs live in its `README.md` and `SKILL.md`.

### sensors-analyze

LLM/Python split pipeline (LLM produces `sensors.json` + traces CSV rows; deterministic scripts extract entries, locate call sites, validate reports). Its scripts print a single `next_step:` line on stdout and logs to `--log`. Read `sensors-analyze/CLAUDE.md` before working on it — especially: never modify the target codebase, no dynamic placeholders in CSV values, and `entry.txt` needs a human `# CONFIRMED` marker.

## Conventions

- Every skill's `SKILL.md` frontmatter carries `name` + `description` (trigger text); `tools:` is a qoder/qwen field that `install.sh` strips for other targets.
- Skill READMEs / `SKILL.md` / `references/` are the user-facing contract — keep them in sync with script behavior (flags, exit codes, protocol fields).
