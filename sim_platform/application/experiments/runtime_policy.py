"""Trusted prepared-market observation adapter injected into the Runtime child.

Only this trusted adapter opens the immutable event index. Agent and Tools never
receive its path or reader. Financial notifications retain their existing
journal sequence; clock-visible market observations have a separate watermark.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional, Union

from sim_platform.experiments.scheduling.clock import real_delay_ns
from sim_platform.experiments.scheduling.policy.controller import LiveWorkPolicy
from sim_platform.market.prepared.reader import PreparedMarket

if TYPE_CHECKING:  # Annotation only.
    from sim_platform.experiments.scheduling.clock import ClockReader
    from sim_platform.experiments.scheduling.worker import NoticeCallback
    from sim_platform.market.prepared.publications import PublicationReader

# Fail work if a model receipt is still unsealed after this many wall seconds.
RECEIPT_SEAL_TIMEOUT_SECONDS = 5
# Poll interval while an attempt waits for its receipt seal.
EXPIRY_POLL_SECONDS = 0.01
NS_PER_SECOND = 1e9


class PreparedWorkPolicy(LiveWorkPolicy):
    """LiveWorkPolicy that also watches the prepared market inside the Runtime child.

    Built in the Runtime child by its factory; it opens its own read-only prepared
    reader and closes it in end(). Market updates come from the reader at the clock's
    time; financial notifications cannot move market visibility backwards.
    """

    def __init__(
        self, configuration: Dict[str, Any], clock: ClockReader, notice: NoticeCallback
    ) -> None:
        super().__init__(configuration, clock, notice)
        self._reader: Union[PreparedMarket, PublicationReader] = PreparedMarket(
            configuration['prepared_artifact']
        )
        if configuration.get('prepared_news'):
            from sim_platform.market.prepared.publications import PublicationReader
            from sim_platform.market.models import NewsRecord

            self._reader = PublicationReader(
                self._reader, tuple(NewsRecord(**n) for n in configuration['prepared_news'])
            )
        self._market_prefix = self._reader.prefix(clock.target_ns())
        self._visible_sequence = self._reader.snapshot_at(clock.target_ns()).sequence
        self.observed['sequence'] = self._visible_sequence
        self._reader_closed = False
        self.attempts.require_receipt_seal = True
        self._seal_pending_since: Optional[float] = None
        self._model_error: Optional[str] = None

    def _notice(self, event: Optional[Dict[str, Any]]) -> None:
        if event is not None and event['event'] == 'attempt_failed':
            self._model_error = event.get('error')
        super()._notice(event)

    def snapshot(self) -> Dict[str, Any]:
        return dict(super().snapshot(), model_error=self._model_error)

    def _observe(self, data: Dict[str, Any], *, resync: bool = False) -> Dict[str, bool]:
        # A delayed financial publication cannot move market visibility backwards.
        from sim_platform.domain.json_values import copy_json

        data = copy_json(data)
        data['observation']['sequence'] = max(
            data['observation']['sequence'], self._visible_sequence
        )
        data['events'] = [e for e in data['events'] if e['kind'] != 'market_update']
        return super()._observe(data, resync=resync)

    def step(self) -> None:
        if not self._stopped and not self._paused and self.clock.snapshot()['running']:
            self._market_now = self.clock.target_ns()
            now = min(self._market_now, self._reader.manifest['last_ns'])
            prefix = self._reader.prefix(now)
            if prefix > self._market_prefix:
                self._market_prefix = prefix
                self._visible_sequence = self._reader.snapshot_at(now).sequence
                self.observed['sequence'] = self._visible_sequence
                self.work.observe(
                    dict(kind='market_update', symbol=self.work.symbol), self._position()
                )
        super().step()
        import time

        if self._paused:
            self._seal_pending_since = None
        elif self.attempts.state == 'expiry_pending':
            if self._seal_pending_since is None:
                self._seal_pending_since = time.monotonic()
            elif time.monotonic() - self._seal_pending_since > RECEIPT_SEAL_TIMEOUT_SECONDS:
                self.work.end('MODEL_RECEIPT_UNAVAILABLE', failed=True)
                self.attempts.end()
                self.notice('platform_failure', dict(error='MODEL_RECEIPT_UNAVAILABLE'))
        else:
            self._seal_pending_since = None

    def dispatch(self, name: str, data: Dict[str, Any]) -> Any:
        if name == 'model_receipt':
            accepted = self.attempts.receive_at(
                data['attempt_id'],
                data['received_ns'],
                result=(
                    dict(received=True, **({'usage': data['usage']} if data.get('usage') else {}))
                    if data.get('error') is None
                    else None
                ),
                error=data.get('error'),
                retry_after_seconds=data.get('retry_after_seconds'),
            )
            sealed = self.attempts.seal_receipt(data['attempt_id'])
            return dict(accepted=accepted, sealed=sealed)
        return super().dispatch(name, data)

    def wait_seconds(self) -> Optional[float]:
        delay = super().wait_seconds()
        if self.attempts.state == 'expiry_pending':
            delay = EXPIRY_POLL_SECONDS
        if self._stopped or self._paused or not self.clock.snapshot()['running']:
            return delay
        if self._market_prefix >= self._reader.manifest['frames']:
            return delay
        next_ns = self._reader.event_time(self._market_prefix)
        market_delay = real_delay_ns(self.clock, next_ns) / NS_PER_SECOND
        return min(delay, market_delay) if delay is not None else market_delay

    def end(self, reason: str) -> None:
        try:
            super().end(reason)
        finally:
            if not self._reader_closed:
                self._reader.close()
                self._reader_closed = True
