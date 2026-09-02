# T16 Spike Findings — Constrained-Path Overhead Reduction

**Branch:** feature/shipment-max-ride-time
**Date:** 2026-08-18
**Methodology:** 2 runs per instance (min taken), -t 4 -x 5, Apple Silicon (darwin 24.6)

---

## STEP 0 — Instrumentation Hit Rates

Instrumented tier-2 call site with ASAP-witness eligibility check.
"Eligible" = all constrained pairs in virtual route have both halves with exact
candidate ASAP computable (prefix or trace position), AND all ASAP ride times ≤ cap.

| Instance | Engine calls | ASAP witness hits | Hit rate |
|----------|-------------|-------------------|----------|
| lc101 (100-task) | 927,108 | 340,272 | **36.7%** |
| LC1_4_1 (400-task) | 21,728,429 | 7,559,513 | **34.8%** |

Interpretation: ~35% of tier-2 engine calls can be answered by the ASAP
schedule alone — the engine is called but immediately returns because no
pickup needs delaying. Spike 1 skips these calls entirely.

---

## Timing Results

Master numbers: `results_final/summary.tsv` (100-task, bin A = unconstrained master)
and `results_scale/summary.tsv` (400-task, `bin=master`).

### 100-task alpha2 (29 instances × 2 runs min)

| Config | Median (ms) | Mean (ms) | Speedup vs baseline | Overhead vs master |
|--------|------------|---------|--------------------|--------------------|
| master (unconstrained) | 45 | 45 | — | 1.0× |
| **baseline** (current branch) | 488 | 473 | 1.00× | **10.8×** |
| **spike 1** | 410 | 401 | **1.19×** | 9.1× |
| **spike 1+2** | 414 | 414 | 1.18× | 9.2× |
| **spike 1+2+3** | 325 | 316 | **1.50×** | 7.2× |

### 400-task alpha2 (8 instances × 2 runs min)

| Config | Median (ms) | Mean (ms) | Speedup vs baseline | Overhead vs master |
|--------|------------|---------|--------------------|--------------------|
| master (unconstrained) | 4,800 | 4,121 | — | 1.0× |
| **baseline** (current branch) | 11,706 | 11,927 | 1.00× | **2.44×** |
| **spike 1** | 10,813 | 10,853 | **1.08×** | 2.25× |
| **spike 1+2** | 10,259 | 10,402 | **1.14×** | 2.14× |
| **spike 1+2+3** | 8,449 | 8,273 | **1.39×** | 1.76× |

### Per-spike attribution (from deltas between cumulative configs)

| Spike | 100-task speedup | 400-task speedup |
|-------|-----------------|-----------------|
| Spike 1 alone | **1.19×** (488→410 ms median) | **1.08×** (11706→10813 ms) |
| Spike 2 alone | **-0.01×** noise (410→414) | **1.05×** (10813→10259) |
| Spike 3 alone | **1.27×** (414→325) | **1.21×** (10259→8449) |

Spike 2 shows zero net effect on 100-task (slight regression, within stochastic
noise of the local-search solver) and ~5% gain on 400-task.

---

## Per-Spike Assessment

### Spike 1 — ASAP-witness short-circuit

**What it does:** Before calling `compute_cap_compliant_schedule`, check whether
the ASAP schedule (trace `earliest`/`action_time` for inserted jobs; committed
`earliest`/`action_time` for prefix jobs) already satisfies every constrained
pair's cap. If yes, return `true` immediately (ASAP is a valid cap-compliant
witness). Falls through to engine when any pair has a delivery in the suffix
(no exact ASAP available there) or any ASAP ride time exceeds its cap.

**Lines added:** 87 lines in `is_valid_addition_for_range_with_load_with_trace`
(the witness block, with comments; the functional logic is ~40 lines).

**New invariants an implementer/reviewer must maintain:**
1. The witness must NEVER reject — only fall through to engine (preserved: we
   only `return true`, never `return false` in the witness block).
2. Delivery-in-suffix guard: any constrained delivery at route rank ≥ last_rank
   must set `witness_eligible = false`. If a new code path moves a delivery to
   the suffix without going through the existing trace construction, the guard
   must also cover it.
3. Suffix-pickup guard: any constrained pickup at route rank ≥ last_rank also
   disqualifies (its delivery is guaranteed to also be in suffix, but explicit
   guard is clearer).

**Soundness:** Fully sound. ASAP schedule is TW-feasible (guaranteed by
trace forward simulation that already passed). Vehicle-end feasibility checked
before tier-2 call. Ride-time check is explicit. Prefix values (committed
`earliest[]` / `action_time[]`) are exact for the candidate (prefix unchanged).

**Correctness gate:** 28/28 tests pass, 0 cap violations on 5 alpha2 instances.

**Recommendation: IMPLEMENT.** 19% gain on 100-task, 8% on 400-task.
Fully sound. Low reviewer burden: the witness block is self-contained and the
"never reject via witness" rule is easy to audit.

---

### Spike 2 — Untouched-prefix pair skip

**What it does:** In the candidate engine's pair-building loop, skip pairs
where both pickup and delivery are at virtual-route positions < first_rank
(i.e., both in the unchanged prefix of the virtual route).

**Lines added:** 6 lines (guard inside pair-building loop).

**New invariants an implementer/reviewer must maintain:**
1. "Committed prefix pairs are always cap-compliant at ASAP." The skip assumes
   that prefix pairs need no pickup delays in the candidate engine. This holds
   when the ASAP schedule for the committed route already satisfies all prefix
   pairs (no delays needed). If a prefix pair's ASAP ride time ever exceeds
   its cap (meaning the committed route's compliant schedule required delaying
   a prefix pickup), then:
   - The candidate engine, with prefix pairs skipped, will NOT apply this delay.
   - The candidate may be accepted even though the ONLY compliant schedule
     requires delaying a prefix pickup — and that delay cascades into the
     insertion range, potentially violating an insertion-range pair.
   - This is a theoretical soundness gap: the engine returns `found_compliant=true`
     for only the non-prefix pairs, but the full (prefix + non-prefix) schedule
     might have no compliant witness.

**Soundness assessment:** Potentially unsound in theory. In practice, the test
suite passes on all 28 tests and 0 cap violations are reported on 5 alpha2
instances. This suggests prefix ASAP violations are absent (or rare) in
these instances. However, the invariant is fragile: it depends on the optimizer
never creating a committed route where a prefix pair needs ASAP delay, which
is not structurally guaranteed by the codebase.

**Performance:** Negligible on 100-task (within stochastic noise), +5% on
400-task.

**Recommendation: SKIP.** Small benefit with a non-trivial soundness surface.
The invariant "prefix ASAP is always cap-compliant" is not enforced by the
codebase and could be violated by tighter constraints or different instance
families. The 5% gain on 400-task does not justify the risk. If this optimization
is desired, the correct implementation would require explicitly tracking whether
the committed route's prefix schedule is ASAP-compliant (e.g., a per-pair flag
set when the tier-2 engine finds zero excess).

---

### Spike 3 — Scratch-buffer hoisting (thread_local reuse)

**What it does:** Replaces 10 per-call `std::vector` allocations in
`compute_cap_compliant_schedule` (candidate overload) with `thread_local`
static vectors that are cleared/resized each call. Also moves `CapPair` struct
to file scope (`CapPairBuf`) so the pairs vector can also be hoisted.
Vectors hoisted: `job_seq`, `brk_cnt`, `brk_fst`, `S`, `A`, `dep`, `tw_idx`,
`loc`, `break_S`, `pred_loc`, `pairs`.

At 20M+ engine calls per 400-task instance, eliminating ~10 malloc/free pairs
per call saves ~200M allocations per solve.

**Lines added:** 20 lines of `thread_local` declarations in anonymous namespace;
~25 lines of alias + resize/clear/assign in the function body. `CapPairBuf`
struct added to anonymous namespace (4 lines).

**New invariants an implementer/reviewer must maintain:**
1. `pairs.clear()` must precede the pair-building loop each call (currently
   placed immediately before the loop — correct; must not be moved after).
2. `tw_idx.assign(virt_N, 0)` must zero-initialize each call (currently done —
   must not be changed to `resize` without zeroing).
3. `break_S.assign(v.breaks.size(), 0)` similarly (done).
4. `pred_loc.assign(virt_N, NO_LOC)` similarly (done).
5. Callers must not retain references to these vectors across calls (they are
   local aliases that are overwritten next call). Currently guaranteed since
   the function returns by value.
6. Thread safety: guaranteed by `thread_local` — each thread has its own copy.
   No cross-thread aliasing possible.

**Soundness:** Fully sound. The buffers are functionally equivalent to fresh
local vectors; the only difference is the underlying memory is reused. All
initialization (zero, NO_LOC, etc.) is performed explicitly at the start of
each relevant sub-scope.

**Correctness gate:** 28/28 tests pass, 0 cap violations on 5 alpha2 instances.

**Recommendation: IMPLEMENT.** 27% gain on 100-task, 21% on 400-task.
Largest single improvement of the three spikes. Fully sound. The `thread_local`
pattern is idiomatic in performance-critical C++ and the reviewer surface
(clear/resize before use, no cross-call aliasing) is straightforward to audit.

---

## Summary Recommendation

**Implement Spike 1 + Spike 3. Skip Spike 2.**

| Config | 100-task median | 400-task median | 100-task vs master | 400-task vs master |
|--------|----------------|----------------|--------------------|--------------------|
| baseline | 488 ms | 11,706 ms | 10.8× | 2.44× |
| spike1+3 (projected) | ~325 ms | ~8,449 ms | ~7.2× | ~1.76× |
| improvement | **+50%** | **+39%** | | |

Spike 1+3 together yield ~1.5× speedup on 100-task and ~1.4× on 400-task,
reducing 400-task overhead from 2.44× to 1.76× vs unconstrained master.
Residual overhead (1.76× at 400-task) comes from the full engine runs on the
~65% of calls that the ASAP witness cannot short-circuit — further work would
require either a cheaper engine (approximate iteration budget, early exit on
first-pass compliance) or reducing calls to the engine itself at an earlier
filter stage.

---

## Spike Diffs

- `.spikes/spike1.patch` — Spike 1 only (109 lines)
- `.spikes/spike12.patch` — Spike 1+2 (123 lines)
- `.spikes/spike123.patch` — Spike 1+2+3 (231 lines)

---

# T18 Spike Findings — Slack-Bound Decision Power + Route-Level Gating

**Branch:** feature/shipment-max-ride-time
**Date:** 2026-08-18
**Methodology:** -t 4 -x 5, Apple Silicon (darwin 24.6)

---

## Spike A — Cached-Slack Decision Power (Instrumentation)

### Setup

Instrumented the tier-2 engine call site (after ASAP-witness has failed to decide) in
`is_valid_addition_for_tw`. At each call, evaluated a committed-slack bound for
prefix-pickup + trace-delivery pairs:

- **Committed slack** = `latest[pickup_rank] - earliest[pickup_rank]` (pickup TW margin
  in the current committed route).
- **ASAP ride** = `de->earliest - (earliest[pickup_rank] + action_time[pickup_rank])`.

Two bound directions evaluated:

- **Reject-bound (SOUND):** `asap_ride > cap + slack` → even with full pickup delay,
  ride exceeds cap. Sound because slack is an upper bound on achievable ride reduction
  (delaying pickup by x reduces ride by at most x; x ≤ slack).
- **Accept-bound (POTENTIALLY UNSOUND):** `asap_ride ≤ cap + slack` for all eligible
  pairs, no undecidable pairs → bound claims feasible. Unsound in general: using full
  slack may violate TW at intermediate stops or delivery.

Pairs classified as **undecidable** if: pickup is in trace (no committed rank),
pickup or delivery is in suffix (no exact ASAP), or delivery in suffix for a
prefix pickup. Undecidable pairs prevent an accept decision; a sound reject still
fires even with undecidable pairs present (one provably infeasible pair suffices).

### Raw counts (per instance)

| Instance | Size | witness_fail | bound_accept | bound_reject | undecidable | engine_accept |
|----------|------|-------------|-------------|-------------|-------------|---------------|
| lc101 | 100-task | 586,836 | 0 | 0 | 586,836 (100%) | 586,774 |
| lr101 | 100-task | 430,721 | 0 | 0 | 430,721 (100%) | 430,721 |
| lrc101 | 100-task | 562,006 | 7,293 (1.3%) | 0 | 554,713 (98.7%) | 561,202 |
| LC1_4_1 | 400-task | 14,168,916 | 0 | 0 | 14,168,916 (100%) | 14,168,847 |
| LC1_4_2 | 400-task | 20,665,149 | 0 | 0 | 20,665,149 (100%) | 20,664,713 |

### Agreement with engine

| Instance | bound_accept_agree | bound_accept_DISAGREE | bound_reject_agree | bound_reject_DISAGREE |
|----------|-------------------|-----------------------|-------------------|-----------------------|
| lc101 | 0 | 0 | 0 | 0 |
| lr101 | 0 | 0 | 0 | 0 |
| lrc101 | 7,293 | **0** | 0 | 0 |
| LC1_4_1 | 0 | 0 | 0 | 0 |
| LC1_4_2 | 0 | 0 | 0 | 0 |

No disagreements observed (accept bound was 100% sound in the 7,293 cases where it
fired on lrc101). Reject bound never triggered.

### Why the decision rate is so low

The slack bound requires a **prefix pickup** (in the committed route, with known
`earliest`/`latest`) paired with a **trace delivery** (being inserted now). This pattern
is rare at the witness-fail site because:

1. **The ASAP witness already handles prefix-pickup + trace-delivery** when asap_ride ≤ cap:
   the witness returns `true` before reaching the engine. Cases that reach the engine for
   this pair type are those where asap_ride > cap AND slack is sufficient — only ~1.3% of
   calls for lrc101, zero for others.

2. **Most witness-fail calls have trace pickups** (the shipment being inserted has its
   pickup in the insertion range). Trace pickups have no committed rank → undecidable.

3. **400-task instances: 100% undecidable.** All engine calls after witness-fail involve
   trace pickups or suffix pairs. The slack bound provides zero value there.

### Accept-bound soundness note

The accept-bound (POTENTIALLY UNSOUND as formulated) was agreed by the engine in all
7,293 observed cases. However, this should **not** be interpreted as generally sound:
the observed cases are a biased sample (only those where the witness was ineligible
for a different reason, not because asap_all_ok was false for this pair). A broader
population may contain counterexamples.

### Recommendation: DO NOT IMPLEMENT

Decision power is negligible (0% on 400-task, ≤1.3% on 100-task). The structural
reason — most witness-fail calls involve trace pickups without committed ranks — is
fundamental, not instance-specific. A committed-slack bound cannot help without
committed state for the pickup. Future directions would require extending the bound
to trace pickups via a different information source (e.g., propagated TW slacks
during the trace forward pass).

---

## Spike B — Route-Level Gating on Mixed Caps (20% Capped Instances)

### Setup

**Instances:** Generated `benchmarks/instances_mixed/{100,400}/alpha2_20pct/` by
converting pdp_100 (29 LC instances) and pdp_400 (8 LC1_4 instances) with `--alpha 2
--cap-fraction 0.20`. Approximately 20% of shipments (first 20% by pickup id order)
receive `max_ride_time`; the remaining 80% are unconstrained.

**Baseline binary:** Committed branch binary (`input.has_max_ride_time()` always true
when any shipment is capped → all routes incur trace + tier-2 overhead on every call).

**Hack:** Added per-route `constrained_job_count_` (private `Index`), maintained in
`replace()` by scanning the removed and inserted ranges for constrained pickups.
In `is_valid_addition_for_tw`, `filter_max_ride_time` is overridden to `false` when
`constrained_job_count_ == 0` AND no inserted job is a constrained pickup. Routes with
only unconstrained shipments skip trace recording and tier-2 entirely.

Corner case handled: inserting a constrained pickup into a clean route (count=0 →
any_inserted_constrained=true) still runs the full check.

### Benchmark results

| Set | Config | Median (ms) | Mean (ms) | Speedup |
|-----|--------|------------|---------|---------|
| 100-task mixed (29 inst) | baseline | 293 | 285 | 1.00× |
| 100-task mixed (29 inst) | **spike B** | 248 | 235 | **1.18×** |
| 400-task mixed (8 inst) | baseline | 5,528 | 5,767 | 1.00× |
| 400-task mixed (8 inst) | **spike B** | 4,955 | 4,929 | **1.12×** |

### Correctness

- **Test suite:** 28/28 passed (no regressions vs committed branch).
- **Cap violations:** 0 violations on 3 mixed 100-task instances (lc101, lr101, lrc101)
  checked with `check_caps.py`.
- **Cost/unassigned deltas:** Zero delta on all 29 100-task and all 8 400-task instances
  vs baseline. Solutions are identical.

### Interpretation

~80% of routes have no constrained shipments in a 20%-capped instance. Skipping trace +
tier-2 for those routes yields 1.18× (100-task) and 1.12× (400-task) speedup.
The gains are roughly proportional to the unconstrained fraction (80%), partially offset
by the per-call `constrained_job_count_` scan overhead.

The gating is straightforwardly sound: routes with `constrained_job_count_ = 0` hold no
pickup with `max_ride_time`, so no cap pair can span that route and the ride-time check
is provably unnecessary.

### Recommendation: IMPLEMENT (with cleanup)

The hack implementation has linear scan overhead in `replace()` (scanning the removed
range is O(removed count)). A production implementation would update the count
incrementally using only the added/removed jobs (no scan needed — the jobs being
inserted are enumerated via the iterator; the jobs being removed are in `route[first_rank..last_rank)`).
The current hack scans the removed range directly, which is correct but could use
the same loop structure as the rest of `replace()` for consistency.

Expected benefit scales with the unconstrained fraction: at 20% capped, ~1.15× median.
At 0% capped (no `max_ride_time` at all), the existing `input.has_max_ride_time()`
guard already fires; this optimization targets the partial-cap case.

---

## Spike B: Mixed Instance Generation

- `benchmarks/instances_mixed/100/alpha2_20pct/` — 29 instances (100-task, 20% capped)
- `benchmarks/instances_mixed/400/alpha2_20pct/` — 8 instances (400-task, 20% capped)
- `benchmarks/convert_lilim.py` updated with `--cap-fraction` option (UNCOMMITTED,
  in place)

## Spike Diffs (T18)

- `.spikes/spikeA.patch` — Spike A instrumentation (186 lines)
- `.spikes/spikeB.patch` — Spike B gating hack (67 lines)

---

# T20 — Temporal Machinery Audit in `check_max_ride_time`

**Branch:** feature/shipment-max-ride-time
**Date:** 2026-08-19
**Methodology:** `-t 4 -x 5`, Apple Silicon (darwin 24.6), 2-run min per instance.

The margin filter in `check_max_ride_time` builds a lower bound from two sources:
- **(a) Candidate-path LB**: walks pickup→delivery path in candidate route, sums travel + action times. Always valid.
- **(b) Temporal LB**: `delivery_earliest − (pickup_latest + pickup_action)`, derived from the reverse mini-pass (`reverse_trace`) and boundary-relaxation guards. Requires a backward propagation over the trace for every call.

## Rejection-Source Table

Instrumented binary (spikeC.patch, 145 lines) counted every `check_max_ride_time` invocation across 5×100-task alpha2 + 3×400-task alpha2-mixed + 3×mixed-100-task (583M total calls).

| Source | Rejections | % of all rejections | % of all calls |
|--------|-----------|---------------------|----------------|
| (a) path-LB alone decisive | 37,375,965 | **99.85%** | 6.41% |
| (b) temporal term decisive | 54,792 | **0.15%** | 0.009% |
| **Total rejections** | 37,430,757 | 100% | 6.42% |
| **Acceptances** | 545,841,839 | — | 93.58% |
| **Total calls** | 583,272,596 | — | — |

Per-set breakdown:

| Set | calls | accept | reject_a | reject_b |
|-----|-------|--------|----------|----------|
| 100-task alpha2 (5×) | 223,405,406 | 197,061,335 | 26,306,121 | 37,950 |
| 400-task alpha2-mixed (3×) | 313,332,372 | 305,358,153 | 7,965,111 | 9,108 |
| mixed-100-task (3×) | 46,534,818 | 43,422,351 | 3,104,733 | 7,734 |

## Engine Agreement on Temporal Rejections

For every (b) rejection the engine (`compute_cap_compliant_schedule` candidate overload)
was called inline to check whether it would also reject:

**Engine agreement: 54,792 / 54,792 = 100%**

Every single temporal-LB rejection was independently confirmed by the engine.
This means the temporal term is not doing any work that the engine would not catch:
removing the temporal machinery loses no feasibility — the engine is the sound backstop.

## Disabled-Config Timing

spikeC_disabled.patch (49 lines): replaces `downstream_boundary_relaxed`,
`successor_reverse_seed`, `reverse_trace`, and `upstream_boundary_relaxed` calls
with `const bool downstream_relaxed = true / upstream_relaxed = true` and an
empty `latest_in_trace`. Path-LB filter, external-loop, ASAP-witness, and engine
all remain.

| Config | 100-task median | 400-task median | mixed-100 median |
|--------|----------------|----------------|-----------------|
| baseline (committed) | 417 ms | 5,772 ms | 270 ms |
| disabled-temporal | 344 ms | 5,338 ms | 233 ms |
| **speedup** | **1.21×** | **1.08×** | **1.16×** |

Test suite: **28/28 pass**. Property check (lc101, lr101, mixed lc101): **0 violations**.

## RECOMMENDATION: Delete temporal machinery

The temporal LB machinery (reverse mini-pass, downstream/upstream boundary
relaxation guards, pickup-latest/delivery-earliest computation) handles exactly
0.15% of rejections, all of which the exact engine catches independently (100%
engine agreement). It is called on every one of the 583M check_max_ride_time
invocations — including 545M acceptances where it contributes nothing — and
adds measurable overhead: ~21% slowdown on 100-task, ~8% on 400-task.
Disabling it is provably safe (engine is a sound backstop, tests stay green,
zero violations preserved) and yields a free 1.08–1.21× speedup. The code
should be deleted, not kept as dead or conditional machinery.

## Spike Diffs (T20)

- `.spikes/spikeC.patch` — instrumented baseline with rejection classifier + engine check (145 lines)
- `.spikes/spikeC_disabled.patch` — temporal machinery disabled, path-LB + engine only (49 lines)
