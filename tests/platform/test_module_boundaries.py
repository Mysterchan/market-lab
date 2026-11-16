"""Dependency direction and import isolation for the expanded package layout."""

import ast
import importlib.util
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]


class ModuleBoundaries(unittest.TestCase):
    def test_package_initializers_keep_core_and_optional_ui_imports_isolated(self):
        program = '''
import sys
import sim_platform.agents
import sim_platform.agents.configuration
import sim_platform.application.use_cases
import sim_platform.application.experiments
import sim_platform.market.prepared
import sim_platform.experiments.execution.timeline
import sim_platform.experiments.execution
import sim_platform.experiments.scheduling
import sim_platform.experiments.scheduling.policy
import sim_platform.agents.hosting
import sim_platform.decisions.agent
import sim_platform.agents.tools.worker
import sim_platform.agents.tools.protocol
import sim_platform.agents.tools.reads
import sim_platform.agents.tools.trading
import sim_platform.agents.tools.server
import sim_platform.persistence.sql
import sim_platform.persistence.sql.repositories
import sim_platform.persistence.sql.migrations
import sim_platform.desktop
import sim_platform.desktop.web
import sim_platform.desktop.workflows
'''
        # Import only package roots: SQL composition above legitimately loads ORM.
        # The UI roots must still remain inert and never bring Qt into the core.
        program += "assert not any(name.startswith('PySide6') for name in sys.modules)\n"
        subprocess.run([sys.executable, '-c', program], cwd=ROOT, check=True)

    def test_subpackages_follow_their_responsibility_boundaries(self):
        rules = {
            'market/prepared': (
                'sim_platform.application',
                'sim_platform.execution',
                'sim_platform.persistence',
                'sim_platform.agents',
                'PySide6',
            ),
            'experiments/execution/timeline': (
                'sim_platform.application',
                'sim_platform.persistence',
                'sim_platform.agents',
                'PySide6',
            ),
            'experiments/scheduling': (
                'sim_platform.agents',
                'sim_platform.market',
                'sim_platform.execution',
                'sim_platform.persistence',
                'sim_platform.application',
                'sqlalchemy',
                'PySide6',
            ),
            'agents/tools/reads': (
                'sim_platform.agents.tools.trading',
                'sim_platform.agents.tools.server',
            ),
            'models': (
                'sim_platform.agents',
                'sim_platform.market',
                'sim_platform.execution',
                'sim_platform.persistence',
                'sim_platform.application',
                'PySide6',
            ),
            'decisions/policy/live': (
                'sim_platform.agents',
                'sim_platform.application',
                'sim_platform.persistence',
                'sqlalchemy',
                'PySide6',
            ),
            'agents': (
                'sqlalchemy',
                'sqlite3',
                'PySide6',
                'sim_platform.application',
                'sim_platform.persistence',
                'sim_platform.desktop',
            ),
            'application/use_cases': (
                'sqlalchemy',
                'sqlite3',
                'PySide6',
                'sim_platform.persistence',
                'sim_platform.desktop',
            ),
            'persistence': (
                'PySide6',
                'sim_platform.application',
                'sim_platform.desktop',
                'sim_platform.cli',
                'sim_platform.agents',
            ),
            'desktop/workflows': (
                'PySide6',
                'sim_platform.persistence',
                'sqlalchemy',
            ),
            'desktop/web': (
                'sqlalchemy',
                'sqlite3',
                'sim_platform.market',
            ),
        }
        for directory, forbidden in rules.items():
            paths = list((ROOT / 'sim_platform' / directory).rglob('*.py'))
            self.assertTrue(paths, 'boundary check must cover real source: ' + directory)
            for path in paths:
                relative = path.relative_to(ROOT).with_suffix('')
                module = '.'.join(relative.parts)
                package = module.rpartition('.')[0]
                for node in ast.walk(ast.parse(path.read_text())):
                    if isinstance(node, ast.Import):
                        imports = [alias.name for alias in node.names]
                    elif isinstance(node, ast.ImportFrom):
                        target = node.module or ''
                        if node.level:
                            target = importlib.util.resolve_name('.' * node.level + target, package)
                        imports = [target]
                    else:
                        continue
                    with self.subTest(path=path.relative_to(ROOT), line=node.lineno):
                        self.assertFalse(any(name.startswith(forbidden) for name in imports))


if __name__ == '__main__':
    unittest.main()
