(() => {
  "use strict";
  const TOKEN_KEY = "whm_token";
  const token = localStorage.getItem(TOKEN_KEY);
  if (!token) {
    location.replace("/");
    return;
  }

  // ---------- helpers ----------
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const esc = (v) =>
    String(v ?? "").replace(
      /[&<>"']/g,
      (c) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[c],
    );
  const STATUS = {
    operational: "Operational",
    degraded: "Degraded",
    down: "Down",
    unknown: "Unknown",
  };
  const pill = (s) => {
    const k = STATUS[s] ? s : "unknown";
    return `<span class="pill ${k}">${STATUS[k]}</span>`;
  };
  const PALETTE = [
    "#7b7dff",
    "#34d399",
    "#fbbf24",
    "#fb7185",
    "#38bdf8",
    "#a78bfa",
    "#fb923c",
    "#2dd4bf",
  ];
  const hostOf = (u) => {
    try {
      return new URL(u).hostname;
    } catch {
      return u;
    }
  };
  const siteName = (w) => w.display_name || hostOf(w.url);
  const fmtMs = (v) => (v == null ? "n/a" : `${Math.round(v)} ms`);
  const fmtPct = (v) => (v == null ? "n/a" : `${v}%`);
  const fmtDate = (iso) =>
    iso
      ? new Date(iso).toLocaleString([], {
          dateStyle: "medium",
          timeStyle: "short",
        })
      : "n/a";
  function ago(iso) {
    if (!iso) return "Never";
    const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
    if (s < 60) return "just now";
    if (s < 3600) return `${Math.floor(s / 60)} min ago`;
    if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
    return `${Math.floor(s / 86400)} d ago`;
  }
  const tLabel = (iso, hours) => {
    const d = new Date(iso);
    if (hours <= 24)
      return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    const day = d.toLocaleDateString([], { day: "numeric", month: "short" });
    return hours <= 168
      ? `${day} ${d.toLocaleTimeString([], { hour: "2-digit" })}`
      : day;
  };

  function toast(msg, type = "info") {
    const el = document.createElement("div");
    el.className = `toast ${type}`;
    el.textContent = msg;
    $("#toasts").append(el);
    setTimeout(() => el.classList.add("out"), 3800);
    setTimeout(() => el.remove(), 4300);
  }

  const errMsg = (d) => {
    if (!d) return "";
    if (typeof d.detail === "string") return d.detail;
    if (Array.isArray(d.detail))
      return d.detail
        .map((e) => String(e.msg).replace(/^Value error, /, ""))
        .join("; ");
    return "";
  };

  async function api(path, { method = "GET", body } = {}) {
    const res = await fetch(path, {
      method,
      headers: {
        Authorization: `Bearer ${token}`,
        ...(body ? { "Content-Type": "application/json" } : {}),
      },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (res.status === 401) {
      localStorage.removeItem(TOKEN_KEY);
      location.replace("/");
      throw new Error("Session expired");
    }
    if (res.status === 204) return null;
    let data = null;
    try {
      data = await res.json();
    } catch {}
    if (!res.ok)
      throw new Error(errMsg(data) || `Request failed (${res.status})`);
    return data;
  }

  const skeletonCards = (n) =>
    `<div class="metrics">${Array(n).fill('<div class="card skeleton sk-card"></div>').join("")}</div>`;
  const emptyState = (icon, title, text, action = "") =>
    `<div class="card empty"><div class="empty-icon">${icon}</div><h3>${esc(title)}</h3><p class="muted">${esc(text)}</p>${action}</div>`;
  const metric = (label, value, sub = "", tone = "") =>
    `<div class="card metric ${tone}"><small>${esc(label)}</small><b>${esc(value)}</b><span>${esc(sub)}</span></div>`;

  // ---------- charts ----------
  const charts = {};
  function destroyCharts() {
    Object.keys(charts).forEach((k) => {
      charts[k].destroy();
      delete charts[k];
    });
  }
  function drawChart(id, config) {
    if (charts[id]) charts[id].destroy();
    const canvas = document.getElementById(id);
    if (!canvas || typeof Chart === "undefined") return;
    const css = getComputedStyle(document.documentElement);
    Chart.defaults.color = css.getPropertyValue("--muted").trim();
    Chart.defaults.borderColor = css.getPropertyValue("--border").trim();
    Chart.defaults.font.family = "Inter, system-ui, sans-serif";
    charts[id] = new Chart(canvas, config);
  }
  const cssVar = (n) =>
    getComputedStyle(document.documentElement).getPropertyValue(n).trim();

  // ---------- dialogs ----------
  function confirmDialog({ title, text, ok = "Confirm", danger = false }) {
    const dlg = $("#confirm-dialog");
    $("#confirm-title").textContent = title;
    $("#confirm-text").textContent = text;
    const okBtn = $("#confirm-ok");
    okBtn.textContent = ok;
    okBtn.className = "btn " + (danger ? "btn-danger" : "btn-primary");
    return new Promise((resolve) => {
      const done = (v) => {
        dlg.close();
        resolve(v);
      };
      okBtn.onclick = () => done(true);
      $("#confirm-cancel").onclick = () => done(false);
      dlg.addEventListener("cancel", () => resolve(false), { once: true });
      dlg.showModal();
    });
  }

  const checkCache = new Map();
  async function fetchActivity(params) {
    const clean = Object.fromEntries(
      Object.entries(params).filter(([, v]) => v !== "" && v != null),
    );
    const rows = await api(`/websites/activity?${new URLSearchParams(clean)}`);
    rows.forEach((r) => checkCache.set(r.id, r));
    return rows;
  }
  function showCheck(id) {
    const r = checkCache.get(id);
    if (!r) return;
    const rows = [
      ["Website", esc(r.website_name)],
      ["Status", pill(r.health_status)],
      ["Checked at", esc(fmtDate(r.checked_at))],
      ["HTTP status", r.status_code ?? "n/a"],
      ["Response time", fmtMs(r.response_time_ms)],
      ["Reachable", r.is_reachable ? "Yes" : "No"],
      ["Requested URL", esc(r.requested_url)],
      ["Final URL", esc(r.final_url || "n/a")],
      ["Redirects", r.redirect_count],
      ["Page title", esc(r.page_title || "n/a")],
      ["Content type", esc(r.content_type || "n/a")],
      ["Server", esc(r.server || "n/a")],
      ["Error", esc(r.error_message || "None")],
    ];
    $("#check-body").innerHTML =
      `<dl class="detail-list">${rows.map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("")}</dl>`;
    $("#check-dialog").showModal();
  }

  const activityList = (rows) =>
    `<ul class="activity">${rows
      .map(
        (r) => `
        <li data-check="${r.id}">
          ${pill(r.health_status)}
          <div class="act-main"><strong>${esc(r.website_name)}</strong>
            <span class="muted small">${r.status_code ? `HTTP ${r.status_code} · ` : ""}${fmtMs(r.response_time_ms)}${r.error_message ? ` · ${esc(r.error_message)}` : ""}</span></div>
          <span class="muted small">${ago(r.checked_at)}</span>
        </li>`,
      )
      .join("")}</ul>`;

  // ---------- views ----------
  const siteCache = new Map();
  let siteHours = 24;

  async function renderOverview(silent) {
    const el = $("#view-overview");
    if (!silent) el.innerHTML = skeletonCards(6);
    const [ov, act] = await Promise.all([
      api("/websites/overview"),
      fetchActivity({ limit: 8 }),
    ]);
    if (ov.total === 0) {
      el.innerHTML = emptyState(
        "🌐",
        "No websites yet",
        "Add your first website and we will start monitoring it right away.",
        '<a class="btn btn-primary" href="#websites">Add your first website</a>',
      );
      return;
    }
    const issues = ov.degraded + ov.down;
    el.innerHTML = `
        <div class="metrics">
          ${metric("Websites", ov.total, "registered")}
          ${metric("Operational", ov.operational, "healthy right now", ov.operational ? "ok" : "")}
          ${metric("Needs attention", issues, `${ov.degraded} degraded · ${ov.down} down${ov.unknown ? ` · ${ov.unknown} not checked yet` : ""}`, issues ? "bad" : "")}
          ${metric("Avg response (24 h)", fmtMs(ov.avg_response_ms), `${ov.checks_24h} checks`)}
          ${metric("Observed availability (24 h)", fmtPct(ov.availability_pct), "share of checks that were operational")}
          ${metric("Latest check", ov.last_checked_at ? ago(ov.last_checked_at) : "Never", ov.last_checked_at ? fmtDate(ov.last_checked_at) : "")}
        </div>
        <div class="card panel"><h2>Recent activity</h2>
          ${act.length ? activityList(act) : '<p class="muted">No checks recorded yet. Use "Check now" on the My Websites page.</p>'}
        </div>`;
  }

  function siteCard(w) {
    const c = w.latest;
    const st = c ? c.health_status : "unknown";
    return `<article class="card site ${st in STATUS ? st : "unknown"}">
        <div class="site-head">
          <div class="site-title"><h3>${esc(siteName(w))}</h3>
            <a class="url" href="${esc(w.url)}" target="_blank" rel="noopener noreferrer">${esc(w.url)}</a></div>
          ${pill(st)}
        </div>
        <dl class="kv">
          <div><dt>HTTP</dt><dd>${c && c.status_code ? c.status_code : "n/a"}</dd></div>
          <div><dt>Response</dt><dd>${fmtMs(c ? c.response_time_ms : null)}</dd></div>
          <div><dt>Checked</dt><dd>${c ? ago(c.checked_at) : "Never"}</dd></div>
        </dl>
        ${c && c.page_title ? `<p class="title-line" title="${esc(c.page_title)}">${esc(c.page_title)}</p>` : ""}
        ${c && c.error_message ? `<p class="err-line">${esc(c.error_message)}</p>` : ""}
        ${!w.monitoring_enabled ? '<p class="muted small">Automatic monitoring is paused.</p>' : ""}
        <div class="site-actions">
          <button class="btn btn-sm btn-primary" data-action="check" data-id="${w.id}">Check now</button>
          <a class="btn btn-sm" href="#site-${w.id}">Details</a>
          <button class="btn btn-sm" data-action="edit" data-id="${w.id}">Edit</button>
          <button class="btn btn-sm btn-danger" data-action="delete" data-id="${w.id}">Delete</button>
        </div></article>`;
  }

  async function renderWebsites(silent) {
    const el = $("#view-websites");
    if (!$("#add-form")) {
      el.innerHTML = `
          <div class="card panel"><h2>Add a website</h2>
            <form id="add-form" class="add-form" novalidate>
              <div class="field grow"><label for="add-url">Website URL</label>
                <input id="add-url" type="text" placeholder="https://example.com" autocomplete="off" required /></div>
              <div class="field"><label for="add-name">Display name <span class="muted">(optional)</span></label>
                <input id="add-name" type="text" maxlength="100" placeholder="My shop" autocomplete="off" /></div>
              <button class="btn btn-primary" type="submit" id="add-btn">Add website</button>
            </form></div>
          <div id="site-grid">${skeletonCards(3)}</div>`;
      $("#add-form").addEventListener("submit", onAddWebsite);
    }
    const sites = await api("/websites");
    siteCache.clear();
    sites.forEach((w) => siteCache.set(w.id, w));
    $("#site-grid").className = sites.length ? "site-grid" : "";
    $("#site-grid").innerHTML = sites.length
      ? sites.map(siteCard).join("")
      : emptyState(
          "🌐",
          "No websites yet",
          "Add a public URL above to start monitoring it.",
        );
  }

  async function onAddWebsite(e) {
    e.preventDefault();
    const btn = $("#add-btn");
    const url = $("#add-url").value.trim();
    if (!url) {
      toast("Please enter a website URL", "error");
      return;
    }
    btn.disabled = true;
    btn.textContent = "Adding…";
    try {
      const w = await api("/websites", {
        method: "POST",
        body: { url, display_name: $("#add-name").value.trim() || null },
      });
      e.target.reset();
      toast("Website added. Running the first check…", "success");
      try {
        await api(`/websites/${w.id}/check`, { method: "POST" });
      } catch {}
    } catch (err) {
      toast(err.message, "error");
    } finally {
      btn.disabled = false;
      btn.textContent = "Add website";
      await route(true);
    }
  }

  async function renderHistory() {
    const el = $("#view-history");
    if (!$("#hist-site")) {
      const sites = await api("/websites");
      if (!sites.length) {
        el.innerHTML = emptyState(
          "☰",
          "Nothing to show yet",
          "Add a website first. Every check will be recorded here.",
          '<a class="btn btn-primary" href="#websites">Add a website</a>',
        );
        return;
      }
      el.innerHTML = `
          <div class="card panel filters">
            <div class="field"><label for="hist-site">Website</label><select id="hist-site"><option value="">All websites</option>
              ${sites.map((s) => `<option value="${s.id}">${esc(siteName(s))}</option>`).join("")}</select></div>
            <div class="field"><label for="hist-status">Status</label><select id="hist-status">
              <option value="">All</option><option value="operational">Operational</option><option value="degraded">Degraded</option>
              <option value="down">Down</option><option value="issues">Any issue</option></select></div>
            <div class="field"><label for="hist-range">Period</label><select id="hist-range">
              <option value="24">Last 24 hours</option><option value="168">Last 7 days</option><option value="720">Last 30 days</option><option value="">All time</option></select></div>
          </div>
          <div class="card panel">
            <div class="table-wrap"><table class="table"><thead><tr>
              <th>Time</th><th>Website</th><th>Status</th><th>HTTP</th><th>Response</th><th>Error</th></tr></thead>
              <tbody id="hist-body"></tbody></table></div>
            <div class="load-more" id="hist-foot"></div>
          </div>`;
      $$("#hist-site, #hist-status, #hist-range").forEach((s) =>
        s.addEventListener("change", () => loadHistory(true)),
      );
    }
    await loadHistory(true);
  }

  let histOffset = 0;
  async function loadHistory(reset) {
    if (reset) histOffset = 0;
    const PAGE = 50;
    const rows = await fetchActivity({
      website_id: $("#hist-site").value,
      status: $("#hist-status").value,
      hours: $("#hist-range").value,
      limit: PAGE,
      offset: histOffset,
    });
    const body = $("#hist-body");
    if (reset) body.innerHTML = "";
    body.insertAdjacentHTML(
      "beforeend",
      rows
        .map(
          (r) => `
        <tr data-check="${r.id}">
          <td>${esc(fmtDate(r.checked_at))}</td><td>${esc(r.website_name)}</td><td>${pill(r.health_status)}</td>
          <td>${r.status_code ?? "n/a"}</td><td>${fmtMs(r.response_time_ms)}</td><td class="err">${esc(r.error_message || "")}</td></tr>`,
        )
        .join(""),
    );
    histOffset += rows.length;
    const foot = $("#hist-foot");
    if (reset && !rows.length)
      foot.innerHTML = '<p class="muted">No checks match these filters.</p>';
    else
      foot.innerHTML =
        rows.length === PAGE
          ? '<button class="btn" id="hist-more">Load more</button>'
          : "";
    const more = $("#hist-more");
    if (more) more.onclick = () => loadHistory(false);
  }

  async function renderAnalytics() {
    const el = $("#view-analytics");
    if (!$("#an-range")) {
      el.innerHTML = `
          <div class="toolbar"><div><h2 class="h2">Performance &amp; availability</h2>
            <p class="muted small">Built from the checks recorded by the monitor.</p></div>
            <select id="an-range" class="select"><option value="24">Last 24 hours</option><option value="168">Last 7 days</option><option value="720">Last 30 days</option></select></div>
          <div id="an-body"></div>`;
      $("#an-range").addEventListener("change", () =>
        loadAnalytics().catch((e) => toast(e.message, "error")),
      );
    }
    await loadAnalytics();
  }

  async function loadAnalytics() {
    const hours = Number($("#an-range").value);
    const body = $("#an-body");
    body.innerHTML = skeletonCards(3);
    const data = await api(`/websites/analytics?hours=${hours}`);
    const sites = data.websites;
    if (!sites.length) {
      body.innerHTML = emptyState(
        "▥",
        "No websites yet",
        "Add a website to see analytics.",
        '<a class="btn btn-primary" href="#websites">Add a website</a>',
      );
      return;
    }
    if (sites.every((s) => s.total === 0)) {
      body.innerHTML = emptyState(
        "▥",
        "No checks in this period",
        "Run a check from My Websites, or wait for the next automatic check.",
      );
      return;
    }
    body.innerHTML = `
        <div class="grid-2">
          <div class="card panel wide"><h3>Response time by website</h3><div class="chart-box"><canvas id="ch-rt"></canvas></div></div>
          <div class="card panel"><h3>Observed availability</h3>
            <p class="muted small" style="margin:-8px 0 10px">Share of checks that were operational. This is not an SLA figure.</p>
            <div class="chart-box"><canvas id="ch-av"></canvas></div></div>
          <div class="card panel"><h3>Monitoring activity</h3>
            <p class="muted small" style="margin:-8px 0 10px">Operational checks vs checks with issues.</p>
            <div class="chart-box"><canvas id="ch-act"></canvas></div></div>
        </div>`;

    const labels = [
      ...new Set(sites.flatMap((s) => s.series.map((p) => p.t))),
    ].sort();
    const labelText = labels.map((t) => tLabel(t, hours));
    drawChart("ch-rt", {
      type: "line",
      data: {
        labels: labelText,
        datasets: sites.map((s, i) => {
          const m = new Map(s.series.map((p) => [p.t, p.avg_ms]));
          const color = PALETTE[i % PALETTE.length];
          return {
            label: s.name,
            data: labels.map((t) => m.get(t) ?? null),
            borderColor: color,
            backgroundColor: color,
            tension: 0.3,
            spanGaps: true,
            pointRadius: 0,
            borderWidth: 2,
          };
        }),
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        scales: {
          y: { beginAtZero: true, title: { display: true, text: "ms" } },
          x: { ticks: { maxTicksLimit: 8 } },
        },
        plugins: { legend: { position: "bottom" } },
      },
    });
    drawChart("ch-av", {
      type: "bar",
      data: {
        labels: sites.map((s) => s.name),
        datasets: [
          {
            label: "Observed availability %",
            data: sites.map((s) => s.availability_pct ?? 0),
            backgroundColor: sites.map((_, i) => PALETTE[i % PALETTE.length]),
            borderRadius: 8,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: { y: { min: 0, max: 100 } },
        plugins: { legend: { display: false } },
      },
    });
    const agg = new Map(labels.map((t) => [t, { h: 0, i: 0 }]));
    sites.forEach((s) =>
      s.series.forEach((p) => {
        const a = agg.get(p.t);
        a.h += p.healthy;
        a.i += p.issues;
      }),
    );
    drawChart("ch-act", {
      type: "bar",
      data: {
        labels: labelText,
        datasets: [
          {
            label: "Operational",
            data: labels.map((t) => agg.get(t).h),
            backgroundColor: cssVar("--ok"),
            stack: "a",
          },
          {
            label: "Issues",
            data: labels.map((t) => agg.get(t).i),
            backgroundColor: cssVar("--bad"),
            stack: "a",
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: { stacked: true, ticks: { maxTicksLimit: 8 } },
          y: { stacked: true, beginAtZero: true },
        },
        plugins: { legend: { position: "bottom" } },
      },
    });
  }

  async function renderSite(silent) {
    const el = $("#view-site");
    const id = current.id;
    if (!silent) el.innerHTML = skeletonCards(4);
    const [w, st, errs] = await Promise.all([
      api(`/websites/${id}`),
      api(`/websites/${id}/stats?hours=${siteHours}`),
      fetchActivity({ website_id: id, status: "issues", limit: 8 }),
    ]);
    const c = w.latest;
    const health = c ? c.health_status : "unknown";
    const info = c
      ? [
          ["HTTP status", c.status_code ?? "n/a"],
          ["Response time", fmtMs(c.response_time_ms)],
          ["Checked", esc(fmtDate(c.checked_at))],
          ["Page title", esc(c.page_title || "n/a")],
          ["Content type", esc(c.content_type || "n/a")],
          ["Server", esc(c.server || "n/a")],
          ["Final URL", esc(c.final_url || "n/a")],
          ["Redirects", c.redirect_count],
        ]
      : [];
    el.innerHTML = `
        <a class="back" href="#websites">← All websites</a>
        <div class="card panel site-hero">
          <div style="min-width:0"><h2 class="h2">${esc(siteName(w))}</h2>
            <a class="url" href="${esc(w.url)}" target="_blank" rel="noopener noreferrer">${esc(w.url)}</a></div>
          <div class="hero-actions">${pill(health)}<button class="btn btn-primary btn-sm" data-action="check" data-id="${w.id}">Check now</button></div>
        </div>
        <div class="metrics">
          ${metric("Observed availability", fmtPct(st.availability_pct), `${st.total} checks in period`)}
          ${metric("Avg response", fmtMs(st.avg_ms), "")}
          ${metric("Fastest / slowest", `${fmtMs(st.min_ms)} / ${fmtMs(st.max_ms)}`, "")}
          ${metric("Check results", `${st.healthy} ok`, `${st.degraded} degraded · ${st.down} down`, st.down ? "bad" : "")}
        </div>
        <div class="grid-2">
          <div class="card panel wide">
            <div class="toolbar"><h3>Response time</h3>
              <select id="site-range" class="select">
                ${[
                  [24, "Last 24 hours"],
                  [168, "Last 7 days"],
                  [720, "Last 30 days"],
                ]
                  .map(
                    ([v, l]) =>
                      `<option value="${v}" ${v === siteHours ? "selected" : ""}>${l}</option>`,
                  )
                  .join("")}
              </select></div>
            ${st.series.length ? '<div class="chart-box"><canvas id="ch-site"></canvas></div>' : '<p class="muted">No checks in this period yet.</p>'}
          </div>
          <div class="card panel"><h3>Latest check</h3>
            ${c ? `<dl class="detail-list">${info.map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("")}</dl>` : '<p class="muted">Not checked yet. Press "Check now".</p>'}
          </div>
          <div class="card panel"><h3>Recent issues</h3>
            ${errs.length ? activityList(errs) : '<p class="muted">No issues recorded. 🎉</p>'}
          </div>
        </div>`;
    if (st.series.length) {
      drawChart("ch-site", {
        type: "line",
        data: {
          labels: st.series.map((p) => tLabel(p.t, siteHours)),
          datasets: [
            {
              label: "Avg response (ms)",
              data: st.series.map((p) => p.avg_ms),
              borderColor: cssVar("--accent"),
              backgroundColor: cssVar("--accent-soft"),
              fill: true,
              tension: 0.3,
              spanGaps: true,
              pointRadius: 0,
              borderWidth: 2,
            },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          scales: {
            y: { beginAtZero: true, title: { display: true, text: "ms" } },
            x: { ticks: { maxTicksLimit: 8 } },
          },
          plugins: { legend: { display: false } },
        },
      });
    }
  }

  // ---------- router ----------
  const TITLES = {
    overview: "Overview",
    websites: "My Websites",
    history: "Monitoring History",
    analytics: "Analytics",
      extension: "Chrome Extension",
  };
  const renderers = {
    overview: renderOverview,
    websites: renderWebsites,
    history: renderHistory,
    analytics: renderAnalytics,
      extension: renderExtension,
    site: renderSite,
  };
  let current = { name: "overview", id: null };

  function parseHash() {
    const h = location.hash.slice(1) || "overview";
    if (h.startsWith("site-")) {
      const id = Number(h.slice(5));
      if (Number.isInteger(id) && id > 0) return { name: "site", id };
    }
    return { name: TITLES[h] ? h : "overview", id: null };
  }

  async function route(silent = false) {
    const prev = current.name;
    current = parseHash();
    if (current.name !== prev || !silent) destroyCharts();
    $$(".view").forEach((v) =>
      v.classList.toggle("hidden", v.id !== `view-${current.name}`),
    );
    const active = current.name === "site" ? "websites" : current.name;
    $$("#side-nav a").forEach((a) =>
      a.classList.toggle("active", a.dataset.view === active),
    );
    $("#page-title").textContent =
      current.name === "site" ? "Website details" : TITLES[current.name];
    document.body.classList.remove("nav-open");
    try {
      await renderers[current.name](silent);
    } catch (e) {
      if (silent) return;
      toast(e.message, "error");
      const view = $(`#view-${current.name}`);
      if (!view.firstElementChild || view.querySelector(".skeleton")) {
        view.innerHTML = emptyState(
          "⚠️",
          "Could not load this page",
          e.message,
          '<a class="btn" href="#overview">Back to overview</a>',
        );
      }
    }
  }

  
  async function renderExtension() {
    const el = $("#view-extension");
    let status = { active_tokens: 0 };
    try { status = await api("/extension/status"); } catch {}

    const liveUrl = window.location.origin || "https://api-health-monitor-lcx5.onrender.com";

    el.innerHTML = `
      <div class="card" style="padding:24px; max-width:640px;">
        <div style="display:flex; justify-content:space-between; align-items:center; gap:12px; margin-bottom:18px; flex-wrap:wrap;">
          <div>
            <h3 style="margin-bottom:4px;">Chrome Extension</h3>
            <p class="muted small">Follow these simple steps to install and connect the extension.</p>
          </div>
          <span class="pill ${status.active_tokens > 0 ? "operational" : "unknown"}">
            ${status.active_tokens > 0 ? "Connected" : "Ready"}
          </span>
        </div>

        <div style="background:var(--surface-2); border:1px solid var(--border); border-radius:12px; padding:16px; margin-bottom:16px;">
          <div style="display:flex; justify-content:space-between; align-items:center; gap:12px; flex-wrap:wrap; margin-bottom:10px;">
            <strong style="font-size:14px;">How to Set Up &amp; Use</strong>
            <a class="btn btn-sm btn-primary" href="/extension/download" download>Download Extension (.zip) &#8595;</a>
          </div>
          <ol class="small muted" style="padding-left:20px; line-height:1.8;">
            <li>Click <strong>Download Extension (.zip)</strong> above and <strong>unzip (Extract All)</strong> the folder.</li>
            <li>Open <code>chrome://extensions</code> in Chrome, turn ON <strong>Developer mode</strong>, click <strong>Load unpacked</strong>, and select the unzipped folder.</li>
            <li>Open the extension from your Chrome toolbar, click the <strong>&#9881; (Gear) icon</strong> at the top-right, paste <code>${liveUrl}</code> as the <strong>Base URL</strong>, and click <strong>Save</strong>.</li>
            <li>Click <strong>Generate Pairing Code</strong> below, paste the code into the extension, and click <strong>Pair with Code</strong>.</li>
            <li>Open any website in Chrome, open the extension, and click <strong>+ Monitor</strong> to start monitoring it!</li>
          </ol>
        </div>

        <div style="display:flex; gap:10px; flex-wrap:wrap; align-items:center;">
          <button class="btn btn-primary" id="ext-generate-btn">Generate Pairing Code</button>
          ${status.active_tokens > 0 ? '<button class="btn btn-danger" id="ext-revoke-btn">Disconnect</button>' : ""}
        </div>

        <div id="ext-code-box" class="hidden" style="background:var(--surface-2); border:1px dashed var(--accent); border-radius:10px; padding:14px; margin-top:16px; text-align:center;">
          <div class="small muted" style="margin-bottom:4px;">One-Time Pairing Code (valid 10 min)</div>
          <div style="display:flex; justify-content:center; align-items:center; gap:10px; flex-wrap:wrap;">
            <span id="ext-pairing-code" style="font-size:24px; font-weight:800; letter-spacing:0.14em; font-family:monospace; color:var(--accent);"></span>
            <button type="button" class="btn btn-xs" id="ext-copy-code-btn">Copy Code</button>
          </div>
        </div>
      </div>
    `;

    $("#ext-generate-btn").addEventListener("click", async (ev) => {
      const btn = ev.currentTarget;
      btn.disabled = true;
      btn.innerHTML = '<span class="spinner"></span> Generating?';
      try {
        const res = await api("/extension/pair/start", { method: "POST" });
        $("#ext-pairing-code").textContent = res.code;
        $("#ext-code-box").classList.remove("hidden");
        toast("Pairing code generated!", "success");
      } catch (err) {
        toast(err.message, "error");
      } finally {
        btn.disabled = false;
        btn.textContent = "Generate Pairing Code";
      }
    });

    const copyBtn = $("#ext-copy-code-btn");
    if (copyBtn) {
      copyBtn.addEventListener("click", async () => {
        const code = $("#ext-pairing-code").textContent.trim();
        if (!code) return;
        try {
          await navigator.clipboard.writeText(code);
          toast("Pairing code copied!", "success");
        } catch {
          toast("Select and copy the code above", "info");
        }
      });
    }

    const revokeBtn = $("#ext-revoke-btn");
    if (revokeBtn) {
      revokeBtn.addEventListener("click", async () => {
        try {
          await api("/extension/disconnect", { method: "POST" });
          toast("Extension disconnected", "success");
          route(true);
        } catch (err) {
          toast(err.message, "error");
        }
      });
    }
  }

  // ---------- events ----------
  document.addEventListener("click", async (e) => {
    const row = e.target.closest("[data-check]");
    if (row) {
      showCheck(Number(row.dataset.check));
      return;
    }

    const btn = e.target.closest("[data-action]");
    if (!btn) return;
    const id = Number(btn.dataset.id);
    const action = btn.dataset.action;

    if (action === "check") {
      btn.disabled = true;
      btn.innerHTML = '<span class="spinner"></span> Checking';
      try {
        await api(`/websites/${id}/check`, { method: "POST" });
        toast("Check complete", "success");
      } catch (err) {
        toast(err.message, "error");
      }
      await route(true);
    } else if (action === "delete") {
      const w = siteCache.get(id);
      const ok = await confirmDialog({
        title: "Delete this website?",
        text: `${w ? siteName(w) : "This website"} and all of its monitoring history will be permanently removed.`,
        ok: "Delete",
        danger: true,
      });
      if (!ok) return;
      try {
        await api(`/websites/${id}`, { method: "DELETE" });
        toast("Website deleted", "success");
      } catch (err) {
        toast(err.message, "error");
      }
      await route(true);
    } else if (action === "edit") {
      const w = siteCache.get(id);
      if (!w) return;
      editingId = id;
      $("#edit-url").value = w.url;
      $("#edit-name").value = w.display_name || "";
      $("#edit-enabled").checked = w.monitoring_enabled;
      $("#edit-dialog").showModal();
    }
  });

  document.addEventListener("change", (e) => {
    if (e.target.id === "site-range") {
      siteHours = Number(e.target.value);
      route();
    }
  });

  let editingId = null;
  $("#edit-cancel").addEventListener("click", () => $("#edit-dialog").close());
  $("#edit-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = $("#edit-save");
    btn.disabled = true;
    try {
      await api(`/websites/${editingId}`, {
        method: "PUT",
        body: {
          display_name: $("#edit-name").value.trim() || null,
          monitoring_enabled: $("#edit-enabled").checked,
        },
      });
      $("#edit-dialog").close();
      toast("Changes saved", "success");
      await route(true);
    } catch (err) {
      toast(err.message, "error");
    } finally {
      btn.disabled = false;
    }
  });
  $("#check-close").addEventListener("click", () => $("#check-dialog").close());

  $("#menu-btn").addEventListener("click", () =>
    document.body.classList.toggle("nav-open"),
  );
  $("#scrim").addEventListener("click", () =>
    document.body.classList.remove("nav-open"),
  );
  $("#refresh-btn").addEventListener("click", () => route());
  $("#theme-btn").addEventListener("click", () => {
    const root = document.documentElement;
    const next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
    root.setAttribute("data-theme", next);
    try {
      localStorage.setItem("whm_theme", next);
    } catch {}
    route(true);
  });
  $("#logout-btn").addEventListener("click", () => {
    localStorage.removeItem(TOKEN_KEY);
    location.replace("/");
  });
  window.addEventListener("hashchange", () => route());

  // Refresh live views every 60 s (not while a dialog is open or the tab is hidden)
  setInterval(() => {
    if (document.hidden || $("dialog[open]")) return;
    if (["overview", "websites", "site"].includes(current.name)) route(true);
  }, 60000);

  (async () => {
    try {
      const me = await api("/me");
      $("#user-name").textContent = me.name;
      $("#user-email").textContent = me.email;
    } catch {}
    route();
  })();
})();
