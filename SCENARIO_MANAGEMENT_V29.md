# FieldNet v29 — Scenario Management & Reproducibility

FieldNet v29 adds a production-only scenario-management layer above the existing engineering engines.

## Guarantees
- Snapshots are deep-copied and content-addressed with SHA-256 over canonical nodes, edges, assumptions, and parent lineage.
- Branches retain explicit parent IDs.
- Scenario comparison is structural and deterministic.
- Archive import verifies snapshot hashes before accepting content.
- Model Assurance status may be captured with a snapshot; scenario management never changes solver physics.
- Run manifests bind settings/results to an immutable snapshot hash.

## Scope
This is engineering case governance and reproducibility, not economics and not source-control replacement. Timestamps are traceability metadata and are intentionally excluded from engineering content hashes.
