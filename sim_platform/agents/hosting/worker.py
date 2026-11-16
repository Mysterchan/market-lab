"""Agent child entrypoint: initialize once, wait for Play, then run the carrier."""

from __future__ import annotations
import logging

from typing import TYPE_CHECKING, Any, Callable, Dict, Optional, cast

from sim_platform.domain.models import ContractError
from sim_platform.agents.hosting.client import AgentProcessClient
from .lifecycle import AgentStartupError
from sim_platform.agents.tools.protocol.errors import ToolError

_log = logging.getLogger(__name__)

if TYPE_CHECKING:  # Annotation only.
    from multiprocessing.connection import Connection

    from sim_platform.experiments.scheduling.client import RuntimeClient
    from sim_platform.experiments.scheduling.signals import ProcessSignal
    from .protocol import TransportLimits

# Poll interval while waiting for the launch signal.
LAUNCH_POLL_SECONDS = 0.05


def idle_program(client: AgentProcessClient, configuration: Dict[str, Any]) -> None:
    """Remain resident until stopped; no default decision loop is installed."""
    client.wait_stopped()


def run_program(
    outgoing: Connection,
    incoming: Connection,
    ready: ProcessSignal,
    stop: ProcessSignal,
    running: ProcessSignal,
    limits: TransportLimits,
    program: Callable[[AgentProcessClient, Dict[str, Any]], None],
    configuration: Dict[str, Any],
    work_client: Optional[RuntimeClient] = None,
    model_setup: Optional[Connection] = None,
    startup: Optional[Connection] = None,
    family_ready: Optional[ProcessSignal] = None,
    launch: Optional[ProcessSignal] = None,
) -> None:
    """Agent child entry point: join the process family, prepare, then run after Play.

    The optional arguments are always supplied by the host; they default to None only
    in the signature.
    """
    client = AgentProcessClient(outgoing, incoming, limits, stop, running)
    client.work_client = work_client
    client.model_setup = model_setup
    try:
        from .processes import enter_process_family

        enter_process_family()
        # The host always supplies startup, family_ready and launch.
        cast('ProcessSignal', family_ready).set()
        while not cast('ProcessSignal', launch).wait(LAUNCH_POLL_SECONDS):
            if stop.is_set():
                return
        from sim_platform.agents.carrier.program import ConfiguredAgentProgram

        prepared = None
        try:
            if isinstance(program, ConfiguredAgentProgram):
                prepared = program.prepare(client, configuration)
        except Exception as error:
            _log.warning('Handled failure in run_program', exc_info=True)
            code = 'AGENT_INITIALIZATION_FAILED'
            if isinstance(error, AgentStartupError):
                code = error.code
            elif isinstance(error, ToolError) and error.code == 'TOOL_UNAVAILABLE':
                code = 'AGENT_TOOL_UNAVAILABLE'
            cast('Connection', startup).send(code)
            ready.set()
            return
        cast('Connection', startup).send(None)
        cast('Connection', startup).close()
        ready.set()
        client.wait_running()
        if prepared is not None:
            prepared.run()
        else:
            program(client, configuration)
    except ContractError:
        if not client.stopping:
            # Uploaded code can raise ContractError with private content too.
            # Report the failed process without copying that content to stderr.
            raise SystemExit(1) from None
    except Exception:
        # User exceptions may contain prompts/keys. Report a nonzero exit, not their text.
        raise SystemExit(1) from None
    finally:
        cast('Connection', startup).close()
        outgoing.close()
        incoming.close()
        if model_setup is not None:
            model_setup.close()
