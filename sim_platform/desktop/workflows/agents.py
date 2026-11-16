"""Saved Agent management; imports and environment jobs remain Platform-owned."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional
from uuid import uuid4
from sim_platform.domain.models import ContractError
from .configuration import schedule

if TYPE_CHECKING:  # Annotation only.
    from sim_platform.application.platform import Platform
    from sim_platform.research.jobs import Cancellation

# The Platform's Agent listing page size; a shorter page is the last one.
PAGE_SIZE = 100
# Saved model reference for a local model server that needs no stored key.
LOCAL_MODEL_REF = 'local-model'


def with_reference_agent(values: Dict[str, Any]) -> Dict[str, Any]:
    """Fill a new Agent's source, tools and parameters from a chosen reference Agent.

    The form's own choices win: its parameters override the reference defaults, its
    tools are kept and the reference's required tools are added. A source path the user
    typed is used instead of the reference.
    """
    key = values.get('reference')
    if not key or values.get('source') or values.get('expected_revision') is not None:
        return values
    from sim_platform.reference_agents import reference_agent

    entry = reference_agent(str(key))
    requires = list(values['requires'])
    requires += [tool for tool in entry['requires'] if tool not in requires]
    return dict(
        values,
        source=entry['path'],
        name=values.get('name') or entry['name'],
        integration='python_strategy',
        requires=requires,
        parameters=dict(entry['parameters'], **values['parameters']),
    )


class AgentLibrary:
    """Desktop listing and saving of Agents through the Platform; holds no state."""

    def __init__(self, platform: Platform) -> None:
        self.platform = platform

    def catalog(self, profile: str) -> Dict[str, Any]:
        """Every saved Agent of the profile with its environment status."""
        rows: List[Dict[str, Any]] = []
        after: Optional[str] = None
        while True:
            page = self.platform.agents(profile, after_id=after)
            for item in page:
                value = dict(item)
                value['environment'] = (
                    (
                        'Ready'
                        if self.platform.agent_environment(
                            profile, item['agent_id'], item['revision']
                        )
                        else 'Not prepared'
                    )
                    if item['definition'].get('package')
                    else 'Built in'
                )
                rows.append(value)
            if len(page) < PAGE_SIZE:
                return dict(profile=profile, agents=rows)
            after = page[-1]['agent_id']

    def save(self, *, profile: str, values: Dict[str, Any], cancellation: Cancellation) -> Any:
        """Create or update an Agent from the desktop form (runs as a background job)."""
        if cancellation.cancelled:
            raise ContractError('Agent save cancelled')
        expected = values.get('expected_revision')
        identity = values.get('agent_id') or uuid4().hex
        values = with_reference_agent(values)
        parameters = dict(values['parameters'])
        parameters['desktop_model'] = values['model']
        requires = values['requires']
        mode = values['integration']
        timing = (
            None
            if mode == 'python_carrier'
            else schedule(
                values['schedule_mode'],
                values['interval_seconds'],
                values['busy_policy'],
                values['events'],
            )
        )
        if mode != 'configured':
            if not values['source']:
                if expected is None:
                    raise ContractError(
                        'Choose a Python file/folder; local tools belong in that source'
                    )
                definition = self.platform.agent(profile, identity, expected)['definition']
                definition.update(
                    name=values['name'],
                    initial_prompt=values['prompt'],
                    parameters=parameters,
                    requires=requires,
                    model_profile_ref=values['model']['credential_ref'] or LOCAL_MODEL_REF,
                    revision=str(expected + 1),
                )
                if timing is not None:
                    definition['schedule'] = timing
                return self.platform.update_agent(profile, identity, expected, definition)
            return self.platform.import_agent(
                profile,
                values['source'],
                name=values['name'],
                agent_id=identity,
                expected_revision=expected,
                integration=mode,
                initial_prompt=values['prompt'],
                parameters=parameters,
                requires=requires,
                model_profile_ref=values['model']['credential_ref'] or LOCAL_MODEL_REF,
                schedule=timing,
            )
        definition = dict(
            agent_id=identity,
            revision=str((expected or 0) + 1),
            name=values['name'],
            integration='configured',
            carrier='platform',
            initial_prompt=values['prompt'],
            parameters=parameters,
            requires=requires,
            schedule=timing,
            model_profile_ref=values['model']['credential_ref'] or LOCAL_MODEL_REF,
        )
        if expected is None:
            return self.platform.create_agent(profile, definition)
        return self.platform.update_agent(profile, identity, expected, definition)
