# Governance

Market Lab is a research tool. Decisions favour, in order: the validity of experiment
results, their reproducibility, then convenience. This page says who decides and how.

## Roles

| Role | Who | Can |
| --- | --- | --- |
| Maintainer | Listed in [CODEOWNERS](.github/CODEOWNERS) | Merge pull requests, cut releases, set the roadmap, handle security reports |
| Reviewer | Contributors invited by the maintainers | Review and approve pull requests in the areas they know |
| Contributor | Anyone | Open issues and pull requests, take part in discussions |

A contributor becomes a reviewer after a sustained record of careful contributions in an
area, by agreement of the maintainers. A maintainer is added the same way and steps down
by telling the other maintainers; inactive maintainers become emeritus after a year.

## How decisions are made

- **Everyday changes** need one approving review from a maintainer or a reviewer of that
  area, and green CI.
- **Changes to simulated results** (fills, fees, metrics, statistics, look-ahead rules)
  need a maintainer's approval and a test that states the new expected result. The
  changelog names the change, and the [methodology](docs/reference/methodology.md) is
  updated in the same pull request.
- **Public API changes** follow [API stability](docs/reference/api-stability.md): additions
  in minor releases, removals only after a deprecation and in a major release.
- **Larger proposals** (a new subsystem, a new dependency of the core, a change of
  licence or governance) start as an issue labelled `proposal`, stay open for comment for
  at least two weeks and are decided by the maintainers, who record the reason. Lasting
  architectural decisions are written down in `docs/adr/`.

Maintainers seek consensus. When it cannot be reached, the maintainers decide by simple
majority; with one maintainer, that maintainer decides and explains why.

## Releases

Maintainers cut releases from `main` following [the release guide](docs/development/releases.md).
Every release passes CI on macOS, Linux and Windows, the public API check, the dependency
audit and the documentation check, and lists its changes in the [changelog](CHANGELOG.md).

## Conduct and security

Be respectful and specific; criticise ideas, not people. Maintainers may remove comments,
pull requests or contributors that harass others. Report security problems privately as
described in [SECURITY.md](SECURITY.md), never in a public issue.
