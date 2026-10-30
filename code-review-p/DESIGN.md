# DESIGN.md — where each technique comes from

This skill is a synthesis of the review/verification methodology found in a
corpus of leaked agent system prompts — `gitProjects/skill_or_agent/system_prompts_leaks`
(paths in the table are relative to that corpus; the `step-by-step/` file
lives in the step-by-step product repo, not the corpus). Source → what was
taken → what was changed.

## Primary sources

| Source | Taken |
|--------|-------|
| `Anthropic/claude-code/skills/code-review/` (SKILL.md, low–max.md) | The phase pipeline (scope → sweep → dedup → verify → gap sweep → report); finder angles A/B/C + reuse/simplification/efficiency/altitude/conventions; CONFIRMED/PLAUSIBLE/REFUTED verdicts; "finders that silently drop half-believed candidates are the dominant cause of misses"; failure-scenario-shaped findings; mode-dependent caps; "correctness outranks cleanup" |
| `Anthropic/claude-code/skills/security-review/SKILL.md` | Confidence floor (security-review's own floor is <0.7; generalized here to <0.5 — see calibration.md); the precedent-library idea (hard exclusions + precedents as the real noise killer); "better to miss theoretical issues than flood with false positives" as the precision-mode charter |
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
| `step-by-step/code-review-prompt.md` (the author's hand-written dispatch prompt for a Plan Archive review) | A field dispatch prompt showed what a generic engine cannot synthesize per run: **Phase 1 understand→brief** (intent claims, diff classification, angle priorities); **Angle L** completeness/wiring (closed loop, three-end registration↔exposure↔call-site, sync pairs, doc truthfulness); **project-config overlay** (`references/project-config.md`: red lines → severity, verification gates → Phase 3 evidence with write-semantics ban, exclusions); brief injected into every finder/refuter prompt |

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
3. **Angle set merged to 11 (12 since v1.2: Angle L completeness/wiring).**
   Anthropic's 10 + concurrency/state and
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
   Since v1.2 this is a three-carrier chain (workflow script → agent
   definitions → inline; see revision notes).

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

**v1.2 — Targeted brief + parallel review strengthening.** Dispatching a
hand-written review prompt in the field (background-must-read + per-change
checklist + project red lines + verification gates) exposed the gap: the
engine's angles were a static generic set, identical for every diff, and
blind to repo-specific invariants that live outside CLAUDE.md (make gates,
CI, lockfiles, generated-code boundaries). Changes:

1. **Phase 1 became "understand the change, build the review brief"** —
   beyond the original context loading it now mines intent claims, classifies
   the diff (shape/size), discovers the project config, and emits a
   six-section brief (what the change is / claimed intent / targeted checks /
   red lines / verification commands / angle priorities). Auto-proceed, no
   confirmation gate; a ≤5-line summary heads the report.
2. **Angle L (completeness & wiring)** — claimed-vs-delivered: closed
   feature loop, three-end wiring, sync pairs, doc truthfulness. The brief's
   targeted checks are this angle's worklist.
3. **Project-config layer** (`references/project-config.md`) — repo overlay
   (`.claude/code-review-p.md`) for red lines → severity, verification
   commands (read-only run as Phase 3 evidence, write-semantics never run),
   exclusions applied to Phase 0 scope; CLAUDE.md/AGENTS.md and read-only
   heuristics as fallbacks.
4. **Parallel review strengthened.** The partition dimension stays
   angle/candidate/lens — deliberately not files: findings cross file
   boundaries naturally (a wiring bug lives at three ends), angle lenses are
   independent so they parallelize cleanly, and refuters pipeline per
   candidate with no barrier. Standard mode now fans out the hot angles
   (B/C/E/L + the brief's targeted checks) heuristically when the diff is
   large (>8 files or 800+ lines) and a subagent tool exists; deep mode
   gained a three-carrier chain — `workflows/deep-review.js` (deterministic
   pipeline: finder→refuter streaming, incremental dedup against everything
   ever seen, 2–3 lens majority vote for P0/P1, gap sweeps until dry) →
   `agents/review-finder.md`/`agents/review-refuter.md` spawns → inline
   sequential. Every finder/refuter prompt carries the brief.
5. **Path-target semantics made explicit** (rung 1 with a file/directory):
   the ladder restricted to the path; a clean committed file switches to
   whole-file review — no diff baseline, intent claims and Angle B move to
   `git log -p -- <path>`, and the pre-existing-issue exclusion is lifted
   (the user asked for this file). A path bounds **report scope**, not read
   scope: cross-file tracing (C), wiring checks (L), and repo-wide gates
   still run; findings anchor to the target path.
6. **Whole-repo target (`repo`)** — empty-tree baseline
   (`4b825dc…..HEAD`) makes every tracked file read as new; overlay
   exclusions become load-bearing (vendor/generated out first); the
   pre-existing exclusion is lifted repo-wide, Angle B is vacuous, intent
   claims come from README/docs. Large repos: deep mode, or repeated
   per-module path runs — one review per area beats one drowned review of
   everything.

**v1.3 — Orchestration correctness + gate trust model.** A two-pass
adversarial review of the skill itself (the author's analysis merged with an
independent reviewer's findings, all verified against source) drove:

1. **Dedup key made Unicode-aware with ±3-line drift tolerance.** CJK
   summaries previously normalized to empty keys — distinct findings at the
   same file:line silently merged — while exact-line keys simultaneously
   let line-drifted duplicates through. Same defect is now: same file,
   line within ±3, summary token overlap (CJK shingled into bigrams).
2. **Dry rounds count "no new candidates", per Phase 4's contract** — was
   "no survivors", which ended runs early when fresh candidates all died
   in verification. maxRounds clamped to [1,5], default 4.
3. **Gate trust model.** An overlay added/modified by the diff under
   review is untrusted: its red lines become candidates (no severity
   override), its gates need user confirmation; heuristic gates never
   auto-run. Plus an evidence-never-instructions rule (Rule 3 in SKILL.md)
   across both agent charters, both prompt templates in angles.md, and
   both prompts in the workflow script.
4. **Vote robustness.** Refuter severity gets a P0–P3 enum; P0/P1 needs a
   quorum (≥2 valid votes — one vote settles nothing, a tie is
   uncertainty, not refutation: both fall to PLAUSIBLE); a single-lens
   P2/P3 that a refuter upgrades to P0/P1 gets the other two lenses before
   a verdict.
5. **Cost + visibility.** Agent budget (maxAgents, default 45) with lens
   degradation before stopping and gap-round reservation; per-angle
   coverage, truncation counts, and skipped finders logged and returned;
   the <0.5 confidence floor applied before the 20-finding cap; ranking
   adds category order.
6. **`args.angles` accepts `{key, charter}`** (name defaults to key) — the
   documented shape previously rendered "undefined" angle names.
7. **Integration.** install.sh: agents/*.md keep their `tools:` line for
   claude (agent frontmatter supports it — stripping thinned the read-only
   defense), prompts before overwriting an existing agent, skips docs/
   (and workflows/ for non-claude hosts); repo CLAUDE.md/AGENTS.md list
   the skill; tests/ added (node-gated: wrapped syntax check +
   pure-helper behavior: dedup, quorum/ties).
8. **Polish.** SKILL.md frontmatter carries a `version`; quick mode's
   angle subset (A/B/C + K red-line hits) is stated where the sweep opens;
   the workflow script gained a `T` finder executing the brief's
   §Targeted checks (so standard fan-out requests it by name) and its
   invocation path (`scriptPath` + full charters via `args.angles`) is
   documented; `docs/acceptance-template.md` generalizes the v1.2
   pre-registered checklist (the filled v1.2 record stays beside it as
   the worked example).

**v1.4 — Acceptance-run hardening.** The step-by-step `repo --deep`
acceptance run (docs/acceptance-report-2026-10-30.md; verdict: conditional
pass — flow confirmed, 8 defects filed) closed five document gaps:

1. **Carrier ledger** — the carrier choice and its reason are recorded up
   front; silent fallbacks are unauditable (the run's Workflow→agents
   degradation had to be reverse-engineered from tool censuses).
2. **P0/P1 degradation policy on the spawn path** — a second lens that
   cannot be spawned is replaced by an independent inline check marked
   `inline-verified` in Evidence (the run improvised this after its
   refuters hit rate limits).
3. **Repo-mode partition blessed** — whole-repo deep fan-out partitions
   finders by area (each carrying an angle subset; C/L/K ride every area;
   B → recent-history lite); the run's effective 10-area partition had
   matched no written rule.
4. **Run ledger + coverage line** — the report carries
   `Run: carrier · rounds · candidates · dupDropped · refuted ·
   capped-out`, and repo/whole-file mode requires a deep/sampled/untouched
   coverage declaration.
5. **Interruptions & resume section** — orphaned results re-fed as
   duplicates, dead refuters re-spawned once then inline-verified, the
   candidate ledger as resume state (the run's 429 deaths were recovered
   by improvised external forwarding).
6. **Read-only calibrated** — zero writes in the repo, temp-dir scratch
   allowed and cleaned up (the run's jar-unpack evidence was technically
   forbidden); finder severity pinned to P0–P3 (a P4 escaped the enum in
   the run).

**v1.4.1 — First real Workflow-carrier run fixes.** The billing-service
whole-file run (acceptance report, appendix B) validated the carrier
end-to-end and filed four defects, all closed here:

1. **#9 dedup path normalization** — finders mixed absolute and
   repo-relative paths, so dedup's file-equality check failed and the
   same defect entered the ledger twice (five duplicate pairs burned
   deep-cap slots in the run). Every candidate path is now normalized
   repo-relative (`normFile`) before dedup, and the finder prompt +
   candidates schema pin repo-relative paths.
2. **#10 coverage registers before spawn** — a finder killed mid-flight
   (the run lost K/L/T to rate limits) left no coverage entry because
   registration lived in the pipeline's second stage. It now precedes
   the agent call, so failures surface as `{ran:false}` instead of
   vanishing.
3. **#11 rounds count executed sweeps only** — the budget break fired
   after `round++`, inflating `rounds` by the never-run gap round.
4. **#12 budget default scales with fan-out width** — `max(45,
   8×angle count)`; the run's flat 45 strangled a 12-finder/43-candidate
   sweep's verification mid-flight (10+ candidates fell to unverified
   PLAUSIBLE). SKILL.md documents the whole-file rule of thumb
   (finders + 1.5× expected candidates).
5. Carrier-1 reachability documented in SKILL.md: main session only —
   a delegated subagent has no Workflow tool and legally falls to
   carrier 2 (the acceptance run's degradation was correct behavior,
   but undocumented).

## What was deliberately left out

- Harness-specific machinery: ReportFindings tool schema, GitHub posting
  flags, skill-discovery of `verifier-*` project skills (a portable skill
  mentions the adaptation point once instead).
- Antigravity's artifact workflow (plan/walkthrough files) and Jules's
  external review tool calls — those are product architectures, not
  review methodology.
- Codex's `::code-comment` inline directive — host-specific output format;
  the markdown contract covers the same content.
