"""Tools child entrypoint: format captured values without mutable financial access."""

from __future__ import annotations
import logging

from typing import TYPE_CHECKING, Any, Callable, Dict

from sim_platform.domain.json_values import encode
from sim_platform.agents.tools.reads.capture import CapturedRead
from sim_platform.agents.tools.trading.financial import format_financial
from sim_platform.agents.tools.protocol.responses import response_size, error_response
from sim_platform.agents.tools.protocol.contracts import ToolError
from sim_platform.agents.tools.protocol.transport import decode_job, MAX_JOB_BYTES

_log = logging.getLogger(__name__)

if TYPE_CHECKING:  # Annotation only.
    from multiprocessing.connection import Connection
    from multiprocessing.synchronize import Event

    from sim_platform.agents.tools.reads.formatter import ReadFormatter

# How long one wait for a job lasts before the stop flag is checked again.
POLL_SECONDS = 0.05
# Longest fault text returned for an unexpected formatter error.
FAULT_TEXT_LIMIT = 256


def run_tools(
    incoming: Connection,
    outgoing: Connection,
    ready: Event,
    stop: Any,
    binding: Dict[str, Any],
    formatter_factory: Callable[[Dict[str, Any]], ReadFormatter],
) -> None:
    """Spawn entrypoint: trusted factory only; no user manifest/import resolution.

    ``stop`` is a shared ctypes byte; a nonzero value ends the loop.
    """
    try:
        formatter = formatter_factory(binding)
        ready.set()
        while not stop.value:
            if not incoming.poll(POLL_SECONDS):
                continue
            identity, captured = decode_job(incoming.recv_bytes(MAX_JOB_BYTES))
            try:
                result = (
                    formatter.execute(captured)
                    if isinstance(captured, CapturedRead)
                    else format_financial(captured, binding['source_manifest_ref'])
                )
                response_size(result, binding['limits']['tool_response_bytes'])
                reply = dict(identity=identity, result=result, fault=None)
            except ToolError as error:
                reply = dict(
                    identity=identity,
                    result=error_response(
                        captured.request, captured.receipt_index, dict(captured.position), error
                    ),
                    fault=None,
                )
            except Exception as error:
                _log.warning('Handled failure in run_tools', exc_info=True)
                reply = dict(
                    identity=identity,
                    result=None,
                    fault=type(error).__name__ + ': ' + str(error)[:FAULT_TEXT_LIMIT],
                )
            outgoing.send_bytes(encode(reply).encode())
    finally:
        incoming.close()
        outgoing.close()
