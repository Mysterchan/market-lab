"""Translate Responses model items into tool calls for the provider-neutral Agent loop."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Sequence, Set
from sim_platform.domain.json_values import copy_json
from sim_platform.domain.models import ContractError
from sim_platform.models.contracts import ProviderError
from sim_platform.agents.tools.protocol.errors import ToolError
from .conversation import INSTRUCTIONS

if TYPE_CHECKING:  # Annotation only.
    from sim_platform.agents.tools.client.base import Tool
    from .client import AgentClient
    from .conversation import Conversation


@dataclass(frozen=True)
class ToolCall:
    """A requested tool invocation; the original provider item stays in the conversation."""

    call_id: str
    name: str
    arguments_json: str

    def arguments(self) -> Dict[str, Any]:
        try:
            value = copy_json(json.loads(self.arguments_json))
        except (ValueError, RecursionError, TypeError, ContractError) as exc:
            raise ToolError('INVALID_JSON', 'tool arguments must be finite JSON') from exc
        if type(value) is not dict:
            raise ToolError('INVALID_SCHEMA', 'tool arguments must be an object')
        return value


def validate_response(response: Any, previous_call_ids: Set[str]) -> None:
    """Reject malformed/reused call identities before an attempt succeeds or tools run."""
    if type(response) is not dict or type(response.get('output')) is not list:
        raise ProviderError('FORMAT_ERROR')
    seen: Set[str] = set()
    for item in response['output']:
        if type(item) is not dict or item.get('type') not in (
            'message',
            'reasoning',
            'function_call',
        ):
            raise ProviderError('FORMAT_ERROR')
        if item.get('status') not in (None, 'completed'):
            raise ProviderError('FORMAT_ERROR')
        if item['type'] != 'function_call':
            continue
        identity = item.get('call_id')
        if (
            not isinstance(identity, str)
            or not identity
            or identity in seen
            or identity in previous_call_ids
            or not isinstance(item.get('name'), str)
            or not isinstance(item.get('arguments'), str)
        ):
            raise ProviderError('FORMAT_ERROR')
        seen.add(identity)


class ResponsesModel:
    """Keep provider payloads and function-call decoding out of TradingAgent.

    Model IO and attempts still use AgentClient and the existing Runtime policy.
    The adapter retains all original output items, including reasoning. It does
    not rerun tools, summarize context or decide when a task is finished.
    """

    def __init__(self, client: AgentClient) -> None:
        self.client = client

    @staticmethod
    def payload(
        conversation: Conversation, tools: Iterable[Tool], limits: Dict[str, Any]
    ) -> Dict[str, Any]:
        """The Responses request for the conversation and the tools' JSON schemas."""
        return dict(
            input=copy_json(conversation.items),
            instructions=INSTRUCTIONS,
            tools=[
                dict(
                    type='function',
                    name=tool.function_name,
                    description=tool.description,
                    parameters=tool.input_schema(limits),
                    strict=False,
                )
                for tool in tools
            ],
            parallel_tool_calls=False,
        )

    def respond(self, conversation: Conversation, tools: Sequence[Tool]) -> List[ToolCall]:
        """Ask the model once; keep its output in the conversation and return its tool calls."""

        def validate(value: Any) -> None:
            validate_response(value, conversation.call_ids)

        response = self.client.request_model(
            self.payload(conversation, tools, self.client.limits),
            validate_response=validate,
            label=getattr(conversation, 'label', None),
        )
        validate(response)
        conversation.accept_response(response['output'])
        return [
            ToolCall(item['call_id'], item['name'], item['arguments'])
            for item in response['output']
            if item['type'] == 'function_call'
        ]
