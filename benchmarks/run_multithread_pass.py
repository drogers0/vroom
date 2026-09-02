#!/usr/bin/env python3
"""Multithreaded benchmark pass at the default thread count.

Runs the feature binary on the capped and uncapped sets at vroom's default
`-t 4`, REPS runs per instance. Reports per-set totals: served, cap
violations (property checker), median/min/max wall-clock per instance and
run-to-run cost spread (multithreaded solving is nondeterministic, so cost
may vary between runs; byte-identical comparison does not apply here).

Usage: run_multithread_pass.py BINARY CAPPED_DIR UNCAPPED_DIR
"""

import json
import pathlib
import statistics
import subprocess
import sys
import time

REPS = 5


def run_set(binary: str, d: pathlib.Path, check_caps: bool) -> None:
    tot_ship = 0
    tot_unassigned_best = 0
    viol_instances = 0
    times = []
    spread_instances = 0
    for f in sorted(d.glob("*.json")):
        data = json.loads(f.read_text())
        n_ship = len(data.get("shipments", []))
        costs = set()
        best_unassigned = None
        inst_times = []
        last_out = None
        for _ in range(REPS):
            t0 = time.time()
            r = subprocess.run([binary, "-i", str(f), "-t", "4"],
                               capture_output=True, text=True)
            inst_times.append((time.time() - t0) * 1000)
            out = json.loads(r.stdout)
            last_out = out
            costs.add(out["summary"]["cost"])
            u = out["summary"]["unassigned"]
            best_unassigned = u if best_unassigned is None else min(
                best_unassigned, u)
            if check_caps:
                chk = subprocess.run(
                    [sys.executable, "tests/max_transit_time/check_caps.py",
                     str(f), "/dev/stdin", "-q"],
                    input=json.dumps(out), capture_output=True, text=True)
                if chk.returncode != 0:
                    viol_instances += 1
        del last_out
        tot_ship += n_ship
        tot_unassigned_best += best_unassigned
        times.append(statistics.median(inst_times))
        if len(costs) > 1:
            spread_instances += 1
    print(f"  instances={len(times)} shipments={tot_ship} "
          f"best-run unassigned={tot_unassigned_best}")
    print(f"  per-instance median ms: median={statistics.median(times):.0f} "
          f"min={min(times):.0f} max={max(times):.0f}")
    print(f"  instances with run-to-run cost spread: {spread_instances}"
          f"/{len(times)}")
    if check_caps:
        print(f"  runs with cap violations: {viol_instances} "
              f"(of {len(times) * REPS})")


def main() -> None:
    binary, capped, uncapped = sys.argv[1], sys.argv[2], sys.argv[3]
    print(f"threads=4 (vroom default), reps={REPS} per instance, "
          f"medians reported")
    print("capped set:")
    run_set(binary, pathlib.Path(capped), True)
    print("uncapped set:")
    run_set(binary, pathlib.Path(uncapped), False)


if __name__ == "__main__":
    main()
