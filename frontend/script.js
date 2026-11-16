(() => {
  "use strict";
  const TOKEN_KEY = "whm_token";
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
  const errMsg = (d) => {
    if (!d) return "";
    if (typeof d.detail === "string") return d.detail;
    if (Array.isArray(d.detail))
      return d.detail
        .map((e) => String(e.msg).replace(/^Value error, /, ""))
        .join("; ");
    return "";
  };

  const root = document.documentElement;
  const loggedIn = !!localStorage.getItem(TOKEN_KEY);

  // Theme
  $("#theme-btn").addEventListener("click", () => {
    const next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
    root.setAttribute("data-theme", next);
    try {
      localStorage.setItem("whm_theme", next);
    } catch {}
  });

  // Mobile menu
  const links = $("#nav-links");
  const toggle = $("#menu-toggle");
  toggle.addEventListener("click", () => {
    const open = links.classList.toggle("open");
    toggle.setAttribute("aria-expanded", String(open));
  });
  links.addEventListener("click", (e) => {
    if (e.target.closest("a")) links.classList.remove("open");
  });

  // Logged-in state
  if (loggedIn) {
    $("#nav-login").classList.add("hidden");
    $("#nav-start").textContent = "Open dashboard";
    $$("[data-open-auth]").forEach((b) => {
      if (b.id !== "nav-start" && b.textContent.includes("Get Started"))
        b.textContent = "Open dashboard";
    });
  }

  // Auth dialog
  const dlg = $("#auth-dialog");
  function setMode(mode) {
    $$("[data-auth-tab]").forEach((t) => {
      const on = t.dataset.authTab === mode;
      t.classList.toggle("active", on);
      t.setAttribute("aria-selected", String(on));
    });
    $("#login-form").classList.toggle("hidden", mode !== "login");
    $("#register-form").classList.toggle("hidden", mode !== "register");
    $("#auth-error").textContent = "";
  }
  function openAuth(mode) {
    if (loggedIn) {
      location.href = "/dashboard";
      return;
    }
    setMode(mode);
    dlg.showModal();
  }
  document.addEventListener("click", (e) => {
    const t = e.target.closest("[data-open-auth]");
    if (t) {
      e.preventDefault();
      openAuth(t.dataset.openAuth);
    }
  });
  $$("[data-auth-tab]").forEach((t) =>
    t.addEventListener("click", () => setMode(t.dataset.authTab)),
  );
  $("#auth-close").addEventListener("click", () => dlg.close());
  dlg.addEventListener("click", (e) => {
    if (e.target === dlg) dlg.close();
  });

  async function submitAuth(form, path, payload) {
    const btn = $("button[type=submit]", form);
    const err = $("#auth-error");
    const label = btn.textContent;
    err.textContent = "";
    btn.disabled = true;
    btn.textContent = "Please wait…";
    try {
      const res = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(errMsg(data) || "Something went wrong");
      localStorage.setItem(TOKEN_KEY, data.access_token);
      location.href = "/dashboard";
    } catch (e) {
      err.textContent = e.message;
    } finally {
      btn.disabled = false;
      btn.textContent = label;
    }
  }
  $("#login-form").addEventListener("submit", (e) => {
    e.preventDefault();
    submitAuth(e.target, "/auth/login", {
      email: $("#login-email").value.trim(),
      password: $("#login-password").value,
    });
  });
  $("#register-form").addEventListener("submit", (e) => {
    e.preventDefault();
    submitAuth(e.target, "/auth/register", {
      name: $("#reg-name").value.trim(),
      email: $("#reg-email").value.trim(),
      password: $("#reg-password").value,
    });
  });

  // Live demo check
  const STATUS = {
    operational: "Operational",
    degraded: "Degraded",
    down: "Down",
    unknown: "Unknown",
  };
  $("#demo-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const out = $("#demo-result");
    const btn = $("#demo-btn");
    const url = $("#demo-url").value.trim();
    if (!url) {
      out.innerHTML = '<p class="demo-error">Please enter a website URL.</p>';
      return;
    }
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> Checking';
    out.innerHTML = "";
    try {
      const res = await fetch("/demo/check", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url }),
      });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(errMsg(d) || "The check failed");
      const st = STATUS[d.health_status] ? d.health_status : "unknown";
      out.innerHTML = `
          <span class="pill ${st}">${STATUS[st]}</span>
          <dl class="result-grid">
            <div><dt>HTTP status</dt><dd>${d.status_code ?? "n/a"}</dd></div>
            <div><dt>Response time</dt><dd>${d.response_time_ms != null ? Math.round(d.response_time_ms) + " ms" : "n/a"}</dd></div>
            <div><dt>Final URL</dt><dd>${esc(d.final_url || d.requested_url)}</dd></div>
            <div><dt>Page title</dt><dd>${esc(d.page_title || "n/a")}</dd></div>
            <div><dt>Server</dt><dd>${esc(d.server || "Not disclosed")}</dd></div>
            <div><dt>Redirects</dt><dd>${d.redirect_count}</dd></div>
          </dl>
          ${d.error_message ? `<p class="demo-error">${esc(d.error_message)}</p>` : ""}
          <p class="muted small" style="margin-top:12px">Live check performed just now. Not saved to any account.</p>`;
    } catch (err) {
      out.innerHTML = `<p class="demo-error">${esc(err.message)}</p>`;
    } finally {
      btn.disabled = false;
      btn.textContent = "Check website";
    }
  });

  // Scroll reveal
  if ("IntersectionObserver" in window) {
    const io = new IntersectionObserver(
      (entries) =>
        entries.forEach((en) => {
          if (en.isIntersecting) {
            en.target.classList.add("in");
            io.unobserve(en.target);
          }
        }),
      { threshold: 0.12 },
    );
    $$(".reveal").forEach((el) => io.observe(el));
  } else {
    $$(".reveal").forEach((el) => el.classList.add("in"));
  }
})();
