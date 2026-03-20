#!/usr/bin/env python3
"""Run real-life timing-objective benchmarks and generate a markdown report.

Benchmark coverage:
- VRPTW: Solomon + Homberger
- PDPTW: Li&Lim
- CVRP: CVRPLIB X

Outputs:
- benchmark_results/real_benchmark_runs.csv
- benchmark_results/REAL_BENCHMARK_REPORT.md
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import os
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = ROOT / "benchmark_results"
INPUT_DIR = ROOT / "benchmark_real_inputs"

VROOM_SCRIPTS = Path(
    os.environ.get("VROOM_SCRIPTS_DIR", "/Users/david/Documents/git/vroom-scripts")
).resolve()

MASTER_IMAGE = os.environ.get("MASTER_IMAGE", "vroom-master")
CURRENT_IMAGE = os.environ.get("CURRENT_IMAGE", "vroom-build")
RUNS = int(os.environ.get("BENCH_RUNS", "3"))

VRPTW_CONVERTER = VROOM_SCRIPTS / "src" / "vrptw_to_json.py"
PDPTW_CONVERTER = VROOM_SCRIPTS / "src" / "pdptw_to_json.py"
CVRP_CONVERTER = VROOM_SCRIPTS / "src" / "cvrplib_to_json.py"


@dataclass(frozen=True)
class InstanceSpec:
    suite: str
    family: str
    label: str
    source: Path
    converter: Path
    kind: str  # vrptw, pdptw, cvrp


@dataclass(frozen=True)
class ModeSpec:
    mode: str
    timing_objective: Optional[Dict]
    add_target_time: bool


INSTANCES: List[InstanceSpec] = [
    # Solomon (100 customers)
    InstanceSpec(
        suite="VRPTW",
        family="Solomon",
        label="C101",
        source=VROOM_SCRIPTS / "benchmarks" / "VRPTW" / "solomon" / "C101.txt",
        converter=VRPTW_CONVERTER,
        kind="vrptw",
    ),
    InstanceSpec(
        suite="VRPTW",
        family="Solomon",
        label="R101",
        source=VROOM_SCRIPTS / "benchmarks" / "VRPTW" / "solomon" / "R101.txt",
        converter=VRPTW_CONVERTER,
        kind="vrptw",
    ),
    InstanceSpec(
        suite="VRPTW",
        family="Solomon",
        label="RC101",
        source=VROOM_SCRIPTS / "benchmarks" / "VRPTW" / "solomon" / "RC101.txt",
        converter=VRPTW_CONVERTER,
        kind="vrptw",
    ),
    # Homberger (200, 400 customers)
    InstanceSpec(
        suite="VRPTW",
        family="Homberger",
        label="c1_2_1",
        source=VROOM_SCRIPTS
        / "benchmarks"
        / "VRPTW"
        / "homberger_200"
        / "c1_2_1.txt",
        converter=VRPTW_CONVERTER,
        kind="vrptw",
    ),
    InstanceSpec(
        suite="VRPTW",
        family="Homberger",
        label="c1_4_1",
        source=VROOM_SCRIPTS
        / "benchmarks"
        / "VRPTW"
        / "homberger_400"
        / "c1_4_1.txt",
        converter=VRPTW_CONVERTER,
        kind="vrptw",
    ),
    # Li&Lim PDPTW (100, 200, 400 requests)
    InstanceSpec(
        suite="PDPTW",
        family="Li&Lim",
        label="lc101",
        source=VROOM_SCRIPTS / "benchmarks" / "PDPTW" / "li_lim_100" / "lc101.txt",
        converter=PDPTW_CONVERTER,
        kind="pdptw",
    ),
    InstanceSpec(
        suite="PDPTW",
        family="Li&Lim",
        label="LC1_2_1",
        source=VROOM_SCRIPTS
        / "benchmarks"
        / "PDPTW"
        / "li_lim_200"
        / "LC1_2_1.txt",
        converter=PDPTW_CONVERTER,
        kind="pdptw",
    ),
    InstanceSpec(
        suite="PDPTW",
        family="Li&Lim",
        label="LC1_4_1",
        source=VROOM_SCRIPTS
        / "benchmarks"
        / "PDPTW"
        / "li_lim_400"
        / "LC1_4_1.txt",
        converter=PDPTW_CONVERTER,
        kind="pdptw",
    ),
    # CVRP (100, 219, 491 customers)
    InstanceSpec(
        suite="CVRP",
        family="CVRPLIB-X",
        label="X-n101-k25",
        source=VROOM_SCRIPTS / "benchmarks" / "CVRP" / "X" / "X-n101-k25.vrp",
        converter=CVRP_CONVERTER,
        kind="cvrp",
    ),
    InstanceSpec(
        suite="CVRP",
        family="CVRPLIB-X",
        label="X-n219-k73",
        source=VROOM_SCRIPTS / "benchmarks" / "CVRP" / "X" / "X-n219-k73.vrp",
        converter=CVRP_CONVERTER,
        kind="cvrp",
    ),
    InstanceSpec(
        suite="CVRP",
        family="CVRPLIB-X",
        label="X-n491-k59",
        source=VROOM_SCRIPTS / "benchmarks" / "CVRP" / "X" / "X-n491-k59.vrp",
        converter=CVRP_CONVERTER,
        kind="cvrp",
    ),
]

DEFAULT_MODE = ModeSpec(mode="default", timing_objective=None, add_target_time=False)

EXTRA_MODES: Dict[str, List[ModeSpec]] = {
    "vrptw": [
        ModeSpec(
            mode="hierarchical_ms",
            timing_objective={"mode": "hierarchical", "objectives": ["makespan"]},
            add_target_time=False,
        )
    ],
    "cvrp": [
        ModeSpec(
            mode="hierarchical_ms",
            timing_objective={"mode": "hierarchical", "objectives": ["makespan"]},
            add_target_time=False,
        )
    ],
    "pdptw": [
        ModeSpec(
            mode="hierarchical_ms",
            timing_objective={"mode": "hierarchical", "objectives": ["makespan"]},
            add_target_time=False,
        ),
        ModeSpec(
            mode="hierarchical_tt_ss_ms",
            timing_objective={
                "mode": "hierarchical",
                "objectives": ["target_time", "shipment_span", "makespan"],
            },
            add_target_time=True,
        ),
        ModeSpec(
            mode="weighted_3_2_1",
            timing_objective={
                "mode": "weighted",
                "weights": {"target_time": 3, "shipment_span": 2, "makespan": 1},
            },
            add_target_time=True,
        ),
    ],
}


def run_cmd(cmd: List[str], *, stdin_path: Optional[Path] = None) -> str:
    stdin_file = None
    try:
        if stdin_path is not None:
            stdin_file = open(stdin_path, "rb")
        proc = subprocess.run(
            cmd,
            stdin=stdin_file,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    finally:
        if stdin_file is not None:
            stdin_file.close()

    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"Command failed ({proc.returncode}): {' '.join(cmd)}\n{stderr}")

    return proc.stdout.decode("utf-8", errors="replace")


def ensure_converted(spec: InstanceSpec) -> Path:
    if not spec.source.exists():
        raise FileNotFoundError(f"Missing benchmark source: {spec.source}")

    json_path = spec.source.with_suffix(".json")
    if not json_path.exists():
        print(f"[convert] {spec.source.name} -> {json_path.name}")
        run_cmd([sys.executable, str(spec.converter), str(spec.source)])
    return json_path


def build_variant(base_json: Path, spec: InstanceSpec, mode: ModeSpec) -> Path:
    out_name = f"{spec.suite}_{spec.label}_{mode.mode}.json"
    out_path = INPUT_DIR / out_name

    with open(base_json, "r", encoding="utf-8") as f:
        data = json.load(f)

    if mode.add_target_time:
        if "shipments" in data:
            for shipment in data["shipments"]:
                pickup = shipment["pickup"]
                tw = pickup.get("time_windows", [[0, 0]])[0]
                pickup["target_time"] = (int(tw[0]) + int(tw[1])) // 2
        elif "jobs" in data:
            for job in data["jobs"]:
                tw = job.get("time_windows", [[0, 0]])[0]
                job["target_time"] = (int(tw[0]) + int(tw[1])) // 2

    if mode.timing_objective is None:
        data.pop("options", None)
    else:
        data.setdefault("options", {})
        data["options"]["timing_objective"] = mode.timing_objective

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f)

    return out_path


def inspect_input(path: Path) -> Dict[str, int]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    vehicles = len(data.get("vehicles", []))
    jobs = len(data.get("jobs", []))
    shipments = len(data.get("shipments", []))
    matrix_dim = 0
    matrices = data.get("matrices", {})
    if "car" in matrices and "durations" in matrices["car"]:
        matrix_dim = len(matrices["car"]["durations"])
    return {
        "vehicles": vehicles,
        "jobs": jobs,
        "shipments": shipments,
        "matrix_dim": matrix_dim,
    }


def docker_image_id(image: str) -> str:
    out = run_cmd(["docker", "image", "inspect", image, "--format", "{{.Id}}"])
    return out.strip().replace("sha256:", "")[:12]


def solve_once(image: str, input_path: Path) -> Dict[str, float]:
    cmd = [
        "docker",
        "run",
        "--platform",
        "linux/amd64",
        "--rm",
        "-i",
        "--entrypoint",
        "/usr/local/bin/vroom",
        image,
    ]

    t0 = time.perf_counter()
    out = run_cmd(cmd, stdin_path=input_path)
    wall_ms = (time.perf_counter() - t0) * 1000.0

    try:
        result = json.loads(out)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON output for {input_path} on {image}: {exc}")

    summary = result.get("summary", {})
    ct = summary.get("computing_times", {})

    return {
        "loading_ms": float(ct.get("loading", 0.0)),
        "solving_ms": float(ct.get("solving", 0.0)),
        "routing_ms": float(ct.get("routing", 0.0)),
        "wall_ms": wall_ms,
        "cost": float(summary.get("cost", 0.0)),
        "duration": float(summary.get("duration", 0.0)),
        "waiting_time": float(summary.get("waiting_time", 0.0)),
        "unassigned": float(summary.get("unassigned", 0.0)),
        "routes": float(summary.get("routes", len(result.get("routes", [])))),
    }


def mean(rows: List[Dict], key: str) -> float:
    return statistics.mean(r[key] for r in rows)


def pct_delta(new: float, old: float) -> float:
    if old == 0:
        return 0.0
    return 100.0 * (new - old) / old


def fmt_f(v: float, digits: int = 1) -> str:
    return f"{v:.{digits}f}"


def main() -> int:
    if not VROOM_SCRIPTS.exists():
        raise FileNotFoundError(f"vroom-scripts not found at {VROOM_SCRIPTS}")

    for required in (MASTER_IMAGE, CURRENT_IMAGE):
        run_cmd(["docker", "image", "inspect", required])

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    INPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("Preparing benchmark inputs...")
    benchmark_cases = []
    for spec in INSTANCES:
        base_json = ensure_converted(spec)

        default_path = build_variant(base_json, spec, DEFAULT_MODE)
        benchmark_cases.append((spec, DEFAULT_MODE, default_path, [MASTER_IMAGE, CURRENT_IMAGE]))

        for mode in EXTRA_MODES[spec.kind]:
            mode_path = build_variant(base_json, spec, mode)
            benchmark_cases.append((spec, mode, mode_path, [CURRENT_IMAGE]))

    runs_csv = RESULTS_DIR / "real_benchmark_runs.csv"

    rows: List[Dict] = []

    print(f"Running benchmarks: {len(benchmark_cases)} cases x {RUNS} runs")
    for spec, mode, input_path, images in benchmark_cases:
        info = inspect_input(input_path)
        for run_idx in range(1, RUNS + 1):
            for image in images:
                metrics = solve_once(image, input_path)
                row = {
                    "suite": spec.suite,
                    "family": spec.family,
                    "instance": spec.label,
                    "kind": spec.kind,
                    "mode": mode.mode,
                    "image": image,
                    "run": run_idx,
                    "vehicles": info["vehicles"],
                    "jobs": info["jobs"],
                    "shipments": info["shipments"],
                    "matrix_dim": info["matrix_dim"],
                    **metrics,
                }
                rows.append(row)
                print(
                    f"  [{image}] {spec.suite}/{spec.label} {mode.mode} run {run_idx}: "
                    f"solve={metrics['solving_ms']:.1f}ms routes={metrics['routes']:.0f}"
                )

    csv_fields = [
        "suite",
        "family",
        "instance",
        "kind",
        "mode",
        "image",
        "run",
        "vehicles",
        "jobs",
        "shipments",
        "matrix_dim",
        "loading_ms",
        "solving_ms",
        "routing_ms",
        "wall_ms",
        "cost",
        "duration",
        "waiting_time",
        "unassigned",
        "routes",
    ]

    with open(runs_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=csv_fields)
        writer.writeheader()
        writer.writerows(rows)

    # Aggregate helper maps.
    grouped: Dict[tuple, List[Dict]] = {}
    for row in rows:
        key = (row["suite"], row["instance"], row["mode"], row["image"])
        grouped.setdefault(key, []).append(row)

    report_lines: List[str] = []
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S %Z")
    report_lines.append("# Real-Life Timing Objective Benchmark Report")
    report_lines.append("")
    report_lines.append(f"Generated: {now}")
    report_lines.append(f"Runs per case: {RUNS}")
    report_lines.append(f"Master image: `{MASTER_IMAGE}` ({docker_image_id(MASTER_IMAGE)})")
    report_lines.append(f"Current image: `{CURRENT_IMAGE}` ({docker_image_id(CURRENT_IMAGE)})")
    report_lines.append("")
    report_lines.append("## Instance Matrix")
    report_lines.append("")
    report_lines.append("| Suite | Family | Instance | Vehicles | Jobs | Shipments | Matrix |")
    report_lines.append("|---|---|---:|---:|---:|---:|---:|")

    seen_defaults = sorted(
        {
            (r["suite"], r["family"], r["instance"], r["vehicles"], r["jobs"], r["shipments"], r["matrix_dim"])
            for r in rows
            if r["mode"] == "default" and r["image"] == CURRENT_IMAGE
        }
    )
    for suite, family, inst, vehicles, jobs, shipments, matrix_dim in seen_defaults:
        report_lines.append(
            f"| {suite} | {family} | {inst} | {vehicles} | {jobs} | {shipments} | {matrix_dim} |"
        )

    report_lines.append("")
    report_lines.append("## Regression Check (Default Mode, Master vs Current)")
    report_lines.append("")
    report_lines.append(
        "| Suite | Instance | Routes | Master solve ms | Current solve ms | Delta % | Master cost | Current cost |"
    )
    report_lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")

    for spec in INSTANCES:
        key_master = (spec.suite, spec.label, "default", MASTER_IMAGE)
        key_current = (spec.suite, spec.label, "default", CURRENT_IMAGE)
        mr = grouped[key_master]
        cr = grouped[key_current]
        m_solve = mean(mr, "solving_ms")
        c_solve = mean(cr, "solving_ms")
        m_cost = mean(mr, "cost")
        c_cost = mean(cr, "cost")
        routes = round(mean(cr, "routes"))
        report_lines.append(
            "| "
            + f"{spec.suite} | {spec.label} | {routes} | {fmt_f(m_solve)} | {fmt_f(c_solve)} | "
            + f"{fmt_f(pct_delta(c_solve, m_solve))}% | {fmt_f(m_cost, 0)} | {fmt_f(c_cost, 0)} |"
        )

    report_lines.append("")
    report_lines.append("## Objective Modes (Current Image)")
    report_lines.append("")
    report_lines.append(
        "| Suite | Instance | Mode | Avg solve ms | Delta vs current default % | Avg routes | Avg cost |"
    )
    report_lines.append("|---|---:|---|---:|---:|---:|---:|")

    for spec in INSTANCES:
        base_key = (spec.suite, spec.label, "default", CURRENT_IMAGE)
        base_rows = grouped[base_key]
        base_solve = mean(base_rows, "solving_ms")

        for mode in EXTRA_MODES[spec.kind]:
            key = (spec.suite, spec.label, mode.mode, CURRENT_IMAGE)
            mode_rows = grouped[key]
            mode_solve = mean(mode_rows, "solving_ms")
            mode_cost = mean(mode_rows, "cost")
            mode_routes = mean(mode_rows, "routes")
            report_lines.append(
                "| "
                + f"{spec.suite} | {spec.label} | {mode.mode} | {fmt_f(mode_solve)} | "
                + f"{fmt_f(pct_delta(mode_solve, base_solve))}% | {fmt_f(mode_routes, 0)} | {fmt_f(mode_cost, 0)} |"
            )

    report_lines.append("")
    report_lines.append("## Notes")
    report_lines.append("")
    report_lines.append(
        "- `default` rows compare branch behavior against clean `master` using identical real benchmark inputs."
    )
    report_lines.append(
        "- Objective rows are current-branch only because `timing_objective` is the feature under test."
    )
    report_lines.append(
        "- For Li&Lim objective modes with `target_time`, pickup `target_time` was set to the midpoint of each pickup time window."
    )

    report_path = RESULTS_DIR / "REAL_BENCHMARK_REPORT.md"
    report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")

    print(f"Wrote raw runs: {runs_csv}")
    print(f"Wrote report:   {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
