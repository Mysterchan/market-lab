"""Per-process interpreter selection without changing multiprocessing global settings.

Only saved projects use this adapter. The child receives installed SDK modules,
shared capability handles and its source directory, never the App's import paths.
"""

from __future__ import annotations

from multiprocessing.context import SpawnProcess
from multiprocessing import spawn
from typing import Any, Dict, List, Optional


def preparation_data(process: InterpreterProcess) -> Dict[str, Any]:
    """Spawn preparation data for the child, pointing at the project instead of the App."""
    data = spawn.get_preparation_data(process.name)
    for key in ('sys_path', 'init_main_from_path', 'init_main_from_name'):
        data.pop(key, None)
    data.update(dir=process.project, orig_dir=process.project, sys_argv=['agent.py'])
    return data


def command(executable: str, **handles: Any) -> List[str]:
    """Command line that starts multiprocessing's spawn entry in ``executable``, isolated."""
    arguments = ', '.join(f'{key}={value!r}' for key, value in handles.items())
    return [
        executable,
        '-I',
        '-B',
        '-c',
        'from multiprocessing.spawn import spawn_main; spawn_main(' + arguments + ')',
        '--multiprocessing-fork',
    ]


class InterpreterProcess(SpawnProcess):
    """A spawn Process run by a chosen Python interpreter in the project folder.

    The Agent host creates one per saved-project Agent; it is otherwise an ordinary
    multiprocessing Process.
    """

    @staticmethod
    def _Popen(process: Any) -> Any:
        import os

        if os.name == 'nt':
            from .spawn_windows import InterpreterPopen

            return InterpreterPopen(process)
        from .spawn_posix import InterpreterPopen as PosixInterpreterPopen

        return PosixInterpreterPopen(process)

    def __init__(
        self,
        *,
        executable: str,
        project: str,
        sandbox: Optional[Dict[str, str]] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.executable, self.project = executable, project
        # An optional OS sandbox around the interpreter (processes.sandbox).
        self.sandbox = sandbox
