# Provider adapter

OpenAIResponses runs in the Agent process. It uses standard-library async TLS,
api.openai.com/v1/responses, non-streaming, store=false, truncation=disabled.
There is no provider-level automatic retry. Runtime owns attempts and retry policy.

request_received stamps complete raw-body receipt before JSON parse. Header/body
and payload sizes are bounded; errors are classified without copying raw secrets.
Cancellation closes the socket. Network wall timeout and experiment active-time
attempt deadline are separate bounds. Tests use local mock providers only.

[Verification and support scope](../../../docs/development/verification.md).
