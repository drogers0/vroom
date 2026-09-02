/*
 * T8 Engine Test Harness
 *
 * Tests realize_cap_compliant_schedule.
 *
 * Build (from repo root):
 *   cd src && CPLUS_INCLUDE_PATH=/opt/homebrew/include
 * LIBRARY_PATH=/opt/homebrew/lib \
 *     CXX=g++-15 g++-15 -std=c++20 -O2 -I. -DUSE_LIBGLPK=true \
 *     ../.t8_harness/t8_engine_test.cpp \
 *     -o ../.t8_harness/t8_engine_test \
 *     -L../lib -lvroom -L/opt/homebrew/lib -lglpk -lpthread -lssl -lcrypto
 */

#include <cassert>
#include <cstdio>
#include <string>
#include <vector>

#include "structures/generic/matrix.h"
#include "structures/vroom/break.h"
#include "structures/vroom/input/input.h"
#include "structures/vroom/location.h"
#include "structures/vroom/time_window.h"
#include "structures/vroom/tw_route.h"

using namespace vroom;

// Internal Duration = 100 * UserDuration (seconds).  DURATION_FACTOR = 100.

static int g_pass = 0;
static int g_fail = 0;

static void report(const char* name, bool ok, const char* evidence) {
  if (ok) {
    ++g_pass;
    std::printf("  PASS  %-22s  %s\n", name, evidence);
  } else {
    ++g_fail;
    std::printf("  FAIL  %-22s  %s\n", name, evidence);
  }
}

// ---------------------------------------------------------------------------
// TestEnv: owns the matrix and input for one test.
// ---------------------------------------------------------------------------
struct TestEnv {
  Matrix<UserDuration> dur_mat;
  Matrix<UserCost> cost_mat;     // zeros; needed for choose_ETA's v.eval()
  Matrix<UserDistance> dist_mat; // zeros; needed for choose_ETA's v.eval()
  Input input;

  explicit TestEnv(unsigned n_locs)
    : dur_mat(n_locs), cost_mat(n_locs), dist_mat(n_locs), input() {
  }

  void set_travel(unsigned from, unsigned to, UserDuration secs) {
    dur_mat[from][to] = secs;
    dur_mat[to][from] = secs;
  }

  // attach all three matrices so cost_wrapper has no dangling pointers.
  void attach_matrices() {
    input.vehicles[0].cost_wrapper.set_durations_matrix(&dur_mat);
    input.vehicles[0].cost_wrapper.set_costs_matrix(&cost_mat);
    input.vehicles[0].cost_wrapper.set_distances_matrix(&dist_mat);
  }

  // Vehicle must have at least a start; we use Location(0) (same as first
  // job in most tests) so the engine's init_rem = duration(0,0) = 0.
  void add_plain_vehicle() {
    Vehicle v(1, std::make_optional(Location(0)), std::nullopt);
    input.add_vehicle(v);
    attach_matrices();
  }

  void add_vehicle_with_breaks(const std::vector<Break>& brks) {
    Vehicle v(1,
              std::make_optional(Location(0)),
              std::nullopt,
              DEFAULT_PROFILE,
              Amount(0),
              Skills(),
              TimeWindow(),
              brks);
    input.add_vehicle(v);
    attach_matrices();
  }

  // Mimic private set_jobs_durations_per_vehicle_type().
  void finalize_jobs() {
    for (auto& j : input.jobs) {
      j.setups = {j.default_setup};
      j.services = {j.default_service};
    }
  }

  TWRoute build_route(const std::vector<Index>& seq) {
    TWRoute tw(input, 0, 0);
    tw.replace(input, input.zero_amount(), seq.begin(), seq.end(), 0, 0);
    return tw;
  }

  // Insert jobs at [first_rank, first_rank) without removal and check TW
  // validity via the template overload of is_valid_addition_for_tw.
  bool check_insertion(TWRoute& tw,
                       const std::vector<Index>& jobs,
                       Index first_rank) {
    return tw.is_valid_addition_for_tw(input,
                                       input.zero_amount(),
                                       jobs.begin(),
                                       jobs.end(),
                                       first_rank,
                                       first_rank);
  }
};

// ---------------------------------------------------------------------------
// Independent schedule checker: recomputes departures and verifies caps.
// Returns worst excess (0 = all caps satisfied).
// ---------------------------------------------------------------------------
static Duration check_schedule(const Input& input,
                               const TWRoute& tw,
                               const std::vector<Duration>& S) {
  const Index v_type = tw.v_type;
  const Index NO_LOC = std::numeric_limits<Index>::max();
  const std::size_t N = tw.route.size();

  std::vector<Duration> dep(N);
  Index prev_loc = NO_LOC;
  for (std::size_t i = 0; i < N; ++i) {
    const auto& j = input.jobs[tw.route[i]];
    const Duration at = (j.index() == prev_loc)
                          ? j.services[v_type]
                          : j.setups[v_type] + j.services[v_type];
    dep[i] = S[i] + at;
    prev_loc = j.index();
  }

  Duration worst = 0;
  for (std::size_t i = 0; i < N; ++i) {
    const auto& j = input.jobs[tw.route[i]];
    if (j.type != JOB_TYPE::PICKUP || !j.max_transit_time.has_value()) {
      continue;
    }
    const Index del_rank = tw.route[i] + 1;
    for (std::size_t d = i + 1; d < N; ++d) {
      if (tw.route[d] == del_rank) {
        const Duration ride = S[d] - dep[i];
        const Duration cap = j.max_transit_time.value();
        const Duration exc = (ride > cap) ? (ride - cap) : 0;
        if (exc > worst)
          worst = exc;
        break;
      }
    }
  }
  return worst;
}

// ===========================================================================
// T8-1a: Single pair — excess fully absorbed at delivery wait.
//
// Route: [pickup(loc=0), delivery(loc=1)].
// travel(0,1)=10 s (1000 internal).  cap=15 s (1500).
// delivery TW=[25,∞] (2500 internal).
//
// ASAP: S_P=0, dep_P=0, arrival_D=1000, S_D=2500, ride=2500−0=2500>1500.
// excess=1000.  Wait at D=1500 ≥ excess.
// Engine delays P by 1000 → dep_P=1000, S_D=2500, ride=1500=cap.
// Expected: feasible, S=[1000, 2500].
// ===========================================================================
static void test_t8_1a() {
  TestEnv env(2);
  env.set_travel(0, 1, 10);
  env.add_plain_vehicle();

  Job pickup(1,
             JOB_TYPE::PICKUP,
             Location(0),
             0,
             0,
             Amount(0),
             Skills(),
             0,
             {TimeWindow()},
             "",
             {},
             {},
             UserDuration(15));
  Job delivery(2,
               JOB_TYPE::DELIVERY,
               Location(1),
               0,
               0,
               Amount(0),
               Skills(),
               0,
               {TimeWindow(25, 1000000)},
               "",
               {},
               {},
               UserDuration(15));
  env.input.add_shipment(pickup, delivery);
  env.finalize_jobs();

  TWRoute tw = env.build_route({0, 1});
  auto result = tw.realize_cap_compliant_schedule(env.input);

  bool ok = result.has_value();
  char ev[128] = {};
  if (ok) {
    const Duration excess = check_schedule(env.input, tw, result.value());
    const bool exact =
      (result.value()[0] == 1000) && (result.value()[1] == 2500);
    ok = (excess == 0) && exact;
    std::snprintf(ev,
                  sizeof(ev),
                  "S=[%lld,%lld] excess=%lld",
                  (long long)result.value()[0],
                  (long long)result.value()[1],
                  (long long)excess);
  } else {
    std::snprintf(ev, sizeof(ev), "nullopt (unexpected)");
  }
  report("T8-1a", ok, ev);
}

// ===========================================================================
// T8-1b: Path travel alone exceeds cap, no absorbing wait → nullopt.
//
// travel=20 s (2000).  cap=15 s (1500).  delivery TW=[0,∞].
// ASAP: ride=2000>1500, excess=500, no waits → loops 4 iters → nullopt.
// ===========================================================================
static void test_t8_1b() {
  TestEnv env(2);
  env.set_travel(0, 1, 20);
  env.add_plain_vehicle();

  Job pickup(1,
             JOB_TYPE::PICKUP,
             Location(0),
             0,
             0,
             Amount(0),
             Skills(),
             0,
             {TimeWindow()},
             "",
             {},
             {},
             UserDuration(15));
  Job delivery(2,
               JOB_TYPE::DELIVERY,
               Location(1),
               0,
               0,
               Amount(0),
               Skills(),
               0,
               {TimeWindow()},
               "",
               {},
               {},
               UserDuration(15));
  env.input.add_shipment(pickup, delivery);
  env.finalize_jobs();

  TWRoute tw = env.build_route({0, 1});
  auto result = tw.realize_cap_compliant_schedule(env.input);

  bool ok = !result.has_value();
  char ev[64];
  std::snprintf(ev,
                sizeof(ev),
                "nullopt=%s (travel 2000 > cap 1500)",
                ok ? "yes" : "no");
  report("T8-1b", ok, ev);
}

// ===========================================================================
// T8-2a: Intermediate job with tight TW blocks delay → nullopt.
//
// Route: [pickup(loc=0), intermediate-single(loc=1), delivery(loc=2)].
// travels: 0→1=5s(500), 1→2=5s(500).  cap=12s(1200).
// intermediate TW=[5,5] (tight at 500 internal).  delivery TW=[15,∞](1500).
//
// ASAP: S_P=0, S_mid=500, S_D=1500, ride=1500>1200, excess=300.
// Delay P by 300 → arrival_mid=800 > TW_mid.end=500 → TW missed → nullopt.
// ===========================================================================
static void test_t8_2a() {
  TestEnv env(3);
  env.set_travel(0, 1, 5);
  env.set_travel(1, 2, 5);
  env.dur_mat[0][2] = 10; // not used in this route but needed for matrix
  env.dur_mat[2][0] = 10;
  env.add_plain_vehicle();

  // add_shipment: pickup=jobs[0](loc=0), delivery=jobs[1](loc=2)
  Job pickup(1,
             JOB_TYPE::PICKUP,
             Location(0),
             0,
             0,
             Amount(0),
             Skills(),
             0,
             {TimeWindow()},
             "",
             {},
             {},
             UserDuration(12));
  Job delivery(2,
               JOB_TYPE::DELIVERY,
               Location(2),
               0,
               0,
               Amount(0),
               Skills(),
               0,
               {TimeWindow(15, 1000000)},
               "",
               {},
               {},
               UserDuration(12));
  env.input.add_shipment(pickup, delivery);

  // add_job: intermediate single at loc=1, TW=[5,5].  Becomes jobs[2].
  Job intermediate(3,
                   Location(1),
                   0,
                   0,
                   Amount(0),
                   Amount(0),
                   Skills(),
                   0,
                   {TimeWindow(5, 5)});
  env.input.add_job(intermediate);
  env.finalize_jobs();

  // Route: pickup(0), intermediate(2), delivery(1)
  TWRoute tw = env.build_route({0, 2, 1});
  auto result = tw.realize_cap_compliant_schedule(env.input);

  bool ok = !result.has_value();
  char ev[64];
  std::snprintf(ev,
                sizeof(ev),
                "nullopt=%s (tight intermediate TW blocked delay)",
                ok ? "yes" : "no");
  report("T8-2a", ok, ev);
}

// ===========================================================================
// T8-2b: Widened intermediate TW → feasible.
//
// Same geometry.  intermediate TW=[5,100] (500..10000 internal).
// After delay 300: arrival_mid=800 ≤ 10000 → ok; S_D=1500,
// ride=1500−300=1200=cap.
// ===========================================================================
static void test_t8_2b() {
  TestEnv env(3);
  env.set_travel(0, 1, 5);
  env.set_travel(1, 2, 5);
  env.dur_mat[0][2] = 10;
  env.dur_mat[2][0] = 10;
  env.add_plain_vehicle();

  Job pickup(1,
             JOB_TYPE::PICKUP,
             Location(0),
             0,
             0,
             Amount(0),
             Skills(),
             0,
             {TimeWindow()},
             "",
             {},
             {},
             UserDuration(12));
  Job delivery(2,
               JOB_TYPE::DELIVERY,
               Location(2),
               0,
               0,
               Amount(0),
               Skills(),
               0,
               {TimeWindow(15, 1000000)},
               "",
               {},
               {},
               UserDuration(12));
  env.input.add_shipment(pickup, delivery);

  Job intermediate(3,
                   Location(1),
                   0,
                   0,
                   Amount(0),
                   Amount(0),
                   Skills(),
                   0,
                   {TimeWindow(5, 100)});
  env.input.add_job(intermediate);
  env.finalize_jobs();

  TWRoute tw = env.build_route({0, 2, 1});
  auto result = tw.realize_cap_compliant_schedule(env.input);

  bool ok = result.has_value();
  char ev[128] = {};
  if (ok) {
    const Duration excess = check_schedule(env.input, tw, result.value());
    ok = (excess == 0);
    std::snprintf(ev,
                  sizeof(ev),
                  "S=[%lld,%lld,%lld] excess=%lld",
                  (long long)result.value()[0],
                  (long long)result.value()[1],
                  (long long)result.value()[2],
                  (long long)excess);
  } else {
    std::snprintf(ev, sizeof(ev), "nullopt (unexpected)");
  }
  report("T8-2b", ok, ev);
}

// ===========================================================================
// T8-3: Multi-pair interleaved — pair A fix creates pair B excess, converges.
//
// Route: [P_A(loc=0), P_B(loc=1), D_A(loc=2), D_B(loc=3)].
// job input ranks: P_A=0, D_A=1, P_B=2, D_B=3.
// All travels=5s(500).  delivery_A TW=[20,∞](2000).
// cap_A=15s(1500), cap_B=14s(1400).
//
// ASAP: S=[0,500,2000,2500].  Pair A excess=500, Pair B excess=600.
// Iter 1 (worst=B, exc=600): S[1]=1100 → S=[0,1100,2000,2500].
//   Pair A exc=500, Pair B=2500−1100=1400=cap ✓.
// Iter 2 (worst=A, exc=500): S[0]=500 → forward S=[500,1000,2000,2500].
//   Pair A=2000−500=1500=cap ✓, Pair B=2500−1000=1500>1400, exc=100.
// Iter 3: S[1]=1100 → S=[500,1100,2000,2500]. Both pairs OK. Converged.
// ===========================================================================
static void test_t8_3() {
  TestEnv env(4);
  // Simple travel: adjacent locations 5s, non-adjacent 10+.
  for (unsigned i = 0; i < 4; ++i)
    for (unsigned j = 0; j < 4; ++j)
      env.dur_mat[i][j] = (i == j) ? 0 : 5;
  env.add_plain_vehicle();

  Job pa(1,
         JOB_TYPE::PICKUP,
         Location(0),
         0,
         0,
         Amount(0),
         Skills(),
         0,
         {TimeWindow()},
         "",
         {},
         {},
         UserDuration(15));
  Job da(2,
         JOB_TYPE::DELIVERY,
         Location(2),
         0,
         0,
         Amount(0),
         Skills(),
         0,
         {TimeWindow(20, 1000000)},
         "",
         {},
         {},
         UserDuration(15));
  env.input.add_shipment(pa, da); // PA=jobs[0], DA=jobs[1]

  Job pb(3,
         JOB_TYPE::PICKUP,
         Location(1),
         0,
         0,
         Amount(0),
         Skills(),
         0,
         {TimeWindow()},
         "",
         {},
         {},
         UserDuration(14));
  Job db(4,
         JOB_TYPE::DELIVERY,
         Location(3),
         0,
         0,
         Amount(0),
         Skills(),
         0,
         {TimeWindow()},
         "",
         {},
         {},
         UserDuration(14));
  env.input.add_shipment(pb, db); // PB=jobs[2], DB=jobs[3]
  env.finalize_jobs();

  // Route: PA(0), PB(2), DA(1), DB(3)
  TWRoute tw = env.build_route({0, 2, 1, 3});
  auto result = tw.realize_cap_compliant_schedule(env.input);

  bool ok = result.has_value();
  char ev[160] = {};
  if (ok) {
    const Duration excess = check_schedule(env.input, tw, result.value());
    ok = (excess == 0);
    std::snprintf(ev,
                  sizeof(ev),
                  "S=[%lld,%lld,%lld,%lld] excess=%lld",
                  (long long)result.value()[0],
                  (long long)result.value()[1],
                  (long long)result.value()[2],
                  (long long)result.value()[3],
                  (long long)excess);
  } else {
    std::snprintf(ev, sizeof(ev), "nullopt (unexpected)");
  }
  report("T8-3", ok, ev);
}

// ===========================================================================
// T8-4: Multi-pair — path alone exceeds cap_A → nullopt.
//
// Route: [P_A(loc=0), P_B(loc=1), D_A(loc=2), D_B(loc=3)].
// All travels=5s(500).  cap_A=8s(800).
// Path P_A→P_B→D_A = 500+500=1000 > 800.  Every schedule: ride_A ≥ 1000 > 800.
// Engine loops until budget (6 iters) → nullopt.
// ===========================================================================
static void test_t8_4() {
  TestEnv env(4);
  for (unsigned i = 0; i < 4; ++i)
    for (unsigned j = 0; j < 4; ++j)
      env.dur_mat[i][j] = (i == j) ? 0 : 5;
  env.add_plain_vehicle();

  Job pa(1,
         JOB_TYPE::PICKUP,
         Location(0),
         0,
         0,
         Amount(0),
         Skills(),
         0,
         {TimeWindow()},
         "",
         {},
         {},
         UserDuration(8));
  Job da(2,
         JOB_TYPE::DELIVERY,
         Location(2),
         0,
         0,
         Amount(0),
         Skills(),
         0,
         {TimeWindow()},
         "",
         {},
         {},
         UserDuration(8));
  env.input.add_shipment(pa, da);

  Job pb(3,
         JOB_TYPE::PICKUP,
         Location(1),
         0,
         0,
         Amount(0),
         Skills(),
         0,
         {TimeWindow()},
         "",
         {},
         {},
         UserDuration(100));
  Job db(4,
         JOB_TYPE::DELIVERY,
         Location(3),
         0,
         0,
         Amount(0),
         Skills(),
         0,
         {TimeWindow()},
         "",
         {},
         {},
         UserDuration(100));
  env.input.add_shipment(pb, db);
  env.finalize_jobs();

  TWRoute tw = env.build_route({0, 2, 1, 3});
  auto result = tw.realize_cap_compliant_schedule(env.input);

  bool ok = !result.has_value();
  char ev[64];
  std::snprintf(ev,
                sizeof(ev),
                "nullopt=%s (path 1000 > cap 800)",
                ok ? "yes" : "no");
  report("T8-4", ok, ev);
}

// ===========================================================================
// T8-5a: Break between pickup and delivery absorbs delay.
//
// Route: [pickup(loc=0), delivery(loc=1)].  1 break (forced between them).
// travel(0,1)=10s(1000).  break TW=[25,∞](2500), service=0.  cap=20s(2000).
//
// With break at pos=1 (before delivery):
// ASAP: cur_dep_after_P=0, rem=1000, break:
// margin=2500>1000→rem=0,cur_dep=2500,
//       break_S=2500.  delivery: arrival=2500, S_D=2500. ride=2500−0=2500>2000.
// excess=500.  Delay P by 500: cur_dep=500, break margin=2000>1000→rem=0,
//   cur_dep=2500, S_D=2500.  ride=2500−500=2000=cap.  S_D unchanged.
// ===========================================================================
static void test_t8_5a() {
  TestEnv env(2);
  env.set_travel(0, 1, 10);

  // Break TW opens at 25 s = 2500 internal.
  Break brk(1, {TimeWindow(25, 1000000)}, 0 /*service=0*/);
  env.add_vehicle_with_breaks({brk});

  Job pickup(1,
             JOB_TYPE::PICKUP,
             Location(0),
             0,
             0,
             Amount(0),
             Skills(),
             0,
             {TimeWindow()},
             "",
             {},
             {},
             UserDuration(20));
  Job delivery(2,
               JOB_TYPE::DELIVERY,
               Location(1),
               0,
               0,
               Amount(0),
               Skills(),
               0,
               {TimeWindow()},
               "",
               {},
               {},
               UserDuration(20));
  env.input.add_shipment(pickup, delivery);
  env.finalize_jobs();

  TWRoute tw = env.build_route({0, 1});

  // Force break to be between pickup (pos=0) and delivery (pos=1).
  // breaks_at_rank: [before_pos0, before_pos1, trailing] = [0, 1, 0].
  // breaks_counts:  [cumul_at_pos0, cumul_at_pos1, cumul_trailing] = [0, 1, 1].
  tw.breaks_at_rank = {0, 1, 0};
  tw.breaks_counts = {0, 1, 1};

  auto result = tw.realize_cap_compliant_schedule(env.input);

  bool ok = result.has_value();
  char ev[160] = {};
  if (ok) {
    const Duration excess = check_schedule(env.input, tw, result.value());
    const bool sd_unchanged = (result.value()[1] == 2500);
    ok = (excess == 0) && sd_unchanged;
    std::snprintf(ev,
                  sizeof(ev),
                  "S=[%lld,%lld] excess=%lld S_D_unchanged=%s",
                  (long long)result.value()[0],
                  (long long)result.value()[1],
                  (long long)excess,
                  sd_unchanged ? "yes" : "no");
  } else {
    std::snprintf(ev, sizeof(ev), "nullopt (unexpected)");
  }
  report("T8-5a", ok, ev);
}

// ===========================================================================
// T8-5b: Break with zero wait, delivery no wait → nullopt.
//
// travel=10s(1000).  break TW=[0,∞](no wait).  cap=5s(500).
// ASAP: break_S=0 (no wait), S_D=1000, ride=1000>500.
// No wait anywhere — delay propagates without absorption → nullopt.
// ===========================================================================
static void test_t8_5b() {
  TestEnv env(2);
  env.set_travel(0, 1, 10);

  Break brk(1, {TimeWindow()}, 0); // TW=[0,∞], service=0
  env.add_vehicle_with_breaks({brk});

  Job pickup(1,
             JOB_TYPE::PICKUP,
             Location(0),
             0,
             0,
             Amount(0),
             Skills(),
             0,
             {TimeWindow()},
             "",
             {},
             {},
             UserDuration(5));
  Job delivery(2,
               JOB_TYPE::DELIVERY,
               Location(1),
               0,
               0,
               Amount(0),
               Skills(),
               0,
               {TimeWindow()},
               "",
               {},
               {},
               UserDuration(5));
  env.input.add_shipment(pickup, delivery);
  env.finalize_jobs();

  TWRoute tw = env.build_route({0, 1});
  tw.breaks_at_rank = {0, 1, 0};
  tw.breaks_counts = {0, 1, 1};

  auto result = tw.realize_cap_compliant_schedule(env.input);

  bool ok = !result.has_value();
  char ev[64];
  std::snprintf(ev,
                sizeof(ev),
                "nullopt=%s (no absorbing wait)",
                ok ? "yes" : "no");
  report("T8-5b", ok, ev);
}

// ===========================================================================
// T8-6a: Multiple TWs at pickup — delay exceeds TW_0, engine advances to TW_1.
//
// Route: [pickup(loc=0), delivery(loc=1)].
// travel=5s(500).  cap=15s(1500).  delivery TW=[30,∞](3000).
// Pickup TW_0=[0,8](0..800).  TW_1=[25,∞](2500..∞).
//
// ASAP: S_P=0 (TW_0), dep=0, S_D=3000, ride=3000>1500, excess=1500.
// candidate=0+1500=1500 > TW_0.end=800 → advance to TW_1: S_P=2500.
// Forward: S_D=max(3000,3000)=3000. ride=3000−2500=500 ≤ 1500. ✓
// ===========================================================================
static void test_t8_6a() {
  TestEnv env(2);
  env.set_travel(0, 1, 5);
  env.add_plain_vehicle();

  Job pickup(1,
             JOB_TYPE::PICKUP,
             Location(0),
             0,
             0,
             Amount(0),
             Skills(),
             0,
             {TimeWindow(0, 8), TimeWindow(25, 1000000)},
             "",
             {},
             {},
             UserDuration(15));
  Job delivery(2,
               JOB_TYPE::DELIVERY,
               Location(1),
               0,
               0,
               Amount(0),
               Skills(),
               0,
               {TimeWindow(30, 1000000)},
               "",
               {},
               {},
               UserDuration(15));
  env.input.add_shipment(pickup, delivery);
  env.finalize_jobs();

  TWRoute tw = env.build_route({0, 1});
  auto result = tw.realize_cap_compliant_schedule(env.input);

  bool ok = result.has_value();
  char ev[128] = {};
  if (ok) {
    const Duration excess = check_schedule(env.input, tw, result.value());
    // After TW advancement S_P should jump to TW_1.start=2500.
    const bool tw_advanced = (result.value()[0] == 2500);
    ok = (excess == 0) && tw_advanced;
    std::snprintf(ev,
                  sizeof(ev),
                  "S=[%lld,%lld] excess=%lld TW_adv=%s",
                  (long long)result.value()[0],
                  (long long)result.value()[1],
                  (long long)excess,
                  tw_advanced ? "yes" : "no");
  } else {
    std::snprintf(ev, sizeof(ev), "nullopt (unexpected)");
  }
  report("T8-6a", ok, ev);
}

// ===========================================================================
// T8-6b: No TW can accommodate delay → nullopt.
//
// Pickup TW_0=[0,5](0..500), TW_1=[10,12](1000..1200).  No TW_2.
// travel=5s(500).  cap=10s(1000).  delivery TW=[30,∞](3000).
//
// ASAP: ride=3000>1000, excess=2000.  candidate=2000>TW_1.end=1200 → nullopt.
// ===========================================================================
static void test_t8_6b() {
  TestEnv env(2);
  env.set_travel(0, 1, 5);
  env.add_plain_vehicle();

  Job pickup(1,
             JOB_TYPE::PICKUP,
             Location(0),
             0,
             0,
             Amount(0),
             Skills(),
             0,
             {TimeWindow(0, 5), TimeWindow(10, 12)},
             "",
             {},
             {},
             UserDuration(10));
  Job delivery(2,
               JOB_TYPE::DELIVERY,
               Location(1),
               0,
               0,
               Amount(0),
               Skills(),
               0,
               {TimeWindow(30, 1000000)},
               "",
               {},
               {},
               UserDuration(10));
  env.input.add_shipment(pickup, delivery);
  env.finalize_jobs();

  TWRoute tw = env.build_route({0, 1});
  auto result = tw.realize_cap_compliant_schedule(env.input);

  bool ok = !result.has_value();
  char ev[64];
  std::snprintf(ev,
                sizeof(ev),
                "nullopt=%s (no TW accommodates delay)",
                ok ? "yes" : "no");
  report("T8-6b", ok, ev);
}

// ===========================================================================
// T8-7: Boundary equality — ASAP transit_time == cap exactly → schedule
// unchanged.
//
// travel=20s(2000).  cap=20s(2000).  delivery TW=[0,∞].
// ASAP: S_P=0, dep=0, S_D=2000, ride=2000=cap → zero excess.
// Expected: feasible, S=[0, 2000] returned unchanged.
// ===========================================================================
static void test_t8_7() {
  TestEnv env(2);
  env.set_travel(0, 1, 20);
  env.add_plain_vehicle();

  Job pickup(1,
             JOB_TYPE::PICKUP,
             Location(0),
             0,
             0,
             Amount(0),
             Skills(),
             0,
             {TimeWindow()},
             "",
             {},
             {},
             UserDuration(20));
  Job delivery(2,
               JOB_TYPE::DELIVERY,
               Location(1),
               0,
               0,
               Amount(0),
               Skills(),
               0,
               {TimeWindow()},
               "",
               {},
               {},
               UserDuration(20));
  env.input.add_shipment(pickup, delivery);
  env.finalize_jobs();

  TWRoute tw = env.build_route({0, 1});
  auto result = tw.realize_cap_compliant_schedule(env.input);

  bool ok = result.has_value();
  char ev[128] = {};
  if (ok) {
    const Duration excess = check_schedule(env.input, tw, result.value());
    const bool asap_unchanged =
      (result.value()[0] == 0) && (result.value()[1] == 2000);
    ok = (excess == 0) && asap_unchanged;
    std::snprintf(ev,
                  sizeof(ev),
                  "S=[%lld,%lld] excess=%lld ASAP_unchanged=%s",
                  (long long)result.value()[0],
                  (long long)result.value()[1],
                  (long long)excess,
                  asap_unchanged ? "yes" : "no");
  } else {
    std::snprintf(ev, sizeof(ev), "nullopt (unexpected)");
  }
  report("T8-7", ok, ev);
}

// ===========================================================================
// T8-8: Self-verification stress test — independent schedule re-derivation.
//
// Uses T8-1a fixture (feasible).  For the returned schedule:
//   (a) realize_cap_compliant_schedule feasibility must agree with
//   realize_cap_compliant_schedule. (b) Independently re-derive arrivals and
//   verify TW + cap constraints.
// This exercises the self-verification codepath from the user side.
// ===========================================================================
static void test_t8_8() {
  TestEnv env(2);
  env.set_travel(0, 1, 10);
  env.add_plain_vehicle();

  Job pickup(1,
             JOB_TYPE::PICKUP,
             Location(0),
             0,
             0,
             Amount(0),
             Skills(),
             0,
             {TimeWindow()},
             "",
             {},
             {},
             UserDuration(15));
  Job delivery(2,
               JOB_TYPE::DELIVERY,
               Location(1),
               0,
               0,
               Amount(0),
               Skills(),
               0,
               {TimeWindow(25, 1000000)},
               "",
               {},
               {},
               UserDuration(15));
  env.input.add_shipment(pickup, delivery);
  env.finalize_jobs();

  TWRoute tw = env.build_route({0, 1});

  // (a) flag agreement.
  const bool flag = tw.realize_cap_compliant_schedule(env.input).has_value();
  const auto result = tw.realize_cap_compliant_schedule(env.input);
  const bool result_has = result.has_value();
  bool ok = (flag == result_has);

  // (b) independent re-derivation when feasible.
  char ev[256] = {};
  if (result_has) {
    const auto& S = result.value();
    const Index v_type = tw.v_type;
    const Index NO_LOC = std::numeric_limits<Index>::max();
    // Re-derive departure from raw input.
    Index prev = NO_LOC;
    Duration dep0 = 0;
    {
      const auto& j = env.input.jobs[tw.route[0]];
      const Duration at = (j.index() == prev)
                            ? j.services[v_type]
                            : j.setups[v_type] + j.services[v_type];
      dep0 = S[0] + at;
      prev = j.index();
    }
    // Check TW for delivery.
    const auto& dj = env.input.jobs[tw.route[1]];
    const auto& dtw = dj.tws.front();
    const bool tw_ok = (S[1] >= dtw.start) && (S[1] <= dtw.end);
    // Check ride.
    const Duration ride = S[1] - dep0;
    const Duration cap = env.input.jobs[tw.route[0]].max_transit_time.value();
    const bool cap_ok = (ride <= cap);
    ok = ok && tw_ok && cap_ok;
    std::snprintf(ev,
                  sizeof(ev),
                  "flags_agree=%s tw_ok=%s ride=%lld cap=%lld cap_ok=%s",
                  (flag == result_has) ? "T" : "F",
                  tw_ok ? "T" : "F",
                  (long long)ride,
                  (long long)cap,
                  cap_ok ? "T" : "F");
  } else {
    std::snprintf(ev,
                  sizeof(ev),
                  "flags_agree=%s result_nullopt",
                  ok ? "T" : "F");
  }
  report("T8-8", ok, ev);
}

// ===========================================================================
// T8-9: Consistency probe — all engine-feasible schedules satisfy caps
//       when transit times are independently recomputed from scratch.
//
// Runs two feasible fixtures (T8-1a and T8-7) and for each:
//   1. Calls realize_cap_compliant_schedule.
//   2. Re-derives departure times from raw input (not engine intermediates).
//   3. Checks every constrained pair: transit_time ≤ cap.
//
// Note: choose_ETA requires full Input initialization
// (set_skills_compatibility, _vehicle_to_job_compatibility) which requires
// compute_solution — not callable from a unit harness.  This independent
// re-derivation is equivalent for the consistency property: the engine may only
// return a schedule that is genuinely cap-compliant under VROOM's ride-time
// semantics.
// ===========================================================================
static bool transit_time_probe(const char* label,
                               TestEnv& env,
                               const std::vector<Index>& seq) {
  TWRoute tw = env.build_route(seq);
  const auto result = tw.realize_cap_compliant_schedule(env.input);
  if (!result.has_value()) {
    char ev[64];
    std::snprintf(ev,
                  sizeof(ev),
                  "%s: engine nullopt (pre-condition fail)",
                  label);
    report("T8-9", false, ev);
    return false;
  }
  const Duration excess = check_schedule(env.input, tw, result.value());
  bool ok = (excess == 0);
  char ev[128];
  std::snprintf(ev,
                sizeof(ev),
                "%s: excess=%lld (independently re-derived)",
                label,
                (long long)excess);
  report("T8-9", ok, ev);
  return ok;
}

static void test_t8_9() {
  // Probe 1: T8-1a fixture — delay absorbed at delivery.
  {
    TestEnv env(2);
    env.set_travel(0, 1, 10);
    env.add_plain_vehicle();
    Job pickup(1,
               JOB_TYPE::PICKUP,
               Location(0),
               0,
               0,
               Amount(0),
               Skills(),
               0,
               {TimeWindow()},
               "",
               {},
               {},
               UserDuration(15));
    Job delivery(2,
                 JOB_TYPE::DELIVERY,
                 Location(1),
                 0,
                 0,
                 Amount(0),
                 Skills(),
                 0,
                 {TimeWindow(25, 1000000)},
                 "",
                 {},
                 {},
                 UserDuration(15));
    env.input.add_shipment(pickup, delivery);
    env.finalize_jobs();
    transit_time_probe("T8-1a_fixture", env, {0, 1});
  }

  // Probe 2: T8-7 fixture — exact boundary (ride==cap).
  {
    TestEnv env(2);
    env.set_travel(0, 1, 20);
    env.add_plain_vehicle();
    Job pickup(1,
               JOB_TYPE::PICKUP,
               Location(0),
               0,
               0,
               Amount(0),
               Skills(),
               0,
               {TimeWindow()},
               "",
               {},
               {},
               UserDuration(20));
    Job delivery(2,
                 JOB_TYPE::DELIVERY,
                 Location(1),
                 0,
                 0,
                 Amount(0),
                 Skills(),
                 0,
                 {TimeWindow()},
                 "",
                 {},
                 {},
                 UserDuration(20));
    env.input.add_shipment(pickup, delivery);
    env.finalize_jobs();
    transit_time_probe("T8-7_fixture", env, {0, 1});
  }
}

// ===========================================================================
// T8-10 / guard: has_max_transit_time=false → schedule feasibility true,
//                realize_cap_compliant_schedule=nullopt (guard fast paths).
// ===========================================================================
static void test_t8_10() {
  TestEnv env(2);
  env.set_travel(0, 1, 10);
  env.add_plain_vehicle();

  // SINGLE jobs — no max_transit_time → has_max_transit_time stays false.
  Job
    j1(1, Location(0), 0, 0, Amount(0), Amount(0), Skills(), 0, {TimeWindow()});
  Job
    j2(2, Location(1), 0, 0, Amount(0), Amount(0), Skills(), 0, {TimeWindow()});
  env.input.add_job(j1);
  env.input.add_job(j2);
  env.finalize_jobs();

  TWRoute tw = env.build_route({0, 1});

  // Guard off: realizer must take the fast path and return nullopt (output
  // code then keeps plain ASAP times).
  const auto realize = tw.realize_cap_compliant_schedule(env.input);

  const bool ok = !env.input.has_max_transit_time() && !realize.has_value();
  char ev[128];
  std::snprintf(ev,
                sizeof(ev),
                "has_mrt=%s realize_null=%s",
                env.input.has_max_transit_time() ? "T" : "F",
                !realize.has_value() ? "T" : "F");
  report("T8-10/guard", ok, ev);
}

// ===========================================================================
// T8-11: vehicle end TW violated after engine schedule → nullopt.
//
// Route: [pickup(loc=0), delivery(loc=1)].
// travel(0,1)=10s (1000).  cap=15s (1500).  delivery TW=[0,∞].
// Vehicle: start=Location(0), end=Location(1), wide TW (built normally).
//
// Route is built with default (infinite) vehicle TW so all replace()
// assertions pass.  Then tw.v_end is tightened to 800 (internal) — same
// technique as setting breaks_at_rank after build — to simulate a vehicle
// whose end TW end was tighter than the engine's departure from the last
// stop.
//
// ASAP: S_P=0, dep_P=0, S_D=1000, dep_D=1000.
// cap check: ride=1000 ≤ 1500 → cap satisfied (no delay needed).
// Vehicle end: dep_D + dur(1,1) = 1000+0 = 1000 > tw.v_end=800 → nullopt.
// ===========================================================================
static void test_t8_11() {
  TestEnv env(2);
  env.set_travel(0, 1, 10);

  // Vehicle with end location; wide (default) TW so build_route passes.
  Vehicle v(1,
            std::make_optional(Location(0)),
            std::make_optional(Location(1)),
            DEFAULT_PROFILE,
            Amount(0),
            Skills(),
            TimeWindow()); // default TW = [0, ∞]
  env.input.add_vehicle(v);
  env.attach_matrices();

  Job pickup(1,
             JOB_TYPE::PICKUP,
             Location(0),
             0,
             0,
             Amount(0),
             Skills(),
             0,
             {TimeWindow()},
             "",
             {},
             {},
             UserDuration(15));
  Job delivery(2,
               JOB_TYPE::DELIVERY,
               Location(1),
               0,
               0,
               Amount(0),
               Skills(),
               0,
               {TimeWindow()},
               "",
               {},
               {},
               UserDuration(15));
  env.input.add_shipment(pickup, delivery);
  env.finalize_jobs();

  TWRoute tw = env.build_route({0, 1});

  // Tighten v_end to 800 internal units.  dep_D = 1000, dur(1,1) = 0, so
  // dep_D + end_travel = 1000 > 800 — vehicle end TW violated.
  tw.v_end = 800;

  auto result = tw.realize_cap_compliant_schedule(env.input);

  bool ok = !result.has_value();
  char ev[128];
  std::snprintf(ev,
                sizeof(ev),
                "nullopt=%s (dep_D=1000 > v_end=800, stop TWs OK)",
                ok ? "yes" : "no");
  report("T8-11/veh_end", ok, ev);
}

// ===========================================================================
// T8-12: first_rank=1, both P and D in trace — witness short-circuits.
//
// Prefix route: [single_A(loc=0)].  Insert [pickup(loc=1), delivery(loc=2)]
// at first_rank=1.  Virtual: [single_A, pickup, delivery].
// travel(0,1)=5s(500), travel(1,2)=5s(500).  cap=12s(1200).
// Delivery TW=[15,∞](1500).
//
// Trace ASAP: pickup earliest=500, delivery earliest=max(1000,1500)=1500.
// transit=1500-500=1000 ≤ 1200 → ASAP witness accepts; engine not called.
// Seeded prefix buffers (first_rank=1, single_A) are populated but not used.
// Expected: is_valid_addition_for_tw returns true.
// Verify: full route [2,0,1] → realize gives S=[0,500,1500].
// ===========================================================================
static void test_t8_12() {
  TestEnv env(3);
  env.set_travel(0, 1, 5);
  env.set_travel(1, 2, 5);
  env.dur_mat[0][2] = 10;
  env.dur_mat[2][0] = 10;
  env.add_plain_vehicle();

  // add_shipment: jobs[0]=pickup(loc=1), jobs[1]=delivery(loc=2), cap=12s.
  Job pickup(1,
             JOB_TYPE::PICKUP,
             Location(1),
             0,
             0,
             Amount(0),
             Skills(),
             0,
             {TimeWindow()},
             "",
             {},
             {},
             UserDuration(12));
  Job delivery(2,
               JOB_TYPE::DELIVERY,
               Location(2),
               0,
               0,
               Amount(0),
               Skills(),
               0,
               {TimeWindow(15, 1000000)},
               "",
               {},
               {},
               UserDuration(12));
  env.input.add_shipment(pickup, delivery);

  // add_job: jobs[2]=single_A(loc=0)
  Job single_A(3,
               Location(0),
               0,
               0,
               Amount(0),
               Amount(0),
               Skills(),
               0,
               {TimeWindow()});
  env.input.add_job(single_A);
  env.finalize_jobs();

  // Prefix route: [single_A=jobs[2]].
  TWRoute tw = env.build_route({2});
  bool valid = env.check_insertion(tw, {0, 1}, 1);

  bool ok = valid;
  char ev[128] = {};
  if (ok) {
    // Verify: commit full route and check realize.
    TWRoute tw2 = env.build_route({2, 0, 1});
    auto result = tw2.realize_cap_compliant_schedule(env.input);
    ok = result.has_value();
    if (ok) {
      const Duration excess = check_schedule(env.input, tw2, result.value());
      const bool exact = (result.value()[0] == 0) &&
                         (result.value()[1] == 500) &&
                         (result.value()[2] == 1500);
      ok = (excess == 0) && exact;
      std::snprintf(ev,
                    sizeof(ev),
                    "valid=true S=[%lld,%lld,%lld] excess=%lld",
                    (long long)result.value()[0],
                    (long long)result.value()[1],
                    (long long)result.value()[2],
                    (long long)excess);
    } else {
      std::snprintf(ev, sizeof(ev), "valid=true but realize=nullopt");
      ok = false;
    }
  } else {
    std::snprintf(ev, sizeof(ev), "valid=false (unexpected)");
  }
  report("T8-12/seed-witness", ok, ev);
}

// ===========================================================================
// T8-13: first_rank=1, pickup in prefix, delivery in suffix — engine called,
//         ASAP transit within cap.
//
// Prefix route: [pickup(loc=0), delivery(loc=2)] (cap=15s(1500)).
// Insert single(loc=1) at first_rank=1.
// Virtual: [pickup, single, delivery].
// travel(0,1)=5s(500), travel(1,2)=5s(500).
//
// Delivery in suffix → witness ineligible → engine called (seeded).
// Seeded ASAP: S[0]=0 (prefix), seed from dep[0]=0, S[1]=500, S[2]=1000.
// transit=1000-0=1000 ≤ 1500 → feasible.
// Expected: is_valid_addition_for_tw returns true.
// Verify: route [0,2,1] → realize gives S=[0,500,1000].
// ===========================================================================
static void test_t8_13() {
  TestEnv env(3);
  env.set_travel(0, 1, 5);
  env.set_travel(1, 2, 5);
  env.dur_mat[0][2] = 10;
  env.dur_mat[2][0] = 10;
  env.add_plain_vehicle();

  // jobs[0]=pickup(loc=0), jobs[1]=delivery(loc=2), cap=15s.
  Job pickup(1,
             JOB_TYPE::PICKUP,
             Location(0),
             0,
             0,
             Amount(0),
             Skills(),
             0,
             {TimeWindow()},
             "",
             {},
             {},
             UserDuration(15));
  Job delivery(2,
               JOB_TYPE::DELIVERY,
               Location(2),
               0,
               0,
               Amount(0),
               Skills(),
               0,
               {TimeWindow()},
               "",
               {},
               {},
               UserDuration(15));
  env.input.add_shipment(pickup, delivery);

  // jobs[2]=single(loc=1)
  Job single(3,
             Location(1),
             0,
             0,
             Amount(0),
             Amount(0),
             Skills(),
             0,
             {TimeWindow()});
  env.input.add_job(single);
  env.finalize_jobs();

  // Prefix route: [pickup=0, delivery=1] (2-job committed route).
  TWRoute tw = env.build_route({0, 1});
  bool valid = env.check_insertion(tw, {2}, 1);

  bool ok = valid;
  char ev[128] = {};
  if (ok) {
    TWRoute tw2 = env.build_route({0, 2, 1});
    auto result = tw2.realize_cap_compliant_schedule(env.input);
    ok = result.has_value();
    if (ok) {
      const Duration excess = check_schedule(env.input, tw2, result.value());
      const bool exact = (result.value()[0] == 0) &&
                         (result.value()[1] == 500) &&
                         (result.value()[2] == 1000);
      ok = (excess == 0) && exact;
      std::snprintf(ev,
                    sizeof(ev),
                    "valid=true S=[%lld,%lld,%lld] excess=%lld",
                    (long long)result.value()[0],
                    (long long)result.value()[1],
                    (long long)result.value()[2],
                    (long long)excess);
    } else {
      std::snprintf(ev, sizeof(ev), "valid=true but realize=nullopt");
      ok = false;
    }
  } else {
    std::snprintf(ev, sizeof(ev), "valid=false (unexpected)");
  }
  report("T8-13/seed-basic", ok, ev);
}

// ===========================================================================
// T8-14: first_rank=1, prefix pickup delayed by fixpoint.
//
// Prefix route: [pickup(loc=0), delivery(loc=2)] (cap=12s(1200)).
// Insert single(loc=1, TW=[20,∞](2000)) at first_rank=1.
// Virtual: [pickup, single, delivery].  travel(0,1)=5s, travel(1,2)=5s.
//
// Delivery in suffix → witness ineligible → engine called (seeded).
// Seeded ASAP: S[0]=0 (prefix), S[1]=max(500,2000)=2000, S[2]=2500.
// transit=2500-0=2500>1200, excess=1300.
// Fixpoint: delay pickup to S[0]=1300; re-sim gives S[1]=2000, S[2]=2500.
// transit=2500-1300=1200=cap → feasible.
// Expected: is_valid_addition_for_tw returns true.
// Verify: route [0,2,1] → realize gives S=[1300,2000,2500].
// ===========================================================================
static void test_t8_14() {
  TestEnv env(3);
  env.set_travel(0, 1, 5);
  env.set_travel(1, 2, 5);
  env.dur_mat[0][2] = 10;
  env.dur_mat[2][0] = 10;
  env.add_plain_vehicle();

  // jobs[0]=pickup(loc=0), jobs[1]=delivery(loc=2), cap=12s.
  Job pickup(1,
             JOB_TYPE::PICKUP,
             Location(0),
             0,
             0,
             Amount(0),
             Skills(),
             0,
             {TimeWindow()},
             "",
             {},
             {},
             UserDuration(12));
  Job delivery(2,
               JOB_TYPE::DELIVERY,
               Location(2),
               0,
               0,
               Amount(0),
               Skills(),
               0,
               {TimeWindow()},
               "",
               {},
               {},
               UserDuration(12));
  env.input.add_shipment(pickup, delivery);

  // jobs[2]=single(loc=1, TW=[20,∞])
  Job single(3,
             Location(1),
             0,
             0,
             Amount(0),
             Amount(0),
             Skills(),
             0,
             {TimeWindow(20, 1000000)});
  env.input.add_job(single);
  env.finalize_jobs();

  // Prefix route: [pickup=0, delivery=1].
  TWRoute tw = env.build_route({0, 1});
  bool valid = env.check_insertion(tw, {2}, 1);

  bool ok = valid;
  char ev[128] = {};
  if (ok) {
    TWRoute tw2 = env.build_route({0, 2, 1});
    auto result = tw2.realize_cap_compliant_schedule(env.input);
    ok = result.has_value();
    if (ok) {
      const Duration excess = check_schedule(env.input, tw2, result.value());
      const bool exact = (result.value()[0] == 1300) &&
                         (result.value()[1] == 2000) &&
                         (result.value()[2] == 2500);
      ok = (excess == 0) && exact;
      std::snprintf(ev,
                    sizeof(ev),
                    "valid=true S=[%lld,%lld,%lld] excess=%lld",
                    (long long)result.value()[0],
                    (long long)result.value()[1],
                    (long long)result.value()[2],
                    (long long)excess);
    } else {
      std::snprintf(ev, sizeof(ev), "valid=true but realize=nullopt");
      ok = false;
    }
  } else {
    std::snprintf(ev, sizeof(ev), "valid=false (unexpected)");
  }
  report("T8-14/seed-fixpoint", ok, ev);
}

// ===========================================================================
// T8-15: first_rank=1, break in trace (B_fst=0, no prefix break seeded).
//
// Prefix route: [pickup(loc=0), delivery(loc=1)] with break manually placed
// before delivery (breaks_at_rank={0,1,0}, breaks_counts={0,1,1}).
// Insert single(loc=2) at first_rank=1, last_rank=1.
// Virtual: [pickup, single, delivery].
// travel(0,1)=10s(1000), travel(0,2)=5s(500), travel(2,1)=5s(500).
// cap=25s(2500).  Break TW=[0,∞], service=0.
//
// B_fst=breaks_counts[1]-breaks_at_rank[1]=1-1=0: no prefix break seeded.
// Delivery in suffix → witness ineligible → engine called (seeded).
// Seeded: S[0]=0 (prefix), seeded forward from (dep=0, loc=0, rem=500).
// Break appears in the trace range; engine handles it normally.
// Transit ≤ 2500 → feasible.
// Expected: is_valid_addition_for_tw returns true.
// ===========================================================================
static void test_t8_15() {
  TestEnv env(3);
  env.set_travel(0, 1, 10);
  env.set_travel(0, 2, 5);
  env.set_travel(2, 1, 5);

  Break brk(1, {TimeWindow()}, 0); // TW=[0,∞], service=0
  env.add_vehicle_with_breaks({brk});

  // jobs[0]=pickup(loc=0), jobs[1]=delivery(loc=1), cap=25s.
  Job pickup(1,
             JOB_TYPE::PICKUP,
             Location(0),
             0,
             0,
             Amount(0),
             Skills(),
             0,
             {TimeWindow()},
             "",
             {},
             {},
             UserDuration(25));
  Job delivery(2,
               JOB_TYPE::DELIVERY,
               Location(1),
               0,
               0,
               Amount(0),
               Skills(),
               0,
               {TimeWindow()},
               "",
               {},
               {},
               UserDuration(25));
  env.input.add_shipment(pickup, delivery);

  // jobs[2]=single(loc=2)
  Job single(3,
             Location(2),
             0,
             0,
             Amount(0),
             Amount(0),
             Skills(),
             0,
             {TimeWindow()});
  env.input.add_job(single);
  env.finalize_jobs();

  // Build prefix route [pickup=0, delivery=1], then manually force break
  // before delivery (rank=1) as in T8-5a.
  TWRoute tw = env.build_route({0, 1});
  tw.breaks_at_rank = {0, 1, 0};
  tw.breaks_counts = {0, 1, 1};
  // B_fst=breaks_counts[1]-breaks_at_rank[1]=0: no prefix break seeded.

  bool valid = env.check_insertion(tw, {2}, 1);

  char ev[64];
  std::snprintf(ev,
                sizeof(ev),
                "valid=%s (no prefix break)",
                valid ? "true" : "false");
  report("T8-15/seed-noprefix-brk", valid, ev);
}

// ===========================================================================
// T8-16: first_rank=0 degeneracy — vehicle-start path unchanged.
//
// Empty prefix route.  Insert [pickup=0, delivery=1] at first_rank=0.
// Same fixture as T8-1a: travel=10s(1000), cap=15s(1500), delivery TW=[25,∞].
// Engine uses the else branch (first_rank==0): no seeding, start from v_start.
// Expected: is_valid_addition_for_tw returns true (engine finds S=[1000,2500]).
// ===========================================================================
static void test_t8_16() {
  TestEnv env(2);
  env.set_travel(0, 1, 10);
  env.add_plain_vehicle();

  Job pickup(1,
             JOB_TYPE::PICKUP,
             Location(0),
             0,
             0,
             Amount(0),
             Skills(),
             0,
             {TimeWindow()},
             "",
             {},
             {},
             UserDuration(15));
  Job delivery(2,
               JOB_TYPE::DELIVERY,
               Location(1),
               0,
               0,
               Amount(0),
               Skills(),
               0,
               {TimeWindow(25, 1000000)},
               "",
               {},
               {},
               UserDuration(15));
  env.input.add_shipment(pickup, delivery);
  env.finalize_jobs();

  // Empty route: no committed jobs.
  TWRoute tw(env.input, 0, 0);
  bool valid = env.check_insertion(tw, {0, 1}, 0);

  bool ok = valid;
  char ev[64];
  std::snprintf(ev,
                sizeof(ev),
                "valid=%s (first_rank=0)",
                valid ? "true" : "false");
  report("T8-16/seed-fr0", ok, ev);
}

// ===========================================================================
// T8-17: first_rank=2, B_fst=1 — prefix break seeded from break_earliest.
//        Tests amendment A1: explicit harness coverage of B_fst > 0 path.
//
// Committed route: [pickup(loc=0), single_A(loc=2), delivery(loc=1)].
// Break manually placed before single_A (rank=1):
//   breaks_at_rank={0,1,0,0}, breaks_counts={0,1,1,1}, break_earliest[0]=0.
// B_fst=breaks_counts[2]-breaks_at_rank[2]=1-0=1 > 0.
// Insert single_B(loc=3) at first_rank=2, last_rank=2.
// Virtual: [pickup, single_A, single_B, delivery].
// travel(0,2)=5s(500), travel(2,1)=5s(500), travel(2,3)=5s(500),
//            travel(3,1)=5s(500).  cap=50s(5000).  Break TW=[0,∞], service=0.
//
// Delivery in suffix → witness ineligible → engine called (seeded).
// Seeded: break_S[0]=0 (from break_earliest[0]), S[0]=0, S[1]=500 (from
// prefix). Forward from pos=2: S[2]=1000(single_B), S[3]=1500(delivery).
// transit=1500-0=1500 ≤ 5000 → feasible.
// Expected: is_valid_addition_for_tw returns true.
// Verify: route [0,2,3,1] → realize gives S=[0,500,1000,1500].
// ===========================================================================
static void test_t8_17() {
  TestEnv env(4);
  env.set_travel(0, 2, 5); // pickup → single_A
  env.set_travel(2, 1, 5); // single_A → delivery
  env.set_travel(2, 3, 5); // single_A → single_B
  env.set_travel(3, 1, 5); // single_B → delivery
  env.dur_mat[0][1] = 10;  // pickup → delivery (for committed-route build)
  env.dur_mat[1][0] = 10;

  Break brk(1, {TimeWindow()}, 0); // TW=[0,∞], service=0
  env.add_vehicle_with_breaks({brk});

  // jobs[0]=pickup(loc=0), jobs[1]=delivery(loc=1), cap=50s.
  Job pickup(1,
             JOB_TYPE::PICKUP,
             Location(0),
             0,
             0,
             Amount(0),
             Skills(),
             0,
             {TimeWindow()},
             "",
             {},
             {},
             UserDuration(50));
  Job delivery(2,
               JOB_TYPE::DELIVERY,
               Location(1),
               0,
               0,
               Amount(0),
               Skills(),
               0,
               {TimeWindow()},
               "",
               {},
               {},
               UserDuration(50));
  env.input.add_shipment(pickup, delivery);

  // jobs[2]=single_A(loc=2), jobs[3]=single_B(loc=3)
  Job single_A(3,
               Location(2),
               0,
               0,
               Amount(0),
               Amount(0),
               Skills(),
               0,
               {TimeWindow()});
  Job single_B(4,
               Location(3),
               0,
               0,
               Amount(0),
               Amount(0),
               Skills(),
               0,
               {TimeWindow()});
  env.input.add_job(single_A);
  env.input.add_job(single_B);
  env.finalize_jobs();

  // Build 3-job committed route [pickup=0, single_A=2, delivery=1].
  TWRoute tw = env.build_route({0, 2, 1});

  // Manually place break before single_A (rank=1) in the committed route.
  // breaks_at_rank size = route.size()+1 = 4.
  tw.breaks_at_rank = {0, 1, 0, 0};
  tw.breaks_counts = {0, 1, 1, 1};
  // break_earliest[0]=0: with break TW=[0,∞] and pickup dep=0, the break
  // fires immediately at time 0 before any travel (same result as
  // fwd_update_earliest_from would compute for this placement).
  tw.break_earliest[0] = 0;

  // Verify sanity: B_fst > 0 so the prefix-break seeding path is exercised.
  const Index B_fst = tw.breaks_counts[2] - tw.breaks_at_rank[2];
  assert(B_fst > 0 && "T8-17 sanity: B_fst must be > 0 for this test");

  // Insert single_B=jobs[3] at first_rank=2 on the 3-job prefix.
  bool valid = env.check_insertion(tw, {3}, 2);

  bool ok = valid;
  char ev[128] = {};
  if (ok) {
    // Verify: commit full route and check realize.
    // Note: realize uses the whole-route overload (no seeding), so it
    // independently confirms the schedule.
    TWRoute tw2 = env.build_route({0, 2, 3, 1});
    auto result = tw2.realize_cap_compliant_schedule(env.input);
    ok = result.has_value();
    if (ok) {
      const Duration excess = check_schedule(env.input, tw2, result.value());
      const bool exact =
        (result.value()[0] == 0) && (result.value()[1] == 500) &&
        (result.value()[2] == 1000) && (result.value()[3] == 1500);
      ok = (excess == 0) && exact;
      std::snprintf(ev,
                    sizeof(ev),
                    "valid=true B_fst=%u S=[%lld,%lld,%lld,%lld] excess=%lld",
                    (unsigned)B_fst,
                    (long long)result.value()[0],
                    (long long)result.value()[1],
                    (long long)result.value()[2],
                    (long long)result.value()[3],
                    (long long)excess);
    } else {
      std::snprintf(ev, sizeof(ev), "valid=true but realize=nullopt");
      ok = false;
    }
  } else {
    std::snprintf(ev, sizeof(ev), "valid=false (unexpected)");
  }
  report("T8-17/seed-prefix-brk", ok, ev);
}

// ===========================================================================
// T8-18: tw_idx seeded at index > 0.
//
// Prefix pickup has two time windows: TW0=[0s,3s], TW1=[10s,∞].
// Vehicle arrives at pickup after TW0.end, so ASAP selects TW1 and waits
// until 1000.  Seeding must reproduce found_ti=1 (tw_idx[0]=1).
// Delivery TW=[20s,∞] forces ASAP transit >> cap; fixpoint shifts pickup
// within TW1.  Exercises: tw_idx>0 seeding + prefix pickup fixpoint (F7a).
//
// 3 locs (0=vehicle, 1=pickup, 2=delivery).
// travel(0,1)=4s(400), travel(1,2)=5s(500).  cap=6s(600).
// Prefix [pickup]; first_rank=1.  Insert delivery at rank 1.
// Seeded: S[0]=1000 (TW1), tw_idx[0]=1.  ASAP: S[1]=2000.
// transit=1000>600.  Fixpoint: pickup→1400.  Re-sim: S[1]=2000.
// transit=600=cap. Expected: check_insertion true; realize [P,D] →
// S=[1400,2000].
// ===========================================================================
static void test_t8_18() {
  TestEnv env(3);
  env.set_travel(0, 1, 4);
  env.set_travel(1, 2, 5);
  env.dur_mat[0][2] = 9;
  env.dur_mat[2][0] = 9;
  env.add_plain_vehicle();

  Job pickup(1,
             JOB_TYPE::PICKUP,
             Location(1),
             0,
             0,
             Amount(0),
             Skills(),
             0,
             {TimeWindow(0, 3), TimeWindow(10, 1000000)},
             "",
             {},
             {},
             UserDuration(6));
  Job delivery(2,
               JOB_TYPE::DELIVERY,
               Location(2),
               0,
               0,
               Amount(0),
               Skills(),
               0,
               {TimeWindow(20, 1000000)},
               "",
               {},
               {},
               UserDuration(6));
  env.input.add_shipment(pickup, delivery);
  env.finalize_jobs();

  // Prefix route: [pickup=jobs[0]].  earliest[0]=1000 (waited for TW1).
  TWRoute tw = env.build_route({0});
  bool valid = env.check_insertion(tw, {1}, 1);

  bool ok = valid;
  char ev[128] = {};
  if (ok) {
    TWRoute tw2 = env.build_route({0, 1});
    auto result = tw2.realize_cap_compliant_schedule(env.input);
    ok = result.has_value();
    if (ok) {
      const Duration excess = check_schedule(env.input, tw2, result.value());
      const bool exact =
        (result.value()[0] == 1400) && (result.value()[1] == 2000);
      ok = (excess == 0) && exact;
      std::snprintf(ev,
                    sizeof(ev),
                    "valid=true tw_idx1 S=[%lld,%lld] excess=%lld",
                    (long long)result.value()[0],
                    (long long)result.value()[1],
                    (long long)excess);
    } else {
      std::snprintf(ev, sizeof(ev), "valid=true but realize=nullopt");
      ok = false;
    }
  } else {
    std::snprintf(ev, sizeof(ev), "valid=false (unexpected)");
  }
  report("T8-18/seed-tw-idx1", ok, ev);
}

// ===========================================================================
// T8-19: first_rank == virt_N — pure tail removal.
//
// Route=[pickup(0), delivery(1), single(2)].  Remove single at rank 2.
// is_valid_removal(2,1): first_rank=2, last_rank=3, virt_N=2=first_rank.
// Seeding block populates prefix [P,D]; cap_asap_forward is skipped
// (first_rank == virt_N guard).  Transit for pair < cap → feasible.
// Expected: true.
// ===========================================================================
static void test_t8_19() {
  TestEnv env(3);
  env.set_travel(0, 1, 5);
  env.set_travel(1, 2, 5);
  env.dur_mat[0][2] = 10;
  env.dur_mat[2][0] = 10;
  env.add_plain_vehicle();

  Job pickup(1,
             JOB_TYPE::PICKUP,
             Location(0),
             0,
             0,
             Amount(0),
             Skills(),
             0,
             {TimeWindow()},
             "",
             {},
             {},
             UserDuration(15));
  Job delivery(2,
               JOB_TYPE::DELIVERY,
               Location(1),
               0,
               0,
               Amount(0),
               Skills(),
               0,
               {TimeWindow()},
               "",
               {},
               {},
               UserDuration(15));
  env.input.add_shipment(pickup, delivery);

  Job single(3,
             Location(2),
             0,
             0,
             Amount(0),
             Amount(0),
             Skills(),
             0,
             {TimeWindow()});
  env.input.add_job(single);
  env.finalize_jobs();

  TWRoute tw = env.build_route({0, 1, 2});
  // is_valid_removal(2,1): first_rank=2=virt_N.  cap_asap_forward skipped.
  bool valid = tw.is_valid_removal(env.input, 2, 1);

  char ev[128] = {};
  std::snprintf(ev,
                sizeof(ev),
                "valid=%s (first_rank==virt_N=2, fwd-pass skipped)",
                valid ? "true" : "false");
  report("T8-19/seed-frN=virtN", valid, ev);
}

// ===========================================================================
// T8-20: P and D both in committed prefix; fixpoint rewrites D in prefix.
//
// Route=[pickup(0), delivery(1)].  Insert single(2) at first_rank=2.
// Delivery TW=[20s,∞] forces ASAP transit=2000>>cap=600.
// Seeded prefix: S=[0,2000].  Cap violated.  Fixpoint shifts pickup (pos 0):
// calls cap_asap_forward(1,...) which overwrites S[1] (delivery, in prefix).
// Expected: check_insertion true; realize [P,D,single] → S=[1400,2000,2500].
//
// 3 locs (0=vehicle/pickup, 1=delivery, 2=single).
// travel(0,1)=5s(500), travel(1,2)=5s(500).  cap=6s(600).
// ===========================================================================
static void test_t8_20() {
  TestEnv env(3);
  env.set_travel(0, 1, 5);
  env.set_travel(1, 2, 5);
  env.dur_mat[0][2] = 10;
  env.dur_mat[2][0] = 10;
  env.add_plain_vehicle();

  Job pickup(1,
             JOB_TYPE::PICKUP,
             Location(0),
             0,
             0,
             Amount(0),
             Skills(),
             0,
             {TimeWindow()},
             "",
             {},
             {},
             UserDuration(6));
  Job delivery(2,
               JOB_TYPE::DELIVERY,
               Location(1),
               0,
               0,
               Amount(0),
               Skills(),
               0,
               {TimeWindow(20, 1000000)},
               "",
               {},
               {},
               UserDuration(6));
  env.input.add_shipment(pickup, delivery);

  Job single(3,
             Location(2),
             0,
             0,
             Amount(0),
             Amount(0),
             Skills(),
             0,
             {TimeWindow()});
  env.input.add_job(single);
  env.finalize_jobs();

  // Committed [P,D]: ASAP S=[0,2000], transit=2000>600 (delivery TW forces
  // wait).
  TWRoute tw = env.build_route({0, 1});
  // Insert single at first_rank=2; P and D both in seeded prefix.
  // Fixpoint shifts P and rewrites D (overwrite of seeded prefix position 1).
  bool valid = env.check_insertion(tw, {2}, 2);

  bool ok = valid;
  char ev[128] = {};
  if (ok) {
    TWRoute tw2 = env.build_route({0, 1, 2});
    auto result = tw2.realize_cap_compliant_schedule(env.input);
    ok = result.has_value();
    if (ok) {
      const Duration excess = check_schedule(env.input, tw2, result.value());
      const bool exact = (result.value()[0] == 1400) &&
                         (result.value()[1] == 2000) &&
                         (result.value()[2] == 2500);
      ok = (excess == 0) && exact;
      std::snprintf(ev,
                    sizeof(ev),
                    "valid=true P+D-prefix S=[%lld,%lld,%lld] excess=%lld",
                    (long long)result.value()[0],
                    (long long)result.value()[1],
                    (long long)result.value()[2],
                    (long long)excess);
    } else {
      std::snprintf(ev, sizeof(ev), "valid=true but realize=nullopt");
      ok = false;
    }
  } else {
    std::snprintf(ev, sizeof(ev), "valid=false (unexpected)");
  }
  report("T8-20/seed-prefix-pd", ok, ev);
}

// ===========================================================================
// T8-21: same-location seed boundary (setup suppression across the seam).
//
// Committed route: [pickup(loc=0, service=2s), delivery(loc=1)], cap=15s.
// Insert single(loc=0, setup=3s, service=1s) at first_rank=1.
// Virtual: [pickup(loc=0), single(loc=0), delivery(loc=1)]. travel(0,1)=5s.
//
// The seeded pass departs the prefix at loc=0 and the first candidate job is
// at the SAME location, so its setup must be suppressed: action=service=100.
// Seeded ASAP: dep[0]=200, S[1]=200, dep[1]=300, S[2]=800.
// transit = 800 - 200 = 600 <= 1500 → feasible.
// If suppression were lost across the seam (action=400), S[2]=1100 and the
// exact-match check below fails.
// Expected: is_valid_addition_for_tw returns true.
// Verify: route [0,2,1] → realize gives S=[0,200,800], excess=0.
// ===========================================================================
static void test_t8_21() {
  TestEnv env(2);
  env.set_travel(0, 1, 5);
  env.add_plain_vehicle();

  // jobs[0]=pickup(loc=0, service=2s), jobs[1]=delivery(loc=1), cap=15s.
  Job pickup(1,
             JOB_TYPE::PICKUP,
             Location(0),
             0,
             2,
             Amount(0),
             Skills(),
             0,
             {TimeWindow()},
             "",
             {},
             {},
             UserDuration(15));
  Job delivery(2,
               JOB_TYPE::DELIVERY,
               Location(1),
               0,
               0,
               Amount(0),
               Skills(),
               0,
               {TimeWindow()},
               "",
               {},
               {},
               UserDuration(15));
  env.input.add_shipment(pickup, delivery);

  // jobs[2]=single(loc=0, setup=3s, service=1s).
  Job single(3,
             Location(0),
             3,
             1,
             Amount(0),
             Amount(0),
             Skills(),
             0,
             {TimeWindow()});
  env.input.add_job(single);
  env.finalize_jobs();

  // Committed route: [pickup=0, delivery=1]; insert single at first_rank=1.
  TWRoute tw = env.build_route({0, 1});
  bool valid = env.check_insertion(tw, {2}, 1);

  bool ok = valid;
  char ev[128] = {};
  if (ok) {
    TWRoute tw2 = env.build_route({0, 2, 1});
    auto result = tw2.realize_cap_compliant_schedule(env.input);
    ok = result.has_value();
    if (ok) {
      const Duration excess = check_schedule(env.input, tw2, result.value());
      const bool exact = (result.value()[0] == 0) &&
                         (result.value()[1] == 200) &&
                         (result.value()[2] == 800);
      ok = (excess == 0) && exact;
      std::snprintf(ev,
                    sizeof(ev),
                    "valid=true same-loc-seam S=[%lld,%lld,%lld] excess=%lld",
                    (long long)result.value()[0],
                    (long long)result.value()[1],
                    (long long)result.value()[2],
                    (long long)excess);
    } else {
      std::snprintf(ev, sizeof(ev), "valid=true but realize=nullopt");
      ok = false;
    }
  } else {
    std::snprintf(ev, sizeof(ev), "valid=false (unexpected)");
  }
  report("T8-21/seed-same-loc", ok, ev);
}

// ===========================================================================
// T8-22: terminal break forces rejection.
//
// Route: [pickup(loc=0), delivery(loc=0)].  1 terminal break
// (breaks_at_rank[2]=1). travel(0,0)=0.  delivery TW=[100,100]s (forces
// S_D=10000).  cap=90s (9000). Fixpoint delays pickup to S_P=10s(1000),
// transit=9000=cap, compliant on the pair. Terminal break TW=[0,5]s=[0,500]:
// cur_dep after delivery=10000 > 500, so the break can no longer fit and no
// compliant schedule exists. The engine must process the terminal break slot
// (breaks_at_rank[N]) and reject.
// ===========================================================================
static void test_t8_22() {
  TestEnv env(1);
  env.set_travel(0, 0, 0);

  // Terminal break TW ends at 5s=500: expires before the delayed delivery.
  Break brk(1, {TimeWindow(0, 5)}, 0 /*service=0*/);
  env.add_vehicle_with_breaks({brk});

  Job pickup(1,
             JOB_TYPE::PICKUP,
             Location(0),
             0,
             0,
             Amount(0),
             Skills(),
             0,
             {TimeWindow()},
             "",
             {},
             {},
             UserDuration(90));
  Job delivery(2,
               JOB_TYPE::DELIVERY,
               Location(0),
               0,
               0,
               Amount(0),
               Skills(),
               0,
               {TimeWindow(100, 100)},
               "",
               {},
               {},
               UserDuration(90));
  env.input.add_shipment(pickup, delivery);
  env.finalize_jobs();

  TWRoute tw = env.build_route({0, 1});
  // Place the 1 break as a terminal break (after both jobs).
  tw.breaks_at_rank = {0, 0, 1};
  tw.breaks_counts = {0, 0, 1};

  auto result = tw.realize_cap_compliant_schedule(env.input);

  bool ok = !result.has_value();
  char ev[96];
  std::snprintf(ev,
                sizeof(ev),
                "nullopt=%s (terminal break TW expired after fixpoint delay)",
                ok ? "yes" : "no");
  report("T8-22/term-brk-reject", ok, ev);
}

// ===========================================================================
// T8-23: same layout, terminal break TW loose → engine accepts and schedules.
//
// Same route and cap as T8-22.  Terminal break TW=[100,∞]s=[10000,MAX],
// service=0 (avoids TW conflict during build_route interleaving).
// After fixpoint: S_P=10s(1000), S_D=100s(10000), dep_D=10000, break fits
// at break_S=10000 (TW start), absorbed by rem=0 (no vehicle end).
// Expected: schedule S=[1000, 10000], result has_value.
// ===========================================================================
static void test_t8_23() {
  TestEnv env(1);
  env.set_travel(0, 0, 0);

  // Terminal break TW opens at 100s=10000: fits exactly when delivery ends.
  // Service=0 keeps timing consistent during the initial build_route call.
  Break brk(1, {TimeWindow(100, 1000000)}, 0 /*service=0*/);
  env.add_vehicle_with_breaks({brk});

  Job pickup(1,
             JOB_TYPE::PICKUP,
             Location(0),
             0,
             0,
             Amount(0),
             Skills(),
             0,
             {TimeWindow()},
             "",
             {},
             {},
             UserDuration(90));
  Job delivery(2,
               JOB_TYPE::DELIVERY,
               Location(0),
               0,
               0,
               Amount(0),
               Skills(),
               0,
               {TimeWindow(100, 100)},
               "",
               {},
               {},
               UserDuration(90));
  env.input.add_shipment(pickup, delivery);
  env.finalize_jobs();

  TWRoute tw = env.build_route({0, 1});
  tw.breaks_at_rank = {0, 0, 1};
  tw.breaks_counts = {0, 0, 1};

  auto result = tw.realize_cap_compliant_schedule(env.input);

  bool ok = result.has_value();
  char ev[160] = {};
  if (ok) {
    const bool pickup_ok = (result.value()[0] == 1000);
    const bool delivery_ok = (result.value()[1] == 10000);
    ok = pickup_ok && delivery_ok;
    std::snprintf(ev,
                  sizeof(ev),
                  "S=[%lld,%lld] pickup_ok=%s delivery_ok=%s",
                  (long long)result.value()[0],
                  (long long)result.value()[1],
                  pickup_ok ? "yes" : "no",
                  delivery_ok ? "yes" : "no");
  } else {
    std::snprintf(ev, sizeof(ev), "nullopt (unexpected)");
  }
  report("T8-23/term-brk-loose", ok, ev);
}

// ===========================================================================
// T8-24: multi-TW window-gap interaction (regression pin).
//
// Route P0(loc0) P1(loc1) single(loc2) D1(loc3) D0(loc4), legs 10s.
// P0 windows [0,3],[20,61], cap 94s; D0 window [57,135].
// P1 windows [0,44],[76,98], cap 25s; D1 window [65,119]. single [100,inf).
// Fixing pair 1 requires a pickup delay to 85s, which falls in the gap
// between its windows, while every re-simulation triggered by fixing pair 0
// resets pair 1's pickup to its first window. The engine must carry the
// required delay across the window jump (start at max(needed, window start),
// not the window start) to converge within its iteration budget; the
// compliant schedule is S = [26, 85, 100, 110, 120]s (transits 94 and 25,
// both exactly at cap). Found by differential testing against an exhaustive
// oracle (multi_tw_oracle.cpp: 0 misses, 0 unsound in 20000 configurations).
// ===========================================================================
static void test_t8_24() {
  TestEnv env(5);
  for (unsigned a = 0; a < 5; ++a) {
    for (unsigned b = a + 1; b < 5; ++b) {
      env.set_travel(a, b, 10 * (b - a));
    }
  }
  env.add_plain_vehicle();

  Job p0(1, JOB_TYPE::PICKUP, Location(0), 0, 0, Amount(0), Skills(), 0,
         {TimeWindow(0, 3), TimeWindow(20, 61)}, "", {}, {},
         UserDuration(94));
  Job d0(2, JOB_TYPE::DELIVERY, Location(4), 0, 0, Amount(0), Skills(), 0,
         {TimeWindow(57, 135)}, "", {}, {}, UserDuration(94));
  env.input.add_shipment(p0, d0);
  Job p1(3, JOB_TYPE::PICKUP, Location(1), 0, 0, Amount(0), Skills(), 0,
         {TimeWindow(0, 44), TimeWindow(76, 98)}, "", {}, {},
         UserDuration(25));
  Job d1(4, JOB_TYPE::DELIVERY, Location(3), 0, 0, Amount(0), Skills(), 0,
         {TimeWindow(65, 119)}, "", {}, {}, UserDuration(25));
  env.input.add_shipment(p1, d1);
  Job single(5, Location(2), 0, 0, Amount(0), Amount(0), Skills(), 0,
             {TimeWindow(100, 1000000)});
  env.input.add_job(single);
  env.finalize_jobs();

  TWRoute tw = env.build_route({0, 2, 4, 3, 1});
  const auto result = tw.realize_cap_compliant_schedule(env.input);
  bool ok = result.has_value();
  char ev[128] = {};
  if (ok) {
    const Duration excess = check_schedule(env.input, tw, result.value());
    const bool exact = (result.value()[0] == 2600) &&
                       (result.value()[1] == 8500) &&
                       (result.value()[2] == 10000) &&
                       (result.value()[3] == 11000) &&
                       (result.value()[4] == 12000);
    ok = (excess == 0) && exact;
    std::snprintf(ev,
                  sizeof(ev),
                  "S=[%lld,%lld,%lld,%lld,%lld] excess=%lld",
                  (long long)result.value()[0],
                  (long long)result.value()[1],
                  (long long)result.value()[2],
                  (long long)result.value()[3],
                  (long long)result.value()[4],
                  (long long)(ok ? 0 : -1));
  } else {
    std::snprintf(ev, sizeof(ev), "nullopt (window-gap delay not carried)");
  }
  report("T8-24/multi-tw-gap", ok, ev);
}

// ===========================================================================
// T8-25: witness must not certify routes with ASAP-violating prefix pairs.
//
// Committed route [P0(loc0), D0(loc1)], legs 10s. P0 windows [0,3],[95,inf),
// cap 20s. D0 window [100,inf). The committed route is compliant only via a
// delayed schedule (P0=95, D0=105, transit 10); its ASAP schedule (P0=0,
// D0=100) violates the cap. Insert single J(loc2), window [110,112], at the
// route end. Under candidate ASAP, J fits (arrival 110), and the pair is
// prefix-internal — but every cap-compliant schedule needs P0 >= 95, which
// pushes J's arrival to 115 > 112. The route is infeasible; the witness must
// detect the prefix pair's ASAP violation and fall through to the engine,
// which rejects. Also verifies the committed route itself still realizes.
// ===========================================================================
static void test_t8_25() {
  TestEnv env(3);
  env.set_travel(0, 1, 10);
  env.set_travel(1, 2, 10);
  env.set_travel(0, 2, 20);
  env.add_plain_vehicle();

  Job p0(1, JOB_TYPE::PICKUP, Location(0), 0, 0, Amount(0), Skills(), 0,
         {TimeWindow(0, 3), TimeWindow(95, 10000)}, "", {}, {},
         UserDuration(20));
  Job d0(2, JOB_TYPE::DELIVERY, Location(1), 0, 0, Amount(0), Skills(), 0,
         {TimeWindow(100, 10000)}, "", {}, {}, UserDuration(20));
  env.input.add_shipment(p0, d0);
  Job single(3, Location(2), 0, 0, Amount(0), Amount(0), Skills(), 0,
             {TimeWindow(110, 112)});
  env.input.add_job(single);
  env.finalize_jobs();

  TWRoute tw = env.build_route({0, 1});
  const auto committed = tw.realize_cap_compliant_schedule(env.input);
  bool ok = committed.has_value() && committed.value()[0] == 9500 &&
            committed.value()[1] == 10500;
  const bool valid = env.check_insertion(tw, {2}, 2);
  ok = ok && !valid;
  char ev[128] = {};
  std::snprintf(ev,
                sizeof(ev),
                "committed S=[%lld,%lld] insertion_valid=%s (expect false)",
                committed.has_value() ? (long long)committed.value()[0] : -1,
                committed.has_value() ? (long long)committed.value()[1] : -1,
                valid ? "true" : "false");
  report("T8-25/witness-prefix-pair", ok, ev);
}

// ===========================================================================
// T8-26: half-pair insertion probes on a constrained route.
//
// Insertion search evaluates a shipment's pickup alone before evaluating
// the full pair, so the validity entry point must treat a constrained
// pickup whose delivery is absent from the candidate as unconstrained (the
// pair cannot violate until its delivery exists). Committed route
// [P0(loc0), D0(loc1)], legs 10s, cap 100s (ASAP-compliant, transit 10).
// Probe A: pickup of a NEW shipment (loc2, open windows) alone at the route
// end: must be accepted. Probe B: the same pickup with a window ending
// before its arrival (arrival 20s, window [0,15]): must be rejected by the
// ordinary TW check, proving the probe still validates windows.
// ===========================================================================
static void test_t8_26() {
  TestEnv env(4);
  env.set_travel(0, 1, 10);
  env.set_travel(1, 2, 10);
  env.set_travel(0, 2, 20);
  env.set_travel(1, 3, 10);
  env.set_travel(2, 3, 10);
  env.set_travel(0, 3, 30);
  env.add_plain_vehicle();

  Job p0(1, JOB_TYPE::PICKUP, Location(0), 0, 0, Amount(0), Skills(), 0,
         {TimeWindow()}, "", {}, {}, UserDuration(100));
  Job d0(2, JOB_TYPE::DELIVERY, Location(1), 0, 0, Amount(0), Skills(), 0,
         {TimeWindow()}, "", {}, {}, UserDuration(100));
  env.input.add_shipment(p0, d0);
  Job p1_open(3, JOB_TYPE::PICKUP, Location(2), 0, 0, Amount(0), Skills(), 0,
              {TimeWindow()}, "", {}, {}, UserDuration(100));
  Job d1(4, JOB_TYPE::DELIVERY, Location(3), 0, 0, Amount(0), Skills(), 0,
         {TimeWindow()}, "", {}, {}, UserDuration(100));
  env.input.add_shipment(p1_open, d1);
  Job p2_tight(5, JOB_TYPE::PICKUP, Location(2), 0, 0, Amount(0), Skills(), 0,
               {TimeWindow(0, 15)}, "", {}, {}, UserDuration(100));
  Job d2(6, JOB_TYPE::DELIVERY, Location(3), 0, 0, Amount(0), Skills(), 0,
         {TimeWindow()}, "", {}, {}, UserDuration(100));
  env.input.add_shipment(p2_tight, d2);
  env.finalize_jobs();

  TWRoute tw = env.build_route({0, 1});
  const bool open_probe = env.check_insertion(tw, {2}, 2);
  const bool tight_probe = env.check_insertion(tw, {4}, 2);
  const bool ok = open_probe && !tight_probe;
  char ev[96] = {};
  std::snprintf(ev,
                sizeof(ev),
                "open_pickup_probe=%s (expect true) tight_tw_probe=%s "
                "(expect false)",
                open_probe ? "true" : "false",
                tight_probe ? "true" : "false");
  report("T8-26/half-pair-probe", ok, ev);
}

// ===========================================================================
int main() {
  std::printf("\n=== T8 Engine Test Suite ===\n\n");

  test_t8_1a();
  test_t8_1b();
  test_t8_2a();
  test_t8_2b();
  test_t8_3();
  test_t8_4();
  test_t8_5a();
  test_t8_5b();
  test_t8_6a();
  test_t8_6b();
  test_t8_7();
  test_t8_8();
  test_t8_9();
  test_t8_10();
  test_t8_11();
  test_t8_12();
  test_t8_13();
  test_t8_14();
  test_t8_15();
  test_t8_16();
  test_t8_17();
  test_t8_18();
  test_t8_19();
  test_t8_20();
  test_t8_21();
  test_t8_22();
  test_t8_23();
  test_t8_24();
  test_t8_25();
  test_t8_26();

  std::printf("\n=== Results: %d passed, %d failed ===\n\n", g_pass, g_fail);
  return (g_fail == 0) ? 0 : 1;
}
