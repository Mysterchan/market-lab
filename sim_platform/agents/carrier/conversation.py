"""Keep the exact system, model and tool messages for one agent execution.

History stays inside the Agent process. A new work round appends its notification;
it does not clear the previous round. No automatic summary or truncation is applied.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set

from sim_platform.domain.json_values import copy_json, encode

if TYPE_CHECKING:  # Annotation only.
    from .model import ToolCall
    from .task import Task

INSTRUCTIONS = (
    'You are running inside a local trading experiment. Use only the provided platform '
    'functions to obtain market or account information and place orders. A work message '
    'announces a round; its times and notifications are not a current price quote. '
    'Registered local tools can perform computation without platform access. '
    'Use tools to read the information you need. You may use several tools or make no '
    'trade. Ordinary text does not end a round. Explicitly call control_finish or '
    'control_wait when finished; call either alone, never in a batch. Keep the original '
    'conversation across rounds. Respect tool errors and do not resend an order merely '
    'because the model or a later query failed. Tool outputs include platform request IDs '
    'and the actual data cutoff; use those values when inspecting results or waiting.'
)


class Conversation:
    """Build model inputs while retaining every accepted response and matching tool result."""

    def __init__(self, prompt: str, parameters: Dict[str, Any]) -> None:
        self.items: List[Dict[str, Any]] = [dict(role='system', content=prompt)]
        self.call_ids: Set[str] = set()
        # Optional role name recorded with this conversation's model usage.
        self.label: Optional[str] = None
        if parameters:
            self.items.append(dict(role='user', content='Agent parameters: ' + encode(parameters)))

    def start_task(self, task: Task) -> None:
        self.items.append(dict(role='user', content='Task: ' + task.instruction))
        if task.notification:
            self.items.append(
                dict(role='user', content='Work started: ' + encode(task.notification))
            )

    def accept_response(self, output: List[Dict[str, Any]]) -> None:
        """Keep every validated model item unchanged, including reasoning and call IDs."""
        self.items.extend(copy_json(output))
        self.call_ids.update(item['call_id'] for item in output if item['type'] == 'function_call')

    def tool_result(self, call: ToolCall, result: Any) -> None:
        self.items.append(
            dict(type='function_call_output', call_id=call.call_id, output=encode(result))
        )

    def continue_work(self) -> None:
        self.items.append(
            dict(
                role='user',
                content='This work round is still active. Use a tool, control_finish or control_wait.',
            )
        )
