# FieldNet v25 — Reservoir–Network Coupling 2.0

Production-only reduced-order reservoir/network planning model. It extends the independent v11 tanks with simultaneous material-balance updates, communicating-tank transmissibility, pressure-dependent aquifer influx, explicit injector-to-tank connectivity and a timestep ledger.

## Accounting
For each tank and timestep, net voidage is production withdrawal + communication outflow − direct injection − aquifer influx − communication inflow. Pressure change is net voidage divided by pore-volume × total-compressibility capacity, subject to minimum pressure. Communication transfers are equal-and-opposite across tank pairs.

## Boundaries
This is not a gridded or compositional reservoir simulator. Transmissibilities, aquifer productivity and connectivity weights are reduced-order planning parameters and should be calibrated to reservoir studies/history where available. The network remains quasi-steady at each timestep.
