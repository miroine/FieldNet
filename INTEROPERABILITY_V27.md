# FieldNet v27 — Data & Interoperability

FieldNet v27 adds a validated interchange boundary while preserving canonical internal project storage.

## Formats
- Canonical project JSON remains backward compatible.
- Nodes and edges can be exported/imported as CSV tables.
- A `.zip` project package contains `project.json`, `nodes.csv`, `edges.csv`, and `manifest.json`.
- The manifest records application/schema version, interchange unit profile, standard conditions, and table counts.

## Unit policy
Canonical storage is unchanged. CSV exchange may be canonical, Norwegian SI, or Field. Recognized engineering fields are converted through `physics.unit_system`; unknown fields are not guessed from their values.

## Validation policy
Imports are validated before application. Duplicate/missing IDs, invalid edge references, non-positive pipe geometry, malformed parameter JSON, and unsupported unit profiles are rejected. Failed validation does not mutate the active project.

## Scope
v27 is an engineering-data interchange layer, not a database, historian, WITSML/PRODML implementation, or vendor-specific simulator bridge. CSV files are intentionally spreadsheet-friendly. The package schema is versioned for future adapters.
