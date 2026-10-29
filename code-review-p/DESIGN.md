# DESIGN.md — where each technique comes from

This skill is a synthesis of the review/verification methodology found in the
leaked prompts in this repository. Source → what was taken → what was changed.

## Primary sources

| Source | Taken |
|--------|-------|
| `Anthropic/claude-code/skills/code-review/` (SKILL.md, low–max.md) | The phase pipeline (scope → sweep → dedup → verify → gap sweep → report); finder angles A/B/C + reuse/simplification/efficiency/altitude/conventions; CONFIRMED/PLAUSIBLE/REFUTED verdicts; "finders that silently drop half-believed candidates are the dominant cause of misses"; failure-scenario-shaped findings; mode-dependent caps; "correctness outranks cleanup" |
| `Anthropic/claude-code/skills/security-review/SKILL.md` | Confidence floor (<0.7 don't report); the precedent-library idea (hard exclusions + precedents as the real noise killer); "better to miss theoretical issues than flood with false positives" as the precision-mode charter |
| `Anthropic/claude-code/skills/verify/SKILL.md` | "The diff is ground truth; descriptions are claims — a mismatch is a finding"; empirical verification with fake-positive guards; residual-risk reporting |
| `Microsoft/copilot-cli.md` (`code-review.agent.yaml`) | Precision charter ("$20 bill"), the never-flag deny-list, confidence-as-admission ("if unsure, don't mention it"), mandatory **Evidence** field per finding, read-only reviewer, fixed clean-verdict string, scope-from-state detection (staged → unstaged → branch diff; reworked into the five-rung ladder, see revision notes) |
| `OpenAI/Codex/*` (review stance paragraph, `codex-full.md`) | The 4 review categories (bugs / risks / behavioral regressions / missing tests), findings-first ordering with the summary last, explicit null-result with residual risks and testing gaps, P0–P3 severity convention, "verify in proportion to risk" |
| `Misc/amp-code.md` | Findings-first with file/line citations, explicit no-findings statement + residual risks, honest-reporting clauses (never manufacture green), suppression ban (→ calibration precedent 13) |
| `Anthropic/claude-code/skills/workflow-authoring/SKILL.md` + `Meta/muse-code/skills/workflow-authoring/SKILL.md` | Adversarial verify (skeptics prompted to refute; "a failed or invalid judge is not an affirmative vote"); perspective-diverse lenses for high-severity candidates; loop-until-dry (stop after 2 dry rounds); dedup against everything ever seen **including rejected findings** — otherwise the loop never converges |
| `Meta/muse-code/prompts/reminders/verify-reminder.md` + `muse-spark-1.3-muse-code.md` | Oracle independence — "a check built from the assumption under test proves nothing"; proxy evidence ≠ observation |
| `xAI/grok-build.md`, `xAI/grok-4.7-cli.md` | Fake-positive guards ("a clean build is not evidence"; dev vs deploy divergence); "claim only what tool output supports"; drive-by-refactor ban (→ `--fix` scoping rule); "never fix by silencing the check" |
| `Google/gemini-cli.md` | Empirical reproduction of suspected failures before reporting; convention conformance as a review axis |
| `Google/jules.md` | Plan review vs code review separation (reflected in the pointer to security-review for security deltas — different reviews for different objects) |
| `Anthropic/claude-code/skills/claude-api/shared/model-migration.md` (Anthropic's own harness research) | The load-bearing empirical finding: **severity filters at generation time depress measured recall — report everything with confidence and severity, filter in a separate pass.** This is Rule 1 in SKILL.md and the reason Phase 2 forbids filtering while Phase 3 is strict |

## Design decisions

1. **Three modes in one file** (quick / standard / deep) instead of five
   harness-selected effort files — portable across hosts, and the
   precision/recall tradeoff becomes an explicit, documented dial rather than
   an implicit one.
2. **Generation/judgment separation made a named rule** (SKILL.md Rule 1).
   The corpus shows two opposite failure modes: noise floods (unfiltered
   generation in a precision context) and silent misses (severity gates at
   generation time). The fix is the same for both: liberal generation,
   strict separate judgment, mode sets the keep-threshold.
3. **Angle set merged to 11.** Anthropic's 10 + concurrency/state and
   error/resources split out of the hunk scan (from Copilot's category
   allowlist and xAI's taxonomies), test audit as its own angle (Codex's
   "missing tests" category), security reduced to a *delta* lens (Jules's
   separation of review objects).
4. **Evidence field mandatory per finding** (Copilot) — this is what makes
   the verify phase auditable: a reader can check the quoted line instead of
   trusting the reviewer.
5. **Clean verdict is a contract, not an option** (Copilot/amp): an explicit
   no-findings statement with residual risks and testing gaps. Vague
   non-answers are the most common real-world review failure.
6. **Read-only by default, `--fix` opt-in** (Copilot reviewer), with the fix
   loop's anti-gaming rules from amp/grok/Kimi (no silencing checks, no
   drive-by refactors, outcome tracking fixed/skipped/no_change_needed —
   from the ReportFindings outcome enum and the batch skill's fix loop).
7. **Precedent library rewritten for general code review** — security-review's
   precedents are vulnerability-specific; the general set covers framework
   defaults, trust boundaries, generated code, cold-path perf, and
   suppression-as-fix.
8. **Subagent orchestration documented but optional** — deep mode uses the
   pipeline pattern (verify streams behind finders, no barrier) from
   workflow-authoring; without an Agent tool the same phases run inline.

## Revision notes

**v1.1 — Phase 0 scope-detection rework.** Field use showed the review
repeatedly reporting no changed files. Root causes, all in v1.0 Phase 0:

1. **Untracked files are invisible to every `git diff` form** — a diff made
   only of new, never-staged files reads as empty. The most common
   "review what I just wrote" case fell through every rung.
2. **"First match wins" had no fallthrough** — a rung that errored or
   returned empty dead-ended the search instead of descending. (The Anthropic
   original widened on an empty range diff; v1.0 regressed that.)
3. **`@{upstream}` is the same branch once work is pushed** — the standard
   write→push→review flow makes that rung's diff permanently empty.
4. **The fallback hard-coded `main`** — dead-ends on `master`/`develop` or
   repos with only remote-tracking refs.

Fix: state probes (status, default-branch resolution with remote-tracking
preference) → five-rung ladder with explicit empty/error fallthrough (explicit
target → `gh pr diff` → upstream diff → merge-base vs resolved default →
working tree) → accumulate working-tree changes onto range scopes → add
untracked `??` files from `git status --porcelain` explicitly → escape hatch
that reports what was run and asks the user, instead of silently reviewing
nothing.

## What was deliberately left out

- Harness-specific machinery: ReportFindings tool schema, GitHub posting
  flags, skill-discovery of `verifier-*` project skills (a portable skill
  mentions the adaptation point once instead).
- Antigravity's artifact workflow (plan/walkthrough files) and Jules's
  external review tool calls — those are product architectures, not
  review methodology.
- Codex's `::code-comment` inline directive — host-specific output format;
  the markdown contract covers the same content.
