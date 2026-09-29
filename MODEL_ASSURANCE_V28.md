# FieldNet v28 — Engineering QA & Model Assurance

The v28 assurance layer is read-only. It consolidates structural, numerical and engineering-plausibility checks without changing the underlying physics or automatically correcting inputs.

## Quality gates
- **PASS**: no error or warning findings.
- **REVIEW**: no definite invariant violation, but at least one suspicious value or sensitivity warning needs engineering review.
- **FAIL**: a definite structural, range, conservation, convergence or physical-closure invariant is violated.

Checks include topology/reference integrity, pressure and fraction ranges, well/tubing plausibility, pipeline geometry and relative roughness, equipment control ranges, nonlinear convergence, independent pressure/mass residual closure, operating constraints, Jacobian conditioning and forecast chronology/non-negative production.

Threshold warnings are screening QA and are not substitutes for project-specific design criteria. Standard-volume reference conditions remain those documented in UNIT_SYSTEMS.md.
