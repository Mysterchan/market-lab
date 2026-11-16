# Trading commands and retained results

`TradingTools` in `commands.py` runs on the financial owner. It uses injected submit
and cancel capabilities through ExecutionCommands, Coordinator and Service.

`requests.py` keeps bounded request identities and original outcomes in RAM. A repeat
with the same ID and content returns that outcome; conflicting content is rejected.
It never repeats a financial transaction or creates a separate SQL receipt store.

`financial.py` defines FinancialCapture and its result formatter. Detached captures
may cross to Tools; the financial writing capability stays in the App.
See the [complete tool flow](../README.md).
