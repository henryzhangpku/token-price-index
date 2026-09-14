/**
 * The site renders what the pipeline decided. It recomputes nothing, so the
 * page and the CLI cannot disagree about a published number.
 *
 * Laid out to match the compute benchmark's dashboard section for section --
 * header, sticky index picker, board, as-of panel, then a per-index detail
 * block rendered here. One difference is forced by the data and is labelled
 * rather than hidden: that benchmark charts a fixing across dates, and this
 * one has a single collection date, so the chart in the same slot plots the
 * sellers instead. Same question, different axis.
 */

const $ = (sel) => document.querySelector(sel);

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

const fmt = (n, dp = 3) =>
  n === null || n === undefined ? "--" : Number(n).toFixed(dp);

let DATA = null;
let CURRENT = null;

/* ---------- theme ------------------------------------------------------- */

function initTheme() {
  const btn = $(".theme-toggle");
  if (!btn) return;
  const stored = (() => {
    try { return localStorage.getItem("tokidx-theme"); } catch { return null; }
  })();
  if (stored) document.documentElement.setAttribute("data-theme", stored);

  const isDark = () =>
    document.documentElement.getAttribute("data-theme") === "dark"
    || (!document.documentElement.getAttribute("data-theme")
        && window.matchMedia("(prefers-color-scheme: dark)").matches);

  const label = () => { btn.textContent = isDark() ? "Light" : "Dark"; };
  label();
  btn.addEventListener("click", () => {
    const next = isDark() ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    try { localStorage.setItem("tokidx-theme", next); } catch { /* private mode */ }
    label();
  });
}

/* ---------- head, picker, board ----------------------------------------- */

function renderHead(data) {
  const sub = $("#dash-sub");
  if (!sub) return;
  const day = new Date(data.index_date + "T00:00:00Z").toLocaleDateString("en-GB", {
    day: "numeric", month: "long", year: "numeric", timeZone: "UTC",
  });
  sub.textContent =
    `${day} · prices read ${data.prices_read} · methodology ${data.methodology_version}`;
}

function renderPicker(data) {
  const host = $("#picker");
  if (!host) return;
  host.innerHTML = data.indices.map((idx) =>
    `<button type="button" data-code="${escapeHtml(idx.code)}"
       aria-pressed="${idx.code === CURRENT}">${escapeHtml(idx.code)}</button>`
  ).join("");
  host.querySelectorAll("button").forEach((btn) => {
    btn.addEventListener("click", () => select(btn.dataset.code));
  });
}

function renderBoard(data) {
  const host = $("#board-table");
  if (!host) return;
  const rows = data.indices.map((idx) => {
    const sellers = idx.providers.filter((p) => !p.screened_out);
    const failed = idx.gates.filter((g) => !g.passed).map((g) => g.name.replace(/_/g, " "));
    const value = idx.published
      ? `<span class="price">$${fmt(idx.value)}</span>`
      : `<span class="price none">--</span>`;
    return `<tr data-code="${escapeHtml(idx.code)}" class="${idx.code === CURRENT ? "on" : ""}">
      <td><strong>${escapeHtml(idx.code)}</strong><br>
          <span style="color:var(--muted)">${escapeHtml(idx.display_name)}</span></td>
      <td class="r">${value}</td>
      <td class="r">${sellers.length}</td>
      <td class="r">${idx.dispersion === null ? "--" : fmt(idx.dispersion)}</td>
      <td><span class="status ${idx.published ? "published" : "withheld"}">${
        idx.published ? "published" : "withheld"}</span></td>
      <td><span class="reason">${idx.published ? "" : escapeHtml(failed.join(", "))}</span></td>
    </tr>`;
  }).join("");

  host.innerHTML = `<table>
    <thead><tr>
      <th>index</th><th class="r">fixing</th><th class="r">sellers</th>
      <th class="r">dispersion</th><th>status</th><th>gate</th>
    </tr></thead><tbody>${rows}</tbody></table>`;

  host.querySelectorAll("tbody tr").forEach((tr) => {
    tr.style.cursor = "pointer";
    tr.addEventListener("click", () => select(tr.dataset.code));
  });

  const withheld = data.indices.filter((i) => !i.published).length;
  const meta = $("#board-meta");
  if (meta) {
    meta.textContent =
      `${data.observation_count} observations · ${withheld} of ${data.indices.length} withheld · USD per Mtok`;
  }
}

function renderAsOf(data) {
  const note = $("#asof-note");
  const label = $("#asof-label");
  if (!note || !label) return;
  const dates = (data.collection_dates || []).length || 1;
  note.textContent = `${dates} revision${dates === 1 ? "" : "s"} across ${dates} date${dates === 1 ? "" : "s"}`;
  label.innerHTML = dates <= 1
    ? `<strong>${escapeHtml(data.index_date)}</strong> &mdash; the only revision on the tape. `
      + `There is one collection date, so there is nothing earlier to read back to yet. `
      + `The control appears here because the machinery is in place, not to imply a history `
      + `that does not exist.`
    : `<strong>${escapeHtml(data.index_date)}</strong> &mdash; newest of `
      + `${dates} collection dates on the tape. Each date's fixing is computed from that `
      + `date's snapshot alone, so a past value can still be shown to follow from its own `
      + `inputs. The series chart plots them.`;
}

/* ---------- the chart --------------------------------------------------- */

function chartPanel(idx) {
  const sellers = [...idx.providers].filter((p) => !p.screened_out)
    .sort((a, b) => a.price - b.price);
  const screened = idx.providers.filter((p) => p.screened_out);
  if (!sellers.length) {
    return `<div class="panel"><header><h2>Sellers</h2></header>
      <div class="body"><div class="loading">no seller survived restatement</div></div></div>`;
  }

  // A strip plot: price on the axis, one dot per seller, stacked where sellers
  // quote the same number -- which is the shape this market actually has.
  //
  // The stack height has to be solved for before anything is drawn. Fifteen
  // sellers at one price is a column fifteen dots tall, and a fixed step size
  // sends it straight out of the top of the panel.
  const W = chartWidth(), L = 26, R = 26, TOP = 26, AXIS_GAP = 34, LABEL_ROW = 26;

  const prices = sellers.map((p) => p.price);
  let lo = Math.min(...prices), hi = Math.max(...prices);
  if (hi - lo < 1e-9) { const c = hi || 1; lo = c * 0.85; hi = c * 1.15; }
  const pad = (hi - lo) * 0.08;
  lo -= pad; hi += pad;
  const x = (v) => L + (W - L - R) * (v - lo) / (hi - lo);

  // How tall does the tallest column get? Count collisions on the x axis.
  const xs = sellers.map((p) => x(p.price));
  const tallest = xs.reduce((most, cx, i) => {
    const n = xs.slice(0, i + 1).filter((q) => Math.abs(q - cx) < 9).length;
    return Math.max(most, n);
  }, 1);

  const STACK = 168;                                   // room the column may use
  const step = Math.max(6, Math.min(13, STACK / tallest));
  const radius = Math.max(3, Math.min(5, step * 0.42));
  const H = TOP + STACK + AXIS_GAP + LABEL_ROW;
  const baseline = TOP + STACK;

  const placed = [];
  const dots = sellers.map((p) => {
    const cx = x(p.price);
    const level = placed.filter((q) => Math.abs(q - cx) < radius * 1.9).length;
    placed.push(cx);
    return `<circle class="dot" cx="${cx.toFixed(1)}" cy="${(baseline - radius - 2 - level * step).toFixed(1)}" r="${radius.toFixed(1)}">
        <title>${escapeHtml(p.provider)} — $${fmt(p.price)}</title></circle>`;
  }).join("");

  const ticks = [lo + (hi - lo) * 0.02, (lo + hi) / 2, hi - (hi - lo) * 0.02].map((t, i) =>
    `<text class="tick" x="${x(t).toFixed(1)}" y="${baseline + 20}"
       text-anchor="${i === 0 ? "start" : i === 2 ? "end" : "middle"}">$${fmt(t, 3)}</text>`
  ).join("");

  // The fixing label sits below the tick row, never beside the column.
  const fx = idx.published ? x(idx.value) : null;
  const fixing = idx.published
    ? `<line class="fixing" x1="${fx.toFixed(1)}" x2="${fx.toFixed(1)}" y1="${TOP - 6}" y2="${baseline}"/>
       <text class="fixing-label" x="${Math.min(Math.max(fx, 52), W - 52).toFixed(1)}"
             y="${baseline + AXIS_GAP + 14}" text-anchor="middle">fixing $${fmt(idx.value)}</text>`
    : "";

  const counts = {};
  for (const p of sellers) counts[p.price] = (counts[p.price] || 0) + 1;
  const [modePrice, modeN] = Object.entries(counts).sort((a, b) => b[1] - a[1])[0];

  const note = Number(modeN) > 1
    ? `<strong>${modeN} of ${sellers.length}</strong> sellers quote exactly $${fmt(Number(modePrice))}. `
      + `A seller count says this market is competitive; the prices say most of it is anchored `
      + `on one number, and the spread lives in the tails.`
    : `Every seller quotes a different price.`;

  return `<div class="panel">
    <header>
      <h2>${escapeHtml(idx.display_name)}</h2>
      <span class="note">${sellers.length} seller${sellers.length === 1 ? "" : "s"}${
        screened.length ? ` · ${screened.length} screened` : ""}</span>
    </header>
    <div class="body">
      <svg class="chart" viewBox="0 0 ${W} ${H}" role="img"
           aria-label="Price distribution across sellers for ${escapeHtml(idx.code)}">
        <line class="grid" x1="${L}" x2="${W - R}" y1="${baseline}" y2="${baseline}"/>
        ${fixing}${dots}${ticks}
      </svg>
      <div class="legend">
        <span class="key dot-key">one seller</span>
        ${idx.published ? '<span class="key line-key">published fixing</span>' : '<span class="key none-key">withheld</span>'}
      </div>
      <p class="chart-note">${note}</p>
    </div>
  </div>`;
}

/* ---------- the series -------------------------------------------------- */

/* One point per collection date. The line BREAKS on a withheld day rather than
 * bridging it: a bridged segment draws a price that was never published, which
 * is the visual form of the carry-forward the gates exist to prevent. Same
 * grammar as the compute benchmark's series, deliberately.
 */
function seriesPanel(idx, data) {
  const entries = (data.series && data.series[idx.code]) || [];
  const values = entries.filter((e) => e.value !== null).map((e) => e.value);

  if (!entries.length) {
    return "";
  }
  if (!values.length) {
    return `<div class="panel">
      <header><h2>Series</h2>
        <span class="note">${entries.length} date${entries.length === 1 ? "" : "s"}</span>
      </header>
      <div class="body"><p class="chart-note">Every date so far was withheld, so this
        index has printed no value to plot. The gap is the finding.</p></div>
    </div>`;
  }

  const W = chartWidth(), H = 240;
  const pad = { t: 18, r: 24, b: 64, l: 58 };
  const iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;
  const stripY = H - 40, stripH = 9;

  // The axis never zooms tighter than ±4% of the level. Autoscaling to the
  // data alone turns a 0.5% drift into a full-height cliff, which says the
  // opposite of what the number says. A small move has to look small.
  let lo = Math.min(...values), hi = Math.max(...values);
  const mid = (lo + hi) / 2 || 1;
  const floor = Math.abs(mid) * 0.04;
  if (hi - lo < floor * 2) { lo = mid - floor; hi = mid + floor; }
  const span = hi - lo;
  lo -= span * 0.18; hi += span * 0.18;

  const n = entries.length;
  const x = (i) => pad.l + (n === 1 ? iw / 2 : (i * iw) / (n - 1));
  const y = (v) => pad.t + ih - ((v - lo) / (hi - lo)) * ih;

  // Tick precision follows the tick step, or two adjacent ticks round to the
  // same label -- "$0.330" printed twice on a 0.0004 step.
  const tickStep = (hi - lo) / 4;
  const dp = Math.max(2, Math.min(4, Math.ceil(-Math.log10(tickStep)) + 1));
  let grid = "";
  for (let i = 0; i <= 4; i++) {
    const v = lo + ((hi - lo) * i) / 4, yy = y(v);
    grid += `<line class="grid" x1="${pad.l}" y1="${yy.toFixed(1)}" x2="${W - pad.r}" y2="${yy.toFixed(1)}"/>`
          + `<text class="tick" x="${pad.l - 9}" y="${(yy + 3.5).toFixed(1)}" text-anchor="end">$${fmt(v, dp)}</text>`;
  }

  // Consecutive published days form one segment; a withheld day ends it.
  const segments = [];
  let run = [];
  entries.forEach((e, i) => {
    if (e.value === null) { if (run.length) segments.push(run); run = []; }
    else run.push([x(i), y(e.value)]);
  });
  if (run.length) segments.push(run);

  // A lone published day between two withheld ones has no line to belong to and
  // would read as an unexplained speck, so it is ringed instead.
  const lines = segments.map((seg) =>
    seg.length === 1
      ? `<circle class="lone" cx="${seg[0][0].toFixed(1)}" cy="${seg[0][1].toFixed(1)}" r="6"/>`
      : `<path class="line" d="M ${seg.map((p) => p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" L ")}"/>`
  ).join("");

  const dots = entries.map((e, i) => e.value === null ? "" :
    `<circle class="dot" cx="${x(i).toFixed(1)}" cy="${y(e.value).toFixed(1)}" r="3.5">
       <title>${escapeHtml(e.index_date)}: $${fmt(e.value)}</title></circle>`).join("");

  // One cell per date, so a run of withheld days is legible as a pattern
  // without competing with the prices for vertical space.
  const cellW = Math.max(6, Math.min(22, (n > 1 ? iw / (n - 1) : iw) * 0.55));
  const strip = entries.map((e, i) => {
    const cx = Math.min(Math.max(x(i) - cellW / 2, pad.l), W - pad.r - cellW);
    const tip = e.value !== null
      ? `${e.index_date}: published at $${fmt(e.value)}`
      : `${e.index_date}: withheld — ${e.withheld_reason || "gated"}`;
    return `<rect class="cell ${e.value !== null ? "on" : "off"}" x="${cx.toFixed(1)}" y="${stripY}"
                  width="${cellW.toFixed(1)}" height="${stripH}" rx="2"><title>${escapeHtml(tip)}</title></rect>`;
  }).join("");

  const step = Math.max(1, Math.ceil(n / 7));
  const xlabels = entries.map((e, i) =>
    (i % step === 0 || i === n - 1)
      ? `<text class="tick" x="${x(i).toFixed(1)}" y="${H - 10}" text-anchor="${
          i === 0 ? "start" : i === n - 1 ? "end" : "middle"}">${escapeHtml(e.index_date.slice(5))}</text>`
      : "").join("");

  const withheldCount = entries.filter((e) => e.value === null).length;
  const first = values.length ? entries.find((e) => e.value !== null) : null;
  const last = [...entries].reverse().find((e) => e.value !== null);
  let note;
  if (n === 1) {
    note = `One collection date. The machinery plots a series; there is not yet a series to plot.`;
  } else if (first && last && first !== last) {
    const move = (last.value - first.value) / first.value;
    note = `${escapeHtml(first.index_date)} to ${escapeHtml(last.index_date)}: `
         + `$${fmt(first.value)} to $${fmt(last.value)}, `
         + `<strong>${move >= 0 ? "+" : ""}${(move * 100).toFixed(1)}%</strong>.`
         + (withheldCount ? ` ${withheldCount} date${withheldCount === 1 ? "" : "s"} withheld and printed nothing; the line breaks there rather than bridging a price that never existed.` : "");
  } else {
    note = `A single published date so far.`;
  }

  return `<div class="panel">
    <header>
      <h2>Series</h2>
      <span class="note">${n} date${n === 1 ? "" : "s"}${withheldCount ? ` · ${withheldCount} withheld` : ""}</span>
    </header>
    <div class="body">
      <svg class="chart" viewBox="0 0 ${W} ${H}" role="img"
           aria-label="Published fixings over time for ${escapeHtml(idx.code)}. ${withheldCount} of ${n} dates withheld.">
        ${grid}${lines}${dots}
        <text class="tick" x="${pad.l - 9}" y="${stripY + stripH - 1}" text-anchor="end">status</text>
        ${strip}${xlabels}
      </svg>
      <div class="legend">
        <span class="key line-key">published fixing</span>
        <span class="key dot-key">collection date</span>
        <span class="key none-key">withheld — no value printed</span>
      </div>
      <p class="chart-note">${note}</p>
    </div>
  </div>`;
}

/* ---------- detail panels ----------------------------------------------- */

function gatesPanel(idx) {
  return `<div class="panel">
    <header><h2>Publication gates</h2><span class="note">all must hold</span></header>
    <div class="body"><div class="gates">${
      idx.gates.map((g) => `<div class="gate ${g.passed ? "" : "fail"}">
          <span class="mark">${g.passed ? "PASS" : "FAIL"}</span>
          <span class="name">${escapeHtml(g.name.replace(/_/g, " "))}</span>
          <span class="detail">${escapeHtml(g.detail)}</span>
        </div>`).join("")
    }</div></div></div>`;
}

function contributionsPanel(idx) {
  if (!idx.providers.length) return "";
  const total = idx.providers.filter((p) => !p.screened_out)
    .reduce((a, p) => a + p.weight, 0);
  return `<div class="panel">
    <header>
      <h2>Contributions</h2>
      <span class="note">${idx.providers.length} seller${idx.providers.length === 1 ? "" : "s"} · ${idx.rejections} observations discarded</span>
    </header>
    <div class="table-scroll">
    <table>
      <thead><tr>
        <th>seller</th><th class="r">price</th><th class="r">quotes</th>
        <th class="r">tier</th><th class="r">weight</th><th class="r">share</th><th>note</th>
      </tr></thead>
      <tbody>${idx.providers.map((p) => `<tr>
        <td${p.screened_out ? ' style="color:var(--muted)"' : ""}>${escapeHtml(p.provider)}</td>
        <td class="r">$${fmt(p.price)}</td>
        <td class="r">${p.quotes}</td>
        <td class="r">${p.tier}</td>
        <td class="r">${p.screened_out ? "--" : fmt(p.weight, 2)}</td>
        <td class="r">${p.screened_out || total <= 0 ? "--" : (100 * p.weight / total).toFixed(1) + "%"}</td>
        <td><span class="reason">${escapeHtml(p.screen_reason || "")}</span></td>
      </tr>`).join("")}</tbody>
    </table>
    </div>
  </div>`;
}

function contractPanel(idx, data) {
  const g = data.spec.gates;
  return `<div class="panel">
    <header><h2>The contract</h2><span class="note">what every input is restated onto</span></header>
    <div class="body">
      <p class="contract">
        One million <strong>${escapeHtml(idx.direction)}</strong> tokens of
        <strong>${escapeHtml(idx.model)}</strong>, served standard &mdash; synchronous,
        uncached, standard priority &mdash; in the US region, priced in USD.
        ${idx.indexable
          ? "Open weights, so the same good is sold by more than one seller."
          : "<strong>Proprietary weights, so each model has exactly one seller and no cross-seller price exists to discover.</strong>"}
      </p>
      <p class="contract dim">
        Gates: at least ${g.min_providers} sellers and ${g.min_observations} observations,
        dispersion at or below ${g.max_dispersion}, no seller above
        ${Math.round(g.max_provider_weight_share * 100)}% of weight, and the good must be
        indexable at all.
      </p>
    </div>
  </div>`;
}

/* The SVGs scale to their panel through viewBox, so a fixed 720 drawn into a
 * 310px phone column shrank every tick label to about five pixels. Draw at
 * the width the panel actually has, and redraw when that width changes. */
function chartWidth() {
  const host = $("#detail");
  return host && host.clientWidth ? Math.max(320, Math.min(720, host.clientWidth - 44)) : 720;
}

function renderDetail() {
  const host = $("#detail");
  if (!host || !DATA) return;
  const idx = DATA.indices.find((i) => i.code === CURRENT) || DATA.indices[0];
  host.innerHTML = seriesPanel(idx, DATA) + chartPanel(idx) + gatesPanel(idx)
    + contributionsPanel(idx) + contractPanel(idx, DATA);
}

let redrawTimer = null;
window.addEventListener("resize", () => {
  clearTimeout(redrawTimer);
  redrawTimer = setTimeout(renderDetail, 120);
});

function select(code) {
  CURRENT = code;
  renderPicker(DATA);
  renderBoard(DATA);
  renderDetail();
}

/* ---------- the cross-section ------------------------------------------- */

function renderCliff(data) {
  const host = $("#cliff");
  if (!host) return;
  const all = data.indices.flatMap((i) =>
    i.providers.filter((p) => !p.screened_out).map((p) => p.price));
  if (!all.length) { host.innerHTML = `<div class="loading">no prices</div>`; return; }
  const lo = Math.log(Math.min(...all) * 0.8);
  const hi = Math.log(Math.max(...all) * 1.25);
  const at = (price) => (100 * (Math.log(price) - lo) / (hi - lo)).toFixed(2);

  host.innerHTML = data.indices.map((idx) => {
    const sellers = idx.providers.filter((p) => !p.screened_out);
    const dots = sellers.map((p) =>
      `<i style="left:${at(p.price)}%" title="${escapeHtml(p.provider)} $${fmt(p.price)}"></i>`
    ).join("");
    // Three states, not two. A good can be indexable in principle and still
    // show no discovery, because its sellers happen to quote the same number.
    const distinct = new Set(sellers.map((p) => p.price)).size;
    let verdict;
    if (!idx.indexable) {
      verdict = `${sellers.length} sellers of <strong>different goods</strong>`;
    } else if (distinct <= 1) {
      verdict = `${sellers.length} sellers, <strong>one price</strong> &mdash; no spread`;
    } else {
      verdict = `${sellers.length} sellers, ${distinct} prices`;
    }
    return `<div class="cliff-row ${idx.indexable ? "" : "incoherent"}">
      <span class="who">${escapeHtml(idx.code)}</span>
      <span class="axis">${dots}</span>
      <span class="verdict">${verdict}</span>
    </div>`;
  }).join("");
}

/* ---------- boot --------------------------------------------------------- */

initTheme();

fetch("data/fixings.json", { cache: "no-store" })
  .then((r) => r.json())
  .then((data) => {
    DATA = data;
    CURRENT = (data.indices.find((i) => i.published) || data.indices[0]).code;

    renderHead(data);
    renderAsOf(data);
    renderCliff(data);
    select(CURRENT);

    const v = $("#foot-version");
    const rd = $("#foot-read");
    if (v) v.textContent = data.methodology_version;
    if (rd) rd.textContent = data.prices_read;
  })
  .catch((err) => {
    const board = $("#board-table");
    if (board) {
      board.innerHTML =
        `<div class="loading">could not load the fixings: ${escapeHtml(err.message)}</div>`;
    }
  });
