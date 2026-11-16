/* The only link to Python: named commands out, published events in.

   The Qt bridge accepts a fixed list of command names and JSON arguments. The page
   has no filesystem, SQL or network access of its own. */
import { s } from "./state.js";

let bridge, noticeTimer;

/* Ask Python to run a named command. Before the channel connects this does nothing. */
export const send = (name, args = {}) =>
  bridge?.invoke(name, JSON.stringify(args));

/* Show a short message, and keep it in the activity records. Errors stay longer. */
export function notice(text, failed = false) {
  const node = document.querySelector("#notice");
  s.notices.push({
    time: new Date().toLocaleTimeString(),
    message: String(text),
    status: failed ? "Error" : "Notice",
  });
  if (s.notices.length > 500) s.notices.shift();
  node.textContent = text;
  node.className = "visible" + (failed ? " failed" : "");
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => (node.className = ""), failed ? 12000 : 3500);
}

/* Open the channel. `onEvent` receives each published {kind, value}. */
export function connect(onEvent, onReady) {
  new QWebChannel(qt.webChannelTransport, (channel) => {
    bridge = channel.objects.desktop;
    bridge.event.connect((encoded) => onEvent(JSON.parse(encoded)));
    onReady();
  });
}
