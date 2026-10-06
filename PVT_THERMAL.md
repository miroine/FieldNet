# Fluid & PVT and temperature modelling

Where: **Reservoir & wells → Fluid & PVT**. Everything is opt-in per element; elements you do not touch keep the previous screening model, so existing cases give the same numbers.

## Why this was needed
The previous black-oil model (`physics/pvt.py: simple_black_oil`) used a fixed bubble point (150 bar) and solution GOR (120 Sm³/Sm³) for every fluid and ignored the element's own GOR; Z, viscosities and water density were simple fits. Flowlines were isothermal and tubing used a linear temperature between two user numbers. Expect results to move when you switch (free gas now appears where the fluid really crosses its bubble point).

## Correlations (`physics/pvt_model.py`)
| Property | Options |
|---|---|
| Pb, Rs | Standing, Vasquez-Beggs, Glaso, Petrosky-Farshad |
| Bo (saturated) | Standing, Vasquez-Beggs, Glaso, Petrosky-Farshad; undersaturated: Vasquez-Beggs compressibility |
| Dead-oil viscosity | Beggs-Robinson, Glaso, Egbogah, Beal; live: Beggs-Robinson; undersaturated: Vasquez-Beggs |
| Z-factor | Dranchuk-Abou-Kassem, Hall-Yarborough, Papay (low pressure only) |
| Gas viscosity | Lee-Gonzalez-Eakin + Carr-Kobayashi-Burrows N2/CO2/H2S corrections |
| Water | McCain Bw, density, viscosity with salinity |

**Contaminants**: CO2, H2S, N2 mole fractions of the associated gas. Kay mixing of pseudo-criticals (Sutton for the hydrocarbon part), Wichert-Aziz sour correction, Standing's contaminant factors on the bubble point.
Not modelled: CO2 dissolving in the oil (swelling, viscosity reduction) - calibrate to a swelling test for CO2-rich fluids; condensate / volatile-oil behaviour (use tables).

## Calibration
Lab input: Pb, Rsb, Bo and viscosity at Pb, and a table (Rs, Bo, μo, Z, μg vs pressure). Fitted parameters: Pb multiplier, Rs curve shape, (Bo-1) multiplier, undersaturated compressibility multiplier, dead-oil viscosity multiplier, Z multiplier, gas-viscosity multiplier. Measured values are never changed. A report shows mean absolute % error per property before / after, and a **correlation ranking** shows which published correlation fits your lab data best. Lab Rsb is stored on the element (a fluid property), so the producing GOR may rise above it in a forecast.

## Temperature
| Model | Where | What it does |
|---|---|---|
| fixed (default) | all | previous behaviour |
| `ramey` | wells | Ramey (1962) steady wellbore profile from BHT, geothermal gradient and overall U; relaxation distance depends on mass rate, so wellhead temperature rises with rate; the profile feeds the PVT of every tubing segment |
| `heat_loss` | pipelines, risers | marching energy balance per segment: heat loss to ambient, Joule-Thomson (gas from dZ/dT of the same Z correlation, liquid from the thermal-expansion term), elevation (potential energy), mixture cp from oil / water / gas mass rates |

**Network pass** (`network/thermal_network.py`): temperatures propagate from the wells through flowlines and chokes (JT), mix at nodes (mass-flow × cp weighted), and flowline inlet temperatures are fed back into the hydraulics (max 4 passes, 0.5 °C tolerance). The flow-assurance profile (hydrate / wax margins) uses the same energy balance and the network inlet temperature.

## Gas quality (`physics/gas_quality.py`)
pCO2, pH2S, de Waard-Milliams CO2 corrosion rate (uncorrected, conservative) and the ISO 15156 / NACE MR0175 sour-service threshold per well and flowline.

## Verification done
* Z-factors vs Standing-Katz chart points (4 points within 0.02); Standing Rs at 2000 psia = 445 scf/STB (textbook example); Pb ↔ Rs inversion consistent for all four sets.
* Calibration recovers a synthetic fluid generated with known multipliers; ranking selects the generating correlation.
* Thermal: analytic exponential decay; adiabatic elevation cooling = g·Δz/cp; JT of a wet gas 0.15-0.55 K/bar; Ramey wellhead temperature rises with rate and is bounded by BHT; network mixing between incoming outlets.

## Limits (be aware)
* Correlations are empirical: use them inside their ranges (API 15-45, Rs < ~1500 scf/STB for most). Always calibrate to a lab report for design numbers.
* Standing's contaminant factors are valid for modest fractions; verify the bubble point against a lab / EoS value for > 20 mol % CO2 or > 5 mol % H2S.
* Thermal: constant U, no phase-change enthalpy, no transient (cool-down / shut-in) behaviour; the forecast uses each element's own thermal model but does not iterate the network inlet temperatures between timesteps (use the Network solve for that).
* The gas-network solver (`gas_network.py`) still uses its own isothermal Z.
* No emulsion viscosity (liquid viscosity is volume-weighted): high water-cut inversion is not captured.
* Not validated against a commercial PVT package; no EoS / compositional model.
