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
  note.textContent = `1 revision across 1 date`;
  label.innerHTML =
    `<strong>${escapeHtml(data.index_date)}</strong> &mdash; the only revision on the tape. `
    + `There is one collection date, so there is nothing earlier to read back to yet. `
    + `The control appears here because the machinery is in place, not to imply a history `
    + `that does not exist.`;
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
  const W = 720, L = 26, R = 26, TOP = 26, AXIS_GAP = 34, LABEL_ROW = 26;

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

function renderDetail() {
  const host = $("#detail");
  if (!host || !DATA) return;
  const idx = DATA.indices.find((i) => i.code === CURRENT) || DATA.indices[0];
  host.innerHTML = chartPanel(idx) + gatesPanel(idx)
    + contributionsPanel(idx) + contractPanel(idx, DATA);
}

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
