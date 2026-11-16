# Agent process hosting

This folder starts and communicates with a Python carrier. It is not the work
scheduler; that process is in [experiments/scheduling](../../experiments/scheduling/README.md).

| File | Runs where | Responsibility |
| --- | --- | --- |
| `host.py` | App | AgentProcessHost prepares, gates, monitors and reaps the child. |
| `lifecycle.py` | App | Named initialization failures and bounded family termination/root reaping. |
| `processes/` | App / Agent startup | POSIX group or Windows Job ownership; no sandbox. |
| `worker.py` | Agent child | run_program initializes the Agent before ready, waits for Play, runs the carrier. |
| `client.py` | Agent child | AgentProcessClient sends tools, gets work and binds Agent-local model IO. |
| `bridge.py` | App IPC threads | ToolPipeBridge receives requests and delivers results through injected callbacks. |
| `protocol.py` | Both sides | Transport limits and bounded JSON encoding/decoding. |

The AgentDecisionRunner is in [decisions/agent](../../decisions/agent/README.md).
The Agent's internal loop is in [carrier](../carrier/README.md). User projects use
[SDK](../../sdk/README.md), not these internal classes or IPC envelopes.
Live Rule code reuses the host with a trusted policy-specific program; that does
not turn LivePolicyDecisionRunner into the Agent runner.

Saved-project loading is in `loader.py`; it calls the same factory inside the selected
interpreter. `processes/interpreter.py` and the two spawn adapters select Python per
process without global executable mutation. No OS sandbox is supplied. Existing
capability gates remain cooperative for arbitrary local Python computation.

## Startup and teardown

The parent spawns a child, attaches OS ownership and waits for the child's family-ready
handshake. Only then does it open the launch gate. The child calls the Agent factory,
reports initialization success and waits for Play. Failed initialization or a deadline
closes partially prepared resources before the exception returns to the caller.

Normal completion, crash and Stop all clean ordinary descendant processes. Stop first
opens the cooperative exit gate, then the supervisor escalates after shutdown_grace
(default 0.5 seconds) to family termination and forced termination. Startup timeout
(default 5 seconds) is distinct from model timeout and is not a whole-work deadline.
Long CPU computation alone does not mark an Agent failed.

snapshot() preserves state/pid/exitcode/error/transport and also reports exit_reason,
stop_requested, cleanup_pending, process_family and pause_mode. Initialization failures
raise AgentStartupError with a named code. Runtime crash reports a nonzero exit code;
platform status does not copy a user exception's text or model payload.

Pause uses capability gates: SDK work/tool/model delivery pauses, but arbitrary Python
calculation and user-created children are not OS-suspended. Resume opens the same
Agent's gates. Stop can forcibly terminate local CPU computation. The experiment's
clock, model timeout accounting and financial cancellation remain in their existing
controllers; the process adapter does not take ownership of those rules.

The supervisor closes IPC even if exit notification fails. close() retries failed
cleanup/notification, stays idempotent after success and retains ownership on failure.
Pending tool futures are not retried or replayed; their late results cannot restart a
closed Agent. Closing one family does not terminate another experiment or the App.

## Reading the IPC adapter

`ToolPipeBridge._receive()` manages bounded IO and admission pacing. Its helpers separate
transport envelope checks (`_decode_request`), unfinished-ID tracking (`_register_request`)
and runner submission (`_dispatch_request`). The runner still validates tool grants,
arguments and active work. `_send()` alone writes replies; `_encode_reply` checks size.

A reply ID is retired before the peer can receive it. Its capacity slot remains held
until writing finishes. This allows a peer to reuse an ID after receiving the reply,
even if its sending thread has not returned yet. Broken pipes during reads mean peer
closure; other transport errors remain failures. The supervisor decides whether the
Agent returned normally, crashed or was stopped from its process exit.

User exceptions, including ContractError, may contain private content. The child reports
an unsuccessful exit without echoing that text. Initialization uses named failure codes.
