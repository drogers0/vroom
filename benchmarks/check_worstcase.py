#!/usr/bin/env python3
"""Verify worst-case instance solve output for max_transit_time and break-TW constraints.

Checks:
  (a) Every served shipment: transit_time = service_start(delivery) - departure(pickup) <= cap
      departure(pickup) = arrival + waiting_time + setup + service  (step output fields)
      setup is skipped (= 0) when the previous step in the route is at the same location,
      but we read setup directly from the output step so this is handled automatically.
  (b) Every break step: service_start falls within at least one of the break's declared TWs.
  (c) Reports counts: served, unassigned, cap violations, break violations.

Exit code: 0 on clean, 1 if any violation found.

Usage:
  check_worstcase.py input.json output.json [-q]
    -q  suppress per-shipment OK lines; always print violations
"""

import json
import sys


def load(path):
    with open(path) as f:
        return json.load(f)


def check(inp_path: str, out_path: str, quiet: bool) -> int:
    inp = load(inp_path)
    out = load(out_path)

    # ---- index input caps ------------------------------------------------
    # cap keyed by pickup_id -> (delivery_id, max_transit_time)
    caps = {}
    for s in inp.get("shipments", []):
        if "max_transit_time" in s:
            caps[s["pickup"]["id"]] = (s["delivery"]["id"], s["max_transit_time"])

    # ---- index break TWs from input vehicles -----------------------------
    # break_tws: break_id -> list of [start, end]
    break_tws = {}
    for v in inp.get("vehicles", []):
        for br in v.get("breaks", []):
            break_tws[br["id"]] = br["time_windows"]

    # ---- walk output routes ----------------------------------------------
    served_ids = set()   # pickup IDs that appear in routes
    cap_violations  = 0
    break_violations = 0

    for route in out.get("routes", []):
        # Collect step info keyed by (type, id)
        step_info = {}  # (type, id) -> (service_start, departure)

        for st in route.get("steps", []):
            stype = st["type"]
            if stype in ("pickup", "delivery"):
                sid = st["id"]
                service_start = st["arrival"] + st.get("waiting_time", 0)
                departure     = service_start + st.get("setup", 0) + st.get("service", 0)
                step_info[(stype, sid)] = (service_start, departure)
                if stype == "pickup":
                    served_ids.add(sid)

            elif stype == "break":
                bid = st["id"]
                service_start = st["arrival"] + st.get("waiting_time", 0)
                tws = break_tws.get(bid)
                if tws is None:
                    # break id not in input — can't validate
                    print(f"  WARN: break id {bid} not found in input vehicles")
                    continue
                in_window = any(tw[0] <= service_start <= tw[1] for tw in tws)
                if not in_window:
                    tw_str = ", ".join(f"[{tw[0]},{tw[1]}]" for tw in tws)
                    print(
                        f"  BREAK VIOLATION: break {bid} "
                        f"service_start={service_start} not in {tw_str}"
                    )
                    break_violations += 1
                elif not quiet:
                    tw_str = ", ".join(f"[{tw[0]},{tw[1]}]" for tw in tws)
                    print(
                        f"  break {bid}: service_start={service_start} "
                        f"in {tw_str} OK"
                    )

        # Cap check for this route
        for pid, (did, cap) in caps.items():
            p_key = ("pickup", pid)
            d_key = ("delivery", did)
            if p_key not in step_info or d_key not in step_info:
                continue  # shipment partially or wholly unserved — counted separately
            _, p_departure       = step_info[p_key]
            d_service_start, _  = step_info[d_key]
            transit = d_service_start - p_departure
            ok = transit <= cap
            if not quiet or not ok:
                print(
                    f"  shipment p{pid}->d{did}: "
                    f"transit={transit} cap={cap} {'OK' if ok else 'VIOLATION'}"
                )
            if not ok:
                cap_violations += 1

    # ---- unassigned count ------------------------------------------------
    unassigned_shipments = []
    for s in inp.get("shipments", []):
        pid = s["pickup"]["id"]
        if pid not in served_ids:
            unassigned_shipments.append(pid)

    served_count    = len(served_ids)
    unassigned_count = len(unassigned_shipments)

    # ---- summary ---------------------------------------------------------
    print(f"\n--- Summary ---")
    print(f"  Shipments served:    {served_count}")
    print(f"  Shipments unassigned:{unassigned_count}")
    if unassigned_shipments:
        print(f"  Unassigned pickup IDs: {sorted(unassigned_shipments)}")
    print(f"  Cap violations:      {cap_violations}")
    print(f"  Break violations:    {break_violations}")

    any_fail = cap_violations > 0 or break_violations > 0
    return 1 if any_fail else 0


def main():
    quiet = "-q" in sys.argv
    args  = [a for a in sys.argv[1:] if not a.startswith("-")]
    if len(args) < 2:
        print("Usage: check_worstcase.py input.json output.json [-q]", file=sys.stderr)
        sys.exit(2)
    sys.exit(check(args[0], args[1], quiet))


if __name__ == "__main__":
    main()
