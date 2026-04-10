document.addEventListener("DOMContentLoaded", () => {
  const defaultMargins = [10, 20, 30, 40, 50];

  const calculatePrice = (cost, margin) => {
    if (!Number.isFinite(cost) || cost < 0) {
      return "";
    }

    const divisor = 1 - margin / 100;
    if (divisor <= 0) {
      return "";
    }

    return (cost / divisor).toFixed(2);
  };

  document.querySelectorAll("[data-price-formula]").forEach((scope) => {
    const margins = (scope.dataset.priceMargins || "")
      .split(",")
      .map((value) => Number.parseFloat(value))
      .filter((value) => Number.isFinite(value));

    const activeMargins = margins.length === 5 ? margins : defaultMargins;
    const costInput = scope.querySelector("[data-cost-input]");
    if (!costInput) {
      return;
    }

    const priceOutputs = activeMargins.map((margin, index) => {
      const output = scope.querySelector(`[data-price-output="${index + 1}"]`);
      return { margin, output };
    });

    const syncPrices = ({ initial = false } = {}) => {
      const rawValue = (costInput.value || "").trim().replace(",", ".");
      if (!rawValue) {
        if (!initial) {
          priceOutputs.forEach(({ output }) => {
            if (output) {
              output.value = "";
            }
          });
        }
        return;
      }

      const cost = Number.parseFloat(rawValue);
      priceOutputs.forEach(({ margin, output }) => {
        if (output) {
          output.value = calculatePrice(cost, margin);
        }
      });
    };

    syncPrices({ initial: true });
    costInput.addEventListener("input", () => syncPrices());
  });
});