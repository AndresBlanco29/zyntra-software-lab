(function () {
  'use strict';

  function getSelectedPresentationOption(presentationSelect) {
    if (!presentationSelect || !presentationSelect.value) {
      return null;
    }
    var selectedValue = presentationSelect.value;
    return Array.prototype.find.call(presentationSelect.options, function (option) {
      return option.value === selectedValue;
    }) || null;
  }

  function readPresentationPrice(selectedOption, tier) {
    if (!selectedOption) {
      return null;
    }
    var rawValue = selectedOption.getAttribute('data-price-' + tier);
    if (rawValue === null || rawValue === undefined) {
      return null;
    }
    var normalized = String(rawValue).trim().replace(',', '.');
    if (!normalized) {
      return null;
    }
    var parsed = Number(normalized);
    if (Number.isNaN(parsed) || parsed <= 0) {
      return null;
    }
    return normalized;
  }

  function destroyPriceSelectEnhancement(priceSelect) {
    if (window.LTGSearchableSelect && typeof window.LTGSearchableSelect.destroy === 'function') {
      window.LTGSearchableSelect.destroy(priceSelect);
    }
  }

  function initDirectInvoiceForm() {
    var deliveryMethod = document.getElementById('directInvoiceDeliveryMethod');
    var driverField = document.getElementById('directInvoiceDriverField');
    var estimatedDeliveryField = document.getElementById('directInvoiceEstimatedDeliveryField');
    var driverSelect = document.getElementById('directInvoiceDriverSelect');
    var submitButton = document.getElementById('directInvoiceSubmitButton');
    var linesContainer = document.getElementById('direct-invoice-lines');
    var addLineButton = document.getElementById('direct-invoice-add-line');
    var lineTemplate = document.getElementById('direct-invoice-line-template');

    if (!linesContainer || !addLineButton || !lineTemplate) {
      return;
    }

    var createDirectInvoiceLabel = linesContainer.dataset.createDirectInvoiceLabel || 'Create direct invoice';
    var generateInvoiceLabel = linesContainer.dataset.generateInvoiceLabel || 'Generate invoice';
    var selectPriceLabel = linesContainer.dataset.selectPriceLabel || 'Select a price';
    var noPricesLabel = linesContainer.dataset.noPricesLabel || 'This product has no configured prices.';
    var priceLabel = linesContainer.dataset.priceLabel || 'Price';

    function syncDirectInvoiceDeliveryFields() {
      if (!deliveryMethod) {
        return;
      }
      var isRoute = deliveryMethod.value === 'RUTA_DRIVER';
      if (driverField) {
        driverField.hidden = !isRoute;
      }
      if (estimatedDeliveryField) {
        estimatedDeliveryField.hidden = !isRoute;
      }
      if (driverSelect) {
        driverSelect.required = isRoute;
        if (!isRoute) {
          driverSelect.value = '';
        }
      }
      var estimatedInput = estimatedDeliveryField && estimatedDeliveryField.querySelector('input[name="estimated_delivery_at"]');
      if (estimatedInput && !isRoute) {
        estimatedInput.value = '';
      }
      if (submitButton) {
        submitButton.textContent = isRoute ? generateInvoiceLabel : createDirectInvoiceLabel;
      }
    }

    if (deliveryMethod) {
      deliveryMethod.addEventListener('change', syncDirectInvoiceDeliveryFields);
      syncDirectInvoiceDeliveryFields();
    }

    function buildPriceOptions(lineElement, preferDefaultTier) {
      var presentationSelect = lineElement.querySelector('.direct-invoice-presentation-select');
      var priceSelect = lineElement.querySelector('.direct-invoice-price-select');
      if (!presentationSelect || !priceSelect) {
        return;
      }

      var selectedOption = getSelectedPresentationOption(presentationSelect);
      var selectedValue = preferDefaultTier ? '' : (priceSelect.dataset.selectedValue || priceSelect.value || '');
      var defaultTier = String(priceSelect.dataset.defaultTier || '1');

      destroyPriceSelectEnhancement(priceSelect);
      priceSelect.innerHTML = '';

      var placeholderOption = document.createElement('option');
      placeholderOption.value = '';
      placeholderOption.textContent = selectPriceLabel;
      priceSelect.appendChild(placeholderOption);

      if (!selectedOption) {
        priceSelect.disabled = true;
        priceSelect.dataset.selectedValue = '';
        return;
      }

      var availablePrices = [];
      for (var tier = 1; tier <= 5; tier += 1) {
        var priceValue = readPresentationPrice(selectedOption, tier);
        if (!priceValue) {
          continue;
        }
        availablePrices.push({ tier: String(tier), value: priceValue });
      }

      if (!availablePrices.length) {
        placeholderOption.textContent = noPricesLabel;
        priceSelect.disabled = true;
        priceSelect.dataset.selectedValue = '';
        return;
      }

      priceSelect.disabled = false;
      var matchedOption = null;
      var defaultOption = null;

      availablePrices.forEach(function (priceEntry, index) {
        var option = document.createElement('option');
        option.value = priceEntry.value;
        option.dataset.tier = priceEntry.tier;
        option.textContent = priceLabel + ' ' + priceEntry.tier + ' - $' + priceEntry.value;
        if (priceEntry.value === selectedValue) {
          option.selected = true;
          matchedOption = option;
        }
        if (priceEntry.tier === defaultTier) {
          defaultOption = option;
        }
        if (index === 0 && !defaultOption) {
          defaultOption = option;
        }
        priceSelect.appendChild(option);
      });

      if (!matchedOption && defaultOption) {
        defaultOption.selected = true;
      }

      priceSelect.dataset.selectedValue = '';
    }

    function updateRemoveButtons() {
      var lineElements = linesContainer.querySelectorAll('.direct-invoice-line');
      lineElements.forEach(function (lineElement) {
        var removeButton = lineElement.querySelector('.direct-invoice-remove-line');
        if (!removeButton) {
          return;
        }
        removeButton.disabled = lineElements.length === 1;
      });
    }

    function bindPresentationChange(lineElement) {
      var presentationSelect = lineElement.querySelector('.direct-invoice-presentation-select');
      if (!presentationSelect || presentationSelect.dataset.directInvoiceChangeBound === 'true') {
        return;
      }
      presentationSelect.dataset.directInvoiceChangeBound = 'true';

      function handlePresentationChange() {
        var priceSelect = lineElement.querySelector('.direct-invoice-price-select');
        if (priceSelect) {
          priceSelect.dataset.selectedValue = '';
        }
        buildPriceOptions(lineElement, true);
      }

      var tomSelectInstance = typeof TomSelect !== 'undefined' ? TomSelect.getInstance(presentationSelect) : null;
      if (tomSelectInstance) {
        tomSelectInstance.on('change', handlePresentationChange);
      } else {
        presentationSelect.addEventListener('change', handlePresentationChange);
      }
    }

    function setupLine(lineElement) {
      var presentationSelect = lineElement.querySelector('.direct-invoice-presentation-select');
      var removeButton = lineElement.querySelector('.direct-invoice-remove-line');

      if (window.LTGSearchableSelect && presentationSelect) {
        window.LTGSearchableSelect.init(presentationSelect);
      }

      buildPriceOptions(lineElement, false);
      bindPresentationChange(lineElement);

      if (!removeButton) {
        return;
      }

      removeButton.addEventListener('click', function () {
        var lineElements = linesContainer.querySelectorAll('.direct-invoice-line');
        if (lineElements.length === 1) {
          if (presentationSelect) {
            presentationSelect.value = '';
            if (typeof TomSelect !== 'undefined') {
              var tomSelectInstance = TomSelect.getInstance(presentationSelect);
              if (tomSelectInstance) {
                tomSelectInstance.clear(true);
              }
            }
          }
          var quantityInput = lineElement.querySelector('input[name="cantidad"]');
          if (quantityInput) {
            quantityInput.value = '';
          }
          var priceSelect = lineElement.querySelector('.direct-invoice-price-select');
          if (priceSelect) {
            priceSelect.dataset.selectedValue = '';
          }
          buildPriceOptions(lineElement, false);
          return;
        }
        lineElement.remove();
        updateRemoveButtons();
      });
    }

    linesContainer.querySelectorAll('.direct-invoice-line').forEach(setupLine);
    updateRemoveButtons();

    addLineButton.addEventListener('click', function () {
      var fragment = lineTemplate.content.cloneNode(true);
      linesContainer.appendChild(fragment);
      setupLine(linesContainer.lastElementChild);
      updateRemoveButtons();
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    if (window.LTGSearchableSelect && typeof window.LTGSearchableSelect.initAll === 'function') {
      window.LTGSearchableSelect.initAll(document);
    }
    initDirectInvoiceForm();
  });
})();
