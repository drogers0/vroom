#!/usr/bin/env python3
"""Plan-mode round-trip check: solve -> plan-mode input -> check for violations.

For each instance in an input directory:
  1. Solve with bin/vroom -t 1.
  2. Convert the solution into a plan-mode input: same vehicles/shipments/matrices,
     each vehicle gains a "steps" array built from the solve output (start/end omitted;
     pickups use {"type":"pickup","id":N}, deliveries {"type":"delivery","id":N},
     breaks {"type":"break","id":N}).
  3. Run bin/vroom -c on the plan-mode input.
  4. Count max_transit_time violations from the plan output's violations arrays.

Expected: zero violations on every instance (a solution the solver produced must be
self-consistent under plan-mode re-evaluation).

If any counterexample is found, the offending instance and plan input are saved
to results_dir/ and clearly flagged.

Writes result table to benchmarks/results_phase8/E3.txt.

Usage:
  python3 benchmarks/roundtrip_check.py INST_DIR [INST_DIR2 ...]
                                        [--vroom bin/vroom]
                                        [--results-dir benchmarks/results_phase8]
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


def solve(vroom_bin: str, inst_path: Path):
    """Return (output_dict, elapsed_ms) or (None, elapsed_ms)."""
    t0 = time.perf_counter()
    try:
        r = subprocess.run(
            [vroom_bin, "-t", "1", "-i", str(inst_path)],
            capture_output=True, text=True, timeout=120
        )
        ms = int((time.perf_counter() - t0) * 1000)
        if r.returncode != 0:
            return None, ms
        return json.loads(r.stdout), ms
    except Exception:
        return None, int((time.perf_counter() - t0) * 1000)


def build_plan_input(inst: dict, solve_out: dict) -> dict:
    """Construct plan-mode input from original instance + solve output.

    Adds vehicle steps from the solved routes (omitting start/end steps).
    Empty routes (vehicle not used) get no steps key.
    """
    import copy
    plan = copy.deepcopy(inst)

    route_by_vehicle = {r["vehicle"]: r for r in solve_out.get("routes", [])}

    for v in plan.get("vehicles", []):
        vid = v["id"]
        if vid not in route_by_vehicle:
            continue
        route = route_by_vehicle[vid]
        steps = []
        for st in route.get("steps", []):
            stype = st["type"]
            if stype in ("start", "end"):
                continue
            step_entry = {"type": stype}
            if "id" in st:
                step_entry["id"] = st["id"]
            steps.append(step_entry)
        if steps:
            v["steps"] = steps

    return plan


def run_plan(vroom_bin: str, plan: dict):
    """Run bin/vroom -c on plan dict. Return (output_dict, elapsed_ms) or (None, ms)."""
    t0 = time.perf_counter()
    try:
        r = subprocess.run(
            [vroom_bin, "-c"],
            input=json.dumps(plan),
            capture_output=True, text=True, timeout=120
        )
        ms = int((time.perf_counter() - t0) * 1000)
        if r.returncode != 0:
            return None, ms
        return json.loads(r.stdout), ms
    except Exception:
        return None, int((time.perf_counter() - t0) * 1000)


def count_max_transit_violations(plan_out: dict) -> int:
    """Count max_transit_time violations at route level and step level."""
    viol = 0
    for route in plan_out.get("routes", []):
        for v_obj in route.get("violations", []):
            if v_obj.get("cause") == "max_transit_time":
                viol += 1
        for st in route.get("steps", []):
            for v_obj in st.get("violations", []):
                if v_obj.get("cause") == "max_transit_time":
                    viol += 1
    for v_obj in plan_out.get("summary", {}).get("violations", []):
        if v_obj.get("cause") == "max_transit_time":
            viol += 1
    return viol


def process_dir(inst_dir: Path, vroom_bin: str, results_dir: Path):
    """Process all JSON instances in inst_dir. Return list of row dicts."""
    rows = []
    for inst_path in sorted(inst_dir.glob("*.json")):
        inst = json.loads(inst_path.read_text())
        n_routes_solve = 0

        # step 1: solve
        solve_out, ms_solve = solve(vroom_bin, inst_path)
        if solve_out is None:
            rows.append({"name": inst_path.stem, "dir": inst_dir.name,
                         "routes": 0, "violations": "SOLVE_ERR", "flag": ""})
            continue

        n_routes_solve = len(solve_out.get("routes", []))

        # step 2: build plan input
        plan = build_plan_input(inst, solve_out)

        # step 3: run plan mode
        plan_out, ms_plan = run_plan(vroom_bin, plan)
        if plan_out is None:
            rows.append({"name": inst_path.stem, "dir": inst_dir.name,
                         "routes": n_routes_solve, "violations": "PLAN_ERR", "flag": ""})
            continue

        # step 4: count violations
        viol = count_max_transit_violations(plan_out)
        flag = ""
        if viol > 0:
            flag = "*** COUNTEREXAMPLE ***"
            # save offending files
            (results_dir / f"COUNTEREXAMPLE_{inst_dir.name}_{inst_path.stem}_inst.json").write_text(
                inst_path.read_text())
            (results_dir / f"COUNTEREXAMPLE_{inst_dir.name}_{inst_path.stem}_plan.json").write_text(
                json.dumps(plan, indent=2))
            print(f"  COUNTEREXAMPLE: {inst_dir.name}/{inst_path.stem} violations={viol}",
                  file=sys.stderr)

        rows.append({"name": inst_path.stem, "dir": inst_dir.name,
                     "routes": n_routes_solve, "violations": viol, "flag": flag})

    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inst_dirs", nargs="*",
                        default=["benchmarks/instances/alpha2",
                                 "benchmarks/instances_multiwindow"],
                        metavar="INST_DIR")
    parser.add_argument("--vroom", default="bin/vroom")
    parser.add_argument("--results-dir", default="benchmarks/results_phase8")
    args = parser.parse_args()

    results_dir = Path(args.results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)

    header = f"{'dir':<20} {'instance':<12} {'routes':>7} {'violations':>11} {'flag'}"
    sep = "-" * 60
    all_rows = []

    for d in args.inst_dirs:
        inst_dir = Path(d)
        if not inst_dir.exists():
            print(f"WARNING: {inst_dir} does not exist, skipping", file=sys.stderr)
            continue
        print(f"Processing {inst_dir} ...", file=sys.stderr)
        rows = process_dir(inst_dir, args.vroom, results_dir)
        all_rows.extend(rows)

    lines = ["E3 — Plan-mode round-trip check", "=" * 60, header, sep]
    total_viol = 0
    any_error = False
    for row in all_rows:
        viol_str = str(row["violations"])
        if isinstance(row["violations"], int):
            total_viol += row["violations"]
        else:
            any_error = True
        lines.append(
            f"{row['dir']:<20} {row['name']:<12} {row['routes']:>7} {viol_str:>11} {row['flag']}"
        )

    lines.append(sep)
    lines.append(f"Total max_transit_time violations: {total_viol}")
    if any_error:
        lines.append("WARNING: some instances had solve/plan errors (see SOLVE_ERR/PLAN_ERR above)")
    if total_viol == 0 and not any_error:
        lines.append("PASS: zero violations on all instances.")
    else:
        lines.append("FAIL: counterexamples found — see results_phase8/COUNTEREXAMPLE_* files.")

    output = "\n".join(lines)
    results_path = results_dir / "E3.txt"
    results_path.write_text(output)
    print(output)
    print(f"\nResults saved: {results_path}")


if __name__ == "__main__":
    main()
