#!/usr/bin/env python3
"""Classify unassigned shipments in the worst-case stress set.

For every shipment left unassigned by a full solve, build a single-shipment
instance with one vehicle from the original (same time window and breaks) and
solve it alone. Alone on a dedicated vehicle is the most favorable setting a
shipment can get (no competing stops between its pickup and delivery, no
fleet contention), so a shipment that cannot be served alone is provably
infeasible on any route of that instance. Reports, per instance and in total:
unassigned shipments, provably infeasible among them, and the remainder
(not proven either way).

Usage: count_infeasible_worstcase.py INSTANCE_DIR BINARY
"""

import copy
import json
import pathlib
import subprocess
import sys


def solve(binary: str, data: dict) -> dict:
    r = subprocess.run([binary, "-i", "/dev/stdin", "-t", "1"],
                       input=json.dumps(data), capture_output=True, text=True)
    return json.loads(r.stdout)


def main() -> None:
    inst_dir = pathlib.Path(sys.argv[1])
    binary = sys.argv[2]
    tot_unassigned = 0
    tot_infeasible = 0
    print(f"{'inst':8} {'unassigned':>10} {'infeasible':>10} {'unproven':>9}")
    for f in sorted(inst_dir.glob("*.json")):
        data = json.loads(f.read_text())
        full = solve(binary, data)
        unassigned_ids = {u["id"] for u in full.get("unassigned", [])}
        un_ship = [s for s in data["shipments"]
                   if s["pickup"]["id"] in unassigned_ids
                   or s["delivery"]["id"] in unassigned_ids]
        infeasible = 0
        for s in un_ship:
            solo = {"matrices": data["matrices"],
                    "vehicles": [copy.deepcopy(data["vehicles"][0])],
                    "shipments": [s]}
            alone = solve(binary, solo)
            if alone["summary"]["unassigned"] > 0:
                infeasible += 1
        print(f"{f.stem:8} {len(un_ship):>10} {infeasible:>10} "
              f"{len(un_ship) - infeasible:>9}")
        tot_unassigned += len(un_ship)
        tot_infeasible += infeasible
    print(f"\nTOTAL unassigned shipments={tot_unassigned} "
          f"provably infeasible alone-on-a-vehicle={tot_infeasible} "
          f"({100.0 * tot_infeasible / max(1, tot_unassigned):.1f}%) "
          f"unproven={tot_unassigned - tot_infeasible}")


if __name__ == "__main__":
    main()
