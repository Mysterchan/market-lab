# Coding and component guidelines

Start at [Platform](../../sim_platform/application/platform.py) for public setup and the
[package map](../../sim_platform/README.md) for responsibilities. Folders describe functions;
`host.py` generally controls a child and `worker.py`/`process.py` holds its entrypoint.
A runner/controller is an object, not an implicit thread.

## Ownership and dependencies

- Platform constructs shared resources and registers roots; lower layers do not create it.
- Controllers own their runner/market; runners own method-specific background resources.
- Domain rules do not import ORM/UI. Persistence handles file/SQL IO, not trading decisions.
- Financial changes follow the shared transaction path; publish RAM only after commit.
- Agent local tools remain in Agent; platform commands retain validation and financial ownership.
- Qt widgets display data and submit commands through existing controllers, not a second engine.
- Public package initializers stay lightweight and avoid eager optional Qt imports.

The [boundary tests](../../tests/platform/test_module_boundaries.py) check selected import
directions. They do not detect all private-field coupling or guarantee lifecycle correctness.

## Functions and interfaces

Give a function one clear operation with a name explaining its purpose. Separate validation,
resource assembly, business work and rendering when they have independent responsibilities.
Keep genuinely shared helpers close to their owner; do not create a generic utilities dump
or a class whose only purpose is forwarding another class's methods.
Name the owning use-case service explicitly inside transaction boundaries; avoid searching
multiple services for a string method name. UI builders return named controls or emit signals
instead of attaching undocumented attributes to a caller.

Document boundary inputs/results, units, synchronous waiting, side effects, state ownership
and failure behavior. Prefer named types/Protocols for stable internal shapes. Keep wire
values detached and finite JSON. Do not introduce contract-version labels or business hashes
as a substitute for a clear interface. Normal distribution versions, external protocol fields
and database migration identifiers still have their legitimate meanings.

Within a Python class/module, define helper dependencies before their callers, consistent
with the existing project rule. Avoid modifying another class's private fields when extending
an adapter. Existing such coupling is debt to remove, not a pattern to copy.

## Public API documentation

Every public Platform method needs parameter/return annotations and a docstring explaining
when to use it, what it does, what is returned, and the next step. Include useful examples,
units/defaults, blocking work, lifecycle/resource ownership and major failure conditions.
Fixed dictionary shapes belong in `application/api_types.py`; these are descriptions,
not new runtime wrappers. Distinguish saving, preparing, Play, Pause, terminal state and close.
Use simple English that a new maintainer can follow. Keep development-stage names out of
function explanations, comments and user errors.

## Formatting and tests

Python uses four-space indentation, a 100-column target, Black and Ruff pinned in
`requirements-dev.txt`, with settings in `pyproject.toml`. `make python-quality` checks
`sim_platform`, `tests/platform` and `tools`. Ruff checks basic errors, likely bugs,
inconsistent returns and functions with more than 20 branches; split such a function into
named steps instead of silencing the rule. Mypy checks the packages listed under
`[tool.mypy]` in `pyproject.toml`, where every function must be annotated. Type checking is
adopted one package at a time: annotate a package, add it to that list, and it stays checked.
C++ uses C++17, the root `.clang-format` and Doxygen-style interface comments in headers.
Run `make format-check` with a compatible clang-format when changing native code.

Desktop frontend pages are built from small section functions that each return one part
of the page (for example `toolbar`, `stats`, `records`). Add a section function instead of
extending one long template string, and keep the class names that `updateWorkspace` swaps.
Buttons, forms and published events each have one table or binder to extend; see the
[frontend ownership map](../../sim_platform/desktop/web/frontend/README.md).

Choose independent expected behavior, not assertions that repeat the implementation.
Keep test fixtures reusable without manually invoking another TestCase's setup. Use controlled
clocks for ordering/deadline tests; real elapsed-time/performance experiments are separate.
See [verification](verification.md) for commands, CI scope and known gaps.

[Section index](README.md) · [All documentation](../README.md)
