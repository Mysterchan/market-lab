"""Validate a sealed operation stream before creating a destination run.

Controls and reads are audit history. Only commands confirmed as processed are
financial inputs. Every admission must have exactly one processed/cancelled
outcome, including commands pending when the experiment ended.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from sim_platform.domain.models import ContractError
from sim_platform.domain.json_values import copy_json
from sim_platform.agents.tools.protocol.contracts import IMPLEMENTED_TOOLS

# Agent tools whose recorded requests Replay executes again as financial commands.
FINANCIAL_TOOLS = ('orders.submit_limit', 'orders.submit_market', 'orders.cancel')

# A decoded operation log: 'header', 'records' (each with 'index', 'event' and
# 'details') and 'end' (the seal: 'status' and 'record_count' or 'records').
Archive = Dict[str, Any]
# One recorded command, from its 'command_admitted' details: 'command_id',
# 'effective_ns', 'admission_sequence', 'prepared_prefix', 'source_sequence', 'payload'
# and, once processed, 'financial_effect'.
RecordedCommand = Dict[str, Any]
# decode_plan's result: (seed 'prepared_run' details, final financial snapshot,
# processed financial commands in order, {'position': terminal position},
# original order ID -> client ID).
Plan = Tuple[Dict[str, Any], Dict[str, Any], List[RecordedCommand], Dict[str, Any], Dict[str, str]]
# _collect's result: seeds, finals, commands by ID, and each command's outcome event.
Collected = Tuple[
    List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, RecordedCommand], Dict[str, str]
]


def _number(value: Any, name: str) -> int:
    """Parse a recorded non-negative integer given as int or digit string."""
    if type(value) not in (str, int) or not str(value).isdigit():
        raise ContractError('invalid recorded ' + name)
    return int(value)


def decode_plan(archive: Archive) -> Plan:
    """Return seed, final account, committed commands, endpoint and order mapping.

    A failed run is usable only if it has a sealed final financial snapshot and
    all command outcomes. Unfinished or financially ambiguous logs are refused.
    """
    try:
        return _decode(archive)
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        if isinstance(exc, ContractError):
            raise
        raise ContractError('invalid prepared operation log') from exc


def _decode(archive: Archive) -> Plan:
    if archive.get('header') is None or archive.get('end') is None:
        raise ContractError('prepared Replay requires a sealed operation log')
    records, footer = archive['records'], archive['end']
    if type(archive['header']) is not dict or type(footer) is not dict or type(records) is not list:
        raise ContractError('invalid prepared operation envelope')
    if footer.get('record_count', footer.get('records')) != len(records):
        raise ContractError('prepared operation footer count mismatch')
    if footer['status'] not in ('stopped', 'completed', 'failed'):
        raise ContractError('unsupported recorded terminal status')
    seeds, finals, commands, outcomes = _collect(records)
    if len(seeds) != 1 or len(finals) != 1:
        raise ContractError('this historical run lacks the prepared Replay contract')
    if set(outcomes) != set(commands):
        raise ContractError('incomplete recorded command outcomes')
    seed, final = seeds[0], finals[0]
    start, end = seed['start_ns'], seed['end_ns']
    if type(start) is not int or type(end) is not int or not 0 <= start < end:
        raise ContractError('invalid recorded experiment interval')
    if final['financial']['status'] != footer['status']:
        raise ContractError('financial endpoint was not sealed successfully')
    position = final['position']
    stamp = _number(position['time_ns'], 'terminal time')
    prefix = _number(position['boundary'], 'terminal prefix')
    if not start <= stamp <= end:
        raise ContractError('recorded endpoint outside experiment interval')
    committed: List[RecordedCommand] = []
    for identity, command in commands.items():
        time_ns = _number(command['effective_ns'], 'command time')
        boundary = _number(command['prepared_prefix'], 'command prefix')
        _number(command['source_sequence'], 'source sequence')
        if not start <= time_ns < end:
            raise ContractError('recorded command outside experiment interval')
        if outcomes[identity] == 'command_processed':
            if time_ns > stamp or boundary > prefix:
                raise ContractError('recorded command after financial endpoint')
            payload = command['payload']
            if type(payload) is not dict:
                raise ContractError('invalid recorded command payload')
            if payload.get('operation') == 'observe' or (
                payload.get('tool') in IMPLEMENTED_TOOLS and payload['tool'] not in FINANCIAL_TOOLS
            ):
                continue
            if (
                payload.get('operation') not in ('manual', 'commit')
                and payload.get('tool') not in FINANCIAL_TOOLS
            ):
                raise ContractError('unsupported recorded financial command')
            committed.append(command)
    orders = {
        o['order_id']: o['intent_id'].split(':intent:', 1)[-1]
        for o in final['financial']['account']['orders']
    }
    return seed, final['financial'], committed, dict(position=position), orders


def _collect(records: List[Dict[str, Any]]) -> Collected:
    """Check record indices, admission ordering and exactly one outcome per request."""
    seeds: List[Dict[str, Any]] = []
    finals: List[Dict[str, Any]] = []
    commands: Dict[str, RecordedCommand] = {}
    outcomes: Dict[str, str] = {}
    previous = (-1, 0)
    for number, record in enumerate(records, 1):
        if type(record) is not dict or type(record.get('details', {})) is not dict:
            raise ContractError('invalid prepared operation record')
        if type(record['index']) is not int or record['index'] != number:
            raise ContractError('prepared operation record gap')
        event, details = record.get('event'), record.get('details', {})
        if event == 'prepared_run':
            seeds.append(details)
        elif event == 'prepared_final':
            finals.append(details)
        elif event == 'command_admitted':
            if len(seeds) != 1 or finals:
                raise ContractError('command outside recorded financial lifecycle')
            identity = details['command_id']
            key = (
                _number(details['effective_ns'], 'command time'),
                _number(details['admission_sequence'], 'admission sequence'),
            )
            if identity in commands or key[0] < previous[0] or key[1] <= previous[1]:
                raise ContractError('invalid prepared command ordering/identity')
            commands[identity] = copy_json(details)
            previous = key
        elif event in ('command_processed', 'command_cancelled'):
            if finals:
                raise ContractError('command outcome after financial endpoint')
            identity = details['command_id']
            if identity not in commands or identity in outcomes:
                raise ContractError('missing or duplicate recorded command outcome')
            command = commands[identity]
            if event == 'command_processed' and any(
                details[key] != command[key] for key in ('effective_ns', 'admission_sequence')
            ):
                raise ContractError('processed command position differs from admission')
            if event == 'command_processed' and 'financial_effect' in details:
                effect = details['financial_effect']
                if effect is not None and type(effect) is not bool:
                    raise ContractError('invalid recorded financial effect')
                command['financial_effect'] = effect
            outcomes[identity] = event
    return seeds, finals, commands, outcomes
