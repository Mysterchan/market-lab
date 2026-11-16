# Maintain the documentation

The root README owns installation and the shortest route to a first run. `docs/README.md`
helps readers choose one of five sections:

| Section | Reader and content |
| --- | --- |
| `user-guide/` | App users: workflows, controls, troubleshooting and supported scope |
| `agent-development/` | Agent authors: factory/SDK, source import, dependencies and Python |
| `architecture/` | Maintainers: component cooperation, process/state ownership, time and shutdown |
| `development/` | Contributors/maintainers: code navigation, conventions, verification and releases |
| `reference/` | API users: precise interface and native input/CLI details |

Each section has one README explaining a reading order. Every guide should be reachable
from its section index. The five sections retain ownership of their guide content.
`docs/site/` contains the website shell, homepage and shared tutorial media; it does not
hold duplicate HTML editions of guides. The [website build](../site/README.md) renders
the maintained Markdown for GitHub Pages, with separate App and Agent/SDK entry paths.
Store images
with the owning guide. The Desktop visual assets belong under `development/desktop-design/`.

## One owner for each explanation

Put the full explanation in one guide and link to it elsewhere. A package README explains
what that folder owns and which file to read next; it should not repeat a user tutorial or
the entire architecture. Platform method docstrings own exact signatures, types, units,
side effects and lifecycle; the API guide explains how to choose and combine those methods.

Write simple English for someone new to the repository. Explain what the reader can do,
what input is needed and what happens next. Use a complete runnable example where useful;
label fragments that require existing objects. Keep component names tied to their purpose.

Use English in all current guides, package READMEs and code docstrings. Original internal
archives keep their original language and remain excluded from public export.

Run `python tools/check_documentation.py` before submitting a documentation change.
It checks language, local file links, Markdown headings and public/internal separation.
Use `--include-internal` in the development checkout. The checker needs no Git history
or network, so it also runs against exported source in CI. It cannot judge whether an
explanation is accurate or easy to read; compare the guide with its code and caller flow.

Internal plans, discussions, investigations and original historical documents belong under
`internal/` in the development checkout and are excluded from public sync. Public guides
must not depend on internal links. Tests load data from `tests/fixtures/`; benchmark inputs
live in `tools/fixtures/`. Generated measurements belong in `build/` or internal evidence,
not among the guides. Preserve licenses when moving design assets.

## Review a documentation change

- Update the owning current guide and its section index; merge duplicate explanations.
- Describe implemented behavior accurately. Put future scope in the roadmap, not API examples.
- Verify links and anchors, code-span paths and all consumers of a moved file.
- Run runnable examples/help and any tests that read moved fixtures. Do not run paid models
  or full-day performance experiments merely to validate links.
- Preserve the visual reference unless a deliberate design change has been agreed.
- Preview the public candidate and check old paths are removed as well as new paths added.

Support and CI configuration are separate from actual acceptance results. Keep measurements
with their source/environment and limitations rather than rewriting historical evidence.

## Documentation language

Describe the current feature, its inputs, results and limits. Do not explain public
behavior through private development task numbers or the order in which the team
built it. Keep historical planning and investigation records under internal/.
Public architecture diagrams should explain the running application, not its
development history. Future product directions belong in the roadmap.

Use "checkpoint" only for an actual saved processing state, and explain what is
saved and how it is used. It is not a label for development progress.

[Section index](README.md) · [All documentation](../README.md)
