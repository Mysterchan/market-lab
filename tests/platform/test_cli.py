"""CLI boundary contracts: dispatch, resources, output, and failures."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
# Absolute on every OS (UNUSED is not absolute on Windows); never created.
UNUSED = str(Path(tempfile.gettempdir()) / 'market-lab-unused')
DEFAULT = Path(tempfile.gettempdir()) / 'market-lab-default'
sys.path.insert(0, str(ROOT))

from sim_platform.cli.main import main, parser
from sim_platform.domain.models import ContractError


class CliTests(unittest.TestCase):
    def invoke(self, *argv):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = main(list(argv))
        return status, stdout.getvalue(), stderr.getvalue()

    def test_version_exits_without_opening_platform(self):
        output = io.StringIO()
        with patch('sim_platform.cli.context.Platform') as platform:
            with redirect_stdout(output), self.assertRaises(SystemExit) as result:
                main(['--version'])
        self.assertEqual(result.exception.code, 0)
        self.assertEqual(output.getvalue(), 'Market Lab 1.0.0\n')
        platform.assert_not_called()

    def test_path_reports_explicit_directory_without_creating_it(self):
        with TemporaryDirectory() as temporary:
            directory = Path(temporary) / 'not-created'
            with patch('sim_platform.cli.context.Platform') as platform:
                status, stdout, stderr = self.invoke('--data-dir', str(directory), 'path')
            self.assertEqual(status, 0)
            self.assertEqual(stderr, '')
            self.assertEqual(
                json.loads(stdout),
                {
                    'data_directory': str(directory),
                    'database': str(directory / 'platform.sqlite'),
                },
            )
            self.assertFalse(directory.exists())
            platform.assert_not_called()

    def test_path_resolves_default_directory_without_opening_platform(self):
        with (
            patch('sim_platform.cli.context.data_directory', return_value=DEFAULT),
            patch('sim_platform.cli.context.Platform') as platform,
        ):
            status, stdout, stderr = self.invoke('path')
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(stdout)['data_directory'], str(DEFAULT))
        self.assertEqual(stderr, '')
        platform.assert_not_called()

    def test_relative_directory_is_an_expected_error(self):
        with patch('sim_platform.cli.context.Platform') as platform:
            status, stdout, stderr = self.invoke('--data-dir', 'relative', 'profiles')
        self.assertEqual(status, 1)
        self.assertEqual(stdout, '')
        self.assertEqual(stderr, 'Platform error: data directory must be absolute\n')
        platform.assert_not_called()

    def test_comparison_still_validates_global_directory(self):
        with patch('sim_platform.research.batch.compare') as compare:
            status, stdout, stderr = self.invoke(
                '--data-dir',
                'relative',
                'compare',
                '--input',
                'unused.bin',
            )
        self.assertEqual(status, 1)
        self.assertEqual(stdout, '')
        self.assertIn('data directory must be absolute', stderr)
        compare.assert_not_called()

    def test_account_handler_closes_platform_on_expected_failure(self):
        with patch('sim_platform.cli.context.Platform') as factory:
            app = factory.return_value
            app.create_account.side_effect = ContractError('unknown profile')
            status, stdout, stderr = self.invoke(
                '--data-dir',
                UNUSED,
                'create-account',
                '--profile',
                'missing',
                '--name',
                'Account',
                '--cash',
                '100.0001',
            )
        self.assertEqual(status, 1)
        self.assertEqual(stdout, '')
        self.assertEqual(stderr, 'Platform error: unknown profile\n')
        app.create_account.assert_called_once_with('missing', 'Account', 1_000_001)
        factory.return_value.close.assert_called_once()

    def test_market_owns_stream_output_and_exit_code_without_storage(self):
        def run_market(args, context):
            print('{"event": 1}')
            print('{"event": 2}')
            return 7

        with (
            patch('sim_platform.cli.commands.market.session.run_market', side_effect=run_market),
            patch('sim_platform.cli.context.data_directory') as directory,
            patch('sim_platform.cli.context.Platform') as platform,
        ):
            status, stdout, stderr = self.invoke('market', '--input', 'unused.bin')
        self.assertEqual(status, 7)
        self.assertEqual(stdout, '{"event": 1}\n{"event": 2}\n')
        self.assertEqual(stderr, '')
        directory.assert_not_called()
        platform.assert_not_called()

    def test_desktop_delegates_optional_launch_without_resolving_storage(self):
        with (
            patch('sim_platform.desktop.app.launch', return_value=4) as launch,
            patch('sim_platform.cli.context.data_directory') as directory,
        ):
            status, stdout, stderr = self.invoke('desktop', '--demo', '--library', 'reader.so')
        self.assertEqual((status, stdout, stderr), (4, '', ''))
        launch.assert_called_once_with(None, Path('reader.so'), True)
        directory.assert_not_called()

    def test_desktop_has_no_classic_entry(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as result:
            parser().parse_args(['desktop', '--classic'])
        self.assertEqual(result.exception.code, 2)

    def test_decision_options_do_not_leak_into_execution(self):
        arguments = ['--profile', 'p', '--account', 'a', '--maker', 'm', '--input', 'source.bin']
        decision = parser().parse_args(['decision', *arguments, '--policy', 'human'])
        self.assertEqual(decision.policy, 'human')
        execution = parser().parse_args(['execution', *arguments])
        self.assertFalse(hasattr(execution, 'policy'))
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            parser().parse_args(['execution', *arguments, '--policy', 'human'])
        self.assertEqual(error.exception.code, 2)

    def test_invalid_arguments_keep_argparse_exit_code(self):
        with redirect_stderr(io.StringIO()) as stderr, self.assertRaises(SystemExit) as error:
            main(['create-account', '--profile', 'p', '--name', 'A', '--cash', '0.00001'])
        self.assertEqual(error.exception.code, 2)
        self.assertIn('at most four decimal places', stderr.getvalue())
        self.assertNotIn('Platform error:', stderr.getvalue())

    def test_unexpected_errors_propagate_for_debugging(self):
        with patch('sim_platform.cli.context.Platform') as factory:
            app = factory.return_value
            app.profiles.side_effect = RuntimeError('programming error')
            with self.assertRaisesRegex(RuntimeError, 'programming error'):
                self.invoke('--data-dir', UNUSED, 'profiles')
        factory.return_value.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
