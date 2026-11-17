// Announces extension presence to the Website Health Monitor dashboard for 1-click pairing
window.postMessage({ type: "WHM_EXTENSION_READY", extensionId: chrome.runtime.id }, "*");
window.addEventListener("message", (event) => {
  if (event.source !== window || !event.data) return;
  if (event.data.type === "WHM_PING_FROM_PAGE") {
    window.postMessage({ type: "WHM_EXTENSION_READY", extensionId: chrome.runtime.id }, "*");
  }
});
