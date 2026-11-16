# Replay files and isolated storage

`files.py` reads source and implementation identities. `storage.py` reads the original
run and prepares an account database copy in a separate directory.

Replay execution belongs in [replay](../../replay/README.md); manifest assembly belongs
in application/replay_manifest.py. Keep the original database unchanged. This package
handles IO, not the algorithm that reapplies recorded operations.
