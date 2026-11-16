"""Load a saved project only inside its selected Agent interpreter."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from typing import TYPE_CHECKING, Any, Union

from .lifecycle import AgentStartupError

if TYPE_CHECKING:  # Annotation only.
    from os import PathLike

    from sim_platform.sdk.context import AgentContext


class ProjectFactory:
    """Agent factory that imports a saved project's agent.py inside the Agent child.

    Created per launch with the project's source folder; calling it imports the
    module once and returns create_agent(context).
    """

    def __init__(self, directory: Union[str, PathLike[str]]) -> None:
        self.directory = directory

    def __call__(self, context: AgentContext) -> Any:
        root = Path(self.directory)
        # Project-local imports are allowed. SDK resolution comes from installed site-packages.
        sys.path.insert(0, str(root))
        specification = importlib.util.spec_from_file_location('agent', root / 'agent.py')
        if specification is None or specification.loader is None:
            raise AgentStartupError('AGENT_ENTRYPOINT_INVALID')
        module = importlib.util.module_from_spec(specification)
        sys.modules['agent'] = module
        try:
            specification.loader.exec_module(module)
        except ModuleNotFoundError as exc:
            raise AgentStartupError('AGENT_DEPENDENCY_MISSING') from exc
        factory = getattr(module, 'create_agent', None)
        if not callable(factory):
            raise AgentStartupError('AGENT_ENTRYPOINT_INVALID')
        return factory(context)
