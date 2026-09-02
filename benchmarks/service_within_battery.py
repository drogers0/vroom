#!/usr/bin/env python3
"""service_within evidence battery over full instance sets.

For each instance: solve it, build the plan-mode input from the solution,
then:

1. PARITY (key-free): run the plan input as-is on the feature binary and a
   pre-change reference binary; normalized outputs must match. Covers the
   soft-pair collection restructure on inputs with and without shipment
   caps.
2. TIMING (key-free): median plan-mode wall time, feature vs reference.
3. FEED-BACK (key-present): add service_within to every delivery step of a
   served shipment, set to the transit the solve schedule realized (the
   tightest cap known to be satisfiable for this route order). The plan run
   must succeed with zero max_transit_time violations and every realized
   transit within its key. Median wall time gives the hard-machinery cost
   (Sigma_Y unbounded + maximal horizon margins) vs the key-free run.

Usage: service_within_battery.py FEATURE_BIN REFERENCE_BIN DIR [DIR...]
"""

import copy
import importlib.util
import json
import math
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


def timed_plan(binary, plan):
    times = []
    out = None
    code = 0
    for _ in range(REPS):
        t0 = time.time()
        r = subprocess.run([binary, "-c", "-t", "1", "-i", "/dev/stdin"],
                           input=json.dumps(plan), capture_output=True,
                           text=True)
        times.append((time.time() - t0) * 1000)
        code = r.returncode
        out = json.loads(r.stdout) if r.returncode == 0 else None
    return statistics.median(times), out, code


def normalized(d):
    d = copy.deepcopy(d)
    d.get("summary", {}).pop("computing_times", None)
    for r in d.get("routes", []):
        r.pop("computing_times", None)
    return json.dumps(d, sort_keys=True)


def transits(out, delivery_of):
    """(pickup_id -> realized transit) from an output's routes."""
    res = {}
    for route in out.get("routes", []):
        info = {}
        for st in route.get("steps", []):
            if st["type"] in ("pickup", "delivery"):
                start = st["arrival"] + st.get("waiting_time", 0)
                dep = start + st.get("setup", 0) + st.get("service", 0)
                info[(st["type"], st["id"])] = (start, dep)
        for (typ, pid), (_, dep) in list(info.items()):
            if typ != "pickup":
                continue
            did = delivery_of.get(pid)
            if did is not None and ("delivery", did) in info:
                res[pid] = info[("delivery", did)][0] - dep
    return res


def main():
    feature, reference = sys.argv[1], sys.argv[2]
    parity_ok = parity_n = 0
    fb_ok = fb_n = 0
    free_ratios, hard_ratios = [], []
    for d in sys.argv[3:]:
        for f in sorted(pathlib.Path(d).glob("*.json")):
            inst = json.loads(f.read_text())
            out, _ = rt.solve(feature, f)
            if out is None:
                print(f"{f.stem}: SOLVE FAILED")
                continue
            plan = rt.build_plan_input(inst, out)

            feat_ms, feat_out, rc = timed_plan(feature, plan)
            ref_ms, ref_out, _ = timed_plan(reference, plan)
            parity_n += 1
            same = rc == 0 and normalized(feat_out) == normalized(ref_out)
            parity_ok += same
            free_ratios.append(feat_ms / max(ref_ms, 0.001))

            delivery_of = {s["pickup"]["id"]: s["delivery"]["id"]
                           for s in inst.get("shipments", [])}
            pickup_of = {d: p for p, d in delivery_of.items()}
            realized = transits(out, delivery_of)
            hard = copy.deepcopy(plan)
            keys = 0
            for v in hard.get("vehicles", []):
                for s in v.get("steps", []):
                    if (s.get("type") == "delivery"
                            and pickup_of.get(s["id"]) in realized):
                        s["service_within"] = realized[pickup_of[s["id"]]]
                        keys += 1
            hard_ms, hard_out, hard_rc = timed_plan(feature, hard)
            fb_n += 1
            ok = hard_rc == 0 and hard_out is not None
            if ok:
                viol = [x for x in
                        hard_out.get("summary", {}).get("violations", [])
                        if x["cause"] == "max_transit_time"]
                new_transits = transits(hard_out, delivery_of)
                within = all(pid in new_transits
                             and new_transits[pid] <= cap
                             for pid, cap in realized.items())
                ok = not viol and within
            fb_ok += ok
            hard_ratios.append(hard_ms / max(feat_ms, 0.001))
            print(f"{f.stem}: parity={'ok' if same else 'DIFF'} "
                  f"free={feat_ms:.0f}ms ref={ref_ms:.0f}ms "
                  f"hard({keys} keys)={hard_ms:.0f}ms "
                  f"feedback={'ok' if ok else 'FAIL'}")

    def geo(rs):
        return math.exp(sum(math.log(r) for r in rs) / len(rs))

    print(f"\nkey-free parity feature vs reference: {parity_ok}/{parity_n}")
    print(f"key-free timing feature/reference geomean: {geo(free_ratios):.2f}")
    print(f"feed-back (tightest satisfiable keys): {fb_ok}/{fb_n} clean")
    print(f"hard-key timing vs key-free geomean: {geo(hard_ratios):.2f}")


if __name__ == "__main__":
    main()
