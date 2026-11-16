# Agent-local model IO

Platform.configure_experiment_model validates one ready Agent experiment and sends
only its selected profile/credential/provider over the dedicated setup pipe.
AgentDecisionRunner owns the Agent host, not an App model worker.

- agent.py: Agent-local provider IO, raw receipt hook, bounded redaction/parse,
  and following attempt/retry permits from Runtime.
- contracts.py: ModelProfile, payload bounds and provider error classifications.
- credentials.py / keychain.py: explicit session or macOS OS storage; no plaintext fallback.
- credential_file.py: one selected variable from an explicit .env path; no shell,
  expansion, bulk environment forwarding or logging of values.
- providers/: async HTTP protocol, bounded input/body and classifications; no retry policy.

Prompt, key and full model output remain in Agent. Runtime receives identity,
receipt time and error metadata. At deadline, expiry_pending waits for the trusted
receipt seal; an on-time reply with delayed parse is retained. Missing seal has a
separate five-second wall deadline and is a platform failure, not a model retry.
Pause freezes active time and delivery; Stop cancels Agent IO and closes its host.

There is no additional ModelProvider process. These modules do not import the
application/market/financial engine/ORM/Qt. The built-in conversation loop is in
[agents/carrier](../agents/carrier/README.md); it can supply a response validator so
format errors use Runtime retry before tools run. Uploaded-code OS isolation and
non-macOS persistent key storage remain separate work. Automated integration uses scripted/local providers, not paid API acceptance.

[Verification and support scope](../../docs/development/verification.md) ·
[Tests](../../tests/platform/test_models.py).
