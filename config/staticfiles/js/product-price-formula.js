(function () {
  function parseNumber(value) {
    if (value === null || value === undefined || value === '') {
      return null;
    }

    var normalized = String(value).replace(',', '.').trim();
    var parsed = Number.parseFloat(normalized);
    return Number.isFinite(parsed) ? parsed : null;
  }

  function formatPrice(value) {
    return value.toFixed(2);
  }

  function calculatePrice(cost, margin) {
    if (!Number.isFinite(cost) || !Number.isFinite(margin)) {
      return '';
    }

    var divisor = 1 - (margin / 100);
    if (divisor <= 0) {
      return '';
    }

    return formatPrice(cost / divisor);
  }

  function getMarginInputs(container) {
    return [1, 2, 3, 4, 5].map(function (index) {
      return container.querySelector('[name="porcentaje_' + index + '"]');
    });
  }

  function getMargins(container) {
    var marginInputs = getMarginInputs(container);
    var hasEditableMargins = marginInputs.some(Boolean);

    if (hasEditableMargins) {
      return marginInputs.map(function (input) {
        return input ? parseNumber(input.value) : null;
      });
    }

    var rawMargins = (container.getAttribute('data-price-margins') || '').split(',');
    return rawMargins.map(parseNumber);
  }

  function updateLabels(container, margins) {
    container.querySelectorAll('[data-price-label]').forEach(function (label) {
      var index = Number.parseInt(label.getAttribute('data-price-label'), 10) - 1;
      var baseLabel = label.getAttribute('data-price-label-base') || label.textContent;
      var margin = margins[index];

      if (Number.isFinite(margin)) {
        label.textContent = baseLabel + ' (' + formatPrice(margin) + '%)';
      } else {
        label.textContent = baseLabel;
      }
    });
  }

  function updateOutputs(container) {
    var margins = getMargins(container);
    updateLabels(container, margins);

    var costInput = container.querySelector('[data-cost-input]');
    var cost = costInput ? parseNumber(costInput.value) : null;

    container.querySelectorAll('[data-price-output]').forEach(function (output) {
      var index = Number.parseInt(output.getAttribute('data-price-output'), 10) - 1;
      var margin = margins[index];
      output.value = calculatePrice(cost, margin);
    });
  }

  function bindContainer(container) {
    if (!container || container.dataset.priceFormulaBound === '1') {
      return;
    }
    container.dataset.priceFormulaBound = '1';

    var costInput = container.querySelector('[data-cost-input]');
    if (costInput) {
      costInput.addEventListener('input', function () {
        updateOutputs(container);
      });
    }

    getMarginInputs(container).forEach(function (input) {
      if (!input) {
        return;
      }

      input.addEventListener('input', function () {
        updateOutputs(container);
      });
    });

    updateOutputs(container);
  }

  window.ProductPriceFormula = {
    initContainer: bindContainer,
  };

  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('[data-price-formula]').forEach(bindContainer);
  });
})();
