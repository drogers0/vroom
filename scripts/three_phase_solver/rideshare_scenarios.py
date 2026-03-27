"""
Rideshare test scenarios for the three-phase VROOM solver.

Each scenario returns (problem, priority_stops, description).
All times are in seconds from midnight.

Convention: every time window is exactly 1 hour wide; requested times
sit at the 30-minute mark of their window.

Scenarios:
  - rideshare_all_pickup_priority: all passengers prefer a pickup time
  - rideshare_mixed_priority:      mix of pickup and dropoff preferences
  - rideshare_all_dropoff_priority: all passengers prefer a dropoff time
  - rideshare_dwell_collapse:      demonstrates onboard dwell reduction
"""

PROFILE = "car"


def _t(h: int, m: int = 0) -> int:
    """Shorthand: hours + minutes -> seconds from midnight."""
    return h * 3600 + m * 60


def _vehicle(vid: int, capacity: list, time_window: list, start: int = 0, end: int = 0) -> dict:
    return {
        "id": vid,
        "profile": PROFILE,
        "start_index": start,
        "end_index": end,
        "capacity": capacity,
        "time_window": time_window,
    }


def _matrices(durations: list) -> dict:
    return {"matrices": {PROFILE: {"durations": durations}}}


def _rideshare_base_problem():
    """Shared problem definition for the standard rideshare variants.

    2 vehicles, 5 passengers.
    0 = depot, 1-5 = pickup locations, 6-10 = dropoff locations.
    """
    n = 11
    coords = [
        (0, 0),    # depot
        (2, 5),    # pickup 1
        (4, 3),    # pickup 2
        (1, 8),    # pickup 3
        (6, 2),    # pickup 4
        (3, 7),    # pickup 5
        (8, 6),    # dropoff 1
        (7, 1),    # dropoff 2
        (9, 8),    # dropoff 3
        (5, 5),    # dropoff 4
        (8, 3),    # dropoff 5
    ]

    durations = []
    for i in range(n):
        row = []
        for j in range(n):
            dx = coords[i][0] - coords[j][0]
            dy = coords[i][1] - coords[j][1]
            dist = (dx * dx + dy * dy) ** 0.5
            row.append(int(dist * 120))
        durations.append(row)

    return {
        "vehicles": [
            _vehicle(1, [3], [_t(17, 0), _t(20, 0)]),
            _vehicle(2, [3], [_t(17, 0), _t(20, 0)]),
        ],
        "shipments": [
            {  # Passenger 1 -- pickup 17:00-18:00, dropoff 17:30-18:30
                "amount": [1],
                "pickup": {
                    "id": 1,
                    "location_index": 1,
                    "service": 60,
                    "time_windows": [[_t(17, 0), _t(18, 0)]],
                },
                "delivery": {
                    "id": 1,
                    "location_index": 6,
                    "service": 60,
                    "time_windows": [[_t(17, 30), _t(18, 30)]],
                },
            },
            {  # Passenger 2 -- pickup 17:15-18:15, dropoff 17:30-18:30
                "amount": [1],
                "pickup": {
                    "id": 2,
                    "location_index": 2,
                    "service": 60,
                    "time_windows": [[_t(17, 15), _t(18, 15)]],
                },
                "delivery": {
                    "id": 2,
                    "location_index": 7,
                    "service": 60,
                    "time_windows": [[_t(17, 30), _t(18, 30)]],
                },
            },
            {  # Passenger 3 -- pickup 17:00-18:00, dropoff 17:30-18:30
                "amount": [1],
                "pickup": {
                    "id": 3,
                    "location_index": 3,
                    "service": 60,
                    "time_windows": [[_t(17, 0), _t(18, 0)]],
                },
                "delivery": {
                    "id": 3,
                    "location_index": 8,
                    "service": 60,
                    "time_windows": [[_t(17, 30), _t(18, 30)]],
                },
            },
            {  # Passenger 4 -- pickup 17:30-18:30, dropoff 18:00-19:00
                "amount": [1],
                "pickup": {
                    "id": 4,
                    "location_index": 4,
                    "service": 60,
                    "time_windows": [[_t(17, 30), _t(18, 30)]],
                },
                "delivery": {
                    "id": 4,
                    "location_index": 9,
                    "service": 60,
                    "time_windows": [[_t(18, 0), _t(19, 0)]],
                },
            },
            {  # Passenger 5 -- pickup 17:15-18:15, dropoff 17:30-18:30
                "amount": [1],
                "pickup": {
                    "id": 5,
                    "location_index": 5,
                    "service": 60,
                    "time_windows": [[_t(17, 15), _t(18, 15)]],
                },
                "delivery": {
                    "id": 5,
                    "location_index": 10,
                    "service": 60,
                    "time_windows": [[_t(17, 30), _t(18, 30)]],
                },
            },
        ],
        **_matrices(durations),
    }


# ---------------------------------------------------------------------------
# Scenario: All Pickup Priority
#
# All 5 passengers have a preferred pickup time.
# ---------------------------------------------------------------------------


def rideshare_all_pickup_priority() -> tuple[dict, dict, str]:
    description = (
        "Rideshare (all pickup priority) -- 2 vehicles, 5 passengers.\n"
        "All passengers have a preferred pickup time."
    )

    problem = _rideshare_base_problem()

    priority_stops = {
        0: {"type": "pickup", "requested_time": _t(17, 30)},
        1: {"type": "pickup", "requested_time": _t(17, 45)},
        2: {"type": "pickup", "requested_time": _t(17, 30)},
        3: {"type": "pickup", "requested_time": _t(18, 0)},
        4: {"type": "pickup", "requested_time": _t(17, 45)},
    }

    return problem, priority_stops, description


# ---------------------------------------------------------------------------
# Scenario: Mixed Priority (some pickup, some dropoff)
#
# Passengers 1, 3 have preferred pickup times.
# Passengers 2, 4, 5 have preferred dropoff times.
# ---------------------------------------------------------------------------


def rideshare_mixed_priority() -> tuple[dict, dict, str]:
    description = (
        "Rideshare (mixed priority) -- 2 vehicles, 5 passengers.\n"
        "Passengers 1 & 3: pickup priority. Passengers 2, 4 & 5: dropoff priority."
    )

    problem = _rideshare_base_problem()

    priority_stops = {
        0: {"type": "pickup", "requested_time": _t(17, 30)},   # P1 pickup
        1: {"type": "delivery", "requested_time": _t(18, 0)},   # P2 dropoff
        2: {"type": "pickup", "requested_time": _t(17, 30)},   # P3 pickup
        3: {"type": "delivery", "requested_time": _t(18, 30)},  # P4 dropoff
        4: {"type": "delivery", "requested_time": _t(18, 0)},   # P5 dropoff
    }

    return problem, priority_stops, description


# ---------------------------------------------------------------------------
# Scenario: All Dropoff Priority
#
# All 5 passengers have a preferred dropoff (arrival) time.
# ---------------------------------------------------------------------------


def rideshare_all_dropoff_priority() -> tuple[dict, dict, str]:
    description = (
        "Rideshare (all dropoff priority) -- 2 vehicles, 5 passengers.\n"
        "All passengers have a preferred dropoff time."
    )

    problem = _rideshare_base_problem()

    priority_stops = {
        0: {"type": "delivery", "requested_time": _t(18, 0)},
        1: {"type": "delivery", "requested_time": _t(18, 0)},
        2: {"type": "delivery", "requested_time": _t(18, 0)},
        3: {"type": "delivery", "requested_time": _t(18, 30)},
        4: {"type": "delivery", "requested_time": _t(18, 0)},
    }

    return problem, priority_stops, description


# ---------------------------------------------------------------------------
# Scenario: Dwell Collapse
#
# 1 vehicle (capacity 3), 3 passengers.
#
# Route pattern:  P1 -> D1 -> P2(early) -> P3(late) -> D2 -> D3
#
# Passenger 1 is picked up and dropped off first (short trip near depot).
# The vehicle then picks up Passenger 2 (early window) and drives to
# Passenger 3's location -- but P3's window hasn't opened yet.  The vehicle
# waits with P2 on board -> onboard dwell.
#
# All passengers have delivery-priority.  Phase 2 pushes P2's pickup later
# (toward the delivery time), reducing the gap between P2 pickup and P3's
# window opening -- collapsing the dwell.
# ---------------------------------------------------------------------------


def rideshare_dwell_collapse() -> tuple[dict, dict, str]:
    description = (
        "Rideshare (dwell collapse) -- 1 vehicle, 3 passengers.\n"
        "Route: P1->D1->P2(early)->P3(late wait)->D2->D3.  Phase 2 collapses dwell."
    )

    # 0  = depot (west)
    # 1  = pickup 1 (near depot)
    # 2  = dropoff 1 (short trip east -- dropped before P2/P3)
    # 3  = pickup 2 (on the way east, right after D1)
    # 4  = pickup 3 (far east, late window -- vehicle waits here)
    # 5  = dropoff 2 (far east)
    # 6  = dropoff 3 (far east)
    coords = [
        (0, 5),    # 0  depot
        (2, 5),    # 1  pickup 1
        (5, 5),    # 2  dropoff 1
        (6, 5),    # 3  pickup 2 (right next to D1, on the way)
        (10, 5),   # 4  pickup 3 (far east, late window)
        (13, 4),   # 5  dropoff 2 (far east)
        (13, 6),   # 6  dropoff 3 (far east)
    ]
    n = len(coords)

    durations = []
    for i in range(n):
        row = []
        for j in range(n):
            dx = coords[i][0] - coords[j][0]
            dy = coords[i][1] - coords[j][1]
            dist = (dx * dx + dy * dy) ** 0.5
            row.append(int(dist * 120))
        durations.append(row)

    problem = {
        "vehicles": [
            _vehicle(1, [3], [_t(7, 0), _t(10, 0)]),
        ],
        "shipments": [
            {  # Passenger 1 -- short trip, picked up & dropped first
                "amount": [1],
                "pickup": {
                    "id": 1,
                    "location_index": 1,
                    "service": 60,
                    "time_windows": [[_t(7, 0), _t(7, 30)]],
                },
                "delivery": {
                    "id": 1,
                    "location_index": 2,
                    "service": 60,
                    "time_windows": [[_t(7, 0), _t(8, 0)]],
                },
            },
            {  # Passenger 2 -- wide pickup window (picked up right after D1)
                "amount": [1],
                "pickup": {
                    "id": 2,
                    "location_index": 3,
                    "service": 60,
                    "time_windows": [[_t(7, 0), _t(8, 30)]],
                },
                "delivery": {
                    "id": 2,
                    "location_index": 5,
                    "service": 60,
                    "time_windows": [[_t(8, 30), _t(9, 30)]],
                },
            },
            {  # Passenger 3 -- late pickup window (P2 dwells while waiting)
                "amount": [1],
                "pickup": {
                    "id": 3,
                    "location_index": 4,
                    "service": 60,
                    "time_windows": [[_t(8, 15), _t(9, 15)]],
                },
                "delivery": {
                    "id": 3,
                    "location_index": 6,
                    "service": 60,
                    "time_windows": [[_t(8, 30), _t(9, 30)]],
                },
            },
        ],
        **_matrices(durations),
    }

    # Delivery-priority on passengers 2 & 3
    priority_stops = {
        1: {"type": "delivery", "requested_time": _t(9, 0)},
        2: {"type": "delivery", "requested_time": _t(9, 0)},
    }

    return problem, priority_stops, description


ALL_SCENARIOS = {
    "rideshare_all_pickup_priority": rideshare_all_pickup_priority,
    "rideshare_mixed_priority": rideshare_mixed_priority,
    "rideshare_all_dropoff_priority": rideshare_all_dropoff_priority,
    "rideshare_dwell_collapse": rideshare_dwell_collapse,
}
