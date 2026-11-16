"""Inspect captured project text without importing or executing it."""

from __future__ import annotations

import ast
import json
import re
import sys
import io
import tokenize
from typing import Any, Dict, List, Optional, Set

from sim_platform.agents.runtimes.versions import DEFAULT_PYTHON, PythonRequest
from sim_platform.agents.preparation.errors import PreparationError


# Direct dependency bounds: longest requirement text, and most requirements.
MAX_REQUIREMENT_LENGTH = 512
MAX_REQUIREMENTS = 100


def requirements(text: str) -> List[str]:
    """Accept package/version requirements; never interpret pip options or executable sources."""
    result: Set[str] = set()
    name = r'[A-Za-z0-9][A-Za-z0-9._-]*(?:\[[A-Za-z0-9_,.-]+\])?'
    version = r'(?:===|==|!=|~=|>=|<=|>|<)[A-Za-z0-9.*+!_-]+(?:\.[A-Za-z0-9.*+!_-]+)*'
    for line in text.splitlines():
        line = line.split('#', 1)[0].strip()
        if not line:
            continue
        line = re.sub(r'\s+', '', line)
        if (
            len(line) > MAX_REQUIREMENT_LENGTH
            or re.fullmatch(name + '(?:' + version + '(?:,' + version + ')*)?', line) is None
        ):
            raise PreparationError(
                'INVALID_DEPENDENCY',
                'use package/version requirements; URLs, paths, pip options and markers are not supported',
            )
        result.add(line)
    if len(result) > MAX_REQUIREMENTS:
        raise PreparationError('PROJECT_TOO_LARGE', 'at most 100 direct dependencies are supported')
    return sorted(result)


def inspect_project(
    files: Dict[str, str], *, default_python: Optional[str] = None
) -> Dict[str, Any]:
    """Validate syntax, the factory declaration and environment settings from text values."""
    if 'agent.py' not in files:
        raise PreparationError(
            'MISSING_ENTRYPOINT', 'the project needs agent.py with create_agent(context)'
        )
    settings: Any = {}
    if 'agent.json' in files:
        try:
            settings = json.loads(files['agent.json'])
        except ValueError as exc:
            raise PreparationError(
                'INVALID_PROJECT_SETTINGS', 'agent.json must contain a JSON object'
            ) from exc
        if type(settings) is not dict or set(settings) - {'python'}:
            raise PreparationError(
                'INVALID_PROJECT_SETTINGS', 'agent.json only accepts the python setting'
            )
    python = settings.get('python', default_python or DEFAULT_PYTHON)
    if not isinstance(python, str) or not re.fullmatch(
        r'(?:[0-9]+\.[0-9]+(?:\.[0-9]+)?|[<>=0-9., ]+)', python
    ):
        raise PreparationError(
            'INVALID_PYTHON_VERSION',
            'python must be a minor, patch or version range, such as 3.12 or >=3.11,<3.14',
        )
    try:
        request = PythonRequest(python)
        newer_grammar = any(minor > sys.version_info[:2] for minor in request.candidates)
    except PreparationError:
        newer_grammar = False
    for name, text in files.items():
        if not name.endswith('.py'):
            continue
        try:
            tree = ast.parse(text, filename=name)
        except (SyntaxError, ValueError) as exc:
            if newer_grammar:
                try:
                    list(tokenize.generate_tokens(io.StringIO(text).readline))
                except (tokenize.TokenError, IndentationError) as token_error:
                    raise PreparationError(
                        'INVALID_PYTHON', 'invalid Python syntax in ' + name
                    ) from token_error
                continue
            raise PreparationError('INVALID_PYTHON', 'invalid Python syntax in ' + name) from exc
        if name == 'agent.py':
            validate_factory(tree)
    return dict(
        entrypoint='agent:create_agent',
        python=python,
        requirements=requirements(files.get('requirements.txt', '')),
        files=[
            dict(path=name, bytes=len(text.encode('utf-8'))) for name, text in sorted(files.items())
        ],
    )


def validate_factory(tree: ast.Module) -> None:
    """Require exactly one plain top-level create_agent(context) function."""
    factories = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == 'create_agent'
    ]
    if len(factories) != 1:
        raise PreparationError(
            'MISSING_ENTRYPOINT', 'declare exactly one top-level create_agent(context) function'
        )
    factory = factories[0]
    arguments = factory.args
    if (
        isinstance(factory, ast.AsyncFunctionDef)
        or factory.decorator_list
        or len(arguments.posonlyargs + arguments.args) != 1
        or arguments.vararg
        or arguments.kwarg
        or arguments.kwonlyargs
    ):
        raise PreparationError(
            'INVALID_ENTRYPOINT',
            'create_agent must be an undecorated synchronous function taking one context argument',
        )
