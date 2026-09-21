(() => {
  const status = document.querySelector("#connection-status");

  function updateStatus() {
    if (!status) {
      return;
    }
    const online = navigator.onLine;
    status.textContent = online ? status.dataset.online : status.dataset.offline;
    status.classList.toggle("connection-status--offline", !online);
    status.hidden = online;
  }

  window.addEventListener("online", updateStatus);
  window.addEventListener("offline", updateStatus);
  updateStatus();

  if (
    "serviceWorker" in navigator &&
    document.querySelector('link[rel="manifest"]')
  ) {
    window.addEventListener("load", () => {
      navigator.serviceWorker
        .register("/offline/service-worker.js", { scope: "/offline/" })
        .catch(() => undefined);
    });
  }
})();
