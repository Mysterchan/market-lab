# Contributing to Market Lab

Thank you for helping. Market Lab is a research tool: a change is judged first by whether
experiment results stay correct and reproducible, then by how easy it is to maintain.

Pull requests, issues and discussion are welcome. [Governance](GOVERNANCE.md) explains
who reviews and decides; changes to simulated results or the public API have extra rules
there.

## Set up

You need Python 3.9–3.12, Make and a C++17 compiler.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pip install -e . --no-deps
make all
```

Add `-r requirements-desktop.txt` when you work on the Desktop.

## Before you open a pull request

Run the same checks as CI:

```bash
make test
make python-quality
python tools/check_documentation.py
```

To measure test coverage of the platform package:

```bash
python -m coverage run -m unittest discover -s tests/platform -p 'test_*.py'
python -m coverage report
```

[Verification](docs/development/verification.md) explains what each check covers and how
to run Desktop suites.

## What a good change looks like

- **One purpose per pull request.** Separate refactors from behaviour changes.
- **Financial rules go through the shared execution path.** Manual, rule, model, Agent and
  Replay experiments must keep producing the same fills for the same inputs.
- **No look-ahead.** Any new data source must only show what was published before the
  experiment clock. Add a test that proves a later record is hidden.
- **Tests beside the behaviour.** Add or update a test in `tests/platform/` for every fix.
- **Documented public API.** New public `Platform` methods need type annotations and a
  docstring; see [coding guidelines](docs/development/coding.md).
- **Diagnostics through logging.** Use `logging.getLogger(__name__)`; never log credentials.
  `print` is only for command-line output.
- **Clear names over comments.** Describe what code does for a new maintainer.

## Commit messages

Write the subject in the imperative mood (for example `Add borrow costs to short sales`),
at most 72 characters, and explain why in the body when it is not obvious.

## Reporting problems

Open an issue with steps to reproduce, your OS and Python versions and redacted logs
(`python -m sim_platform --log-level debug ...`). Report security problems privately as
described in [SECURITY.md](SECURITY.md).

## Licence

Contributions are accepted under the [Apache-2.0 licence](LICENSE).
