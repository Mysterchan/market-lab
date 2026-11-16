# Shared experiment infrastructure

Human, Rule and Agent experiments share execution infrastructure. Work scheduling
is independent of model IO and financial execution. This package supplies the
mechanisms; [Platform assembly](../application/experiments/README.md) connects them.
No component here constructs Platform or becomes a second public setup entry.

## Before Play

`open_research()` returns an empty controller. Its `create_experiment()` prepares the
source and creates a run. `open_experiment()` performs both steps for the caller.
For an Agent, assembly prepares the shared clock, Runtime, financial execution,
Tools host, runner and Agent host. The Agent child calls `create_agent(context)` and
reports ready before setup returns. The clock is still stopped. Configure the model
before Play; the model adapter is lazy during Agent construction.

## After Play

- [scheduling/worker.py](scheduling/worker.py): the Runtime child schedules work,
  event waits and model-request attempts. It does not perform model IO or trade.
- [execution/session.py](execution/session.py): the App's ExperimentSession starts
  an `execution-owner` thread to process the ordered financial timeline.
- [execution/delivery.py](execution/delivery.py): the App output thread records and
  delivers results without making financial decisions. It starts during preparation.
- [execution/controls.py](execution/controls.py): Play/Pause/Stop coordinate participant
  gates and acknowledgements. The caller's thread invokes these controls.
- [recording.py](recording.py): ordinary ordered JSON recording for policy experiments.

The shared clock derives time from monotonic elapsed time. For a multi-day recording
it also skips the closed market between sessions; see `SessionGaps` in
[scheduling/clock.py](scheduling/clock.py). It is not a separate
process or a thread incrementing time. Visibility queries use immutable prepared
data at that time, independently of the financial owner's processed frontier.

## Related code

- [decisions](../decisions/README.md): method-specific runners.
- [agents/hosting](../agents/hosting/README.md): Agent child lifecycle and communication.
- [agents/tools](../agents/tools/README.md): platform tools and formatter child.
- [execution](../execution/README.md): financial session, validation, OMS and fills.

`ExperimentSession` here owns the background flow. `ExecutionSession` in the financial
package owns the run's financial execution. Their similar names do not mean two
financial owners or two different market clocks.
