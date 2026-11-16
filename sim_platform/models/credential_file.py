"""Read one explicitly selected credential from a user-specified local env file.

No shell evaluation, variable expansion or export of the rest of the file.
The returned secret is for the selected credential store, never run config.
"""

from __future__ import annotations

from pathlib import Path
import re
from typing import TYPE_CHECKING, List, Union

from sim_platform.domain.models import ContractError
from .credentials import validate_secret

if TYPE_CHECKING:  # Annotation only.
    import os

# Largest credential file read, in bytes; one more character is read to detect overflow.
MAX_FILE_BYTES = 65536


def read_credential(path: Union[str, 'os.PathLike[str]'], variable: str = 'OPENAI_API_KEY') -> str:
    """Return the one value assigned to ``variable``; it must appear exactly once."""
    if not isinstance(variable, str) or re.fullmatch('[A-Za-z_][A-Za-z0-9_]*', variable) is None:
        raise ContractError('invalid credential variable name')
    with Path(path).expanduser().open(encoding='utf-8') as source:
        text = source.read(MAX_FILE_BYTES + 1)
    if len(text.encode()) > MAX_FILE_BYTES:
        raise ContractError('credential file exceeds supported size')
    found: List[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[len('export ') :].lstrip()
        (name, separator, value) = line.partition('=')
        if not separator or name.strip() != variable:
            continue
        value = value.strip()
        if value.startswith(('"', "'")):
            quote = value[0]
            end = value.find(quote, 1)
            if end < 0 or (
                value[end + 1 :].strip() and (not value[end + 1 :].lstrip().startswith('#'))
            ):
                raise ContractError('invalid quoted credential value')
            value = value[1:end]
        else:
            value = value.split(' #', 1)[0].rstrip()
        found.append(validate_secret(value))
    if len(found) != 1:
        raise ContractError('credential variable missing or repeated')
    return found[0]
