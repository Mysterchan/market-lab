# Dataset file storage

`files.py` owns DatasetFiles: source identity, copying, registry and preference IO.
The import workflow lives in `research/datasets.py`. Platform supplies the storage
location. This package does not own an account database, UI or playback loop.
