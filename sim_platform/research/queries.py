"""Bounded research reads; live data is captured at one owner boundary, without SQL."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import asdict
from typing import TYPE_CHECKING, Any, Dict, List, cast

from sim_platform.domain.json_values import copy_json
from .reports import report_view
from .views import VIEW_RECORD_LIMIT, CapturedData

if TYPE_CHECKING:  # Annotation only.
    from sim_platform.decisions.policy.runner import PolicyDecisionRunner
    from sim_platform.market.models import MarketSnapshot
    from .controller import ResearchController

# Most recent trades, one-minute bars and news items captured for display or archive.
TRADE_VIEW_LIMIT = 50
BAR_VIEW_LIMIT = 60
# A daily bar is stored as four one-minute bars (its open, low, high and close prints),
# so a daily recording needs four times as many to show the same number of candles.
DAILY_BAR_VIEW_LIMIT = 4 * BAR_VIEW_LIMIT
# Most Agent orders shown with their decision context on a saved result.
VIEW_DECISION_LIMIT = 100


def bar_view_limit(market: Any) -> int:
    """How many one-minute bars a view should carry for this market."""
    calendar = getattr(market, 'calendar', None) or {}
    return DAILY_BAR_VIEW_LIMIT if calendar.get('timeframe') == '1d' else BAR_VIEW_LIMIT


NEWS_VIEW_LIMIT = 50


def daily_closes(market: Any) -> List[List[int]]:
    """[day, time_ns, price] of the last trade of every market day seen so far.

    Saved with the run so the results page can value the account at each day's close
    over the whole run, not only over the short window of archived bars.
    """
    from sim_platform.market.models import DAY_NS

    closes: Dict[int, List[int]] = {}
    for bar in market.bars(limit=None):
        day = bar.start_ns // DAY_NS
        last = bar.end_ns if bar.complete else bar.observed_through_ns
        closes[day] = [day, min(last, (day + 1) * DAY_NS - 1), bar.close]
    return [closes[day] for day in sorted(closes)]


def symbol_markets(market: Any, *, closes: bool = False) -> Dict[str, Any]:
    """Each symbol's book, recent trades and bars, for an experiment on several symbols.

    Empty for a one-symbol market, whose view keeps its flat ``market``/``bars`` keys.
    With ``closes`` every symbol's daily closes are added, as saved with the run.
    """
    universe = getattr(market, 'universe', ())
    if len(universe) < 2:
        return {}
    markets: Dict[str, Any] = {}
    for symbol in universe:
        session = market.symbol_market(symbol)
        markets[symbol] = dict(
            market=asdict(market.book(symbol)),
            trades=[asdict(t) for t in session.trades(limit=TRADE_VIEW_LIMIT)],
            bars=[asdict(b) for b in session.bars(limit=bar_view_limit(market))],
        )
        if closes:
            markets[symbol]['daily_closes'] = daily_closes(session)
    return markets


class ResearchQueries:
    """Reads one consistent view of a controller's market, run and saved report.

    Each ResearchController creates one and keeps it for its lifetime; it holds no state.
    """

    def capture(self, session: ResearchController) -> CapturedData:
        """Capture the controller's current data, preferring a runner's published view."""
        runner = session.runner
        if runner is not None:
            published = runner.detached_view()
            if published is not None:
                return published
        # Suspected bug: LivePolicyDecisionRunner and AgentDecisionRunner have no
        # control_boundary(); a Live runner that has not published a view yet raises
        # AttributeError here instead of returning a view.
        with runner.control_boundary() if runner else nullcontext():  # type: ignore[union-attr]
            if session.viewed_run:
                # view_run() sets profile, run_id and viewed_run together.
                (profile, run_id) = (cast(str, session.profile), cast(str, session.run_id))
                saved: CapturedData = dict(
                    mode='saved', report=report_view(session.app, profile, run_id)
                )
                if (saved['report']['run'].get('config') or {}).get('agent'):
                    from .attribution import decision_timeline

                    # Each Agent order with the reads and notes that preceded it.
                    saved['agent_decisions'] = decision_timeline(
                        session.app.agent_log_archive(profile, run_id),
                        saved['report']['run_events'],
                    )['orders'][-VIEW_DECISION_LIMIT:]
                return saved
            snapshot = session.market.snapshot if session.market else None
            data = dict(
                mode='simulation' if runner else 'research',
                preparation=copy_json(session.preparation),
                market=asdict(snapshot) if snapshot else None,
                account=None,
                execution=None,
                trades=[],
                bars=[],
                news=[],
                events=[],
                decisions=[],
                handoffs=[],
                run=None,
                summary=None,
                source=copy_json(session.source),
            )
            # snapshot is the market's snapshot whenever a market is loaded.
            if session.market and cast('MarketSnapshot', snapshot).status not in (
                'failed',
                'closed',
                'unloaded',
                'loading',
            ):
                data.update(
                    trades=[asdict(t) for t in session.market.trades(limit=TRADE_VIEW_LIMIT)],
                    bars=[
                        asdict(b) for b in session.market.bars(limit=bar_view_limit(session.market))
                    ],
                    news=[asdict(n) for n in session.market.news(limit=NEWS_VIEW_LIMIT)],
                )
                markets = symbol_markets(session.market)
                if markets:
                    data['markets'] = markets
            if runner:
                # Only the synchronous policy runner reaches this point: background
                # runners return their published view above.
                execution = cast('PolicyDecisionRunner', runner).snapshot
                run = session.app.run(session.profile, session.run_id, include_records=False)
                data.update(
                    account=asdict(execution.account),
                    execution=asdict(execution),
                    run=run,
                    events=session.app.run_events(
                        session.profile, session.run_id, limit=VIEW_RECORD_LIMIT
                    ),
                    decisions=session.app.decision_summaries(
                        session.profile, session.run_id, limit=VIEW_RECORD_LIMIT
                    ),
                    handoffs=run['policy_state'].get('handoffs', []),
                )
            return copy_json(data)
