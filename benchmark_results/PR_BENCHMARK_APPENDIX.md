# PR Benchmark Appendix: `timing_objective`

Date: March 20, 2026  
Branch image: `vroom-build` (`d82ab6b28853`)  
Baseline image: `vroom-master` (`d00dddc58c23`)

## Scope

Real-life benchmark coverage across 11 instances:

- VRPTW: Solomon (`C101`, `R101`, `RC101`), Homberger (`c1_2_1`, `c1_4_1`)
- PDPTW: Li&Lim (`lc101`, `LC1_2_1`, `LC1_4_1`)
- CVRP: CVRPLIB-X (`X-n101-k25`, `X-n219-k73`, `X-n491-k59`)

## Method

- 3 runs per case.
- `default` mode compared `vroom-master` vs `vroom-build`.
- Objective modes run on `vroom-build`:
  - `hierarchical_ms` for VRPTW/CVRP.
  - `hierarchical_ms`, `hierarchical_tt_ss_ms`, `weighted_3_2_1` for PDPTW.
- For Li&Lim objective modes using `target_time`, pickup `target_time` is set to the midpoint of the pickup TW.

Repro command:

```bash
BENCH_RUNS=3 ./scripts/run_real_benchmarks.py
```

## Default Regression Check (`master` vs `current`)

All tested instances keep identical route count and cost between `master` and `current`.

| Suite | Instance | Routes | Master solve ms | Current solve ms | Delta % |
|---|---:|---:|---:|---:|---:|
| VRPTW | C101 | 10 | 297.3 | 287.0 | -3.5% |
| VRPTW | R101 | 20 | 740.3 | 734.3 | -0.8% |
| VRPTW | RC101 | 16 | 753.7 | 728.3 | -3.4% |
| VRPTW | c1_2_1 | 20 | 936.7 | 920.7 | -1.7% |
| VRPTW | c1_4_1 | 40 | 3037.7 | 3460.7 | +13.9% |
| PDPTW | lc101 | 10 | 288.3 | 295.0 | +2.3% |
| PDPTW | LC1_2_1 | 20 | 692.7 | 711.0 | +2.6% |
| PDPTW | LC1_4_1 | 40 | 2461.0 | 2466.0 | +0.2% |
| CVRP | X-n101-k25 | 27 | 536.7 | 564.7 | +5.2% |
| CVRP | X-n219-k73 | 73 | 1896.3 | 1905.0 | +0.5% |
| CVRP | X-n491-k59 | 60 | 11355.7 | 10307.3 | -9.2% |

## Objective Overhead on Current Branch

| Suite | Instance | Mode | Avg solve ms | Delta vs current default % |
|---|---:|---|---:|---:|
| VRPTW | C101 | hierarchical_ms | 329.7 | +14.9% |
| VRPTW | R101 | hierarchical_ms | 786.7 | +7.1% |
| VRPTW | RC101 | hierarchical_ms | 724.7 | -0.5% |
| VRPTW | c1_2_1 | hierarchical_ms | 924.7 | +0.4% |
| VRPTW | c1_4_1 | hierarchical_ms | 3080.3 | -11.0% |
| PDPTW | lc101 | hierarchical_ms | 306.0 | +3.7% |
| PDPTW | lc101 | hierarchical_tt_ss_ms | 456.3 | +54.7% |
| PDPTW | lc101 | weighted_3_2_1 | 466.0 | +58.0% |
| PDPTW | LC1_2_1 | hierarchical_ms | 669.7 | -5.8% |
| PDPTW | LC1_2_1 | hierarchical_tt_ss_ms | 866.3 | +21.8% |
| PDPTW | LC1_2_1 | weighted_3_2_1 | 863.0 | +21.4% |
| PDPTW | LC1_4_1 | hierarchical_ms | 2366.7 | -4.0% |
| PDPTW | LC1_4_1 | hierarchical_tt_ss_ms | 2489.0 | +0.9% |
| PDPTW | LC1_4_1 | weighted_3_2_1 | 2545.0 | +3.2% |
| CVRP | X-n101-k25 | hierarchical_ms | 495.0 | -12.3% |
| CVRP | X-n219-k73 | hierarchical_ms | 2097.0 | +10.1% |
| CVRP | X-n491-k59 | hierarchical_ms | 9733.3 | -5.6% |

## Variance Note (Largest Instance)

`X-n491-k59` shows high run-to-run variance in 3-run samples. A focused 8x interleaved check (`master/current/master/current/...`) gave:

- `master`: 14571.5 ms
- `current`: 14622.2 ms
- delta: +0.3%

Interpretation: no stable large regression signal on this case; larger sample counts are recommended for strict perf claims on largest instances.
