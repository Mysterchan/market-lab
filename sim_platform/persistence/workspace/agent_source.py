"""One execution's writable source copy, independent of the saved Agent revision."""

from __future__ import annotations

from pathlib import Path
import shutil
from typing import TYPE_CHECKING, Dict, Union

if TYPE_CHECKING:  # Annotation only.
    from os import PathLike


class AgentSourceFiles:
    """A writable copy of an Agent's source files for one execution.

    The App workspace allocates the folder; the Agent host owns this object and
    close() deletes the folder. A failed write deletes it at once.
    """

    def __init__(self, directory: Union[str, PathLike[str]], files: Dict[str, str]) -> None:
        self.directory = Path(directory)
        try:
            for name, text in files.items():
                target = self.directory / name
                target.parent.mkdir(parents=True, exist_ok=True)
                # Preserve the saved source, including CRLF, without OS text conversion.
                target.write_bytes(text.encode('utf-8'))
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        if self.directory.exists():
            shutil.rmtree(self.directory)
