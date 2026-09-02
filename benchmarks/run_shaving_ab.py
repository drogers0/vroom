#!/usr/bin/env python3
"""Compare exact max_transit_time enforcement against the delivery-window
shaving workaround on stock master.

A = master binary on the shaved variant (workaround).
B = feature binary on the capped originals (exact enforcement).

Per instance: shipments served, unassigned (workaround pre-drops included),
total cost, median solve ms of REPS runs. Shaved outputs are also verified
against the ORIGINAL caps with the property checker, proving the workaround
compliant (it should be, by construction).

Usage: run_shaving_ab.py CAPPED_DIR SHAVED_DIR MASTER_BIN FEATURE_BIN
"""

import json
import pathlib
import statistics
import subprocess
import sys
import time

REPS = 3


def solve(binary: str, path: pathlib.Path) -> tuple[dict, float]:
    times = []
    out = None
    for _ in range(REPS):
        t0 = time.time()
        r = subprocess.run([binary, "-i", str(path), "-t", "1"],
                           capture_output=True, text=True)
        times.append((time.time() - t0) * 1000)
        out = json.loads(r.stdout)
    return out, statistics.median(times)


def main() -> None:
    capped_dir = pathlib.Path(sys.argv[1])
    shaved_dir = pathlib.Path(sys.argv[2])
    master, feature = sys.argv[3], sys.argv[4]
    dropped = json.loads((shaved_dir / "dropped.json").read_text())

    tot = {"n": 0, "a_served": 0, "b_served": 0, "a_cost": 0, "b_cost": 0,
           "a_ms": 0.0, "b_ms": 0.0, "cap_viol": 0}
    print(f"{'inst':8} {'ship':>5} {'A_served':>8} {'B_served':>8} "
          f"{'A_cost':>8} {'B_cost':>8} {'A_ms':>6} {'B_ms':>6} {'capchk':>6}")
    for f in sorted(capped_dir.glob("*.json")):
        name = f.stem
        n_ship = len(json.loads(f.read_text())["shipments"])
        a_out, a_ms = solve(master, shaved_dir / f.name)
        b_out, b_ms = solve(feature, f)
        a_served = (2 * dropped[name]["kept"]
                    - a_out["summary"]["unassigned"]) // 2
        b_served = (2 * n_ship - b_out["summary"]["unassigned"]) // 2
        # Verify shaved output against the ORIGINAL caps.
        chk = subprocess.run(
            [sys.executable, "tests/max_transit_time/check_caps.py",
             str(f), "/dev/stdin", "-q"],
            input=json.dumps(a_out), capture_output=True, text=True)
        cap_ok = chk.returncode == 0
        print(f"{name:8} {n_ship:>5} {a_served:>8} {b_served:>8} "
              f"{a_out['summary']['cost']:>8} {b_out['summary']['cost']:>8} "
              f"{a_ms:>6.0f} {b_ms:>6.0f} {'ok' if cap_ok else 'VIOL':>6}")
        tot["n"] += n_ship
        tot["a_served"] += a_served
        tot["b_served"] += b_served
        tot["a_cost"] += a_out["summary"]["cost"]
        tot["b_cost"] += b_out["summary"]["cost"]
        tot["a_ms"] += a_ms
        tot["b_ms"] += b_ms
        tot["cap_viol"] += 0 if cap_ok else 1
    print(f"\nTOTAL shipments={tot['n']} workaround_served={tot['a_served']} "
          f"exact_served={tot['b_served']} "
          f"(+{tot['b_served'] - tot['a_served']})")
    print(f"cost: workaround={tot['a_cost']} exact={tot['b_cost']}")
    print(f"total median ms: workaround={tot['a_ms']:.0f} "
          f"exact={tot['b_ms']:.0f}")
    print(f"instances with cap violations in workaround output: "
          f"{tot['cap_viol']}")


if __name__ == "__main__":
    main()
