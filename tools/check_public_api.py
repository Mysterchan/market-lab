"""Check (or record) the frozen public API: names and signatures of every public object.

    python tools/check_public_api.py           # fail if the API differs from the record
    python tools/check_public_api.py --update  # record the current API (a reviewed change)

The record is ``tests/fixtures/public-api.json``. Removing or changing a recorded name or
signature is a breaking change: it needs a deprecation first (docs/reference/api-stability.md)
and a major version. Additions are allowed in minor versions; record them with --update.
"""

import argparse
import importlib
import inspect
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
RECORD = ROOT / 'tests/fixtures/public-api.json'
# Public modules and the objects in each that form the API.
PUBLIC = {
    'sim_platform': ['Platform', 'ContractError', '__version__'],
    'sim_platform.sdk': None,
    'sim_platform.models.contracts': ['ModelProfile', 'ProviderError', 'ModelProvider'],
    'sim_platform.models.providers.rehearsal': ['RehearsalProvider'],
    'sim_platform.models.providers.local': ['OpenAICompatibleChat'],
    'sim_platform.execution': ['ExecutionConfig'],
    'sim_platform.research.attribution': ['decision_timeline'],
    'sim_platform.research.pnl_attribution': ['pnl_attribution', 'regress'],
    'sim_platform.research.sweeps': ['expand_grid', 'heatmap', 'render_heatmap_svg'],
    'sim_platform.research.forward': ['ForwardTest', 'CsvFeed', 'BarFeed'],
    'sim_platform.research.portfolio': None,
    'sim_platform.research.overfitting': None,
    'sim_platform.research.metrics': None,
    'sim_platform.research.universe': None,
    'sim_platform.market.connectors': ['DataConnector', 'export_connector'],
    'sim_platform.network': ['offline', 'set_offline'],
    'sim_platform.diagnostics': ['configure_logging'],
}


def _signature(value):
    try:
        return str(inspect.signature(value))
    except (TypeError, ValueError):
        return None


def describe(value):
    """A comparable description of a public object: kind, signature, public members."""
    if inspect.isclass(value):
        members = {
            name: _signature(member)
            for name, member in inspect.getmembers(value)
            if not name.startswith('_')
            and (inspect.isfunction(member) or isinstance(member, property))
        }
        return dict(kind='class', signature=_signature(value), members=members)
    if callable(value):
        return dict(kind='function', signature=_signature(value))
    return dict(kind='value', type=type(value).__name__)


def current():
    api = {}
    for module_name, names in PUBLIC.items():
        module = importlib.import_module(module_name)
        if names is None:
            names = getattr(module, '__all__', None) or [
                n
                for n, v in vars(module).items()
                if not n.startswith('_')
                and (inspect.isfunction(v) or inspect.isclass(v))
                and getattr(v, '__module__', '') == module_name
            ]
        api[module_name] = {name: describe(getattr(module, name)) for name in sorted(names)}
    return api


def compare(recorded, now):
    """Breaking differences (removed or changed) and additions."""
    breaking, added = [], []
    for module, objects in recorded.items():
        for name, before in objects.items():
            after = now.get(module, {}).get(name)
            if after is None:
                breaking.append(f'removed {module}.{name}')
                continue
            if before.get('signature') != after.get('signature'):
                breaking.append(
                    f'changed {module}.{name}{before.get("signature")} -> {after.get("signature")}'
                )
            for member, signature in (before.get('members') or {}).items():
                new = (after.get('members') or {}).get(member, 'missing')
                if new == 'missing':
                    breaking.append(f'removed {module}.{name}.{member}')
                elif new != signature:
                    breaking.append(f'changed {module}.{name}.{member}{signature} -> {new}')
            for member in set(after.get('members') or {}) - set(before.get('members') or {}):
                added.append(f'{module}.{name}.{member}')
    for module, objects in now.items():
        for name in set(objects) - set(recorded.get(module, {})):
            added.append(f'{module}.{name}')
    return breaking, sorted(added)


if __name__ == '__main__':
    sys.path.insert(0, str(ROOT))
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--update', action='store_true')
    arguments = parser.parse_args()
    now = current()
    if arguments.update:
        RECORD.write_text(json.dumps(now, indent=1, sort_keys=True) + '\n')
        print(f'Recorded {sum(len(v) for v in now.values())} public objects in {RECORD}')
        raise SystemExit(0)
    recorded = json.loads(RECORD.read_text())
    breaking, added = compare(recorded, now)
    for line in added:
        print('added (record with --update):', line)
    for line in breaking:
        print('BREAKING:', line)
    raise SystemExit(1 if breaking or added else 0)
