# Local models, budgets and run lineage

Three controls keep an Agent study private, affordable and reproducible:

- a **local model** keeps prompts and market data on your machine or network;
- a **budget** caps the tokens or US dollars one run may spend on its model;
- **lineage** records exactly what each run used and proves later that nothing changed.

## Use a local model server

Any server with the OpenAI-compatible Chat Completions endpoint works: vLLM, Ollama,
llama.cpp's server and LM Studio. Start the server, then choose the
`openai-compatible` provider and give its address:

```python
from sim_platform.models.contracts import ModelProfile

# Ollama on this machine; no key is needed.
model = ModelProfile('openai-compatible', 'qwen2.5:7b', base_url='http://127.0.0.1:11434/v1')
app.configure_experiment_model(experiment, model)
```

For a server that checks a key (vLLM's `--api-key`), store the key with
`app.set_model_credential('local-key', key)` and pass `credential_ref='local-key'`.

`base_url` must name this machine or a private network: `localhost`, a loopback address,
or a private or link-local IP address such as `192.168.1.20`. Public hosts are refused,
so a local profile cannot send your data off premises. The adapter translates the
Agent's requests into chat messages and back; tool calls, temperature and the output
token limit are passed through, and the model name the server reports is recorded with
each reply, as for the hosted provider.

In the Desktop, choose **Local server: vLLM, Ollama, llama.cpp** under **Model provider**
when editing an Agent and enter the server URL. Leave the credential reference empty if
the server needs no key.

Local models differ in how well they follow tool schemas. Rehearse an Agent with
`RehearsalProvider` first, then run a short window with the local model and read its
tool calls in the Agent log before a full study.

## Cap a run's model spending

Add `budget` to the Agent experiment settings:

```python
settings['budget'] = dict(max_tokens=200_000)
# or, priced in US dollars per million tokens:
settings['budget'] = dict(max_usd=5, prices=dict(input=2.5, output=10, cached_input=1.25))
```

Tokens count input plus output tokens as the provider reports them. Before each model
request the Runtime checks what the run has spent; once the budget is reached, the
request is refused and the run ends cleanly with status `stopped`, recorded as ended by
its budget. Orders already accepted keep their normal lifecycle. The reply that crossed
the limit has already been paid for, so a run can end slightly above its budget.

The Agent log records a `budget_exceeded` notice with the tokens and dollars spent, and
evaluations show it in the run's model usage (`budget_exceeded`). Treat a run that its
budget ended as a different treatment from one that ran its whole window: compare like
with like, or report both.

## Prove a run is unchanged

Every run records its lineage automatically in three parts, each written once and never
replaced:

| Part | Recorded when | Contents |
| --- | --- | --- |
| `inputs` | the run binds to its market | package version, Git commit and whether the checkout had uncommitted changes, Python and OS, execution-rule and native-library digests, a digest of the run configuration, the SHA-256 of every input file or folder (recording, news, fundamentals, documents, model file) |
| `records` | the run ends | status, number of events and digests of its events and decisions |
| `agent` | an Agent's log is archived | log digest, model profile (provider, model, temperature, server address), the model versions the provider served and the Agent definition's digest |

```python
lineage = app.run_lineage(profile_id, run_id)
proof = app.verify_run(profile_id, run_id)
if not proof['unchanged']:
    print([check['item'] for check in proof['checks'] if not check['match']])
```

`verify_run()` recomputes every digest from what is stored now: the run configuration,
each input file, the events and decisions and the archived Agent log. A changed or
missing input file, an edited seal or a changed record each appears as a failed check.
`code_changed` reports whether the code now running differs from the run's version,
commit or execution rules; that does not change the stored run, but Replay may then
disagree with it.

Lineage files live in the data folder under `lineage/<run id>/`. They are local files,
not signatures: someone with write access to the data folder could rewrite both a
record and its seal. Copy the lineage with a study's report when results must be
checked by someone else.

[Section index](README.md) · [All documentation](../README.md)
