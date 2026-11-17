(() => {
  "use strict";
  const TOKEN_KEY = "whm_token";
  const token = localStorage.getItem(TOKEN_KEY);
  if (!token) {
    location.replace("/");
    return;
  }

  const $ = (s, r = document) => r.querySelector(s);
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
    let data = null;
    try {
      data = await res.json();
    } catch {}
    if (!res.ok)
      throw new Error(errMsg(data) || `Request failed (${res.status})`);
    return data;
  }

  function toast(msg, type = "info") {
    const el = document.createElement("div");
    el.className = `toast ${type}`;
    el.textContent = msg;
    $("#toasts").append(el);
    setTimeout(() => el.classList.add("out"), 3800);
    setTimeout(() => el.remove(), 4300);
  }

  const fmtDate = (iso) =>
    iso
      ? new Date(iso).toLocaleString([], {
          dateStyle: "medium",
          timeStyle: "short",
        })
      : "n/a";
  function fmtDuration(sec) {
    sec = Math.round(sec);
    if (sec < 60) return `${sec} s`;
    const m = Math.floor(sec / 60);
    if (m < 60) return `${m} min`;
    const h = Math.floor(m / 60);
    if (h < 24) return `${h} h ${m % 60} min`;
    return `${Math.floor(h / 24)} d ${h % 24} h`;
  }

  async function withBusy(btn, label, fn) {
    const old = btn.textContent;
    btn.disabled = true;
    btn.textContent = label;
    try {
      await fn();
    } finally {
      btn.disabled = false;
      btn.textContent = old;
    }
  }

  // ---------- settings ----------
  let settings = null;

  function renderSettings(s) {
    settings = s;
    $("#cfg-banner").innerHTML = s.email_configured
      ? ""
      : '<div class="banner warn">Email sending is not configured on the server. Add the SMTP_* settings to your .env file and restart the server.</div>';
    $("#al-email").value = s.email;
    $("#al-threshold").value = String(s.failure_threshold);
    $("#al-enabled").checked = s.enabled;
    $("#verify-state").innerHTML = s.verified
      ? '<div class="verify-row"><span class="pill operational">Verified</span><span class="hint">Alerts will be sent to this address.</span></div>'
      : '<div class="verify-row"><span class="pill degraded">Not verified</span><button class="btn btn-sm" type="button" id="al-verify">Send verification email</button></div><p class="hint" style="margin-top:8px">Alerts are only sent to verified addresses.</p>';
    $("#al-test").disabled = !(s.verified && s.email_configured);
    const v = $("#al-verify");
    if (v) v.onclick = sendVerification;
  }

  async function saveSettings(e) {
    e.preventDefault();
    await withBusy($("#al-save"), "Saving…", async () => {
      try {
        const s = await api("/alerts/settings", {
          method: "PUT",
          body: {
            email: $("#al-email").value.trim(),
            enabled: $("#al-enabled").checked,
            failure_threshold: Number($("#al-threshold").value),
          },
        });
        renderSettings(s);
        toast(
          s.verified
            ? "Settings saved"
            : "Settings saved. Verify your email address to start receiving alerts.",
          "success",
        );
      } catch (err) {
        toast(err.message, "error");
      }
    });
  }

  async function sendVerification() {
    try {
      const r = await api("/alerts/verification", { method: "POST" });
      toast(r.detail, "success");
    } catch (err) {
      toast(err.message, "error");
    }
  }

  async function sendTest() {
    await withBusy($("#al-test"), "Sending…", async () => {
      try {
        const r = await api("/alerts/test", { method: "POST" });
        toast(r.detail, "success");
      } catch (err) {
        toast(err.message, "error");
      }
    });
  }

  // ---------- incidents ----------
  let offset = 0;
  const PAGE = 25;

  const incidentRow = (r) => `
      <tr>
        <td><a href="/dashboard#site-${r.website_id}">${esc(r.website_name)}</a></td>
        <td>${esc(fmtDate(r.started_at))}</td>
        <td>${
          r.status === "open"
            ? '<span class="pill down">Ongoing</span>'
            : `<span class="pill operational">Resolved</span> <span class="muted small">${esc(fmtDate(r.resolved_at))}</span>`
        }</td>
        <td>${esc(fmtDuration(r.duration_seconds))}</td>
        <td class="err">${esc(r.first_error || "")}</td>
      </tr>`;

  async function loadIncidents(reset) {
    if (reset) offset = 0;
    const params = new URLSearchParams({ limit: PAGE, offset });
    const status = $("#inc-status").value;
    if (status) params.set("status", status);
    const rows = await api(`/alerts/incidents?${params}`);
    const body = $("#inc-body");
    if (reset) body.innerHTML = "";
    body.insertAdjacentHTML("beforeend", rows.map(incidentRow).join(""));
    offset += rows.length;
    const foot = $("#inc-foot");
    if (reset && !rows.length)
      foot.innerHTML =
        '<p class="muted">No incidents. Everything has been healthy. 🎉</p>';
    else
      foot.innerHTML =
        rows.length === PAGE
          ? '<button class="btn" id="inc-more">Load more</button>'
          : "";
    const more = $("#inc-more");
    if (more)
      more.onclick = () =>
        loadIncidents(false).catch((e) => toast(e.message, "error"));
  }

  // ---------- page chrome ----------
  $("#alert-form").addEventListener("submit", saveSettings);
  $("#al-test").addEventListener("click", sendTest);
  $("#inc-status").addEventListener("change", () =>
    loadIncidents(true).catch((e) => toast(e.message, "error")),
  );
  $("#menu-btn").addEventListener("click", () =>
    document.body.classList.toggle("nav-open"),
  );
  $("#scrim").addEventListener("click", () =>
    document.body.classList.remove("nav-open"),
  );
  $("#theme-btn").addEventListener("click", () => {
    const root = document.documentElement;
    const next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
    root.setAttribute("data-theme", next);
    try {
      localStorage.setItem("whm_theme", next);
    } catch {}
  });
  $("#logout-btn").addEventListener("click", () => {
    localStorage.removeItem(TOKEN_KEY);
    location.replace("/");
  });

  (async () => {
    const q = new URLSearchParams(location.search);
    if (q.has("verified")) {
      const ok = q.get("verified") === "1";
      toast(
        ok
          ? "Email verified. Alerts are now active."
          : "That verification link is invalid or has expired.",
        ok ? "success" : "error",
      );
      history.replaceState(null, "", "/dashboard/alerts");
    }
    try {
      const me = await api("/me");
      $("#user-name").textContent = me.name;
      $("#user-email").textContent = me.email;
    } catch {}
    try {
      renderSettings(await api("/alerts/settings"));
    } catch (e) {
      toast(e.message, "error");
    }
    try {
      await loadIncidents(true);
    } catch (e) {
      toast(e.message, "error");
    }
  })();
})();
