(function () {
  function announce() {
    window.postMessage({ type: "WHM_EXTENSION_READY", extensionId: chrome.runtime.id }, "*");
  }
  announce();
  window.addEventListener("DOMContentLoaded", announce);
  window.addEventListener("message", (event) => {
    if (event.source !== window || !event.data) return;
    if (event.data.type === "WHM_PING_FROM_PAGE") announce();
  });
})();
