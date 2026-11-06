#ifndef VRP_H
#define VRP_H

/*

This file is part of VROOM.

Copyright (c) 2015-2025, Julien Coupey.
All rights reserved (see LICENSE).

*/

#include <algorithm>
#include <atomic>
#include <condition_variable>
#include <limits>
#include <mutex>
#include <numeric>
#include <optional>
#include <ranges>
#include <semaphore>
#include <set>
#include <thread>

#include "algorithms/heuristics/heuristics.h"
#include "algorithms/local_search/local_search.h"
#include "structures/vroom/eval.h"
#include "structures/vroom/input/input.h"
#include "structures/vroom/solution/solution.h"

namespace vroom {

template <class Route>
std::vector<Route> set_init_sol(const Input& input,
                                std::unordered_set<Index>& init_assigned) {
  std::vector<Route> init_sol;
  init_sol.reserve(input.vehicles.size());

  for (Index v = 0; v < input.vehicles.size(); ++v) {
    init_sol.emplace_back(input, v, input.zero_amount().size());
  }

  if (input.has_initial_routes()) {
    heuristics::set_initial_routes<Route>(input, init_sol, init_assigned);
  }

  return init_sol;
}

template <class Route> struct SolvingContext {
  std::unordered_set<Index> init_assigned;
  const std::vector<Route> init_sol;
  std::set<Index> unassigned;
  std::vector<Index> vehicles_ranks;
  std::vector<std::vector<Route>> solutions;
  std::vector<utils::SolutionIndicators> sol_indicators;

  std::set<utils::SolutionIndicators> heuristic_indicators;
  std::mutex heuristic_indicators_m;

  SolvingContext(const Input& input, unsigned nb_searches)
    : init_sol(set_init_sol<Route>(input, init_assigned)),
      vehicles_ranks(input.vehicles.size()),
      solutions(nb_searches, init_sol),
      sol_indicators(nb_searches) {

    // Deduce unassigned jobs from initial solution.
    std::ranges::copy_if(std::views::iota(0u, input.jobs.size()),
                         std::inserter(unassigned, unassigned.begin()),
                         [this](const Index j) {
                           return !init_assigned.contains(j);
                         });

    // Heuristics will operate on all vehicles.
    std::iota(vehicles_ranks.begin(), vehicles_ranks.end(), 0);
  }

  bool heuristic_solution_already_found(unsigned rank) {
    assert(rank < sol_indicators.size());
    const std::scoped_lock<std::mutex> lock(heuristic_indicators_m);
    const auto [dummy, insertion_ok] =
      heuristic_indicators.insert(sol_indicators[rank]);

    return !insertion_ok;
  }
};

// Build the heuristic solution for search at rank. Returns false when it
// duplicates the heuristic solution of another search.
template <class Route>
bool run_heuristic(const Input& input,
                   const HeuristicParameters& p,
                   const unsigned rank,
                   SolvingContext<Route>& context) {
  Eval h_eval;
  switch (p.heuristic) {
  case HEURISTIC::BASIC:
    h_eval = heuristics::basic<Route>(input,
                                      context.solutions[rank],
                                      context.unassigned,
                                      context.vehicles_ranks,
                                      p.init,
                                      p.regret_coeff,
                                      p.sort);
    break;
  case HEURISTIC::DYNAMIC:
    h_eval = heuristics::dynamic_vehicle_choice<Route>(input,
                                                       context.solutions[rank],
                                                       context.unassigned,
                                                       context.vehicles_ranks,
                                                       p.init,
                                                       p.regret_coeff,
                                                       p.sort);
    break;
  }

  if (!input.has_homogeneous_costs() && p.sort == SORT::AVAILABILITY) {
    // Worth trying another vehicle ordering scheme in
    // heuristic.
    std::vector<Route> other_sol = context.init_sol;

    Eval h_other_eval;
    switch (p.heuristic) {
    case HEURISTIC::BASIC:
      h_other_eval = heuristics::basic<Route>(input,
                                              other_sol,
                                              context.unassigned,
                                              context.vehicles_ranks,
                                              p.init,
                                              p.regret_coeff,
                                              SORT::COST);
      break;
    case HEURISTIC::DYNAMIC:
      h_other_eval =
        heuristics::dynamic_vehicle_choice<Route>(input,
                                                  other_sol,
                                                  context.unassigned,
                                                  context.vehicles_ranks,
                                                  p.init,
                                                  p.regret_coeff,
                                                  SORT::COST);
      break;
    }

    if (h_other_eval < h_eval) {
      context.solutions[rank] = std::move(other_sol);
    }
  }

  // Check if heuristic solution has been encountered before.
  context.sol_indicators[rank] =
    utils::SolutionIndicators(input, context.solutions[rank]);

  return !context.heuristic_solution_already_found(rank);
}

template <class Route, class LocalSearch>
void run_single_search(const Input& input,
                       const HeuristicParameters& p,
                       const unsigned rank,
                       const unsigned depth,
                       const Timeout& search_time,
                       SolvingContext<Route>& context) {
  const auto heuristic_start = utils::now();

  if (!run_heuristic<Route>(input, p, rank, context)) {
    // Duplicate heuristic solution, so skip local search.
    return;
  }

  const auto heuristic_end = utils::now();

  Timeout ls_search_time;
  if (search_time.has_value()) {
    const auto heuristic_time =
      std::chrono::duration_cast<std::chrono::milliseconds>(heuristic_end -
                                                            heuristic_start);

    if (search_time.value() <= heuristic_time) {
      // No time left for local search!
      return;
    }

    ls_search_time = search_time.value() - heuristic_time;
  }

  // Local search phase.
  LocalSearch ls(input, context.solutions[rank], depth, ls_search_time);
  ls.run();

  // Store solution indicators.
  context.sol_indicators[rank] = ls.indicators();
}

// Number of searches kept after checkpoint k when pruning progressively:
// halved every two checkpoints, with an intermediate 3/4 step, e.g. 32,
// 24, 16, 12, 8 for 32 searches.
inline unsigned nb_kept_searches(unsigned nb_searches, unsigned k) {
  const unsigned halved = (k / 2 < 32) ? nb_searches >> (k / 2) : 0;
  return std::max(1u, (k % 2 == 0) ? halved : 3 * halved / 4);
}

class VRP {
  // Abstract class describing a VRP (vehicle routing problem).
protected:
  const Input& _input;

  // Searches deepen together and are pruned along the way. Checkpoint k
  // is reached when a search completes depth k (k = 0 being the end of
  // the first descent). Once all remaining searches reached checkpoint
  // k, only the best nb_kept_searches(k) of them carry on. While waiting
  // for that, idle threads advance the best-ranked searches that already
  // reached checkpoint k towards checkpoint k + 1, and a search pruned
  // while running is stopped. Pruning only depends on the indicators at
  // each checkpoint, so as with the other solving path, the number of
  // threads only matters through which of two heuristic solutions with
  // equal indicators is seen first.
  template <class Route, class LocalSearch>
  Solution
  solve_progressive(const unsigned nb_searches,
                    const unsigned depth,
                    const unsigned nb_threads,
                    const std::vector<HeuristicParameters>& parameters) const {
    constexpr unsigned UNBOUNDED = std::numeric_limits<unsigned>::max();

    SolvingContext<Route> context(_input, nb_searches);
    // Number of checkpoints reached by each search, depth + 1 once its
    // climb is over.
    std::vector<unsigned> reached(nb_searches, 0);
    std::vector<std::vector<utils::SolutionIndicators>>
      at_checkpoint(nb_searches, std::vector<utils::SolutionIndicators>(depth));
    std::vector<char> running(nb_searches, 0);
    // Set for searches that are pruned or duplicates.
    std::vector<std::atomic<bool>> stop(nb_searches);

    // Checkpoint whose pruning comes next, depth once all are done.
    unsigned stage = 0;
    bool done = false;
    std::exception_ptr ep = nullptr;
    std::mutex m;
    std::condition_variable cv;

    // All functions below are called with the lock held.
    auto release = [&](unsigned rank) {
      context.solutions[rank] = std::vector<Route>();
    };

    auto prune = [&]() {
      while (!done) {
        for (unsigned rank = 0; rank < nb_searches; ++rank) {
          if (!stop[rank] && reached[rank] <= stage) {
            return;
          }
        }
        if (stage == depth) {
          done = true;
          return;
        }
        std::vector<unsigned> live;
        for (unsigned rank = 0; rank < nb_searches; ++rank) {
          if (!stop[rank]) {
            live.push_back(rank);
          }
        }
        std::ranges::stable_sort(live, [&](unsigned a, unsigned b) {
          return at_checkpoint[a][stage] < at_checkpoint[b][stage];
        });
        for (std::size_t i = nb_kept_searches(nb_searches, stage);
             i < live.size();
             ++i) {
          const auto rank = live[i];
          stop[rank] = true;
          if (!running[rank]) {
            release(rank);
          }
        }
        ++stage;
      }
    };

    // Next (rank, checkpoint to run to): searches still short of the
    // current checkpoint first, then the best-ranked one that already
    // reached it.
    auto pick = [&]() -> std::optional<std::pair<unsigned, unsigned>> {
      const auto target = [&](unsigned k) {
        return (k < depth) ? k : UNBOUNDED;
      };
      for (unsigned rank = 0; rank < nb_searches; ++rank) {
        if (!stop[rank] && !running[rank] && reached[rank] <= stage) {
          return std::make_pair(rank, target(stage));
        }
      }
      std::optional<unsigned> best;
      for (unsigned rank = 0; rank < nb_searches; ++rank) {
        if (!stop[rank] && !running[rank] && reached[rank] == stage + 1 &&
            stage < depth &&
            (!best.has_value() ||
             at_checkpoint[rank][stage] < at_checkpoint[*best][stage])) {
          best = rank;
        }
      }
      if (!best.has_value()) {
        return std::nullopt;
      }
      return std::make_pair(*best, target(stage + 1));
    };

    auto worker = [&]() {
      std::unique_lock<std::mutex> lock(m);
      while (true) {
        std::optional<std::pair<unsigned, unsigned>> task;
        cv.wait(lock, [&] { return done || (task = pick()).has_value(); });
        if (done) {
          return;
        }
        const auto [rank, until] = *task;
        const auto from = reached[rank];
        running[rank] = 1;
        lock.unlock();

        // Between checkpoints, a search is only its best solution in
        // context.solutions and the depth it completed, so at most one
        // local search per thread is held in memory.
        bool unique = true;
        bool paused = false;
        utils::SolutionIndicators indicators;
        try {
          if (from == 0) {
            unique =
              run_heuristic<Route>(_input, parameters[rank], rank, context);
          }
          if (unique) {
            LocalSearch ls(_input,
                           context.solutions[rank],
                           depth,
                           Timeout(),
                           (from == 0) ? std::nullopt
                                       : std::optional<unsigned>(from - 1));
            paused = ls.run(until, &stop[rank]);
            indicators = ls.indicators();
          }
        } catch (...) {
          lock.lock();
          ep = std::current_exception();
          done = true;
          for (auto& s : stop) {
            s = true;
          }
          cv.notify_all();
          return;
        }

        lock.lock();
        running[rank] = 0;
        if (done) {
          // Another search threw.
          cv.notify_all();
          return;
        }
        if (!unique) {
          // Duplicate heuristic solution: no local search.
          stop[rank] = true;
          release(rank);
        } else if (stop[rank]) {
          // Pruned while running.
          release(rank);
        } else {
          if (paused) {
            at_checkpoint[rank][until] = indicators;
            reached[rank] = until + 1;
          } else {
            // Climb over, only ever run to past the last checkpoint.
            assert(reached[rank] == depth);
            reached[rank] = depth + 1;
            context.sol_indicators[rank] = indicators;
          }
        }
        prune();
        cv.notify_all();
      }
    };

    {
      const auto nb_workers = std::clamp(nb_threads, 1u, nb_searches);
      std::vector<std::jthread> pool;
      pool.reserve(nb_workers);
      for (unsigned t = 0; t < nb_workers; ++t) {
        pool.emplace_back(worker);
      }
    }

    if (ep != nullptr) {
      std::rethrow_exception(ep);
    }

    std::optional<unsigned> best;
    for (unsigned rank = 0; rank < nb_searches; ++rank) {
      if (!stop[rank] &&
          (!best.has_value() ||
           context.sol_indicators[rank] < context.sol_indicators[*best])) {
        best = rank;
      }
    }
    assert(best.has_value());
    return utils::format_solution(_input, context.solutions[*best]);
  }

  template <class Route, class LocalSearch>
  Solution solve(
    unsigned nb_searches,
    const unsigned depth,
    const unsigned nb_threads,
    const Timeout& timeout,
    const std::vector<HeuristicParameters>& homogeneous_parameters,
    const std::vector<HeuristicParameters>& heterogeneous_parameters) const {
    const auto& parameters = (_input.has_homogeneous_locations())
                               ? homogeneous_parameters
                               : heterogeneous_parameters;
    assert(nb_searches != 0);
    nb_searches =
      std::min(nb_searches, static_cast<unsigned>(parameters.size()));

    if (!timeout.has_value() && depth > 0) {
      return solve_progressive<Route, LocalSearch>(nb_searches,
                                                   depth,
                                                   nb_threads,
                                                   parameters);
    }

    SolvingContext<Route> context(_input, nb_searches);

    std::exception_ptr ep = nullptr;
    std::mutex ep_m;

    const auto actual_nb_threads = std::min(nb_searches, nb_threads);
    assert(actual_nb_threads <= 32);
    std::counting_semaphore<32> semaphore(actual_nb_threads);

    Timeout search_time;
    if (timeout.has_value()) {
      // Max number of solving per thread.
      const auto dv = std::div(static_cast<long>(nb_searches),
                               static_cast<long>(actual_nb_threads));
      const unsigned max_solving_number = dv.quot + ((dv.rem == 0) ? 0 : 1);
      search_time = timeout.value() / max_solving_number;
    }

    auto run_solving = [&context,
                        &semaphore,
                        &search_time,
                        &parameters,
                        &timeout,
                        &ep,
                        &ep_m,
                        depth,
                        this](const unsigned rank) {
      semaphore.acquire();
      try {
        run_single_search<Route, LocalSearch>(_input,
                                              parameters[rank],
                                              rank,
                                              depth,
                                              search_time,
                                              context);
      } catch (...) {
        const std::scoped_lock<std::mutex> lock(ep_m);
        ep = std::current_exception();
      }
      semaphore.release();
    };

    std::vector<std::jthread> solving_threads;
    solving_threads.reserve(nb_searches);

    for (unsigned i = 0; i < nb_searches; ++i) {
      solving_threads.emplace_back(run_solving, i);
    }

    for (auto& t : solving_threads) {
      t.join();
    }

    if (ep != nullptr) {
      std::rethrow_exception(ep);
    }

    auto best_indic = std::min_element(context.sol_indicators.cbegin(),
                                       context.sol_indicators.cend());

    return utils::
      format_solution(_input,
                      context.solutions[std::distance(context.sol_indicators
                                                        .cbegin(),
                                                      best_indic)]);
  }

public:
  explicit VRP(const Input& input);

  virtual ~VRP();

  virtual Solution solve(unsigned nb_searches,
                         unsigned depth,
                         unsigned nb_threads,
                         const Timeout& timeout) const = 0;
};

} // namespace vroom

#endif
