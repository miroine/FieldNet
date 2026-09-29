# FieldNet v19 — Flow Assurance

FieldNet v19 is a production-only release; economics is intentionally excluded.

## Scope
The flow-assurance layer post-processes solved pipeline states and reports a segmented steady thermal profile, generic hydrate screening margin, user-configured wax appearance temperature margin, API-RP-14E-style erosional velocity ratio, Turner-style liquid-loading ratio, and a regime/Froude slugging indicator.

## Interpretation
These diagnostics are screening/planning tools. Hydrate prediction is not compositional thermodynamics; wax risk requires a calibrated WAT; API-14E is an empirical erosion screen; Turner is a liquid-loading criterion; and the slugging flag is not a transient multiphase simulation. Hydraulic convergence, operating feasibility, and flow-assurance risk remain separate statuses.

## Thermal model
The thermal model uses a steady exponential heat-loss solution with overall U, pipe outside diameter approximated by configured hydraulic diameter, ambient temperature, mixture mass-flow proxy and heat capacity. Joule-Thomson and phase-change heat are not modeled.

## Units
The v17.2 canonical unit boundary remains in force. Norwegian SI and Field display/input profiles convert to the same canonical calculation inputs. v19 adds velocity and overall heat-transfer-coefficient conversions. Saved project physics remains canonical.
