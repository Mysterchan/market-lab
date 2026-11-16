"""Run a whole experiment from one configuration, without the Desktop.

The command line (``market-lab experiment --config run.json``) and scripts use this to
do what the Desktop's New experiment form does: create an account, open the
experiment, configure the model, play it to the end and report the result with its
lineage check. A configuration is plain JSON:

.. code-block:: json

    {
      "profile_name": "Research",
      "cash_usd": 100000,
      "source": "/data/aapl.csv",
      "method": "agent",
      "execution_mode": "live",
      "agent_id": "my-agent", "revision": 1,
      "settings": {"symbol": "AAPL", "speed": "turn", "budget": {"max_tokens": 200000}},
      "execution": {"fee_per_share": 5, "latency_ns": 1000000},
      "model": {"provider": "openai-compatible", "model": "qwen2.5:7b",
                "base_url": "http://127.0.0.1:11434/v1"}
    }

Fields: ``profile`` (an ID) or ``profile_name`` (found or created); ``cash_usd`` or
``cash`` (money units); ``source`` (path or list); ``method`` ('rule', 'model' or
'agent'); ``execution_mode`` ('controlled' or 'live'); ``settings``; ``execution``
(ExecutionConfig fields) or ``execution_preset`` ('paper' or 'strict'); ``allow_short``;
``book``; for Agents ``agent_id``, ``revision``, ``model`` (ModelProfile fields) and
``credential`` (``{"reference", "file", "variable"}`` to load a key from a .env file,
or ``{"reference", "environment"}`` to read it from an environment variable), and
``rehearsal`` (a symbol: answer with RehearsalProvider, no model calls);
``timeout_seconds`` bounds the wait (default one day).
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, Mapping, Optional

from sim_platform.domain.models import ContractError

# Money units per US dollar (prices and cash are integers in ten-thousandths).
UNITS_PER_USD = 10_000
# How often a Live or Agent experiment is serviced while it plays.
POLL_SECONDS = 0.02
# Default bound on one experiment's wait, in seconds.
DEFAULT_TIMEOUT_SECONDS = 86_400
FIELDS = frozenset(
    (
        'profile',
        'profile_name',
        'cash',
        'cash_usd',
        'account_name',
        'source',
        'method',
        'execution_mode',
        'settings',
        'execution',
        'execution_preset',
        'allow_short',
        'book',
        'agent_id',
        'revision',
        'model',
        'credential',
        'rehearsal',
        'timeout_seconds',
    )
)


def _profile(platform: Any, config: Mapping[str, Any]) -> str:
    if config.get('profile'):
        return str(config['profile'])
    name = config.get('profile_name') or 'Research'
    for profile in platform.profiles():
        if profile['name'] == name:
            return str(profile['id'])
    return str(platform.create_profile(name))


def _execution(config: Mapping[str, Any]) -> Any:
    from sim_platform.execution import ExecutionConfig

    if config.get('execution_preset'):
        if config.get('execution'):
            raise ContractError('give execution or execution_preset, not both')
        from sim_platform.reference_agents import execution_preset

        return execution_preset(config['execution_preset'])
    return ExecutionConfig(**(config.get('execution') or {}))


def _credential(platform: Any, config: Mapping[str, Any]) -> None:
    credential = config.get('credential')
    if not credential:
        return
    reference = credential.get('reference')
    if credential.get('file'):
        platform.load_model_credential(
            reference, credential['file'], variable=credential.get('variable', 'OPENAI_API_KEY')
        )
    elif credential.get('environment'):
        secret = os.environ.get(credential['environment'])
        if not secret:
            raise ContractError(f'environment variable {credential["environment"]} is not set')
        platform.set_model_credential(reference, secret)
    else:
        raise ContractError('credential needs a file or an environment variable')


def _wait(experiment: Any, timeout: float, agent: bool, controlled: bool) -> None:
    """Play or step the experiment until it ends (and an Agent's log is archived)."""
    deadline = time.monotonic() + timeout
    if controlled and not agent:
        while experiment.runner.active:
            experiment.step()
            if time.monotonic() > deadline:
                experiment.stop()
                raise ContractError('experiment did not finish before its timeout')
        return
    experiment.play()
    while experiment.runner.active or (agent and not experiment.runner.archive_ready):
        experiment.tick(render=False)
        time.sleep(POLL_SECONDS)
        if time.monotonic() > deadline:
            experiment.stop()
            raise ContractError('experiment did not finish before its timeout')
    experiment.tick(render=False)


def run_experiment(platform: Any, config: Mapping[str, Any]) -> Dict[str, Any]:
    """Create an account, run one experiment to its end and report it.

    Returns ``{'profile', 'account', 'run_id', 'status', 'summary', 'verified'}`` where
    ``verified`` is the run's lineage check (``Platform.verify_run``).
    """
    unknown = set(config) - FIELDS
    if unknown:
        raise ContractError('unknown experiment fields: ' + ', '.join(sorted(unknown)))
    if 'source' not in config or 'method' not in config:
        raise ContractError('an experiment needs source and method')
    method = config['method']
    agent = method == 'agent'
    profile = _profile(platform, config)
    cash = (
        int(round(float(config['cash_usd']) * UNITS_PER_USD))
        if 'cash_usd' in config
        else int(config.get('cash', 100_000 * UNITS_PER_USD))
    )
    account = platform.create_account(profile, config.get('account_name', 'Experiment'), cash)
    options: Dict[str, Any] = dict(
        source=config['source'],
        method=method,
        settings=config.get('settings'),
        execution_config=_execution(config),
        execution_mode=config.get('execution_mode', 'live' if agent else 'controlled'),
    )
    if agent:
        options.update(agent_id=config['agent_id'], revision=int(config['revision']))
    else:
        options.update(allow_short=bool(config.get('allow_short', False)), book=config.get('book'))
    experiment = platform.open_experiment(profile, account, **options)
    try:
        if agent:
            from sim_platform.models.contracts import ModelProfile

            _credential(platform, config)
            provider: Optional[Any] = None
            if config.get('rehearsal'):
                from sim_platform.models.providers.rehearsal import RehearsalProvider

                provider = RehearsalProvider(str(config['rehearsal']))
            platform.configure_experiment_model(
                experiment, ModelProfile(**(config.get('model') or {})), provider=provider
            )
        _wait(
            experiment,
            float(config.get('timeout_seconds', DEFAULT_TIMEOUT_SECONDS)),
            agent,
            options['execution_mode'] == 'controlled',
        )
        report = experiment.report()
        run_id = experiment.run_id
    finally:
        experiment.close()
    proof = platform.verify_run(profile, run_id)
    return dict(
        profile=profile,
        account=account,
        run_id=run_id,
        status=report['run']['status'],
        summary=report['summary'],
        verified=dict(unchanged=proof['unchanged'], sealed=proof['sealed']),
    )
