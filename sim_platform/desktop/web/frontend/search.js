/* The ⌘K dialog: jump to a page or an open experiment. */
import { s, navigation } from "./state.js";
import { esc, button as b } from "./ui.js";

const dialog = document.querySelector("#commands"),
  input = document.querySelector("#command-search");
let choose = () => {};

function destinations() {
  const experiments = s.tabs.map((id) => [
    "open:" + id,
    (s.sessions[id]?.view.market?.symbol || "Experiment") + " · " + id.slice(0, 6),
    "chart",
  ]);
  return [
    ...navigation,
    ["new", "New experiment", "plus"],
    ["settings", "Settings", "settings"],
    ...experiments,
  ];
}

function showResults() {
  const wanted = input.value.toLowerCase();
  document.querySelector("#command-results").innerHTML = destinations()
    .filter(([, title]) => title.toLowerCase().includes(wanted))
    .map(([id, title, symbol]) => b(esc(title), id, "command-result", symbol))
    .join("");
  dialog.querySelectorAll("[data-action]").forEach(
    (node) =>
      (node.onclick = () => {
        dialog.close();
        choose(node.dataset.action);
      }),
  );
}

export function showSearch() {
  input.value = "";
  showResults();
  dialog.showModal();
  input.focus();
}

/* Wire the dialog once. `onChoose` receives the chosen action name. */
export function initSearch(onChoose) {
  choose = onChoose;
  input.oninput = showResults;
  input.onkeydown = (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      dialog.querySelector("[data-action]")?.click();
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      dialog.querySelector("[data-action]")?.focus();
    }
  };
  // A click on the backdrop, outside the dialog's content, closes it.
  dialog.addEventListener("click", (e) => {
    if (e.target === dialog) dialog.close();
  });
  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
      e.preventDefault();
      showSearch();
    }
  });
}
