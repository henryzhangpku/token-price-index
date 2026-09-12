/**
 * The site renders what the pipeline decided. It recomputes nothing, so the
 * page and the CLI cannot disagree about a published number.
 *
 * Both pages load this file, so every renderer checks for its container first
 * rather than assuming the dashboard's markup is present.
 */

const $ = (sel) => document.querySelector(sel);

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

const fmt = (n, dp = 3) =>
  n === null || n === undefined ? "--" : Number(n).toFixed(dp);

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

/* ---------- the board --------------------------------------------------- */

function renderBoard(data) {
  if (!$("#board")) return;
  const rows = data.indices.map((idx) => {
    const contributing = idx.providers.filter((p) => !p.screened_out);
    const failed = idx.gates.filter((g) => !g.passed).map((g) => g.name.replace(/_/g, " "));
    const value = idx.published
      ? `<span class="price">$${fmt(idx.value)}</span>`
      : `<span class="price none">--</span>`;
    return `<tr data-code="${escapeHtml(idx.code)}">
      <td><strong>${escapeHtml(idx.code)}</strong><br>
          <span style="color:var(--muted)">${escapeHtml(idx.display_name)}</span></td>
      <td class="r">${value}</td>
      <td class="r">${contributing.length}</td>
      <td class="r">${idx.dispersion === null ? "--" : fmt(idx.dispersion)}</td>
      <td><span class="status ${idx.published ? "published" : "withheld"}">${
        idx.published ? "published" : "withheld"}</span></td>
      <td><span class="reason">${idx.published ? "" : escapeHtml(failed.join(", "))}</span></td>
    </tr>`;
  }).join("");

  $("#board").innerHTML = `<table>
    <thead><tr>
      <th>index</th><th class="r">fixing</th><th class="r">sellers</th>
      <th class="r">dispersion</th><th>status</th><th>gate</th>
    </tr></thead>
    <tbody>${rows}</tbody></table>`;

  const withheld = data.indices.filter((i) => !i.published).length;
  const meta = $("#board-meta");
  if (meta) {
    meta.textContent =
      `${data.observation_count} observations · ${withheld} of ${data.indices.length} withheld · USD per Mtok`;
  }

  const note = $("#onedate");
  if (note) {
    note.innerHTML =
      `Prices were read once, on <strong>${escapeHtml(data.prices_read)}</strong>. `
      + `There is one collection date, so there is no series and no chart is drawn &mdash; `
      + `re-running a fixed table daily would manufacture a flat line that looks like `
      + `data and is not. What can be shown from a single date is the cross-section below.`;
  }
}

/* ---------- gates and sellers for one index ----------------------------- */

function renderIndex(data, code) {
  if (!$("#gates")) return;
  const idx = data.indices.find((i) => i.code === code) || data.indices[0];

  document.querySelectorAll("#board tbody tr").forEach((tr) => {
    tr.classList.toggle("on", tr.dataset.code === idx.code);
  });

  $("#gates-meta").textContent = `${idx.code} · all must hold`;
  $("#gates").innerHTML = `<div class="gates">${
    idx.gates.map((g) => `<div class="gate ${g.passed ? "" : "fail"}">
        <span class="mark">${g.passed ? "PASS" : "FAIL"}</span>
        <span class="name">${escapeHtml(g.name.replace(/_/g, " "))}</span>
        <span class="detail">${escapeHtml(g.detail)}</span>
      </div>`).join("")
  }</div>`;

  const total = idx.providers.filter((p) => !p.screened_out)
    .reduce((a, p) => a + p.weight, 0);
  $("#sellers-meta").textContent =
    `${idx.code} · ${idx.providers.length} sellers · ${idx.rejections} observations discarded`;
  $("#sellers").innerHTML = idx.providers.length === 0
    ? `<div class="loading">no seller survived restatement</div>`
    : `<table>
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
      </tr>`).join("")}</tbody></table>`;
}

/* ---------- the cross-section ------------------------------------------- */

function renderCliff(data) {
  const host = $("#cliff");
  if (!host) return;

  // One shared log axis, because the contracts span input and output pricing
  // and a linear axis would flatten every input series into the left margin.
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
  }).join("") + `<p class="axis-note">log scale, USD per million tokens</p>`;
}

/* ---------- the spread strip -------------------------------------------- */

function renderSpread(data) {
  if (!$("#spread")) return;
  const idx = data.indices.find((i) => i.code === "TIX-K26-OUT");
  if (!idx) return;
  const sellers = idx.providers.filter((p) => !p.screened_out);
  const max = Math.max(...sellers.map((p) => p.price));

  $("#spread").innerHTML = sellers.map((p) => `<div class="spread-row">
      <span class="who">${escapeHtml(p.provider)}</span>
      <span class="bar"><i style="width:${(100 * p.price / max).toFixed(1)}%"></i></span>
      <span class="amt">$${fmt(p.price, 2)}</span>
    </div>`).join("");
}

/* ---------- boot --------------------------------------------------------- */

initTheme();

fetch("data/fixings.json", { cache: "no-store" })
  .then((r) => r.json())
  .then((data) => {
    renderBoard(data);
    renderCliff(data);
    renderSpread(data);
    renderIndex(data, "TIX-K26-OUT");

    const v = $("#foot-version");
    const rd = $("#foot-read");
    if (v) v.textContent = data.methodology_version;
    if (rd) rd.textContent = data.prices_read;

    document.querySelectorAll("#board tbody tr").forEach((tr) => {
      tr.style.cursor = "pointer";
      tr.addEventListener("click", () => renderIndex(data, tr.dataset.code));
    });
  })
  .catch((err) => {
    const board = $("#board");
    if (board) {
      board.innerHTML =
        `<div class="loading">could not load the fixings: ${escapeHtml(err.message)}</div>`;
    }
  });
