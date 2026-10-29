# Repository Guidelines

This repo is a collection of five **agent skills** (for Claude Code, Qwen, Qoder, OpenCode). There is no application — each top-level directory is a self-contained skill installed into *other* projects.

## Project Structure & Module Organization

| Skill | Purpose |
|---|---|
| `java-unit-test-generator/` | Generate JUnit5 + Mockito tests for one Java class until a JaCoCo coverage threshold is met |
| `batch-unit-test-generator/` | Same engine, batched over classes from a branch diff, via the `agents/batch-class-writer.md` subagent |
| `sensitive-log-review/` | Audit Java diffs for sensitive data leaking into logs; emits an HTML report and CI gate codes |
| `sensors-analyze/` | Inventory analytics tracking calls (埋点) into `sensors.csv` |
| `code-review-p/` | Docs-driven code review (diff/PR/path/whole repo): targeted brief → 12-angle sweep → adversarial verify → ranked findings |

Inside a skill: `SKILL.md` (triggers + workflow), `references/` and `protocol/` (rules, JSON schema), `scripts/` (Python drivers), `tests/` (pytest). `code-review-p` is the docs-driven exception: `SKILL.md` + `references/` + `agents/` + a Workflow-tool script (`workflows/deep-review.js`), no `scripts/`. Docs and comments are largely Chinese — keep them that way.

## Build, Test, and Development Commands

No root-level runner exists; run tests from inside a skill directory:

```bash
cd sensitive-log-review        # or java-unit-test-generator / batch-unit-test-generator / code-review-p
python3 -m pytest tests/                     # full suite
python3 -m pytest tests/test_cli.py          # one file
python3 -m pytest tests/test_cli.py::test_x  # one test
./install.sh <claude|qwen|qoder|opencode|all> [skill,skill] [project-dir]  # install
```

Scripts are Python 3 stdlib-only (pytest only for tests); each `tests/conftest.py` adds that skill's `scripts/` to `sys.path`. Entry points support `--help`.

## Coding Style & Naming Conventions

- 4-space indent; prefer `from __future__ import annotations` and `pathlib` over `os.path`.
- `snake_case` for functions, modules, files; `PascalCase` for classes. Tests are `test_<subject>.py`.
- Keep scripts dependency-free and cross-platform; put shared helpers in `scripts/common/`.
- No linter or formatter is configured — match surrounding code and existing docstring style.

## Testing Guidelines

Use pytest, matching existing frameworks and conventions. Add focused tests beside related ones, name cases `test_<behavior>`, and cover failure paths, exit codes, and protocol `next_step` blocks. `sensors-analyze` has no suite.

## Architecture & Agent Notes

- The unit-test skills drive scripts and an LLM through the **NEXT_STEP protocol**; exit codes: 0 = done, 1 = continue, 2 = script error, 3 = state/protocol error. On conflict: Schema > `references/UnitTestRules.md` > `SKILL.md` > judgment.
- The two unit-test skills each carry a copy of the `jaut` engine that **has diverged** — diff both and run both suites after shared changes.
- Only `src/test/java/**` may change; never production code, `pom.xml`, or a hand-edited `state.json`. Read `sensors-analyze/CLAUDE.md` before touching that skill.
- `code-review-p/workflows/deep-review.js` assumes the Workflow tool's script API (agent/parallel/pipeline/phase/log/budget) — check it against the workflow-authoring reference when editing; its suite (`tests/test_workflow_script.py`) needs node and skips without it.

## Commit & Pull Request Guidelines

History uses short imperative subjects. Prefer imperative, scoped messages, e.g. `sensitive-log-review: fix log-print false positive`. PRs should describe the change, list affected skills, note which suites you ran, link issues, and include sample CLI output or a screenshot for behavior changes. Keep `README.md` and `SKILL.md` in sync with script flags and exit codes.
