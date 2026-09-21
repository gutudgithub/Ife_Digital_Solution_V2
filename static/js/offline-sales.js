(() => {
  const app = document.querySelector("#offline-sales-app");
  if (!app || !("indexedDB" in window)) {
    return;
  }

  const catalogStatus = document.querySelector("#offline-catalog-status");
  const linesContainer = document.querySelector("#offline-lines");
  const queueContainer = document.querySelector("#offline-queue");
  const form = document.querySelector("#offline-sale-form");
  const paymentMethod = document.querySelector("#offline-payment-method");
  const telebirrReference = document.querySelector("#offline-telebirr-reference");
  const provisionalNote = document.querySelector("#offline-provisional-note");
  const csrfToken = form.querySelector("[name=csrfmiddlewaretoken]").value;
  const sevenDays = 7 * 24 * 60 * 60 * 1000;
  const catalogKey = `${app.dataset.businessId}:${app.dataset.branchId}`;
  let catalog = null;
  let variants = new Map();

  const databasePromise = new Promise((resolve, reject) => {
    const request = indexedDB.open("ife-offline-v1", 2);
    request.onupgradeneeded = (event) => {
      const database = request.result;
      if (
        database.objectStoreNames.contains("catalogs") &&
        event.oldVersion < 2
      ) {
        database.deleteObjectStore("catalogs");
      }
      if (!database.objectStoreNames.contains("catalogs")) {
        database.createObjectStore("catalogs", { keyPath: "catalog_key" });
      }
      if (!database.objectStoreNames.contains("drafts")) {
        database.createObjectStore("drafts", { keyPath: "local_draft_id" });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });

  async function storeRequest(storeName, mode, operation) {
    const database = await databasePromise;
    return new Promise((resolve, reject) => {
      const transaction = database.transaction(storeName, mode);
      const store = transaction.objectStore(storeName);
      const request = operation(store);
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  }

  const put = (store, value) =>
    storeRequest(store, "readwrite", (objectStore) => objectStore.put(value));
  const remove = (store, key) =>
    storeRequest(store, "readwrite", (objectStore) => objectStore.delete(key));
  const get = (store, key) =>
    storeRequest(store, "readonly", (objectStore) => objectStore.get(key));
  const getAll = (store) =>
    storeRequest(store, "readonly", (objectStore) => objectStore.getAll());

  function setCatalogStatus(message) {
    catalogStatus.textContent = message;
  }

  async function purgeExpiredData() {
    const now = Date.now();
    let removedDraft = false;
    const drafts = await getAll("drafts");
    for (const draft of drafts) {
      if (now - Date.parse(draft.offline_created_at) > sevenDays) {
        await remove("drafts", draft.local_draft_id);
        removedDraft = true;
      }
    }
    const savedCatalog = await get("catalogs", catalogKey);
    if (
      savedCatalog &&
      now - Date.parse(savedCatalog.generated_at) > sevenDays
    ) {
      await remove("catalogs", catalogKey);
    }
    if (removedDraft) {
      setCatalogStatus(app.dataset.expired);
    }
  }

  async function loadCatalog() {
    setCatalogStatus(app.dataset.loading);
    if (navigator.onLine) {
      try {
        const response = await fetch(app.dataset.catalogUrl, {
          credentials: "same-origin",
          headers: { Accept: "application/json" },
        });
        if (response.ok) {
          catalog = await response.json();
          catalog.catalog_key = catalogKey;
          await put("catalogs", catalog);
        }
      } catch {
        catalog = null;
      }
    }
    if (!catalog) {
      catalog = await get("catalogs", catalogKey);
    }
    if (!catalog) {
      setCatalogStatus(app.dataset.catalogMissing);
      form.querySelectorAll("button").forEach((button) => {
        button.disabled = true;
      });
      return;
    }
    variants = new Map(catalog.variants.map((variant) => [variant.id, variant]));
    setCatalogStatus(app.dataset.catalogReady);
    addLine();
  }

  function fieldLabel(text, control) {
    const label = document.createElement("label");
    label.textContent = text;
    label.append(control);
    return label;
  }

  function addLine(selectedId = "", quantity = "") {
    if (!catalog) {
      return;
    }
    const row = document.createElement("div");
    row.className = "offline-line";

    const select = document.createElement("select");
    select.required = true;
    const empty = document.createElement("option");
    empty.value = "";
    empty.textContent = app.dataset.selectProduct;
    select.append(empty);
    for (const variant of catalog.variants) {
      const option = document.createElement("option");
      option.value = variant.id;
      option.textContent = `${variant.label} — ETB ${variant.selling_price} / ${variant.stock_unit}`;
      option.selected = variant.id === selectedId;
      select.append(option);
    }

    const quantityInput = document.createElement("input");
    quantityInput.type = "number";
    quantityInput.min = "0.001";
    quantityInput.step = "0.001";
    quantityInput.inputMode = "decimal";
    quantityInput.required = true;
    quantityInput.value = quantity;

    const removeButton = document.createElement("button");
    removeButton.type = "button";
    removeButton.className = "button button--secondary";
    removeButton.textContent = app.dataset.removeLine;
    removeButton.addEventListener("click", () => {
      if (linesContainer.children.length > 1) {
        row.remove();
      }
    });

    row.append(
      fieldLabel(app.dataset.productLabel, select),
      fieldLabel(app.dataset.quantityLabel, quantityInput),
      removeButton,
    );
    linesContainer.append(row);
  }

  function collectLines() {
    const lines = [];
    const seen = new Set();
    for (const row of linesContainer.querySelectorAll(".offline-line")) {
      const select = row.querySelector("select");
      const input = row.querySelector("input");
      const variant = variants.get(select.value);
      const quantity = input.value.trim();
      const validQuantity =
        /^\d{1,15}(\.\d{1,3})?$/.test(quantity) &&
        !/^0+(?:\.0+)?$/.test(quantity);
      if (
        !variant ||
        seen.has(variant.id) ||
        !validQuantity
      ) {
        return null;
      }
      seen.add(variant.id);
      lines.push({
        variant_id: variant.id,
        quantity,
        selling_price: variant.selling_price,
        product_name: variant.product_name,
        variant_label: variant.label,
      });
    }
    return lines.length ? lines : null;
  }

  function resetForm() {
    linesContainer.replaceChildren();
    addLine();
    paymentMethod.value = "cash";
    telebirrReference.value = "";
  }

  function restorePaymentSelection() {
    if (!paymentMethod.value) {
      paymentMethod.value = "cash";
      telebirrReference.value = "";
    }
  }

  async function saveDraft(event) {
    event.preventDefault();
    const lines = collectLines();
    if (!lines) {
      setCatalogStatus(app.dataset.invalidLines);
      return;
    }
    const draft = {
      business_id: app.dataset.businessId,
      branch_id: app.dataset.branchId,
      role: app.dataset.role,
      local_draft_id: crypto.randomUUID(),
      idempotency_key: crypto.randomUUID(),
      offline_created_at: new Date().toISOString(),
      payment_method: paymentMethod.value,
      telebirr_reference: telebirrReference.value.trim(),
      lines,
      status: "pending",
      conflicts: [],
      errors: [],
    };
    await put("drafts", draft);
    resetForm();
    setCatalogStatus(app.dataset.draftSaved);
    await renderQueue();
  }

  function button(text, callback, secondary = true) {
    const action = document.createElement("button");
    action.type = "button";
    action.className = secondary ? "button button--secondary" : "button";
    action.textContent = text;
    action.addEventListener("click", callback);
    return action;
  }

  function statusText(status) {
    const labels = {
      pending: app.dataset.pending,
      synced: app.dataset.synced,
      needs_review: app.dataset.needsReview,
      rejected: app.dataset.rejected,
    };
    return labels[status] || status;
  }

  function conflictText(code) {
    const labels = {
      price_changed: app.dataset.priceChanged,
      catalog_label_changed: app.dataset.labelChanged,
    };
    return labels[code] || code;
  }

  async function discardDraft(draft) {
    if (!window.confirm(app.dataset.discardConfirm)) {
      return;
    }
    await remove("drafts", draft.local_draft_id);
    await renderQueue();
  }

  function copyDraft(draft) {
    linesContainer.replaceChildren();
    for (const line of draft.lines) {
      addLine(line.variant_id, line.quantity);
    }
    paymentMethod.value = draft.payment_method;
    telebirrReference.value = draft.telebirr_reference;
    form.scrollIntoView({ behavior: "smooth" });
  }

  async function syncDraft(draft) {
    if (!navigator.onLine) {
      setCatalogStatus(app.dataset.syncFailed);
      return;
    }
    const payload = {
      local_draft_id: draft.local_draft_id,
      idempotency_key: draft.idempotency_key,
      business_id: draft.business_id,
      branch_id: draft.branch_id,
      role_at_draft: draft.role,
      offline_created_at: draft.offline_created_at,
      payment_method: draft.payment_method,
      telebirr_reference: draft.telebirr_reference,
      lines: draft.lines,
    };
    try {
      const response = await fetch(app.dataset.syncUrl, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          "X-CSRFToken": csrfToken,
        },
        body: JSON.stringify(payload),
      });
      const result = await response.json();
      if (!response.ok) {
        draft.status = "rejected";
        draft.errors = result.errors || [app.dataset.syncFailed];
      } else {
        draft.status = result.status;
        draft.conflicts = result.conflicts || [];
        draft.errors = [];
        draft.sale_url = result.sale_url;
      }
      await put("drafts", draft);
    } catch {
      setCatalogStatus(app.dataset.syncFailed);
    }
    await renderQueue();
  }

  async function syncAll() {
    const drafts = await getAll("drafts");
    for (const draft of drafts.filter(
      (item) =>
        item.status === "pending" &&
        item.business_id === app.dataset.businessId &&
        item.branch_id === app.dataset.branchId,
    )) {
      await syncDraft(draft);
    }
  }

  function showProvisionalNote(draft) {
    document.querySelector("#offline-provisional-warning").textContent =
      app.dataset.notReceipt;
    document.querySelector("#offline-note-created").textContent = new Date(
      draft.offline_created_at,
    ).toLocaleString();
    document.querySelector("#offline-note-payment").textContent =
      draft.payment_method === "cash" ? app.dataset.cash : app.dataset.telebirr;

    const table = document.createElement("table");
    const head = document.createElement("thead");
    const headRow = document.createElement("tr");
    for (const heading of [
      app.dataset.productLabel,
      app.dataset.quantityLabel,
      app.dataset.snapshotPrice,
    ]) {
      const cell = document.createElement("th");
      cell.textContent = heading;
      headRow.append(cell);
    }
    head.append(headRow);
    const body = document.createElement("tbody");
    for (const line of draft.lines) {
      const row = document.createElement("tr");
      for (const value of [
        line.variant_label,
        line.quantity,
        `ETB ${line.selling_price}`,
      ]) {
        const cell = document.createElement("td");
        cell.textContent = value;
        row.append(cell);
      }
      body.append(row);
    }
    table.append(head, body);
    document.querySelector("#offline-note-lines").replaceChildren(table);
    provisionalNote.hidden = false;
    provisionalNote.scrollIntoView({ behavior: "smooth" });
  }

  async function renderQueue() {
    const drafts = (await getAll("drafts")).filter(
      (draft) =>
        draft.business_id === app.dataset.businessId &&
        draft.branch_id === app.dataset.branchId,
    );
    drafts.sort(
      (left, right) =>
        Date.parse(right.offline_created_at) - Date.parse(left.offline_created_at),
    );
    queueContainer.replaceChildren();
    if (!drafts.length) {
      const empty = document.createElement("p");
      empty.className = "muted";
      empty.textContent = app.dataset.emptyQueue;
      queueContainer.append(empty);
      return;
    }
    for (const draft of drafts) {
      const card = document.createElement("article");
      card.className = "offline-draft-card";
      const summary = document.createElement("div");
      const status = document.createElement("span");
      status.className = `offline-status offline-status--${draft.status}`;
      status.textContent = statusText(draft.status);
      const created = document.createElement("p");
      created.textContent = new Date(draft.offline_created_at).toLocaleString();
      const lines = document.createElement("p");
      lines.textContent = `${draft.lines.length} ${app.dataset.lineCount}`;
      summary.append(status, created, lines);
      for (const message of [
        ...(draft.conflicts || []).map(conflictText),
        ...(draft.errors || []),
      ]) {
        const warning = document.createElement("p");
        warning.className = "errorlist";
        warning.textContent = message;
        summary.append(warning);
      }
      if (draft.sale_url) {
        const link = document.createElement("a");
        link.href = draft.sale_url;
        link.textContent = app.dataset.openServerDraft;
        summary.append(link);
      }

      const actions = document.createElement("div");
      actions.className = "offline-draft-actions";
      actions.append(
        button(app.dataset.provisional, () => showProvisionalNote(draft)),
      );
      if (draft.status === "pending") {
        actions.append(button(app.dataset.sync, () => syncDraft(draft), false));
      }
      if (draft.status === "rejected") {
        actions.append(button(app.dataset.copy, () => copyDraft(draft)));
      }
      if (draft.status === "synced" || draft.status === "needs_review") {
        actions.append(
          button(app.dataset.acknowledge, async () => {
            await remove("drafts", draft.local_draft_id);
            await renderQueue();
          }),
        );
      } else {
        actions.append(button(app.dataset.discard, () => discardDraft(draft)));
      }
      card.append(summary, actions);
      queueContainer.append(card);
    }
  }

  document
    .querySelector("#add-offline-line")
    .addEventListener("click", () => addLine());
  document
    .querySelector("#sync-all-offline")
    .addEventListener("click", syncAll);
  document
    .querySelector("#print-offline-note")
    .addEventListener("click", () => window.print());
  paymentMethod.addEventListener("change", () => {
    if (paymentMethod.value === "cash") {
      telebirrReference.value = "";
    }
  });
  form.addEventListener("submit", saveDraft);
  window.addEventListener("pageshow", restorePaymentSelection);

  restorePaymentSelection();
  purgeExpiredData()
    .then(loadCatalog)
    .then(renderQueue)
    .catch(() => setCatalogStatus(app.dataset.catalogMissing));
})();
