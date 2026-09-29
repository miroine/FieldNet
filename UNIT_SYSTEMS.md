# FieldNet v17.2 unit systems

## Canonical calculation/storage basis
FieldNet stores and solves in canonical engineering quantities: pressure in bar, temperature in °C, geometry in m, liquid rate in m³/d, standard gas rate in Sm³/d, density in kg/m³, viscosity in cP where applicable, and power in kW. Saved project JSON remains canonical so changing the display profile never reinterprets existing numbers.

## Norwegian SI profile
- Pressure: bar (PVT/thermodynamic pressure is absolute; gauge/absolute conversion is explicit)
- Temperature: °C
- Length / diameter: m / mm
- Liquid and standard rates: Sm³/d where the quantity is at standard/stock-tank conditions
- Standard volumes: Sm³
- GOR: Sm³/Sm³
- Density: kg/m³
- Power: kW

## Field profile
- Pressure: psi
- Temperature: °F
- Length / diameter: ft / in
- Liquid rate / volume: stb/d / stb
- Standard gas rate / volume: Mscf/d / MMscf
- GOR: scf/stb
- Density: lb/ft³
- Power: hp

## Standard conditions
FieldNet v17.2 reporting reference for standard volumes is 15 °C and 1.01325 bara. Standard gas volume is not treated as flowing/actual m³. Stock-tank liquid and standard-gas conversions are independent. Individual published correlations can retain their documented internal reference basis; those are correlation assumptions, not UI unit conversions.

## Pressure convention
Network hydraulic pressure is represented canonically in bar. PVT and gas-law calculations require absolute pressure. Helpers explicitly convert gauge ↔ absolute using 1.01325 bar atmospheric pressure by default; no hidden psi-g/bar-g offset is applied during ordinary unit conversion.

## Acceptance checks
The v17.2 test suite includes exact round trips, known conversion constants, 1000 m water static head (98.0665 bar), pump hydraulic power, PI deliverability, standard gas/liquid distinction, project mapping round trips, non-finite input rejection, and Norwegian-SI-vs-Field physics invariance.
