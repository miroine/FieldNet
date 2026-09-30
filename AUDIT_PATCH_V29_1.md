# FieldNet v29.1 audit/corrective release

This release is feature-frozen and addresses defects found in the post-v29 adversarial audit.

1. Non-finite numeric values (NaN/Inf) are rejected at CSV/project validation boundaries and by Model Assurance for solved/forecast values.
2. Pipeline roughness calibration now writes the top-level `roughness_m` consumed by the hydraulic solver.
3. Reliability simulation uses exact interval durations and clips the final timestep to the requested horizon.
4. Reservoir communication is capped by pairwise pressure equalization and donor available storage, preventing a stiff explicit step from crossing equilibrium in the audited two-tank failure mode.
5. Scenario archives verify each run manifest SHA-256 and its referenced snapshot during export/import.
6. Stale application metadata and the main UI release banner are aligned to FieldNet v29.1.

The reservoir coupling remains a reduced-order planning model; complex multi-link stiff systems should still be treated as screening calculations rather than a replacement for a reservoir simulator.
