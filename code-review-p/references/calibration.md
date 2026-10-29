# Calibration — severity, confidence, and the never-flag list

## Severity

| Level | Name | Bar |
|-------|------|-----|
| **P0** | Critical | Data loss/corruption; security compromise on an untrusted path; crash of the main flow; broken build or public API. Stop-ship. |
| **P1** | High | Wrong behavior in a realistic scenario; silently wrong results; resource leak in a long-running process; regression of an existing guarantee. |
| **P2** | Medium | Wrong behavior in edge cases; measurable performance regression; confusing failure mode; risky new logic with no test. |
| **P3** | Low | Cleanup: reuse, simplification, efficiency, conventions. Real cost, no runtime failure. |

Correctness (P0–P2) always outranks cleanup (P3) when the output cap forces a
cut.

## Confidence

| Band | Meaning | Handling |
|------|---------|----------|
| 0.9–1.0 | Certain — quoted line + named trigger | Report as CONFIRMED |
| 0.7–0.9 | Likely — mechanism verified, trigger needs specific conditions | CONFIRMED with stated conditions, or PLAUSIBLE |
| 0.5–0.7 | Uncertain | PLAUSIBLE only; kept in deep mode, and in standard mode only for P0/P1 |
| < 0.5 | Speculative | Never report |

The confidence number ranks findings; it never substitutes for the failure
scenario. A 0.95 confidence finding without a trigger scenario is still not a
finding.

## Mode asymmetry

The mode sets which error you prefer, never the definition of a finding:

- **quick / standard = precision.** Your comment should be worth reading —
  like finding a $20 bill in your jeans after laundry. If unsure whether
  something is a problem, do not mention it. Silence beats noise.
- **deep = recall.** Catching real bugs matters more than avoiding false
  positives — a missed bug ships. Err on the side of surfacing; PLAUSIBLE
  findings are kept and marked.

## Never-flag list

Do not report, in any mode:

1. Style, formatting, or naming conventions (unless a quoted rule from a
   governing CLAUDE.md/AGENTS.md is violated — that is Angle K, cited).
2. Grammar or spelling in comments/strings.
3. "Consider doing X" suggestions that are not bugs.
4. Minor refactoring opportunities that don't improve correctness or design.
5. Code organization preferences with no functional consequence.
6. Missing documentation/comments unless the omission causes real
   misunderstanding.
7. "Best practices" that don't prevent an actual, nameable problem.
8. Anything you cannot name a failure scenario for.
9. Pre-existing bugs in untouched code — unless the diff re-exposes them
   (touched function), depends on them, or a finding's fix is blocked by
   them (then note the dependency, don't file the old bug). Whole-file
   review (explicit single-file target with no diff baseline) and
   whole-repo review (`repo`) are the exceptions: the whole file/repo is
   the scope, nothing is "pre-existing".
10. Theoretical race conditions without a concrete interleaving.
11. Performance issues with no measurable impact path.
12. Compliments, restatements of what the code does, or narration of the
    review process.

## Precedent library

False positives recur in patterns. Precedents encode the resolutions so they
are not re-litigated per review. Adapt and extend per repo.

1. **Framework-safe defaults.** React/Angular escape by default; ORMs
   parameterize; templating engines auto-escape. Not XSS/injection unless the
   diff uses the unsafe escape hatch (`dangerouslySetInnerHTML`,
   `bypassSecurityTrustHtml`, raw SQL string, `| safe`).
2. **Trusted inputs.** Environment variables, CLI flags, and server-side
   config are trusted. Any finding whose attack requires an
   attacker-controlled env var is invalid.
3. **UUIDs are unguessable** — no validation finding on them.
4. **Client-side code needs no authz.** The server validates; a missing
   permission check in browser code is not a finding.
5. **Internal boundaries are trusted.** Missing input validation on
   internal-only functions is not a finding; validation belongs at system
   boundaries (user input, network, files, IPC).
6. **Generated code is out of scope.** Findings in `dist/`, `build/`,
   generated protobufs, lockfiles are misdirected — trace to the source, or
   drop.
7. **Log content is not a correctness finding.** Unsanitized input in logs is
   not a bug in a correctness review (log injection/spoofing belongs to
   security review, and is usually excluded there too).
8. **Docs and markdown carry no correctness findings.**
9. **Tests-only diffs** are reviewed only by Angle H (weakened assertions,
   asymmetry, cannot-fail tests) — never for style.
10. **Cold-path inefficiency with bounded small N** is at most P3, usually
    cut. Hot path, startup, or unbounded N → report.
11. **Missing defensive checks for impossible internal states** — trust
    internal code; not a finding.
12. **A passing dev environment is not deployability.** Don't accept "works
    in dev" as closing a finding about build/deploy behavior — but also don't
    report deploy risk without a concrete divergence.
13. **Suppressions are not fixes.** If the diff silences a check (`as any`,
    `@ts-expect-error`, skipped test, lint-disable) rather than fixing the
    cause, the suppression itself is a finding — P2, or P1 if it hides a
    changed behavior.
14. **Flag-default flips.** A changed default (config, flag, locale, timeout)
    counts as a behavior change even when the diff touches nothing else —
    verify the new default for every environment that reads it.

## Clean verdict

When nothing survives, say so explicitly — vagueness is not humility:

```
No significant issues found in <scope>.
Residual risks: <what could not be checked — runtime behavior, untested paths>.
Testing gaps: <changed behavior without coverage>.
```

Never soften a real finding to fill space, and never invent findings to fill
space. An empty report with honest residuals is a complete review.
