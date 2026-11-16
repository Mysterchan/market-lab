"""Preview a source archive against the public checkout; --apply copies reviewed files.

No commits, branches, tags or network calls are created. Existing public checkouts must
be clean Git worktrees with the manifest from the previous sync. First sync requires an
empty directory (an existing .git directory is allowed). An uncommitted review directory
can be updated with --previous-archive after byte comparison. Only previously managed
files can be deleted. A working-tree archive additionally requires --allow-candidate.
"""

import argparse
import json
from pathlib import Path
import subprocess
import tarfile

from build_source_release import PROVENANCE, public_path

MANIFEST = '.market-lab-sync.json'


def read_candidate(path, allow_candidate):
    """Read regular public files only; do not extract archive paths onto disk."""
    files = {}
    roots = set()
    with tarfile.open(path, 'r:gz') as archive:
        for item in archive:
            root, separator, name = item.name.partition('/')
            roots.add(root)
            if not separator or not item.isfile() or not (public_path(name) or name == PROVENANCE):
                raise ValueError('Unexpected archive member: ' + item.name)
            if name in files:
                raise ValueError('Duplicate archive member: ' + name)
            files[name] = (archive.extractfile(item).read(), bool(item.mode & 0o111))
    if len(roots) != 1 or not next(iter(roots)).startswith('market-lab-'):
        raise ValueError('Expected one market-lab archive root')
    provenance = json.loads(files[PROVENANCE][0])
    kind = provenance['source_kind']
    if kind not in ('commit', 'working-tree-candidate'):
        raise ValueError('Unknown source provenance')
    if kind != 'commit' and not allow_candidate:
        raise ValueError(
            'Uncommitted candidate requires --allow-candidate; it is not release approval'
        )
    for required in ('README.md', 'LICENSE', 'NOTICE', 'sim_platform/__init__.py'):
        if required not in files:
            raise ValueError('Required public source missing: ' + required)
    return files, provenance


def safe_target(destination, name):
    target = destination / name
    for part in (target, *target.parents):
        if part.is_symlink():
            raise ValueError('Destination contains a symlink: ' + str(part))
        if part != target and part.exists() and not part.is_dir():
            raise ValueError('Destination parent is not a directory: ' + str(part))
        if part == destination:
            break
    return target


def review_copy_files(destination, previous_archive):
    """Allow a review-copy update only when every file still matches its saved archive."""
    files, provenance = read_candidate(previous_archive, True)
    manifest = safe_target(destination, MANIFEST)
    expected = {'source': provenance, 'files': sorted(files)}
    if not manifest.is_file() or json.loads(manifest.read_text()) != expected:
        raise ValueError('Previous archive does not match the review-copy manifest')
    actual = {p.relative_to(destination).as_posix() for p in destination.rglob('*') if p.is_file()}
    if actual != set(files) | {MANIFEST}:
        raise ValueError('Review copy has added or missing files; resolve local changes first')
    for name, (content, executable) in files.items():
        target = safe_target(destination, name)
        if target.read_bytes() != content or bool(target.stat().st_mode & 0o111) != executable:
            raise ValueError('Review copy has local changes: ' + name)
    return set(files)


def previous_files(destination, previous_archive=None):
    """Require a recoverable, clean public checkout before replacing existing content."""
    entries = [p for p in destination.iterdir() if p.name != '.git'] if destination.exists() else []
    if not entries:
        return set()
    if not (destination / '.git').exists():
        if previous_archive is not None:
            return review_copy_files(destination, previous_archive)
        raise ValueError('Nonempty destination must be a clean Git checkout')
    if previous_archive is not None:
        raise ValueError('Use clean Git state, not --previous-archive, for an existing repository')
    root = subprocess.run(
        ['git', '-C', str(destination), 'rev-parse', '--show-toplevel'],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if Path(root).resolve() != destination.resolve():
        raise ValueError('Destination must be the public repository root')
    status = subprocess.run(
        ['git', '-C', str(destination), 'status', '--porcelain', '--untracked-files=all'],
        check=True,
        capture_output=True,
    ).stdout
    if status:
        raise ValueError('Public checkout has local changes; commit or resolve them before syncing')
    manifest = safe_target(destination, MANIFEST)
    if not manifest.is_file():
        raise ValueError(
            'Existing checkout has no sync manifest; review it manually before adoption'
        )
    names = json.loads(manifest.read_text())['files']
    if not isinstance(names, list) or any(
        not isinstance(n, str) or not (public_path(n) or n == PROVENANCE) for n in names
    ):
        raise ValueError('Invalid managed file list')
    return set(names)


def plan_sync(destination, files, previous_archive=None):
    if any(p.is_symlink() for p in (destination, *destination.parents)):
        raise ValueError('Destination must not be a symlink')
    previous = previous_files(destination, previous_archive)
    plan = {'added': [], 'modified': [], 'deleted': sorted(previous - files.keys())}
    for name, (content, executable) in sorted(files.items()):
        target = safe_target(destination, name)
        if target.exists():
            if name not in previous or not target.is_file():
                raise ValueError('Refusing to overwrite unmanaged path: ' + name)
            if target.read_bytes() != content or bool(target.stat().st_mode & 0o111) != executable:
                plan['modified'].append(name)
        else:
            plan['added'].append(name)
    for name in plan['deleted']:
        if not safe_target(destination, name).is_file():
            raise ValueError('Managed deletion is not a regular file: ' + name)
    return plan


def apply_sync(destination, files, provenance, plan):
    """Change only planned paths; restore their original contents if a write fails."""
    changed = plan['added'] + plan['modified'] + plan['deleted'] + [MANIFEST]
    original = {}
    destination.mkdir(parents=True, exist_ok=True)
    for name in changed:
        path = safe_target(destination, name)
        original[name] = (path.read_bytes(), path.stat().st_mode) if path.exists() else None
    try:
        for name in plan['added'] + plan['modified']:
            path = safe_target(destination, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            content, executable = files[name]
            path.write_bytes(content)
            path.chmod(0o755 if executable else 0o644)
        for name in plan['deleted']:
            safe_target(destination, name).unlink()
        manifest = {'source': provenance, 'files': sorted(files)}
        (destination / MANIFEST).write_text(json.dumps(manifest, indent=2) + '\n')
    except Exception:
        for name, saved in original.items():
            path = destination / name
            if saved is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(saved[0])
                path.chmod(saved[1])
        raise
    # Remove only empty ancestors of managed deletions; leave unrelated folders alone.
    for name in plan['deleted']:
        parent = (destination / name).parent
        while parent != destination:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent


def sync(archive, destination, *, apply=False, allow_candidate=False, previous_archive=None):
    files, provenance = read_candidate(archive, allow_candidate)
    plan = plan_sync(destination, files, previous_archive)
    print(json.dumps({'source': provenance, 'changes': plan, 'applied': apply}, indent=2))
    if apply:
        apply_sync(destination, files, provenance, plan)
    return plan


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('destination', type=Path)
    parser.add_argument(
        '--previous-archive',
        type=Path,
        help='For a non-Git review copy only: require byte equality with its previous archive',
    )
    parser.add_argument(
        '--apply', action='store_true', help='Apply the preview to local files only'
    )
    parser.add_argument(
        '--allow-candidate',
        action='store_true',
        help='Explicitly permit an uncommitted review candidate',
    )
    args = parser.parse_args()
    sync(
        args.archive,
        args.destination.absolute(),
        apply=args.apply,
        allow_candidate=args.allow_candidate,
        previous_archive=args.previous_archive,
    )
