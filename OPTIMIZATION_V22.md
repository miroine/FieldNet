# FieldNet v22 — Integrated Production Optimization

v22 jointly optimizes controls that are explicitly represented in the steady-state network kernel: well opening/productivity multipliers, control-valve opening, pump speed through affinity-law scaling, and compressor-map speed. Each candidate is solved by the v21 Solver 2.0 and evaluated for numerical/physical quality plus configured operating constraints.

## Integrity rules
- Feasibility is reported separately from production objective.
- Constraint violations receive explicit penalties and remain visible in results.
- Differential evolution is used as a screening search; FieldNet does **not** claim a guaranteed global optimum.
- Base cases are deep-copied; optimization does not mutate the saved project.
- Seed and bounds are retained for reproducibility.

## Current exclusions
Gas-lift allocation remains a v20 nodal-analysis screening model and is not coupled into the network equations, so v22 does not falsely optimize it. Likewise, injector allocation is not optimized until injection is represented as a coupled source equation in the steady-state network kernel.

No economics are included.
