# Finder angles — full catalog

Each angle is an independent lens. Run them all; do not let one angle's
conclusions suppress another's. Output shape for every candidate:

```json
{
  "file": "path/to/file.ext",
  "line": 123,
  "summary": "one-sentence statement of the defect",
  "failure_scenario": "concrete inputs/state → wrong output/crash",
  "confidence": 0.85,
  "severity": "P1",
  "category": "correctness"
}
```

`confidence` and `severity` are metadata for the verification and ranking
phases — they do **not** gate what you report. Report everything you can name
a failure scenario for. Cleanup angles (I), altitude (J), and conventions (K)
use `failure_scenario` for the concrete cost (duplicated / wasted / harder to
maintain / rule broken) instead of a crash.

---

## Angle A — Hunk scan

**Mission:** catch what is wrong on the face of the diff.

Read every hunk line by line, then read the enclosing function of each hunk.
For every line ask: *what input, state, timing, or platform makes this line
wrong?*

Hunt list: inverted or wrong condition; off-by-one; null/undefined deref
where adjacent lines show the value can be absent; missing `await` /
unpropagated async error; falsy-zero or empty-string checks where 0/"" is
valid; wrong-variable copy-paste; error swallowed in `catch` that should
propagate; unescaped regex metacharacters; assignment in condition; wrong
comparison operator; integer truncation or overflow; inclusive/exclusive
bound confusion; encoding assumptions.

## Angle B — Removed-behavior audit

**Mission:** catch what the diff silently stopped doing. Highest signal rate.

For every line the diff DELETES or replaces: name the invariant or behavior
it enforced, then search the new code for where that invariant is
re-established. If you cannot find it, that is a candidate.

Typical returns: removed guard/validation; dropped error path; deleted
`defer`/`finally`/dispose; removed lock or release; deleted test that covered
a real case; removed feature-flag or permission check; narrowed retry or
timeout.

## Angle C — Cross-file tracer

**Mission:** catch breakage at the seams.

For each function/type/API the diff changes: Grep for its callers and check
each call site — new precondition unmet, changed return shape, new exception,
new timing or ordering dependency, changed mutation of shared state. Then
check callees: does a parallel change in the same PR make this call unsafe?
For exported/public APIs, consider external consumers: is this a breaking
change?

## Angle D — Language pitfalls

**Mission:** catch the footguns the diff's own language ecosystem predicts.
Flag only instances the diff introduces.

**JavaScript / TypeScript** — falsy `0`/`''` checks; `==` coercion;
closure-captured loop variable; missing `await`; floating promises;
unhandled rejection; `parseInt` without radix; `NaN` comparisons (any `!==`
itself); default `Array.sort` lexicographic on numbers; mutating shared
state/props; `?.` chain that hides an error state; `JSON.parse` without
guard; prototype pollution via merge.

**Python** — mutable default arguments; late-binding closures in loops; `is`
vs `==`; bare `except:`; mutable class attributes shared across instances;
`str`/`bytes` confusion; mutating a dict while iterating it; shallow copy of
nested structures; missing context manager (`with`) for cleanup; `finally`
that swallows the in-flight exception.

**Go** — write to nil map; range-variable capture (pre-1.22); shadowed `err`
via `:=`; `defer` in a loop; goroutine leak (no exit path); slice aliasing —
`append` sharing backing arrays; nil interface vs typed-nil; `Mutex` copied
by value; `context` not propagated; `time.After` leak in loops.

**Rust** — `unwrap()`/`expect()` on fallible ops; blocking call inside async;
lock (Mutex/RefCell) held across `.await`; integer overflow wrapping in
release; `unsafe` without invariant comment; `.clone()` added to satisfy the
borrow checker hiding an ownership bug.

**Java / C#** — `==` on boxed types or strings; `equals`/`hashCode` pairs
broken by the change; resource leak (try-with-resources / `using` missing);
`ConcurrentModificationException`; autoboxing NPE; `BigDecimal.equals` vs
`compareTo`; `DateTime` without timezone; cancellation token ignored.

**SQL** — injection via string concatenation; N+1 per-row queries; query
shape the existing indexes don't serve; transaction scope too wide (locks
held across I/O); `NULL` semantics (`=` vs `IS`); implicit type coercion in
comparisons.

**Shell** — unquoted variable expansion; word splitting; missing `set -e` /
`pipefail`; `rm -rf "$VAR"` where `VAR` may be empty; subshell scoping losing
assignments.

**Generic** — timezone/DST drift; float equality; money as float; cache
invalidation missing when the underlying value changes; migration without
rollback; feature-flag default flipped for some environment.

## Angle E — Concurrency & state

**Mission:** catch what only breaks under interleaving or repetition.

Shared mutable state without synchronization; check-then-act races
(exists-then-create, get-then-set); idempotency — what happens when this
operation runs twice (retries, at-least-once delivery, double-click);
partial failure — a multi-step operation where step 2 or 3 fails: what state
is left behind, what is rolled back; lock scope narrowed or widened by the
diff; lock ordering changes that enable deadlock; leaked goroutines/tasks/
timers/subscriptions; session or connection state shared across requests.

Report a race only if you can sketch the concrete interleaving — theoretical
races are on the never-flag list.

## Angle F — Error paths & resources

**Mission:** catch what happens when things go wrong.

`catch` that swallows or logs-and-continues where the caller needs to know;
errors wrapped so the cause is lost; wrong error surfaced (returns error of
step 1 after step 2 failed); cleanup missing on the failure path — file
handles, connections, transactions, locks, temp files; transaction boundaries
that don't cover all writes of the operation; I/O without timeout; retry
without backoff or budget; process exit paths that skip flushing.

## Angle G — Security delta

**Mission:** catch attack surface the diff introduces. (A full security audit
is a separate review — scope to the delta.)

New input parsing without validation at the trust boundary; tainted input
flowing into SQL, shell, HTML, URLs, file paths (traversal), or
deserialization; new route/endpoint/permission check — who can reach it;
secrets in logs, error messages, or storage; auth logic changes that widen
access; CORS/redirect/origin changes.

Calibrate with the precedent library (calibration.md): framework-safe
defaults are not vulns; env vars and CLI flags are trusted inputs; report
only concrete, reachable issues.

## Angle H — Test audit

**Mission:** catch tests that no longer guard anything.

New behavior without a test; changed behavior with an unchanged test (the
test now asserts the wrong thing and still passes — trace it); assertions
weakened, tolerances bumped, cases skipped or `pytest.skip`/`t.Skip` added;
tests asserting implementation details instead of behavior (break on
refactor, miss regressions); setup/teardown asymmetry (state leaking between
tests); tests that cannot fail (assertion inside a callback that never runs,
subtest error not propagated, catch-all that masks failure); fixtures that
drifted from production shapes.

## Angle I — Cleanup trio

**Mission:** cut maintenance cost in the changed code. Correctness always
outranks cleanup.

**Reuse** — new code re-implementing something the codebase has: Grep shared
utility modules and files adjacent to the change; name the existing helper to
call instead.

**Simplification** — redundant or derivable state; copy-paste with slight
variation; deep nesting; dead code the diff leaves behind; speculative
generality (abstractions for one-time operations). Name the simpler form.

**Efficiency** — N+1 or repeated I/O; independent operations run sequentially
that could be concurrent; blocking work added to startup or hot paths;
long-lived objects built from closures or captured environments (they keep
the entire enclosing scope alive — a memory leak when that scope holds large
values); unbounded caches/logs. Name the cheaper alternative. No measurable
impact path → cut it (see calibration).

## Angle J — Altitude

**Mission:** catch fixes at the wrong depth.

Check each change: does it fix the root cause, or patch a symptom with a
fragile bandaid? Special cases layered on shared infrastructure are the tell
— the fix isn't deep enough. Prefer the simpler, more general change to the
underlying mechanism, and name it in the suggested fix. Also: logic
duplicated across call sites that belongs in one place; a condition
special-cased by callers that the callee should own; config that should be
data, hard-coded.

## Angle K — Conventions

**Mission:** enforce the repo's own written rules, nothing softer.

Find the governing files: `CLAUDE.md` / `AGENTS.md` at repo root, plus any in
ancestor directories of changed files (a directory's file governs only files
at or below it), plus user-level config where the harness exposes it. Flag a
violation only when you can quote the exact rule and the exact line that
breaks it, and cite the file path in the finding. No style preferences, no
vague "spirit of the doc" inferences. No governing file → this angle returns
nothing.

---

## Deep mode — subagent prompt templates

Run one finder per angle as a parallel subagent; pipeline each candidate into
a refuter subagent as soon as it arrives (no barrier waiting for all finders).
If subagents are unavailable, run the same prompts sequentially inline.

**Finder prompt (one per angle):**

```
You are one finder angle in a multi-angle code review of the repo at <cwd>.
Your angle: <angle charter — paste the angle section above>.
Scope: <the exact diff command> . Read enclosing functions; Grep callers and
adjacent modules as needed.
Surface up to 8 candidates in the JSON shape below. Do NOT filter for
confidence or severity — a separate verifier does that; your only job is
recall for this angle. If two candidates share a line but differ in
mechanism, report both.
Return JSON: [{file, line, summary, failure_scenario, confidence, severity,
category}]
```

**Refuter prompt (one per deduped candidate):**

```
Try to refute this code-review finding. Repo at <cwd>. Read the actual code
and quote the lines.
Finding: <file:line — summary — failure_scenario>
Check: (1) does the code say what the finding claims? (2) is it guarded
elsewhere — caller, wrapper, framework default, type system? (3) can you name
precise trigger inputs/state?
Return exactly one verdict with evidence:
- CONFIRMED — quoted line + trigger inputs → wrong outcome
- PLAUSIBLE — mechanism real, trigger uncertain; state what would confirm it
- REFUTED — quote the line that proves it wrong
No verdict without a quoted line.
```

For P0/P1 candidates, run 2–3 refuters with distinct lenses (correctness,
reproduction, security) and keep the finding only if a majority cannot
refute. A refuter that fails or returns garbage is not an affirmative vote.
