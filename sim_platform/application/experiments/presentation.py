"""Detached Agent presentation using committed accounts and immutable market reads.

No GUI, model or tool invocation occurs here. Financial queries refresh only
when the committed account sequence changes; market history is bounded.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any, Dict, List, Optional, cast

from sim_platform.domain.json_values import copy_json

if TYPE_CHECKING:  # Annotation only.
    from sim_platform.experiments.execution.session import ExperimentSession
    from ..operations import DatabaseOperations
    from .agent_driver import AgentPipelineOutput
    from .contracts import AgentBinding

# Most recent run events and trades shown, and one-minute bars.
EVENT_LIMIT = 100
TRADE_LIMIT = 100
BAR_LIMIT = 60


class AgentPresentation:
    """Builds the Agent experiment view from committed accounts and prepared market reads.

    The Agent runner holds one for the run; it re-reads run events only when the
    committed account sequence changes.
    """

    def __init__(
        self,
        operations: DatabaseOperations,
        profile: str,
        binding: AgentBinding,
        pipeline: ExperimentSession,
    ) -> None:
        self.operations, self.profile, self.pipeline = operations, profile, pipeline
        self.run = operations.run(profile, binding['run_id'], include_records=False)
        self._sequence: Optional[int] = None
        self._events: List[Dict[str, Any]] = []

    def __call__(self, state: Dict[str, Any]) -> Dict[str, Any]:
        execution = self.pipeline.driver.execution.snapshot
        if execution.account.sequence != self._sequence:
            self._events = self.operations.run_events(
                self.profile, execution.run_id, limit=EVENT_LIMIT
            )
            self._sequence = execution.account.sequence
        reader = self.pipeline.reader
        cutoff = min(state['pipeline']['target_ns'], reader.manifest['last_ns'])
        prefix = reader.prefix(cutoff)
        market = asdict(reader.snapshot_at(cutoff))
        market['status'] = {'running': 'playing', 'ready': 'ready', 'paused': 'paused'}.get(
            state['pipeline']['state'], state['pipeline']['state']
        )
        run = dict(self.run, status=execution.status)
        # Agent pipelines always deliver to an AgentPipelineOutput.
        sink = cast('AgentPipelineOutput', self.pipeline.channels.sink)
        return copy_json(
            dict(
                state,
                market=market,
                account=asdict(execution.account),
                execution=asdict(execution),
                run=run,
                source=cast(Dict[str, Any], run['config']).get('research_source'),
                preparation=None,
                summary=None,
                decisions=[],
                handoffs=[],
                trades=[
                    asdict(t) for t in reader.history.trades(prefix, cutoff, limit=TRADE_LIMIT)
                ],
                bars=[asdict(b) for b in reader.history.bars(prefix, cutoff, limit=BAR_LIMIT)],
                news=[],
                events=self._events,
                activity=sink.activity.snapshot(),
            )
        )
