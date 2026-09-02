# Test and verification assets

This branch carries the test suite, differential oracles and benchmark
harness for the `max_transit_time` and `service_within` work, on top of
the source branch they verify (`feature/service-within`, itself stacked on
`feature/max-transit-time`). They live on their own branch so the feature
branches stay reviewable as source-only changes.

Everything here was rerun against this branch's source: feature suite
47/47, engine harness 31/31, and every oracle at 0 unsound / 0 missed.

## Feature suite

```
tests/max_transit_time/run.sh          # 47 checks, needs bin/vroom built with libglpk
tests/max_transit_time/check_caps.py   # cap property checker (both keys)
```

T1 input plumbing, T2 solve-mode enforcement, T3 operator coverage, T4
plan-mode reporting, T5 scheduler edge cases, T6 plan-mode
`service_within`. Plan-mode checks are skipped automatically on a build
without libglpk.

## Differential oracles

Brute-force comparisons behind the exactness claims. Build each against a
static `libvroom.a`:

```
make -C src -j8                        # produces lib/libvroom.a
g++ -std=c++20 -O2 -I src -DASIO_STANDALONE -DUSE_ROUTING=true \
    -D USE_LIBGLPK=true .t8_harness/<file>.cpp \
    -L lib -lvroom -lglpk -lpthread -lssl -lcrypto -o <out>
```

| File | What it checks |
| --- | --- |
| `.t8_harness/t8_engine_test.cpp` | 31 pinned engine cases: seeding, fixpoint, break slots, window advancement, half-pair probes |
| `.t8_harness/multi_tw_oracle.cpp` | engine vs full schedule enumeration, 2 to 5 constrained pairs with multiple windows |
| `.t8_harness/boundary_oracle.cpp` | `is_valid_addition_for_tw` accept/reject vs the enumeration, over random committed routes and insertions |
| `.t8_harness/complexity_bench.cpp` | per-check scaling over route doublings |

`benchmarks/service_within_oracle.py` is the plan-mode counterpart: it
decides the hard constraint system with Bellman-Ford and compares against
`vroom -c`.

## Benchmarks

`benchmarks/*.py` generate instance sets and measure the published
figures; `benchmarks/*.txt` and `benchmarks/results_*/` are recorded runs.
Entry points: `convert_lilim.py` (Li&Lim to vroom JSON), `gen_worstcase.py`
/ `gen_multiwindow.py` / `gen_corner.py` / `gen_shaved.py` (instance
synthesis), `run_shaving_ab.py` and `capped_vs_filtered.py` (workaround
comparisons), `planmode_timing.py` and `service_within_battery.py`
(plan-mode timing and parity), `check_byte_identical.py` (two-binary
output comparator that reruns mismatches so run-to-run nondeterminism is
not mistaken for a behavioral difference).

`SPIKE_FINDINGS.md` records the overhead-reduction spike that shaped the
layered enforcement design.
