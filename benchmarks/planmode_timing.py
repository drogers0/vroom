#!/usr/bin/env python3
"""Plan-mode (-c) LP timing and parity evidence.

For each instance: solve it, convert the solution to a plan-mode input
(vehicle steps, via roundtrip_check's builder), then time `-c` on that plan
input with caps present vs stripped (medians of REPS). Optionally, when a
reference binary is given, verify that the caps-stripped plan output is
byte-identical to the reference (a build of upstream master WITH libglpk;
plan mode errors out entirely on a GLPK-less build).

This is the rerunnable source for the design doc's plan-mode claims: the
soft transit-excess terms do not move LP solve time measurably, and with
the field absent the LP and its output are untouched.

Usage: planmode_timing.py BINARY DIR [DIR...] [--reference MASTER_BINARY]
"""

import copy
import importlib.util
import json
import pathlib
import statistics
import subprocess
import sys
import time

REPS = 3

spec = importlib.util.spec_from_file_location(
    "rt", pathlib.Path(__file__).parent / "roundtrip_check.py")
rt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rt)


def timed_plan(binary: str, plan: dict):
    times = []
    out = None
    for _ in range(REPS):
        t0 = time.time()
        r = subprocess.run([binary, "-c", "-i", "/dev/stdin"],
                           input=json.dumps(plan), capture_output=True,
                           text=True)
        times.append((time.time() - t0) * 1000)
        out = json.loads(r.stdout)
    return statistics.median(times), out


def normalized(d: dict) -> str:
    d = copy.deepcopy(d)
    d.get("summary", {}).pop("computing_times", None)
    for r in d.get("routes", []):
        r.pop("computing_times", None)
    return json.dumps(d, sort_keys=True)


def main() -> None:
    args = sys.argv[1:]
    reference = None
    if "--reference" in args:
        i = args.index("--reference")
        reference = args[i + 1]
        args = args[:i] + args[i + 2:]
    binary = args[0]
    ratios = []
    parity_ok = 0
    parity_n = 0
    for d in args[1:]:
        for f in sorted(pathlib.Path(d).glob("*.json")):
            if f.name == "dropped.json":
                continue
            inst = json.loads(f.read_text())
            solved = rt.solve(binary, f)
            out = solved[0] if isinstance(solved, tuple) else solved
            plan = rt.build_plan_input(inst, out)
            capped_ms, _ = timed_plan(binary, plan)
            stripped = copy.deepcopy(plan)
            for s in stripped.get("shipments", []):
                s.pop("max_transit_time", None)
            stripped_ms, stripped_out = timed_plan(binary, stripped)
            ratios.append(capped_ms / max(stripped_ms, 0.001))
            line = (f"{f.stem}: capped={capped_ms:.0f}ms "
                    f"stripped={stripped_ms:.0f}ms "
                    f"ratio={capped_ms / stripped_ms:.2f}")
            if reference:
                _, ref_out = timed_plan(reference, stripped)
                parity_n += 1
                same = normalized(stripped_out) == normalized(ref_out)
                parity_ok += same
                line += f" reference_parity={'ok' if same else 'DIFF'}"
            print(line)
    import math
    print(f"GEOMEAN capped/stripped: "
          f"{math.exp(sum(math.log(r) for r in ratios) / len(ratios)):.2f} "
          f"over {len(ratios)}")
    if reference:
        print(f"caps-stripped parity vs reference: {parity_ok}/{parity_n}")


if __name__ == "__main__":
    main()
