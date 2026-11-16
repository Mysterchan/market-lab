# Work and model-attempt policy

These objects run inside the Runtime child. They do not create processes or threads.

- `controller.py`: LiveWorkPolicy handles work/attempt commands and lifecycle.
- `schedule.py`: one active work item, completion and waiting conditions.
- `attempts.py`: active-time model deadlines, retry eligibility and attempt identity.
- `events.py`: bounded event buckets for wake conditions.
- `conditions.py`: exact wait-condition validation, shared with tool ingress.

A model retry does not repeat tools or financial commands. A work wakeup does not
require a trade. Platform injects market observations and financial publications;
these policy objects do not read mutable account state or run models.
