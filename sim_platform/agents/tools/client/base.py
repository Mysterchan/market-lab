"""Tool interfaces for local computation and remote platform operations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from inspect import signature, Parameter
from typing import Any, Dict, Optional, Protocol, Tuple

from sim_platform.domain.json_values import copy_json
from sim_platform.domain.models import ContractError
from ..protocol.errors import ToolError

ToolResponse = Dict[str, Any]


class ToolClient(Protocol):
    """What a platform tool proxy needs: the Agent client's call_tool."""

    def call_tool(self, name: str, arguments: dict) -> ToolResponse:
        """Send a tool operation under the client's active work identity."""
        ...


def text(description: str) -> Dict[str, Any]:
    """JSON schema for a string argument."""
    return dict(type='string', description=description)


def counter(description: str) -> Dict[str, Any]:
    """JSON schema for a canonical decimal counter string."""
    return dict(type='string', pattern=r'^(0|[1-9][0-9]*)$', description=description)


def integer(description: str, minimum: int = 1, maximum: Optional[int] = None) -> Dict[str, Any]:
    """JSON schema for an integer argument with bounds."""
    value: Dict[str, Any] = dict(type='integer', minimum=minimum, description=description)
    if maximum is not None:
        value['maximum'] = maximum
    return value


SYMBOL = text('The experiment stock symbol, such as AAPL.')
CURSOR = text('A next_cursor returned by this tool; omit on the first page.')
LIMIT = integer('Maximum items in this page.')


class Tool(ABC):
    """Keep one tool's name, description, arguments and callable behavior together.

    Subclasses implement a typed run() method. Required arguments come from that
    signature; optional arguments have Python defaults. Both direct code and model
    calls use run(). Local tools execute in the Agent process and return finite
    JSON values. They do not need a platform client or a platform result envelope.
    """

    name: str
    description: str
    parameters: dict = {}
    category = 'read'
    ends_work = False

    @classmethod
    def required_arguments(cls) -> Tuple[str, ...]:
        return tuple(
            name
            for name, value in signature(cls.run).parameters.items()
            if name != 'self' and value.default is Parameter.empty
        )

    @classmethod
    def optional_arguments(cls) -> Tuple[str, ...]:
        return tuple(
            name
            for name, value in signature(cls.run).parameters.items()
            if name != 'self' and value.default is not Parameter.empty
        )

    @property
    def function_name(self) -> str:
        return self.name.replace('.', '_')

    def input_schema(self, limits: dict) -> dict:
        """Describe this tool's arguments with the experiment's granted bounds."""
        properties = copy_json(self.parameters)
        return dict(
            type='object',
            properties=properties,
            required=list(self.required_arguments()),
            additionalProperties=False,
        )

    def invoke(self, arguments: dict) -> Any:
        """Call run() with model-supplied arguments, rejecting missing or extra fields."""
        if type(arguments) is not dict:
            raise ToolError('INVALID_SCHEMA', 'tool arguments must be an object')
        if any(value is None for value in arguments.values()):
            raise ToolError('INVALID_SCHEMA', 'omit optional tool arguments instead of using null')
        try:
            signature(self.run).bind(**arguments)
        except TypeError as exc:
            raise ToolError('INVALID_SCHEMA', 'invalid arguments for ' + self.name) from exc
        try:
            result = self.run(**arguments)
        except ContractError:
            raise
        except (ValueError, TypeError, ArithmeticError) as exc:
            raise ToolError('TOOL_EXECUTION_FAILED', 'tool failed: ' + self.name) from exc
        try:
            return copy_json(result)
        except ContractError as exc:
            raise ToolError('TOOL_RESULT_INVALID', 'tool must return finite JSON') from exc

    @abstractmethod
    def run(self, *positional: Any, **arguments: Any) -> Any:
        """Execute locally and return a JSON value, such as a number, list or object."""
        raise NotImplementedError


class PlatformTool(Tool):
    """Proxy one granted platform operation through the existing Agent client.

    The server validates permissions, time boundaries and financial rules. A proxy
    does not read platform storage or execute financial logic in the Agent process.
    """

    # Mutable account/state and financial operations require the financial owner.
    execution_route = 'owner'

    def __init__(self, client: ToolClient) -> None:
        self.client = client

    def input_schema(self, limits: dict) -> dict:
        schema = super().input_schema(limits)
        for field, limit in (
            ('limit', 'page_items'),
            ('order_limit', 'page_items'),
            ('levels', 'depth_levels'),
        ):
            if field in schema['properties']:
                schema['properties'][field]['maximum'] = limits[limit]
        return schema
