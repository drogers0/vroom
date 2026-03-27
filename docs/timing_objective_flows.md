# Timing Objective Flows

This document summarizes:

- old flow: `master` branch behavior
- new flow: current `feature/timing-objective` branch behavior

## Old Flow (master branch)

```mermaid
flowchart TD
    A[Build MIP model] --> B[Pass 1: minimize violations plus makespan]
    B --> C[Compute delta majorant M]
    C --> D[Set pass-2 objective to earliness only using M]
    D --> E[Pin makespan at pass-1 optimum]
    E --> F[Pin sum of violations Sigma_Y]
    F --> G[Pass 2: solve once for earliest start dates]
    G --> H[Extract ETAs and build route]
```

## New Flow (current feature branch implementation)

```mermaid
flowchart TD
    A2[Build MIP model] --> B2[Pass 1: minimize violations and baseline makespan]
    B2 --> C2[Pin sum of violations Sigma_Y]
    C2 --> D2[Pin makespan at pass-1 optimum]
    D2 --> E2{Default timing_objective path?}

    E2 -->|Yes| F2[Set earliness objective]
    F2 --> J2[Pass 2 solve once]
    J2 --> K2[Extract ETAs and build route]

    E2 -->|No| L2[Unpin makespan row]
    L2 --> M2{Mode}

    M2 -->|Hierarchical| N2[For each objective: set, solve, pin]
    N2 --> O2[Final earliness tiebreak solve]
    O2 --> K2

    M2 -->|Weighted| P2[Set weighted sum over objectives]
    P2 --> Q2[Add small earliness tiebreak]
    Q2 --> R2[Solve once]
    R2 --> K2
```

## Notes

- The old `master` flow has a fixed second pass: earliness minimization with pinned makespan and pinned violations.
- The new flow keeps that default path and ignores `target_time` when `timing_objective` is default.
- The new flow also adds a configurable branch for hierarchical and weighted timing objectives.
- In the configurable branch, makespan is explicitly unpinned before applying user objectives.
