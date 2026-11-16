## What and why

<!-- One purpose per pull request. Link the issue it closes. -->

## Checklist

- [ ] `make test` passes
- [ ] `make python-quality` passes
- [ ] `python tools/check_documentation.py` passes
- [ ] Tests cover the change
- [ ] Results are unchanged for existing experiments, or the change explains why they differ
- [ ] New data paths hide records published after the experiment clock
- [ ] Public API changes have annotations, docstrings and a CHANGELOG entry
