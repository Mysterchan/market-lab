# Prepare a public source update

Develop and keep internal plans in the development checkout. The public checkout contains
only source, tests, examples, licenses and public guides. The tools below do not push,
commit, tag, configure GitHub permissions or decide that a release is approved.

## Choose the source revision

After reviewing and committing a change, export its exact commit:

```bash
python tools/build_source_release.py --ref HEAD --output build/releases
```

The exporter reads Git objects, not current files. Uncommitted edits cannot silently enter
this archive. It selects the documented public roots in `build_source_release.py`, rejects
symlinks and excludes internal plans, credentials, recordings, virtualenvs and build output.
`SOURCE_PROVENANCE.json` records the source commit and product version. The same commit
produces the same archive bytes with the same exporter. A commit identifies source; it is
not evidence that somebody reviewed or tested it.

For review before committing, use `--working-tree`. This includes non-ignored new public
files, labels the archive `candidate`, and records `source_kind=working-tree-candidate`.
Its recorded commit is only the base commit. It must not be presented as a committed release.
Do not edit files concurrently with an export or sync.

## Preview and apply a local sync

Substitute the actual archive filename and public destination:

```bash
python tools/sync_public_source.py build/releases/market-lab-0.1.0-source.tar.gz /path/to/market-lab
# Review the added, modified and deleted file lists, then apply the same archive:
python tools/sync_public_source.py build/releases/market-lab-0.1.0-source.tar.gz /path/to/market-lab --apply
```

A preview writes nothing. A first sync requires an empty destination; a pre-existing `.git`
directory is allowed. Later syncs require the destination to be a clean Git repository root
with `.market-lab-sync.json` committed from the preceding sync. Keep that manifest: it owns
the file list and source provenance. Only files in that previous list can be deleted.
Files added separately in the public repository are preserved; a conflicting new source
path causes an error rather than an overwrite. Symlink destinations are rejected.

An uncommitted review archive additionally needs `--allow-candidate`, including for a
preview. Before Git history is initialized, an unchanged review copy can be updated using
its exact previous archive:

```bash
python tools/sync_public_source.py new-candidate-source.tar.gz /path/to/market-lab \
  --allow-candidate --previous-archive previous-candidate-source.tar.gz
# After reviewing the plan, repeat with --apply.
```

Keep that previous archive before creating the next one. This option compares the manifest,
file list and every file's bytes/mode; edits, added or missing files block the update. It
cannot bypass the clean-worktree rule once the public directory is a Git repository.
After review, initialize Git if needed and commit the candidate plus manifest on a review
branch. The tool does not initialize or modify history. An interrupted process can leave a
partial copy; inspect/restore before retrying. Ordinary write exceptions attempt to restore
changed file contents.

Do not use a whole-folder mirror or copy `.git` from the internal repository. Bring public
hotfixes and external changes back into the internal checkout before the next export.
The clean-worktree check prevents accidental overwrites, but does not perform this merge.

## Validate the public candidate

Use a clean extraction or public review branch and install dependencies there. Run the
[verification checks](verification.md), including enabled Desktop tests where supported,
CLI demos, and a native window check before making rendering claims. CI source jobs export
the actual workflow commit and run from the extraction, so omitted source files can fail CI.
Changes to workflows themselves still need review; a passing workflow is not a design audit.

On GitHub, configure branch protection separately: require the actual source, Desktop and
OS/Python lifecycle checks, disallow direct updates to the release branch, and require the
maintainer's review. These account settings are not configured by this repository. Check
actual results after each push; additional OS jobs are not accepted just because YAML exists.
Pull requests follow [governance](../../GOVERNANCE.md): a pull request must satisfy
behavior, ownership/design, documentation and license review as well as tests.
Publication, release tags and support promises remain explicit maintainer decisions.

## Cut a release

1. Move the changelog's **Unreleased** entries under `## X.Y.Z — YYYY-MM-DD` and set
   `__version__` in `sim_platform/__init__.py` and `version` in `CITATION.cff`.
2. Check the public API: `python tools/check_public_api.py` must pass; a breaking change
   needs a major version ([API stability](../reference/api-stability.md)).
3. Build the API reference for the version: `python tools/build_api_reference.py`
   (or `tools/build_pages.py`, which includes it). Each version keeps its own
   `api/X.Y.Z/` folder and `api/versions.json` lists them.
4. Commit, then push the tag `vX.Y.Z`. The [release workflow](../../.github/workflows/release.yml)
   checks that the tag, version and changelog agree, builds the wheels, sdist and
   installers, and waits for a maintainer to approve the `pypi` environment (PyPI
   trusted publishing, no stored token) and the `release` environment (a GitHub Release
   with the installers, `SHA256SUMS` and build provenance attestations).

Before the first publication, register the trusted publisher on PyPI for the project
`market-lab-research` (the distribution name; the command stays `market-lab`) and the repository
and the `release.yml` workflow, create the two environments with required reviewers, and
add the macOS signing secrets named in the workflow. Windows code signing needs a
certificate; until then the Windows installer is unsigned.

[Section index](README.md) · [All documentation](../README.md)

For local binary candidates and explicit public-release gates, see [Desktop release](desktop-release.md).
