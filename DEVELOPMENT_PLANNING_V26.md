# FieldNet v26 — Development Planning

v26 compiles development tasks into an executable production schedule before invoking the existing quasi-steady forecast. It contains no economics.

## Supported planning logic
- Finish-to-start task dependencies.
- Shared resource constraints such as drilling/workover rigs.
- Parallel work on distinct resources.
- Drilling, workover, tieback, commissioning/first-production, facility expansion, compression start, shutdown and abandonment task semantics.
- Explicit target IDs and event fields for auditable schedule-to-model mapping.
- Gantt-style schedule display and JSON export.

## Engineering boundary
This is deterministic development scheduling coupled to a production forecast. It is not a drilling-duration simulator, probabilistic project scheduler, construction model, or economic optimizer. Tasks affect production only through explicit compiled model events.
