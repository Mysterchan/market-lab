# Agent process-family ownership

This folder only handles OS lifecycle primitives. It does not schedule work,
validate trades, select a Python runtime or isolate files/network access.

- `__init__.py`: select the native adapter and enter a private POSIX session in the child.
- `posix.py`: target that child's process group with TERM then KILL. The App's group
  is never used. Ordinary descendants inherit membership; a program explicitly
  creating another session can escape it. This is trusted-code management, not containment.
- `windows.py`: parent-owned, non-inheritable Job Object with kill-on-close. Assign the
  Agent before opening its launch gate. Ordinary CreateProcess descendants join it.
  Assignment errors fail startup; there is no silent fallback to unmanaged execution.

The host owns the adapter, startup handshake, graceful deadline, notification and
retryable cleanup. `hosting/lifecycle.py` performs bounded termination and root reaping.
Windows Job handles remain owned until successful host close. POSIX groups have no
parent kernel handle; sudden App death is not automatic process-tree recovery.

Run `tests/platform/test_agent_lifecycle.py` on each actual target OS. Adapter code
and a CI matrix are not evidence that Windows or Linux already passed.

References: [Microsoft Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)
and [Python process-session primitives](https://docs.python.org/3/library/os.html#os.setsid).

`interpreter.py` selects a per-child Python and removes App import/main-script metadata.
`spawn_posix.py` and `spawn_windows.py` preserve CPython native handle reducers and the
resource tracker while selecting that executable. On Windows, the venv redirector is
bypassed using its base interpreter and __PYVENV_LAUNCHER__, so the owned PID is the
actual Agent. This implementation boundary needs actual Python-pair/OS tests.

Spawn launch sequencing adapts CPython multiprocessing; its Python/PSF notice is
retained in the SDK license metadata. See sdk/licenses/Python-PSF.txt.
