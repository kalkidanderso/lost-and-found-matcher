"use strict";

/* SPA with proper state management, real-time updates,
   keyboard shortcuts, and flows that actually work.
   Zero framework (no build step), one JS file, all vanilla.
*/

const api = async (path, options = {}) => {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const body = res.status === 204 ? {} : await res.json();
  if (!res.ok) {
    const err = new Error(body?.error?.message || "Request failed");
    err.code = body?.error?.code;
    err.fields = body?.error?.fields || {};
    throw err;
  }
  return body;
};

const state = {
  currentView: "board",
  board: { matches: [], total: 0, by_band: {} },
  reports: { reports: [], count: 0 },
  stats: { reports: {}, matches: {} },
  config: { places: [] },
  filter: { band: "", searchQuery: "" },
  selectedMatch: null,
  selectedReport: null,
};

const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
};

const svg = (name, size = 20) => {
  const icons = {
    check: '<path d="M2 10l5 5 10-10"/>',
    close: '<path d="M3 3l14 14M17 3L3 17"/>',
    search: '<circle cx="9" cy="9" r="7"/><path d="M14 14l5 5"/>',
    info: '<circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/>',
    warning: '<path d="M12 2L2 20h20L12 2z M12 9v4M12 17h.01"/>',
  };
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("width", size);
  s.setAttribute("height", size);
  s.setAttribute("viewBox", "0 0 20 20");
  s.setAttribute("fill", "none");
  s.setAttribute("stroke", "currentColor");
  s.setAttribute("stroke-width", "1.5");
  s.innerHTML = icons[name] || '';
  return s;
};

const fmtDate = (iso, precision) => {
  if (!iso) return "no date";
  const d = new Date(iso);
  const opts = precision === "day"
    ? { day: "numeric", month: "short", year: "numeric" }
    : { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" };
  return d.toLocaleString(undefined, opts);
};

/* ============================================================ state + render */

const render = () => {
  const view = state.currentView;
  // Hide all screens
  document.querySelectorAll(".screen").forEach(s => {
    s.style.display = "none";
    s.classList.remove("is-active");
  });
  // Show current screen
  const currentScreen = document.getElementById(`screen-${view}`);
  if (currentScreen) {
    currentScreen.style.display = "block";
    currentScreen.classList.add("is-active");
  }
  // Update nav active state
  document.querySelectorAll(".nav-item").forEach(item => {
    item.classList.toggle("is-active", item.dataset.view === view);
  });
  // Render content for the current view
  if (view === "board") renderBoard();
  if (view === "reports") refreshReports();
  if (view === "reunited") renderReunited();
  if (view === "file") renderFileForm();
};

const navigate = (view) => {
  state.currentView = view;
  render();
};

/* ============================================================ navigation */

document.querySelectorAll(".nav-item").forEach(item => {
  item.addEventListener("click", () => navigate(item.dataset.view));
});

document.addEventListener("keydown", (e) => {
  if (e.ctrlKey || e.metaKey) {
    if (e.key === "1") navigate("board");
    if (e.key === "2") navigate("file");
    if (e.key === "3") navigate("reports");
    if (e.key === "4") navigate("reunited");
  }
});

/* ============================================================ dashboard */

const refreshStats = async () => {
  try {
    state.stats = await api("/api/meta").then(meta => api("/api/stats")).catch(async () => await api("/api/stats"));
    renderStats();
  } catch (err) {
    console.error("stats:", err);
  }
};

const renderStats = () => {
  const s = state.stats;
  const lostOpen = s.reports?.lost_open || 0;
  const foundOpen = s.reports?.found_open || 0;
  const strong = s.matches?.strong || 0;
  const possible = s.matches?.possible || 0;
  const weak = s.matches?.weak || 0;
  const resolved = s.reports?.resolved || 0;

  // sidebar detail panel
  document.getElementById("stat-lost").textContent = lostOpen;
  document.getElementById("stat-found").textContent = foundOpen;
  document.getElementById("stat-strong").textContent = strong;
  document.getElementById("stat-possible").textContent = possible;
  document.getElementById("stat-weak").textContent = weak;

  // nav badges (separate elements, so no duplicate-ID collision)
  document.getElementById("nav-strong").textContent = strong;
  document.getElementById("nav-open").textContent = lostOpen + foundOpen;
  document.getElementById("nav-reunited").textContent = resolved;
};

/* ============================================================ board */

const refreshBoard = async () => {
  try {
    const band = state.filter.band ? `?band=${state.filter.band}` : "";
    state.board = await api(`/api/matches${band}`);
    renderBoard();
  } catch (err) {
    console.error("board:", err);
  }
};

const renderBoard = () => {
  // Only render if we're on the board view
  if (state.currentView !== "board") return;
  const container = document.getElementById("board-matches");
  if (!container) return; // Fallback if element doesn't exist
  if (!state.board.matches || !state.board.matches.length) {
    container.replaceChildren(el("div", "empty", "No matches yet."));
    return;
  }
  container.replaceChildren(...state.board.matches.map(matchCard));
};

const matchCard = (match) => {
  const card = el("article", "card match-card");

  const header = el("div", "match-header");
  header.append(
    el("div", "match-score", `${match.score}/100`),
    el("span", `band band-${match.band}`, match.band.toUpperCase()),
    el("span", "match-ids", `${match.lost_id} ↔ ${match.found_id}`)
  );

  const pair = el("div", "pair");
  const lost = el("div", "side");
  lost.append(
    el("h4", null, "Lost"),
    el("p", "description", match.lost?.description || "(removed)"),
    el("p", "meta", match.lost ? `${match.lost.id} · ${match.lost.location || "?"} · ${fmtDate(match.lost.occurred_at, match.lost.time_precision)}` : "")
  );

  const found = el("div", "side");
  found.append(
    el("h4", null, "Found"),
    el("p", "description", match.found?.description || "(removed)"),
    el("p", "meta", match.found ? `${match.found.id} · ${match.found.location || "?"} · ${fmtDate(match.found.occurred_at, match.found.time_precision)}` : "")
  );
  pair.append(lost, found);

  const signals = el("div", "signals");
  match.signals.forEach(s => {
    const row = el("div", `signal${s.available ? "" : " unavailable"}`);
    const bar = el("div", "bar");
    const fill = el("i");
    fill.style.width = `${Math.round((s.available ? s.score : 0) * 100)}%`;
    bar.append(fill);
    row.append(
      el("span", "signal-name", s.label),
      bar,
      el("span", "signal-score", s.available ? s.score.toFixed(2) : "—"),
      el("span", "signal-reason", s.reason)
    );
    signals.append(row);
  });

  const actions = el("div", "actions");
  const confirm = el("button", "btn primary", "Confirm");
  confirm.addEventListener("click", () => decide(match, "confirmed", card));
  const reject = el("button", "btn secondary", "Not a match");
  reject.addEventListener("click", () => decide(match, "rejected", card));
  actions.append(confirm, reject);

  card.append(header, pair, signals, actions);
  return card;
};

const decide = async (match, decision, card) => {
  card.querySelectorAll("button").forEach(b => b.disabled = true);
  try {
    await api(`/api/matches/${match.lost_id}/${match.found_id}/decision`,
      { method: "POST", body: JSON.stringify({ decision }) });
    const actions = card.querySelector(".actions");
    actions.replaceChildren(el("span", "resolved", decision === "confirmed" ? "✓ Confirmed" : "✗ Rejected"));
    await refreshStats();
    await refreshBoard();
  } catch (err) {
    console.error("decide:", err);
    card.querySelector(".actions").appendChild(el("span", "error", err.message));
  }
};

document.querySelectorAll(".filter-band").forEach(btn => {
  btn.addEventListener("click", () => {
    state.filter.band = btn.dataset.band || "";
    document.querySelectorAll(".filter-band").forEach(b => b.classList.remove("is-active"));
    btn.classList.add("is-active");
    refreshBoard();
  });
});

/* ============================================================ reports list */

const refreshReports = async () => {
  try {
    const q = state.filter.searchQuery ? `&q=${encodeURIComponent(state.filter.searchQuery)}` : "";
    state.reports = await api(`/api/reports?status=open${q}`);
    renderReports();
  } catch (err) {
    console.error("reports:", err);
  }
};

const renderReports = () => {
  if (state.currentView !== "reports") return;
  const container = document.getElementById("reports-list");
  if (!container) return; // Fallback if element doesn't exist
  if (!state.reports.reports || !state.reports.reports.length) {
    container.replaceChildren(el("div", "empty", "No open reports."));
    return;
  }
  container.replaceChildren(...state.reports.reports.map(r => {
    const card = el("article", "card report-card");
    card.append(
      el("span", `tag tag-${r.kind}`, r.kind),
      el("div", "report-body", null),
      el("span", "report-id", r.id)
    );
    card.querySelector(".report-body").append(
      el("p", "description", r.description),
      el("p", "meta", `${r.location || "?"} · ${fmtDate(r.occurred_at, r.time_precision)}`)
    );
    card.addEventListener("click", () => viewReportMatches(r.id));
    return card;
  }));
};

const viewReportMatches = async (reportId) => {
  try {
    const result = await api(`/api/reports/${reportId}/matches`);
    const modal = document.getElementById("modal");
    const content = modal.querySelector(".modal-content");
    content.replaceChildren();

    const title = el("h3", null, `${result.report.kind.toUpperCase()} ${reportId}`);
    const desc = el("p", "description", result.report.description);
    content.append(title, desc);

    if (result.matches.length) {
      content.append(el("h4", null, `${result.matches.length} potential match(es)`));
      const list = el("div", "match-list");
      list.append(...result.matches.map(matchCard));
      content.append(list);
    } else {
      content.append(el("p", "empty", "No matches for this report."));
    }

    modal.style.display = "block";
  } catch (err) {
    console.error("matches:", err);
  }
};

document.getElementById("search-reports").addEventListener("input", (e) => {
  state.filter.searchQuery = e.target.value;
  clearTimeout(window.__searchTimer);
  window.__searchTimer = setTimeout(refreshReports, 300);
});

document.getElementById("modal").addEventListener("click", (e) => {
  if (e.target.id === "modal") e.target.style.display = "none";
});

/* ============================================================ file report */

const renderFileForm = () => {
  if (state.currentView !== "file") return;
  const result = document.getElementById("result");
  if (result) {
    result.innerHTML = "";
  }
};

const form = document.getElementById("report-form");
form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const data = new FormData(form);
  const when = String(data.get("occurred_at") || "");
  const payload = {
    kind: data.get("kind"),
    description: String(data.get("description") || ""),
    location: String(data.get("location") || ""),
    contact: String(data.get("contact") || ""),
    identifiers: String(data.get("identifiers") || "")
      .split(",").map((s) => s.trim()).filter(Boolean),
  };
  if (when) payload.occurred_at = new Date(when).toISOString();

  const btn = form.querySelector("button[type='submit']");
  btn.disabled = true;
  try {
    const result = await api("/api/reports", { method: "POST", body: JSON.stringify(payload) });
    const resultDiv = document.getElementById("result");
    const card = el("div", "card result-card");
    const h = el("h3", null, `Report ${result.report.id} filed`);
    const p = el("p", null, result.matches.length
      ? `We found ${result.matches.length} potential match(es) already.`
      : "No matches yet. We'll notify you if one appears.");
    card.append(h, p);
    if (result.matches.length) {
      card.append(...result.matches.map(matchCard));
    }
    resultDiv.replaceChildren(card);
    form.reset();
    await refreshStats();
  } catch (err) {
    const errors = document.getElementById("form-errors");
    errors.replaceChildren();
    Object.entries(err.fields || {}).forEach(([field, msg]) => {
      errors.append(el("p", "error", `${field}: ${msg}`));
    });
  } finally {
    btn.disabled = false;
  }
});

/* ============================================================ reunited */

const renderReunited = async () => {
  if (state.currentView !== "reunited") return;
  try {
    const data = await api("/api/reunions");
    const container = document.getElementById("reunions-list");
    if (!container) return; // Fallback if element doesn't exist
    if (!data.reunions || !data.reunions.length) {
      container.replaceChildren(el("div", "empty", "No confirmed matches yet."));
      return;
    }
    container.replaceChildren(...data.reunions.map(r => {
      const card = el("article", "card reunion-card");
      const header = el("div", "reunion-header");
      header.append(
        el("span", "score", `${r.score || "—"}/100`),
        el("span", "date", fmtDate(r.decided_at))
      );
      const pair = el("div", "pair");
      [["Lost", r.lost], ["Found", r.found]].forEach(([label, rep]) => {
        const side = el("div", "side");
        side.append(
          el("h4", null, label),
          el("p", "description", rep.description),
          el("p", "meta", `${rep.id} · ${rep.location || "?"}`)
        );
        pair.append(side);
      });
      card.append(header, pair);
      return card;
    }));
  } catch (err) {
    console.error("reunited:", err);
  }
};

/* ============================================================ boot */

(async () => {
  // Render initial screen view immediately so the UI structure is visible
  render();

  if (window.location.protocol === "file:") {
    const container = document.getElementById("board-matches");
    if (container) {
      container.replaceChildren(el("div", "card form-errors",
        "Notice: You opened index.html directly via file:// protocol. Please open http://127.0.0.1:8000 in your web browser after running 'python3 -m lostfound serve --demo'."
      ));
    }
    return;
  }

  try {
    state.config = await api("/api/meta");
    const places = document.getElementById("places");
    if (places && state.config?.places) {
      places.replaceChildren(...state.config.places.map(p => {
        const opt = el("option");
        opt.value = p.name;
        return opt;
      }));
    }
    await refreshStats();
    setInterval(refreshStats, 5000);
    await refreshBoard();
    render();
  } catch (err) {
    console.error("boot:", err);
    const container = document.getElementById("board-matches");
    if (container) {
      container.replaceChildren(el("div", "card form-errors",
        `Backend Connection Error: Unable to fetch data (${err.message}). Ensure 'python3 -m lostfound serve --demo' is running and refresh http://127.0.0.1:8000.`
      ));
    }
  }
})();