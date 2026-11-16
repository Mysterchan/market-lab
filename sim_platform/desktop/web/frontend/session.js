/* Human labels and controls derived from a published run, not frontend guesses. */
export function policyLabel(view) {
  if (view.mode === "agent" || view.run?.config?.agent)
    return view.run?.config?.agent?.definition?.name || "Agent";
  const policy = view.run?.config?.decision?.policy;
  if (policy?.kind === "model")
    return (
      "Model · " +
      (policy.parameters?.model_file || "").split(/[\\/]/).at(-1)
    );
  return policy?.kind === "human" ? "Manual" : policy?.name || "Manual";
}
/* How the experiment's time passes, for its header badge. */
export function paceLabel(view) {
  const agent = view.run?.config?.agent;
  if (view.mode === "agent" || agent) return agent?.speed === "turn" ? "turn-based" : "live";
  return view.execution_mode || "";
}
export function runStatus(view) {
  if (view.mode === "saved") return "Read-only result";
  const end = view.execution?.status || view.run?.status;
  if (["stopped", "completed", "failed", "cancelled", "ended"].includes(end))
    return end[0].toUpperCase() + end.slice(1);
  const pipeline = view.pipeline?.state;
  if (isPlaying(view))
    return "Running";
  if (pipeline === "failed") return "Failed";
  if (pipeline === "ended") return "Ended";
  return "Paused";
}
export function sessionName(s, id) {
  const packet = s.sessions[id];
  return (
    packet?.name ||
    (s.catalog.accounts || []).find(
      (account) => account.id === packet?.view.run?.account_id,
    )?.name ||
    `${packet?.view.market?.symbol || "Experiment"} research session`
  );
}

export function isPlaying(view) {
  return view?.pipeline ? view.pipeline.state === "running" : view?.market?.status === "playing";
}

/* The symbols of an experiment on several symbols, in the order they were chosen;
   empty for one symbol, whose view has no per-symbol markets. */
export function symbolsOf(view) {
  return Object.keys(view?.markets || {});
}
/* The symbol a workspace shows: the one picked, else the experiment's first. */
export function focusedSymbol(s, id) {
  const symbols = symbolsOf(s.sessions[id]?.view);
  if (!symbols.length) return null;
  return symbols.includes(s.symbols[id]) ? s.symbols[id] : symbols[0];
}
/* The published view seen through the shown symbol: its book, bars and trades. */
export function focusedView(s, id) {
  const view = s.sessions[id]?.view;
  const symbol = focusedSymbol(s, id);
  if (!symbol) return view;
  const chosen = view.markets[symbol];
  return {
    ...view,
    market: chosen.market,
    bars: chosen.bars || [],
    trades: chosen.trades || [],
    daily_closes: chosen.daily_closes || view.daily_closes,
  };
}
