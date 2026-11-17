(function () {
  "use strict";
  const STORAGE_KEYS = { TOKEN: "whm_ext_token", USER: "whm_ext_user", API_BASE: "whm_api_base_url" };
  let apiBaseUrl = WHM_CONFIG.DEFAULT_API_BASE_URL;
  let extToken = null;
  let currentUser = null;
  let websites = [];
  let activeTabUrl = null;
  let activeTabTitle = null;
  const checkingIds = new Set();
  const $ = (sel) => document.querySelector(sel);

  function escapeHtml(str) {
    return String(str ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function showBanner(message, type = "info") {
    const el = $("#alert-banner");
    el.textContent = message;
    el.className = `banner ${type}`;
    el.classList.remove("hidden");
    clearTimeout(showBanner._timer);
    showBanner._timer = setTimeout(() => el.classList.add("hidden"), 4500);
  }
  function formatRelativeTime(isoString) {
    if (!isoString) return "Never";
    const diffSec = Math.max(0, Math.floor((Date.now() - new Date(isoString).getTime()) / 1000));
    if (diffSec < 10) return "Just now";
    if (diffSec < 60) return `${diffSec}s ago`;
    const mins = Math.floor(diffSec / 60);
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    return `${Math.floor(hrs / 24)}d ago`;
  }
  function getHostLabel(url) {
    try { return new URL(url).host; } catch { return url; }
  }
  async function loadStorage() {
    const data = await chrome.storage.local.get([STORAGE_KEYS.TOKEN, STORAGE_KEYS.USER, STORAGE_KEYS.API_BASE]);
    apiBaseUrl = (data[STORAGE_KEYS.API_BASE] || WHM_CONFIG.DEFAULT_API_BASE_URL).replace(/\/+$/, "");
    extToken = data[STORAGE_KEYS.TOKEN] || null;
    currentUser = data[STORAGE_KEYS.USER] || null;
    $("#api-url-input").value = apiBaseUrl;
    $("#api-env-label").textContent = getHostLabel(apiBaseUrl);
  }
  async function apiFetch(path, options = {}) {
    const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
    if (extToken) headers["Authorization"] = `Bearer ${extToken}`;
    let response;
    try {
      response = await fetch(`${apiBaseUrl}${path}`, {
        method: options.method || "GET",
        headers,
        body: options.body ? JSON.stringify(options.body) : undefined
      });
    } catch {
      throw new Error(`Cannot reach backend at ${apiBaseUrl}. Ensure server is running.`);
    }
    if (response.status === 204) return null;
    let payload = null;
    try { payload = await response.json(); } catch { payload = null; }
    if (!response.ok) {
      if (response.status === 401 && extToken && !options.skipAuthReset) {
        await clearSession("Session expired or revoked. Please pair again.");
      }
      const detail = payload?.detail || `Request failed (${response.status})`;
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return payload;
  }
  async function clearSession(reasonMessage) {
    extToken = null; currentUser = null; websites = [];
    await chrome.storage.local.remove([STORAGE_KEYS.TOKEN, STORAGE_KEYS.USER]);
    renderAuthState();
    if (reasonMessage) showBanner(reasonMessage, "error");
  }
  function renderAuthState() {
    const connected = Boolean(extToken);
    const badge = $("#conn-badge");
    const connText = $("#conn-text");
    if (connected) {
      badge.className = "conn-badge connected";
      connText.textContent = "Connected";
      $("#pairing-view").classList.add("hidden");
      $("#monitor-view").classList.remove("hidden");
      $("#refresh-btn").disabled = false;
      const name = currentUser?.name || "Connected User";
      $("#user-name").textContent = name;
      $("#user-email").textContent = currentUser?.email || "";
      $("#user-avatar").textContent = name.charAt(0).toUpperCase();
    } else {
      badge.className = "conn-badge disconnected";
      connText.textContent = "Not Connected";
      $("#monitor-view").classList.add("hidden");
      $("#pairing-view").classList.remove("hidden");
      $("#refresh-btn").disabled = true;
    }
    updateCurrentTabMatch();
  }
  function renderWebsites() {
    const listEl = $("#websites-list");
    const counts = { operational: 0, degraded: 0, down: 0 };
    websites.forEach((w) => {
      const st = w.latest?.health_status;
      if (st && Object.prototype.hasOwnProperty.call(counts, st)) counts[st] += 1;
    });
    $("#stat-total").textContent = websites.length;
    $("#stat-ok").textContent = counts.operational;
    $("#stat-warn").textContent = counts.degraded;
    $("#stat-bad").textContent = counts.down;

    if (!websites.length) {
      listEl.innerHTML = `<div class="empty-state"><h3>No monitored websites yet</h3><p>Click <strong>+ Monitor</strong> above to monitor your current open tab, or open the dashboard.</p><button type="button" class="btn btn-sm btn-primary" id="empty-open-dash">Open Dashboard &#8599;</button></div>`;
      const btn = $("#empty-open-dash");
      if (btn) btn.addEventListener("click", openDashboard);
      updateCurrentTabMatch();
      return;
    }
    listEl.innerHTML = websites.map((site) => {
      const latest = site.latest;
      const status = latest?.health_status || "unknown";
      const displayName = site.display_name || getHostLabel(site.url);
      const httpCode = latest?.status_code != null ? `HTTP ${latest.status_code}` : "No HTTP code";
      const latency = latest?.response_time_ms != null ? `${Math.round(latest.response_time_ms)} ms` : "?";
      const checked = formatRelativeTime(latest?.checked_at);
      const isChecking = checkingIds.has(site.id);
      return `<article class="site-card" data-site-id="${site.id}">
        <div class="site-top">
          <div class="site-title-wrap">
            <div class="site-name">${escapeHtml(displayName)}</div>
            <a class="site-url" href="${escapeHtml(site.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(site.url)}</a>
          </div>
          <span class="pill ${escapeHtml(status)}">${escapeHtml(status)}</span>
        </div>
        <div class="site-metrics">
          <div class="metric-group">
            <span class="metric"><strong>${escapeHtml(httpCode)}</strong></span>
            <span class="metric">Latency: <strong>${escapeHtml(latency)}</strong></span>
            <span class="metric">Checked: <strong>${escapeHtml(checked)}</strong></span>
          </div>
          <button type="button" class="btn btn-xs" data-check-id="${site.id}" ${isChecking ? "disabled" : ""}>
            ${isChecking ? '<span class="spinner"></span> Checking' : "Check Now"}
          </button>
        </div>
      </article>`;
    }).join("");
    updateCurrentTabMatch();
  }
  async function refreshData(silent = false) {
    if (!extToken) return;
    const refreshBtn = $("#refresh-btn");
    refreshBtn.disabled = true;
    refreshBtn.innerHTML = '<span class="spinner"></span> Syncing';
    try {
      const [meData, sitesData] = await Promise.all([apiFetch("/me"), apiFetch("/websites")]);
      currentUser = meData;
      websites = Array.isArray(sitesData) ? sitesData : [];
      await chrome.storage.local.set({ [STORAGE_KEYS.USER]: currentUser });
      renderAuthState();
      renderWebsites();
      if (!silent) showBanner("Websites updated", "success");
    } catch (err) {
      if (extToken) showBanner(err.message, "error");
    } finally {
      refreshBtn.disabled = !extToken;
      refreshBtn.innerHTML = "&#8635; Refresh";
    }
  }
  async function triggerManualCheck(websiteId) {
    if (checkingIds.has(websiteId)) return;
    checkingIds.add(websiteId);
    renderWebsites();
    try {
      const checkResult = await apiFetch(`/websites/${websiteId}/check`, { method: "POST" });
      websites = websites.map((w) => (w.id === websiteId ? { ...w, latest: checkResult } : w));
      showBanner(`Check complete: ${checkResult.health_status.toUpperCase()}`, "success");
    } catch (err) {
      showBanner(err.message, "error");
    } finally {
      checkingIds.delete(websiteId);
      renderWebsites();
    }
  }
  async function monitorCurrentSite() {
    if (!extToken || !activeTabUrl) return;
    const btn = $("#monitor-current-btn");
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> Adding';
    try {
      const parsed = new URL(activeTabUrl);
      const cleanName = (activeTabTitle || parsed.hostname).slice(0, 60);
      const created = await apiFetch("/websites", {
        method: "POST",
        body: { url: parsed.origin, display_name: cleanName }
      });
      websites = [...websites, created];
      renderWebsites();
      showBanner(`Added ${parsed.hostname} ? running initial check?`, "success");
      await triggerManualCheck(created.id);
    } catch (err) {
      showBanner(err.message, "error");
    } finally {
      btn.disabled = false;
      btn.textContent = "+ Monitor";
    }
  }
  async function detectCurrentTab() {
    try {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (tab?.url && /^https?:\/\//i.test(tab.url)) {
        activeTabUrl = tab.url;
        activeTabTitle = tab.title || "";
        const bar = $("#current-tab-bar");
        const urlEl = $("#current-tab-url");
        urlEl.textContent = getHostLabel(tab.url);
        urlEl.title = tab.url;
        bar.classList.remove("hidden");
        updateCurrentTabMatch();
      }
    } catch {}
  }
  function updateCurrentTabMatch() {
    if (!activeTabUrl) return;
    const statusEl = $("#current-tab-status");
    const addBtn = $("#monitor-current-btn");
    try {
      const activeOrigin = new URL(activeTabUrl).origin;
      const match = websites.find((w) => { try { return new URL(w.url).origin === activeOrigin; } catch { return false; } });
      if (match) {
        statusEl.textContent = `Monitored (${match.latest?.health_status || "unknown"})`;
        addBtn.classList.add("hidden");
      } else {
        statusEl.textContent = extToken ? "" : "Not monitored";
        if (extToken) addBtn.classList.remove("hidden");
        else addBtn.classList.add("hidden");
      }
    } catch {
      statusEl.textContent = "";
      addBtn.classList.add("hidden");
    }
  }
  function openDashboard() { chrome.tabs.create({ url: `${apiBaseUrl}/dashboard#extension` }); }

  $("#monitor-current-btn").addEventListener("click", monitorCurrentSite);
  $("#pairing-code-input").addEventListener("input", (e) => {
    const cleaned = e.target.value.replace(/[^a-zA-Z0-9]/g, "").toUpperCase().slice(0, 8);
    e.target.value = cleaned.length > 4 ? `${cleaned.slice(0, 4)}-${cleaned.slice(4)}` : cleaned;
  });
  $("#pairing-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const rawCode = $("#pairing-code-input").value.trim();
    if (!rawCode) return;
    const btn = $("#connect-btn");
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> Connecting?';
    try {
      const res = await apiFetch("/extension/pair/exchange", { method: "POST", body: { code: rawCode }, skipAuthReset: true });
      extToken = res.access_token;
      currentUser = res.user;
      await chrome.storage.local.set({ [STORAGE_KEYS.TOKEN]: extToken, [STORAGE_KEYS.USER]: currentUser });
      $("#pairing-code-input").value = "";
      renderAuthState();
      showBanner("Extension connected!", "success");
      await refreshData(true);
    } catch (err) {
      showBanner(err.message, "error");
    } finally {
      btn.disabled = false;
      btn.textContent = "Pair with Code";
    }
  });
  $("#websites-list").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-check-id]");
    if (btn) triggerManualCheck(Number(btn.dataset.checkId));
  });
  $("#refresh-btn").addEventListener("click", () => refreshData(false));
  $("#dashboard-btn").addEventListener("click", openDashboard);
  $("#pair-open-dashboard").addEventListener("click", openDashboard);
  $("#disconnect-btn").addEventListener("click", async () => {
    const btn = $("#disconnect-btn");
    btn.disabled = true;
    try { await apiFetch("/extension/disconnect", { method: "POST", skipAuthReset: true }); } catch {}
    finally { btn.disabled = false; await clearSession("Extension disconnected."); }
  });
  $("#settings-toggle").addEventListener("click", () => $("#settings-panel").classList.toggle("hidden"));
  $("#save-api-url").addEventListener("click", async () => {
    const val = $("#api-url-input").value.trim().replace(/\/+$/, "");
    if (!/^https?:\/\//i.test(val)) { showBanner("Enter a valid http:// or https:// URL", "error"); return; }
    apiBaseUrl = val;
    await chrome.storage.local.set({ [STORAGE_KEYS.API_BASE]: apiBaseUrl });
    $("#api-env-label").textContent = getHostLabel(apiBaseUrl);
    $("#settings-panel").classList.add("hidden");
    showBanner(`API URL set to ${apiBaseUrl}`, "success");
    if (extToken) refreshData(true);
  });
  document.querySelectorAll("[data-preset]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const preset = WHM_CONFIG.PRESETS[btn.dataset.preset];
      if (preset) $("#api-url-input").value = preset;
    });
  });
  $("#help-link").addEventListener("click", (e) => {
    e.preventDefault();
    showBanner("Open Dashboard -> Chrome Extension -> Connect Extension (1-Click), or click + Monitor on any open site.", "info");
  });

  (async () => {
    await loadStorage();
    renderAuthState();
    await detectCurrentTab();
    if (extToken) await refreshData(true);
  })();
})();
