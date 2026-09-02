(function () {
  "use strict";

  function ready(fn) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", fn);
    } else {
      fn();
    }
  }

  ready(function () {
    const startBtn = document.querySelector("[data-demo-tour-start]");
    if (!startBtn || typeof window.driver === "undefined" || !window.driver.js) {
      // driver.js may expose driver.js.driver depending on bundle.
    }
    const DriverFactory = (window.driver && window.driver.js && window.driver.js.driver)
      || (window.driver && window.driver.driver)
      || window.driver;
    if (!startBtn || typeof DriverFactory !== "function") {
      return;
    }

    const steps = [
      { element: "[data-demo-tour='brand']", popover: { title: "Zyntra", description: "Software Lab demo brand — not La Tortilla Grocery production." } },
      { element: "[data-demo-tour='admin']", popover: { title: "Dashboard", description: "Start here for KPIs and operational overview." } },
      { element: "[data-demo-tour='products']", popover: { title: "Products", description: "Browse and edit the fictitious catalog." } },
      { element: "[data-demo-tour='orders']", popover: { title: "Orders", description: "Orders move through back office, picking, invoice and delivery." } },
      { element: "[data-demo-tour='picking']", popover: { title: "Picking", description: "Selectors prepare verified warehouse tickets." } },
      { element: "[data-demo-tour='invoices']", popover: { title: "Invoices", description: "Billing documents with demo INV-DEMO numbers." } },
      { element: "[data-demo-tour='drivers']", popover: { title: "Drivers", description: "Route deliveries and customer pickup states." } },
      { element: "[data-demo-tour='quickbooks']", popover: { title: "QuickBooks", description: "Mock sync — realistic UI, no external Intuit calls." } },
      { element: "[data-demo-tour='reports']", popover: { title: "Reports", description: "Operational reports on showcase data." } },
      { element: "[data-demo-tour='reset']", popover: { title: "Reset Demo", description: "Restore the film-ready dataset after exploring." } }
    ].filter(function (step) {
      return document.querySelector(step.element);
    });

    if (!steps.length) return;

    startBtn.addEventListener("click", function () {
      const tour = DriverFactory({
        showProgress: true,
        steps: steps,
        nextBtnText: "Next",
        prevBtnText: "Back",
        doneBtnText: "Finish"
      });
      tour.drive();
    });
  });
})();
