(function () {
  'use strict';

  function escapeHtml(text) {
    return String(text)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function loadPresentations() {
    var node = document.getElementById('pickerAvailablePresentationsJson');
    if (!node) {
      return [];
    }
    try {
      return JSON.parse(node.textContent || '[]');
    } catch (error) {
      return [];
    }
  }

  document.addEventListener('DOMContentLoaded', function () {
    var form = document.getElementById('pickerVerificationForm');
    if (!form) {
      return;
    }

    var config = window.LTG_PICKER_PRODUCT_SEARCH || {};
    var lockBadge = document.getElementById('pickerLockBadge');
    var noteInput = document.getElementById('pickerNoteInput');
    var noteHelp = document.getElementById('pickerNoteHelp');
    var stockAlert = document.getElementById('pickerStockAlert');
    var approvedCheckbox = document.getElementById('notaResuelta');
    var addProductRowButton = document.getElementById('addProductRowButton');
    var pickerItemsTableBody = document.getElementById('pickerItemsTableBody');
    var reviewValidationMessage = config.reviewValidationMessage || 'Check every product line before saving.';
    var stockAvailableLabelPrefix = config.stockAvailableLabelPrefix || 'Available stock:';
    var searchPlaceholder = config.searchPlaceholder || 'Search products by full name...';
    var searchLabel = config.searchLabel || 'Search product';
    var minCharsMessage = config.minCharsMessage || 'Type at least 2 characters to search.';
    var emptyMessage = config.emptyMessage || 'No products found.';
    var stockDashLabel = config.stockDashLabel || 'Available stock: - CS';
    var labelBlocked = config.labelBlocked || 'Blocked';
    var labelUnlocked = config.labelUnlocked || 'Unlocked';
    var labelNew = config.labelNew || 'New';
    var labelDecrease = config.labelDecrease || 'Decrease real quantity';
    var labelIncrease = config.labelIncrease || 'Increase real quantity';
    var labelRemove = config.labelRemove || 'Remove';
    var labelReviewed = config.labelReviewed || 'I reviewed this added product';
    var labelProduct = config.labelProduct || 'Product';
    var labelReviewedCol = config.labelReviewedCol || 'Reviewed';
    var availablePresentations = loadPresentations();

    function findPresentationById(presentationId) {
      var id = String(presentationId || '');
      return availablePresentations.find(function (item) {
        return String(item.id) === id;
      }) || null;
    }

    function formatAvailableStockLabel(amount) {
      return stockAvailableLabelPrefix + ' ' + amount + ' CS';
    }

    function applyStockLabelStyles(label, amount) {
      if (!label) {
        return;
      }
      label.textContent = formatAvailableStockLabel(amount);
      label.classList.remove('text-success', 'text-danger', 'text-muted', 'fw-semibold');
      if (amount <= 0) {
        label.classList.add('text-danger', 'fw-semibold');
      } else {
        label.classList.add('text-success', 'fw-semibold');
      }
    }

    function selectedOptionStock(select) {
      if (!select || !select.value) {
        return null;
      }
      if (select.tagName === 'SELECT') {
        var selectedOption = Array.from(select.options).find(function (option) {
          return option.value === select.value;
        });
        return selectedOption ? Number(selectedOption.dataset.stockPhysical || 0) : null;
      }
      var presentation = findPresentationById(select.value);
      return presentation ? Number(presentation.stock_physical || 0) : null;
    }

    function syncOrderLinePresentationStock(row) {
      var select = row.querySelector('[data-picker-presentation-select]');
      var stepper = row.querySelector('[data-quantity-stepper]');
      var stockLabel = row.querySelector('[data-picker-stock-label]');
      var stockPhysical = selectedOptionStock(select);
      if (stockPhysical === null) {
        return;
      }
      if (stepper) {
        stepper.dataset.stockPhysical = String(stockPhysical);
      }
      applyStockLabelStyles(stockLabel, stockPhysical);
    }

    function getRequiredReviewCheckboxes() {
      var checkboxes = Array.from(document.querySelectorAll('[data-picker-order-line] .picker-line-reviewed'));
      document.querySelectorAll('[data-added-product-row]').forEach(function (row) {
        var select = row.querySelector('[data-added-product-select]');
        var reviewCheckbox = row.querySelector('.picker-line-reviewed');
        if (select && select.value && reviewCheckbox) {
          checkboxes.push(reviewCheckbox);
        }
      });
      return checkboxes;
    }

    function syncReviewRowHighlights() {
      getRequiredReviewCheckboxes().forEach(function (checkbox) {
        var row = checkbox.closest('tr');
        if (!row) {
          return;
        }
        row.classList.toggle('table-warning', !checkbox.checked);
      });
    }

    function syncAddedProductRow(row) {
      var select = row.querySelector('[data-added-product-select]');
      var stepper = row.querySelector('[data-quantity-stepper]');
      var stockLabel = row.querySelector('[data-picker-stock-label]');
      if (!select || !stepper) {
        return;
      }

      var stockPhysical = selectedOptionStock(select);
      if (stockPhysical === null) {
        stepper.dataset.stockPhysical = '0';
        if (stockLabel) {
          stockLabel.textContent = stockDashLabel;
          stockLabel.classList.remove('text-success', 'text-danger', 'fw-semibold');
          stockLabel.classList.add('text-muted');
        }
        return;
      }

      stepper.dataset.stockPhysical = String(stockPhysical);
      applyStockLabelStyles(stockLabel, stockPhysical);
    }

    function hideProductResults(resultsBox) {
      if (!resultsBox) {
        return;
      }
      resultsBox.hidden = true;
      resultsBox.innerHTML = '';
      resultsBox.classList.remove('picker-product-search-results--floating');
      resultsBox.style.top = '';
      resultsBox.style.left = '';
      resultsBox.style.width = '';
      resultsBox.style.maxHeight = '';
      if (resultsBox.dataset.floated === 'true' && resultsBox.parentElement === document.body) {
        var ownerId = resultsBox.dataset.ownerRootId;
        var ownerRoot = ownerId ? document.getElementById(ownerId) : null;
        var host = ownerRoot ? ownerRoot.querySelector('.position-relative') : null;
        if (host) {
          host.appendChild(resultsBox);
        }
        delete resultsBox.dataset.floated;
      }
    }

    function positionFloatingResults(searchInput, resultsBox) {
      if (!searchInput || !resultsBox || resultsBox.hidden) {
        return;
      }
      var rect = searchInput.getBoundingClientRect();
      var viewportPadding = 12;
      var availableBelow = window.innerHeight - rect.bottom - viewportPadding;
      var availableAbove = rect.top - viewportPadding;
      var maxHeight = Math.min(280, Math.max(availableBelow, availableAbove, 140));
      var openUpward = availableBelow < 160 && availableAbove > availableBelow;
      var top = openUpward
        ? Math.max(viewportPadding, rect.top - maxHeight - 6)
        : Math.min(window.innerHeight - viewportPadding - 40, rect.bottom + 6);

      resultsBox.classList.add('picker-product-search-results--floating');
      resultsBox.style.position = 'fixed';
      resultsBox.style.left = Math.max(viewportPadding, Math.round(rect.left)) + 'px';
      resultsBox.style.width = Math.round(Math.min(rect.width, window.innerWidth - (viewportPadding * 2))) + 'px';
      resultsBox.style.top = Math.round(top) + 'px';
      resultsBox.style.maxHeight = Math.round(maxHeight) + 'px';
      resultsBox.style.zIndex = '4000';
      resultsBox.style.overflowY = 'auto';
    }

    function ensureFloatingResults(root, resultsBox, searchInput) {
      if (!root.id) {
        root.id = 'pickerProductSearchRoot-' + String(Date.now()) + '-' + String(Math.floor(Math.random() * 1000));
      }
      resultsBox.dataset.ownerRootId = root.id;
      if (resultsBox.parentElement !== document.body) {
        document.body.appendChild(resultsBox);
        resultsBox.dataset.floated = 'true';
      }
      positionFloatingResults(searchInput, resultsBox);
    }

    function filterPresentations(query) {
      var tokens = String(query || '').trim().toLowerCase().split(/\s+/).filter(Boolean);
      if (!tokens.length) {
        return [];
      }
      return availablePresentations.filter(function (item) {
        var label = String(item.label || '').toLowerCase();
        return tokens.every(function (token) {
          return label.indexOf(token) >= 0;
        });
      }).slice(0, 40);
    }

    function renderProductResults(root, query) {
      var resultsBox = root.querySelector('[data-picker-product-results]')
        || document.querySelector('[data-picker-product-results][data-owner-root-id="' + root.id + '"]');
      var searchInput = root.querySelector('[data-picker-product-search]');
      if (!resultsBox || !searchInput) {
        return;
      }

      var trimmed = String(query || '').trim();
      if (trimmed.length < 2) {
        resultsBox.innerHTML = '<div class="list-group-item text-muted small">' + escapeHtml(minCharsMessage) + '</div>';
        resultsBox.hidden = false;
        ensureFloatingResults(root, resultsBox, searchInput);
        return;
      }

      var matches = filterPresentations(trimmed);
      if (!matches.length) {
        resultsBox.innerHTML = '<div class="list-group-item text-muted small">' + escapeHtml(emptyMessage) + '</div>';
        resultsBox.hidden = false;
        ensureFloatingResults(root, resultsBox, searchInput);
        return;
      }

      resultsBox.innerHTML = matches.map(function (item) {
        return '' +
          '<button type="button" class="list-group-item list-group-item-action product-search-result" ' +
            'data-presentation-id="' + escapeHtml(item.id) + '" ' +
            'data-presentation-label="' + escapeHtml(item.label) + '">' +
            '<div class="fw-semibold">' + escapeHtml(item.label) + '</div>' +
            '<div class="small text-muted">' + escapeHtml(formatAvailableStockLabel(item.stock_physical || 0)) + '</div>' +
          '</button>';
      }).join('');
      resultsBox.hidden = false;
      ensureFloatingResults(root, resultsBox, searchInput);
    }

    function selectPresentation(root, presentationId, presentationLabel) {
      var searchInput = root.querySelector('[data-picker-product-search]');
      var hiddenInput = root.querySelector('[data-added-product-select]');
      var resultsBox = root.querySelector('[data-picker-product-results]')
        || document.querySelector('[data-picker-product-results][data-owner-root-id="' + root.id + '"]');
      if (!hiddenInput) {
        return;
      }
      hiddenInput.value = presentationId || '';
      if (searchInput) {
        searchInput.value = presentationLabel || '';
      }
      hideProductResults(resultsBox);
      hiddenInput.dispatchEvent(new Event('change', { bubbles: true }));
    }

    function initProductSearchRoot(root) {
      if (!root || root.dataset.searchReady === 'true') {
        return;
      }

      var searchInput = root.querySelector('[data-picker-product-search]');
      var hiddenInput = root.querySelector('[data-added-product-select]');
      var resultsBox = root.querySelector('[data-picker-product-results]');
      if (!searchInput || !hiddenInput || !resultsBox) {
        return;
      }

      var debounceTimer = null;

      function repositionIfOpen() {
        if (!resultsBox.hidden) {
          positionFloatingResults(searchInput, resultsBox);
        }
      }

      searchInput.addEventListener('input', function () {
        if (hiddenInput.value) {
          hiddenInput.value = '';
          hiddenInput.dispatchEvent(new Event('change', { bubbles: true }));
        }
        window.clearTimeout(debounceTimer);
        debounceTimer = window.setTimeout(function () {
          renderProductResults(root, searchInput.value);
        }, 180);
      });

      searchInput.addEventListener('focus', function () {
        if (String(searchInput.value || '').trim().length >= 2) {
          renderProductResults(root, searchInput.value);
        }
      });

      resultsBox.addEventListener('click', function (event) {
        var button = event.target.closest('[data-presentation-id]');
        if (!button) {
          return;
        }
        selectPresentation(root, button.dataset.presentationId, button.dataset.presentationLabel);
      });

      document.addEventListener('click', function (event) {
        if (root.contains(event.target) || resultsBox.contains(event.target)) {
          return;
        }
        hideProductResults(resultsBox);
      });

      window.addEventListener('resize', repositionIfOpen);
      window.addEventListener('scroll', repositionIfOpen, true);

      root.dataset.searchReady = 'true';
    }

    function initQuantityStepper(stepper) {
      if (!stepper || stepper.dataset.stepperReady === 'true') {
        return;
      }

      var input = stepper.querySelector('.quantity-stepper__input');
      stepper.querySelectorAll('[data-step]').forEach(function (button) {
        button.addEventListener('click', function () {
          var currentValue = Number(input.value || 0);
          var minValue = Number(input.min || 0);
          var nextValue = button.dataset.step === 'down' ? currentValue - 1 : currentValue + 1;

          input.value = Math.max(minValue, nextValue);
          input.dispatchEvent(new Event('input', { bubbles: true }));
          input.dispatchEvent(new Event('change', { bubbles: true }));
        });
      });

      input.addEventListener('input', updateFormState);
      input.addEventListener('change', updateFormState);
      stepper.dataset.stepperReady = 'true';
    }

    function buildAddedProductRow() {
      var row = document.createElement('tr');
      row.setAttribute('data-added-product-row', 'true');
      row.innerHTML = '' +
        '<td data-label="' + escapeHtml(labelProduct) + '">' +
          '<div class="picker-product-search" data-picker-product-search-root>' +
            '<label class="form-label small fw-semibold mb-1">' + escapeHtml(searchLabel) + '</label>' +
            '<div class="position-relative">' +
              '<input type="search" class="form-control form-control-sm" data-picker-product-search placeholder="' + escapeHtml(searchPlaceholder) + '" autocomplete="off">' +
              '<input type="hidden" name="presentacion_nueva[]" data-added-product-select value="">' +
              '<div class="list-group product-search-results shadow-sm" data-picker-product-results hidden></div>' +
            '</div>' +
          '</div>' +
          '<div class="small mt-1 picker-stock-label text-muted" data-picker-stock-label>' + escapeHtml(stockDashLabel) + '</div>' +
        '</td>' +
        '<td data-label="U/M"><span class="badge text-bg-danger">' + escapeHtml(labelNew) + '</span></td>' +
        '<td data-label="QTY ORD">-</td>' +
        '<td data-label="QTY PICK" class="mobile-stack-table__input-cell">' +
          '<div class="d-flex gap-2 align-items-center">' +
            '<div class="input-group quantity-stepper flex-grow-1" data-quantity-stepper data-stock-physical="0" data-applied-quantity="0">' +
              '<button type="button" class="btn btn-outline-secondary quantity-stepper__button" data-step="down" aria-label="' + escapeHtml(labelDecrease) + '">-</button>' +
              '<input type="number" min="0" class="form-control quantity-stepper__input" name="cantidad_nueva[]" value="0">' +
              '<button type="button" class="btn btn-outline-secondary quantity-stepper__button" data-step="up" aria-label="' + escapeHtml(labelIncrease) + '">+</button>' +
            '</div>' +
            '<button type="button" class="btn btn-outline-danger btn-sm" data-remove-product-row>' + escapeHtml(labelRemove) + '</button>' +
          '</div>' +
        '</td>' +
        '<td data-label="' + escapeHtml(labelReviewedCol) + '" class="text-center">' +
          '<div class="form-check d-inline-flex justify-content-center mb-0">' +
            '<input type="checkbox" class="form-check-input picker-line-reviewed" name="linea_revisada_adicional[]" value="on" aria-label="' + escapeHtml(labelReviewed) + '">' +
          '</div>' +
        '</td>';
      return row;
    }

    function validateRequiredLineReviews() {
      var missing = getRequiredReviewCheckboxes().filter(function (checkbox) {
        return !checkbox.checked;
      });
      syncReviewRowHighlights();
      if (missing.length) {
        window.alert(reviewValidationMessage);
        if (missing[0]) {
          missing[0].focus();
        }
        return false;
      }
      return true;
    }

    function updateDispatchSummary() {
      var orderedTotal = 0;
      var dispatchedTotal = 0;
      var notSentTotal = 0;

      document.querySelectorAll('[data-picker-order-line]').forEach(function (row) {
        var orderedCell = row.querySelector('[data-requested-quantity]');
        var pickInput = row.querySelector('.quantity-stepper__input');
        var ordered = Number(orderedCell ? orderedCell.dataset.requestedQuantity : 0) || 0;
        var picked = Number(pickInput ? pickInput.value : 0) || 0;

        orderedTotal += ordered;
        dispatchedTotal += picked;
        notSentTotal += Math.max(ordered - picked, 0);
      });

      document.querySelectorAll('[data-added-product-row]').forEach(function (row) {
        var select = row.querySelector('[data-added-product-select]');
        var pickInput = row.querySelector('.quantity-stepper__input');
        if (!select || !select.value || !pickInput) {
          return;
        }
        dispatchedTotal += Number(pickInput.value || 0) || 0;
      });

      document.getElementById('pickerSummaryOrdered').textContent = orderedTotal;
      document.getElementById('pickerSummaryDispatched').textContent = dispatchedTotal;
      document.getElementById('pickerSummaryNotSent').textContent = notSentTotal;
    }

    function updateFormState() {
      var hasShortage = false;

      document.querySelectorAll('[data-quantity-stepper]').forEach(function (stepper) {
        var input = stepper.querySelector('.quantity-stepper__input');
        var stockPhysical = Number(stepper.dataset.stockPhysical || 0);
        var appliedQuantity = Number(stepper.dataset.appliedQuantity || 0);
        var currentValue = Number(input.value || 0);
        var pendingToApply = Math.max(currentValue - appliedQuantity, 0);

        if (pendingToApply > stockPhysical) {
          hasShortage = true;
        }
      });

      noteInput.required = hasShortage;
      approvedCheckbox.required = !hasShortage;
      approvedCheckbox.disabled = hasShortage;

      if (hasShortage) {
        approvedCheckbox.checked = false;
        stockAlert.classList.remove('d-none');
        noteHelp.textContent = noteHelp.dataset.shortageText;
        lockBadge.className = 'badge bg-danger';
        lockBadge.textContent = labelBlocked;
      } else {
        stockAlert.classList.add('d-none');
        noteHelp.textContent = noteHelp.dataset.normalText;
        lockBadge.className = 'badge bg-success';
        lockBadge.textContent = labelUnlocked;
      }

      form.dataset.hasShortage = hasShortage ? 'true' : 'false';
      updateDispatchSummary();
    }

    document.querySelectorAll('[data-quantity-stepper]').forEach(function (stepper) {
      initQuantityStepper(stepper);
    });

    document.querySelectorAll('.picker-line-reviewed').forEach(function (checkbox) {
      checkbox.addEventListener('change', syncReviewRowHighlights);
    });

    form.addEventListener('submit', function (event) {
      if (!validateRequiredLineReviews()) {
        event.preventDefault();
      }
    });

    document.querySelectorAll('[data-picker-order-line]').forEach(function (row) {
      var presentationSelect = row.querySelector('[data-picker-presentation-select]');
      syncOrderLinePresentationStock(row);
      if (presentationSelect) {
        presentationSelect.addEventListener('change', function () {
          syncOrderLinePresentationStock(row);
          updateFormState();
        });
      }
    });

    document.querySelectorAll('[data-added-product-row]').forEach(function (row) {
      var searchRoot = row.querySelector('[data-picker-product-search-root]');
      if (searchRoot) {
        initProductSearchRoot(searchRoot);
      }
      syncAddedProductRow(row);
    });

    pickerItemsTableBody.addEventListener('click', function (event) {
      var removeButton = event.target.closest('[data-remove-product-row]');
      if (!removeButton) {
        return;
      }

      var row = removeButton.closest('[data-added-product-row]');
      if (row) {
        row.remove();
        updateFormState();
      }
    });

    pickerItemsTableBody.addEventListener('change', function (event) {
      var select = event.target.closest('[data-added-product-select]');
      if (!select) {
        return;
      }

      var row = select.closest('[data-added-product-row]');
      if (row) {
        syncAddedProductRow(row);
        syncReviewRowHighlights();
        updateFormState();
      }
    });

    addProductRowButton.addEventListener('click', function () {
      var row = buildAddedProductRow();
      pickerItemsTableBody.appendChild(row);
      initProductSearchRoot(row.querySelector('[data-picker-product-search-root]'));
      initQuantityStepper(row.querySelector('[data-quantity-stepper]'));
      var addedReviewCheckbox = row.querySelector('.picker-line-reviewed');
      if (addedReviewCheckbox) {
        addedReviewCheckbox.addEventListener('change', syncReviewRowHighlights);
      }
      syncAddedProductRow(row);
      updateFormState();
      var searchInput = row.querySelector('[data-picker-product-search]');
      if (searchInput) {
        searchInput.focus();
      }
    });

    updateFormState();
  });
})();
