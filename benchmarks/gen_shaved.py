#!/usr/bin/env python3
"""Generate the delivery-window-shaving workaround variant of a capped set.

For each shipment with max_transit_time, the classical workaround (absent an
explicit transit constraint) shaves the delivery window so that even an
earliest-possible pickup cannot exceed the cap:

    shaved_delivery_end = pickup_tw_start + pickup_action + cap

The cap field itself is removed (stock solvers reject unknown constraints by
ignoring them; here we strip it so the variant runs on stock master). When the
shave empties the delivery window entirely, the request is unservable under
the workaround; the shipment is dropped from the instance and recorded in a
sidecar so the comparison can count it as workaround-unassigned.

Usage: gen_shaved.py SRC_DIR DST_DIR
"""

import json
import pathlib
import sys


def shave_instance(src: pathlib.Path, dst: pathlib.Path) -> tuple[int, int]:
    data = json.loads(src.read_text())
    kept = []
    dropped = 0
    for s in data.get("shipments", []):
        cap = s.pop("max_transit_time", None)
        if cap is None:
            kept.append(s)
            continue
        p = s["pickup"]
        d = s["delivery"]
        p_start = p.get("time_windows", [[0, None]])[0][0]
        action = p.get("setup", 0) + p.get("service", 0)
        limit = p_start + action + cap
        d_tw = d.get("time_windows")
        if d_tw is None:
            d["time_windows"] = [[0, limit]]
            kept.append(s)
            continue
        lo, hi = d_tw[0]
        if limit < lo:
            dropped += 1
            continue
        d["time_windows"] = [[lo, min(hi, limit)]]
        kept.append(s)
    data["shipments"] = kept
    dst.write_text(json.dumps(data))
    return len(kept), dropped


def main() -> None:
    src_dir = pathlib.Path(sys.argv[1])
    dst_dir = pathlib.Path(sys.argv[2])
    dst_dir.mkdir(parents=True, exist_ok=True)
    sidecar = {}
    for f in sorted(src_dir.glob("*.json")):
        kept, dropped = shave_instance(f, dst_dir / f.name)
        sidecar[f.stem] = {"kept": kept, "dropped": dropped}
        print(f"{f.stem}: kept={kept} dropped={dropped}")
    (dst_dir / "dropped.json").write_text(json.dumps(sidecar, indent=1))


if __name__ == "__main__":
    main()
