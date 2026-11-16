# Database upgrades

`initialize()` in `__init__.py` checks the application ID, layout and schema revision,
then upgrades in order and checks foreign keys. The current revision is v7.
Each revision keeps its original DDL and data conversion rules.

Database runs migration work through one connection inside BEGIN IMMEDIATE. A failure
rolls back the upgrade. Moving Python files is not a schema change. Changes to models
or migrations need upgrade and rollback tests against existing database layouts.
