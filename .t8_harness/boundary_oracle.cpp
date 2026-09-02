/*
 * Differential test at the is_valid_addition_for_tw boundary.
 *
 * Builds a random committed multi-window route of two constrained pairs
 * (chained or nested), gated to be cap-compliant, then proposes a random
 * insertion (a fresh unconstrained single, or a fresh constrained pair) at a
 * random position and compares:
 *
 *   valid  — is_valid_addition_for_tw on the committed TWRoute (the real
 *            entry point: tier-0 TW simulation, path lower bound, ASAP
 *            witness, exact engine).
 *   oracle — exhaustive enumeration over all constrained pickups' service
 *            starts on the candidate sequence, other stops
 *            earliest-feasible, checking windows and caps.
 *
 * valid && !oracle  => unsoundness, must be 0.
 * oracle && !valid  => conservative rejection, counted and reported.
 *
 * Breaks are not modeled: the oracle would have to replicate order_choice
 * placement exactly, and a semantic mismatch there would produce false
 * alarms rather than evidence. Break interactions are covered by the engine
 * harness fixtures and the stress benchmark.
 *
 * Build (from src/): same recipe as t8_engine_test, output
 * ../.t8_harness/boundary_oracle.
 */

#include <cstdio>
#include <optional>
#include <random>
#include <vector>

#include "structures/generic/matrix.h"
#include "structures/vroom/input/input.h"
#include "structures/vroom/location.h"
#include "structures/vroom/time_window.h"
#include "structures/vroom/tw_route.h"

using namespace vroom;

namespace {

constexpr int N_CONFIGS = 6000;
constexpr UserDuration H = 400;

// Input job ranks: pair 0 -> 0,1; pair 1 -> 2,3; inserted pair -> 4,5;
// inserted single -> 4. Location of each job is fixed at input build time;
// travel(a, b) = |loc_a - loc_b| * leg.
struct Case {
  UserDuration leg;
  UserDuration p_w1_end[3], p_w2_start[3], p_w2_end[3];
  UserDuration d_start[3], d_end[3], cap[3];
  bool multiwindow[3]; // pickup pr has two windows, else only [0, w1_end]
  bool insert_pair;    // else insert an unconstrained single
  UserDuration single_start;
  bool nested;
  Index insert_pos;
};

struct StopRef {
  Index input_rank;
  Index loc;
  bool is_pickup;
  int pair; // -1 for the single
};

// Committed stop order (input ranks 0..3) with line locations 0..3.
std::vector<StopRef> committed_stops(const Case& c) {
  if (c.nested) {
    return {{0, 0, true, 0}, {2, 1, true, 1}, {3, 2, false, 1},
            {1, 3, false, 0}};
  }
  return {{0, 0, true, 0}, {1, 1, false, 0}, {2, 2, true, 1},
          {3, 3, false, 1}};
}

std::vector<StopRef> inserted_stops(const Case& c) {
  if (c.insert_pair) {
    return {{4, 4, true, 2}, {5, 5, false, 2}};
  }
  return {{4, 4, false, -1}};
}

UserDuration travel(const Case& c, Index a, Index b) {
  return c.leg * static_cast<UserDuration>(a < b ? b - a : a - b);
}

// Exhaustive oracle over a full candidate sequence.
bool oracle_accept(const Case& c,
                   const std::vector<StopRef>& seq,
                   const bool with_caps) {
  bool present[3] = {false, false, false};
  for (const auto& st : seq) {
    if (st.is_pickup && st.pair >= 0) {
      present[st.pair] = true;
    }
  }
  std::vector<std::vector<UserDuration>> starts(3);
  for (int pr = 0; pr < 3; ++pr) {
    if (!present[pr]) {
      starts[pr] = {0};
      continue;
    }
    for (UserDuration t = 0; t <= c.p_w1_end[pr] && t <= H; ++t) {
      starts[pr].push_back(t);
    }
    if (c.multiwindow[pr]) {
      for (UserDuration t = c.p_w2_start[pr]; t <= c.p_w2_end[pr] && t <= H;
           ++t) {
        starts[pr].push_back(t);
      }
    }
  }

  for (const UserDuration s0 : starts[0]) {
    for (const UserDuration s1 : starts[1]) {
      for (const UserDuration s2 : starts[2]) {
        const UserDuration forced[3] = {s0, s1, s2};
        UserDuration now = 0;
        Index prev_loc = 0; // vehicle start is at location 0
        UserDuration dep[3] = {0, 0, 0};
        UserDuration transit[3] = {0, 0, 0};
        bool ok = true;
        for (std::size_t k = 0; k < seq.size() && ok; ++k) {
          const auto& st = seq[k];
          const UserDuration arrival = now + travel(c, prev_loc, st.loc);
          UserDuration start = arrival;
          if (st.is_pickup && st.pair >= 0) {
            if (forced[st.pair] < arrival) {
              ok = false;
              break;
            }
            start = forced[st.pair];
            dep[st.pair] = start;
          } else if (st.pair >= 0) {
            start = std::max(arrival, c.d_start[st.pair]);
            if (start > c.d_end[st.pair]) {
              ok = false;
              break;
            }
            transit[st.pair] = start - dep[st.pair];
          } else {
            start = std::max(arrival, c.single_start);
          }
          now = start;
          prev_loc = st.loc;
        }
        if (!ok) {
          continue;
        }
        bool caps_ok = true;
        if (with_caps) {
          for (int pr = 0; pr < 3 && caps_ok; ++pr) {
            if (present[pr]) {
              caps_ok = transit[pr] <= c.cap[pr];
            }
          }
        }
        if (caps_ok) {
          return true;
        }
      }
    }
  }
  return false;
}

Job make_pickup(Id id, Index loc, const Case& c, int pr) {
  std::vector<TimeWindow> tws = {TimeWindow(0, c.p_w1_end[pr])};
  if (c.multiwindow[pr]) {
    tws.emplace_back(c.p_w2_start[pr], c.p_w2_end[pr]);
  }
  return {id,       JOB_TYPE::PICKUP, Location(loc), 0, 0, Amount(0),
          Skills(), 0,       std::move(tws), "", {}, {},
          c.cap[pr]};
}

Job make_delivery(Id id, Index loc, const Case& c, int pr) {
  return {id,
          JOB_TYPE::DELIVERY,
          Location(loc),
          0,
          0,
          Amount(0),
          Skills(),
          0,
          {TimeWindow(c.d_start[pr], c.d_end[pr])},
          "",
          {},
          {},
          c.cap[pr]};
}

// Real is_valid_addition_for_tw decision for the case.
bool valid_decision(const Case& c) {
  const unsigned n_locs = 6;
  Matrix<UserDuration> dur(n_locs);
  Matrix<UserCost> cost(n_locs);
  Matrix<UserDistance> dist(n_locs);
  for (unsigned a = 0; a < n_locs; ++a) {
    for (unsigned b = 0; b < n_locs; ++b) {
      dur[a][b] = travel(c, a, b);
    }
  }
  Input input;
  Vehicle v(1, std::make_optional(Location(0)), std::nullopt);
  input.add_vehicle(v);
  input.vehicles[0].cost_wrapper.set_durations_matrix(&dur);
  input.vehicles[0].cost_wrapper.set_costs_matrix(&cost);
  input.vehicles[0].cost_wrapper.set_distances_matrix(&dist);

  const auto committed = committed_stops(c);
  const auto inserted = inserted_stops(c);
  // Committed pairs 0 and 1; locations fixed per StopRef.
  Job p0 = make_pickup(1, 0, c, 0);
  Job d0 = make_delivery(2, 0, c, 0);
  Job p1 = make_pickup(3, 0, c, 1);
  Job d1 = make_delivery(4, 0, c, 1);
  for (const auto& st : committed) {
    Job* j = st.input_rank == 0   ? &p0
             : st.input_rank == 1 ? &d0
             : st.input_rank == 2 ? &p1
                                  : &d1;
    j->location = Location(st.loc);
  }
  input.add_shipment(p0, d0);
  input.add_shipment(p1, d1);
  if (c.insert_pair) {
    input.add_shipment(make_pickup(5, inserted[0].loc, c, 2),
                       make_delivery(6, inserted[1].loc, c, 2));
  } else {
    Job s(5, Location(inserted[0].loc), 0, 0, Amount(0), Amount(0), Skills(),
          0, {TimeWindow(c.single_start, 100000)});
    input.add_job(s);
  }
  for (auto& j : input.jobs) {
    j.setups = {j.default_setup};
    j.services = {j.default_service};
  }

  std::vector<Index> seq;
  for (const auto& st : committed) {
    seq.push_back(st.input_rank);
  }
  TWRoute tw(input, 0, 0);
  tw.replace(input, input.zero_amount(), seq.begin(), seq.end(), 0, 0);

  std::vector<Index> add;
  for (const auto& st : inserted) {
    add.push_back(st.input_rank);
  }
  return tw.is_valid_addition_for_tw(input,
                                     input.zero_amount(),
                                     add.begin(),
                                     add.end(),
                                     c.insert_pos,
                                     c.insert_pos);
}

} // namespace

int main() {
  std::mt19937 rng(4242);
  auto uni = [&](UserDuration lo, UserDuration hi) {
    return std::uniform_int_distribution<UserDuration>(lo, hi)(rng);
  };

  int committed_infeasible = 0;
  int both_accept = 0;
  int both_reject = 0;
  int conservative = 0; // oracle accepts, is_valid rejects
  int unsound = 0;      // is_valid accepts, oracle rejects: must stay 0

  for (int i = 0; i < N_CONFIGS; ++i) {
    Case c{};
    c.leg = uni(3, 12);
    for (int pr = 0; pr < 3; ++pr) {
      c.p_w1_end[pr] = uni(0, 40);
      c.p_w2_start[pr] = c.p_w1_end[pr] + uni(5, 60);
      c.p_w2_end[pr] = c.p_w2_start[pr] + uni(5, 40);
      c.d_start[pr] = uni(10, 120) + 30 * static_cast<UserDuration>(pr);
      c.d_end[pr] = c.d_start[pr] + uni(20, 140);
      c.cap[pr] = uni(c.leg, 5 * c.leg + 60);
      c.multiwindow[pr] = (uni(0, 2) != 0);
    }
    c.insert_pair = (uni(0, 1) == 1);
    c.single_start = uni(0, 150);
    c.nested = (uni(0, 1) == 1);
    c.insert_pos = static_cast<Index>(uni(0, 4));

    // Committed route must be TW- and cap-feasible (real committed routes
    // are); gate with the oracle on the committed sequence.
    const auto committed = committed_stops(c);
    if (!oracle_accept(c, committed, true)) {
      ++committed_infeasible;
      continue;
    }

    // Candidate sequence: splice insertion at insert_pos.
    std::vector<StopRef> candidate = committed;
    const auto ins = inserted_stops(c);
    candidate.insert(candidate.begin() + c.insert_pos, ins.begin(), ins.end());

    const bool o = oracle_accept(c, candidate, true);
    const bool v = valid_decision(c);
    if (v && o) {
      ++both_accept;
    } else if (!v && !o) {
      ++both_reject;
    } else if (o && !v) {
      ++conservative;
    } else {
      ++unsound;
      std::printf("UNSOUND #%d\n", i);
    }
  }

  const int evaluated = both_accept + both_reject + conservative + unsound;
  std::printf("configs=%d committed_infeasible_skipped=%d evaluated=%d\n",
              N_CONFIGS, committed_infeasible, evaluated);
  std::printf("both_accept=%d both_reject=%d conservative_rejects=%d "
              "unsound=%d\n",
              both_accept, both_reject, conservative, unsound);
  const int feasible = both_accept + conservative;
  std::printf("conservative rate: %.3f%% of oracle-feasible insertions\n",
              feasible ? 100.0 * conservative / feasible : 0.0);
  return unsound == 0 ? 0 : 1;
}
