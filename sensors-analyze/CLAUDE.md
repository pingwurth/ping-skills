# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Repo Is

This is a **Claude Code skill** (`sensors-analyze`) for auditing analytics tracking implementations (埋点) in arbitrary codebases. It is NOT an application itself — it is a toolkit that gets applied to other projects to inventory and verify tracking calls (e.g., `sensors.track`, `gio.track`, `v-track`, `@TrackEvent`).

The skill targets six business fields: `page_name`, `first_biz_id`, `second_biz_id`, `first_biz_name`, `second_biz_name`, `event`.

## 5-Step Workflow

The skill follows a deterministic pipeline where LLM and Python each handle what they do best:

1. **LLM** reads the target codebase, identifies tracking paradigms, produces `sensors.json` (must validate against `sensors.schema.json`)
2. **Script** (`scripts/extract_entries.py`) extracts entry-function regex candidates → `entry.txt` for human review
3. **Script** (`scripts/locate_call_sites.py`) greps the codebase → `call_sites.json`
4. **LLM** traces each call site through the call chain to determine actual field values → appends to `sensors.csv`
5. **Script** (`scripts/check_report.py`) validates consistency → `check_report.txt` (PASS/FAIL)
5.5. **Script** (`scripts/review_report.py`) pre-reviews CSV, adds `illegal_reason` column → `review_report.txt`
5.6. **LLM** reviews rows with non-empty `illegal_reason`, verifies/corrects field values → updates `sensors.csv`

## Key Commands

```bash
# Validate sensors.json against schema (do this before step 2)
python3 -c "import json; d=json.load(open('sensors.json')); json.load(open('sensors.schema.json'))" && echo "OK"
# Or with jsonschema if installed:
python3 -c "import json,jsonschema; jsonschema.validate(json.load(open('sensors.json')),json.load(open('sensors.schema.json'))); print('schema OK')"

# Step 2: extract entry candidates (with optional audit)
python3 scripts/extract_entries.py --sensors sensors.json --out entry.txt
python3 scripts/extract_entries.py --sensors sensors.json --out entry.txt --audit --root <target-codebase>  # + cross-validation

# Step 3: locate all call sites (requires '# CONFIRMED' in entry.txt)
python3 scripts/locate_call_sites.py --entry entry.txt --root <target-codebase> --sensors sensors.json --out call_sites.json

# Step 5: validate results (outputs check_report.txt + resolution_report.csv)
python3 scripts/check_report.py --sites call_sites.json --csv sensors.csv --out check_report.txt

# Step 5.5: pre-review CSV, add illegal_reason column
python3 scripts/review_report.py --csv sensors.csv --sites call_sites.json --root <target-codebase> --out review_report.txt

# Step 6: archive CSV with timestamp and diff against previous version
python3 scripts/report_manager.py --csv sensors.csv
python3 scripts/report_manager.py --csv sensors.csv --diff-only  # compare only, no archive
```

**Script output**: all scripts write verbose logs to `--log` (default: `<out>.log`). Only a single `next_step: <instruction>` line is printed to stdout — follow it to decide what to do next.

Scripts use only stdlib (no third-party dependencies). `locate_call_sites.py` prefers `ripgrep` (rg) but falls back to pure Python grep.

## Schema & Output Format

- `sensors.schema.json` — JSON Schema (draft-07) for `sensors.json`. Defines paradigms, call chains, data flow annotations, entry functions, and SDK APIs.
- `sensors.csv` header (fixed, exact order): `page_name,first_biz_id,second_biz_id,first_biz_name,second_biz_name,event,file,illegal_reason`（`file` 格式为 `文件名:start,end`；`illegal_reason` 由 Step 5.5 脚本追加，多个原因用 `; ` 分隔）
- `call_sites.json` entries have: `id, file, start_line, end_line, location, patterns, snippet`

## Critical Constraints (from 问题.txt)

- **Never modify the target codebase** — this is a read-only analysis tool
- No third-party package dependencies (e.g., no `jsonschema` at runtime)
- **No dynamic placeholders in CSV** — every field must be a concrete value. When a field receives a variable (function parameter, prop, runtime context), trace back through all callers to determine the concrete value(s) passed at each call site. One CSV record per call-site × concrete-value combination. If genuinely unresolvable (runtime API, database, user input), leave the field empty — this produces a WARN in check_report, not a FAIL. Never output `<动态:...>`, `<变量:...>`, `<unknown>`, or similar placeholders. **Never modify the target codebase.**
- `entry.txt` requires human review AND a `# CONFIRMED` marker before `locate_call_sites.py` will execute — this is the only manual quality gate
- Step 4 CSV output format: `filename:start,end`
- Drift prevention: compare against previous analysis version and list diffs for human review
- `page_name`, `first_biz_id`, `second_biz_id`, `first_biz_name`, `second_biz_name` should be non-empty. Empty values produce WARN (not FAIL) in check_report and are listed in resolution_report.csv for human review
- `event` field must be recorded but may be empty
- CSV row count may exceed call_sites count (one call site with N different upstream callers producing N rows)

## Skill Trigger Logic

`SKILL.md` contains detailed trigger criteria and `trigger-eval.json` has test cases. The skill triggers on requests to audit/inventory/verify existing tracking implementations. It does NOT trigger on: developing tracking SDKs from scratch, Sentry error monitoring, A/B testing, IoT sensors, generic architecture analysis, or operating on already-collected CSV data.