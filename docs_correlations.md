# Multiphase correlations (physics/correlations.py)

Select by name with `physics.correlations.get_correlation(name)` (case/space/hyphen tolerant, aliases such as `HB`, `HK`, `DF`).
All share the Beggs-Brill signature and PVT. Status of every new correlation: **not validated against published numerical
examples** (none could be retrieved); verified for physical limits, monotonicity, robustness and mutual consistency.

| Name | Reference | Valid for | Caveats |
|---|---|---|---|
| Beggs-Brill | Beggs & Brill 1973 | any inclination, flowlines | existing; over-predicts wet-gas dp (+25..50% vs others) |
| Homogeneous | no-slip mixture | baseline | under-predicts slug-flow friction (-34% on benchmark flowline) |
| Hagedorn-Brown | Hagedorn & Brown 1965; Griffith 1962; Brill & Mukherjee 1999 fits | vertical oil wells (tubing) | uphill/horizontal use vertical holdup, downhill no-slip; over-predicts holdup at high gas fraction; Griffith/HB switch not monotone in friction-dominated horizontal flow |
| Gray | Gray 1974 (API 14B) | vertical gas/condensate wells, vm < 15 m/s, D < 3.5 in | liquid-dominated flow is extrapolation; coefficient 2.314 and ke floor unit (ft) from secondary sources |
| Drift-flux | Zuber-Findlay 1965; Harmathy 1960; Ishii 1977; Bendiksen 1984 | any inclination incl. downhill | simplified high-void closure (implementation choice); no wet-gas film holdup |
| Hasan-Kabir | Hasan & Kabir 1988; Taitel et al. 1980; Butterworth 1975 | vertical / upward-inclined | annular holdup via Butterworth LM fit, not HK's entrainment model; wide (+/-35%) regime blends; downhill delegates to drift-flux |

Not implemented: Mukherjee-Brill, Duns-Ros, Orkiszewski (coefficients could not be retrieved from accessible sources).
