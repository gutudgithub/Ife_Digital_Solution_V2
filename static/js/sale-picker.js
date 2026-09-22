(() => {
  const picker = document.querySelector("[data-sale-picker]");
  const totalForms = document.querySelector("[name$='-TOTAL_FORMS']");
  const emptyTemplate = document.querySelector("#sale-line-empty-template");
  if (!(picker instanceof HTMLElement) ||
      !(totalForms instanceof HTMLInputElement) ||
      !(emptyTemplate instanceof HTMLTemplateElement)) {
    return;
  }

  picker.hidden = false;
  const search = picker.querySelector("[data-sale-picker-search]");
  const status = picker.querySelector("[data-sale-picker-status]");
  const productCards = [...picker.querySelectorAll("[data-sale-picker-product]")];

  const announce = (message) => {
    if (status instanceof HTMLElement) {
      status.textContent = message;
    }
  };

  const lineFields = () => [...document.querySelectorAll("[data-sale-line]")];

  const addLine = () => {
    const index = Number.parseInt(totalForms.value, 10);
    const fragment = emptyTemplate.content.cloneNode(true);
    const container = document.createElement("div");
    container.append(fragment);
    container.innerHTML = container.innerHTML.replaceAll("__prefix__", String(index));
    const line = container.firstElementChild;
    emptyTemplate.before(line);
    totalForms.value = String(index + 1);
    return line;
  };

  const chooseVariant = (button) => {
    const variantId = button.dataset.salePickerVariant || "";
    const label = button.dataset.salePickerLabel || "";
    let selectedLine = null;

    for (const line of lineFields()) {
      const select = line.querySelector("select[name$='-variant']");
      if (select instanceof HTMLSelectElement && select.value === variantId) {
        selectedLine = line;
        break;
      }
    }
    if (selectedLine === null) {
      selectedLine = lineFields().find((line) => {
        const select = line.querySelector("select[name$='-variant']");
        return select instanceof HTMLSelectElement && select.value === "";
      }) || addLine();
    }

    const select = selectedLine.querySelector("select[name$='-variant']");
    const quantity = selectedLine.querySelector("input[name$='-quantity']");
    if (!(select instanceof HTMLSelectElement) || !(quantity instanceof HTMLInputElement)) {
      return;
    }
    if (select.value === variantId) {
      quantity.value = String(Number.parseFloat(quantity.value || "0") + 1);
    } else {
      select.value = variantId;
      select.dispatchEvent(new Event("change", {bubbles: true}));
      quantity.value = quantity.value || "1";
    }
    announce(`${label} — ${picker.dataset.addedMessage || ""}`);
    selectedLine.scrollIntoView({behavior: "smooth", block: "center"});
    quantity.focus();
  };

  picker.addEventListener("click", (event) => {
    const target = event.target;
    if (target instanceof HTMLButtonElement && target.dataset.salePickerVariant) {
      chooseVariant(target);
    }
  });

  if (search instanceof HTMLInputElement) {
    search.addEventListener("input", () => {
      const query = search.value.trim().toLocaleLowerCase();
      productCards.forEach((card) => {
        const text = `${card.textContent || ""} ${card.dataset.search || ""}`.toLocaleLowerCase();
        card.hidden = query !== "" && !text.includes(query);
      });
    });
  }
})();
