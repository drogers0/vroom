#!/usr/bin/env bash
set -euo pipefail
# macOS/homebrew: build vroom with glpk for plan-mode tests:
#   make GLPK_HEADER=/opt/homebrew/include/glpk.h (plus usual CXX/paths)

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$DIR/../.." && pwd)"
VROOM="$REPO/bin/vroom"
PY="${PYTHON:-python3}"

if [[ ! -x "$VROOM" ]]; then
  echo "ERROR: $VROOM not found or not executable" >&2
  exit 1
fi

pass=0
fail=0
skip=0

# Detect plan-mode (libglpk) availability.
HAS_GLPK=true
if ! "$VROOM" -t 1 -c -i "$DIR/t4_04_no_field.json" >/dev/null 2>&1; then
  HAS_GLPK=false
  echo "NOTE: libglpk not available — plan-mode (-c) tests requiring violation data will be SKIPPED"
fi

check() {
  local name="$1"; shift
  if "$@" 2>/dev/null; then
    echo "PASS  $name"
    (( pass++ )) || true
  else
    echo "FAIL  $name"
    (( fail++ )) || true
  fi
}

# Like check, but SKIP when glpk absent (used for tests that verify PRESENCE of violation data).
check_glpk() {
  local name="$1"; shift
  if [[ $HAS_GLPK == false ]]; then
    echo "SKIP  $name (no libglpk)"
    (( skip++ )) || true
    return
  fi
  check "$name" "$@"
}

expect_exit() {
  local want="$1"; shift
  local actual
  "$@" >/dev/null 2>&1; actual=$?
  [[ $actual -eq $want ]]
}

OUT_FILE=""
vroom_to_tmp() {
  local fixture="$1"; shift
  OUT_FILE="$(mktemp /tmp/vroom_test_XXXXXX.json)"
  "$VROOM" -t 1 "$@" -i "$fixture" > "$OUT_FILE" 2>/dev/null
  return 0
}

vroom_plan_to_tmp() {
  local fixture="$1"
  OUT_FILE="$(mktemp /tmp/vroom_test_XXXXXX.json)"
  "$VROOM" -t 1 -c -i "$fixture" > "$OUT_FILE" 2>/dev/null
  return 0
}

cleanup() { [[ -n "${OUT_FILE:-}" ]] && rm -f "$OUT_FILE"; OUT_FILE=""; }

# ─── T1: input plumbing ────────────────────────────────────────────────────────

check "T1-01 valid zero parses OK" \
  expect_exit 0 "$VROOM" -t 1 -i "$DIR/t1_01_valid_zero.json"

check "T1-02 valid positive parses OK" \
  expect_exit 0 "$VROOM" -t 1 -i "$DIR/t1_02_valid_positive.json"

check "T1-03 negative value → exit 2" \
  expect_exit 2 "$VROOM" -t 1 -i "$DIR/t1_03_invalid_negative.json"

check "T1-04 float value → exit 2" \
  expect_exit 2 "$VROOM" -t 1 -i "$DIR/t1_04_invalid_float.json"

check "T1-05 string value → exit 2" \
  expect_exit 2 "$VROOM" -t 1 -i "$DIR/t1_05_invalid_string.json"

check "T1-06 null value → exit 2" \
  expect_exit 2 "$VROOM" -t 1 -i "$DIR/t1_06_invalid_null.json"

check "T1-07 max_transit_time on plain job ignored" \
  expect_exit 0 "$VROOM" -t 1 -i "$DIR/t1_07_job_ignored.json"

# ─── T2: margin filter ─────────────────────────────────────────────────────────

t2_01() {
  vroom_to_tmp "$DIR/t2_01_tight_cap.json"
  local r="$OUT_FILE" n
  n=$(jq '.unassigned | length' "$r")
  cleanup; [[ $n -ge 2 ]]
}
check "T2-01 tight cap → unassigned" t2_01

t2_02() {
  vroom_to_tmp "$DIR/t2_02_loose_cap.json"
  local r="$OUT_FILE" n
  n=$(jq '.unassigned | length' "$r")
  if [[ $n -ne 0 ]]; then cleanup; return 1; fi
  "$PY" "$DIR/check_caps.py" "$DIR/t2_02_loose_cap.json" "$r" -q; local rc=$?
  cleanup; return $rc
}
check "T2-02 loose cap → assigned, cap OK" t2_02

t2_03() {
  vroom_to_tmp "$DIR/t2_03_boundary_equal.json"
  local r="$OUT_FILE" n
  n=$(jq '.unassigned | length' "$r")
  if [[ $n -ne 0 ]]; then cleanup; return 1; fi
  "$PY" "$DIR/check_caps.py" "$DIR/t2_03_boundary_equal.json" "$r" -q; local rc=$?
  cleanup; return $rc
}
check "T2-03 cap=travel boundary → assigned" t2_03

t2_04() {
  vroom_to_tmp "$DIR/t2_04_multi_one_fail.json"
  local r="$OUT_FILE" u1 u2 a3 a4
  u1=$(jq '[.unassigned[]? | select(.id == 1)] | length' "$r")
  u2=$(jq '[.unassigned[]? | select(.id == 2)] | length' "$r")
  a3=$(jq '[.routes[].steps[]? | select(.id == 3)] | length' "$r")
  a4=$(jq '[.routes[].steps[]? | select(.id == 4)] | length' "$r")
  cleanup
  [[ $u1 -ge 1 && $u2 -ge 1 && $a3 -ge 1 && $a4 -ge 1 ]]
}
check "T2-04 multi-shipment: tight unassigned, loose assigned" t2_04

t2_05() {
  vroom_to_tmp "$DIR/t2_05_cap_only_dispatch.json"
  local r="$OUT_FILE" n
  n=$(jq '.unassigned | length' "$r")
  cleanup; [[ $n -ge 2 ]]
}
check "T2-05 cap-only dispatch → unassigned" t2_05

t2_06() {
  vroom_to_tmp "$DIR/t2_06_break_between.json"
  local r="$OUT_FILE" n
  n=$(jq '.unassigned | length' "$r")
  if [[ $n -ne 0 ]]; then cleanup; return 1; fi
  "$PY" "$DIR/check_caps.py" "$DIR/t2_06_break_between.json" "$r" -q; local rc=$?
  cleanup; return $rc
}
check "T2-06 break present, loose cap → assigned, cap OK" t2_06

t2_07() {
  vroom_to_tmp "$DIR/t2_07_same_location.json"
  local r="$OUT_FILE" n
  n=$(jq '.unassigned | length' "$r")
  if [[ $n -ne 0 ]]; then cleanup; return 1; fi
  "$PY" "$DIR/check_caps.py" "$DIR/t2_07_same_location.json" "$r" -q; local rc=$?
  cleanup; return $rc
}
check "T2-07 same location cap=0 → assigned" t2_07

# ─── T3: apply-phase / intra operators ─────────────────────────────────────────

t3_01() {
  vroom_to_tmp "$DIR/t3_01_intra_relocate.json"
  local r="$OUT_FILE" rc=0
  "$PY" "$DIR/check_caps.py" "$DIR/t3_01_intra_relocate.json" "$r" -q || rc=1
  cleanup; return $rc
}
check "T3-01 intra-relocate: final output respects cap" t3_01

# ─── T4: violation reporting ────────────────────────────────────────────────────
# Tests checking PRESENCE of violation data require libglpk (plan mode -c).
# Tests checking ABSENCE of violations or using solve mode run unconditionally.

# T4-01: ride=300, cap=200 → delivery excess=100, pickup clean
t4_01() {
  vroom_plan_to_tmp "$DIR/t4_01_violating.json"
  local r="$OUT_FILE" excess pickup_mrt
  excess=$(jq '[.routes[0].steps[] | select(.type=="delivery") | .violations[]? | select(.cause=="max_transit_time") | .duration] | add // 0' "$r")
  pickup_mrt=$(jq '[.routes[0].steps[] | select(.type=="pickup") | .violations[]?.cause | select(. == "max_transit_time")] | length' "$r")
  cleanup
  [[ $excess -eq 100 && $pickup_mrt -eq 0 ]]
}
check_glpk "T4-01 violating: delivery excess=100, pickup clean" t4_01

# T4-01b: route-level max_transit_time duration=100
t4_01b() {
  vroom_plan_to_tmp "$DIR/t4_01_violating.json"
  local r="$OUT_FILE" route_excess
  route_excess=$(jq '[.routes[0].violations[]? | select(.cause=="max_transit_time") | .duration] | add // 0' "$r")
  cleanup
  [[ $route_excess -eq 100 ]]
}
check_glpk "T4-01b violating: route-level max_transit_time duration=100" t4_01b

# T4-02: ride=300, cap=400 → no max_transit_time violation (absence check: runs even without glpk)
t4_02() {
  vroom_plan_to_tmp "$DIR/t4_02_non_violating.json"
  local r="$OUT_FILE" n
  n=$(jq '[.routes[0].steps[].violations[]?.cause | select(. == "max_transit_time")] | length' "$r")
  cleanup; [[ $n -eq 0 ]]
}
check "T4-02 non-violating: no max_transit_time cause" t4_02

# T4-03: solve mode (no -c) → violations arrays empty
t4_03() {
  vroom_to_tmp "$DIR/t4_03_solve_mode.json"
  local r="$OUT_FILE" sv rv
  sv=$(jq '[.routes[].steps[].violations | length] | add // 0' "$r")
  rv=$(jq '[.routes[].violations | length] | add // 0' "$r")
  cleanup; [[ $sv -eq 0 && $rv -eq 0 ]]
}
check "T4-03 solve mode: violations arrays empty" t4_03

# T4-04: no max_transit_time field → no max_transit_time violation (absence check)
t4_04() {
  vroom_plan_to_tmp "$DIR/t4_04_no_field.json"
  local r="$OUT_FILE" n
  n=$(jq '[.routes[0].steps[].violations[]?.cause | select(. == "max_transit_time")] | length' "$r")
  cleanup; [[ $n -eq 0 ]]
}
check "T4-04 no field: no max_transit_time cause" t4_04

# T4-05: delivery before pickup → PRECEDENCE present, MAX_TRANSIT_TIME absent
t4_05() {
  vroom_plan_to_tmp "$DIR/t4_05_precedence.json"
  local r="$OUT_FILE" mrt prec
  mrt=$(jq '[[.routes[0].steps[].violations[]?.cause], [.routes[0].violations[]?.cause]] | flatten | map(select(. == "max_transit_time")) | length' "$r")
  prec=$(jq '[[.routes[0].steps[].violations[]?.cause], [.routes[0].violations[]?.cause]] | flatten | map(select(. == "precedence")) | length' "$r")
  cleanup
  [[ $mrt -eq 0 && $prec -ge 1 ]]
}
check_glpk "T4-05 precedence: precedence reported, no max_transit_time" t4_05

# T4-06: two violating shipments → route excess=200, 2 delivery violations
t4_06() {
  vroom_plan_to_tmp "$DIR/t4_06_two_shipments.json"
  local r="$OUT_FILE" route_excess step_count
  route_excess=$(jq '[.routes[0].violations[]? | select(.cause=="max_transit_time") | .duration] | add // 0' "$r")
  step_count=$(jq '[.routes[0].steps[] | select(.type=="delivery") | .violations[]? | select(.cause=="max_transit_time")] | length' "$r")
  cleanup
  [[ $route_excess -eq 200 && $step_count -eq 2 ]]
}
check_glpk "T4-06 two shipments: route excess=200, 2 delivery violations" t4_06

# T4-07: no max_transit_time field → no max_transit_time anywhere (absence check)
t4_07() {
  vroom_plan_to_tmp "$DIR/t4_07_parity.json"
  local r="$OUT_FILE" n
  n=$(jq '[[.routes[].steps[].violations[]?.cause], [.routes[].violations[]?.cause]] | flatten | map(select(. == "max_transit_time")) | length' "$r")
  cleanup; [[ $n -eq 0 ]]
}
check "T4-07 parity no field: no max_transit_time anywhere" t4_07

# T4-08: break between P and D counts in transit time; cap=150, ride=200 → excess=50
t4_08() {
  vroom_plan_to_tmp "$DIR/t4_08_break_between.json"
  local r="$OUT_FILE" excess
  excess=$(jq '[.routes[0].steps[] | select(.type=="delivery") | .violations[]? | select(.cause=="max_transit_time") | .duration] | add // 0' "$r")
  cleanup; [[ $excess -eq 50 ]]
}
check_glpk "T4-08 break between: excess=50 (break counts as transit time)" t4_08

# T4-08b: break AFTER delivery → no violation (absence check)
t4_08b() {
  vroom_plan_to_tmp "$DIR/t4_08b_break_after.json"
  local r="$OUT_FILE" n
  n=$(jq '[.routes[0].steps[].violations[]?.cause | select(. == "max_transit_time")] | length' "$r")
  cleanup; [[ $n -eq 0 ]]
}
check "T4-08b break after delivery: no violation" t4_08b

# T4-09: pickup TW [0,400], delivery forced at 300, cap=150, travel=100.
# Without cap MIP term: ASAP sets S_P=0, ride=300-0=300>150, excess=150.
# With cap MIP term (T10): optimizer delays S_P to 150 (within TW),
# ride=300-150=150=cap, e_p=0. Post-pass sees no excess. Zero violations.
t4_09() {
  vroom_plan_to_tmp "$DIR/t4_09_delay_eliminates.json"
  local r="$OUT_FILE" n
  n=$(jq '[.routes[0].steps[].violations[]?.cause | select(. == "max_transit_time")] | length' "$r")
  cleanup; [[ $n -eq 0 ]]
}
check_glpk "T4-09 pickup delay within TW eliminates violation: no max_transit_time" t4_09

# ─── T5: route-independent infeasibility precheck ──────────────────────────────

# T5-01: pickup TW [0,100] service=10, delivery TW [400,500], travel=50, cap=200.
# lb = max(50, 400-(100+10)) = max(50,290) = 290 > 200 → precheck fires → unassigned.
t5_01() {
  vroom_to_tmp "$DIR/t5_01_tw_gap_infeasible.json"
  local r="$OUT_FILE" n
  n=$(jq '.unassigned | length' "$r")
  cleanup; [[ $n -ge 2 ]]
}
check "T5-01 TW-gap infeasible: precheck → unassigned" t5_01

# T5-02: same layout but cap=300 ≥ lb=290 → precheck does NOT fire → assigned, cap OK.
t5_02() {
  vroom_to_tmp "$DIR/t5_02_tw_gap_feasible.json"
  local r="$OUT_FILE" n
  n=$(jq '.unassigned | length' "$r")
  if [[ $n -ne 0 ]]; then cleanup; return 1; fi
  "$PY" "$DIR/check_caps.py" "$DIR/t5_02_tw_gap_feasible.json" "$r" -q; local rc=$?
  cleanup; return $rc
}
check "T5-02 TW-gap feasible: precheck silent → assigned, cap OK" t5_02

# T5-03: pickup with setup=20s, service=0, TW [0,100]; delivery TW [170,∞];
# travel=50; cap=60. The precheck bound must use the largest setup+service
# among compatible vehicles: lb = max(50, 170-(100+20)) = 50 ≤ 60, so the
# shipment is servable (the engine delays the pickup to meet the cap). A bound
# that omitted setup would compute 70 > 60 and wrongly mark it unassignable.
t5_03() {
  vroom_to_tmp "$DIR/t5_03_setup_feasible.json"
  local r="$OUT_FILE" n
  n=$(jq '.unassigned | length' "$r")
  if [[ $n -ne 0 ]]; then cleanup; return 1; fi
  "$PY" "$DIR/check_caps.py" "$DIR/t5_03_setup_feasible.json" "$r" -q; local rc=$?
  cleanup; return $rc
}
check "T5-03 setup counted in precheck bound: assigned, cap OK" t5_03

# T5-04: non-metric matrix; direct pickup->delivery leg (200) exceeds the cap
# (160), but routing through the intermediate job location costs 50+50=100.
# The precheck's travel bound must account for indirect paths (cheapest edge
# out of the pickup plus cheapest edge into the delivery = 100 <= 160), so the
# shipment stays servable and the solver routes pickup -> job -> delivery.
t5_04() {
  vroom_to_tmp "$DIR/t5_04_nonmetric_feasible.json"
  local r="$OUT_FILE" n
  n=$(jq '.unassigned | length' "$r")
  if [[ $n -ne 0 ]]; then cleanup; return 1; fi
  "$PY" "$DIR/check_caps.py" "$DIR/t5_04_nonmetric_feasible.json" "$r" -q; local rc=$?
  cleanup; return $rc
}
check "T5-04 non-metric indirect path: assigned, cap OK" t5_04

# T5-05: multi-window pickup. Windows [0,10] and [50,1000]; delivery starts
# at 100 (forced wait); travel 20; cap 70. Transit from the first window is
# at best 100-10=90 > 70, so the schedule must place the pickup in the
# second window (any start >= 30 works; 50 gives transit 50 <= 70). The
# engine's window advancement must cross the gap for the shipment to be
# served.
t5_05() {
  vroom_to_tmp "$DIR/t5_05_multi_tw_advance.json"
  local r="$OUT_FILE" n
  n=$(jq '.unassigned | length' "$r")
  if [[ $n -ne 0 ]]; then cleanup; return 1; fi
  "$PY" "$DIR/check_caps.py" "$DIR/t5_05_multi_tw_advance.json" "$r" -q; local rc=$?
  cleanup; return $rc
}
check "T5-05 multi-window pickup advancement: assigned, cap OK" t5_05


# ─── T6: plan-mode hard cap via step-level service_within ─────────────────────

# Reports violations but never fails: shipment caps stay soft. service_within
# is the hard counterpart: the LP must satisfy it or the route errors.

t6_01() {
  vroom_plan_to_tmp "$DIR/t6_01_hard_shifts_schedule.json"
  local r="$OUT_FILE" n v
  n=$(jq '.unassigned | length' "$r")
  if [[ $n -ne 0 ]]; then cleanup; return 1; fi
  # The hard cap must be met by trading window compliance instead.
  v=$(jq '[.summary.violations[] | select(.cause == "lead_time" or .cause == "delay")] | length' "$r")
  if [[ $v -eq 0 ]]; then cleanup; return 1; fi
  "$PY" "$DIR/check_caps.py" "$DIR/t6_01_hard_shifts_schedule.json" "$r" -q; local rc=$?
  cleanup; return $rc
}
check_glpk "T6-01 hard cap binds: window violation traded, transit <= cap" t6_01

check_glpk "T6-02 hard cap below travel time: route errors" \
  expect_exit 2 "$VROOM" -t 1 -c -i "$DIR/t6_02_hard_infeasible.json"

t6_03() {
  vroom_plan_to_tmp "$DIR/t6_03_hard_wider_than_shipment.json"
  local r="$OUT_FILE"
  # Pinned transit is 200: within the step cap (250), over the shipment
  # cap (100) which must still be reported as a violation.
  "$PY" - "$r" <<'PYCHK'
import json, sys
o = json.load(open(sys.argv[1]))
v = o["summary"]["violations"]
assert any(x["cause"] == "max_transit_time" and x["duration"] == 100 for x in v), v
st = {(s["type"], s["id"]): s for r in o["routes"] for s in r["steps"] if s["type"] in ("pickup", "delivery")}
p, d = st[("pickup", 1)], st[("delivery", 2)]
ride = d["arrival"] + d.get("waiting_time", 0) - (p["arrival"] + p.get("waiting_time", 0) + p.get("setup", 0) + p.get("service", 0))
assert ride == 200 and ride <= 250, ride
PYCHK
  local rc=$?
  cleanup; return $rc
}
check_glpk "T6-03 step cap wider than shipment cap: hard holds, excess reported" t6_03

# T6-03 pins the schedule, so it proves reporting interaction, not
# enforcement; T6-03b is the enforcement proof: same pins with the step cap
# below the pinned transit must error.
check_glpk "T6-03b step cap below pinned transit: route errors" \
  expect_exit 2 "$VROOM" -t 1 -c -i "$DIR/t6_03b_hard_below_pins.json"

t6_04() {
  vroom_plan_to_tmp "$DIR/t6_04_hard_only.json"
  local r="$OUT_FILE" n v
  n=$(jq '.unassigned | length' "$r")
  if [[ $n -ne 0 ]]; then cleanup; return 1; fi
  v=$(jq '[.summary.violations[] | select(.cause == "max_transit_time")] | length' "$r")
  if [[ $v -ne 0 ]]; then cleanup; return 1; fi
  "$PY" "$DIR/check_caps.py" "$DIR/t6_04_hard_only.json" "$r" -q; local rc=$?
  cleanup; return $rc
}
check_glpk "T6-04 step cap without shipment cap: enforced, nothing reported" t6_04

check "T6-05 service_within on a pickup step rejected" \
  expect_exit 2 "$VROOM" -t 1 -i "$DIR/t6_05_wrong_step_type.json"

t6_06() {
  vroom_plan_to_tmp "$DIR/t6_06_missing_pickup.json"
  local r="$OUT_FILE" v
  v=$(jq '[.summary.violations[] | select(.cause == "precedence")] | length' "$r")
  local rc=1
  [[ $v -ge 1 ]] && rc=0
  cleanup; return $rc
}
check_glpk "T6-06 missing pickup: key ignored, precedence reported" t6_06

t6_07() {
  vroom_plan_to_tmp "$DIR/t6_07_zero_same_location.json"
  local r="$OUT_FILE" n
  n=$(jq '.unassigned | length' "$r")
  if [[ $n -ne 0 ]]; then cleanup; return 1; fi
  "$PY" - "$r" <<'PYCHK'
import json, sys
o = json.load(open(sys.argv[1]))
st = {(s["type"], s["id"]): s for r in o["routes"] for s in r["steps"] if s["type"] in ("pickup", "delivery")}
p, d = st[("pickup", 1)], st[("delivery", 2)]
ride = d["arrival"] + d.get("waiting_time", 0) - (p["arrival"] + p.get("waiting_time", 0) + p.get("setup", 0) + p.get("service", 0))
assert ride == 0, ride
PYCHK
  local rc=$?
  cleanup; return $rc
}
check_glpk "T6-07 zero cap on same-location pair: served, transit zero" t6_07

t6_08() {
  vroom_plan_to_tmp "$DIR/t6_08_break_between.json"
  local r="$OUT_FILE" n
  n=$(jq '.unassigned | length' "$r")
  if [[ $n -ne 0 ]]; then cleanup; return 1; fi
  "$PY" "$DIR/check_caps.py" "$DIR/t6_08_break_between.json" "$r" -q; local rc=$?
  cleanup; return $rc
}
check_glpk "T6-08 break between pickup and delivery: hard cap holds" t6_08

t6_09() {
  vroom_plan_to_tmp "$DIR/t6_09_two_pairs.json"
  local r="$OUT_FILE"
  # First pair hard-only, second soft-only: pins excess-column indexing
  # when hard-only pairs interleave. The soft cap is violated by design,
  # so assert the pieces directly instead of via check_caps.
  "$PY" - "$r" <<'PYCHK'
import json, sys
o = json.load(open(sys.argv[1]))
v = o["summary"]["violations"]
assert any(x["cause"] == "max_transit_time" and x["duration"] == 50 for x in v), v
# The excess belongs to the soft pair: its delivery step carries the cause.
d4 = [s for r in o["routes"] for s in r["steps"] if s["type"] == "delivery" and s["id"] == 4]
assert any(x["cause"] == "max_transit_time" for x in d4[0].get("violations", [])), d4
# The hard pair is binding: window violation traded to meet the cap.
assert any(x["cause"] in ("lead_time", "delay") for x in v), v
info = {}
for r in o["routes"]:
    for st in r["steps"]:
        if st["type"] in ("pickup", "delivery"):
            start = st["arrival"] + st.get("waiting_time", 0)
            info[(st["type"], st["id"])] = (start, start + st.get("setup", 0) + st.get("service", 0))
ride = info[("delivery", 2)][0] - info[("pickup", 1)][1]
assert ride <= 300, ride
PYCHK
  local rc=$?
  cleanup; return $rc
}
check_glpk "T6-09 hard-only pair before soft pair: both behave" t6_09

t6_10() {
  vroom_plan_to_tmp "$DIR/t6_10_with_forced_service.json"
  local r="$OUT_FILE" n
  n=$(jq '.unassigned | length' "$r")
  if [[ $n -ne 0 ]]; then cleanup; return 1; fi
  "$PY" - "$r" <<'PYCHK'
import json, sys
o = json.load(open(sys.argv[1]))
st = {(s["type"], s["id"]): s for r in o["routes"] for s in r["steps"] if s["type"] in ("pickup", "delivery")}
p, d = st[("pickup", 1)], st[("delivery", 2)]
p_start = p["arrival"] + p.get("waiting_time", 0)
d_start = d["arrival"] + d.get("waiting_time", 0)
assert 100 <= p_start <= 150, p_start  # service_after/service_before pins
assert d_start <= 280, d_start         # service_before pin, tighter than the cap allows
PYCHK
  if [[ $? -ne 0 ]]; then cleanup; return 1; fi
  "$PY" "$DIR/check_caps.py" "$DIR/t6_10_with_forced_service.json" "$r" -q; local rc=$?
  cleanup; return $rc
}
check_glpk "T6-10 combined with service_after/service_before pins" t6_10

check "T6-11 invalid negative service_within rejected" \
  expect_exit 2 "$VROOM" -t 1 -i "$DIR/t6_11_invalid_negative.json"

check "T6-12 invalid float service_within rejected" \
  expect_exit 2 "$VROOM" -t 1 -i "$DIR/t6_12_invalid_float.json"

check "T6-13 invalid string service_within rejected" \
  expect_exit 2 "$VROOM" -t 1 -i "$DIR/t6_13_invalid_string.json"

check "T6-14 invalid null service_within rejected" \
  expect_exit 2 "$VROOM" -t 1 -i "$DIR/t6_14_invalid_null.json"

t6_15() {
  # Cap (0) is below the travel time (50): if solve mode enforced the key
  # the shipment could not be served at all.
  vroom_to_tmp "$DIR/t6_15_solve_mode.json"
  local r="$OUT_FILE" n
  n=$(jq '.unassigned | length' "$r")
  local rc=1
  [[ $n -eq 0 ]] && rc=0
  cleanup; return $rc
}
check "T6-15 solve mode: impossible cap ignored, route served" t6_15

# ─── Summary ───────────────────────────────────────────────────────────────────

total=$(( pass + fail + skip ))
echo ""
echo "Results: $pass passed, $fail failed, $skip skipped (of $total)"
if [[ $fail -gt 0 ]]; then
  echo "FAILED: $fail test(s)"
  exit 1
fi
echo "All tests passed."
