"""Hand-calculated detached market/account values shared by read/IPC tests."""

import importlib.util
import json
from pathlib import Path

from sim_platform.execution.models import ExecutionConfig, ExecutionSnapshot
from sim_platform.domain.models import AccountSnapshot, Position, OrderSnapshot
from sim_platform.market.models import MarketSnapshot, MarketTrade, PriceVolumeBar
from sim_platform.agents.tools.protocol.contracts import READ_TOOLS
from sim_platform.agents.tools.reads.formatter import ReadFormatter
from sim_platform.agents.tools.reads.capture import OrderVersions
from support.agents import settings


class ReadFixture:
    def prepare_reads(self):
        self.config = dict(
            settings(),
            run_id='run',
            work_id='work-1',
            epoch='1',
            execution_config_ref='execution-1',
            granted_tools=sorted(READ_TOOLS),
        )
        self.market = MarketSnapshot(
            'AAPL',
            80_000_000_000,
            3,
            'playing',
            'ordered_unverified',
            ((1_000_000, 10),),
            ((1_010_000, 20),),
            1,
            1,
            80_000_000_000,
        )
        account = AccountSnapshot(
            'account',
            'profile',
            'USD',
            8_000_000,
            (Position('AAPL', 2, 2_000_000),),
            0,
            0,
            7,
            (
                OrderSnapshot('o1', 'i1', 'run', 'AAPL', 'buy', 5, 1_000_000, 2, 'partial'),
                OrderSnapshot('o2', 'i2', 'run', 'AAPL', 'sell', 1, 1_020_000, 0, 'accepted'),
            ),
        )
        self.execution = ExecutionSnapshot(
            'run',
            'running',
            80_000_000_000,
            3,
            account,
            3_000_000,
            5_000_000,
            (('AAPL', 1),),
            ExecutionConfig(),
        )
        self.trades = (
            MarketTrade(1, 10_000_000_000, 1, 1_000_000, 1, True, 'P'),
            MarketTrade(2, 40_000_000_000, 2, 1_020_000, 2, True, 'P'),
            MarketTrade(3, 70_000_000_000, 3, 1_010_000, 1, True, 'P'),
        )
        self.bars = (
            PriceVolumeBar(
                0,
                60_000_000_000,
                80_000_000_000,
                True,
                1_000_000,
                1_020_000,
                1_000_000,
                1_020_000,
                3,
                2,
                3_040_000,
            ),
            PriceVolumeBar(
                60_000_000_000,
                120_000_000_000,
                80_000_000_000,
                False,
                1_010_000,
                1_010_000,
                1_010_000,
                1_010_000,
                1,
                1,
                1_010_000,
            ),
        )
        self.history = dict(
            trades=self.trades,
            bars=self.bars,
            trades_complete=True,
            bars_complete=True,
            trade_start_ns=0,
        )
        self.tools = ReadFormatter(self.config)
        self.versions = OrderVersions()

    def capture(self, tool):
        return dict(
            market=self.market,
            execution=self.execution,
            history=self.history,
            order_versions=self.versions.observe(self.execution.account.orders),
        )


ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    'fixture_validator', ROOT / 'tools/validate_contract_fixtures.py'
)
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)
SCHEMA = json.loads((ROOT / 'tests/fixtures/agent-tools.schema.json').read_text())


def request(tool, arguments=None, number='r1', run='run'):
    return dict(
        run_id=run,
        work_id='work-1',
        epoch='1',
        request_id=number,
        tool=tool,
        arguments={} if arguments is None else arguments,
    )


def position(stamp, ordinal=1):
    return dict(sequence='3', time_ns=str(stamp), boundary='1', ordinal=str(ordinal))
