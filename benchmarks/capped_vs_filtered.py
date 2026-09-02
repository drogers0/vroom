#!/usr/bin/env python3
"""Quality evidence: exact cap enforcement vs. solve-then-filter baseline.

For each alpha2 instance (with max_transit_time caps):
  (a) Capped solve: solve with bin/vroom -t 1; record served count and cost.
  (b) Uncapped baseline: solve the matching benchmarks/instances/base twin
      (same instance without caps), then post-hoc drop every shipment whose
      transit time in that solution exceeds the alpha2 cap.
      Transit time = service_start(delivery) - departure(pickup)
        where service_start = arrival + waiting_time
              departure     = service_start + setup + service
      Surviving count = shipments whose transit time fits within the alpha2 cap.
      NOTE: the filtered cost is reported AS-IS from the base solve; it overstates
      efficiency because dropped shipments' travel is still counted.

Point: exact enforcement (capped solve) serves strictly more shipments than
       solving blind and discarding violators.

Writes result table to benchmarks/results_phase8/E4.txt.

Usage:
  python3 benchmarks/capped_vs_filtered.py
          [--alpha2-dir benchmarks/instances/alpha2]
          [--base-dir   benchmarks/instances/base]
          [--vroom      bin/vroom]
          [--results-dir benchmarks/results_phase8]
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


def solve_instance(vroom_bin: str, inst_path: Path):
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


def count_served(out: dict) -> int:
    """Count served shipments = number of pickup steps across all routes."""
    return sum(
        1 for r in out.get("routes", [])
        for st in r.get("steps", []) if st["type"] == "pickup"
    )


def get_cost(out: dict) -> int:
    """Return total solution cost from summary."""
    return out.get("summary", {}).get("cost", 0)


def build_step_info(out: dict) -> dict:
    """Return dict: (type, id) -> (service_start, departure)."""
    info = {}
    for route in out.get("routes", []):
        for st in route.get("steps", []):
            if st["type"] in ("pickup", "delivery"):
                start = st["arrival"] + st.get("waiting_time", 0)
                dep = start + st.get("setup", 0) + st.get("service", 0)
                info[(st["type"], st["id"])] = (start, dep)
    return info


def count_filtered_surviving(alpha2_inst: dict, base_out: dict) -> int:
    """Count base shipments that survive the post-hoc cap filter.

    For each shipment with max_transit_time in alpha2_inst, check whether the
    transit time in base_out satisfies the cap. Unserved shipments (not in
    base_out routes) are not counted either way.
    """
    caps = {}
    for s in alpha2_inst.get("shipments", []):
        if "max_transit_time" in s:
            caps[s["pickup"]["id"]] = (s["delivery"]["id"], s["max_transit_time"])

    info = build_step_info(base_out)
    surviving = 0
    for pid, (did, cap) in caps.items():
        if ("pickup", pid) not in info or ("delivery", did) not in info:
            # not served in base solution
            continue
        ride = info[("delivery", did)][0] - info[("pickup", pid)][1]
        if ride <= cap:
            surviving += 1
    return surviving


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--alpha2-dir", default="benchmarks/instances/alpha2")
    parser.add_argument("--base-dir", default="benchmarks/instances/base")
    parser.add_argument("--vroom", default="bin/vroom")
    parser.add_argument("--results-dir", default="benchmarks/results_phase8")
    args = parser.parse_args()

    alpha2_dir = Path(args.alpha2_dir)
    base_dir = Path(args.base_dir)
    results_dir = Path(args.results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)

    files = sorted(alpha2_dir.glob("*.json"))
    if not files:
        print(f"No JSON files in {alpha2_dir}", file=sys.stderr)
        sys.exit(1)

    header = (f"{'instance':<12} {'capped_served':>14} {'capped_cost':>12} "
              f"{'filtered_served':>16} {'base_cost':>11}  note")
    sep = "-" * 80

    rows = []
    tot_capped_served = 0
    tot_capped_cost = 0
    tot_filtered_served = 0
    tot_base_cost = 0
    errors = 0

    for alpha2_path in files:
        name = alpha2_path.stem
        base_path = base_dir / alpha2_path.name

        alpha2_inst = json.loads(alpha2_path.read_text())

        # (a) capped solve
        capped_out, _ = solve_instance(args.vroom, alpha2_path)
        if capped_out is None:
            rows.append(f"{name:<12} {'SOLVE_ERR':>14}")
            errors += 1
            continue
        capped_served = count_served(capped_out)
        capped_cost = get_cost(capped_out)

        # (b) base solve + filter
        if not base_path.exists():
            rows.append(
                f"{name:<12} {capped_served:>14} {capped_cost:>12} {'NO_BASE':>16} {'N/A':>11}"
            )
            errors += 1
            continue
        base_out, _ = solve_instance(args.vroom, base_path)
        if base_out is None:
            rows.append(
                f"{name:<12} {capped_served:>14} {capped_cost:>12} {'BASE_ERR':>16} {'N/A':>11}"
            )
            errors += 1
            continue

        filtered_served = count_filtered_surviving(alpha2_inst, base_out)
        base_cost = get_cost(base_out)

        delta = capped_served - filtered_served
        note = f"capped+{delta}" if delta >= 0 else f"capped{delta}"

        tot_capped_served += capped_served
        tot_capped_cost += capped_cost
        tot_filtered_served += filtered_served
        tot_base_cost += base_cost

        rows.append(
            f"{name:<12} {capped_served:>14} {capped_cost:>12} "
            f"{filtered_served:>16} {base_cost:>11}  {note}"
        )

    total_line = (
        f"{'TOTAL':<12} {tot_capped_served:>14} {tot_capped_cost:>12} "
        f"{tot_filtered_served:>16} {tot_base_cost:>11}"
    )
    delta_total = tot_capped_served - tot_filtered_served
    note_total = (f"  capped serves {delta_total:+d} more shipments than solve-then-filter "
                  f"({'advantage' if delta_total > 0 else 'no advantage'})")

    lines = [
        "E4 — Quality evidence: capped solve vs. uncapped-then-filtered baseline",
        "=" * 80,
        "NOTE: base_cost includes dropped shipments' travel — overstates efficiency.",
        "=" * 80,
        header, sep,
    ]
    lines += rows
    lines += [sep, total_line, "", note_total]

    output = "\n".join(lines)
    results_path = results_dir / "E4.txt"
    results_path.write_text(output)
    print(output)
    print(f"\nResults saved: {results_path}")


if __name__ == "__main__":
    main()
