/* Prepared visible data only. Chart calculations never advance the experiment.
   Wheel zooms, drag pans, double-click returns to the latest bar. Overlays show
   published fills and working orders; they are display marks, not valuations. */
window.MarketChart = class {
  constructor(root) {
    this.root = root;
    this.svg = root.querySelector(".m-price-chart");
    this.legend = root.querySelector(".m-chart-tooltip");
    this.bars = [];
    this.fills = [];
    this.orders = [];
    this.interval = 1;
    this.byDay = false; // group bars by calendar day instead of by count
    this.span = null; // visible slots; null follows the data
    this.offset = 0; // slots scrolled back from the latest bar
    this.pointer = null;
    this.current = null;
    this.observer = new ResizeObserver(() => this.draw());
    this.observer.observe(this.svg);
    const svg = this.svg;
    svg.addEventListener("pointermove", (e) => this.move(e));
    svg.addEventListener("pointerleave", () => {
      this.pointer = null;
      this.cross();
    });
    svg.addEventListener("pointerdown", (e) => {
      if (e.button !== 0 || !this.current) return;
      this.drag = { x: e.clientX, offset: this.offset };
      svg.setPointerCapture?.(e.pointerId);
      svg.classList.add("dragging");
    });
    const release = () => {
      this.drag = null;
      svg.classList.remove("dragging");
    };
    svg.addEventListener("pointerup", release);
    svg.addEventListener("pointercancel", release);
    svg.addEventListener("dblclick", () => {
      this.span = null;
      this.offset = 0;
      this.draw();
    });
    svg.addEventListener(
      "wheel",
      (e) => {
        if (!this.current || !this.points.length) return;
        e.preventDefault();
        const l = this.current;
        if (Math.abs(e.deltaX) > Math.abs(e.deltaY)) {
          this.pan(this.offset - e.deltaX / l.gap);
        } else {
          const next = Math.max(
            8,
            Math.min(
              Math.max(l.span, this.points.length + 3),
              Math.round(l.span * (e.deltaY > 0 ? 1.15 : 0.87)),
            ),
          );
          this.span = next;
          this.pan(this.offset);
        }
        this.draw();
        this.cross();
      },
      { passive: false },
    );
  }
  setBars(bars) {
    const identity = JSON.stringify(bars);
    if (identity === this.identity) return;
    this.identity = identity;
    this.bars = bars;
    this.draw();
  }
  setOverlay(overlay) {
    const identity = JSON.stringify(overlay || {});
    if (identity === this.overlayIdentity) return;
    this.overlayIdentity = identity;
    this.fills = overlay?.fills || [];
    this.orders = overlay?.orders || [];
    this.draw();
  }
  pan(offset) {
    const span = this.span || this.current?.span || 30;
    this.offset = Math.max(
      0,
      Math.min(Math.max(0, this.points.length - Math.ceil(span / 2)), offset),
    );
  }
  /* Split the published one-minute bars into the groups that each become a candle. */
  groups() {
    const groups = [];
    if (this.byDay) {
      let day = null;
      for (const bar of this.bars) {
        const index = Math.floor(bar.start_ns / 86400e9);
        if (index !== day) groups.push([]);
        day = index;
        groups.at(-1).push(bar);
      }
    } else {
      for (let i = 0; i < this.bars.length; i += this.interval)
        groups.push(this.bars.slice(i, i + this.interval));
    }
    return groups;
  }
  aggregate() {
    this.points = [];
    for (const rows of this.groups()) {
      this.points.push({
        o: rows[0].open / 10000,
        c: rows.at(-1).close / 10000,
        h: Math.max(...rows.map((r) => r.high)) / 10000,
        l: Math.min(...rows.map((r) => r.low)) / 10000,
        v: rows.reduce((sum, r) => sum + r.volume, 0),
        time: rows[0].start_ns,
        end: rows.at(-1).end_ns ?? rows.at(-1).start_ns + 60e9,
      });
    }
  }
  draw() {
    const svg = this.svg,
      box = svg.getBoundingClientRect(),
      w = Math.max(210, box.width),
      h = Math.max(254, box.height);
    this.aggregate();
    const all = this.points;
    if (!all.length) {
      svg.innerHTML = "";
      this.current = null;
      if (this.legend) this.legend.hidden = true;
      return;
    }
    const labelSize =
      parseFloat(getComputedStyle(svg).getPropertyValue("--m-meta-font")) || 10;
    const longest = Math.max(6, ...all.map((p) => p.h.toFixed(2).length));
    const axis = Math.max(58, longest * labelSize * 0.62 + 20);
    const left = 4,
      right = w - axis,
      top = 26,
      timeBand = 20,
      volumeHeight = Math.round(Math.min(64, h * 0.17)),
      volumeBottom = h - timeBand,
      volumeTop = volumeBottom - volumeHeight,
      bottom = volumeTop - 12;
    const rightPad = 3;
    // Intraday bars stream in from the right, so a short run keeps room for the bars to
    // come; daily bars are a finished history and fill the width they have.
    const span = Math.max(
      8,
      this.span || Math.max(this.byDay ? 8 : 30, all.length + rightPad),
    );
    const offset = Math.round(this.offset);
    const end = all.length + rightPad - offset; // exclusive slot index
    const start = end - span;
    const gap = (right - left) / span;
    const x = (index) => left + gap * (index - start + 0.5);
    const first = Math.max(0, Math.floor(start)),
      last = Math.min(all.length, Math.ceil(end));
    const shown = all.slice(first, last);
    const scope = shown.length ? shown : all.slice(-1);
    let low = Math.min(...scope.map((p) => p.l)),
      high = Math.max(...scope.map((p) => p.h));
    // Whole-cent prices are shown with two decimals even when the price does not move;
    // four decimals only when the data itself has sub-penny prices.
    const subPenny = scope.some((p) =>
      [p.o, p.h, p.l, p.c].some((v) => Math.abs(v * 100 - Math.round(v * 100)) > 1e-6),
    );
    const pad = Math.max(subPenny ? 0.001 : 0.05, (high - low) * 0.14);
    low -= pad;
    high += pad;
    const rough = (high - low) / 5;
    const magnitude = Math.pow(10, Math.floor(Math.log10(rough)));
    // Steps of 2.5 would put whole-cent labels on half cents, so they are sub-penny only.
    const steps = subPenny ? [1, 2, 2.5, 5, 10] : [1, 2, 5, 10];
    const nice = steps.map((n) => n * magnitude).find((n) => n >= rough) || rough;
    const unit = subPenny ? nice : Math.max(0.01, nice);
    const y = (price) => bottom - ((price - low) / (high - low)) * (bottom - top);
    const digits = subPenny ? 4 : 2;
    const tagged = [all.at(-1).c, ...this.orders.map((o) => o.price / 10000)].map(y);
    const clear = (py) => tagged.every((ty) => Math.abs(ty - py) > 13);
    let s = `<defs><clipPath id="m-plot"><rect x="${left}" y="${top - 12}" width="${right - left}" height="${volumeBottom - top + 12}"/></clipPath></defs>`;
    for (
      let price = Math.ceil(low / unit) * unit;
      price <= high + unit * 0.01;
      price += unit
    ) {
      const py = y(price);
      if (py < top - 2 || py > bottom + 2) continue;
      s += `<line class="m-grid" x1="${left}" y1="${py}" x2="${right}" y2="${py}"/>${clear(py) ? `<text x="${right + 8}" y="${py + 3.5}">${price.toFixed(digits)}</text>` : ""}`;
    }
    const every = Math.max(1, Math.ceil(78 / gap));
    for (let i = first; i < last; i++) {
      if ((i - (all.length - 1)) % every) continue;
      const px = x(i);
      if (px < left + 18 || px > right - 4) continue;
      s += `<line class="m-grid" x1="${px}" y1="${top}" x2="${px}" y2="${volumeBottom}"/><text text-anchor="middle" x="${px}" y="${h - 6}">${MarketWorkbench.axisLabel(all[i].time)}</text>`;
    }
    s += `<line class="m-axis" x1="${right}" y1="${top - 12}" x2="${right}" y2="${volumeBottom}"/><line class="m-axis" x1="${left}" y1="${volumeBottom}" x2="${right}" y2="${volumeBottom}"/><line class="m-grid" x1="${left}" y1="${volumeTop - 5}" x2="${right}" y2="${volumeTop - 5}"/>`;
    const body = Math.max(1.5, Math.min(14, gap * 0.64));
    const vmax = Math.max(1, ...scope.map((p) => p.v));
    let marks = "";
    for (let i = first; i < last; i++) {
      const p = all[i],
        px = x(i),
        up = p.c >= p.o,
        col = up ? "var(--m-green)" : "var(--m-red)",
        vh = Math.max(1, (p.v / vmax) * (volumeHeight - 4));
      marks += `<line x1="${px}" y1="${y(p.h)}" x2="${px}" y2="${y(p.l)}" stroke="${col}" stroke-width="1"/><rect x="${px - body / 2}" y="${Math.min(y(p.o), y(p.c))}" width="${body}" height="${Math.max(1, Math.abs(y(p.o) - y(p.c)))}" rx=".6" fill="${col}"/><rect x="${px - body / 2}" y="${volumeBottom - vh}" width="${body}" height="${vh}" fill="${col}" opacity=".32"/>`;
    }
    const slot = (time) => {
      for (let i = all.length - 1; i >= 0; i--)
        if (time >= all[i].time) return i;
      return -1;
    };
    const stacked = {};
    for (const fill of this.fills) {
      const i = slot(fill.time_ns);
      if (i < first || i >= last) continue;
      const buy = fill.side !== "sell",
        key = i + (buy ? "b" : "s"),
        n = (stacked[key] = (stacked[key] || 0) + 1),
        px = x(i),
        size = 5,
        tip = buy
          ? Math.min(bottom + 2, y(all[i].l) + 5 + (n - 1) * 9)
          : Math.max(top - 8, y(all[i].h) - 5 - (n - 1) * 9),
        dir = buy ? 1 : -1;
      marks += `<path class="m-fill ${buy ? "buy" : "sell"}" d="M${px} ${tip}l${size} ${dir * size * 1.5}h${-size * 2}z"><title>${buy ? "Bought" : "Sold"} ${fill.quantity} @ ${(fill.price / 10000).toFixed(2)} · ${MarketWorkbench.time(fill.time_ns)}</title></path>`;
    }
    s += `<g clip-path="url(#m-plot)">${marks}</g>`;
    for (const order of this.orders) {
      const price = order.price / 10000,
        py = y(price);
      if (py < top - 4 || py > bottom + 4) continue;
      const cls = order.side === "sell" ? "sell" : "buy";
      s += `<line class="m-order ${cls}" x1="${left}" y1="${py}" x2="${right}" y2="${py}"/><rect class="m-order-tag ${cls}" x="${right + 2}" y="${py - 8}" width="${axis - 6}" height="16" rx="2"/><text class="m-order-text ${cls}" x="${right + 7}" y="${py + 3.5}">${price.toFixed(digits)}</text><text class="m-order-label ${cls}" x="${left + 6}" y="${py - 4}">${order.side === "sell" ? "Sell" : "Buy"} ${order.quantity}</text>`;
    }
    const final = all.at(-1),
      ly = y(final.c),
      tone = final.c >= final.o ? "up" : "down";
    if (ly >= top - 4 && ly <= bottom + 4)
      s += `<line class="m-last ${tone}" x1="${left}" y1="${ly}" x2="${right}" y2="${ly}"/><rect class="m-last-tag ${tone}" x="${right + 2}" y="${ly - 9}" width="${axis - 6}" height="18" rx="2"/><text class="m-last-text" x="${right + 7}" y="${ly + 3.5}">${final.c.toFixed(digits)}</text>`;
    s += `<text class="m-quiet" x="${right + 8}" y="${volumeTop + 6}">Vol</text><g data-crosshair visibility="hidden"><line data-cross-x class="m-cross" y1="${top - 12}" y2="${volumeBottom}"/><line data-cross-y class="m-cross" x1="${left}" x2="${right}"/><rect data-cross-price-box class="m-cross-tag" x="${right + 2}" width="${axis - 6}" height="16" rx="2"/><text data-cross-price class="m-cross-text" x="${right + 7}"></text><rect data-cross-time-box class="m-cross-tag" y="${volumeBottom + 2}" width="46" height="16" rx="2"/><text data-cross-time class="m-cross-text" text-anchor="middle" y="${h - 6}"></text></g>`;
    svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
    svg.innerHTML = s;
    this.current = {
      left,
      right,
      top,
      bottom,
      gap,
      span,
      start,
      low,
      high,
      digits,
      volumeBottom,
    };
    this.cross();
  }
  move(e) {
    if (!this.current) return;
    const r = this.svg.getBoundingClientRect();
    if (this.drag) {
      this.span = this.current.span;
      this.pan(this.drag.offset + (e.clientX - this.drag.x) / this.current.gap);
      this.pointer = { x: e.clientX - r.left, y: e.clientY - r.top };
      this.draw();
      return;
    }
    this.pointer = { x: e.clientX - r.left, y: e.clientY - r.top };
    this.cross();
  }
  describe(p, previous) {
    const l = this.current,
      f = (n) => n.toFixed(l.digits),
      base = previous ? previous.c : p.o,
      change = p.c - base,
      pct = base ? (change / base) * 100 : 0,
      tone = change >= 0 ? "positive" : "negative";
    return `<span class="m-legend-time">${this.byDay ? MarketWorkbench.axisLabel(p.time) : MarketWorkbench.time(p.time).slice(0, -3)}</span><span>O <b>${f(p.o)}</b></span><span>H <b>${f(p.h)}</b></span><span>L <b>${f(p.l)}</b></span><span>C <b>${f(p.c)}</b></span><span class="${tone}">${change >= 0 ? "+" : "−"}${Math.abs(change).toFixed(l.digits)} (${change >= 0 ? "+" : "−"}${Math.abs(pct).toFixed(2)}%)</span><span class="m-legend-note">${previous ? "vs previous close" : "vs open"}</span><span>Vol <b>${p.v.toLocaleString("en-US")}</b></span>`;
  }
  cross() {
    const l = this.current,
      guide = this.svg.querySelector("[data-crosshair]");
    if (!l || !this.points.length) return;
    const legend = this.legend;
    const inside =
      this.pointer &&
      !this.drag &&
      this.pointer.x >= l.left &&
      this.pointer.x <= l.right &&
      this.pointer.y >= l.top - 12 &&
      this.pointer.y <= l.volumeBottom;
    if (!inside) {
      guide?.setAttribute("visibility", "hidden");
      if (legend) {
        legend.hidden = false;
        legend.innerHTML = this.describe(
          this.points.at(-1),
          this.points.at(-2),
        );
      }
      return;
    }
    const index = Math.max(
      0,
      Math.min(
        this.points.length - 1,
        Math.floor((this.pointer.x - l.left) / l.gap + l.start),
      ),
    );
    const px = l.left + l.gap * (index - l.start + 0.5),
      py = Math.max(l.top, Math.min(l.bottom, this.pointer.y)),
      price = l.low + ((l.bottom - py) / (l.bottom - l.top)) * (l.high - l.low);
    const set = (selector, values) => {
      const node = this.svg.querySelector(selector);
      for (const [k, v] of Object.entries(values)) node.setAttribute(k, v);
      return node;
    };
    guide.setAttribute("visibility", "visible");
    set("[data-cross-x]", { x1: px, x2: px });
    set("[data-cross-y]", { y1: py, y2: py });
    set("[data-cross-price-box]", { y: py - 8 });
    set("[data-cross-price]", { y: py + 3.5 }).textContent = price.toFixed(
      l.digits,
    );
    const tx = Math.max(l.left + 23, Math.min(l.right - 23, px));
    set("[data-cross-time-box]", { x: tx - 23 });
    set("[data-cross-time]", { x: tx }).textContent = MarketWorkbench.axisLabel(
      this.points[index].time,
    );
    if (legend) {
      legend.hidden = false;
      legend.innerHTML = this.describe(
        this.points[index],
        this.points[index - 1],
      );
    }
  }
};
