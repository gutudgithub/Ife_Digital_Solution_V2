document.addEventListener("DOMContentLoaded", () => {
  const container = document.querySelector("[data-document-line-formset]");
  const template = document.querySelector("[data-document-empty-line]");
  const addButton = document.querySelector("[data-document-add-line]");
  const totalForms = document.querySelector("[name$='-TOTAL_FORMS']");
  const maxForms = document.querySelector("[name$='-MAX_NUM_FORMS']");

  if (!container || !template || !addButton || !totalForms || !maxForms) {
    return;
  }

  addButton.addEventListener("click", () => {
    const index = Number.parseInt(totalForms.value, 10);
    const maximum = Number.parseInt(maxForms.value, 10);
    if (Number.isNaN(index) || (!Number.isNaN(maximum) && index >= maximum)) {
      return;
    }

    const holder = document.createElement("div");
    holder.innerHTML = template.innerHTML.replaceAll("__prefix__", String(index)).trim();
    const fieldset = holder.firstElementChild;
    if (!fieldset) {
      return;
    }

    const legend = fieldset.querySelector("legend");
    if (legend) {
      legend.textContent = `${addButton.dataset.lineLabel} ${index + 1}`;
    }
    container.append(fieldset);
    totalForms.value = String(index + 1);
    fieldset.querySelector("select, input")?.focus();
  });
});
