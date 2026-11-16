"""Child-process entry point that loads a user's model file and answers decisions.

This file runs in the model's own Python interpreter, which may be a prepared
environment without the platform installed, so it imports only the standard library.
It is started as ``python -I -u model_worker.py MODEL_FILE`` by ModelFilePolicy.

Protocol: one JSON object per line. The worker first writes ``{"ready": true}`` or
``{"ready": false, "error": "..."}``. It then reads one request per line,
``{"bars": [...], "account": {...}, "context": {...}}``, calls the user's
``decide(bars, account, context)`` and writes ``{"ok": true, "result": <value>}`` or
``{"ok": false, "error": "..."}``. End of input ends the worker. The user's own
``print`` output goes to standard error so it cannot corrupt the protocol.
"""

from __future__ import annotations
import logging

import importlib.util
import json
import os
import sys
import traceback
from types import ModuleType
from typing import IO, Any, Dict

_log = logging.getLogger(__name__)

# Longest error text sent back; the full traceback stays on standard error.
MAX_ERROR_CHARACTERS = 2000


def load_model(path: str) -> ModuleType:
    """Import the user's file as module ``user_model`` with its folder on sys.path."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(path)))
    spec = importlib.util.spec_from_file_location('user_model', path)
    if spec is None or spec.loader is None:
        raise ImportError('cannot load model file ' + path)
    module = importlib.util.module_from_spec(spec)
    sys.modules['user_model'] = module
    spec.loader.exec_module(module)
    if not callable(getattr(module, 'decide', None)):
        raise TypeError('the model file must define decide(bars, account, context)')
    return module


def write(stream: IO[str], value: Dict[str, Any]) -> None:
    """Send one protocol message."""
    stream.write(json.dumps(value, allow_nan=False) + '\n')
    stream.flush()


def short_error(error: BaseException) -> str:
    """One-line description of a user error, also printed in full to standard error."""
    traceback.print_exc(file=sys.stderr)
    return (type(error).__name__ + ': ' + str(error))[:MAX_ERROR_CHARACTERS]


def serve(path: str, incoming: IO[str], outgoing: IO[str]) -> None:
    """Load the model, then answer requests until end of input."""
    try:
        module = load_model(path)
        setup = getattr(module, 'setup', None)
        first = incoming.readline()
        if not first:
            return
        options = json.loads(first)
        if callable(setup):
            setup(options.get('parameters', {}))
    except Exception as error:  # Reported to the platform, which fails the run.
        _log.warning('Handled failure in serve', exc_info=True)
        write(outgoing, dict(ready=False, error=short_error(error)))
        return
    write(outgoing, dict(ready=True))
    for line in incoming:
        try:
            request = json.loads(line)
            result = module.decide(request['bars'], request['account'], request['context'])
            write(outgoing, dict(ok=True, result=result))
        except Exception as error:
            _log.warning('Handled failure in serve', exc_info=True)
            write(outgoing, dict(ok=False, error=short_error(error)))


def main() -> None:
    """Run the protocol on the real standard streams."""
    protocol = sys.stdout
    sys.stdout = sys.stderr
    serve(sys.argv[1], sys.stdin, protocol)


if __name__ == '__main__':
    main()
