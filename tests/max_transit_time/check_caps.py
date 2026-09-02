#!/usr/bin/env python3
"""Property check: every served shipment respects its transit cap(s).
transit_time = service_start(delivery) - departure(pickup), where
service_start = arrival + waiting_time and departure = service_start + setup + service
(setup/service taken from the output step itself).
Caps come from shipment max_transit_time and from step-level service_within
on delivery steps in vehicle plan-mode routes.
Usage: check_caps.py input.json output.json [-q]"""
import json, sys

inp = json.load(open(sys.argv[1]))
out = json.load(open(sys.argv[2]))
quiet = "-q" in sys.argv

pickup_of_delivery = {
    s["delivery"]["id"]: s["pickup"]["id"] for s in inp.get("shipments", [])
}

caps = []  # (pickup_id, delivery_id, cap, source)
for s in inp.get("shipments", []):
    if "max_transit_time" in s:
        caps.append((s["pickup"]["id"], s["delivery"]["id"],
                     s["max_transit_time"], "shipment"))
for v in inp.get("vehicles", []):
    for st in v.get("steps", []):
        if st.get("type") == "delivery" and "service_within" in st:
            did = st["id"]
            caps.append((pickup_of_delivery[did], did,
                         st["service_within"], "step"))

fail = 0
for route in out.get("routes", []):
    info = {}
    for st in route.get("steps", []):
        if st["type"] in ("pickup", "delivery"):
            start = st["arrival"] + st.get("waiting_time", 0)
            dep = start + st.get("setup", 0) + st.get("service", 0)
            info[(st["type"], st["id"])] = (start, dep)
    for pid, did, cap, source in caps:
        if ("pickup", pid) in info and ("delivery", did) in info:
            ride = info[("delivery", did)][0] - info[("pickup", pid)][1]
            ok = ride <= cap
            if not quiet or not ok:
                print(f"shipment p{pid}->d{did} ({source}): ride={ride} cap={cap} {'OK' if ok else 'VIOLATION'}")
            if not ok:
                fail += 1
sys.exit(1 if fail else 0)
