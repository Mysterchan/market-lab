# Model examples

Files for the model decision method, which trades on what your own Python model returns.
They run in a separate process and do not import the platform.

- `moving_average.py`: a standard-library trend rule that returns a target position.

See the [model guide](../../docs/user-guide/model-policies.md) for the function, the
values it receives and how its answer becomes orders.
