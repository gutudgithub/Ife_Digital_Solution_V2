(() => {
  const status = document.querySelector("#connection-status");
  const sessionKey = "ife-active-membership-id";
  const sessionExpiryKey = "ife-active-membership-expires-at";

  function updateStatus() {
    if (!status) {
      return;
    }
    const online = navigator.onLine;
    status.textContent = online ? status.dataset.online : status.dataset.offline;
    status.classList.toggle("connection-status--offline", !online);
    status.hidden = online;
  }

  function storeRequest(storeName, mode, operation) {
    if (!("indexedDB" in window)) {
      return Promise.resolve(null);
    }
    return new Promise((resolve) => {
      const request = indexedDB.open("ife-offline-v1", 2);
      request.onupgradeneeded = () => {
        const database = request.result;
        if (!database.objectStoreNames.contains("catalogs")) {
          database.createObjectStore("catalogs", { keyPath: "catalog_key" });
        }
        if (!database.objectStoreNames.contains("drafts")) {
          database.createObjectStore("drafts", { keyPath: "local_draft_id" });
        }
      };
      request.onerror = () => resolve(null);
      request.onsuccess = () => {
        const database = request.result;
        try {
          const transaction = database.transaction(storeName, mode);
          const store = transaction.objectStore(storeName);
          const storeOperation = operation(store);
          storeOperation.onsuccess = () => resolve(storeOperation.result);
          storeOperation.onerror = () => resolve(null);
        } catch {
          resolve(null);
        }
      };
    });
  }

  const getAll = (store) =>
    storeRequest(store, "readonly", (objectStore) => objectStore.getAll());
  const clear = (store) =>
    storeRequest(store, "readwrite", (objectStore) => objectStore.clear());

  async function unsafeLocalDraftCount() {
    const drafts = (await getAll("drafts")) || [];
    const membershipId = document.body.dataset.activeMembershipId;
    return drafts.filter(
      (draft) =>
        draft.drafted_by_id === membershipId &&
        (draft.status === "pending" || draft.status === "rejected"),
    ).length;
  }

  function protectLogout() {
    const form = document.querySelector("[data-offline-logout-form]");
    if (!form) {
      return;
    }
    form.addEventListener("submit", async (event) => {
      if (form.dataset.offlineLogoutConfirmed === "true") {
        return;
      }
      event.preventDefault();
      const count = await unsafeLocalDraftCount();
      if (
        count > 0 &&
        !window.confirm(`${document.body.dataset.logoutDraftsWarning} (${count})`)
      ) {
        return;
      }
      await clear("catalogs");
      sessionStorage.removeItem(sessionKey);
      sessionStorage.removeItem(sessionExpiryKey);
      form.dataset.offlineLogoutConfirmed = "true";
      form.submit();
    });
  }

  window.addEventListener("online", updateStatus);
  window.addEventListener("offline", updateStatus);
  protectLogout();
  updateStatus();

  if (
    "serviceWorker" in navigator &&
    document.querySelector('link[rel="manifest"]')
  ) {
    window.addEventListener("load", () => {
      const url = document.body.dataset.serviceWorkerUrl;
      const scope = document.body.dataset.serviceWorkerScope;
      if (!url || !scope) {
        return;
      }
      navigator.serviceWorker
        .register(url, { scope })
        .catch(() => undefined);
    });
  }
})();
