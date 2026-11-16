"""Run the built-in agent template locally with synthetic data and a fake model.

Run with `python -m sim_platform.examples.configured_agent_demo`. No API key or
network access is needed. The fake model asks for a quote, then finishes without
trading; the real Agent, Tools, Runtime and execution components handle the run.
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import time
from typing import TYPE_CHECKING, Any, Dict, List, Tuple, Union, cast

from sim_platform import Platform
from sim_platform.models.contracts import ModelProfile
from .market_demo import write_fixture

if TYPE_CHECKING:  # Annotation only.
    from sim_platform.research.agent_controller import AgentResearchController

# Starting cash for the demo account (money units).
ACCOUNT_CASH = 100_000_000
# How long the demo waits for one finished work round, and its poll interval.
ROUND_TIMEOUT_SECONDS = 3
POLL_SECONDS = 0.01


class QuoteOnlyProvider:
    """A scripted model for the demo; execution still uses the actual agent template.

    One instance serves one demo run; it asks for a quote, then finishes.
    """

    def __init__(self) -> None:
        self.calls = 0

    async def request(self, profile: ModelProfile, payload: Dict[str, Any], secret: str) -> Any:
        self.calls += 1
        name = 'market_quote' if self.calls == 1 else 'control_finish'
        arguments = '{"symbol":"AAPL"}' if self.calls == 1 else '{}'
        return dict(
            output=[
                dict(
                    type='function_call',
                    call_id='demo-' + str(self.calls),
                    name=name,
                    arguments=arguments,
                )
            ],
            usage={},
        )


def example_configuration() -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Return a saved agent definition and the caller's experiment settings.

    The definition selects the built-in template and a completion-based interval.
    Settings grant read/model/completion capabilities and define one short test.
    References label local demo resources; they do not claim a benchmark ranking.
    """
    grants = ['market.quote', 'control.finish', 'model.request']
    definition = dict(
        agent_id='quote-reader',
        revision='1',
        name='Quote reader',
        integration='configured',
        carrier='platform',
        model_profile_ref='demo-model',
        initial_prompt='Read the current quote, then finish. You do not need to trade.',
        parameters={},
        requires=grants,
        schedule=dict(
            mode='interval', interval_seconds='30', busy_event_policy='preserve_interval', events=[]
        ),
    )
    settings = dict(
        model_profile_revision='1',
        model_parameters={},
        granted_tools=grants,
        symbol='AAPL',
        start_ns='34202000000000',
        end_ns='34206000000000',
        speed='1x',
        source_manifest_ref='synthetic-demo',
        timeout_seconds='30',
        retry=dict(
            max_attempts=3,
            format_max_attempts=3,
            base_delay_seconds='1',
            max_delay_seconds='8',
            retry_classes=['transport', 'rate_limit', 'temporary_provider', 'timeout'],
        ),
        limits=dict(
            tool_request_bytes=65536,
            tool_response_bytes=65536,
            pending_requests=16,
            page_items=100,
            event_buckets=16,
            depth_levels=5,
            json_depth=32,
        ),
        lag=dict(threshold_seconds='2', window_seconds='5', profile_ref='demo-only'),
        support_profile_ref='demo-only',
    )
    return definition, settings


def run_demo(directory: Union[str, Path]) -> List[str]:
    """Prepare an experiment, finish one tool round, and return its archived tools."""
    directory = Path(directory)
    source, _ = write_fixture(directory)
    definition, settings = example_configuration()
    with Platform(directory / 'app') as app:
        profile = app.create_profile('Demo')
        account = app.create_account(profile, 'Cash', ACCOUNT_CASH)
        app.create_agent(profile, definition)
        experiment = app.open_experiment(
            profile,
            account,
            source=source,
            method='agent',
            agent_id=definition['agent_id'],
            revision=1,
            settings=settings,
            execution_mode='live',
        )
        app.set_model_credential('demo-key', 'unused-fake-key')
        # method 'agent' opens an AgentResearchController.
        app.configure_experiment_model(
            cast('AgentResearchController', experiment),
            ModelProfile('openai-responses', 'fake-model', 'demo-key'),
            provider=QuoteOnlyProvider(),
        )
        experiment.play()
        deadline = time.monotonic() + ROUND_TIMEOUT_SECONDS
        while True:
            work = experiment.runner.snapshot()['work']
            # Initial idle is not completion: finish must have scheduled the next interval.
            if work['state'] == 'idle' and work['due_ns'] is not None:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError('demo agent did not finish its work round')
            time.sleep(POLL_SECONDS)
        experiment.stop()
        archive = app.agent_log_archive(profile, experiment.run_id)
        tools = [r['request']['tool'] for r in archive['records'] if r['kind'] == 'tool']
        if tools != ['market.quote', 'control.finish']:
            raise RuntimeError('demo did not execute the expected tool round')
        experiment.close()
        return tools


def main() -> None:
    with TemporaryDirectory() as folder:
        print('Completed tools:', ', '.join(run_demo(folder)))


if __name__ == '__main__':
    main()
