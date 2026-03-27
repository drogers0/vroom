I have been a long time user of VROOM and have implemented a VROOM-native three-phase solve strategy to address some of the rough edges that come with a pure constraint solver. In particular, for passenger-style PDPTW problems, we often need to express soft requested service times and reduce unnecessary ride-time slack without changing stop order.

Today, time windows are strictly hard constraints and waiting time is not part of the objective, which can produce feasible but operationally undesirable results (e.g., necessarily schedule for edge of time window or have early pickups followed by onboard dwell). The approach below works within existing VROOM semantics, but requires non-obvious manipulation of time windows and PLAN mode.

I have a wrapper that does something similar in my codebase, but would like to migrate it into the solver itself to increase performance, simplify my implementation, and open up additional functionality to Vroom users.

---

## Phase 0
Run a VRP solve. 


## Phase 1 – Requested-Time Coercion

**Goal:** Bias solution service toward a requested pickup or dropoff time without breaking original feasibility.

### Steps

3. For the priority stop (pickup or dropoff):

   * Preserve original time window as hard bounds:

     ```
     service_after  = original_lower_bound
     service_before = original_upper_bound
     ```
   * Replace `time_windows` with a tight window around the requested time:

     ```
     time_windows = [requested_time ± ε]
     ```
4. Re-solve in plan mode

### Effect

* Solver attempts to hit requested times (misses are balanced across trips and reported as "violations")
* Original window remains enforced via hard bounds.
* Stop order is unchanged.

---

## Phase 2 – Ride-Time Slack Collapse

**Goal:** Remove unnecessary onboard dwell while preserving sequence and requested-time alignment.

### Steps

1. Extract scheduled times from Phase 1.
2. For each priority stop:

   * Freeze the scheduled time:

     ```
     service_at = scheduled_time_from_phase_1
     ```
3. For each non-priority stop:
   * Tighten `time_windows` around the priority stop service_at time:

     ```
     time_windows = [priority_stop_service_at ± ε]
     ```
     > travel times could be incorporated here to avoid biasing balancing toward longer trips

   * Keep existing:

     ```
     service_after
     service_before
     ```
4. Re-solve in plan mode

### Effect

* Solver collapses slack where possible (misses are balanced across trips and reported as "violations")
* Original window remains enforced via hard bounds.
* Stop order is unchanged.
* Requested-time alignment is preserved.

---

## Proposal

Add native support for soft requested times and slack collapse through api update and internal implementation of logic described above.

example api update:
### job/shipment_step

| Key         | Description |
| ----------- | ----------- |
| `target_time` | a timestamp describing prefered servicing time (in shipments, can only be used on pickup or dropoff, not both) 
| `anchor` | id of the shipment_step or job that anchors compaction



---

I would be happy to contribute documentation or discuss implementation approaches. 
If we are aligned, I could start work on putting a PR forward.

Thank you!