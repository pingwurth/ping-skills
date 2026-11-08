# Repository Guidelines

This repo is a collection of nine **agent skills** (for Claude Code, Qwen, Qoder, OpenCode). There is no application — each top-level directory is a self-contained skill installed into *other* projects.

## Project Structure & Module Organization

| Skill | Purpose |
|---|---|
| `java-unit-test-generator/` | Generate JUnit5 + Mockito tests for one Java class until a JaCoCo coverage threshold is met |
| `batch-unit-test-generator/` | Same engine, batched over classes from a branch diff, via the `agents/batch-class-writer.md` subagent |
| `sensitive-log-review/` | Audit Java diffs for sensitive data leaking into logs; emits an HTML report and CI gate codes |
| `sensors-analyze/` | Inventory analytics tracking calls (埋点) into `sensors.csv` |
| `code-review/` | Lightweight multi-angle diff review (working tree / branch / range / PR): sweep → verify → gap sweep → ranked findings; prompt-only, no engine scripts — the simple predecessor of `code-review-p/` |
| `claude-review/` | Multi-angle, evidence-verified code review of branch diffs / working-tree changes / PRs / paths; severity-ranked JSON defect list |
| `code-review-p/` | Protocol-driven code review (diff/PR/path/whole repo): targeted brief → 12-angle sweep → adversarial verify → ranked findings; single-entry engine `scripts/crp.py` (NEXT_STEP protocol) owns scope/ledger/dedup/accounting/report |
| `pwiki/` | Unified repo-wiki engine (supersedes `wiki-plan-gen`/`qwiki`/`repowiki`, kept for reference): single CLI `scripts/pwiki.py` — theme-pack/layered/seed planners, per-style citation gates (concise/deep), parallel-safe job claiming, snapshot increments, BM25 search, Qoder-IDE plan emit/lint |
| `design-doc-writer/` | Interview-driven requirement analysis → detailed design docs (功能概述/UI/核心逻辑/接口/DB/缓存/非功能/TDD 用例, mermaid-gated): question ledger + frontier rounds via single CLI `scripts/dd.py`, Draft/Confirmed confirm gate, ADR triple gate, KB-aware fact-finding, resumable |

> `pwiki` 取代 `wiki-plan-gen`/`qwiki`/`repowiki` 三个 skill(主体功能覆盖;
> 少数旧闸门按决策降级为警告, 口径见 `pwiki/references/validation.md`)。
> 旧三 skill 的目录**不在本仓库**(历史版本只在开发者机器的 ~/Downloads 与
> ~/.claude、~/.agents/skills 安装位), 不要再给它们加功能; 安装位的
> repowiki 仍带重叠触发词, wiki 类请求可能路由到旧引擎 —— 注意甄别。

Inside a skill: `SKILL.md` (triggers + workflow), `references/` and `protocol/` (rules, JSON schema), `scripts/` (Python drivers), `tests/` (pytest). Docs and comments are largely Chinese — keep them that way (`code-review-p`'s `SKILL.md`/`references/` are English; its code comments and test docstrings are Chinese).

## Build, Test, and Development Commands

No root-level runner exists; run tests from inside a skill directory:

```bash
cd sensitive-log-review        # or java-unit-test-generator / batch-unit-test-generator / sensors-analyze / code-review-p / pwiki / design-doc-writer
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

Use pytest, matching existing frameworks and conventions. Add focused tests beside related ones, name cases `test_<behavior>`, and cover failure paths, exit codes, and protocol `next_step` blocks. `sensors-analyze`'s suite is its `tests/test_p0..p3_regressions.py` + `tests/test_artifact_dir.py` (37 tests). `sensitive-log-review`, `pwiki`, `code-review-p`, and `design-doc-writer` also carry suites; `claude-review` and the `code-review-p` subagent prompts have no tests/ directory.

## Architecture & Agent Notes

- The unit-test skills drive scripts and an LLM through the **NEXT_STEP protocol**; exit codes: 0 = done, 1 = continue, 2 = script error, 3 = state/protocol error. On conflict: Schema > `references/UnitTestRules.md` > `SKILL.md` > judgment.
- The two unit-test skills each carry a copy of the `jaut` engine that **has diverged** — diff both and run both suites after shared changes.
- Only `src/test/java/**` may change; never production code, `pom.xml`, or a hand-edited `state.json`. Read `sensors-analyze/CLAUDE.md` before touching that skill.
- `code-review-p/workflows/deep-review.js` assumes the Workflow tool's script API (agent/parallel/pipeline/phase/log/budget) — check it against the workflow-authoring reference when editing; its suite (`tests/test_workflow_script.py`) needs node and skips without it. Its dedup/aggregate semantics must stay in lockstep with `code-review-p/scripts/crp/ledger.py`/`verify.py` — parity is asserted over the shared fixtures `tests/fixtures/dedup_cases.json` in both suites; after changing either side, run both.
- `code-review-p`'s engine writes only to its run dir outside the reviewed repo (XDG state home; `CRP_HOME` overrides) — the reviewed repo stays strictly read-only, and `state.json` is never hand-edited.
- `design-doc-writer` keeps its question ledger at `<repo>/.agent/design-doc-writer/ledger.json` — script-only, never hand-edited; docs move draft→confirmed solely through `dd.py confirm --because` after the check gate; the target repo's code is read-only (docs, `docs/adr/`, `.agent/` are the only writes).

## Commit & Pull Request Guidelines

History uses short imperative subjects. Prefer imperative, scoped messages, e.g. `sensitive-log-review: fix log-print false positive`. PRs should describe the change, list affected skills, note which suites you ran, link issues, and include sample CLI output or a screenshot for behavior changes. Keep `README.md` and `SKILL.md` in sync with script flags and exit codes.
