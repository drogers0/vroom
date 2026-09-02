#!/usr/bin/env python3
"""Generate quadratic-corner solve instances: a single vehicle, one long
chained route where every second stop is a constrained pickup.

N shipments on a line (pickup at 2i, delivery at 2i+1, leg 10s), pickup
windows open, each delivery window starting late enough to force on-board
waiting that only a pickup delay can remove, caps set to the leg travel plus
a small margin so every insertion is engine-decided. One vehicle, so the
solver is forced into a single ever-growing route and every validity check
carries all committed pairs.

Usage: gen_corner.py OUT_DIR N [N...]
"""

import json
import pathlib
import sys


def gen(n: int) -> dict:
    locs = 2 * n + 1
    dur = [[10 * abs(a - b) for b in range(locs)] for a in range(locs)]
    shipments = []
    for i in range(n):
        asap_arrival = 10 * (2 * i + 1)
        shipments.append({
            "amount": [1],
            "pickup": {"id": 2 * i + 1, "location_index": 2 * i,
                       "service": 0},
            "delivery": {"id": 2 * i + 2, "location_index": 2 * i + 1,
                         "service": 0,
                         "time_windows": [[asap_arrival + 50,
                                           asap_arrival + 100000]]},
            "max_transit_time": 15,
        })
    return {
        "matrices": {"car": {"durations": dur}},
        "vehicles": [{"id": 1, "start_index": 0, "profile": "car",
                      "capacity": [n]}],
        "shipments": shipments,
    }


def main() -> None:
    out = pathlib.Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    for arg in sys.argv[2:]:
        n = int(arg)
        (out / f"corner_{n:04d}.json").write_text(json.dumps(gen(n)))
        print(f"corner_{n:04d}: {n} shipments, route length {2 * n}")


if __name__ == "__main__":
    main()
