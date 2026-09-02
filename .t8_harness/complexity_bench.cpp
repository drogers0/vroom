/*
 * Complexity micro-benchmark for the max_transit_time validity layers.
 *
 * Three curves as a function of committed route length L:
 *
 *   engine   — realize_cap_compliant_schedule on a chained route (P = L/2
 *              pairs) where every delivery TW forces a large on-board wait
 *              and waits grow downstream, so the fixpoint must fix pairs one
 *              by one (worst-first): expected O(P^2 + P*L) = O(L^2).
 *   fulltier — is_valid_addition_for_tw inserting one unconstrained single
 *              job in the middle of a nested route (P0..Pk Dk..D0, all P
 *              pairs span the insertion point, caps loose). The witness is
 *              ineligible (prefix pickups with suffix deliveries), so this
 *              exercises the external-pairs screen walk plus one engine ASAP
 *              pass: expected O(P*L) = O(L^2).
 *   witness  — is_valid_addition_for_tw appending one constrained pair at
 *              the end of a chained route with loose caps: the ASAP witness
 *              accepts, no engine call: expected O(L).
 *
 * Prints median microseconds per call and the log2 slope between consecutive
 * L doublings (slope 1 = linear, 2 = quadratic).
 *
 * Build (from src/): same recipe as t8_engine_test, output
 * ../.t8_harness/complexity_bench.
 */

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <optional>
#include <vector>

#include "structures/generic/matrix.h"
#include "structures/vroom/input/input.h"
#include "structures/vroom/location.h"
#include "structures/vroom/time_window.h"
#include "structures/vroom/tw_route.h"

using namespace vroom;

namespace {

constexpr int REPS = 15;

Job make_pickup(Id id, Index loc, UserDuration cap) {
  return {id,       JOB_TYPE::PICKUP, Location(loc), 0, 0, Amount(0),
          Skills(), 0,       {TimeWindow()}, "", {}, {},
          cap};
}

Job make_delivery(Id id, Index loc, UserDuration cap, const TimeWindow& tw) {
  return {id,       JOB_TYPE::DELIVERY, Location(loc), 0, 0, Amount(0),
          Skills(), 0,       {tw}, "", {}, {},
          cap};
}

struct BenchEnv {
  Matrix<UserDuration> dur_mat;
  Matrix<UserCost> cost_mat;
  Matrix<UserDistance> dist_mat;
  Input input;

  explicit BenchEnv(unsigned n_locs)
    : dur_mat(n_locs), cost_mat(n_locs), dist_mat(n_locs) {
    for (unsigned a = 0; a < n_locs; ++a) {
      for (unsigned b = 0; b < n_locs; ++b) {
        dur_mat[a][b] = 10 * static_cast<UserDuration>(a < b ? b - a : a - b);
      }
    }
    Vehicle v(1, std::make_optional(Location(0)), std::nullopt);
    input.add_vehicle(v);
    input.vehicles[0].cost_wrapper.set_durations_matrix(&dur_mat);
    input.vehicles[0].cost_wrapper.set_costs_matrix(&cost_mat);
    input.vehicles[0].cost_wrapper.set_distances_matrix(&dist_mat);
  }

  void finalize() {
    for (auto& j : input.jobs) {
      j.setups = {j.default_setup};
      j.services = {j.default_service};
    }
  }

  TWRoute committed(const std::vector<Index>& seq) {
    TWRoute tw(input, 0, 0);
    tw.replace(input, input.zero_amount(), seq.begin(), seq.end(), 0, 0);
    return tw;
  }
};

// Chained route P0 D0 P1 D1 ... at line locations 0..2P-1. tight: delivery
// TWs force waits growing downstream, caps equal the 10s leg (feasible only
// after delaying the pickup). loose: open TWs, wide caps.
BenchEnv chained_env(unsigned pairs, bool tight) {
  BenchEnv env(2 * pairs + 2);
  for (unsigned i = 0; i < pairs; ++i) {
    const UserDuration cap = tight ? 10 : 100000;
    const TimeWindow d_tw = tight
                              ? TimeWindow(10000 * (i + 1), 40000000)
                              : TimeWindow();
    env.input.add_shipment(make_pickup(2 * i + 1, 2 * i, cap),
                           make_delivery(2 * i + 2, 2 * i + 1, cap, d_tw));
  }
  env.finalize();
  return env;
}

// Nested route P0 P1 .. Pk Dk .. D1 D0 at line locations matching route
// order, loose caps, plus one unconstrained single job (input rank 2P) for
// mid-route insertion probes.
BenchEnv nested_env(unsigned pairs) {
  BenchEnv env(2 * pairs + 2);
  for (unsigned i = 0; i < pairs; ++i) {
    env.input.add_shipment(
      make_pickup(2 * i + 1, i, 100000),
      make_delivery(2 * i + 2, 2 * pairs - 1 - i, 100000, TimeWindow()));
  }
  Job single(10000, Location(2 * pairs), 0, 0, Amount(0), Amount(0), Skills(),
             0, {TimeWindow()});
  env.input.add_job(single);
  env.finalize();
  return env;
}

double median_us(std::vector<double>& xs) {
  std::sort(xs.begin(), xs.end());
  return xs[xs.size() / 2];
}

template <typename F> double timed_median(F&& f) {
  std::vector<double> xs;
  xs.reserve(REPS);
  for (int r = 0; r < REPS; ++r) {
    const auto t0 = std::chrono::steady_clock::now();
    f();
    const auto t1 = std::chrono::steady_clock::now();
    xs.push_back(std::chrono::duration<double, std::micro>(t1 - t0).count());
  }
  return median_us(xs);
}

} // namespace

int main() {
  std::printf("L = committed route length (jobs), P = L/2 constrained "
              "pairs, medians of %d in microseconds\n"
              "slope = log2 ratio vs previous row (1 = linear, 2 = "
              "quadratic)\n\n",
              REPS);
  std::printf("%6s %10s %6s %10s %6s %10s %6s\n",
              "L",
              "engine",
              "slope",
              "fulltier",
              "slope",
              "witness",
              "slope");

  double prev_e = 0;
  double prev_f = 0;
  double prev_w = 0;
  for (unsigned pairs = 8; pairs <= 512; pairs *= 2) {
    // engine: tight chained route, realize.
    BenchEnv te = chained_env(pairs, true);
    std::vector<Index> chain_seq(2 * pairs);
    for (Index k = 0; k < static_cast<Index>(chain_seq.size()); ++k) {
      chain_seq[k] = k;
    }
    TWRoute tight_route = te.committed(chain_seq);
    bool feasible = true;
    const double e = timed_median([&] {
      feasible = tight_route.realize_cap_compliant_schedule(te.input)
                   .has_value();
    });
    if (!feasible) {
      std::printf("UNEXPECTED: tight chained route infeasible at P=%u\n",
                  pairs);
      return 1;
    }

    // fulltier: nested route, mid-route single-job insertion.
    BenchEnv ne = nested_env(pairs);
    std::vector<Index> nest_seq;
    nest_seq.reserve(2 * pairs);
    for (unsigned i = 0; i < pairs; ++i) {
      nest_seq.push_back(2 * i); // pickups in input-rank order
    }
    for (unsigned i = pairs; i-- > 0;) {
      nest_seq.push_back(2 * i + 1); // deliveries in reverse
    }
    TWRoute nested_route = ne.committed(nest_seq);
    const std::vector<Index> probe = {static_cast<Index>(2 * pairs)};
    const Index mid = static_cast<Index>(pairs);
    bool ft_valid = true;
    const double f = timed_median([&] {
      ft_valid = nested_route.is_valid_addition_for_tw(ne.input,
                                                       ne.input.zero_amount(),
                                                       probe.begin(),
                                                       probe.end(),
                                                       mid,
                                                       mid);
    });
    (void)ft_valid;

    // witness: loose chained route, append one fresh constrained pair.
    BenchEnv le = chained_env(pairs + 1, false);
    std::vector<Index> loose_seq(2 * pairs);
    for (Index k = 0; k < static_cast<Index>(loose_seq.size()); ++k) {
      loose_seq[k] = k;
    }
    TWRoute loose_route = le.committed(loose_seq);
    const std::vector<Index> pair_probe = {static_cast<Index>(2 * pairs),
                                           static_cast<Index>(2 * pairs + 1)};
    const Index end = static_cast<Index>(2 * pairs);
    bool w_valid = true;
    const double w = timed_median([&] {
      w_valid = loose_route.is_valid_addition_for_tw(le.input,
                                                     le.input.zero_amount(),
                                                     pair_probe.begin(),
                                                     pair_probe.end(),
                                                     end,
                                                     end);
    });
    (void)w_valid;

    const auto slope = [](double cur, double prev) {
      return (prev > 0) ? std::log2(cur / prev) : 0.0;
    };
    std::printf("%6u %10.1f %6.2f %10.1f %6.2f %10.1f %6.2f\n",
                2 * pairs,
                e,
                slope(e, prev_e),
                f,
                slope(f, prev_f),
                w,
                slope(w, prev_w));
    prev_e = e;
    prev_f = f;
    prev_w = w;
  }
  return 0;
}
