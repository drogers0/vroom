#!/usr/bin/env bash
# run_ab.sh BIN_A BIN_B OUTDIR
# Runs two VROOM binaries over all Li&Lim instance sets with -t 4 -x 1.
# Captures per-instance: solve time (ms), cost, unassigned count.
# For capped sets (alpha*): also runs CHECKER (env var, optional) to count violations.
#
# Output: OUTDIR/{a,b}/{set}/{instance}.json (raw vroom output)
#         OUTDIR/summary.tsv (tab-separated results)
#
# Env vars:
#   CHECKER   path to violation-checker script (receives vroom output JSON on stdin,
#             prints violation count on stdout)

set -euo pipefail

if [[ $# -lt 3 ]]; then
  echo "Usage: $0 BIN_A BIN_B OUTDIR" >&2
  exit 1
fi

BIN_A="$1"
BIN_B="$2"
OUTDIR="$3"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTANCES_DIR="$SCRIPT_DIR/instances"
THREADS=4
EXPLORATION=1

SETS=(base alpha1.5 alpha2 alpha3)

mkdir -p "$OUTDIR/a" "$OUTDIR/b"

# Output: OUTDIR/summary.tsv
TSV="$OUTDIR/summary.tsv"
printf "bin\tset\tinstance\tsolve_time_ms\tcost\tunassigned\tviolations\n" > "$TSV"

run_instance() {
  local bin="$1"
  local bin_label="$2"
  local set_name="$3"
  local instance_file="$4"
  local out_dir="$5"
  local instance_name
  instance_name="$(basename "$instance_file" .json)"

  local out_file="$out_dir/$instance_name.json"
  mkdir -p "$out_dir"

  # Run vroom, capture wall time and output
  local start_ms
  start_ms=$(python3 -c "import time; print(int(time.time()*1000))")

  if ! "$bin" -i "$instance_file" -t "$THREADS" -x "$EXPLORATION" > "$out_file" 2>/dev/null; then
    printf "%s\t%s\t%s\tERROR\tERROR\tERROR\tERROR\n" "$bin_label" "$set_name" "$instance_name" >> "$TSV"
    return
  fi

  local end_ms
  end_ms=$(python3 -c "import time; print(int(time.time()*1000))")
  local elapsed=$(( end_ms - start_ms ))

  # Parse cost and unassigned from vroom JSON output
  local cost unassigned
  cost=$(python3 -c "
import json, sys
d = json.load(open('$out_file'))
summary = d.get('summary', {})
print(summary.get('cost', summary.get('duration', 'NA')))
")
  unassigned=$(python3 -c "
import json, sys
d = json.load(open('$out_file'))
print(len(d.get('unassigned', [])))
")

  # Run violation checker for capped sets if CHECKER is set
  local violations="NA"
  if [[ "$set_name" != "base" ]] && [[ -n "${CHECKER:-}" ]]; then
    violations=$(python3 "$CHECKER" "$instance_file" "$out_file" -q 2>/dev/null | grep -c VIOLATION || true)
  fi

  printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\n" \
    "$bin_label" "$set_name" "$instance_name" \
    "$elapsed" "$cost" "$unassigned" "$violations" >> "$TSV"
}

for set_name in "${SETS[@]}"; do
  set_dir="$INSTANCES_DIR/$set_name"
  if [[ ! -d "$set_dir" ]]; then
    echo "WARNING: set directory not found: $set_dir" >&2
    continue
  fi

  for instance_file in "$set_dir"/*.json; do
    [[ -f "$instance_file" ]] || continue
    run_instance "$BIN_A" "A" "$set_name" "$instance_file" "$OUTDIR/a/$set_name"
    run_instance "$BIN_B" "B" "$set_name" "$instance_file" "$OUTDIR/b/$set_name"
  done
done

echo "Done. Results in $TSV"
