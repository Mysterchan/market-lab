"""Headless study commands: Agents, credentials, experiments, evaluations and reviews.

Every Desktop research workflow has a command here, so a study can run from scripts,
CI or a server. Configurations are JSON files; results are printed as JSON. Secrets
are never taken from arguments: a key comes from a .env file, an environment variable
or standard input.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import TYPE_CHECKING, Any, Dict, List

from sim_platform.domain.models import ContractError

from ..context import CliContext
from ..output import emit_json

if TYPE_CHECKING:  # Annotation only.
    from ..arguments import Subcommands


def _read_json(path: str) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except ValueError as exc:
        raise ContractError(f'{path} is not valid JSON: {exc}') from None


def _write(path: str, text: str) -> None:
    from sim_platform.persistence.export_files import atomic_write

    atomic_write(Path(path).expanduser().resolve(), lambda stream: stream.write(text))


# ------------------------------------------------------------------------ Agents


def agents(args: argparse.Namespace, context: CliContext) -> int:
    with context.open_platform() as app:
        if args.reference_list:
            return emit_json(app.reference_agents())
        if not args.profile:
            raise ContractError('give --profile, or --reference-list')
        return emit_json(app.agents(args.profile))


def agent_import(args: argparse.Namespace, context: CliContext) -> int:
    with context.open_platform() as app:
        if args.reference:
            result = app.import_reference_agent(args.profile, args.reference)
        elif args.definition:
            result = app.create_agent(args.profile, _read_json(args.definition))
        else:
            options: Dict[str, Any] = dict(integration=args.integration)
            if args.name:
                options['name'] = args.name
            if args.prompt:
                options['initial_prompt'] = args.prompt
            result = app.import_agent(args.profile, args.source, **options)
    return emit_json(result)


def credential(args: argparse.Namespace, context: CliContext) -> int:
    with context.open_platform() as app:
        if args.file:
            app.load_model_credential(
                args.reference, args.file, variable=args.variable, persistence='os'
            )
        else:
            secret = sys.stdin.readline().strip()
            app.set_model_credential(args.reference, secret, persistence='os')
    return emit_json(dict(reference=args.reference, stored='os'))


# ------------------------------------------------------------------- Experiments


def experiment(args: argparse.Namespace, context: CliContext) -> int:
    from sim_platform.research.headless import run_experiment

    with context.open_platform() as app:
        result = run_experiment(app, _read_json(args.config))
    emit_json(result)
    return 0 if result['status'] in ('completed', 'stopped') else 1


def evaluate_runs(args: argparse.Namespace, context: CliContext) -> int:
    entries: Dict[str, List[str]] = {}
    for text in args.entry:
        name, _, runs = text.partition('=')
        if not name or not runs:
            raise ContractError('give each entry as NAME=RUN_ID[,RUN_ID...]')
        entries[name] = [r for r in runs.split(',') if r]
    with context.open_platform() as app:
        result = app.evaluation(
            args.profile, entries, baseline=args.baseline, metric=args.metric, seed=args.seed
        )
    if args.output:
        _write(args.output, json.dumps(result, indent=2) + '\n')
    return emit_json(result)


def suite(args: argparse.Namespace, context: CliContext) -> int:
    config = _read_json(args.config)
    paths, entries = config.pop('recordings'), config.pop('entries')
    with context.open_platform() as app:
        with app.open_batch() as batch:
            result = batch.evaluate_suite(paths, entries, **config)
    if args.output:
        _write(args.output, json.dumps(result, indent=2) + '\n')
    return emit_json(result)


def sweep(args: argparse.Namespace, context: CliContext) -> int:
    config = _read_json(args.config)
    paths, base, grid = config.pop('recordings'), config.pop('base'), config.pop('grid')
    with context.open_platform() as app:
        result: Dict[str, Any] = dict(app.run_sweep(paths, base, grid, **config))
    if args.heatmap:
        from sim_platform.research.sweeps import heatmap, render_heatmap_svg

        x, y, target = args.heatmap
        _write(target, render_heatmap_svg(heatmap(result['rows'], x, y), title=result['metric']))
    if args.output:
        _write(args.output, json.dumps(result, indent=2) + '\n')
    return emit_json(dict(result, suites=f'{len(result["suites"])} suite(s)'))


def disguise(args: argparse.Namespace, context: CliContext) -> int:
    with context.open_platform() as app:
        result = app.disguise_recording(args.source, args.output, seed=args.seed)
    return emit_json(result)


def split(args: argparse.Namespace, context: CliContext) -> int:
    with context.open_platform() as app:
        result = app.split_recording(
            args.source,
            args.folder,
            train_days=args.train_days,
            test_days=args.test_days,
            gap_days=args.gap_days,
            anchored=args.anchored,
        )
    return emit_json(result)


# ---------------------------------------------------------------- Run reviews


def lineage(args: argparse.Namespace, context: CliContext) -> int:
    with context.open_platform() as app:
        return emit_json(app.run_lineage(args.profile, args.run))


def verify(args: argparse.Namespace, context: CliContext) -> int:
    with context.open_platform() as app:
        result = app.verify_run(args.profile, args.run)
    emit_json(result)
    return 0 if result['unchanged'] else 1


def diff(args: argparse.Namespace, context: CliContext) -> int:
    with context.open_platform() as app:
        return emit_json(app.diff_runs(args.profile, args.run_a, args.run_b))


def timeline(args: argparse.Namespace, context: CliContext) -> int:
    with context.open_platform() as app:
        return emit_json(app.decision_timeline(args.profile, args.run))


def attribution(args: argparse.Namespace, context: CliContext) -> int:
    factors = _read_json(args.factors) if args.factors else None
    with context.open_platform() as app:
        return emit_json(app.pnl_attribution(args.profile, args.run, factors=factors))


def risk_report(args: argparse.Namespace, context: CliContext) -> int:
    with context.open_platform() as app:
        app.model_risk_report(
            args.profile, args.runs, path=args.output, title=args.title, baseline=args.baseline
        )
    return emit_json(dict(written=str(Path(args.output).expanduser().resolve())))


def _profile_run(command: argparse.ArgumentParser) -> None:
    command.add_argument('--profile', required=True)
    command.add_argument('--run', required=True)


def register(commands: Subcommands) -> None:
    command = commands.add_parser('agents', help='list saved or reference Agents')
    command.add_argument('--profile')
    command.add_argument('--reference-list', action='store_true')
    command.set_defaults(handler=agents)

    command = commands.add_parser('agent-import', help='save an Agent from source or JSON')
    command.add_argument('--profile', required=True)
    group = command.add_mutually_exclusive_group(required=True)
    group.add_argument('--source', help='Python file or project folder')
    group.add_argument('--definition', help='JSON file of a configured Agent definition')
    group.add_argument('--reference', help='reference Agent key, see agents --reference-list')
    command.add_argument('--name')
    command.add_argument('--prompt')
    command.add_argument('--integration', default='python_strategy')
    command.set_defaults(handler=agent_import)

    command = commands.add_parser(
        'credential', help='store a model key in the OS store (from a file or stdin)'
    )
    command.add_argument('--reference', required=True)
    command.add_argument('--file', help='.env file; otherwise one line is read from stdin')
    command.add_argument('--variable', default='OPENAI_API_KEY')
    command.set_defaults(handler=credential)

    command = commands.add_parser('experiment', help='run one experiment from a JSON config')
    command.add_argument('--config', required=True)
    command.set_defaults(handler=experiment)

    command = commands.add_parser('evaluate-runs', help='compare saved runs as entries')
    command.add_argument('--profile', required=True)
    command.add_argument('--entry', action='append', required=True, help='NAME=RUN,RUN')
    command.add_argument('--baseline')
    command.add_argument('--metric', default='total_return')
    command.add_argument('--seed', type=int, default=2026)
    command.add_argument('--output')
    command.set_defaults(handler=evaluate_runs)

    command = commands.add_parser('suite', help='run an evaluation suite from a JSON config')
    command.add_argument('--config', required=True)
    command.add_argument('--output')
    command.set_defaults(handler=suite)

    command = commands.add_parser('sweep', help='run a parameter or prompt grid')
    command.add_argument('--config', required=True)
    command.add_argument('--output')
    command.add_argument('--heatmap', nargs=3, metavar=('X', 'Y', 'SVG'))
    command.set_defaults(handler=sweep)

    command = commands.add_parser('disguise', help='disguise a price-bar recording')
    command.add_argument('--source', required=True)
    command.add_argument('--output', required=True)
    command.add_argument('--seed', type=int, default=2026)
    command.set_defaults(handler=disguise)

    command = commands.add_parser('split', help='write walk-forward train/test windows')
    command.add_argument('--source', required=True)
    command.add_argument('--folder', required=True)
    command.add_argument('--train-days', type=int, required=True)
    command.add_argument('--test-days', type=int, required=True)
    command.add_argument('--gap-days', type=int, default=0)
    command.add_argument('--anchored', action='store_true')
    command.set_defaults(handler=split)

    for name, handler, text in (
        ('lineage', lineage, "a run's recorded code, data and seals"),
        ('verify', verify, 'prove a stored run unchanged (exit 1 if not)'),
        ('timeline', timeline, "an Agent run's decision timeline"),
    ):
        command = commands.add_parser(name, help=text)
        _profile_run(command)
        command.set_defaults(handler=handler)

    command = commands.add_parser('attribution', help='alpha, beta, factors and costs of a run')
    _profile_run(command)
    command.add_argument('--factors', help='JSON file: {name: [return per period, ...]}')
    command.set_defaults(handler=attribution)

    command = commands.add_parser('diff', help='what differs between two runs')
    command.add_argument('--profile', required=True)
    command.add_argument('run_a')
    command.add_argument('run_b')
    command.set_defaults(handler=diff)

    command = commands.add_parser('risk-report', help='write a model risk report (.md or .json)')
    command.add_argument('--profile', required=True)
    command.add_argument('--runs', nargs='+', required=True)
    command.add_argument('--output', required=True)
    command.add_argument('--title', default='Model risk report')
    command.add_argument('--baseline')
    command.set_defaults(handler=risk_report)
