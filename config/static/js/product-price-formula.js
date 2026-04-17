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

  const formatMargin = (margin) => {
    if (!Number.isFinite(margin)) {
      return "";
    }

    return margin.toFixed(2).replace(/\.00$/, "").replace(/(\.\d*[1-9])0$/, "$1");
  };

  document.querySelectorAll("[data-price-formula]").forEach((scope) => {
    const staticMargins = (scope.dataset.priceMargins || "")
      .split(",")
      .map((value) => Number.parseFloat(value))
      .filter((value) => Number.isFinite(value));

    const marginInputs = Array.from(scope.querySelectorAll('input[name^="porcentaje_"]'));
    const costInput = scope.querySelector("[data-cost-input]");
    if (!costInput) {
      return;
    }

    const priceOutputs = Array.from({ length: 5 }, (_value, index) => {
      const output = scope.querySelector(`[data-price-output="${index + 1}"]`);
      return { index, output };
    });

    const priceLabels = Array.from({ length: 5 }, (_value, index) => {
      const label = scope.querySelector(`[data-price-label="${index + 1}"]`);
      return { index, label };
    });

    const getActiveMargins = () => {
      if (marginInputs.length === 5) {
        return marginInputs.map((input) => Number.parseFloat((input.value || "").replace(",", ".")));
      }
      return staticMargins.length === 5 ? staticMargins : defaultMargins;
    };

    const syncLabels = () => {
      const activeMargins = getActiveMargins();
      priceLabels.forEach(({ index, label }) => {
        if (!label) {
          return;
        }

        const baseLabel = label.dataset.priceLabelBase || label.textContent;
        const margin = activeMargins[index];
        const suffix = Number.isFinite(margin) ? ` (${formatMargin(margin)}%)` : "";
        label.textContent = `${baseLabel}${suffix}`;
      });
    };

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
      const activeMargins = getActiveMargins();
      priceOutputs.forEach(({ index, output }) => {
        if (output) {
          const margin = activeMargins[index];
          output.value = calculatePrice(cost, margin);
        }
      });
    };

    syncLabels();
    syncPrices({ initial: true });
    costInput.addEventListener("input", () => syncPrices());
    marginInputs.forEach((input) => {
      input.addEventListener("input", () => {
        syncLabels();
        syncPrices();
      });
    });
  });
});