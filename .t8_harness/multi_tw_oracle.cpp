/*
 * Differential oracle for multi-TW engine completeness, arbitrary pair
 * count.
 *
 * Generates random routes of P constrained pairs (P = 2..5, random valid
 * pickup/delivery interleavings, optional unconstrained single) where every
 * pickup carries up to two disjoint time windows, then compares:
 *
 *   engine — realize_cap_compliant_schedule on the committed route.
 *   oracle — exhaustive depth-first search over route positions, branching
 *            on every feasible integer start of each constrained pickup and
 *            pruning branches at the first window, precedence or cap
 *            failure. Exact whenever it completes within the node budget;
 *            configurations exceeding the budget are counted and skipped.
 *
 * engine accepts and oracle rejects  => unsoundness, must be 0.
 * oracle accepts and engine rejects  => completeness miss, the quantity
 *                                       under test.
 *
 * All input times are integer user units (internally scaled by 100), so the
 * integer grid is exact.
 *
 * Build (from src/): same recipe as t8_engine_test, output
 * ../.t8_harness/multi_tw_oracle.
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

constexpr int MAX_PAIRS = 5;
constexpr UserDuration H = 400; // horizon, user units
constexpr unsigned long long NODE_BUDGET = 2000000;

struct Stop {
  bool is_pickup;
  int pair; // -1 for the unconstrained single
};

struct Config {
  int n_pairs;
  UserDuration leg;
  UserDuration p_w1_start[MAX_PAIRS], p_w1_end[MAX_PAIRS],
    p_w2_start[MAX_PAIRS], p_w2_end[MAX_PAIRS];
  UserDuration d_start[MAX_PAIRS], d_end[MAX_PAIRS], cap[MAX_PAIRS];
  bool multiwindow[MAX_PAIRS];
  UserDuration s_start; // single's window start (open-ended)
  std::vector<Stop> shape;
};

// Depth-first exhaustive search. now = departure time of the previous stop;
// dep[pr] = departure of pair pr's pickup. Returns true when any compliant
// completion exists; sets budget_hit when the node budget runs out (the
// result is then not authoritative and the caller skips the config).
bool oracle_dfs(const Config& c,
                std::size_t pos,
                UserDuration now,
                UserDuration dep[MAX_PAIRS],
                unsigned long long& nodes,
                bool& budget_hit) {
  if (++nodes > NODE_BUDGET) {
    budget_hit = true;
    return false;
  }
  if (pos == c.shape.size()) {
    return true;
  }
  const auto& st = c.shape[pos];
  const UserDuration arrival = now + (pos == 0 ? 0 : c.leg);
  if (!st.is_pickup && st.pair >= 0) {
    const int pr = st.pair;
    const UserDuration start = std::max(arrival, c.d_start[pr]);
    if (start > c.d_end[pr] || start - dep[pr] > c.cap[pr]) {
      return false;
    }
    return oracle_dfs(c, pos + 1, start, dep, nodes, budget_hit);
  }
  if (st.pair < 0) {
    const UserDuration start = std::max(arrival, c.s_start);
    return oracle_dfs(c, pos + 1, start, dep, nodes, budget_hit);
  }
  // Constrained pickup: branch over every feasible start in its windows.
  const int pr = st.pair;
  const auto try_range = [&](UserDuration lo, UserDuration hi) {
    lo = std::max(lo, arrival);
    hi = std::min(hi, H);
    for (UserDuration t = lo; t <= hi; ++t) {
      dep[pr] = t;
      if (oracle_dfs(c, pos + 1, t, dep, nodes, budget_hit)) {
        return true;
      }
      if (budget_hit) {
        return false;
      }
    }
    return false;
  };
  if (try_range(c.p_w1_start[pr], c.p_w1_end[pr])) {
    return true;
  }
  if (budget_hit) {
    return false;
  }
  if (c.multiwindow[pr] && try_range(c.p_w2_start[pr], c.p_w2_end[pr])) {
    return true;
  }
  return false;
}

bool oracle_accept(const Config& c, const bool with_caps, bool& budget_hit) {
  Config cc = c;
  if (!with_caps) {
    for (int pr = 0; pr < c.n_pairs; ++pr) {
      cc.cap[pr] = 1000000;
    }
  }
  UserDuration dep[MAX_PAIRS] = {};
  unsigned long long nodes = 0;
  budget_hit = false;
  return oracle_dfs(cc, 0, 0, dep, nodes, budget_hit);
}

Job make_pickup(Id id, const Config& c, int pr) {
  std::vector<TimeWindow> tws = {
    TimeWindow(c.p_w1_start[pr], c.p_w1_end[pr])};
  if (c.multiwindow[pr]) {
    tws.emplace_back(c.p_w2_start[pr], c.p_w2_end[pr]);
  }
  return {id,       JOB_TYPE::PICKUP, Location(0), 0, 0, Amount(0),
          Skills(), 0,       std::move(tws), "", {}, {},
          c.cap[pr]};
}

Job make_delivery(Id id, const Config& c, int pr) {
  return {id,
          JOB_TYPE::DELIVERY,
          Location(0),
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

// Engine decision on the committed route for this configuration.
bool engine_accept(const Config& c) {
  const unsigned n_locs = static_cast<unsigned>(c.shape.size());
  Matrix<UserDuration> dur(n_locs);
  Matrix<UserCost> cost(n_locs);
  Matrix<UserDistance> dist(n_locs);
  for (unsigned a = 0; a < n_locs; ++a) {
    for (unsigned b = 0; b < n_locs; ++b) {
      dur[a][b] = c.leg * static_cast<UserDuration>(a < b ? b - a : a - b);
    }
  }
  Input input;
  Vehicle v(1, std::make_optional(Location(0)), std::nullopt);
  input.add_vehicle(v);
  input.vehicles[0].cost_wrapper.set_durations_matrix(&dur);
  input.vehicles[0].cost_wrapper.set_costs_matrix(&cost);
  input.vehicles[0].cost_wrapper.set_distances_matrix(&dist);

  // Input job ranks: pair k -> 2k (pickup), 2k+1 (delivery); the single, if
  // present, follows all pairs. Locations follow visit order on a line.
  std::vector<Job> pickups;
  std::vector<Job> deliveries;
  for (int pr = 0; pr < c.n_pairs; ++pr) {
    pickups.push_back(make_pickup(2 * pr + 1, c, pr));
    deliveries.push_back(make_delivery(2 * pr + 2, c, pr));
  }
  std::vector<Index> seq;
  bool has_single = false;
  Index single_loc = 0;
  for (std::size_t k = 0; k < c.shape.size(); ++k) {
    const auto& st = c.shape[k];
    const auto loc = static_cast<Index>(k);
    if (st.pair < 0) {
      has_single = true;
      single_loc = loc;
      seq.push_back(static_cast<Index>(2 * c.n_pairs));
    } else if (st.is_pickup) {
      pickups[st.pair].location = Location(loc);
      seq.push_back(static_cast<Index>(2 * st.pair));
    } else {
      deliveries[st.pair].location = Location(loc);
      seq.push_back(static_cast<Index>(2 * st.pair + 1));
    }
  }
  for (int pr = 0; pr < c.n_pairs; ++pr) {
    input.add_shipment(pickups[pr], deliveries[pr]);
  }
  if (has_single) {
    Job s(9, Location(single_loc), 0, 0, Amount(0), Amount(0), Skills(), 0,
          {TimeWindow(c.s_start, 100000)});
    input.add_job(s);
  }
  for (auto& j : input.jobs) {
    j.setups = {j.default_setup};
    j.services = {j.default_service};
  }
  TWRoute tw(input, 0, 0);
  tw.replace(input, input.zero_amount(), seq.begin(), seq.end(), 0, 0);
  return tw.realize_cap_compliant_schedule(input).has_value();
}

} // namespace

int main() {
  std::mt19937 rng(42);
  auto uni = [&](UserDuration lo, UserDuration hi) {
    return std::uniform_int_distribution<UserDuration>(lo, hi)(rng);
  };

  // Windows narrow as P grows to keep the exhaustive branch space inside
  // the node budget.
  struct Batch {
    int configs;
    int pairs;
    UserDuration w1, gap, w2;
  };
  const Batch batches[] = {{20000, 2, 60, 80, 60},
                           {8000, 3, 25, 60, 25},
                           {3000, 4, 15, 40, 15},
                           {1500, 5, 10, 30, 10}};

  int total_unsound = 0;
  for (const auto& b : batches) {
    int both_accept = 0;
    int both_reject = 0;
    int misses = 0;
    int unsound = 0;
    int window_infeasible = 0;
    int budget_skips = 0;
    for (int i = 0; i < b.configs; ++i) {
      Config c{};
      c.n_pairs = b.pairs;
      c.leg = (b.pairs == 2) ? uni(5, 25) : uni(2, 8);
      c.s_start = (b.pairs == 2) ? uni(0, 150) : uni(0, 40);
      // Random valid interleaving: insert each pair's delivery after its
      // pickup; optionally one single somewhere.
      std::vector<Stop> shape;
      for (int pr = 0; pr < b.pairs; ++pr) {
        const auto p_at = static_cast<std::size_t>(
          uni(0, static_cast<UserDuration>(shape.size())));
        shape.insert(shape.begin() + p_at, Stop{true, pr});
        const auto d_at = static_cast<std::size_t>(
          uni(static_cast<UserDuration>(p_at) + 1,
              static_cast<UserDuration>(shape.size())));
        shape.insert(shape.begin() + d_at, Stop{false, pr});
      }
      if (uni(0, 1) == 1) {
        const auto s_at = static_cast<std::size_t>(
          uni(0, static_cast<UserDuration>(shape.size())));
        shape.insert(shape.begin() + s_at, Stop{false, -1});
      }
      c.shape = shape;
      // Window generation. For two pairs, windows are free-floating (the
      // historical shape space, kept for continuity). For three and more,
      // windows anchor to each stop's visit position so random
      // interleavings stay window-feasible often enough to evaluate.
      std::size_t pos_of_pickup[MAX_PAIRS] = {};
      std::size_t pos_of_delivery[MAX_PAIRS] = {};
      for (std::size_t k = 0; k < c.shape.size(); ++k) {
        if (c.shape[k].pair >= 0) {
          (c.shape[k].is_pickup ? pos_of_pickup
                                : pos_of_delivery)[c.shape[k].pair] = k;
        }
      }
      for (int pr = 0; pr < b.pairs; ++pr) {
        if (b.pairs == 2) {
          c.p_w1_start[pr] = 0;
          c.p_w1_end[pr] = uni(0, b.w1);
          c.p_w2_start[pr] = c.p_w1_end[pr] + uni(5, b.gap);
          c.p_w2_end[pr] = c.p_w2_start[pr] + uni(5, b.w2);
          c.d_start[pr] = uni(20, 250);
          c.d_end[pr] = c.d_start[pr] + uni(5, 80);
        } else {
          const auto base_p =
            static_cast<UserDuration>(pos_of_pickup[pr]) * (c.leg + 2);
          c.p_w1_start[pr] = base_p;
          c.p_w1_end[pr] = base_p + uni(3, b.w1);
          c.p_w2_start[pr] = c.p_w1_end[pr] + uni(5, b.gap);
          c.p_w2_end[pr] = c.p_w2_start[pr] + uni(3, b.w2);
          const auto base_d =
            static_cast<UserDuration>(pos_of_delivery[pr]) * (c.leg + 2);
          c.d_start[pr] = base_d + uni(3, 30);
          c.d_end[pr] = c.d_start[pr] + uni(30, 100);
        }
        c.cap[pr] = uni(c.leg, 4 * c.leg + 60);
        c.multiwindow[pr] = (uni(0, 2) != 0);
      }

      bool budget_hit = false;
      if (!oracle_accept(c, false, budget_hit)) {
        if (budget_hit) {
          ++budget_skips;
        } else {
          ++window_infeasible;
        }
        continue;
      }
      const bool o = oracle_accept(c, true, budget_hit);
      if (budget_hit) {
        ++budget_skips;
        continue;
      }
      const bool e = engine_accept(c);
      if (o && e) {
        ++both_accept;
      } else if (!o && !e) {
        ++both_reject;
      } else if (o && !e) {
        ++misses;
        std::printf("%d-pair MISS #%d\n", b.pairs, i);
      } else {
        ++unsound;
        std::printf("%d-pair UNSOUND #%d\n", b.pairs, i);
      }
    }
    total_unsound += unsound;
    const int feasible = both_accept + misses;
    std::printf("%d-pair: configs=%d window_infeasible=%d budget_skips=%d "
                "both_accept=%d both_reject=%d misses=%d unsound=%d "
                "(miss rate %.3f%% of feasible)\n",
                b.pairs, b.configs, window_infeasible, budget_skips,
                both_accept, both_reject, misses, unsound,
                feasible ? 100.0 * misses / feasible : 0.0);
  }
  return total_unsound == 0 ? 0 : 1;
}
