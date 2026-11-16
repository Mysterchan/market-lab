# Desktop workflows

`agents.py`: save/import an Agent revision and list saved environments through Platform.
`configuration.py`: translate validated form values into the existing public contracts.
`experiments.py`: prepare market data, create/configure an experiment and return its controller paused.
`replay.py`: verify recorded operations through Platform and return displayable results.
`evaluation.py`: list comparable saved runs by group and evaluate a chosen set through Platform.

These workflows are Qt-free adapters. DesktopTasks runs long operations using the
existing Platform-owned background job. Only the worker adopts a returned controller.
No workflow creates another Platform or implements a financial matching path.
