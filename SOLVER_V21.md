# FieldNet v21 — Network Solver 2.0

v21 is a numerical robustness and diagnostics release. It does not replace the validated production-physics correlations.

## Added
- topology prechecks for dangling edges, self loops and unanchored connected components;
- Jacobian-based variable scaling in the nonlinear least-squares solve;
- reusable warm starts from prior solved pressures and rates;
- bounded retry orchestration with best physical-residual selection;
- Jacobian condition-number, iteration/optimality and attempt-history reporting;
- independent post-solve reconstruction of pressure-equation and node mass-balance residuals;
- structured failure attribution for topology, non-convergence, pressure equations, mass balance, ill-conditioning, stagnation and operating limits;
- Streamlit Solver 2.0 debugger.

## Interpretation
A numerical convergence flag is not treated as proof of an operable physical case. FieldNet reports numerical convergence, reconstructed physical residual closure and operating constraints separately. A large Jacobian condition number is a warning about sensitivity/conditioning, not by itself proof that a solution is physically wrong.

## Physics boundary
Hydraulic, PVT, well-performance and flow-assurance correlations retain their existing validation/screening status. Solver 2.0 improves how the nonlinear system is solved and diagnosed; it does not increase the fidelity of the underlying empirical correlations.
