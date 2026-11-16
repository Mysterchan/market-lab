# Shared test inputs and resource ownership

These modules contain helpers, not collected tests. Test modules may import them;
helpers do not import test modules. Keep expectations in the tests that assert them.

| File | Responsibility |
| --- | --- |
| `agents.py` | Synthetic saved Agent definitions and run settings |
| `clock.py` | Shared deterministic time with optional closed-market skips, a stand-in for the App's clock during assembly, and bounded condition waits |
| `scheduling.py` | Runtime settings and a manual in-process clock |
| `trading.py` | Trading source events and positioned request envelopes |
| `tools.py` | Detached market/account values, tool envelopes and schema checks |
| `async_reads.py` | Owned real-process fixtures with explicit cleanup registration |
| `prepared_market.py` | Small prepared source and temporary store setup |
| `book.py` | Indexed order-book source and prepared reader setup |
| `financial.py` | Trusted native cursor/financial probe assembly |
| `carrier.py` | Scripted model replies and reusable Agent experiment setup |
| `models.py` | Agent-local model provider and process fixtures |
| `execution.py` | Account/market scenario; direct callers must close the owned fixture |
| `desktop.py` | Qt window setup and shutdown; Qt imports happen only during setup |
| `failures.py` | Isolated expected-crash capture, diagnostic checks and private-output assertions |

Setup mixins use the caller's `addCleanup` registration. ExecutionFixture also supports
direct ownership with `close()` and cleans partially initialized resources on failure.
No helper silently takes ownership of another TestCase's cleanup stack.

Native frame builders and the independent price-level reference live in
[itch_fixtures.py](../../itch_fixtures.py), shared by native and platform suites.
