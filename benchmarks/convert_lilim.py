#!/usr/bin/env python3
"""
Convert Li&Lim PDPTW .txt instances to VROOM JSON.

Li&Lim format:
  Line 1:   K  Q  S        (vehicles, capacity, speed)
  Line 2+:  id x y demand tw_start tw_end service pickup_partner delivery_partner
  - id 0 = depot (demand 0)
  - positive demand = pickup; col delivery_partner (col 8) = sibling delivery id
  - negative demand = delivery; col pickup_partner (col 7) = sibling pickup id

Duration matrix: Euclidean distance truncated to integer (standard PDPTW convention).

--alpha ALPHA: for each shipment, max_transit_time = ceil(alpha * max(direct_duration, tw_forced_min))
               where direct_duration = duration[pickup_loc][delivery_loc]
               and tw_forced_min = max(0, delivery_tw_start - pickup_tw_end - pickup_service)
               (route-independent minimum ride forced by the time windows)
"""

import argparse
import json
import math
import sys
from pathlib import Path


def parse_lilim(path: Path):
    lines = path.read_text().strip().splitlines()
    header = lines[0].split()
    K, Q, S = int(header[0]), int(header[1]), int(header[2])

    nodes = {}  # id -> dict
    for line in lines[1:]:
        parts = line.split()
        if not parts:
            continue
        nid = int(parts[0])
        nodes[nid] = {
            "id": nid,
            "x": float(parts[1]),
            "y": float(parts[2]),
            "demand": int(parts[3]),
            "tw_start": int(parts[4]),
            "tw_end": int(parts[5]),
            "service": int(parts[6]),
            "pickup_partner": int(parts[7]),   # non-zero for deliveries
            "delivery_partner": int(parts[8]), # non-zero for pickups
        }

    return K, Q, S, nodes


def euclidean_truncated(x1, y1, x2, y2):
    return int(math.sqrt((x1 - x2) ** 2 + (y1 - y2) ** 2))


def build_duration_matrix(nodes_ordered):
    n = len(nodes_ordered)
    matrix = []
    for i in range(n):
        row = []
        ni = nodes_ordered[i]
        for j in range(n):
            nj = nodes_ordered[j]
            row.append(euclidean_truncated(ni["x"], ni["y"], nj["x"], nj["y"]))
        matrix.append(row)
    return matrix


def convert(src: Path, dst: Path, alpha: float | None = None,
            cap_fraction: float = 1.0):
    K, Q, S, nodes = parse_lilim(src)

    # Ordered node list: depot (id=0) first, then tasks by id
    nodes_ordered = [nodes[i] for i in sorted(nodes.keys())]
    # location_index = position in nodes_ordered = node id (since ids are 0-based consecutive)
    loc_index = {n["id"]: idx for idx, n in enumerate(nodes_ordered)}

    depot = nodes[0]
    duration_matrix = build_duration_matrix(nodes_ordered)

    # Build shipments: iterate pickup nodes (positive demand) sorted by id.
    pickup_ids = sorted(nid for nid, node in nodes.items() if node["demand"] > 0)
    cap_count = math.ceil(len(pickup_ids) * cap_fraction)
    capped_set = set(pickup_ids[:cap_count])

    shipments = []
    for nid in pickup_ids:
        node = nodes[nid]
        pickup_node = node
        delivery_id = node["delivery_partner"]
        delivery_node = nodes[delivery_id]

        shipment = {
            "pickup": {
                "id": pickup_node["id"],
                "location_index": loc_index[pickup_node["id"]],
                "service": pickup_node["service"],
                "time_windows": [[pickup_node["tw_start"], pickup_node["tw_end"]]],
            },
            "delivery": {
                "id": delivery_node["id"],
                "location_index": loc_index[delivery_node["id"]],
                "service": delivery_node["service"],
                "time_windows": [[delivery_node["tw_start"], delivery_node["tw_end"]]],
            },
            "amount": [pickup_node["demand"]],
        }

        if alpha is not None and nid in capped_set:
            pi = loc_index[pickup_node["id"]]
            di = loc_index[delivery_node["id"]]
            direct = duration_matrix[pi][di]
            tw_forced_min = max(0, delivery_node["tw_start"] - pickup_node["tw_end"] - pickup_node["service"])
            shipment["max_transit_time"] = math.ceil(alpha * max(direct, tw_forced_min))

        shipments.append(shipment)

    # Vehicles
    vehicles = []
    for vid in range(K):
        vehicles.append({
            "id": vid,
            "start_index": loc_index[0],
            "end_index": loc_index[0],
            "capacity": [Q],
            "time_window": [0, depot["tw_end"]],
        })

    output = {
        "vehicles": vehicles,
        "shipments": shipments,
        "matrices": {
            "car": {
                "durations": duration_matrix,
            }
        },
    }

    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(output))
    return len(shipments)


def main():
    parser = argparse.ArgumentParser(description="Li&Lim PDPTW -> VROOM JSON converter")
    parser.add_argument("input", type=Path, help="Source .txt instance file")
    parser.add_argument("output", type=Path, help="Destination .json file")
    parser.add_argument(
        "--alpha",
        type=float,
        default=None,
        help="max_transit_time multiplier: ceil(alpha * max(direct_duration, tw_forced_min))",
    )
    parser.add_argument(
        "--cap-fraction",
        type=float,
        default=1.0,
        dest="cap_fraction",
        help="Fraction of shipments (by pickup id order) to cap with max_transit_time (default: 1.0 = all)",
    )
    args = parser.parse_args()

    n = convert(args.input, args.output, args.alpha, args.cap_fraction)
    print(f"Wrote {args.output} ({n} shipments)", file=sys.stderr)


if __name__ == "__main__":
    main()
