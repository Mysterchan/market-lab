"""Experiment lineage: what a run used, recorded once, and proof it has not changed.

A run's lineage has three write-once parts (see ``persistence.lineage``):

- ``inputs``, recorded when the run binds to its market: the code version and commit,
  the execution-rule and native-library digests, a digest of the run's configuration
  and the SHA-256 of every input file or folder (recording, news, fundamentals,
  documents, model files).
- ``records``, recorded when the run ends: its status and the digest of its recorded
  events and decisions.
- ``agent``, recorded when an Agent's log is archived: the log's digest, the model
  profile, the served model versions and the Agent definition's digest.

``verify`` recomputes each digest from what is stored now and reports every mismatch,
so a stored run can be shown unchanged, or exactly where it changed.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import logging
from pathlib import Path
import platform as host_platform
import subprocess
import sys
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from sim_platform.domain.json_values import encode
from sim_platform.persistence.lineage import LineageFiles

_log = logging.getLogger(__name__)

# Configuration keys whose text value names an input file or folder.
INPUT_KEYS = ('path', 'news_path', 'fundamentals_path', 'documents_path', 'model_path')
# Configuration branches that repeat inputs or name derived caches, not inputs.
SKIPPED_BRANCHES = ('replay', 'prepared', 'artifact')
_CODE: Dict[str, Any] = {}


def digest_json(value: Any) -> str:
    """SHA-256 of a value's canonical JSON text."""
    return hashlib.sha256(encode(value).encode()).hexdigest()


def _git(root: Path, *args: str) -> Optional[str]:
    try:
        result = subprocess.run(
            ('git', '-C', str(root)) + args,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def code_identity() -> Dict[str, Any]:
    """The running code: package version, Git commit (in a checkout), Python and OS.

    ``dirty`` is True when the checkout has uncommitted changes, so the commit alone
    does not describe the code. Computed once per process.
    """
    if _CODE:
        return dict(_CODE)
    import sim_platform

    from .replay_manifest import rules_digest

    root = Path(sim_platform.__file__).resolve().parents[1]
    identity: Dict[str, Any] = dict(
        version=sim_platform.__version__,
        python=sys.version.split()[0],
        system=f'{host_platform.system()} {host_platform.machine()}',
        rules_sha256=rules_digest(),
    )
    if (root / '.git').exists():
        commit = _git(root, 'rev-parse', 'HEAD')
        if commit:
            status = _git(root, 'status', '--porcelain', '--untracked-files=no')
            identity.update(commit=commit, dirty=bool(status))
    try:
        from sim_platform.market.native import resolve_library_path
        from sim_platform.persistence.lineage import file_digest

        identity['native_sha256'] = file_digest(resolve_library_path())
    except (OSError, Exception):  # A Python-only workspace may have no native library.
        _log.debug('native library digest unavailable', exc_info=True)
    _CODE.update(identity)
    return dict(identity)


def input_paths(config: Any, prefix: str = '') -> Iterator[Tuple[str, str]]:
    """Yield (field, path) for each input file or folder named in a run configuration."""
    if isinstance(config, dict):
        for key in sorted(config):
            if key in SKIPPED_BRANCHES:
                continue
            value = config[key]
            field = f'{prefix}.{key}' if prefix else key
            if key in INPUT_KEYS and isinstance(value, str) and value:
                yield (field, value)
            else:
                yield from input_paths(value, field)
    elif isinstance(config, list):
        for index, value in enumerate(config):
            yield from input_paths(value, f'{prefix}[{index}]')


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


class RunLineage:
    """Records and checks run lineage; owned by Platform.

    Args:
        files: The write-once lineage store.
        run: ``run(profile, run_id, include_records=False)`` reader.
        events: ``run_events(profile, run_id)`` reader.
        decisions: ``decision_records(profile, run_id)`` reader.
        agent_log: ``agent_log_archive(profile, run_id)`` reader.
    """

    def __init__(
        self,
        files: LineageFiles,
        *,
        run: Callable[..., Any],
        events: Callable[..., Any],
        decisions: Callable[..., Any],
        agent_log: Callable[..., Any],
    ) -> None:
        self.files = files
        (self._run, self._events, self._decisions, self._agent_log) = (
            run,
            events,
            decisions,
            agent_log,
        )

    def _inputs(self, config: Dict[str, Any]) -> List[Dict[str, Any]]:
        inputs: List[Dict[str, Any]] = []
        seen = set()
        for field, text in input_paths(config):
            path = Path(text)
            entry: Dict[str, Any] = dict(field=field, path=text)
            if (field, text) in seen:
                continue
            seen.add((field, text))
            try:
                entry.update(self.files.digest(path))
            except (OSError, Exception) as exc:
                entry['error'] = type(exc).__name__
            inputs.append(entry)
        return inputs

    def _records(self, profile: str, run_id: str) -> Dict[str, Any]:
        events = self._events(profile, run_id)
        try:
            decisions = self._decisions(profile, run_id)
        except Exception:  # Runs without a decision journal (manual, Agent).
            _log.debug('no decision records for %s', run_id, exc_info=True)
            decisions = None
        value: Dict[str, Any] = dict(events=len(events), events_sha256=digest_json(events))
        if decisions:
            value['decisions_sha256'] = digest_json(decisions)
        return value

    def record_start(self, profile: str, run_id: str) -> None:
        """Record a run's inputs, code and configuration when it binds to its market."""
        run = self._run(profile, run_id, include_records=False)
        config = run['config']
        self.files.write_once(
            run_id,
            'inputs',
            dict(
                run_id=run_id,
                profile_id=profile,
                recorded_at=_now(),
                code=code_identity(),
                universe=run['universe'],
                config_sha256=digest_json(config),
                execution=config.get('execution'),
                inputs=self._inputs(config),
            ),
        )

    def record_finish(self, profile: str, run_id: str) -> None:
        """Seal a run's recorded events and decisions when it ends."""
        run = self._run(profile, run_id, include_records=False)
        self.files.write_once(
            run_id,
            'records',
            dict(
                recorded_at=_now(),
                status=run['status'],
                time_ns=run['time_ns'],
                **self._records(profile, run_id),
            ),
        )

    def record_agent(self, profile: str, run_id: str) -> None:
        """Record an archived Agent log's digest, model and Agent definition."""
        archive = self._agent_log(profile, run_id)
        if not archive:
            return
        from sim_platform.research.evaluation import agent_model_usage

        run = self._run(profile, run_id, include_records=False)
        definition = (run['config'].get('agent') or {}).get('definition')
        model = next(
            (
                record.get('details')
                for record in archive.get('records') or []
                if record.get('event') == 'model_profile'
            ),
            None,
        )
        usage = agent_model_usage(archive) or {}
        self.files.write_once(
            run_id,
            'agent',
            dict(
                recorded_at=_now(),
                log_sha256=digest_json(archive),
                records=len(archive.get('records') or []),
                model=model,
                served_models=usage.get('served_models', []),
                definition_sha256=digest_json(definition) if definition else None,
            ),
        )

    def lineage(self, profile: str, run_id: str) -> Dict[str, Any]:
        """Every recorded lineage part of a run (checking the profile owns it)."""
        run = self._run(profile, run_id, include_records=False)
        parts = self.files.read(run_id)
        if parts['agent'] is None and run['config'].get('agent'):
            # A log archived before lineage existed, or archived elsewhere.
            try:
                self.record_agent(profile, run_id)
                parts = self.files.read(run_id)
            except Exception:
                _log.warning('could not record Agent lineage', exc_info=True)
        return dict(run_id=run_id, **parts)

    def verify(self, profile: str, run_id: str) -> Dict[str, Any]:
        """Recompute every recorded digest; report each check and whether all match.

        Returns ``{'run_id', 'unchanged', 'checks', 'code_changed'}``. Each check is
        ``{'item', 'recorded', 'current', 'match'}``; a missing input file is a mismatch.
        ``code_changed`` says the code now running differs from the run's code, which
        does not change the stored run but matters before Replay.
        """
        recorded = self.lineage(profile, run_id)
        run = self._run(profile, run_id, include_records=False)
        checks: List[Dict[str, Any]] = []

        def check(item: str, before: Any, now: Any) -> None:
            checks.append(dict(item=item, recorded=before, current=now, match=before == now))

        inputs = recorded['inputs']
        if inputs is None:
            check('inputs', 'recorded', None)
        else:
            check('config', inputs['config_sha256'], digest_json(run['config']))
            for entry in inputs['inputs']:
                try:
                    now = self.files.digest(Path(entry['path']), fresh=True)['sha256']
                except (OSError, Exception) as exc:
                    now = 'unreadable: ' + type(exc).__name__
                check('input ' + entry['field'], entry.get('sha256'), now)
        sealed = recorded['records']
        if sealed is not None:
            current = self._records(profile, run_id)
            check('events', sealed['events_sha256'], current['events_sha256'])
            if 'decisions_sha256' in sealed:
                check('decisions', sealed['decisions_sha256'], current.get('decisions_sha256'))
        agent = recorded['agent']
        if agent is not None:
            archive = self._agent_log(profile, run_id)
            check('agent log', agent['log_sha256'], digest_json(archive) if archive else None)
        code = code_identity()
        before = (inputs or {}).get('code') or {}
        changed = any(
            before.get(key) != code.get(key) for key in ('version', 'commit', 'rules_sha256')
        )
        return dict(
            run_id=run_id,
            unchanged=all(item['match'] for item in checks),
            sealed=sealed is not None,
            checks=checks,
            code_changed=changed,
        )
