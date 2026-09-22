(() => {
  document.addEventListener("click", (event) => {
    const target = event.target;
    if (target instanceof HTMLElement && target.matches("[data-print-page]")) {
      window.print();
    }
  });
})();
