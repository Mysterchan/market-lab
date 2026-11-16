"""Save an Agent and prepare/reuse its dependency environment without executing it."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from sim_platform import Platform

SOURCE = '''from sim_platform.sdk import TradingAgent


def create_agent(context):
    return TradingAgent(
        model=context.model,
        tools=context.tools.values(),
        instructions=context.settings['initial_prompt'],
    )
'''


# Seconds to wait for each environment preparation.
PREPARE_WAIT_SECONDS = 30


def main() -> None:
    """Import a tiny Agent project, then prepare its environment twice (reusing it)."""
    with TemporaryDirectory() as directory:
        root = Path(directory)
        source = root / 'strategy'
        source.mkdir()
        (source / 'agent.py').write_text(SOURCE)
        (source / 'agent.json').write_text(
            json.dumps({'python': f'{sys.version_info.major}.{sys.version_info.minor}'})
        )
        with Platform(root / 'data') as app:
            profile = app.create_profile('Agent projects')
            saved = app.import_agent(profile, source, name='My Agent')
            first = app.prepare_agent_environment(profile, saved['agent_id'])
            try:
                ready = first.wait(PREPARE_WAIT_SECONDS)
                if ready['state'] != 'ready':
                    raise RuntimeError(str(ready))
            finally:
                first.close()
            again = app.prepare_agent_environment(profile, saved['agent_id'])
            try:
                reused = again.wait(PREPARE_WAIT_SECONDS)
                if reused['state'] != 'ready' or not reused['reused']:
                    raise RuntimeError(str(reused))
            finally:
                again.close()
            print('Agent revision:', saved['revision'])
            print('Environment:', ready['state'])
            print('Reused:', reused['reused'])


if __name__ == '__main__':
    main()
