"""Gantt-style visualization utilities for the three-phase VROOM solver."""

from __future__ import annotations

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np


# -- colour palette -----------------------------------------------------------

VEHICLE_COLORS = [
    "#4C72B0", "#DD8452", "#55A868", "#C44E52",
    "#8172B3", "#937860", "#DA8BC3", "#8C8C8C",
]
PRIORITY_COLOR = "#E03030"
REQUESTED_COLOR = "#FFD700"


def _fmt_time(seconds: int) -> str:
    """Format seconds-since-midnight as HH:MM."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    return f"{h:02d}:{m:02d}"


def _stop_label(step: dict) -> str:
    stype = step["type"]
    sid = step.get("id", "")
    if stype == "start":
        return "START"
    if stype == "end":
        return "END"
    prefix = "P" if stype == "pickup" else "D" if stype == "delivery" else "J"
    return f"{prefix}{sid}"


def _effective_arrival(step: dict) -> int:
    """Return the time service begins (arrival + waiting)."""
    return step["arrival"] + step.get("waiting_time", 0)


def _onboard_dwell(solution: dict) -> int:
    """Total onboard dwell: sum of waiting_time * passengers_on_board.

    Walks each route's steps, tracking the number of passengers currently
    in the vehicle.  At each step with waiting_time > 0, that wait is
    multiplied by the current passenger count.
    """
    total = 0
    for route in solution.get("routes", []):
        pax = 0
        for step in route["steps"]:
            wt = step.get("waiting_time", 0)
            if wt > 0 and pax > 0:
                total += wt * pax
            if step["type"] == "pickup":
                pax += 1
            elif step["type"] == "delivery":
                pax -= 1
    return total


# -- core drawing (renders onto a given Axes) ---------------------------------


def _draw_phase(
    ax,
    problem: dict,
    solution: dict,
    title: str,
    priority_stops: dict | None = None,
    original_problem: dict | None = None,
    epsilon: int = 0,
):
    """Draw a Gantt-style timeline for one phase onto *ax*."""
    routes = solution.get("routes", [])
    if not routes:
        ax.set_title(f"{title} (no routes)")
        return

    # Build lookups
    shipment_tw: dict[tuple[int, str], list] = {}
    shipment_orig_tw: dict[tuple[int, str], list] = {}
    for ship in problem.get("shipments", []):
        for stype in ("pickup", "delivery"):
            step = ship.get(stype, {})
            sid = step.get("id")
            if sid is None:
                continue
            shipment_tw[(sid, stype)] = step.get("time_windows", [])

    if original_problem is not None:
        for ship in original_problem.get("shipments", []):
            for stype in ("pickup", "delivery"):
                step = ship.get(stype, {})
                sid = step.get("id")
                if sid is None:
                    continue
                shipment_orig_tw[(sid, stype)] = step.get("time_windows", [])

    requested_map: dict[tuple[int, str], int] = {}
    if priority_stops:
        for idx, info in priority_stops.items():
            ship = problem["shipments"][idx]
            sid = ship[info["type"]]["id"]
            requested_map[(sid, info["type"])] = info["requested_time"]

    # -- Layout --
    ROW_HEIGHT = 1.0
    SUB_ROW_PAD = 0.08
    MIN_SUB_H = 0.10

    all_times: list[int] = []
    route_layouts: list[dict] = []
    y_cursor = 0.0
    y_centers: list[float] = []

    for route in routes:
        stops = [s for s in route["steps"] if s["type"] in ("pickup", "delivery", "job")]
        n_stops = max(len(stops), 1)
        sub_h = max(MIN_SUB_H, (ROW_HEIGHT - SUB_ROW_PAD * (n_stops + 1)) / n_stops)
        lane_h = sub_h * n_stops + SUB_ROW_PAD * (n_stops + 1)

        stop_offsets = {}
        for si, stop in enumerate(stops):
            stop_offsets[(stop["id"], stop["type"])] = (
                y_cursor + SUB_ROW_PAD + si * (sub_h + SUB_ROW_PAD) + sub_h / 2
            )

        y_centers.append(y_cursor + lane_h / 2)
        route_layouts.append({
            "y_base": y_cursor,
            "lane_h": lane_h,
            "sub_h": sub_h,
            "stop_offsets": stop_offsets,
        })
        y_cursor += lane_h + 0.3

        for step in route["steps"]:
            ea = _effective_arrival(step)
            all_times.append(ea)
            all_times.append(ea + step.get("service", 0))

    for tw_list in shipment_tw.values():
        for tw in tw_list:
            all_times.extend(tw)
    for tw_list in shipment_orig_tw.values():
        for tw in tw_list:
            all_times.extend(tw)
    for rt in requested_map.values():
        all_times.append(rt)

    t_min = min(all_times) - 600
    t_max = max(all_times) + 600

    # -- Draw each route --
    for ri, route in enumerate(routes):
        layout = route_layouts[ri]
        vid = route["vehicle"]
        color = VEHICLE_COLORS[ri % len(VEHICLE_COLORS)]
        steps = route["steps"]
        sub_h = layout["sub_h"]
        offsets = layout["stop_offsets"]

        ax.axhspan(
            layout["y_base"], layout["y_base"] + layout["lane_h"],
            color=color, alpha=0.04,
        )

        task_steps = [s for s in steps if s["type"] in ("pickup", "delivery", "job")]
        for j in range(len(task_steps) - 1):
            s1 = task_steps[j]
            s2 = task_steps[j + 1]
            dep = _effective_arrival(s1) + s1.get("service", 0)
            arr = _effective_arrival(s2)
            y1 = offsets[(s1["id"], s1["type"])]
            y2 = offsets[(s2["id"], s2["type"])]
            if arr > dep:
                ax.annotate(
                    "", xy=(arr, y2), xytext=(dep, y1),
                    arrowprops=dict(
                        arrowstyle="-|>", color=color, alpha=0.3,
                        lw=1.2, connectionstyle="arc3,rad=0.1",
                    ),
                )

        for step in steps:
            if step["type"] in ("start", "end"):
                ax.plot(
                    _effective_arrival(step), y_centers[ri], marker="|", color=color,
                    markersize=14, markeredgewidth=2, zorder=4,
                )

        for step in steps:
            if step["type"] not in ("pickup", "delivery", "job"):
                continue

            key = (step["id"], step["type"])
            y = offsets[key]
            is_priority = key in requested_map

            for otw in shipment_orig_tw.get(key, []):
                ax.barh(
                    y, otw[1] - otw[0], left=otw[0], height=sub_h * 0.9,
                    facecolor="none", edgecolor="grey", linewidth=1.0,
                    linestyle="--", zorder=2,
                )

            for tw in shipment_tw.get(key, []):
                fc = PRIORITY_COLOR if is_priority else color
                ax.barh(
                    y, tw[1] - tw[0], left=tw[0], height=sub_h * 0.9,
                    facecolor=fc, alpha=0.15, edgecolor=fc,
                    linewidth=1.2, zorder=3,
                )

            if is_priority:
                rt = requested_map[key]
                if epsilon > 0 and original_problem is not None:
                    ax.plot(
                        [rt - epsilon, rt + epsilon], [y, y],
                        color=PRIORITY_COLOR, linewidth=2.5, alpha=0.6,
                        zorder=6, solid_capstyle="round",
                    )
                    for edge in (rt - epsilon, rt + epsilon):
                        ax.plot(
                            edge, y, marker="|", color=PRIORITY_COLOR,
                            markersize=8, markeredgewidth=1.5, zorder=6,
                        )
                ax.plot(
                    rt, y, marker="D", color=REQUESTED_COLOR,
                    markersize=8, zorder=7, markeredgecolor="black",
                    markeredgewidth=0.8,
                )

            ea = _effective_arrival(step)
            wt = step.get("waiting_time", 0)
            marker = "v" if step["type"] == "pickup" else "^" if step["type"] == "delivery" else "o"
            mc = PRIORITY_COLOR if is_priority else color

            # -- Waiting-time line (dotted, leading into the marker) --
            if wt > 0:
                ax.plot(
                    [step["arrival"], ea], [y, y],
                    color=mc, linewidth=1.5, linestyle=":",
                    alpha=0.5, zorder=5,
                )

            ax.plot(
                ea, y, marker=marker, color=mc,
                markersize=10, zorder=8, markeredgecolor="white",
                markeredgewidth=0.6,
            )

            svc = step.get("service", 0)
            if svc > 0:
                ax.barh(
                    y, svc, left=ea, height=sub_h * 0.15,
                    color=mc, alpha=0.7, zorder=5,
                )

            lbl = _stop_label(step)
            ax.annotate(
                lbl,
                (ea, y),
                textcoords="offset points",
                xytext=(0, int(sub_h * 40) + 4),
                fontsize=7,
                ha="center",
                fontweight="bold" if is_priority else "normal",
                color=PRIORITY_COLOR if is_priority else "black",
            )

        ax.text(
            t_min - 60, y_centers[ri], f"V{vid}",
            ha="right", va="center", fontsize=9, fontweight="bold", color=color,
        )

    # -- Axes formatting --
    tick_step = max(300, ((t_max - t_min) // 15 // 300) * 300)
    ticks = np.arange(
        (t_min // tick_step) * tick_step,
        t_max + tick_step,
        tick_step,
    )
    ax.set_xticks(ticks)
    ax.set_xticklabels([_fmt_time(t) for t in ticks], rotation=45, fontsize=7)
    ax.set_xlim(t_min, t_max)

    ax.set_yticks([yc for yc in y_centers])
    ax.set_yticklabels([f"Route {r['vehicle']}" for r in routes])
    ax.invert_yaxis()

    ax.set_xlabel("Time", fontsize=8)
    ax.set_title(title, fontweight="bold", fontsize=10)
    ax.grid(axis="x", alpha=0.25)


def _add_legend(ax, epsilon: int = 0, has_original: bool = False):
    """Add a shared legend to *ax*."""
    legend_elements = []
    if has_original:
        legend_elements.append(
            mpatches.FancyBboxPatch((0, 0), 1, 1, boxstyle="round,pad=0.1",
                                    facecolor="none", edgecolor="grey",
                                    linestyle="--", label="Original window"),
        )
    legend_elements += [
        mpatches.Patch(facecolor=VEHICLE_COLORS[0], alpha=0.15,
                       edgecolor=VEHICLE_COLORS[0], label="Time window"),
        plt.Line2D([0], [0], marker="D", color="w", markerfacecolor=REQUESTED_COLOR,
                   markeredgecolor="black", markersize=8, label="Requested time"),
    ]
    if epsilon > 0 and has_original:
        legend_elements.append(
            plt.Line2D([0], [0], color=PRIORITY_COLOR, linewidth=2.5,
                       alpha=0.6, solid_capstyle="round",
                       label=f"Epsilon band (+/-{epsilon}s)"),
        )
    legend_elements += [
        plt.Line2D([0], [0], marker="v", color="w", markerfacecolor="grey",
                   markersize=8, label="Pickup"),
        plt.Line2D([0], [0], marker="^", color="w", markerfacecolor="grey",
                   markersize=8, label="Delivery"),
        plt.Line2D([0], [0], color="grey", linewidth=1.5, linestyle=":",
                   alpha=0.5, label="Waiting"),
    ]
    ax.legend(handles=legend_elements, loc="upper right", fontsize=7, framealpha=0.8)


# -- public API ---------------------------------------------------------------


def visualize_solution(
    problem: dict,
    solution: dict,
    title: str,
    priority_stops: dict | None = None,
    original_problem: dict | None = None,
    epsilon: int = 0,
    figsize: tuple = (18, 6),
    save_path: str | None = None,
):
    """Draw a single-phase Gantt timeline and save / show it."""
    routes = solution.get("routes", [])
    if not routes:
        print(f"  [{title}] no routes to visualize")
        return

    fig, ax = plt.subplots(figsize=figsize)
    _draw_phase(ax, problem, solution, title,
                priority_stops=priority_stops,
                original_problem=original_problem,
                epsilon=epsilon)
    _add_legend(ax, epsilon=epsilon, has_original=original_problem is not None)

    plt.tight_layout()
    if save_path:
        fig.savefig(save_path, dpi=150)
        print(f"  Saved -> {save_path}")
    else:
        plt.show()
    plt.close(fig)


def visualize_all_phases(
    name: str,
    problem: dict,
    problem_1: dict,
    problem_2: dict,
    solution_0: dict,
    solution_1: dict,
    solution_2: dict,
    priority_stops: dict | None = None,
    epsilon: int = 0,
    figsize: tuple = (18, 8),
    save_path: str | None = None,
):
    """Overlay all three phases on a single plot.

    Color scheme per phase:
      Pickups  -- Phase 0: red, Phase 1: orange, Phase 2: purple
      Deliveries -- Phase 0: blue, Phase 1: cyan, Phase 2: green
    """
    # Phase colours: (pickup_color, delivery_color)
    PHASE_COLORS = [
        ("#D62728", "#1F77B4"),   # P0: red / blue
        ("#FF7F0E", "#17BECF"),   # P1: orange / cyan
        ("#9467BD", "#2CA02C"),   # P2: purple / green
    ]
    PHASE_LABELS = ["Phase 0", "Phase 1", "Phase 2"]

    solutions = [solution_0, solution_1, solution_2]

    # Use Phase 0 route structure for layout (all phases share the same
    # route order after PLAN mode locks it).
    routes_0 = solution_0.get("routes", [])
    if not routes_0:
        print(f"  [{name}] no routes to visualize")
        return

    # Requested times
    requested_map: dict[tuple[int, str], int] = {}
    if priority_stops:
        for idx, info in priority_stops.items():
            ship = problem["shipments"][idx]
            sid = ship[info["type"]]["id"]
            requested_map[(sid, info["type"])] = info["requested_time"]

    # Time-window lookup from original problem (for background shading)
    shipment_tw: dict[tuple[int, str], list] = {}
    for ship in problem.get("shipments", []):
        for stype in ("pickup", "delivery"):
            step = ship.get(stype, {})
            sid = step.get("id")
            if sid is None:
                continue
            shipment_tw[(sid, stype)] = step.get("time_windows", [])

    fig, ax = plt.subplots(figsize=figsize)

    # -- Layout (from Phase 0) --
    ROW_HEIGHT = 1.0
    SUB_ROW_PAD = 0.08
    MIN_SUB_H = 0.10

    all_times: list[int] = []
    route_layouts: list[dict] = []
    y_cursor = 0.0
    y_centers: list[float] = []

    for route in routes_0:
        stops = [s for s in route["steps"] if s["type"] in ("pickup", "delivery", "job")]
        n_stops = max(len(stops), 1)
        sub_h = max(MIN_SUB_H, (ROW_HEIGHT - SUB_ROW_PAD * (n_stops + 1)) / n_stops)
        lane_h = sub_h * n_stops + SUB_ROW_PAD * (n_stops + 1)

        stop_offsets = {}
        for si, stop in enumerate(stops):
            stop_offsets[(stop["id"], stop["type"])] = (
                y_cursor + SUB_ROW_PAD + si * (sub_h + SUB_ROW_PAD) + sub_h / 2
            )

        y_centers.append(y_cursor + lane_h / 2)
        route_layouts.append({
            "y_base": y_cursor,
            "lane_h": lane_h,
            "sub_h": sub_h,
            "stop_offsets": stop_offsets,
        })
        y_cursor += lane_h + 0.3

    # Collect all times across all phases for axis range
    for sol in solutions:
        for route in sol.get("routes", []):
            for step in route["steps"]:
                ea = _effective_arrival(step)
                all_times.append(ea)
                all_times.append(ea + step.get("service", 0))
    for tw_list in shipment_tw.values():
        for tw in tw_list:
            all_times.extend(tw)
    for rt in requested_map.values():
        all_times.append(rt)

    t_min = min(all_times) - 600
    t_max = max(all_times) + 600

    # -- Background: route bands + original time windows --
    for ri, route in enumerate(routes_0):
        layout = route_layouts[ri]
        vid = route["vehicle"]
        offsets = layout["stop_offsets"]
        sub_h = layout["sub_h"]

        ax.axhspan(
            layout["y_base"], layout["y_base"] + layout["lane_h"],
            color="#888888", alpha=0.03,
        )

        # Draw original time windows coloured by stop type (Phase 0 palette)
        p0_pickup, p0_delivery = PHASE_COLORS[0]
        for step in route["steps"]:
            if step["type"] not in ("pickup", "delivery", "job"):
                continue
            key = (step["id"], step["type"])
            if key not in offsets:
                continue
            y = offsets[key]
            tw_color = p0_pickup if step["type"] == "pickup" else p0_delivery
            for tw in shipment_tw.get(key, []):
                ax.barh(
                    y, tw[1] - tw[0], left=tw[0], height=sub_h * 0.9,
                    facecolor=tw_color, alpha=0.10, edgecolor=tw_color,
                    linewidth=1.0, zorder=1,
                )

        # Requested time diamonds
        for step in route["steps"]:
            if step["type"] not in ("pickup", "delivery", "job"):
                continue
            key = (step["id"], step["type"])
            if key in requested_map and key in offsets:
                y = offsets[key]
                rt = requested_map[key]
                ax.plot(
                    rt, y, marker="D", color=REQUESTED_COLOR,
                    markersize=9, zorder=15, markeredgecolor="black",
                    markeredgewidth=0.8,
                )

        # Stop labels (from Phase 0 positions)
        for step in route["steps"]:
            if step["type"] not in ("pickup", "delivery", "job"):
                continue
            key = (step["id"], step["type"])
            if key not in offsets:
                continue
            y = offsets[key]
            is_priority = key in requested_map
            lbl = _stop_label(step)
            ax.text(
                t_min + 30, y, lbl,
                fontsize=7, ha="left", va="center",
                fontweight="bold" if is_priority else "normal",
                color="#333333",
                zorder=10,
            )

        # Vehicle label
        ax.text(
            t_min - 60, y_centers[ri], f"V{vid}",
            ha="right", va="center", fontsize=9, fontweight="bold",
            color="#444444",
        )

    # -- Draw each phase's arrivals --
    for pi, sol in enumerate(solutions):
        pickup_c, delivery_c = PHASE_COLORS[pi]
        # Small vertical offset per phase so markers don't overlap exactly
        dy = (pi - 1) * 0.04

        for route in sol.get("routes", []):
            vid = route["vehicle"]
            # Find the matching layout by vehicle id
            layout_idx = None
            for ri, r0 in enumerate(routes_0):
                if r0["vehicle"] == vid:
                    layout_idx = ri
                    break
            if layout_idx is None:
                continue

            offsets = route_layouts[layout_idx]["stop_offsets"]
            sub_h = route_layouts[layout_idx]["sub_h"]

            task_steps = [s for s in route["steps"]
                          if s["type"] in ("pickup", "delivery", "job")]

            for step in task_steps:
                key = (step["id"], step["type"])
                if key not in offsets:
                    continue
                y = offsets[key] + dy
                ea = _effective_arrival(step)
                wt = step.get("waiting_time", 0)

                mc = pickup_c if step["type"] == "pickup" else delivery_c
                marker = "v" if step["type"] == "pickup" else "^"

                # -- Waiting-time line (dotted, leading into the marker) --
                if wt > 0:
                    ax.plot(
                        [step["arrival"], ea], [y, y],
                        color=mc, linewidth=1.5, linestyle=":",
                        alpha=0.5, zorder=11 + pi,
                    )

                ax.plot(
                    ea, y, marker=marker, color=mc,
                    markersize=10, zorder=12 + pi,
                    markeredgecolor="white", markeredgewidth=0.6,
                )

            # Draw travel arrows (thin, phase-colored)
            for j in range(len(task_steps) - 1):
                s1 = task_steps[j]
                s2 = task_steps[j + 1]
                k1 = (s1["id"], s1["type"])
                k2 = (s2["id"], s2["type"])
                if k1 not in offsets or k2 not in offsets:
                    continue
                dep = _effective_arrival(s1) + s1.get("service", 0)
                arr = _effective_arrival(s2)
                y1 = offsets[k1] + dy
                y2 = offsets[k2] + dy
                # Use the pickup color of that phase for arrows
                arrow_c = PHASE_COLORS[pi][0]
                if arr > dep:
                    ax.annotate(
                        "", xy=(arr, y2), xytext=(dep, y1),
                        arrowprops=dict(
                            arrowstyle="-|>", color=arrow_c,
                            alpha=0.25 + pi * 0.1,
                            lw=1.0, connectionstyle="arc3,rad=0.08",
                        ),
                    )

    # -- Axes formatting --
    tick_step = max(300, ((t_max - t_min) // 15 // 300) * 300)
    ticks = np.arange(
        (t_min // tick_step) * tick_step,
        t_max + tick_step,
        tick_step,
    )
    ax.set_xticks(ticks)
    ax.set_xticklabels([_fmt_time(t) for t in ticks], rotation=45, fontsize=7)
    ax.set_xlim(t_min, t_max)

    ax.set_yticks(y_centers)
    ax.set_yticklabels([f"Route {r['vehicle']}" for r in routes_0])
    ax.invert_yaxis()

    ax.set_xlabel("Time")
    ax.set_title(f"{name} -- All Phases", fontweight="bold", fontsize=12)
    ax.grid(axis="x", alpha=0.25)

    # -- Total delta from requested per phase --
    phase_deltas = []
    for sol in solutions:
        total_abs_delta = 0
        for route in sol.get("routes", []):
            for step in route["steps"]:
                if step["type"] not in ("pickup", "delivery", "job"):
                    continue
                key = (step["id"], step["type"])
                if key in requested_map:
                    ea = _effective_arrival(step)
                    total_abs_delta += abs(ea - requested_map[key])
        phase_deltas.append(total_abs_delta)

    # -- Onboard dwell per phase --
    phase_dwells = [_onboard_dwell(sol) for sol in solutions]

    delta_text = "Total |delta| from requested:  "
    for pi, label in enumerate(PHASE_LABELS):
        delta_text += f"{label}: {phase_deltas[pi]}s"
        if pi < len(PHASE_LABELS) - 1:
            delta_text += "  ->  "
    dwell_text = "Onboard dwell (wait x pax):  "
    for pi, label in enumerate(PHASE_LABELS):
        dwell_text += f"{label}: {phase_dwells[pi]}s"
        if pi < len(PHASE_LABELS) - 1:
            dwell_text += "  ->  "
    combined_text = f"{delta_text}\n{dwell_text}"
    ax.text(
        0.5, -0.06, combined_text,
        transform=ax.transAxes, ha="center", va="top",
        fontsize=8, fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.4", facecolor="#F0F0F0",
                  edgecolor="#CCCCCC", alpha=0.9),
    )

    # -- Legend --
    p0_pickup, p0_delivery = PHASE_COLORS[0]
    legend_elements = [
        mpatches.Patch(facecolor=p0_pickup, alpha=0.15, edgecolor=p0_pickup,
                       label="Pickup window"),
        mpatches.Patch(facecolor=p0_delivery, alpha=0.15, edgecolor=p0_delivery,
                       label="Delivery window"),
        plt.Line2D([0], [0], marker="D", color="w", markerfacecolor=REQUESTED_COLOR,
                   markeredgecolor="black", markersize=8, label="Requested time"),
    ]
    for pi, label in enumerate(PHASE_LABELS):
        pc, dc = PHASE_COLORS[pi]
        legend_elements.append(
            plt.Line2D([0], [0], marker="v", color="w", markerfacecolor=pc,
                       markeredgecolor="white", markersize=8,
                       label=f"{label} pickup"),
        )
        legend_elements.append(
            plt.Line2D([0], [0], marker="^", color="w", markerfacecolor=dc,
                       markeredgecolor="white", markersize=8,
                       label=f"{label} delivery"),
        )
    legend_elements.append(
        plt.Line2D([0], [0], color="grey", linewidth=1.5, linestyle=":",
                   alpha=0.5, label="Waiting"),
    )
    ax.legend(handles=legend_elements, loc="upper right", fontsize=7,
              framealpha=0.9, ncol=2)

    plt.tight_layout()
    plt.subplots_adjust(bottom=0.17)
    if save_path:
        fig.savefig(save_path, dpi=150)
        print(f"  Saved -> {save_path}")
    else:
        plt.show()
    plt.close(fig)


# -- summary table ------------------------------------------------------------


def print_summary(
    solution: dict,
    label: str,
    priority_stops: dict | None = None,
    problem: dict | None = None,
):
    """Print a compact text summary of the solution."""
    print(f"\n{'=' * 60}")
    print(f"  {label}")
    print(f"{'=' * 60}")

    summary = solution.get("summary", {})
    print(f"  Cost: {summary.get('cost', '?')}")
    print(f"  Unassigned: {summary.get('unassigned', 0)}")
    print(f"  Duration: {summary.get('duration', '?')}s")
    print(f"  Waiting: {summary.get('waiting_time', '?')}s")
    violations = summary.get("violations", [])
    if violations:
        print(f"  Violations: {violations}")

    requested_map: dict[tuple[int, str], int] = {}
    if priority_stops and problem:
        for idx, info in priority_stops.items():
            ship = problem["shipments"][idx]
            sid = ship[info["type"]]["id"]
            requested_map[(sid, info["type"])] = info["requested_time"]

    for route in solution.get("routes", []):
        vid = route["vehicle"]
        steps_desc = []
        for step in route["steps"]:
            if step["type"] in ("start", "end"):
                continue
            lbl = _stop_label(step)
            ea = _effective_arrival(step)
            arr = _fmt_time(ea)
            key = (step.get("id"), step["type"])
            extra = ""
            if key in requested_map:
                delta = ea - requested_map[key]
                extra = f" (req {_fmt_time(requested_map[key])}, delta={delta:+d}s)"
            steps_desc.append(f"    {arr} {lbl}{extra}")
        print(f"\n  Vehicle {vid}:")
        print("\n".join(steps_desc))
    print()
