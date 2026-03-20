# Real-Life Timing Objective Benchmark Report

Generated: 2026-03-20 09:31:02 
Runs per case: 3
Master image: `vroom-master` (d00dddc58c23)
Current image: `vroom-build` (d82ab6b28853)

## Key Findings

- Default-mode solution quality is unchanged across all tested real instances (costs and route counts match `master` on every case).
- Real-life coverage spans 11 instances across Solomon, Homberger, Li&Lim, and CVRPLIB-X with route counts from 10 to 73.
- For Li&Lim objective modes, full multi-objective configs (`hierarchical_tt_ss_ms`, `weighted_3_2_1`) add measurable solve overhead versus current default (about +1% to +58% depending on size/instance).
- `hierarchical_ms` overhead is generally small versus current default, but per-run variance is non-trivial on larger instances.

## Instance Matrix

| Suite | Family | Instance | Vehicles | Jobs | Shipments | Matrix |
|---|---|---:|---:|---:|---:|---:|
| CVRP | CVRPLIB-X | X-n101-k25 | 27 | 100 | 0 | 101 |
| CVRP | CVRPLIB-X | X-n219-k73 | 78 | 218 | 0 | 219 |
| CVRP | CVRPLIB-X | X-n491-k59 | 63 | 490 | 0 | 491 |
| PDPTW | Li&Lim | LC1_2_1 | 50 | 0 | 106 | 213 |
| PDPTW | Li&Lim | LC1_4_1 | 100 | 0 | 211 | 423 |
| PDPTW | Li&Lim | lc101 | 25 | 0 | 53 | 107 |
| VRPTW | Homberger | c1_2_1 | 50 | 200 | 0 | 201 |
| VRPTW | Homberger | c1_4_1 | 100 | 400 | 0 | 401 |
| VRPTW | Solomon | C101 | 25 | 100 | 0 | 101 |
| VRPTW | Solomon | R101 | 25 | 100 | 0 | 101 |
| VRPTW | Solomon | RC101 | 25 | 100 | 0 | 101 |

## Regression Check (Default Mode, Master vs Current)

| Suite | Instance | Routes | Master solve ms | Current solve ms | Delta % | Master cost | Current cost |
|---|---:|---:|---:|---:|---:|---:|---:|
| VRPTW | C101 | 10 | 297.3 | 287.0 | -3.5% | 828937 | 828937 |
| VRPTW | R101 | 20 | 740.3 | 734.3 | -0.8% | 1657132 | 1657132 |
| VRPTW | RC101 | 16 | 753.7 | 728.3 | -3.4% | 1661027 | 1661027 |
| VRPTW | c1_2_1 | 20 | 936.7 | 920.7 | -1.7% | 2704559 | 2704559 |
| VRPTW | c1_4_1 | 40 | 3037.7 | 3460.7 | 13.9% | 7152040 | 7152040 |
| PDPTW | lc101 | 10 | 288.3 | 295.0 | 2.3% | 828937 | 828937 |
| PDPTW | LC1_2_1 | 20 | 692.7 | 711.0 | 2.6% | 2704559 | 2704559 |
| PDPTW | LC1_4_1 | 40 | 2461.0 | 2466.0 | 0.2% | 7152040 | 7152040 |
| CVRP | X-n101-k25 | 27 | 536.7 | 564.7 | 5.2% | 28272 | 28272 |
| CVRP | X-n219-k73 | 73 | 1896.3 | 1905.0 | 0.5% | 117975 | 117975 |
| CVRP | X-n491-k59 | 60 | 11355.7 | 10307.3 | -9.2% | 68162 | 68162 |

## Objective Modes (Current Image)

| Suite | Instance | Mode | Avg solve ms | Delta vs current default % | Avg routes | Avg cost |
|---|---:|---|---:|---:|---:|---:|
| VRPTW | C101 | hierarchical_ms | 329.7 | 14.9% | 10 | 828937 |
| VRPTW | R101 | hierarchical_ms | 786.7 | 7.1% | 20 | 1657132 |
| VRPTW | RC101 | hierarchical_ms | 724.7 | -0.5% | 16 | 1661027 |
| VRPTW | c1_2_1 | hierarchical_ms | 924.7 | 0.4% | 20 | 2704559 |
| VRPTW | c1_4_1 | hierarchical_ms | 3080.3 | -11.0% | 40 | 7152040 |
| PDPTW | lc101 | hierarchical_ms | 306.0 | 3.7% | 10 | 828937 |
| PDPTW | lc101 | hierarchical_tt_ss_ms | 456.3 | 54.7% | 10 | 828937 |
| PDPTW | lc101 | weighted_3_2_1 | 466.0 | 58.0% | 10 | 828937 |
| PDPTW | LC1_2_1 | hierarchical_ms | 669.7 | -5.8% | 20 | 2704559 |
| PDPTW | LC1_2_1 | hierarchical_tt_ss_ms | 866.3 | 21.8% | 20 | 2704559 |
| PDPTW | LC1_2_1 | weighted_3_2_1 | 863.0 | 21.4% | 20 | 2704559 |
| PDPTW | LC1_4_1 | hierarchical_ms | 2366.7 | -4.0% | 40 | 7152040 |
| PDPTW | LC1_4_1 | hierarchical_tt_ss_ms | 2489.0 | 0.9% | 40 | 7152040 |
| PDPTW | LC1_4_1 | weighted_3_2_1 | 2545.0 | 3.2% | 40 | 7152040 |
| CVRP | X-n101-k25 | hierarchical_ms | 495.0 | -12.3% | 27 | 28272 |
| CVRP | X-n219-k73 | hierarchical_ms | 2097.0 | 10.1% | 73 | 117975 |
| CVRP | X-n491-k59 | hierarchical_ms | 9733.3 | -5.6% | 60 | 68162 |

## Focused Validation: X-n491-k59

The 3-run matrix above showed high variance on the largest CVRP case. To confirm there is no hidden default-mode regression, I ran an additional interleaved benchmark:

- Input: `benchmark_real_inputs/CVRP_X-n491-k59_default.json`
- Protocol: 8 alternating runs per image (`master`, `current`, repeat)
- Raw data: `benchmark_results/xn491_interleaved.csv`
- Result: `master` 14571.5 ms vs `current` 14622.2 ms (`+0.3%`)

This indicates the large swing seen in small samples is run-to-run noise rather than a stable regression signal.

## Notes

- `default` rows compare branch behavior against clean `master` using identical real benchmark inputs.
- Objective rows are current-branch only because `timing_objective` is the feature under test.
- For Li&Lim objective modes with `target_time`, pickup `target_time` was set to the midpoint of each pickup time window.
- For largest cases, use larger samples (or CPU-pinned CI runners) before drawing hard conclusions from sub-5-run differences.
