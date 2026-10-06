# Correlation validation status (FieldNet v32)

**Bottom line: none of the six multiphase correlations has been validated against published measured or worked-example data.**
Published numerical examples could not be retrieved in the build environment, and no values were invented. What exists is a
*consistency* suite (`tests/validation/test_correlation_consistency.py`, 71 checks) that detects implementation errors but cannot
show the correlations are accurate for any real well or line.

## What is checked (consistency only)
- Single-phase limits against Darcy-Weisbach with Colebrook-White friction (oil and water), including the laminar Hagen-Poiseuille limit.
- Hydrostatic limit for vertical no-flow / very low rate.
- Monotonic pressure drop with rate for horizontal flow; sign conventions for up/down inclination.
- Mutual spread across the six correlations on reference cases (table below, regenerate with `physics.correlation_benchmark.benchmark_table()`).

## Mutual spread on reference cases (not an accuracy statement)
| Case | q [m3/d] | L [m] | D [m] | Beggs-Brill [bar] | Homogeneous [bar] | Hagedorn-Brown [bar] | Gray [bar] | Drift-flux [bar] | Hasan-Kabir [bar] | Spread [%] |
|---|---|---|---|---|---|---|---|---|---|---|
| Horizontal oil flowline, moderate GOR | 2000 | 5000 | 0.2 | 4.84 | 3.34 | 2.68 | 4.75 | 5.23 | 5.31 | 60 |
| Horizontal wet-gas flowline | 150 | 8000 | 0.25 | 0.49 | 0.37 | 0.21 | 1.10 | 0.43 | 0.61 | 165 |
| Vertical riser 100 m, oil + gas | 1500 | 100 | 0.2 | 3.94 | 2.62 | 2.61 | 2.78 | 4.41 | 4.46 | 53 |
| Production tubing 2500 m (marched down: dz=+L) | 1200 | 2500 | 0.1 | 123.64 | 87.09 | 98.14 | 89.42 | 122.23 | 110.27 | 35 |
| Uphill flowline 2 deg, high water cut | 3000 | 4000 | 0.25 | 10.62 | 9.70 | 9.56 | 10.54 | 12.25 | 11.21 | 25 |
| Low-rate gas-lifted tubing | 300 | 2000 | 0.1 | 56.84 | 30.41 | 42.59 | 34.39 | 45.17 | 50.81 | 61 |
| Single-phase oil (no gas) | 2000 | 1000 | 0.2 | 0.27 | 0.27 | 0.27 | 0.27 | 0.27 | 0.27 | 0 |

A 30-60 % spread between correlations on the same case is normal in practice and is the reason to calibrate against well tests
(Advanced tab -> Well-test calibration) before trusting absolute pressure drops.

## To validate properly
1. Obtain published worked examples (Beggs & Brill 1973, Brill & Mukherjee 1999, Hasan & Kabir 2002 appendix) and encode them with the published tolerance.
2. Compare with a reference multiphase simulator (e.g. OLGA / LedaFlow steady state, or GAP/PIPESIM) on 3-5 representative wells and flowlines.
3. Calibrate against your own well tests; keep the correlation whose tuned multiplier is closest to 1.
