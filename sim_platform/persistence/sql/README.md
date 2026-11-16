# Account database

`database.py` owns the engine, initialization, short units of work and shutdown.
`models.py` defines the shared Base metadata. `mapping.py` converts ORM rows to detached
domain values. Repositories in one unit of work share the same Session and models.

- [repositories/](repositories/README.md): named queries and storage operations.
- [migrations/](migrations/README.md): database layout upgrades.
- `decision_records.py`: decision audit encoding and decoding.
- `run_checkpoints.py`: validation of saved execution state.

Do not add playback, model loops or Qt code here.
