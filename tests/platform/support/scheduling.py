"""Runtime policy settings and a deterministic in-process clock."""

from support.agents import settings
from support.trading import SECOND


def configuration(interval='0.05'):
    return dict(
        start_ns=0,
        end_ns=10 * SECOND,
        symbol='AAPL',
        event_buckets=4,
        timeout_seconds='0.1',
        retry=settings()['retry'],
        schedule=dict(
            mode='interval',
            interval_seconds=interval,
            busy_event_policy='preserve_interval',
            events=[],
        ),
    )


class ManualClock:
    def __init__(self):
        self.ns, self.running = 0, True

    def target_ns(self):
        return self.ns

    def snapshot(self):
        return dict(target_ns=self.ns, running=self.running)
