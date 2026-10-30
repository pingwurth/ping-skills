---
name: code-review-p
description: >-
  Review a diff for real bugs — the working tree, a branch, a commit range, or a PR —
  and report ranked findings, each with a concrete failure scenario and evidence.
  Pipeline: multi-angle candidate sweep → adversarial verification → gap sweep →
  findings-first report. Modes: quick (precision), standard (default), deep (recall,
  subagent fan-out). --fix applies the findings. Use for "review my changes",
  "review this PR", or before committing nontrivial work.
argument-hint: "[branch | PR number | path | commit range] [--quick | --deep] [--fix]"
---

`quick → 1 pass, no verify → ≤4 · standard → 11 angles → dedup → verify → sweep → ≤10 · deep → parallel finders → adversarial verify → sweep loop → ≤20`

Review a diff the way a staff engineer would: enumerate candidates liberally,
kill them with quoted evidence, and report only what survives — ranked, each
with a concrete failure scenario.

Two rules govern everything below:

1. **Generation is liberal; judgment is strict.** During the sweep, surface
   every candidate you can name a failure scenario for, including ones you
   doubt. Filtering happens only in Phase 3. Severity gates applied at
   generation time measurably depress recall — the finder follows them
   literally and drops real bugs with the noise.
2. **No failure scenario, no finding.** Every reported issue states the
   concrete inputs/state that make the code misbehave. A defect you cannot
   trigger even in imagination is a hunch, and hunches are noise.

## Phase 0 — Establish scope

Probe first, then descend a ladder of diff sources. A rung that errors or
returns empty is not an answer — it means "keep descending". The first
NON-EMPTY rung becomes the base scope. Probe emptiness with `--stat` before
reading any full diff.

State probes (all read-only; in PowerShell, quote `@{...}` args —
`git diff '@{upstream}...HEAD'` — or the shell parses them as hashtables):

```bash
git status --porcelain                     # staged, unstaged, and untracked files
git symbolic-ref -q --short refs/remotes/origin/HEAD   # default branch, if set
git rev-parse -q --verify origin/main      # default-branch fallbacks, first hit wins
git rev-parse -q --verify origin/master
git rev-parse -q --verify main
git rev-parse -q --verify master
```

The ladder (first non-empty wins; never hard-code a branch name — resolve it):

1. **Explicit target argument** — branch, PR number, path, or commit range →
   review exactly that (`gh pr diff <N>`, `git diff <range>`).
2. **PR for the current branch** — `gh pr diff` succeeds → that is the scope,
   regardless of local commit or push state.
3. **Unpushed commits** — `git diff @{upstream}...HEAD` (works on any branch,
   including the default branch ahead of its upstream).
4. **Branch vs default branch** — `git diff <default>...HEAD`, `<default>`
   from the probes above (prefer the remote-tracking ref; a shallow clone may
   fail on merge-base — treat as "keep descending"). This rung is what saves
   a fully pushed feature branch, where `@{upstream}` is the same branch and
   its diff is empty.
5. **Working tree** — `git diff HEAD` (staged + unstaged). The scope when on
   the default branch with local edits.

**Accumulate working-tree changes.** If the base scope came from rung 2–4
and `git status --porcelain` shows staged/unstaged changes, append
`git diff HEAD` to the scope — reviews usually run pre-commit.

**Add untracked files.** `git diff` cannot see untracked files in any form.
Every `??` path in `git status --porcelain` within the scope is a changed
file: Read it and review it as a new file. A diff made only of untracked
files is not an empty diff.

Then `git log --oneline` over the range — commit messages state intent.

**Escape hatch.** If every rung errored or returned empty and there are no
untracked files, report exactly which commands you ran and what each
returned, and ask the user for the target. Never silently review nothing;
never reconstruct a file list from memory.

**The diff is ground truth.** The PR description and commit messages are
claims about it. Where claim and diff disagree, that mismatch is itself a
finding.

When an explicit target was passed (rung 1), note other worktree changes in
one line and review only the target — other agents or the user may be working
here concurrently.

## Phase 1 — Load context before judging

For each changed file:

- **Read the enclosing function/class of every hunk.** Bugs in unchanged
  lines of a touched function are in scope — the diff re-exposes them or
  fails to fix them. (Pre-existing issues in *untouched* code are not, except
  where a finding's fix would be blocked by them; see calibration.)
- **Find the governing convention files** — `CLAUDE.md` / `AGENTS.md` at the
  repo root and in every ancestor directory of a changed file. A conventions
  finding requires quoting the exact rule and the exact line that breaks it.
  No style preferences, no "spirit of the doc" inferences.
- **Learn how this codebase already does what the diff does** — Grep shared
  and adjacent modules. This feeds the Reuse angle and grounds consistency
  judgments: the standard is the repo's own established pattern, not generic
  best practice.

## Phase 2 — Candidate sweep (generate; do NOT filter)

Run every angle below. Full procedures, hunt lists, and per-language pitfall
tables: [references/angles.md](references/angles.md).

Each angle yields candidates shaped: `file`, `line`, one-sentence `summary`,
`failure_scenario` (inputs/state → wrong outcome), `confidence` (0–1),
`severity` guess (P0–P3), `category`.

- Pass through **every** candidate with a nameable failure scenario. Finders
  that silently drop half-believed candidates are the dominant cause of
  misses.
- Do not let one angle's conclusion suppress another's. The same line flagged
  twice for different reasons is two candidates — record both.

The angles:

| # | Angle | Looks for |
|---|-------|-----------|
| A | **Hunk scan** | Inverted conditions, off-by-one, null deref, missing `await`, falsy-zero, copy-paste wrong variable, swallowed errors, unescaped regex metachars — line by line, every hunk |
| B | **Removed-behavior audit** | Every deleted/replaced line enforced some invariant — find where the new code re-establishes it; if nowhere, that's a candidate. Highest signal rate of all angles |
| C | **Cross-file tracer** | Changed signatures/behavior vs their callers and callees: new preconditions, changed return shapes, new exceptions, ordering/timing, breaking API changes |
| D | **Language pitfalls** | The classic footguns of the diff's language/framework, flagged only where the diff introduces them |
| E | **Concurrency & state** | Races, check-then-act, idempotency under retry, partial failure of multi-step ops, lock scope, leaked goroutines/tasks |
| F | **Error paths & resources** | Error handling that swallows or misattributes; cleanup (fd/conn/tx/lock) missing on failure paths; missing timeouts; retry without backoff |
| G | **Security delta** | Only attack surface the diff *introduces*: tainted input reaching SQL/exec/HTML/paths, authz on new routes, secret handling. Full security audit is a different review |
| H | **Test audit** | New behavior without coverage; assertions weakened or tolerances bumped; tests asserting implementation; setup/teardown asymmetry; tests that cannot fail |
| I | **Cleanup trio** | Reuse (re-implementing an existing helper — name it), Simplification (derivable state, copy-paste, dead code), Efficiency (N+1, sequential independent I/O, hot-path blocking, closure-captured scope kept alive) |
| J | **Altitude** | Root cause vs bandaid: special cases layered on shared infrastructure mean the fix isn't deep enough — name the simpler, more general change |
| K | **Conventions** | Quotable violations of CLAUDE.md/AGENTS.md rules only |

Cleanup (I), altitude (J), and conventions (K) candidates state the concrete
cost (duplicated, wasted, harder to maintain, rule broken) in
`failure_scenario` instead of a crash.

## Phase 3 — Verification (judgment; now be strict)

**Dedup first.** Same defect + same location + same reason → keep the most
concrete variant. Dedup against **all** candidates ever seen, including ones
an angle discarded — a rejected finding in new wording is not a new finding.

For each surviving candidate, attempt to **refute** it:

- Re-read the actual code and quote it. Does the code say what the finding claims?
- Is the suspected bug guarded elsewhere — caller, wrapper, framework default, type system?
- Can you name the trigger inputs/state precisely? A mechanism that only fires under conditions you cannot specify is speculative.
- Where cheap, test the suspicion empirically: build, run the relevant test, write a 5-line repro. A repro outranks any amount of reading. Beware fake positives: a clean build is not evidence of correct behavior; a check built from the assumption under test proves nothing — the oracle must be independent of the code it judges.

Verdicts:

- **CONFIRMED** — you can quote the line and name inputs/state → wrong outcome.
- **PLAUSIBLE** — mechanism is real, trigger uncertain (timing, env, config). State what would confirm it.
- **REFUTED** — quote the line that proves the finding wrong. No verdict without a quote.

Keep per mode: **standard** keeps CONFIRMED (plus a PLAUSIBLE only when
severity is P0/P1); **deep** keeps CONFIRMED + PLAUSIBLE — recall mode, a
missed bug ships; **quick** has no verify pass.

**Deep mode with a subagent tool available:** run finders as one parallel
agent per angle, then one refuter per deduped candidate, pipelined so each
candidate verifies as soon as its finder returns (no barrier). For P0/P1
candidates use 2–3 refuters with distinct lenses (correctness, reproduction,
security) and keep only findings a majority cannot refute. Prompt templates
in [references/angles.md](references/angles.md). Without subagents, run the
same phases sequentially in this context.

## Phase 4 — Gap sweep

Take one more pass as a fresh reviewer holding the verified list. Look ONLY
for defects not already on it — do not re-derive or re-confirm. Target what
first passes systematically miss:

- moved/extracted code that dropped a guard, anchor, or lock
- second-tier footguns: defaults evaluated once at import time;
  non-deterministic iteration order; lock scope narrowed; predicate methods
  with side effects; config defaults flipped; timezone/locale assumptions
- test setup/teardown asymmetry; tests that can never fail
- the claim/diff mismatches noted in Phase 0

Surface up to 8 additional candidates through the same verification gate.
At deep effort, repeat sweep → verify until two consecutive rounds add
nothing new, always deduping against everything ever seen — otherwise the
loop does not converge.

## Phase 5 — Report

Rank: correctness outranks security delta outranks cleanup (I) outranks
altitude/conventions (J/K). Cap per mode (4/10/20); when the cap forces a
cut, cut from the bottom.

````markdown
## Review: <scope, one line>

| # | Location | Sev | Finding |
|---|----------|-----|---------|
| 1 | src/auth.ts:42 | P1 | Token compared before expiry parse — NaN passes |

### Details

**[P1] <title> — `path/file.ext:123`**
- **Problem:** one sentence.
- **Failure scenario:** concrete inputs/state → wrong output/crash.
- **Evidence:** how this was verified — quoted line, guard checked, test or
  repro run. Mandatory. "By inspection" is not evidence.
- **Fix:** described, not applied (unless --fix).
- **Confidence:** 0.9 · **Verdict:** CONFIRMED

### Open questions / assumptions
<what you could not determine from the code alone>

### Summary
<≤3 sentences, always LAST — findings lead, summary follows, never the reverse>
````

**No-findings case** — state it explicitly, never vaguely:

```
No significant issues found in <scope>.
Residual risks: <what could not be checked — runtime behavior, untested paths>.
Testing gaps: <behavior the diff changes without coverage>.
```

Never: praise the code, narrate what you looked at, pad with style opinions,
or report anything on the never-flag list
([references/calibration.md](references/calibration.md)). If the host
provides a structured findings tool, report through it once, ranked
most-severe first, and do not duplicate the list as text.

## --fix — Apply the findings

Only with the explicit flag — a reviewer is read-only by default:

1. Apply fixes in severity order. Scope each fix to its finding: no drive-by
   refactors, no touching unrelated code, no unrequested "improvements".
2. Never fix a failure by silencing the check — no deleting or skipping
   tests, no suppressing types/lint, no weakening assertions. Fix the cause;
   green comes as a consequence.
3. Re-run the checks that cover each fix before counting it fixed.
4. Re-report with a per-finding outcome: `fixed` / `skipped` (why) /
   `no_change_needed`.
5. Re-review the fix diff itself at quick effort before finishing.

## Calibration

Severity (P0–P3) definitions, confidence bands, the never-flag list, and the
false-positive precedent library: [references/calibration.md](references/calibration.md).
