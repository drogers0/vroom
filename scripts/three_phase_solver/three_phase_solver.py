"""Three-phase VROOM solve wrapper for soft requested times and slack collapse.

Requires VROOM v1.14+ with the ``-c`` (choose-eta) flag, which is activated
per-request via ``"options": {"c": true}``.

Key API detail: ``service_at``, ``service_after``, and ``service_before``
are properties of **vehicle steps** (``vehicle.steps[]``), *not* of
jobs/shipment objects.
"""

from __future__ import annotations

import requests
import copy


VROOM_URL = "http://localhost:3000"
EPSILON = 0  # default: no tolerance band (set via --epsilon)


def _clamp_tw(center: int, epsilon: int, lo: int, hi: int) -> list[int]:
    """Build a tight window [center +/- epsilon] clamped to [lo, hi]."""
    return [max(lo, center - epsilon), min(hi, center + epsilon)]


class VroomSolver:
    """Wraps a VROOM HTTP server with a three-phase solve strategy.

    Phase 0 -- unconstrained solve.
    Phase 1 -- tighten priority-stop time windows around requested times,
              preserve original window as hard bounds via ``service_after``
              / ``service_before`` on vehicle steps, re-solve in PLAN mode.
    Phase 2 -- freeze priority stops at Phase-1 arrival via ``service_at``,
              tighten non-priority counterparts to collapse onboard dwell,
              re-solve in PLAN mode.
    """

    def __init__(self, url: str = VROOM_URL, epsilon: int = EPSILON):
        self.url = url
        self.epsilon = epsilon

    def solve(self, problem: dict) -> dict:
        resp = requests.post(self.url, json=problem)
        if resp.status_code != 200:
            body = (
                resp.json()
                if "json" in resp.headers.get("content-type", "")
                else {}
            )
            raise RuntimeError(
                f"VROOM HTTP {resp.status_code}: "
                f"{body.get('error', resp.text[:200])}"
            )
        result = resp.json()
        if result.get("code") != 0:
            raise RuntimeError(
                f"VROOM error {result.get('code')}: {result.get('error')}"
            )
        return result

    # -- helpers --------------------------------------------------------------

    @staticmethod
    def _extract_plan_steps(solution: dict) -> dict[int, list[dict]]:
        """Pull ordered step list from a solution for PLAN mode."""
        vehicle_steps: dict[int, list[dict]] = {}
        for route in solution.get("routes", []):
            vid = route["vehicle"]
            steps = []
            for step in route["steps"]:
                if step["type"] in ("pickup", "delivery", "job"):
                    steps.append({"type": step["type"], "id": step["id"]})
            vehicle_steps[vid] = steps
        return vehicle_steps

    @staticmethod
    def _apply_plan_steps(
        problem: dict, vehicle_steps: dict[int, list[dict]]
    ) -> dict:
        """Set steps on vehicles and enable PLAN mode (``-c``)."""
        problem = copy.deepcopy(problem)
        for vehicle in problem["vehicles"]:
            vid = vehicle["id"]
            if vid in vehicle_steps:
                vehicle["steps"] = vehicle_steps[vid]
        problem["options"] = {"c": True}
        return problem

    @staticmethod
    def _scheduled_times(solution: dict) -> dict[tuple[int, str], int]:
        """Map (stop_id, type) -> effective arrival (arrival + waiting)."""
        times: dict[tuple[int, str], int] = {}
        for route in solution.get("routes", []):
            for step in route["steps"]:
                if step["type"] in ("pickup", "delivery", "job"):
                    times[(step["id"], step["type"])] = (
                        step["arrival"] + step.get("waiting_time", 0)
                    )
        return times

    # -- phases ---------------------------------------------------------------

    def phase_0(self, problem: dict) -> dict:
        """Phase 0: plain VRP solve."""
        return self.solve(problem)

    def phase_1(
        self,
        problem: dict,
        solution_0: dict,
        priority_stops: dict[int, dict],
    ) -> tuple[dict, dict]:
        """
        Phase 1 -- Requested-Time Coercion.

        For each priority stop:
          - Original time window -> hard bounds on the **vehicle step**
            (``service_after`` / ``service_before``).
          - Replace ``time_windows`` with a tight band around requested_time +/- epsilon.

        Re-solves in PLAN mode (``-c``) with route order locked from Phase 0.
        """
        vehicle_steps = self._extract_plan_steps(solution_0)
        problem_1 = self._apply_plan_steps(problem, vehicle_steps)

        # Build a lookup: shipment_id -> (shipment_index, priority_info)
        priority_by_id: dict[tuple[int, str], dict] = {}
        for idx, info in priority_stops.items():
            ship = problem["shipments"][idx]
            sid = ship[info["type"]]["id"]
            priority_by_id[(sid, info["type"])] = {
                "idx": idx,
                "orig_tw": ship[info["type"]]["time_windows"][0],
                "requested_time": info["requested_time"],
            }

        # Tighten time_windows on the shipment steps
        for idx, info in priority_stops.items():
            step = problem_1["shipments"][idx][info["type"]]
            req = info["requested_time"]
            step["time_windows"] = [[req - self.epsilon, req + self.epsilon]]

        # Add service_after / service_before to the vehicle steps
        for vehicle in problem_1["vehicles"]:
            for vstep in vehicle.get("steps", []):
                key = (vstep["id"], vstep["type"])
                pinfo = priority_by_id.get(key)
                if pinfo:
                    vstep["service_after"] = pinfo["orig_tw"][0]
                    vstep["service_before"] = pinfo["orig_tw"][1]

        solution_1 = self.solve(problem_1)
        return problem_1, solution_1

    def phase_2(
        self,
        original_problem: dict,
        problem_1: dict,
        solution_1: dict,
        priority_stops: dict[int, dict],
    ) -> tuple[dict, dict]:
        """
        Phase 2 -- Ride-Time Slack Collapse.

        * Lock route order from Phase 1 via ``steps``.
        * Pin each priority stop at its Phase-1 arrival using
          ``service_at`` on the vehicle step.
        * Tighten each non-priority counterpart's ``time_windows`` to
          collapse ride-time slack.  For pickup-priority -> shrink delivery
          window start up to the Phase-1 scheduled time.  For delivery-
          priority -> shrink pickup window end down to the Phase-1 scheduled
          time.  Original bounds are preserved as hard constraints via
          ``service_after`` / ``service_before`` on the vehicle step.
        * Re-solve in PLAN mode (``-c``).
        """
        vehicle_steps = self._extract_plan_steps(solution_1)
        problem_2 = self._apply_plan_steps(original_problem, vehicle_steps)

        scheduled = self._scheduled_times(solution_1)

        priority_pin: dict[tuple[int, str], int] = {}
        counterpart_bounds: dict[tuple[int, str], list] = {}

        for idx, info in priority_stops.items():
            shipment = problem_2["shipments"][idx]
            ptype = info["type"]
            other_type = "delivery" if ptype == "pickup" else "pickup"

            priority_step = shipment[ptype]
            other_step = shipment[other_type]

            # Pin priority stop at Phase-1 arrival
            pid = priority_step["id"]
            sched = scheduled.get((pid, ptype))
            if sched is not None:
                priority_step["time_windows"] = [[sched, sched]]
                priority_pin[(pid, ptype)] = sched

            # Tighten non-priority counterpart around the *priority stop's*
            # scheduled time +/- epsilon.  This creates soft pressure to collapse
            # ride-time slack: for pickup-priority the delivery is pulled
            # toward the pickup time (earlier); for delivery-priority the
            # pickup is pulled toward the delivery time (later).  Original
            # bounds are preserved as hard constraints via service_after /
            # service_before on the vehicle step.
            oid = other_step["id"]
            orig_tw = other_step["time_windows"][0]
            other_step["time_windows"] = [
                [sched - self.epsilon, sched + self.epsilon]
            ]
            counterpart_bounds[(oid, other_type)] = orig_tw

        # Annotate vehicle steps
        for vehicle in problem_2["vehicles"]:
            for vstep in vehicle.get("steps", []):
                key = (vstep["id"], vstep["type"])
                if key in priority_pin:
                    vstep["service_at"] = priority_pin[key]
                elif key in counterpart_bounds:
                    lo, hi = counterpart_bounds[key]
                    vstep["service_after"] = lo
                    vstep["service_before"] = hi

        solution_2 = self.solve(problem_2)
        return problem_2, solution_2

    # -- orchestrator ---------------------------------------------------------

    def three_phase_solve(
        self,
        problem: dict,
        priority_stops: dict[int, dict],
    ) -> tuple[dict, dict, dict, dict, dict]:
        """
        Run all three phases.

        Returns (solution_0, problem_1, solution_1, problem_2, solution_2).
        """
        solution_0 = self.phase_0(problem)
        problem_1, solution_1 = self.phase_1(problem, solution_0, priority_stops)
        problem_2, solution_2 = self.phase_2(
            problem, problem_1, solution_1, priority_stops
        )
        return solution_0, problem_1, solution_1, problem_2, solution_2
