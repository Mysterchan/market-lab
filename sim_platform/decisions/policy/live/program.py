"""Trusted Rule carrier: detached observation, compute, then owner-side commit."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Dict

from sim_platform.domain.json_values import copy_json
from sim_platform.decisions.policy.invocation import invoke

if TYPE_CHECKING:  # Annotation only.
    from sim_platform.decisions.policy.contracts import Policy
    from sim_platform.decisions.policy.models import DecisionConfig


def observation(value: Any) -> Any:
    """Rebuild attribute access over observation JSON: objects become namespaces, lists tuples.

    The result stands in for an Observation inside the Rule child, so policies can read
    ``observation.market.bids`` as they do in Controlled mode.
    """
    if isinstance(value, dict):
        return SimpleNamespace(**{k: observation(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(observation(v) for v in value)
    return value


class TrustedPolicyProgram:
    """The Rule child's main loop: wait for work, observe, decide, then ask to commit.

    Runs inside the decision child started by the Agent process host; it holds only the
    policy and its config. Financial effects happen on the App owner via ``commit``
    tool requests. The loop ends when the host stops the child.
    """

    def __init__(self, policy: Policy, config: DecisionConfig) -> None:
        self.policy, self.config = policy, config

    def __call__(self, client: Any, configuration: Dict[str, Any]) -> None:
        """Run forever; ``client`` is the hosted child's tool/work client (agents package)."""
        count = 0
        while True:
            work = client.next_work()
            count += 1
            captured = client.request_tool(
                dict(request_id='observe-' + str(count), operation='observe')
            )
            source = observation(captured['observation'])
            source.to_json = lambda value=captured['observation']: copy_json(value)
            response = invoke(self.policy, source, captured['state'], self.config)
            client.request_tool(
                dict(
                    request_id='commit-' + str(count),
                    operation='commit',
                    observation_id=captured['observation_id'],
                    response=response,
                )
            )
            client.work_client.finish(work)
