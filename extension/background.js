importScripts("config.js");

const STORAGE_KEYS = {
  TOKEN: "whm_ext_token",
  USER: "whm_ext_user",
  API_BASE: "whm_api_base_url"
};

chrome.runtime.onMessageExternal.addListener((message, sender, sendResponse) => {
  if (!message || typeof message !== "object") return false;

  if (message.type === "WHM_PING") {
    chrome.storage.local.get([STORAGE_KEYS.TOKEN, STORAGE_KEYS.USER, STORAGE_KEYS.API_BASE], (data) => {
      sendResponse({
        installed: true,
        connected: Boolean(data[STORAGE_KEYS.TOKEN]),
        user: data[STORAGE_KEYS.USER] || null,
        apiBaseUrl: data[STORAGE_KEYS.API_BASE] || null
      });
    });
    return true;
  }

  if (message.type === "WHM_AUTO_PAIR" && message.code) {
    (async () => {
      try {
        const senderOrigin = sender?.url ? new URL(sender.url).origin : null;
        const apiBase = (message.apiBaseUrl || senderOrigin || WHM_CONFIG.DEFAULT_API_BASE_URL).replace(/\/+$/, "");
        const res = await fetch(`${apiBase}/extension/pair/exchange`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ code: message.code })
        });
        const payload = await res.json();
        if (!res.ok) {
          sendResponse({ ok: false, error: payload?.detail || "Pairing failed" });
          return;
        }
        await chrome.storage.local.set({
          [STORAGE_KEYS.API_BASE]: apiBase,
          [STORAGE_KEYS.TOKEN]: payload.access_token,
          [STORAGE_KEYS.USER]: payload.user
        });
        sendResponse({ ok: true, user: payload.user });
      } catch (err) {
        sendResponse({ ok: false, error: err.message || "Extension could not reach backend" });
      }
    })();
    return true;
  }

  if (message.type === "WHM_REMOTE_DISCONNECT") {
    chrome.storage.local.remove([STORAGE_KEYS.TOKEN, STORAGE_KEYS.USER], () => {
      sendResponse({ ok: true });
    });
    return true;
  }

  return false;
});
