# Implementation Plan: `target_time` — Requested-Time Coercion (Phase 1)

## Context

Issue [#1330](https://github.com/VROOM-Project/vroom/issues/1330) proposes native support for soft requested service times in passenger-style PDPTW problems. Today, VROOM's time windows are hard constraints and waiting time isn't part of the objective, producing feasible but operationally undesirable results (e.g., edge-of-window scheduling, unnecessary onboard dwell).

The user currently implements a three-phase wrapper externally. This plan covers **Phase 1 only** (Requested-Time Coercion) — biasing service times toward a `target_time` — while leaving clear extension points for Phase 2 (Slack Collapse via `anchor`).

## Architecture Decision

**Approach: ETA-layer coercion via `choose_ETA` in both solve and PLAN modes**

Add `target_time` as a field on `Job`. Extend the `choose_ETA` GLPK MIP's pass 2 objective with a weighted target-deviation term that biases ETAs toward `target_time` values without adding a third solve pass. In the normal **solve path**, when `_has_target_time` is true, route the solution through `choose_ETA` (instead of the simple `format_solution` forward pass) so that target_time optimization happens in a single API call — no external multi-phase workflow needed.

**How it works in each mode:**
- **Solve mode (`Input::solve()`):** Normal VRP solve produces route assignments → convert routes to `VehicleSteps` (preserving order) → call `choose_ETA` per route (only for routes containing target_time jobs) → MIP optimizes ETAs toward target_time. Single API call, no re-solve.
- **PLAN mode (`Input::check()`):** Already calls `choose_ETA` per route. Just needs the weighted target-deviation term in pass 2.

**Why this approach:**
- Single API call — no external multi-phase workflow
- Avoids the `const` field mutation problem (we read `target_time`, never mutate `tws`)
- Leverages the existing MIP infrastructure — target deviation is a weighted term in pass 2, no third pass needed
- Target alignment is a best-effort optimization, not a violation — deviations are reported as informational `target_lead` / `target_delay` fields on steps, not as violations
- Phase 2's `anchor` can later add another constraint/objective term in the same MIP
- Requires `libglpk` (same as PLAN mode — already a project dependency)

---

## Step-by-step Implementation

### Step 1: Add `target_time` to `Job`

**Files:** `src/structures/vroom/job.h`, `src/structures/vroom/job.cpp`

Add field after `tws`:
```cpp
const std::optional<Duration> target_time;
```

Update both constructors (SINGLE and PICKUP/DELIVERY) to accept `std::optional<UserDuration> target_time = std::nullopt` as the **last parameter** (after `service_per_type`, which already has a default). Initialize in the member initializer list with explicit scaling:

```cpp
target_time(target_time.has_value()
              ? std::optional(utils::scale_from_user_duration(target_time.value()))
              : std::nullopt)
```

Note: the parameter is `std::optional<UserDuration>` (user units) while the field is `std::optional<Duration>` (internal scaled units). The scaling is performed during construction, matching the pattern used for `default_setup` / `default_service`.

### Step 2: Parse `target_time` from JSON

**File:** `src/utils/input_parser.cpp`

Reuse the existing `get_value_for<UserDuration>` template pattern (already used for `max_tasks`/`max_travel_time` at ~line 474):

Update `get_job()` (~line 514): Append `get_value_for<UserDuration>(json_job, "target_time")` as the last argument to the Job constructor call.

Update shipment parsing (~line 625 for pickup, ~line 646 for delivery): Append `get_value_for<UserDuration>(json_pickup, "target_time")` and `get_value_for<UserDuration>(json_delivery, "target_time")` as the last argument to their respective Job constructor calls.

### Step 3: Add shipment validation and `_has_target_time` flag

**File:** `src/structures/vroom/input/input.h`

Add private member alongside `_has_TW`, `_has_skills`, etc. (~line 48):
```cpp
bool _has_target_time{false};
```
Add public accessor alongside `has_skills()`, `has_jobs()`, etc.:
```cpp
bool has_target_time() const;
```

**File:** `src/structures/vroom/input/input.cpp`

Add `#include <iostream>` to this file's includes (for `std::cerr` in the warning below).

In `Input::add_job()`: Set `_has_target_time` if job has `target_time`. If `target_time` is set but falls outside all of the job's time windows, log an informational warning:
```cpp
if (job.target_time.has_value()) {
  _has_target_time = true;
  // Force timing-aware solver (VRPTW) so route construction considers
  // timing constraints. Without this, CVRP mode ignores timing entirely
  // and the MIP can only adjust ETAs within a fixed (possibly poor)
  // route order.
  _has_TW = true;
  const auto tt = job.target_time.value();
  if (!std::ranges::any_of(job.tws, [&](const auto& tw) {
        return tw.start <= tt && tt <= tw.end;
      })) {
    // VROOM has no logging framework; use stderr for warnings.
    std::cerr << "[warning] target_time " << utils::scale_to_user_duration(tt)
              << " for job " << job.id
              << " falls outside all time windows; exact alignment is impossible."
              << std::endl;
  }
}
```

In `Input::add_shipment()`: Set `_has_target_time` if either side has `target_time`. Apply the same unreachable-target warning to each side independently. Both pickup and delivery may have `target_time` — the MIP handles them as independent deviation variables.

### Step 4: Modify `choose_ETA` MIP — the core change

**File:** `src/algorithms/validation/choose_ETA.cpp`

#### 4a. Count target_time jobs during initial loop

During the step iteration (~line 103), when processing `STEP_TYPE::JOB` cases, check if the job has `target_time` set. Track their MIP column indices so we can wire them into constraints later.

```cpp
unsigned nb_target_time = 0;
std::vector<Index> target_time_job_ranks;   // job rank in input.jobs (used as key in output)
std::vector<Duration> target_time_values;   // scaled target values
std::vector<unsigned> target_time_t_cols;   // MIP column for t_i

// Track job ranks whose earliness should be zeroed in pass 2:
// target_time jobs themselves + their shipment counterparts.
std::unordered_set<Index> target_linked_job_ranks;
```

In the `STEP_TYPE::JOB` case (after existing processing at ~line 118), add:
```cpp
if (job.target_time.has_value()) {
  target_time_job_ranks.push_back(step.rank);
  target_time_values.push_back(job.target_time.value());
  target_time_t_cols.push_back(current_index + 1);  // MIP cols are 1-indexed, col 1 = t_0
  ++nb_target_time;

  // Mark this job and its shipment counterpart (if any) as target-linked.
  // VROOM convention: pickup at rank r has delivery at rank r + 1.
  target_linked_job_ranks.insert(step.rank);
  if (job.type == JOB_TYPE::PICKUP) {
    target_linked_job_ranks.insert(step.rank + 1);  // delivery counterpart
  } else if (job.type == JOB_TYPE::DELIVERY) {
    target_linked_job_ranks.insert(step.rank - 1);  // pickup counterpart
  }
  // JOB_TYPE::SINGLE has no counterpart — only itself is linked.
}
```

#### 4b. Add MIP variables for target deviation

For each job with `target_time`, add two non-negative variables:
- `target_early_i` — how much earlier than target (positive = service before target)
- `target_late_i` — how much later than target (positive = service after target)

Update column/variable counts explicitly. The existing layout is:

| Columns | Content |
|---------|---------|
| `1` to `n+2` | `t_0` through `t_{n+1}` (service times) |
| `n+3` to `2n+4` | `Y_0` through `Y_{n+1}` (violation vars) |
| `start_X_col` to `start_X_col+K-1` | Binary TW choice vars |
| `start_delta_col` to `start_delta_col+n` | Delta vars (n+1 total) |
| **`start_target_col`** to **`start_target_col+2*nb_target_time-1`** | **New: target deviation vars** |

```cpp
const unsigned start_target_col = start_delta_col + n + 1;  // after last delta var
const unsigned nb_target_vars = 2 * nb_target_time;
const unsigned nb_var = start_target_col + nb_target_vars - 1;  // was: start_delta_col + n
```

When `nb_target_time == 0`, `nb_target_vars == 0` and `nb_var == start_delta_col + n`, preserving the existing value.

Update `nb_constraints`:
```cpp
const unsigned nb_constraints = 4 * n + 3 + nb_delta_constraints + 2 + nb_target_time;
```

**Existing asserts that must be updated** (because `nb_constraints` and `nb_var` now include target terms):
- Line 690: `assert(current_row == nb_constraints)` → `assert(current_row == nb_constraints - nb_target_time)` (target rows not yet added)
- Line 964: `assert(constraint_rank == nb_constraints)` → `assert(constraint_rank == nb_constraints - nb_target_time)` (target matrix entries not yet written)
- Line 974: `assert(r == nb_non_zero + 1)` — **remove** this assert; it is replaced by the new `assert(r == nb_non_zero + 1)` after the target matrix entries block (Step 4c)
- Line 762: `assert(current_col == nb_var + 1)` — this assert is updated in Step 4b's variable definition section to fire after target variable columns are defined
- Line 1012: `assert(current_delta_rank == nb_var + 1)` → `assert(current_delta_rank == start_target_col)` (delta iteration ends before target vars)

Update `nb_non_zero` (3 non-zero entries per target constraint: `t_i`, `target_early_i`, `target_late_i`):
```cpp
const unsigned nb_non_zero =
  2 * (3 * n + 3) + 3 * K + 2 * n + 2 - default_job_tw + 2 + n + 2 + 3 * nb_target_time;
```

After defining delta variables (~line 761), add the target deviation variables:
```cpp
// Target deviation variables.
for (unsigned i = 0; i < nb_target_time; ++i) {
  auto early_name = std::format("te{}", i);
  glp_set_col_name(lp, current_col, early_name.c_str());
  glp_set_col_bnds(lp, current_col, GLP_LO, 0.0, 0.0);
  ++current_col;

  auto late_name = std::format("tl{}", i);
  glp_set_col_name(lp, current_col, late_name.c_str());
  glp_set_col_bnds(lp, current_col, GLP_LO, 0.0, 0.0);
  ++current_col;
}
assert(current_col == nb_var + 1);  // existing assert, now accounts for target vars
```

#### 4c. Add constraints linking t_i to target deviation

For each target_time job (indexed `i` in `0..nb_target_time-1`), with MIP column `target_time_t_cols[i]` for `t_i`:

```
t_i + target_early_i - target_late_i = target_time_values[i] - horizon_start
```

**Important:** Store Makespan and Sigma_Y row indices as named constants when they are created, since `nb_constraints - 1` and `nb_constraints` no longer point to them when `nb_target_time > 0`:
```cpp
// At ~line 686, when Makespan row is set up:
const unsigned makespan_row = current_row;
// At ~line 692, when Sigma_Y row is set up:
const unsigned sigma_y_row = current_row;
```
Then update pass 2 pinning (~lines 1029, 1038) to use these constants instead of `nb_constraints - 1` / `nb_constraints`:
```cpp
glp_set_row_bnds(lp, makespan_row, GLP_FX, best_makespan, best_makespan);
// ...
glp_set_row_bnds(lp, sigma_y_row, GLP_FX, sum_y_i, sum_y_i);
```

This is one equality constraint per target_time job. After the existing `Sigma_Y` row setup (~line 698), increment `current_row` past `Sigma_Y` and then add the target rows:

```cpp
++current_row;  // advance past Sigma_Y row
for (unsigned i = 0; i < nb_target_time; ++i) {
  auto tt_name = std::format("TT{}", i);
  glp_set_row_name(lp, current_row, tt_name.c_str());
  // Cast to int64_t before subtraction to avoid unsigned underflow when
  // target_time < horizon_start (e.g., target_time: 0 with vehicle TW
  // starting later). The RHS may be negative — GLPK handles this fine.
  const double rhs = static_cast<double>(
    static_cast<int64_t>(target_time_values[i]) -
    static_cast<int64_t>(horizon_start));
  glp_set_row_bnds(lp, current_row, GLP_FX, rhs, rhs);
  ++current_row;
}
```

Non-zero matrix entries — appended after the existing `Sigma_Y` entries, before `glp_load_matrix`:
```cpp
for (unsigned i = 0; i < nb_target_time; ++i) {
  const unsigned row = nb_constraints - nb_target_time + 1 + i;  // 1-indexed

  // t_i coefficient = 1
  ia[r] = row;
  ja[r] = target_time_t_cols[i];
  ar[r] = 1;
  ++r;

  // target_early_i coefficient = 1
  ia[r] = row;
  ja[r] = start_target_col + 2 * i;
  ar[r] = 1;
  ++r;

  // target_late_i coefficient = -1
  ia[r] = row;
  ja[r] = start_target_col + 2 * i + 1;
  ar[r] = -1;
  ++r;
}
assert(r == nb_non_zero + 1);
```

#### 4d. Modify pass 2 objective to prioritize target alignment over earliness

Instead of a third MIP solve, add target deviation terms directly to pass 2's objective. The design goal is: **sacrifice earliness only for target-linked stops** (the target job itself and its shipment counterpart) **while preserving earliness for unrelated stops on the same route**.

Pass 2 currently minimizes `sum(M * t_i) + sum(coeff_k * delta_k)` where `M = delta_sum_majorant` (or 1 if no breaks). When `nb_target_time > 0`, restructure the objective (~line 1021):

To selectively zero earliness, we need a mapping from MIP column index back to job rank. Build this during the initial step iteration (Step 4a) alongside the existing tracking:

```cpp
// Map MIP t_i column → job rank for steps that are JOB type.
// Built during initial loop: when processing a JOB step at current_index,
// the MIP column is current_index + 1 and the job rank is step.rank.
std::unordered_map<unsigned, Index> t_col_to_job_rank;
```

In the `STEP_TYPE::JOB` case (same location as the target_time check), add:
```cpp
t_col_to_job_rank[current_index + 1] = step.rank;
```

Then in the pass 2 objective restructuring (~line 1021):

```cpp
// Target deviation must dominate all earliness terms combined so that
// the solver never sacrifices target alignment to keep unrelated jobs
// early. Weight 1 for earliness is a pure tiebreaker — among solutions
// with equal target deviation, prefer earlier arrival for unrelated jobs.
const auto target_weight = M * (n + 2);

if (nb_target_time > 0) {
  // Columns 2..n+1 are t_1..t_n (job/break steps).
  for (unsigned i = 2; i <= n + 1; ++i) {
    auto it = t_col_to_job_rank.find(i);
    if (it != t_col_to_job_rank.end() &&
        target_linked_job_ranks.contains(it->second)) {
      // Target-linked job: no earliness preference — free to drift.
      glp_set_obj_coef(lp, i, 0);
    } else {
      // Unrelated job or break: earliness as tiebreaker (weight 1).
      glp_set_obj_coef(lp, i, 1);
    }
  }

  // Symmetric target deviation — dominates all earliness terms.
  for (unsigned i = 0; i < nb_target_time; ++i) {
    glp_set_obj_coef(lp, start_target_col + 2 * i, target_weight);      // target_early
    glp_set_obj_coef(lp, start_target_col + 2 * i + 1, target_weight);  // target_late
  }
} else {
  // No target_time jobs — existing behavior: minimize earliness.
  for (unsigned i = 2; i <= n + 1; ++i) {
    glp_set_obj_coef(lp, i, M);
  }
}
```

**Why this is safe — no violations can be created or worsened:**
- Pass 1's makespan is pinned via a **hard equality constraint** (`GLP_FX` row bound, line 1028-1032)
- Pass 1's violation sum (`sum(Y_i)`) is pinned via a **hard equality constraint** (`GLP_FX` row bound, line 1038)
- These are inviolable MIP constraints — no objective weight can override them
- The target deviation terms only steer the solver within the feasible region already defined by these pinned constraints

**Weight hierarchy:** `target_weight = M * (n + 2)` ensures that reducing target deviation by 1 unit always outweighs making all `n` unrelated stops maximally late (each with weight 1). Earliness of unrelated jobs is a pure tiebreaker — it can never prevent the solver from achieving better target alignment.

**Behavioral effect:** On a route with A1→A2→B1→B2 where only B2 has `target_time`:
- B2: optimized toward `target_time` via deviation variables (dominant weight)
- B1 (B2's pickup counterpart): earliness zeroed — free to drift to help B2
- A1, A2: earliness weight 1 (tiebreaker) — prefer early, but will shift later if that helps B2 hit its target
- Breaks: earliness weight 1 — unaffected by target unless shifting helps alignment

#### 4e. Extract deviations from solution

After the pass 2 solve, read the target deviation variables to populate per-step lead/delay values. Build a lookup from step index to deviation:

```cpp
// Map job rank → (target_lead, target_delay) for output.
// Keyed on input.jobs[] rank (step.rank), which is available in both
// the initial counting loop and the output generation loop.
std::unordered_map<Index, std::pair<Duration, Duration>> target_deviations;
for (unsigned i = 0; i < nb_target_time; ++i) {
  const auto early = get_duration(glp_mip_col_val(lp, start_target_col + 2 * i));
  const auto late = get_duration(glp_mip_col_val(lp, start_target_col + 2 * i + 1));
  target_deviations[target_time_job_ranks[i]] = {early, late};
}
```

### Step 5: Report deviations as informational fields (not violations)

**File:** `src/structures/vroom/solution/step.h`

Add two fields to `Step`, mirroring the existing `lead_time`/`delay` pattern on `Violations`:
```cpp
UserDuration target_lead{0};   // service was this much earlier than target_time
UserDuration target_delay{0};  // service was this much later than target_time
```

These are plain informational fields on the step, not part of the `Violations` struct.

**File:** `src/algorithms/validation/choose_ETA.cpp`

In the step-building section (~line 1242), in the `STEP_TYPE::JOB` case, after computing `user_service_start`, look up the pre-computed deviation and apply it:

```cpp
if (auto it = target_deviations.find(job_rank); it != target_deviations.end()) {
  const auto& [early, late] = it->second;
  current.target_lead = utils::scale_to_user_duration(early);
  current.target_delay = utils::scale_to_user_duration(late);
}
```

where `job_rank` is `step.rank`, already available at line 1196 of the existing output loop. The deviation map is keyed on job rank (index into `input.jobs`), which is consistent between the initial counting loop and the output loop.

**Note:** If a step has both `forced_service.at` and `target_time`, `forced_service` wins (it's a hard constraint in the MIP). The reported `target_lead`/`target_delay` reflects the actual deviation from `target_time`, which may be nonzero due to the forced service override.

### Step 6: Route solve-mode output through `choose_ETA` when target_time is present

**Files:** `src/utils/helpers.cpp`, `src/utils/helpers.h`

#### 6a. Refactor: extract per-route formatting into a shared helper

The existing `format_solution(const Input&, const RawSolution&)` has a ~160-line per-route body (lines 169–319). Extract this into a standalone function so both the existing path and the new target_time path can reuse it without duplication:

```cpp
// New helper: format a single route from a RawRoute using the simple forward pass.
Route format_route_simple(const Input& input,
                          Index vehicle_rank,
                          const RawRoute& raw_route,
                          std::unordered_set<Index>& unassigned_ranks);
```

The body is the existing per-route logic from `format_solution` (lines 170–318), moved verbatim. The outer `format_solution` becomes a thin loop:

```cpp
Solution format_solution(const Input& input,
                         const RawSolution& raw_routes,
                         unsigned nb_thread) {
  std::unordered_set<Index> unassigned_ranks;
  for (unsigned i = 0; i < input.jobs.size(); ++i) {
    unassigned_ranks.insert(i);
  }

  // ... target_time path (see 6c) ...

  std::vector<Route> routes;
  routes.reserve(raw_routes.size());
  for (std::size_t i = 0; i < raw_routes.size(); ++i) {
    if (raw_routes[i].route.empty()) continue;
    routes.push_back(format_route_simple(input, i, raw_routes[i], unassigned_ranks));
  }

  return Solution(input.zero_amount(),
                  std::move(routes),
                  get_unassigned_jobs_from_ranks(input, unassigned_ranks));
}
```

Declare `format_route_simple` in `helpers.h`.

#### 6b. Build VehicleSteps from route data for target routes

New helper to convert a RawRoute's job sequence into VehicleSteps suitable for `choose_ETA`:

```cpp
// Convert a RawRoute's job sequence into VehicleSteps for choose_ETA.
// No breaks are included — solve-mode RawRoutes don't interleave breaks.
std::vector<VehicleStep> raw_route_to_vehicle_steps(
    const Input& input,
    const std::vector<Index>& route) {
  std::vector<VehicleStep> steps;
  steps.reserve(route.size() + 2);

  steps.emplace_back(STEP_TYPE::START);
  for (const auto job_rank : route) {
    const auto& job = input.jobs[job_rank];
    steps.emplace_back(job.type, job.id, ForcedService());
    steps.back().rank = job_rank;
  }
  steps.emplace_back(STEP_TYPE::END);

  return steps;
}
```

**VehicleStep.rank assignment — verified safe:** `VehicleStep.rank` is a non-const `Index` field (see `vehicle_step.h:37`). Normally set by `Input::set_vehicle_steps_ranks()` which maps job IDs → ranks via `job_id_to_rank`. In the solve-mode path, we already have the internal ranks from `raw_routes[i].route` (these are indices into `input.jobs`), so we set `step.rank` directly after construction. `choose_ETA` reads `step.rank` to look up `input.jobs[step.rank]` for time windows, service times, and now `target_time` — this works correctly with directly-assigned ranks.

New helper to convert a TWRoute (with interleaved breaks) into VehicleSteps:

```cpp
// Convert a TWRoute's job+break sequence into VehicleSteps for choose_ETA.
// Break interleaving is reconstructed from breaks_at_rank / breaks_counts metadata.
std::vector<VehicleStep> tw_route_to_vehicle_steps(
    const Input& input,
    const TWRoute& tw_r) {
  const auto& v = input.vehicles[tw_r.v_rank];
  std::vector<VehicleStep> steps;
  steps.reserve(tw_r.route.size() + 2 + v.breaks.size());

  steps.emplace_back(STEP_TYPE::START);

  for (std::size_t r = 0; r <= tw_r.route.size(); ++r) {
    // Insert breaks before job at rank r (or before END when r == route.size()).
    // breaks_counts[r] is the cumulative count up to rank r.
    // breaks_at_rank[r] is how many breaks sit immediately before rank r.
    // So the break indices are: breaks_counts[r] - breaks_at_rank[r] .. breaks_counts[r] - 1
    assert(tw_r.breaks_at_rank[r] <= tw_r.breaks_counts[r]);
    Index break_rank = tw_r.breaks_counts[r] - tw_r.breaks_at_rank[r];
    for (Index i = 0; i < tw_r.breaks_at_rank[r]; ++i, ++break_rank) {
      const auto& b = v.breaks[break_rank];
      steps.emplace_back(STEP_TYPE::BREAK, b.id, ForcedService());
      steps.back().rank = break_rank;
    }

    // Insert job (except after the last rank, which is END).
    if (r < tw_r.route.size()) {
      const auto job_rank = tw_r.route[r];
      const auto& job = input.jobs[job_rank];
      steps.emplace_back(job.type, job.id, ForcedService());
      steps.back().rank = job_rank;
    }
  }

  steps.emplace_back(STEP_TYPE::END);

  return steps;
}
```

#### 6c. RawSolution format_solution with target_time

Modify `format_solution(const Input&, const RawSolution&, unsigned nb_thread)`. When `has_target_time()`, classify routes into target vs simple, then process them:

```cpp
#if USE_LIBGLPK
  if (input.has_target_time()) {
    std::vector<std::size_t> target_route_indices;
    std::vector<std::size_t> simple_route_indices;

    for (std::size_t i = 0; i < raw_routes.size(); ++i) {
      const auto& route = raw_routes[i].route;
      if (route.empty()) continue;

      bool has_target = std::ranges::any_of(route, [&](Index job_rank) {
        return input.jobs[job_rank].target_time.has_value();
      });

      if (has_target) {
        target_route_indices.push_back(i);
      } else {
        simple_route_indices.push_back(i);
      }

      // Mark all jobs as assigned regardless of path.
      for (const auto job_rank : route) {
        unassigned_ranks.erase(job_rank);
      }
    }

    // Use a flat vector indexed by raw_routes position.
    std::vector<Route> all_routes(raw_routes.size());

    // Format simple routes (no MIP needed).
    // unassigned_ranks tracking is already done above, so pass the real
    // set — format_route_simple will call erase() on already-erased
    // ranks, which is a harmless no-op for std::unordered_set.
    for (const auto i : simple_route_indices) {
      all_routes[i] = format_route_simple(input, i, raw_routes[i], unassigned_ranks);
    }

    // Parallelize MIP-based ETA computation for target routes.
    // Pattern follows check_and_set_ETA (check.cpp:60-90).
    const auto nb_target = target_route_indices.size();
    const auto nb_buckets =
      std::min(static_cast<std::size_t>(nb_thread), nb_target);

    std::vector<std::vector<std::size_t>> thread_buckets(nb_buckets);
    for (std::size_t k = 0; k < nb_target; ++k) {
      thread_buckets[k % nb_buckets].push_back(target_route_indices[k]);
    }

    std::exception_ptr ep = nullptr;
    std::mutex ep_m;

    auto run_eta = [&](const std::vector<std::size_t>& indices) {
      try {
        for (const auto i : indices) {
          auto steps = raw_route_to_vehicle_steps(input, raw_routes[i].route);
          all_routes[i] = validation::choose_ETA(input, raw_routes[i].v_rank, steps);
        }
      } catch (...) {
        const std::scoped_lock<std::mutex> lock(ep_m);
        ep = std::current_exception();
      }
    };

    std::vector<std::jthread> threads;
    threads.reserve(nb_buckets);
    for (const auto& bucket : thread_buckets) {
      threads.emplace_back(run_eta, bucket);
    }
    for (auto& t : threads) {
      t.join();
    }
    if (ep != nullptr) {
      std::rethrow_exception(ep);
    }

    // Collect non-empty routes in order.
    std::vector<Route> routes;
    for (auto& r : all_routes) {
      if (!r.steps.empty()) {
        routes.push_back(std::move(r));
      }
    }

    return Solution(input.zero_amount(),
                    std::move(routes),
                    get_unassigned_jobs_from_ranks(input, unassigned_ranks));
  }
#else
  if (input.has_target_time()) {
    // VROOM has no logging framework; use stderr for warnings.
    std::cerr << "[warning] target_time requires libglpk for optimization; "
                 "falling back to simple forward pass." << std::endl;
  }
#endif
```

#### 6d. TWSolution format_solution with target_time

The `format_solution(const Input&, const TWSolution&, unsigned nb_thread)` overload (~line 812) calls `format_route` per TWRoute. When `has_target_time()`, convert target routes to VehicleSteps via `tw_route_to_vehicle_steps` and call `choose_ETA`.

**TWRoute → VehicleStep conversion rationale:** TWRoute stores the job sequence in `tw_route.route` (inherited from RawRoute) and break interleaving metadata in `breaks_at_rank` / `breaks_counts`:

- `breaks_at_rank[r]` = number of breaks inserted **before** job at rank `r`
- `breaks_at_rank[route.size()]` = number of breaks **after** the last job (before END)
- `breaks_counts[r]` = cumulative break count up to rank `r`
- Break indices into `v.breaks[]` for rank `r`: from `breaks_counts[r] - breaks_at_rank[r]` through `breaks_counts[r] - 1`

This is the same indexing used by `format_route`'s forward pass (helpers.cpp:521-524).

**Note:** Converting to VehicleSteps and calling `choose_ETA` discards TWRoute's own backward/forward-pass scheduling. This is intentional — the MIP produces target-aligned ETAs that TWRoute's heuristic pass cannot.

```cpp
#if USE_LIBGLPK
  if (input.has_target_time()) {
    std::vector<std::size_t> target_route_indices;
    std::vector<std::size_t> simple_route_indices;

    for (std::size_t i = 0; i < tw_routes.size(); ++i) {
      if (tw_routes[i].empty()) continue;

      bool has_target = std::ranges::any_of(tw_routes[i].route, [&](Index job_rank) {
        return input.jobs[job_rank].target_time.has_value();
      });

      if (has_target) {
        target_route_indices.push_back(i);
      } else {
        simple_route_indices.push_back(i);
      }

      // Mark all jobs as assigned regardless of path.
      for (const auto job_rank : tw_routes[i].route) {
        unassigned_ranks.erase(job_rank);
      }
    }

    // Use a flat vector indexed by tw_routes position.
    std::vector<Route> all_routes(tw_routes.size());

    // Format simple routes (no MIP needed).
    for (const auto i : simple_route_indices) {
      all_routes[i] = format_route(input, tw_routes[i], unassigned_ranks);
    }

    // Parallelize MIP-based ETA computation for target routes.
    const auto nb_target = target_route_indices.size();
    const auto nb_buckets =
      std::min(static_cast<std::size_t>(nb_thread), nb_target);

    std::vector<std::vector<std::size_t>> thread_buckets(nb_buckets);
    for (std::size_t k = 0; k < nb_target; ++k) {
      thread_buckets[k % nb_buckets].push_back(target_route_indices[k]);
    }

    std::exception_ptr ep = nullptr;
    std::mutex ep_m;

    auto run_eta = [&](const std::vector<std::size_t>& indices) {
      try {
        for (const auto i : indices) {
          auto steps = tw_route_to_vehicle_steps(input, tw_routes[i]);
          all_routes[i] = validation::choose_ETA(input, tw_routes[i].v_rank, steps);
        }
      } catch (...) {
        const std::scoped_lock<std::mutex> lock(ep_m);
        ep = std::current_exception();
      }
    };

    std::vector<std::jthread> threads;
    threads.reserve(nb_buckets);
    for (const auto& bucket : thread_buckets) {
      threads.emplace_back(run_eta, bucket);
    }
    for (auto& t : threads) {
      t.join();
    }
    if (ep != nullptr) {
      std::rethrow_exception(ep);
    }

    // Collect non-empty routes in order.
    std::vector<Route> routes;
    routes.reserve(tw_routes.size());
    for (auto& r : all_routes) {
      if (!r.steps.empty()) {
        routes.push_back(std::move(r));
      }
    }

    return Solution(input.zero_amount(),
                    std::move(routes),
                    get_unassigned_jobs_from_ranks(input, unassigned_ranks));
  }
#else
  if (input.has_target_time()) {
    // VROOM has no logging framework; use stderr for warnings.
    std::cerr << "[warning] target_time requires libglpk for optimization; "
                 "falling back to simple forward pass." << std::endl;
  }
#endif
```

#### 6e. Thread `nb_thread` through to format_solution

Add `unsigned nb_thread = 1` parameter to both `format_solution` overload declarations in `helpers.h`. Update callers in `vrp.h` (the `solve` template calls `format_solution` after heuristic search) to pass their `nb_thread` through.

**Conditional compilation:** Guard `#include "algorithms/validation/choose_ETA.h"` and all `choose_ETA` calls in `helpers.cpp` with `#if USE_LIBGLPK`. When compiled without libglpk, `choose_ETA.cpp` is excluded from the build (see `src/makefile` filter), so any reference to it must be guarded.

**New includes for `helpers.cpp`:** Add `#include <iostream>` (for `std::cerr` in the no-libglpk fallback warning) and `#include <mutex>`, `#include <thread>` (for the parallelized MIP path).

### Step 7: Update JSON output

**File:** `src/utils/output_json.cpp`

In the `to_json(const Step&, ...)` function (~line 281), after the `violations` member (~line 362) and before the `distance` conditional (~line 364), add:

```cpp
if (s.target_lead > 0) {
  json_step.AddMember("target_lead", s.target_lead, allocator);
}
if (s.target_delay > 0) {
  json_step.AddMember("target_delay", s.target_delay, allocator);
}
```

No changes to `get_violations()` — target deviations are not violations.

### Step 8: Update API documentation

**File:** `docs/API.md` (or equivalent)

Document the new `target_time` field:
- On jobs: `"target_time"` — optional integer, the preferred service start time. Best-effort; does not create violations.
- On shipments: `"target_time"` — can appear on pickup, delivery, or both.
- Output: `"target_lead"` and `"target_delay"` on step objects — how much earlier or later (in user duration units) the scheduled service start is relative to the requested `target_time`. Only present when nonzero. Exactly one of the two will be nonzero when there is a deviation (both zero = exact match, fields omitted).
- Requires `libglpk` for optimization; without it, a warning is logged and `target_time` is ignored for scheduling.

---

## Phase 2 Extension Points

This design leaves clear hooks for Phase 2 (Slack Collapse):

1. **Job struct:** Add `std::optional<Id> anchor` field alongside `target_time`
2. **Validation:** Enforce that `anchor` and `target_time` don't appear on the same side of a shipment
3. **`choose_ETA` MIP:** Add constraints that link a non-priority stop's service time to its anchor's resolved service time. Could be additional constraints/terms in pass 2's objective
4. **`_has_target_time` flag:** Expand to `_has_target_time_or_anchor` to trigger the enhanced solve path
5. **Asymmetric / user-configurable weights:** Phase 1 uses symmetric early/late weights. Phase 2 could expose `target_early_weight` / `target_late_weight` as input parameters for scenarios where being late is worse than being early (common in passenger transport)

---

## Files to Modify (Summary)

| File | Change |
|------|--------|
| `src/structures/vroom/job.h` | Add `target_time` field, update constructor declarations |
| `src/structures/vroom/job.cpp` | Update constructor init lists with scaling |
| `src/utils/input_parser.cpp` | Parse `target_time` for jobs and shipments |
| `src/structures/vroom/input/input.h` | Add `_has_target_time` flag and accessor |
| `src/structures/vroom/input/input.cpp` | Set flag in add_job/add_shipment |
| `src/structures/vroom/solution/step.h` | Add `target_lead` and `target_delay` informational fields |
| `src/algorithms/validation/choose_ETA.cpp` | MIP changes (vars, constraints, weighted pass 2 objective), deviation extraction |
| `src/utils/output_json.cpp` | Serialize `target_lead` / `target_delay` on JOB steps |
| `src/utils/helpers.h` | Declare `format_route_simple`, `raw_route_to_vehicle_steps`, `tw_route_to_vehicle_steps`; add `nb_thread` param to `format_solution` overloads |
| `src/utils/helpers.cpp` | Refactored `format_solution` overloads, new helpers |
| `src/problems/vrp.h` | Pass `nb_thread` to `format_solution` calls |
| `docs/API.md` | Document `target_time` input and `target_lead`/`target_delay` output |

---

## Verification

VROOM uses functional/integration tests via JSON input/output comparison. Tests are JSON files in `docs/`, validated in CI (`.github/workflows/vroom.yml`) by diffing `bin/vroom` output against expected `_sol.json` files using `jq --sort-keys`.

### Build verification

1. `make -j` from `src/` — verify clean compilation with `USE_LIBGLPK`
2. Build without libglpk (remove `GLPK_HEADER` override) — verify clean compilation, `choose_ETA.cpp` excluded, warning logged when `target_time` present

### Regression tests: existing examples unchanged

3. Verify `docs/example_1.json`, `example_2.json`, `example_3.json` produce identical output to their `_sol.json` counterparts. These have no `target_time` and must be bit-for-bit identical. Add `example_1` and `example_3` (plan mode) to CI alongside the existing `example_2` check:
```yaml
- name: Validate example_1 output
  run: diff <(bin/vroom -i docs/example_1.json | jq '.routes[].steps[]' --sort-keys) <(jq '.routes[].steps[]' --sort-keys docs/example_1_sol.json)
- name: Validate example_3 plan mode output
  run: diff <(bin/vroom -c -i docs/example_3.json | jq '.routes[].steps[]' --sort-keys) <(jq '.routes[].steps[]' --sort-keys docs/example_3_sol.json)
```

### New test: solve mode — target achievable (`docs/example_target_time.json`)

4. Create a test input with 4 jobs on a single vehicle, using a custom duration matrix, where 2 jobs have achievable `target_time` values:
   - Vehicle: id 1, start/end at location index 0, time_window [0, 10000]
   - Job 1: id 1, location_index 1, no target_time, TW [1000, 2000]
   - Job 2: id 2, location_index 2, `target_time: 3000`, TW [2500, 4000]
   - Job 3: id 3, location_index 3, `target_time: 5000`, TW [4000, 6000]
   - Job 4: id 4, location_index 4, no target_time, TW [5000, 7000]
   - Duration matrix chosen so travel times allow hitting target_time values exactly
   - **Validate:** output steps for jobs 2 and 3 have no `target_lead`/`target_delay` fields (deviation is 0)
   - **Validate:** output steps for jobs 1 and 4 have no `target_lead`/`target_delay` fields (no target_time set)
   - **Validate:** no violation types in output
   - Generate expected output, manually verify, then add to CI:
   ```yaml
   - name: Validate target_time solve mode
     run: diff <(bin/vroom -i docs/example_target_time.json | jq '.routes[].steps[]' --sort-keys) <(jq '.routes[].steps[]' --sort-keys docs/example_target_time_sol.json)
   ```

### New test: plan mode with target_time (`docs/example_target_time_plan.json`)

5. Plan-mode variant of test 4 — same jobs and matrix, vehicle has explicit `"steps"` array. Run with `-c` flag:
   ```yaml
   - name: Validate target_time plan mode
     run: diff <(bin/vroom -c -i docs/example_target_time_plan.json | jq '.routes[].steps[]' --sort-keys) <(jq '.routes[].steps[]' --sort-keys docs/example_target_time_plan_sol.json)
   ```
   **Validate:** same target alignment behavior and `target_lead`/`target_delay` values as solve mode.

### New test: target_time with deviation (`docs/example_target_deviation.json`)

6. Create a test where `target_time` is **not** achievable due to travel constraints:
   - Two jobs with conflicting `target_time` values (both want similar times but are far apart)
   - **Validate:** at least one step has nonzero `target_lead` or `target_delay`
   - **Validate:** the deviation values are correct: `target_lead = target_time - service_start` (early) or `target_delay = service_start - target_time` (late)
   - **Validate:** no violation types related to target — only informational fields
   - Add to CI with jq-based diff

### New test: shipment with target_time on both sides (`docs/example_target_shipment.json`)

7. Create a test with a shipment where both pickup and delivery have `target_time`:
   - **Validate:** both pickup and delivery steps report independent `target_lead`/`target_delay`
   - **Validate:** deviations are computed independently (pickup target doesn't affect delivery target reporting)
   - Add to CI with jq-based diff

### Edge case tests

8. `target_time` outside all time windows → solver does its best within TW, reports deviation
9. `target_time` with default time windows (no TW set) → works, biases ETA toward target
10. `target_time` combined with `forced_service.at` in plan mode → forced_service wins, deviation reported as `target_lead` or `target_delay`
11. `target_time` only on pickup of shipment → works; delivery step has no target fields
12. Inputs without `target_time` → MIP target path skipped entirely (`nb_target_time == 0`), zero overhead, simple forward pass used
