#!/usr/bin/env python3
"""
Three-Phase VROOM Solver -- scenario runner.

Runs rideshare scenarios through the Phase 0 -> 1 -> 2 pipeline,
printing summaries and saving Gantt-style visualizations to output/.

Usage:
    python run_scenarios.py                              # run all scenarios
    python run_scenarios.py rideshare_all_pickup_priority # run one scenario
    python run_scenarios.py --list                        # list available scenarios
    python run_scenarios.py --epsilon 600                 # override epsilon (seconds)
    python run_scenarios.py --per-phase                   # also save individual phase images
"""

import argparse
import json
import os
import sys

from three_phase_solver import VroomSolver
from gantt_visualizer import visualize_solution, visualize_all_phases, print_summary
from rideshare_scenarios import ALL_SCENARIOS


def run_scenario(
    name: str,
    solver: VroomSolver,
    output_dir: str,
    show: bool = False,
    epsilon: int = 0,
    per_phase: bool = False,
):
    builder = ALL_SCENARIOS[name]
    problem, priority_stops, description = builder()

    print(f"\n{'=' * 70}")
    print(f"  SCENARIO: {name}")
    print(f"  {description}")
    print(f"{'=' * 70}")

    # -- Phase 0 --------------------------------------------------------------
    print("\n> Phase 0 -- Initial solve ...")
    solution_0 = solver.phase_0(problem)
    print_summary(solution_0, "Phase 0 -- Initial Solve", priority_stops, problem)
    if per_phase:
        save = None if show else os.path.join(output_dir, f"{name}_phase0.png")
        visualize_solution(
            problem, solution_0,
            f"{name} -- Phase 0: Initial Solve",
            priority_stops=priority_stops,
            epsilon=epsilon,
            save_path=save,
        )

    # -- Phase 1 --------------------------------------------------------------
    print("> Phase 1 -- Requested-time coercion ...")
    problem_1, solution_1 = solver.phase_1(problem, solution_0, priority_stops)
    print_summary(solution_1, "Phase 1 -- Requested-Time Coercion", priority_stops, problem)
    if per_phase:
        save = None if show else os.path.join(output_dir, f"{name}_phase1.png")
        visualize_solution(
            problem_1, solution_1,
            f"{name} -- Phase 1: Requested-Time Coercion",
            priority_stops=priority_stops,
            original_problem=problem,
            epsilon=epsilon,
            save_path=save,
        )

    # -- Phase 2 --------------------------------------------------------------
    print("> Phase 2 -- Ride-time slack collapse ...")
    problem_2, solution_2 = solver.phase_2(
        problem, problem_1, solution_1, priority_stops
    )
    print_summary(solution_2, "Phase 2 -- Ride-Time Slack Collapse", priority_stops, problem)
    if per_phase:
        save = None if show else os.path.join(output_dir, f"{name}_phase2.png")
        visualize_solution(
            problem_2, solution_2,
            f"{name} -- Phase 2: Ride-Time Slack Collapse",
            priority_stops=priority_stops,
            original_problem=problem,
            epsilon=epsilon,
            save_path=save,
        )

    # -- Combined image (always generated) ------------------------------------
    save = None if show else os.path.join(output_dir, f"{name}.png")
    visualize_all_phases(
        name, problem, problem_1, problem_2,
        solution_0, solution_1, solution_2,
        priority_stops=priority_stops,
        epsilon=epsilon,
        save_path=save,
    )

    # -- Save VROOM payloads as JSON ------------------------------------------
    if not show:
        for label, payload in [
            ("phase0", problem),
            ("phase1", problem_1),
            ("phase2", problem_2),
        ]:
            path = os.path.join(output_dir, f"{name}_{label}.json")
            with open(path, "w") as f:
                json.dump(payload, f, indent=2)
        print(f"  Saved payloads -> {output_dir}/{name}_phase{{0,1,2}}.json")

    # -- Comparison -----------------------------------------------------------
    _print_comparison(solution_0, solution_1, solution_2, priority_stops, problem)


def _print_comparison(sol0, sol1, sol2, priority_stops, problem):
    """Side-by-side delta table for priority stops across phases."""
    requested: dict[tuple[int, str], int] = {}
    for idx, info in priority_stops.items():
        ship = problem["shipments"][idx]
        sid = ship[info["type"]]["id"]
        requested[(sid, info["type"])] = info["requested_time"]

    def _arrival_map(sol):
        m = {}
        for route in sol.get("routes", []):
            for step in route["steps"]:
                if step["type"] in ("pickup", "delivery"):
                    m[(step["id"], step["type"])] = (
                        step["arrival"] + step.get("waiting_time", 0)
                    )
        return m

    arr0 = _arrival_map(sol0)
    arr1 = _arrival_map(sol1)
    arr2 = _arrival_map(sol2)

    print(f"\n{'-' * 60}")
    print("  Priority-stop deltas (arrival - requested, in seconds)")
    print(f"{'-' * 60}")
    print(f"  {'Stop':<12} {'Requested':>10} {'Phase0 d':>10} {'Phase1 d':>10} {'Phase2 d':>10}")
    for key, req in sorted(requested.items()):
        sid, stype = key
        label = f"{'P' if stype == 'pickup' else 'D'}{sid}"
        d0 = arr0.get(key, 0) - req
        d1 = arr1.get(key, 0) - req
        d2 = arr2.get(key, 0) - req
        h, m = divmod(req, 3600)
        m //= 60
        print(f"  {label:<12} {h:02d}:{m:02d}      {d0:>+8d}s {d1:>+8d}s {d2:>+8d}s")

    w0 = sol0.get("summary", {}).get("waiting_time", 0)
    w1 = sol1.get("summary", {}).get("waiting_time", 0)
    w2 = sol2.get("summary", {}).get("waiting_time", 0)
    print(f"\n  Total waiting time:  Phase0={w0}s  Phase1={w1}s  Phase2={w2}s")

    d0 = sol0.get("summary", {}).get("duration", 0)
    d1 = sol1.get("summary", {}).get("duration", 0)
    d2 = sol2.get("summary", {}).get("duration", 0)
    print(f"  Total duration:      Phase0={d0}s  Phase1={d1}s  Phase2={d2}s")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Three-Phase VROOM Solver -- run rideshare scenarios"
    )
    parser.add_argument(
        "scenarios",
        nargs="*",
        help="Scenario names to run (default: all)",
    )
    parser.add_argument("--list", action="store_true", help="List scenarios and exit")
    parser.add_argument("--url", default="http://localhost:3000", help="VROOM server URL")
    parser.add_argument("--epsilon", type=int, default=0, help="Epsilon tolerance in seconds (default 0)")
    parser.add_argument("--show", action="store_true", help="Show plots interactively instead of saving")
    parser.add_argument("--per-phase", action="store_true", help="Also save individual phase images")
    parser.add_argument("--output", default="output", help="Output directory for plots")
    args = parser.parse_args()

    if args.list:
        print("Available scenarios:")
        for name, fn in ALL_SCENARIOS.items():
            _, _, desc = fn()
            print(f"  {name:40s}  {desc.splitlines()[0]}")
        return

    names = args.scenarios or list(ALL_SCENARIOS.keys())
    for name in names:
        if name not in ALL_SCENARIOS:
            print(f"Unknown scenario: {name}", file=sys.stderr)
            print(f"Available: {', '.join(ALL_SCENARIOS)}", file=sys.stderr)
            sys.exit(1)

    output_dir = args.output
    if not args.show:
        os.makedirs(output_dir, exist_ok=True)

    solver = VroomSolver(url=args.url, epsilon=args.epsilon)

    for name in names:
        try:
            run_scenario(
                name, solver, output_dir,
                show=args.show, epsilon=args.epsilon, per_phase=args.per_phase,
            )
        except Exception as e:
            print(f"\n  ERROR: {name} failed: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    main()
