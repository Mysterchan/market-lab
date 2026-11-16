"""Runtime policy bindings for persisted handoff schedules."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Dict, Optional, Tuple

from sim_platform.domain.models import ContractError
from sim_platform.decisions.policy.contracts import policy_descriptor
from sim_platform.decisions.policy.handoffs import condition_met

if TYPE_CHECKING:  # Annotation only.
    from sim_platform.decisions.policy.contracts import Policy
    from sim_platform.decisions.policy.handoffs import SwitchRecord
    from sim_platform.decisions.policy.models import DecisionConfig, DecisionJournal
    from sim_platform.execution.models import ExecutionSnapshot

    # The live objects behind one pending switch: (target maker, policy, config, state mode).
    Binding = Tuple[str, Policy, DecisionConfig, str]


class HandoffSchedule:
    """In-memory policy objects for the journal's pending scheduled switches.

    The journal (persisted) is the source of truth for which switches are pending; this
    object only keeps the matching live Policy instances, keyed by switch id. The
    policy runner owns it and clears it when its execution closes.
    """

    def __init__(self) -> None:
        self._policies: Dict[int, Binding] = {}

    def clear(self) -> None:
        self._policies.clear()

    def reconcile(self, journal: DecisionJournal) -> None:
        """Drop bindings whose switch is no longer pending in the journal."""
        pending = {
            item['id'] for item in journal.get('switches', []) if item['status'] == 'pending'
        }
        self._policies = {key: value for key, value in self._policies.items() if key in pending}

    def register(
        self,
        record: SwitchRecord,
        maker: str,
        policy: Policy,
        config: DecisionConfig,
        state_mode: str,
    ) -> None:
        self._policies[record['id']] = (maker, policy, config, state_mode)

    def resume_bindings(self, journal: DecisionJournal, bindings: Dict[str, Policy]) -> None:
        """Replace every pending switch's policy with the caller's compatible binding."""
        self.reconcile(journal)
        replacements = dict(self._policies)
        for item in journal.get('switches', []):
            if item['status'] != 'pending':
                continue
            if item['id'] not in replacements:
                raise ContractError('scheduled policy has no runtime binding')
            target, adapter, config, mode = replacements[item['id']]
            selected = bindings.get(target)
            if selected is None or policy_descriptor(selected) != policy_descriptor(adapter):
                raise ContractError('supply compatible pending handover policy bindings')
            replacements[item['id']] = (target, selected, config, mode)
        self._policies = replacements

    def due(
        self, journal: DecisionJournal, snapshot: Callable[[], ExecutionSnapshot]
    ) -> Optional[Tuple[SwitchRecord, Binding]]:
        """Return the first pending switch whose condition holds, with its binding."""
        self.reconcile(journal)
        for switch in journal.get('switches', []):
            if switch['status'] == 'pending' and condition_met(switch['condition'], snapshot()):
                binding = self._policies.get(switch['id'])
                if binding is None:
                    raise ContractError('scheduled policy has no runtime binding')
                if policy_descriptor(binding[1]) != switch['policy']:
                    raise ContractError('scheduled policy changed after registration')
                return switch, binding
        return None
