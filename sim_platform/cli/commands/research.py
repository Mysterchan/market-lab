"""Batch evaluation and independent policy comparison commands."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Dict, Optional

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from ..context import CliContext
from ..output import emit_json
from ..arguments import money
from sim_platform.domain.models import ContractError

if TYPE_CHECKING:  # Annotation only.
    from ..arguments import Subcommands


# Defaults for evaluation and comparison runs.
DEFAULT_CASH = 100_000_000  # money units
DEFAULT_FEE_PER_SHARE = 100  # money units
DEFAULT_QUANTITY = 10
DEFAULT_HOLD_NS = 2_000_000_000


def register(commands: Subcommands) -> None:
    evaluation = commands.add_parser(
        'evaluate', help='batch rule evaluation through the shared core'
    )
    comparison = commands.add_parser('compare', help='independent equal-start rule comparisons')
    for command in (evaluation, comparison):
        command.add_argument('--input', type=Path, required=True)
        command.add_argument('--news', type=Path)
        command.add_argument('--symbol', default='AAPL')
        command.add_argument('--library', type=Path)
        command.add_argument('--allow-implicit-eof', action='store_true')
        command.add_argument('--cash', type=money, default=DEFAULT_CASH)
        command.add_argument('--fee-per-share', type=money, default=DEFAULT_FEE_PER_SHARE)
        command.add_argument('--latency-ns', type=int, default=0)
        command.add_argument('--output', type=Path)
        command.add_argument(
            '--bar-spread',
            type=money,
            help='price-bar .csv only: synthetic best ask minus best bid in USD; 0.02',
        )
        command.add_argument(
            '--book-depth', type=int, help='price-bar .csv only: synthetic levels per side; 5'
        )
        command.add_argument(
            '--allow-short',
            action='store_true',
            help='allow short selling under the documented margin rule',
        )
    evaluation.add_argument('--policy', choices=('rule', 'hold', 'model'), default='rule')
    evaluation.add_argument('--quantity', type=int, default=DEFAULT_QUANTITY)
    evaluation.add_argument('--hold-ns', type=int, default=DEFAULT_HOLD_NS)
    evaluation.add_argument(
        '--model-file', type=Path, help='with --policy model: your Python file defining decide()'
    )
    evaluation.add_argument(
        '--model-parameters', type=json.loads, default={}, help='JSON object passed to the model'
    )
    evaluation.add_argument('--lookback-bars', type=int, default=50)
    evaluation.add_argument(
        '--model-python', type=Path, help='interpreter for the model; default: this Python'
    )
    evaluation.add_argument('--model-timeout', type=float, default=10, help='seconds per call')
    comparison.add_argument(
        '--summary-only',
        action='store_true',
        help='persist independent runs and return compact summaries',
    )
    comparison.add_argument(
        '--configurations', type=Path, help='JSON list of named policy/quantity/hold_ns settings'
    )
    evaluation.set_defaults(handler=run_evaluation)
    comparison.set_defaults(handler=run_comparison)


def _book(args: argparse.Namespace) -> Optional[Dict[str, int]]:
    """The synthetic-book choice for a price-bar input, or None for the defaults."""
    book = {}
    if args.bar_spread is not None:
        book['spread'] = args.bar_spread
    if args.book_depth is not None:
        book['depth'] = args.book_depth
    return book or None


def _options(args: argparse.Namespace) -> Dict[str, Any]:
    return dict(
        book=_book(args),
        allow_short=args.allow_short,
        symbol=args.symbol,
        news=str(args.news) if args.news else None,
        library=str(args.library) if args.library else None,
        cash=args.cash,
        fee_per_share=args.fee_per_share,
        latency_ns=args.latency_ns,
        allow_implicit_eof=args.allow_implicit_eof,
    )


def _report(
    args: argparse.Namespace,
    result: Any,
    *,
    exporter: Optional[Callable[[Any, Any], Any]] = None,
) -> int:
    """Print (and optionally export) a report; exit code 1 if any run failed."""
    from sim_platform.research import export_report

    if args.output:
        inputs = [args.input.resolve()]
        if args.news:
            inputs.append(args.news.resolve())
        if args.output.resolve() in inputs:
            raise ContractError("report destination cannot overwrite an input")
        (exporter or export_report)(result, args.output.resolve())
    emit_json(result)
    reports = [item['report'] for item in result['results']] if 'results' in result else [result]
    return 1 if any(report['summary']['status'] == 'failed' for report in reports) else 0


def run_evaluation(args: argparse.Namespace, context: CliContext) -> int:
    with context.open_platform() as platform:
        batch = platform.open_batch(library=args.library)
        model = None
        if args.policy == 'model':
            if args.model_file is None:
                raise ContractError('--policy model needs --model-file')
            model = dict(
                model_file=str(args.model_file.resolve()),
                model_parameters=args.model_parameters,
                lookback_bars=args.lookback_bars,
                model_python=str(args.model_python) if args.model_python else None,
                model_timeout_seconds=args.model_timeout,
            )
        result = batch.evaluate(
            args.input,
            policy=args.policy,
            quantity=args.quantity,
            hold_ns=args.hold_ns,
            model=model,
            **_options(args),
        )
        return _report(args, result, exporter=batch.export)


def run_comparison(args: argparse.Namespace, context: CliContext) -> int:
    context.resolve_directory()  # Validate explicit/global directory even for temporary comparisons.
    configurations = json.loads(args.configurations.read_text()) if args.configurations else None

    def run(selected: CliContext) -> int:
        with selected.open_platform() as platform:
            batch = platform.open_batch(library=args.library)
            result = batch.compare(
                str(args.input),
                configurations,
                include_details=not args.summary_only,
                **_options(args),
            )
            return _report(args, result, exporter=batch.export)

    if args.summary_only or context.data_dir is not None:
        return run(context)
    with TemporaryDirectory(prefix='trading-comparison-') as temporary:
        context.select_directory(Path(temporary))
        try:
            return run(context)
        finally:
            context.close()
