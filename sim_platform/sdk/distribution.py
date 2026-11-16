"""Distribution identity and explicit client-only source inventory for the public SDK.

The version is Python package metadata, not an experiment or protocol marker.
The wheel has no third-party runtime dependency, App, ORM, Qt or native library.
"""

NAME = 'sim_platform_agent_sdk'
VERSION = '0.1.5'
FILENAME = f'{NAME}-{VERSION}-py3-none-any.whl'
DIRECTORIES = (
    'sdk',
    'agents/carrier',
    'agents/tools/client',
    'agents/hosting/processes',
    'models/providers',
)
MODULES = (
    '__init__',
    'domain/__init__',
    'domain/models',
    'domain/json_values',
    'agents/__init__',
    'agents/configuration',
    'agents/tools/__init__',
    'agents/tools/protocol/__init__',
    'agents/tools/protocol/contracts',
    'agents/tools/protocol/errors',
    'agents/hosting/__init__',
    'agents/hosting/client',
    'agents/hosting/protocol',
    'agents/hosting/worker',
    'agents/hosting/loader',
    'agents/hosting/lifecycle',
    'experiments/__init__',
    'experiments/scheduling/__init__',
    'experiments/scheduling/client',
    'experiments/scheduling/protocol',
    'experiments/scheduling/clock',
    'experiments/scheduling/signals',
    'experiments/scheduling/errors',
    'experiments/scheduling/policy/__init__',
    'experiments/scheduling/policy/conditions',
    'models/__init__',
    'models/agent',
    'models/contracts',
)


def identity() -> dict:
    return dict(name=NAME, version=VERSION)
