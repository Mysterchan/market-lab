# Controlled and Live experiments

Execution mode is separate from the setup method. `open_research()` returns an initially
empty controller; `open_experiment()` prepares one in a call. Neither chooses a different
backend. Pass `execution_mode='controlled'` or `'live'` when creating the experiment.

```python
research = app.open_research(method='human')
research.create_experiment(profile_id, account_id, source=source_path,
                           execution_mode='live')
# Equivalent one-call preparation:
experiment = app.open_experiment(profile_id, account_id, source=source_path,
                                method='human', execution_mode='live')
```

These are alternative setup examples, not instructions to open two writers on one account.
Preparation is complete before Play. Changing mode requires a fresh experiment.

| Mode/method | Execution behavior | Controls |
| --- | --- | --- |
| Controlled Human/Rule | Synchronous PolicyDecisionRunner; caller polls/steps prepared events | Step, playback speeds and compatible policy handoff |
| Live Human/Rule | Background ExperimentSession and Runtime; Rule computation in child | 1× Play/Pause/Stop; no controlled-only step/speed/handoff |
| Controlled Model | Synchronous PolicyDecisionRunner; your model runs in its own process, once per bar | Step and playback speeds; Live is not available |
| Agent, either selected mode | Current Agent background path remains 1× with Runtime and platform tools | Play/Pause/Stop; selecting Controlled does not give an Agent single-step or accelerated thinking |
| Agent, turn-based (`speed='turn'` in its settings) | The market waits while the Agent works and moves to its next turn when it is idle ([turn-based Agents](evaluation.md#turn-based-agents)) | Play/Pause/Stop; choose **Turn-based** under Pacing in New experiment, or use the Python API and suites |

Desktop sends its selected mode explicitly. The UI poll refreshes Live views; it does not
drive experimental time. Prepared data is the shared historical-data implementation, not
a third mode. Market reads use clock cutoff; financial events/commands have an ordered
processed frontier. All methods retain the same financial transaction path.

See [support](support.md) for product limits, [architecture](../architecture/experiments.md)
for ownership and [Platform API](../reference/platform-api.md) for setup parameters.

[Section index](README.md) · [All documentation](../README.md)
