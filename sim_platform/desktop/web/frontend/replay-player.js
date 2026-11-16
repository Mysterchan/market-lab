/* Browser playback of a verified Replay. It only moves a cursor over recorded
   events at their recorded spacing; it never reruns a model or matches an order. */
import { s } from "./state.js";
import { render } from "./shell.js";

const TICK_MS = 30;
let timer;

const lastIndex = () => Math.max(0, (s.replay?.events?.length || 0) - 1);

export function playReplay() {
  s.replayPlaying = true;
  clearInterval(timer);
  if ((s.replayIndex || 0) >= (s.replay?.events?.length || 0) - 1)
    s.replayIndex = 0;
  const started = performance.now(),
    origin = Number(s.replay?.events?.[s.replayIndex || 0]?.time_ns || 0);
  timer = setInterval(() => {
    const events = s.replay?.events || [];
    // Recorded time reached so far, in nanoseconds, at the chosen speed.
    const target =
      origin + (performance.now() - started) * 1e6 * (s.replaySpeed || 1);
    let index = s.replayIndex || 0;
    while (
      index < events.length - 1 &&
      Number(events[index + 1].time_ns) <= target
    )
      index++;
    if (index >= events.length - 1) {
      clearInterval(timer);
      s.replayPlaying = false;
    }
    if (index !== (s.replayIndex || 0) || !s.replayPlaying) {
      s.replayIndex = index;
      if (s.route === "replay") render();
    }
  }, TICK_MS);
  render();
}

export function pauseReplay() {
  clearInterval(timer);
  s.replayPlaying = false;
  render();
}

export function nextReplayEvent() {
  s.replayIndex = Math.min((s.replayIndex || 0) + 1, lastIndex());
  render();
}
