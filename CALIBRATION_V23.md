# FieldNet v24 — Calibration & History Matching

v23 adds bounded weighted least-squares calibration around the v21 Solver 2.0 kernel. Supported observations are node pressure and edge liquid rate. Supported calibration parameters include well PI multiplier, optional skin offset, pipeline roughness multiplier, pump head multiplier, and compressor pressure-ratio multiplier.

The base project is deep-copied for every trial. Measurement uncertainty (`sigma`) weights residuals. Jacobian rank/condition, bound hits and local linearized parameter standard deviations are diagnostics only. A low residual does not prove uniqueness, causality, or physical correctness; the covariance estimate is not a Bayesian posterior.
