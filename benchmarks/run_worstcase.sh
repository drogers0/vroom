#!/usr/bin/env bash
# Run the worst-case benchmark suite against bin/vroom.
# Usage: bash benchmarks/run_worstcase.sh [instance_dir]
# Default instance_dir: benchmarks/instances_worstcase/100
#
# For each instance: solve with vroom -t 1, verify with check_worstcase.py,
# measure wall-clock time, and print a summary table.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VROOM="${REPO_ROOT}/bin/vroom"
CHECK="${REPO_ROOT}/benchmarks/check_worstcase.py"
INST_DIR="${1:-${REPO_ROOT}/benchmarks/instances_worstcase/100}"

if [[ ! -x "${VROOM}" ]]; then
  echo "ERROR: vroom binary not found at ${VROOM}" >&2
  exit 1
fi
if [[ ! -d "${INST_DIR}" ]]; then
  echo "ERROR: instance directory not found: ${INST_DIR}" >&2
  exit 1
fi

# Column widths
printf "%-12s %10s %7s %10s %9s %10s\n" \
  "INSTANCE" "solve_ms" "served" "unassigned" "cap_viol" "break_viol"
printf "%s\n" "$(printf '%.0s-' {1..65})"

total_instances=0
total_cap_viol=0
total_break_viol=0
total_unassigned=0
any_failure=0

TMPOUT=$(mktemp /tmp/vroom_wc_XXXXXX.json)
trap 'rm -f "${TMPOUT}"' EXIT

for inst in "${INST_DIR}"/*.json; do
  name=$(basename "${inst}" .json)

  # Solve, capturing wall-clock time
  start_ns=$(date +%s%N 2>/dev/null || python3 -c "import time; print(int(time.time()*1e9))")
  "${VROOM}" -i "${inst}" -t 1 >"${TMPOUT}" 2>/dev/null
  end_ns=$(date +%s%N 2>/dev/null || python3 -c "import time; print(int(time.time()*1e9))")
  solve_ms=$(( (end_ns - start_ns) / 1000000 ))

  # Verify; capture checker output
  check_out=$(python3 "${CHECK}" "${inst}" "${TMPOUT}" -q 2>&1) || true

  # Parse summary counts from checker output
  served=$(     echo "${check_out}" | grep -o 'Shipments served:[[:space:]]*[0-9]*'     | grep -o '[0-9]*$' || echo 0)
  unassigned=$( echo "${check_out}" | grep -o 'Shipments unassigned:[[:space:]]*[0-9]*' | grep -o '[0-9]*$' || echo 0)
  cap_viol=$(   echo "${check_out}" | grep -o 'Cap violations:[[:space:]]*[0-9]*'        | grep -o '[0-9]*$' || echo 0)
  break_viol=$( echo "${check_out}" | grep -o 'Break violations:[[:space:]]*[0-9]*'      | grep -o '[0-9]*$' || echo 0)

  # Defaults if grep found nothing
  served="${served:-0}"
  unassigned="${unassigned:-0}"
  cap_viol="${cap_viol:-0}"
  break_viol="${break_viol:-0}"

  printf "%-12s %10d %7d %10d %9d %10d\n" \
    "${name}" "${solve_ms}" "${served}" "${unassigned}" "${cap_viol}" "${break_viol}"

  total_instances=$(( total_instances + 1 ))
  total_cap_viol=$(( total_cap_viol + cap_viol ))
  total_break_viol=$(( total_break_viol + break_viol ))
  total_unassigned=$(( total_unassigned + unassigned ))
  if [[ "${cap_viol}" -gt 0 || "${break_viol}" -gt 0 ]]; then
    any_failure=1
  fi
done

printf "%s\n" "$(printf '%.0s-' {1..65})"
printf "%-12s %10s %7s %10d %9d %10d\n" \
  "TOTAL" "" "" "${total_unassigned}" "${total_cap_viol}" "${total_break_viol}"

echo ""
if [[ "${any_failure}" -eq 0 ]]; then
  echo "PASS  (${total_instances} instances, 0 cap violations, 0 break violations)"
else
  echo "FAIL  (${total_instances} instances, cap_violations=${total_cap_viol}, break_violations=${total_break_viol}, unassigned_total=${total_unassigned})"
fi
