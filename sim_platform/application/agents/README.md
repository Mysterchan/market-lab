# Saved Agent project orchestration

`projects.py` connects injected file capabilities, static inspection and existing
Agent revision operations. It does not construct Platform or open SQL sessions.

Import captures and validates text, publishes an independent source snapshot, then
appends the definition through the existing short database transaction. If that
transaction fails, only the new unreferenced candidate is removed. Earlier revisions
and environments are untouched. A crash before the database reference can leave an
unreferenced snapshot, never a saved definition pointing to an unpublished copy.

Read operations check Agent ownership in SQL, then validate the snapshot's profile
ownership and inventory. A manually forged project_ref cannot expose another profile.

`environments.py` resolves compatible saved environments, assembles dependency preparation
jobs and validates launch inputs before allocating a temporary source workspace. It receives
project, runtime, environment-file and SDK capabilities. The process host takes ownership
of a successful launch workspace.

Platform is the public entry and registers preparation jobs for shutdown. Agent environment
orchestration remains separate from financial experiment setup.
