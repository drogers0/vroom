#!/usr/bin/env python3
"""Generate adversarial worst-case instances for max_transit_time benchmarking.

Stresses three paths blind to existing benchmarks:
  - nonzero setup times on every job
  - vehicle breaks (mid-shift and terminal/late)
  - very tight caps (5% above theoretical minimum) forcing maximal engine traffic

Formula:
  tw_forced_min = max(0, delivery.rt - (pickup.dt + SETUP + pickup.service))
  forced_min    = max(direct_travel_matrix, tw_forced_min)
  cap           = max(1, ceil(1.05 * forced_min))

Usage:
  python3 benchmarks/gen_worstcase.py              # pdp_100 only
  python3 benchmarks/gen_worstcase.py --pdp400     # also pdp_400
"""

import argparse
import json
import math
import os
import zipfile
from pathlib import Path

# ---- tunables -----------------------------------------------------------
SETUP = 10              # setup time added to every job (same units as Li&Lim TWs);
                        # small enough to preserve Li&Lim TW chaining feasibility
CAP_FACTOR = 1.10       # cap = ceil(CAP_FACTOR * forced_min)
MID_BREAK_SERVICE  = 15 # mid-shift break service duration
LATE_BREAK_SERVICE = 10 # terminal-area break service duration
BREAK_TW_WIDTH     = 240 # TW width for both breaks (wide enough to not
                         # deform stock feasibility; breaks stay mandatory)
BREAK_ID_OFFSET    = 10_000  # break IDs start here to avoid collision with node IDs
# -------------------------------------------------------------------------


def floor_dist(x1: int, y1: int, x2: int, y2: int) -> int:
    """Floor of Euclidean distance — matches how existing vroom instances are built."""
    return int(math.hypot(x1 - x2, y1 - y2))


def parse_lilim(text: str):
    """Parse Li&Lim PDP text format.

    Returns (num_vehicles, capacity, nodes) where nodes is a list of dicts
    keyed by: id, x, y, demand, rt, dt, service, pickup_col, delivery_col.
    """
    lines = [ln.strip() for ln in text.strip().split("\n") if ln.strip()]
    num_vehicles, capacity, _ = (int(v) for v in lines[0].split())
    nodes = []
    for line in lines[1:]:
        parts = line.split()
        if len(parts) < 9:
            continue
        nid, x, y, demand, rt, dt, service, pickup_col, delivery_col = (
            int(v) for v in parts[:9]
        )
        nodes.append(
            {
                "id": nid,
                "x": x,
                "y": y,
                "demand": demand,
                "rt": rt,
                "dt": dt,
                "service": service,
                "pickup_col": pickup_col,    # partner pickup node id (if this is delivery)
                "delivery_col": delivery_col, # partner delivery node id (if this is pickup)
            }
        )
    return num_vehicles, capacity, nodes


def build_matrix(nodes: list) -> list:
    """Build N×N integer travel-time matrix using floor(Euclidean)."""
    n = len(nodes)
    mat = [[0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i != j:
                mat[i][j] = floor_dist(
                    nodes[i]["x"], nodes[i]["y"],
                    nodes[j]["x"], nodes[j]["y"],
                )
    return mat


def make_vehicle_breaks(v_id: int, tw_start: int, tw_end: int) -> list:
    """Return the two break dicts for one vehicle.

    Mid-shift break: TW centred at midpoint of vehicle span.
    Late break: TW starting at 90 % of vehicle span (lands at or after the
    last job on most routes). Note: a late-window break WITHOUT a mid break
    makes packed Li&Lim instances entirely infeasible for stock vroom
    (reproduced on upstream master), so both are kept.
    """
    span = tw_end - tw_start
    mid = tw_start + span // 2
    half_w = BREAK_TW_WIDTH // 2
    mid_break = {
        "id": BREAK_ID_OFFSET + v_id * 2,
        "time_windows": [[max(tw_start, mid - half_w), min(tw_end, mid + half_w)]],
        "service": MID_BREAK_SERVICE,
    }
    late_start = tw_start + int(0.9 * span)
    late_break = {
        "id": BREAK_ID_OFFSET + v_id * 2 + 1,
        "time_windows": [[late_start, late_start + BREAK_TW_WIDTH]],
        "service": LATE_BREAK_SERVICE,
    }
    return [mid_break, late_break]


def convert(num_vehicles: int, capacity: int, nodes: list) -> dict:
    """Convert parsed Li&Lim data to a vroom-format JSON dict.

    Adds setup times, tight max_transit_time caps, and vehicle breaks.
    Returns (json_dict, diagnostics_list).
    """
    depot = nodes[0]
    assert depot["id"] == 0, "Expected depot at node 0"

    # Widen vehicle TW end to accommodate break service totals
    v_tw_start = depot["rt"]
    v_tw_end   = depot["dt"] + MID_BREAK_SERVICE + LATE_BREAK_SERVICE

    by_id = {n["id"]: n for n in nodes}
    mat   = build_matrix(nodes)

    # ---- shipments -------------------------------------------------------
    shipments  = []
    diagnostics = []

    for node in nodes:
        if node["demand"] <= 0:
            continue  # depot (0) or delivery node — process only pickups
        p = node
        delivery_id = p["delivery_col"]
        if delivery_id == 0:
            # No delivery partner listed — skip (shouldn't occur in valid input)
            continue
        d = by_id[delivery_id]

        # Tight cap: 5 % above the theoretical minimum achievable transit
        travel = mat[p["id"]][d["id"]]
        tw_forced_min = max(0, d["rt"] - (p["dt"] + SETUP + p["service"]))
        forced_min    = max(travel, tw_forced_min)
        cap           = max(1, math.ceil(CAP_FACTOR * forced_min))

        diagnostics.append(
            {
                "pickup":       p["id"],
                "delivery":     d["id"],
                "travel":       travel,
                "tw_forced_min": tw_forced_min,
                "forced_min":   forced_min,
                "cap":          cap,
            }
        )

        shipments.append(
            {
                "pickup": {
                    "id":            p["id"],
                    "location_index": p["id"],
                    "setup":         SETUP,
                    "service":       p["service"],
                    "time_windows":  [[p["rt"], p["dt"]]],
                },
                "delivery": {
                    "id":            d["id"],
                    "location_index": d["id"],
                    "setup":         SETUP,
                    "service":       d["service"],
                    "time_windows":  [[d["rt"], d["dt"]]],
                },
                "amount":           [abs(p["demand"])],
                "max_transit_time": cap,
            }
        )

    # ---- vehicles --------------------------------------------------------
    vehicles = []
    for v_id in range(num_vehicles):
        breaks = make_vehicle_breaks(v_id, v_tw_start, v_tw_end)
        vehicles.append(
            {
                "id":          v_id,
                "start_index": 0,
                "end_index":   0,
                "capacity":    [capacity],
                "time_window": [v_tw_start, v_tw_end],
                "breaks":      breaks,
            }
        )

    instance = {
        "vehicles":  vehicles,
        "shipments": shipments,
        "matrices":  {"car": {"durations": mat}},
    }
    return instance, diagnostics


def process_zip(zip_path: str, out_dir: Path, series_filter: str | None = "1"):
    """Process all matching .txt files from a Li&Lim zip.

    series_filter: if set, only process files whose basename second-to-last
    two characters match (e.g. '1' selects lc1xx, lr1xx, lrc1xx over lc2xx).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    all_diag = {}

    with zipfile.ZipFile(zip_path) as zf:
        for zname in zf.namelist():
            if not zname.endswith(".txt"):
                continue
            basename = Path(zname).stem  # e.g. "lc101"
            # Filter to 1xx series if requested (skip 201, 202, …)
            if series_filter:
                # Check that the digit group after the letters starts with the filter
                import re
                m = re.search(r"(\d+)$", basename)
                if not m or not m.group(1).startswith(series_filter):
                    continue

            text = zf.read(zname).decode()
            num_vehicles, capacity, nodes = parse_lilim(text)
            instance, diagnostics = convert(num_vehicles, capacity, nodes)

            out_path = out_dir / f"{basename}.json"
            out_path.write_text(json.dumps(instance, separators=(",", ":")))
            all_diag[basename] = diagnostics
            print(f"  wrote {out_path}  ({len(diagnostics)} shipments)")

    return all_diag


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pdp400", action="store_true",
        help="Also generate instances from pdp_400.zip"
    )
    parser.add_argument(
        "--all-series", action="store_true",
        help="Include 2xx series (wider TWs, looser caps) in addition to 1xx"
    )
    args = parser.parse_args()

    repo = Path(__file__).parent.parent  # project root
    bench = repo / "benchmarks"

    series_filter = None if args.all_series else "1"

    print("=== Generating pdp_100 worst-case instances ===")
    out_100 = bench / "instances_worstcase" / "100"
    diag_100 = process_zip(str(bench / "pdp_100.zip"), out_100, series_filter)

    if args.pdp400:
        print("\n=== Generating pdp_400 worst-case instances ===")
        out_400 = bench / "instances_worstcase" / "400"
        # pdp_400 has no series split — use None filter (all files)
        diag_400 = process_zip(str(bench / "pdp_400.zip"), out_400, series_filter=None)

    # Print summary of cap tightness
    all_diag = diag_100
    total = sum(len(v) for v in all_diag.values())
    tight = sum(
        1
        for diags in all_diag.values()
        for d in diags
        if d["forced_min"] > 0 and d["cap"] <= math.ceil(CAP_FACTOR * d["forced_min"])
    )
    zero_forced = sum(
        1
        for diags in all_diag.values()
        for d in diags
        if d["forced_min"] == 0
    )
    print(f"\nTotal shipments: {total}")
    print(f"  tight caps (forced_min > 0): {tight - zero_forced}")
    print(f"  cap=1 (forced_min=0, co-located same-TW): {zero_forced}")
    print(f"  SETUP added to every job: {SETUP}")
    print(f"  CAP_FACTOR: {CAP_FACTOR}")
    print(f"  Vehicle TW widened by: {MID_BREAK_SERVICE + LATE_BREAK_SERVICE} (break services)")
    print(f"  Mid-break TW width: {BREAK_TW_WIDTH}, service: {MID_BREAK_SERVICE}")
    print(f"  Late-break TW width: {BREAK_TW_WIDTH}, service: {LATE_BREAK_SERVICE}")


if __name__ == "__main__":
    main()
