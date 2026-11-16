"""A tiny trend-following "model" that needs only the Python standard library.

Use it to try the model decision method, then replace decide() with your own model.
It holds a fixed number of shares while the latest close is above the average close of
the previous ``window`` bars, and holds nothing otherwise.

Parameters (pass them as model_parameters): ``window`` (bars, default 5) and ``shares``
(target position while in the market, default 100).
"""

from statistics import mean
from typing import Any, Dict, List, Optional

SETTINGS: Dict[str, Any] = dict(window=5, shares=100)


def setup(parameters: Dict[str, Any]) -> None:
    """Called once before the first decision. Load model weights here."""
    SETTINGS.update(parameters)


def decide(
    bars: List[Dict[str, Any]], account: Dict[str, Any], context: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """Return the number of shares to hold, or None to change nothing.

    bars: completed bars, oldest first, each with time_ns, date, open, high, low, close
        (dollars) and volume (shares).
    account: cash, available_cash, position (shares), average_cost, equity and
        open_orders.
    context: symbol, time_ns, date, bar, bid, ask and parameters.
    """
    window = int(SETTINGS['window'])
    if len(bars) <= window:
        return None
    closes = [bar['close'] for bar in bars]
    above = closes[-1] > mean(closes[-window - 1 : -1])
    return dict(target_position=int(SETTINGS['shares']) if above else 0)
