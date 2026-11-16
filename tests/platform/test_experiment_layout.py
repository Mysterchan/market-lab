"""Keep process ownership discoverable and dependency checks tied to real packages."""

import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2] / 'sim_platform'


class ExperimentLayoutTests(unittest.TestCase):
    def test_removed_internal_packages_have_no_source_or_forwarders(self):
        for name in (
            'agents/runtime',
            'agents/tools/runtime',
            'decisions/runtime',
            'decisions/live',
            'execution/experiment',
            'execution/timeline',
        ):
            with self.subTest(package=name):
                self.assertEqual(list((ROOT / name).rglob('*.py')), [])

    def test_entrypoints_live_under_their_owning_function(self):
        locations = {
            'agents/hosting/host.py': ('AgentProcessHost',),
            'agents/hosting/worker.py': ('run_program',),
            'agents/hosting/client.py': ('AgentProcessClient',),
            'agents/hosting/bridge.py': ('ToolPipeBridge',),
            'agents/tools/worker/host.py': ('ToolProcessHost',),
            'agents/tools/worker/process.py': ('run_tools',),
            'agents/tools/server/router.py': ('AgentTools',),
            'experiments/scheduling/host.py': ('ExperimentRuntimeHandle',),
            'experiments/scheduling/worker.py': ('run_runtime',),
            'experiments/execution/session.py': ('ExperimentSession',),
            'decisions/agent/runner.py': ('AgentDecisionRunner',),
            'decisions/policy/live/runner.py': ('LivePolicyDecisionRunner',),
        }
        for name, expected in locations.items():
            nodes = ast.parse((ROOT / name).read_text()).body
            definitions = {n.name for n in nodes if isinstance(n, (ast.ClassDef, ast.FunctionDef))}
            self.assertTrue(set(expected) <= definitions, name)

    def test_child_entrypoints_do_not_import_app_hosts_or_composition(self):
        for name in (
            'agents/hosting/worker.py',
            'agents/tools/worker/process.py',
            'experiments/scheduling/worker.py',
        ):
            for node in ast.walk(ast.parse((ROOT / name).read_text())):
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ''
                    self.assertFalse(
                        module.startswith(('sim_platform.application', 'sim_platform.desktop')),
                        name,
                    )
                    self.assertNotIn('Host', ''.join(alias.name for alias in node.names), name)


if __name__ == '__main__':
    unittest.main()
