#ifndef LOCAL_SEARCH_H
#define LOCAL_SEARCH_H

/*

This file is part of VROOM.

Copyright (c) 2015-2025, Julien Coupey.
All rights reserved (see LICENSE).

*/

#include <atomic>
#include <limits>

#include "structures/vroom/solution_indicators.h"
#include "structures/vroom/solution_state.h"

namespace vroom::ls {

template <class Route,
          class UnassignedExchange,
          class CrossExchange,
          class MixedExchange,
          class TwoOpt,
          class ReverseTwoOpt,
          class Relocate,
          class OrOpt,
          class IntraExchange,
          class IntraCrossExchange,
          class IntraMixedExchange,
          class IntraRelocate,
          class IntraOrOpt,
          class IntraTwoOpt,
          class PDShift,
          class RouteExchange,
          class SwapStar,
          class RouteSplit,
          class PriorityReplace,
          class TSPFix>
class LocalSearch {
private:
  const Input& _input;
  const std::size_t _nb_vehicles;

  const unsigned _depth;
  const Deadline _deadline;

  std::optional<unsigned> _completed_depth;
  const std::atomic<bool>* _stop{nullptr};
  std::vector<Index> _all_routes;

  utils::SolutionState _sol_state;

  std::vector<Route> _sol;

  std::vector<Route>& _best_sol;
  utils::SolutionIndicators _best_sol_indicators;

  std::unordered_set<Index> try_job_additions(const std::vector<Index>& routes,
                                              double regret_coeff);

  void run_ls_step();

  // Compute "cost" between route at rank v_target and job with rank r
  // in route at rank v. Relies on
  // _sol_state.cheapest_job_rank_in_routes_* being up to date.
  Eval job_route_cost(Index v_target, Index v, Index r);

  // Compute lower bound for the cost of relocating job at rank r
  // (resp. jobs at rank r1 and r2) in route v to any other
  // (compatible) route.
  Eval relocate_cost_lower_bound(Index v, Index r);
  Eval relocate_cost_lower_bound(Index v, Index r1, Index r2);

  void remove_from_routes();

  // Remove jobs and refill, ahead of the next deepening step.
  void ruin_and_recreate();

public:
  // With completed_depth set, tw_sol is the best solution of a search
  // paused once that depth was completed (see run), and the climb resumes
  // from there.
  LocalSearch(const Input& input,
              std::vector<Route>& tw_sol,
              unsigned depth,
              const Timeout& timeout,
              std::optional<unsigned> completed_depth = std::nullopt);

  utils::SolutionIndicators indicators() const;

  // Climb until the completed depth reaches until. Returns true when
  // pausing there with deeper steps left, false once the climb is over or
  // after stop is set (the search is then left unfinished).
  bool run(unsigned until = std::numeric_limits<unsigned>::max(),
           const std::atomic<bool>* stop = nullptr);
};

} // namespace vroom::ls

#endif
