"""Validate captured text with its selected interpreter, without executing the Agent."""

from __future__ import annotations

import json
from typing import Callable, Dict

from sim_platform.agents.preparation.process import run_command
from sim_platform.agents.preparation.errors import PreparationError

CHECK = '''import ast, json, sys
files = json.load(sys.stdin)
result = {'ok': True}
for name, text in files.items():
    if not name.endswith('.py'): continue
    try:
        tree = ast.parse(text, filename=name)
        if name == 'agent.py':
            items = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == 'create_agent']
            assert len(items) == 1
            f = items[0]; a = f.args
            assert not isinstance(f, ast.AsyncFunctionDef) and not f.decorator_list
            assert len(a.posonlyargs + a.args) == 1 and not a.vararg and not a.kwarg and not a.kwonlyargs
    except (SyntaxError, ValueError, AssertionError):
        result = {'ok': False, 'file': name}; break
print(json.dumps(result))
'''


def validate_with_python(
    executable: str, files: Dict[str, str], cancelled: Callable[[], bool]
) -> None:
    result = json.loads(
        run_command(
            [executable, '-I', '-S', '-c', CHECK],
            cancelled,
            input_data=json.dumps(files),
            error_code='INVALID_PYTHON',
        )
    )
    if not result.get('ok'):
        raise PreparationError(
            'INVALID_PYTHON',
            'syntax or factory declaration is invalid for selected Python: '
            + result.get('file', 'agent.py'),
        )
