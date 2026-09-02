#!/usr/bin/env python3
"""Differential feasibility oracle for plan-mode service_within.

The hard system on a fixed route order is a set of difference constraints:
precedence (start_i >= start_{i-1} + action_{i-1} + travel), forced
service_at/after/before pins, and per-pair transit caps
(start_D <= start_P + action_P + cap). Time windows are soft and excluded,
matching the implementation. Bellman-Ford on the constraint graph decides
feasibility exactly.

For each random config the oracle runs vroom -c twice: base (keys
stripped) and keyed. Classification:

- base feasible, cap system feasible: keyed must succeed AND every
  realized transit must be within its key (soundness of the Sigma_Y /
  horizon-margin relaxation: no spurious infeasibility, no violation).
- base feasible, cap system infeasible: keyed must fail (enforcement: no
  accepted violation).
- base infeasible (pre-existing sample-bounded-horizon behavior on far
  pins/windows): the keyed run's wider horizon may legitimately recover a
  schedule the clipped horizon excluded, so the keyed outcome is judged
  against ground truth ("key_repairs_pre_existing" when it succeeds on a
  truly feasible system).

Configs: 1 vehicle, 1-3 shipments in random valid P..D interleavings,
optional plain jobs in between, non-metric random matrices, setup/service
times with same-location setup suppression, soft TWs (including far ones,
the horizon-clipping risk case), random pins, caps swept around the
feasibility boundary (below / exactly at / above minimal transit).
Break-free, like the solve-engine brute-force comparisons in the
max_transit_time design note.

Usage: service_within_oracle.py VROOM_BIN [N_CONFIGS] [SEED]
"""

import json
import random
import subprocess
import sys

def run_vroom(binary, inst):
    r = subprocess.run([binary, "-c", "-t", "1", "-i", "/dev/stdin"],
                       input=json.dumps(inst), capture_output=True, text=True)
    if r.returncode != 0:
        return None
    return json.loads(r.stdout)


def gen_config(rng):
    n_ship = rng.randint(1, 3)
    n_job = rng.randint(0, 2)
    n_loc = rng.randint(3, 5)
    dur = [[0 if i == j else rng.randint(5, 60) for j in range(n_loc)]
           for i in range(n_loc)]

    shipments, jobs = [], []
    tid = 1
    for _ in range(n_ship):
        p_id, d_id = tid, tid + 1
        tid += 2
        shipments.append({
            "pickup": {"id": p_id, "location_index": rng.randrange(n_loc),
                       "setup": rng.choice([0, 0, 5]),
                       "service": rng.choice([0, 5, 10])},
            "delivery": {"id": d_id, "location_index": rng.randrange(n_loc),
                         "setup": rng.choice([0, 0, 5]),
                         "service": rng.choice([0, 5])},
        })
    for _ in range(n_job):
        jobs.append({"id": tid, "location_index": rng.randrange(n_loc),
                     "service": rng.choice([0, 5, 15])})
        tid += 1

    # Soft TWs on a random subset, sometimes far away.
    for task in ([s["pickup"] for s in shipments] +
                 [s["delivery"] for s in shipments] + jobs):
        if rng.random() < 0.5:
            base = rng.choice([0, 50, 200, 2000])
            task["time_windows"] = [[base, base + rng.randint(10, 120)]]

    # Random valid order: shuffle tokens until every P precedes its D.
    tokens = ([("pickup", s["pickup"]["id"]) for s in shipments] +
              [("delivery", s["delivery"]["id"]) for s in shipments] +
              [("job", j["id"]) for j in jobs])
    while True:
        rng.shuffle(tokens)
        pos = {t: i for i, t in enumerate(tokens)}
        if all(pos[("pickup", s["pickup"]["id"])] <
               pos[("delivery", s["delivery"]["id"])] for s in shipments):
            break

    steps = [{"type": "start"}]
    steps += [{"type": t, "id": i} for t, i in tokens]
    steps.append({"type": "end"})

    # Occasional feasible-ish pins.
    for st in steps[1:-1]:
        if rng.random() < 0.25:
            kind = rng.choice(["service_after", "service_before"])
            st[kind] = (rng.randint(0, 150) if kind == "service_after"
                        else rng.randint(150, 600))

    inst = {
        "matrices": {"car": {"durations": dur}},
        "shipments": shipments,
        "vehicles": [{"id": 1, "profile": "car", "start_index": 0,
                      "steps": steps}],
    }
    if jobs:
        inst["jobs"] = jobs
    return inst


def location_of(inst, typ, tid):
    if typ == "job":
        return next(j["location_index"] for j in inst.get("jobs", [])
                    if j["id"] == tid)
    for s in inst["shipments"]:
        if typ == "pickup" and s["pickup"]["id"] == tid:
            return s["pickup"]["location_index"]
        if typ == "delivery" and s["delivery"]["id"] == tid:
            return s["delivery"]["location_index"]
    raise KeyError((typ, tid))


def task_of(inst, typ, tid):
    if typ == "job":
        return next(j for j in inst.get("jobs", []) if j["id"] == tid)
    for s in inst["shipments"]:
        if typ == "pickup" and s["pickup"]["id"] == tid:
            return s["pickup"]
        if typ == "delivery" and s["delivery"]["id"] == tid:
            return s["delivery"]
    raise KeyError((typ, tid))


def hard_system(inst, caps):
    """Difference constraints x_j - x_i <= c over step starts.

    Returns (n, edges) with node 0 the vehicle start step; node n is a
    ground for absolute pins.
    """
    dur = inst["matrices"]["car"]["durations"]
    steps = inst["vehicles"][0]["steps"]
    n = len(steps)
    edges = []
    ground = n
    start_loc = inst["vehicles"][0]["start_index"]

    prev_loc = start_loc
    actions, locs = [], []
    for st in steps:
        if st["type"] in ("start", "end"):
            actions.append(0)
            locs.append(start_loc if st["type"] == "start" else prev_loc)
            prev_loc = locs[-1]
            continue
        loc = location_of(inst, st["type"], st["id"])
        task = task_of(inst, st["type"], st["id"])
        setup = 0 if loc == prev_loc else task.get("setup", 0)
        actions.append(setup + task.get("service", 0))
        locs.append(loc)
        prev_loc = loc

    for i in range(1, n):
        # x_i >= x_{i-1} + action_{i-1} + travel  <=>  x_{i-1} - x_i <= -c
        c = actions[i - 1] + dur[locs[i - 1]][locs[i]]
        edges.append((i, i - 1, -c))

    for i, st in enumerate(steps):
        if "service_after" in st:
            edges.append((i, ground, -st["service_after"]))
        if "service_before" in st:
            edges.append((ground, i, st["service_before"]))
    # x_start >= 0
    edges.append((0, ground, 0))

    pos = {(st["type"], st.get("id")): i for i, st in enumerate(steps)}
    for (p_id, d_id), cap in caps.items():
        p = pos[("pickup", p_id)]
        d = pos[("delivery", d_id)]
        # x_d - x_p <= action_p + cap
        edges.append((p, d, actions[p] + cap))
    return n + 1, edges


def feasible(n, edges):
    dist = [0] * n
    for _ in range(n):
        changed = False
        for u, v, w in edges:
            # constraint x_v - x_u <= w  -> relax dist[v] > dist[u] + w
            if dist[u] + w < dist[v]:
                dist[v] = dist[u] + w
                changed = True
        if not changed:
            return True
    # Still relaxable after n passes = negative cycle = infeasible.
    return not any(dist[u] + w < dist[v] for u, v, w in edges)


def transits_ok(out, caps):
    info = {}
    for route in out.get("routes", []):
        for st in route.get("steps", []):
            if st["type"] in ("pickup", "delivery"):
                start = st["arrival"] + st.get("waiting_time", 0)
                dep = start + st.get("setup", 0) + st.get("service", 0)
                info[(st["type"], st["id"])] = (start, dep)
    for (p_id, d_id), cap in caps.items():
        ride = info[("delivery", d_id)][0] - info[("pickup", p_id)][1]
        if ride > cap:
            return False
    return True


def main():
    binary = sys.argv[1]
    n_configs = int(sys.argv[2]) if len(sys.argv) > 2 else 2000
    seed = int(sys.argv[3]) if len(sys.argv) > 3 else 42
    rng = random.Random(seed)

    counts = {"pre_existing_infeasible": 0, "key_repairs_pre_existing": 0,
              "feasible_ok": 0, "infeasible_ok": 0}
    failures = []

    done = 0
    while done < n_configs:
        inst = gen_config(rng)
        base_out = run_vroom(binary, inst)

        # Pick caps: for each shipment, sweep around the hard-system
        # boundary via binary search on the oracle.
        caps = {}
        for s in inst["shipments"]:
            pair = (s["pickup"]["id"], s["delivery"]["id"])
            if rng.random() < 0.25:
                continue  # leave this pair uncapped
            lo, hi = 0, 4000
            while lo < hi:
                mid = (lo + hi) // 2
                trial = dict(caps)
                trial[pair] = mid
                if feasible(*hard_system(inst, trial)):
                    hi = mid
                else:
                    lo = mid + 1
            boundary = lo
            caps[pair] = max(0, boundary + rng.choice([-7, -1, 0, 1, 13]))
        if not caps:
            continue

        keyed = json.loads(json.dumps(inst))
        pos = {st.get("id"): st for st in keyed["vehicles"][0]["steps"]
               if st.get("type") == "delivery"}
        for (p_id, d_id), cap in caps.items():
            pos[d_id]["service_within"] = cap

        truth = feasible(*hard_system(inst, caps))
        keyed_out = run_vroom(binary, keyed)
        done += 1

        if base_out is None:
            # Pre-existing behavior: forced pins / far windows beyond the
            # sample-bounded horizon error even without the key. When the
            # key is present its wider horizon can legitimately recover
            # the schedule the clipped horizon excluded, so judge the
            # keyed run against ground truth, not against the base run.
            if keyed_out is None:
                # Both runs clipped; key-caused spurious infeasibility is
                # indistinguishable from the pre-existing kind here.
                counts["pre_existing_infeasible"] += 1
            elif truth and transits_ok(keyed_out, caps):
                counts["key_repairs_pre_existing"] += 1
            else:
                failures.append(("keyed_ok_but_truly_infeasible", inst, caps))
            continue
        if truth:
            if keyed_out is None:
                failures.append(("spurious_infeasible", inst, caps))
            elif not transits_ok(keyed_out, caps):
                failures.append(("accepted_violation", inst, caps))
            else:
                counts["feasible_ok"] += 1
        else:
            if keyed_out is not None:
                failures.append(("missed_infeasible", inst, caps))
            else:
                counts["infeasible_ok"] += 1

    print(f"configs: {done}")
    for k, v in counts.items():
        print(f"  {k}: {v}")
    print(f"  FAILURES: {len(failures)}")
    for kind, inst, caps in failures[:5]:
        print(f"--- {kind} caps={caps}")
        print(json.dumps(inst))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
