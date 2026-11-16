"""Read-only replay inputs and isolated database preparation. SQL stays here."""

from __future__ import annotations

from contextlib import closing

from pathlib import Path
import json
import sqlite3
from typing import TYPE_CHECKING, Any, Callable, Dict, Union
from sim_platform.domain.models import ContractError
from sim_platform.domain.json_values import copy_json

if TYPE_CHECKING:  # Annotation only.
    from os import PathLike


# Everything replay needs from the original run: 'account_id', 'initial', 'manifest',
# 'source_info', 'settings', 'journal', 'status', 'profile', 'records', 'terminal',
# 'expected_account', 'expected_events' and 'expected_end'.
ReplayInputs = Dict[str, Any]


def read_inputs(
    directory: Union[str, PathLike[str]],
    run_id: str,
    *,
    recover: bool,
    snapshot_reader: Callable[[Any, str, str], Any],
) -> ReplayInputs:
    """Read a run's replay inputs from the database without modifying it.

    ``snapshot_reader(repositories, profile, account_id)`` supplies the expected
    account when recovering a run that has no saved final snapshot.
    """
    original = Path(directory).resolve() / 'platform.sqlite'
    with closing(sqlite3.connect(original.as_uri() + '?mode=ro', uri=True)) as source, source:
        row = source.execute(
            'SELECT account_id,config,initial_snapshot,policy_state,status FROM runs WHERE id=?',
            (run_id,),
        ).fetchone()
        if row is None:
            raise ContractError('unknown replay run')
        account_id, settings, initial, journal, status = row
        config, initial, journal = json.loads(settings), json.loads(initial), json.loads(journal)
        if 'runtime_backend' in config.get('research_source', {}):
            raise ContractError(
                'obsolete experiment configuration; recreate with an explicit execution_mode'
            )
        if (
            config.get('agent_experiment_id')
            or config.get('research_source', {}).get('execution_mode') == 'live'
        ):
            raise ContractError(
                'live experiments require operation Replay; raw-source replay is unsupported'
            )
        manifest = config.get('replay')
        if not manifest or manifest.get('version') != 1 or not manifest.get('source'):
            raise ContractError('run has no reproducible file-source manifest')
        source_info = manifest['source']
        checkpoint_row = source.execute(
            'SELECT version,state FROM run_checkpoints WHERE run_id=?', (run_id,)
        ).fetchone()
        if checkpoint_row:
            if checkpoint_row[0] != 1:
                raise ContractError('unsupported run checkpoint version')
            terminal = json.loads(checkpoint_row[1])
            from sim_platform.persistence.sql.run_checkpoints import validate

            validate(terminal)
        else:
            archive = source.execute(
                'SELECT data FROM research_archives WHERE run_id=?', (run_id,)
            ).fetchone()
            terminal = json.loads(archive[0]).get('execution') if archive else None
        if terminal is None:
            raise ContractError('missing durable replay endpoint')
        if recover and status != 'running':
            raise ContractError('recovery requires an interrupted running run')
        if status == 'running' and not recover:
            raise ContractError('finish the run, or use recover=True for a durable recovery point')
        profile = source.execute(
            'SELECT profile_id FROM accounts WHERE id=?', (account_id,)
        ).fetchone()[0]
        expected_events = [
            json.loads(x[0])
            for x in source.execute(
                "SELECT data FROM events WHERE run_id=? AND kind='fill' ORDER BY sequence",
                (run_id,),
            )
        ]
        end = source.execute(
            "SELECT data FROM events WHERE run_id=? AND kind='execution_end'", (run_id,)
        ).fetchone()
        expected_end = json.loads(end[0]) if end else None
    # Read the original through a read-only SQLAlchemy connection; never migrate it.
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from urllib.parse import quote
    from sim_platform.persistence.sql.repositories import Repositories

    engine = create_engine('sqlite:///file:' + quote(str(original)) + '?mode=ro&uri=true')
    try:
        with Session(engine) as session:
            repo = Repositories(session)
            records = repo.runs.records(run_id)
            expected_account = repo.runs.final(run_id)
            if expected_account is None and recover:
                expected_account = snapshot_reader(repo, profile, account_id)
    finally:
        engine.dispose()
    return dict(
        account_id=account_id,
        initial=initial,
        manifest=manifest,
        source_info=source_info,
        settings=copy_json(manifest['initial_config']),
        journal=journal,
        status=status,
        profile=profile,
        records=records,
        terminal=terminal,
        expected_account=expected_account,
        expected_events=expected_events,
        expected_end=expected_end,
    )


def prepare_destination(
    directory: Union[str, PathLike[str]],
    run_id: str,
    destination: Union[str, PathLike[str]],
    inputs: ReplayInputs,
) -> None:
    """Copy the database into an empty ``destination`` and reset the run to its start."""
    destination = Path(destination).resolve()
    if destination.exists() and any(destination.iterdir()):
        raise ContractError('replay destination must be empty')
    original = Path(directory).resolve() / 'platform.sqlite'
    account_id, initial, manifest = (inputs[key] for key in ('account_id', 'initial', 'manifest'))
    with closing(sqlite3.connect(original.as_uri() + '?mode=ro', uri=True)) as source, source:
        destination.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(destination / 'platform.sqlite')) as target, target:
            source.backup(target)
            target.execute('PRAGMA foreign_keys=OFF')
            target.execute(
                'DELETE FROM events WHERE account_id=? AND sequence>?',
                (account_id, initial['sequence']),
            )
            target.execute('DELETE FROM ledger_items WHERE account_id=?', (account_id,))
            target.execute('DELETE FROM account_checkpoints WHERE account_id=?', (account_id,))
            for table in ('decision_records', 'research_archives', 'run_checkpoints'):
                target.execute('DELETE FROM ' + table + ' WHERE run_id=?', (run_id,))
            target.execute(
                "UPDATE runs SET status='stopped' WHERE account_id=? AND id<>? AND status='running'",
                (account_id, run_id),
            )
            settings = copy_json(manifest['initial_config'])
            target.execute(
                "UPDATE runs SET config=?,maker_id=?,status='running',time_ns=0,policy_state='{}' WHERE id=?",
                (json.dumps(settings), manifest['initial_maker'], run_id),
            )
            target.commit()
