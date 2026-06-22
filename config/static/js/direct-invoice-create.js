(function () {
  'use strict';

  function parsePriceValue(rawValue) {
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

  function readPresentationOptionsFromPage() {
    var optionsElement = document.getElementById('direct-invoice-presentation-options');
    if (!optionsElement || !optionsElement.textContent) {
      return [];
    }
    try {
      var parsed = JSON.parse(optionsElement.textContent);
      return Array.isArray(parsed) ? parsed : [];
    } catch (error) {
      return [];
    }
  }

  function getSelectedPresentationOption(presentationSelect) {
    if (!presentationSelect || !presentationSelect.value) {
      return null;
    }
    var selectedValue = presentationSelect.value;
    return Array.prototype.find.call(presentationSelect.options, function (option) {
      return option.value === selectedValue;
    }) || null;
  }

  function getPresentationPriceData(presentationSelect, presentationOptionsByValue) {
    if (!presentationSelect || !presentationSelect.value) {
      return null;
    }

    if (typeof TomSelect !== 'undefined') {
      var instance = TomSelect.getInstance(presentationSelect);
      if (instance && instance.options && instance.options[presentationSelect.value]) {
        return instance.options[presentationSelect.value];
      }
    }

    if (presentationOptionsByValue && presentationOptionsByValue[presentationSelect.value]) {
      return presentationOptionsByValue[presentationSelect.value];
    }

    var selectedOption = getSelectedPresentationOption(presentationSelect);
    if (!selectedOption) {
      return null;
    }

    var data = {
      value: selectedOption.value,
      text: selectedOption.textContent,
    };
    for (var tier = 1; tier <= 5; tier += 1) {
      data['price_' + tier] = selectedOption.getAttribute('data-price-' + tier);
    }
    return data;
  }

  function readPresentationPriceFromData(priceData, tier) {
    if (!priceData) {
      return null;
    }
    return parsePriceValue(priceData['price_' + tier]);
  }

  function destroyPresentationSelectEnhancement(presentationSelect) {
    if (typeof TomSelect === 'undefined') {
      return;
    }
    var instance = TomSelect.getInstance(presentationSelect);
    if (instance) {
      instance.destroy();
    }
    delete presentationSelect.dataset.directInvoiceSelectInitialized;
  }

  function destroyPriceSelectEnhancement(priceSelect) {
    if (window.LTGSearchableSelect && typeof window.LTGSearchableSelect.destroy === 'function') {
      window.LTGSearchableSelect.destroy(priceSelect);
    }
  }

  function initPresentationSelect(presentationSelect, linesContainer, presentationOptions, onSelectionChange) {
    if (!presentationSelect || typeof TomSelect === 'undefined') {
      return null;
    }
    if (presentationSelect.dataset.directInvoiceSelectInitialized === 'true') {
      return TomSelect.getInstance(presentationSelect);
    }

    var existingInstance = TomSelect.getInstance(presentationSelect);
    if (existingInstance) {
      existingInstance.destroy();
    }

    var i18n = window.LTG_SEARCHABLE_SELECT_I18N || {};
    var typeToSearchLabel = linesContainer.dataset.typeToSearchLabel || i18n.placeholder || 'Type to search products...';
    var noResultsLabel = i18n.noResults || 'No results found';
    var selectedValue = presentationSelect.value || '';

    var instance = new TomSelect(presentationSelect, {
      valueField: 'value',
      labelField: 'text',
      searchField: ['text'],
      options: presentationOptions,
      allowEmptyOption: true,
      create: false,
      maxOptions: null,
      openOnFocus: true,
      dropdownParent: 'body',
      placeholder: typeToSearchLabel,
      render: {
        no_results: function () {
          return '<div class="no-results px-3 py-2 text-muted">' + noResultsLabel + '</div>';
        },
        option: function (item, escape) {
          return '<div>' + escape(item.text) + '</div>';
        },
      },
      onChange: function () {
        if (typeof onSelectionChange === 'function') {
          onSelectionChange();
        }
      },
    });

    if (instance.wrapper) {
      instance.wrapper.classList.add('form-select');
    }

    if (selectedValue) {
      instance.setValue(selectedValue, true);
    }

    presentationSelect.dataset.directInvoiceSelectInitialized = 'true';
    return instance;
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
    var presentationOptions = readPresentationOptionsFromPage();
    var presentationOptionsByValue = {};

    presentationOptions.forEach(function (option) {
      if (option && option.value) {
        presentationOptionsByValue[String(option.value)] = option;
      }
    });

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

      var priceData = getPresentationPriceData(presentationSelect, presentationOptionsByValue);
      var selectedValue = preferDefaultTier ? '' : (priceSelect.dataset.selectedValue || priceSelect.value || '');
      var defaultTier = String(priceSelect.dataset.defaultTier || '1');

      destroyPriceSelectEnhancement(priceSelect);
      priceSelect.innerHTML = '';

      var placeholderOption = document.createElement('option');
      placeholderOption.value = '';
      placeholderOption.textContent = selectPriceLabel;
      priceSelect.appendChild(placeholderOption);

      if (!priceData) {
        priceSelect.disabled = true;
        priceSelect.dataset.selectedValue = '';
        return;
      }

      var availablePrices = [];
      for (var tier = 1; tier <= 5; tier += 1) {
        var priceValue = readPresentationPriceFromData(priceData, tier);
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

    function setupLine(lineElement) {
      var presentationSelect = lineElement.querySelector('.direct-invoice-presentation-select');
      var removeButton = lineElement.querySelector('.direct-invoice-remove-line');

      function handlePresentationChange() {
        var priceSelect = lineElement.querySelector('.direct-invoice-price-select');
        if (priceSelect) {
          priceSelect.dataset.selectedValue = '';
        }
        buildPriceOptions(lineElement, true);
      }

      initPresentationSelect(presentationSelect, linesContainer, presentationOptions, handlePresentationChange);
      buildPriceOptions(lineElement, false);

      if (!removeButton) {
        return;
      }

      removeButton.addEventListener('click', function () {
        var lineElements = linesContainer.querySelectorAll('.direct-invoice-line');
        if (lineElements.length === 1) {
          if (presentationSelect) {
            destroyPresentationSelectEnhancement(presentationSelect);
            presentationSelect.innerHTML = '';
            initPresentationSelect(presentationSelect, linesContainer, presentationOptions, handlePresentationChange);
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
    initDirectInvoiceForm();
  });
})();
