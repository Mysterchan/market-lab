"""Static descriptions of Platform values; methods still return ordinary dictionaries.

These types document keys for IDEs and callers. They do not validate, copy,
serialize, open storage or introduce alternative runtime objects.
"""

from typing import Dict, Iterator, List, Literal, Optional, Tuple, TypedDict, Union

JsonValue = Union[
    None, bool, int, float, str, List['JsonValue'], Tuple['JsonValue', ...], Dict[str, 'JsonValue']
]
JsonObject = Dict[str, JsonValue]
ExperimentMethod = Literal['human', 'rule', 'hold', 'model', 'agent']
ExecutionMode = Literal['controlled', 'live']
TerminalStatus = Literal['completed', 'stopped', 'failed']
RunStatus = Literal['running', 'completed', 'stopped', 'failed']
CredentialPersistence = Literal['session', 'os']
MakerKind = Literal['human', 'rule', 'model', 'agent']


class ProfileDetails(TypedDict):
    """Profile identity; does not include account balances."""

    id: str
    name: str


class AccountDetails(TypedDict):
    """Account identity; use Platform.snapshot for cash and positions."""

    id: str
    profile_id: str
    name: str
    currency: str


class MakerDetails(TypedDict):
    """Decision-maker identity and caller-defined configuration."""

    id: str
    profile_id: str
    name: str
    kind: MakerKind
    version: str
    config: JsonValue


class AgentRevisionDetails(TypedDict):
    """An immutable saved definition revision, represented as detached data."""

    agent_id: str
    revision: int
    profile_id: str
    definition: JsonObject


class AgentExperimentDetails(TypedDict):
    """Saved draft; state=prepared does not mean runtime resources are bound."""

    id: str
    profile_id: str
    account_id: str
    agent_id: str
    revision: int
    config: JsonObject
    initial_snapshot: JsonObject
    execution_config: JsonObject
    state: Literal['prepared']
    readiness: Literal['requires_runtime_binding']
    bindings_required: Dict[str, str]


class RunDetails(TypedDict):
    """Persistent run view; running includes the ready period before Play."""

    id: str
    account_id: str
    maker_id: str
    status: RunStatus
    time_ns: int
    universe: List[str]
    config: JsonObject
    initial_snapshot: JsonObject
    policy_state: JsonValue
    decision_maker: MakerDetails


class ArchiveReceipt(TypedDict):
    """Acknowledgement of completed Agent JSON archival, not a financial order."""

    run_id: str
    record_count: int


class PreparedReplayResult(TypedDict):
    """Successful original-operation comparison in the destination account."""

    run_id: str
    matched: bool
    commands: int
    financial: JsonObject
    replay: bool
    model_calls: int
    operations: List[JsonObject]
    events: List[JsonObject]
    events_verified: bool


class ReplayVerification(TypedDict):
    """Completed isolated raw-source Replay comparison."""

    verified: bool
    run_id: str
    decisions: int
    fills: int
    account: JsonObject
    position: JsonObject
    destination: str


class ReportData(TypedDict):
    """Run, final account projection, market endpoint and full ledger evidence."""

    run: RunDetails
    final: JsonObject
    last_market: Optional[JsonObject]
    account_ledger: List[JsonObject]
    run_events: List[JsonObject]


class ReportViewData(ReportData):
    """Bounded report projection; account_ledger is empty, event/decision tails limited."""

    fill_count: int
    decision_count: int
    decision_tail: List[JsonObject]


class ReportStreams(TypedDict):
    """Single-pass iterators valid only inside Platform.report_stream's context."""

    account_ledger: Iterator[JsonObject]
    run_events: Iterator[JsonObject]
    decisions: Iterator[JsonObject]
    nested_decisions: Iterator[JsonObject]
