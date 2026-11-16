"""Build a public source archive from one Git commit, or an explicit local candidate.

Default: python tools/build_source_release.py --ref HEAD
For uncommitted review only: add --working-tree. Neither mode publishes anything.
"""

import argparse
import ast
import gzip
import io
import json
from pathlib import Path, PurePosixPath
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]
PROVENANCE = 'SOURCE_PROVENANCE.json'
SOURCE_DIRECTORIES = {
    '.github',
    'src',
    'include',
    'sim_platform',
    'examples',
    'tests',
    'tools',
    'docs',
}
SOURCE_FILES = {
    'README.md',
    'LICENSE',
    'NOTICE',
    'THIRD_PARTY_NOTICES.md',
    'SECURITY.md',
    'CONTRIBUTING.md',
    'GOVERNANCE.md',
    'CITATION.cff',
    'CHANGELOG.md',
    'CMakeLists.txt',
    'Makefile',
    'pyproject.toml',
    'setup.py',
    'MANIFEST.in',
    'requirements.txt',
    'requirements-dev.txt',
    'requirements-desktop.txt',
    'requirements-packaging.txt',
    'requirements-site.txt',
    'requirements-demo.txt',
    '.gitignore',
    '.clang-format',
    '.gitattributes',
}


def public_path(name):
    """Allow public roots, excluding local state; reject noncanonical archive paths."""
    path = PurePosixPath(name)
    if not name or path.is_absolute() or path.as_posix() != name or '\\' in name:
        return False
    if any(
        p in ('.', '..', '.git', '__pycache__', 'node_modules', '.DS_Store') for p in path.parts
    ):
        return False
    if any(p.startswith('.env') for p in path.parts):
        return False
    if path.suffix in ('.bin', '.gz', '.csv', '.pem', '.key', '.pyc') or '.sqlite' in path.name:
        return False
    return name in SOURCE_FILES or path.parts[0] in SOURCE_DIRECTORIES


def git(root, *args):
    # core.autocrlf=false: git archive must return the committed bytes on every OS.
    return subprocess.run(
        ['git', '-c', 'core.autocrlf=false', '-C', str(root), *args],
        check=True,
        capture_output=True,
    ).stdout


def source_files(root=ROOT):
    """List the explicit working-tree candidate; never use this for commit exports."""
    names = git(root, 'ls-files', '--cached', '--others', '--exclude-standard', '-z')
    for name in sorted(set(names.decode().split('\0')) - {''}):
        if not public_path(name):
            continue
        path = root / name
        for parent in (path, *path.parents):
            if parent.is_symlink():
                raise ValueError('Source export cannot contain symlinks: ' + name)
            if parent == root:
                break
        if path.is_file():
            yield Path(name)


def collect(root, ref, working_tree):
    commit = git(root, 'rev-parse', '--verify', ref + '^{commit}').decode().strip()
    if working_tree:
        files = {
            p.as_posix(): ((root / p).read_bytes(), (root / p).stat().st_mode & 0o111 != 0)
            for p in source_files(root)
        }
    else:
        files = {}
        with tarfile.open(
            fileobj=io.BytesIO(git(root, 'archive', '--format=tar', commit))
        ) as archive:
            for member in archive:
                if not public_path(member.name) or member.isdir():
                    continue
                if not member.isfile():
                    raise ValueError('Source export requires regular files: ' + member.name)
                files[member.name] = (archive.extractfile(member).read(), bool(member.mode & 0o111))
    return commit, files


def product_version(content):
    """Read literal product metadata without importing application code."""
    for node in ast.parse(content).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == '__version__' for target in node.targets
        ):
            version = ast.literal_eval(node.value)
            if (
                not isinstance(version, str)
                or not version
                or any(c not in '0123456789.-abcdefghijklmnopqrstuvwxyz' for c in version)
            ):
                break
            return version
    raise ValueError('Product version is missing or invalid')


def build(output, *, root=ROOT, ref='HEAD', working_tree=False):
    """Write a deterministic commit archive; label working-tree exports as candidates."""
    commit, files = collect(root, ref, working_tree)
    required = {
        'README.md',
        'LICENSE',
        'NOTICE',
        'sim_platform/__init__.py',
        'sim_platform/desktop/web/frontend/index.html',
    }
    if not required.issubset(files):
        raise ValueError('Required release files are missing')
    version = product_version(files['sim_platform/__init__.py'][0])
    provenance = {
        'source_commit': commit,
        'source_kind': 'working-tree-candidate' if working_tree else 'commit',
        'product_version': version,
    }
    files[PROVENANCE] = ((json.dumps(provenance, indent=2) + '\n').encode(), False)
    output.mkdir(parents=True, exist_ok=True)
    label = '-candidate' if working_tree else ''
    destination = output / ('market-lab-' + version + label + '-source.tar.gz')
    temporary = destination.with_suffix('.tmp')
    try:
        with (
            temporary.open('wb') as raw,
            gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=0) as compressed,
        ):
            with tarfile.open(fileobj=compressed, mode='w') as archive:
                for name, (content, executable) in sorted(files.items()):
                    info = tarfile.TarInfo('market-lab-' + version + '/' + name)
                    info.size = len(content)
                    info.mode = 0o755 if executable else 0o644
                    archive.addfile(info, io.BytesIO(content))
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    print(destination)
    print(json.dumps(provenance, sort_keys=True))
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'build' / 'releases')
    parser.add_argument(
        '--ref', default='HEAD', help='Exact source commit/ref; uncommitted files are excluded'
    )
    parser.add_argument(
        '--working-tree',
        action='store_true',
        help='Export uncommitted contents for local review only',
    )
    args = parser.parse_args()
    if args.working_tree and args.ref != 'HEAD':
        parser.error('--working-tree cannot be combined with another --ref')
    build(args.output.resolve(), ref=args.ref, working_tree=args.working_tree)
