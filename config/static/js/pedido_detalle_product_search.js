(function () {
  'use strict';

  function escapeHtml(text) {
    return String(text)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function parseDecimal(value) {
    var normalized = String(value || '').trim().replace(',', '.');
    if (!normalized) {
      return null;
    }
    var parsed = Number(normalized);
    return Number.isFinite(parsed) ? parsed : null;
  }

  function formatMoney(value) {
    var amount = Number(value);
    if (!Number.isFinite(amount)) {
      amount = 0;
    }
    return amount.toFixed(2);
  }

  document.addEventListener('DOMContentLoaded', function () {
    var root = document.getElementById('newProductSearchRoot');
    if (!root) {
      return;
    }

    var searchInput = document.getElementById('buscadorProductoPedido');
    var hiddenInput = document.getElementById('presentacionNuevaId');
    var resultsBox = document.getElementById('newProductSearchResults');
    var selectedLabel = document.getElementById('newProductSelectedLabel');
    var qtyInput = document.getElementById('cantidadNuevaPedido');
    var pricePresetSelect = document.getElementById('precioNuevoPedidoPreset');
    var priceInput = document.getElementById('precioNuevoPedido');
    var addButton = document.getElementById('addPendingProductBtn');
    var tableBody = document.getElementById('pedidoItemsTableBody');
    var totalDisplay = document.getElementById('pedidoFormTotalDisplay');
    var orderForm = document.getElementById('backofficeOrderForm');
    var searchUrl = root.dataset.searchUrl;
    var pedidoId = root.dataset.pedidoId;
    var cotizacionId = root.dataset.cotizacionId;
    var emptyMessage = root.dataset.emptyMessage || 'No products found.';
    var minCharsMessage = root.dataset.minCharsMessage || 'Type at least 2 characters to search.';
    var manualPriceLabel = root.dataset.manualPriceLabel || 'Manual price';
    var addMissingMessage = root.dataset.addMissingMessage || 'Select a product before adding it.';
    var addQtyMessage = root.dataset.addQtyMessage || 'Enter a quantity of at least 1.';
    var addPriceMessage = root.dataset.addPriceMessage || 'Enter a valid price.';
    var pendingLabel = root.dataset.pendingLabel || 'Pending';
    var removeLabel = root.dataset.removeLabel || 'Remove';
    var priceLocked = (pricePresetSelect && pricePresetSelect.dataset.locked === 'true')
      || (priceInput && priceInput.dataset.locked === 'true');

    var debounceTimer = null;
    var activeIndex = -1;
    var lastResults = [];

    function hideResults() {
      resultsBox.hidden = true;
      resultsBox.innerHTML = '';
      activeIndex = -1;
      lastResults = [];
    }

    function resetPriceFields() {
      if (!pricePresetSelect || !priceInput) {
        return;
      }

      pricePresetSelect.innerHTML = '';
      var manualOption = document.createElement('option');
      manualOption.value = '';
      manualOption.textContent = manualPriceLabel;
      pricePresetSelect.appendChild(manualOption);
      pricePresetSelect.value = '';
      pricePresetSelect.disabled = true;

      priceInput.value = '';
      priceInput.disabled = true;
    }

    function clearStagingFields() {
      hiddenInput.value = '';
      searchInput.value = '';
      selectedLabel.hidden = true;
      selectedLabel.textContent = '';
      if (qtyInput) {
        qtyInput.value = '1';
      }
      resetPriceFields();
      hideResults();
    }

    function syncPresetFromInput() {
      if (!pricePresetSelect || !priceInput) {
        return;
      }

      var inputValue = parseDecimal(priceInput.value);
      var matchedValue = '';

      Array.prototype.forEach.call(pricePresetSelect.options, function (option) {
        if (!option.value) {
          return;
        }
        var optionValue = parseDecimal(option.value);
        if (inputValue !== null && optionValue !== null && optionValue === inputValue) {
          matchedValue = option.value;
        }
      });

      pricePresetSelect.value = matchedValue;
    }

    function populatePriceFields(item) {
      if (!pricePresetSelect || !priceInput || priceLocked) {
        return;
      }

      pricePresetSelect.innerHTML = '';
      var manualOption = document.createElement('option');
      manualOption.value = '';
      manualOption.textContent = manualPriceLabel;
      pricePresetSelect.appendChild(manualOption);

      (item.prices || []).forEach(function (priceOption) {
        var option = document.createElement('option');
        option.value = priceOption.value;
        option.textContent = priceOption.label;
        option.dataset.priceKey = priceOption.key;
        pricePresetSelect.appendChild(option);
      });

      var defaultPrice = item.price || '';
      priceInput.value = defaultPrice;
      syncPresetFromInput();

      if (!pricePresetSelect.value && defaultPrice) {
        var defaultOption = pricePresetSelect.querySelector('option[data-price-key="' + item.default_price_key + '"]');
        if (defaultOption) {
          pricePresetSelect.value = defaultOption.value;
        }
      }

      pricePresetSelect.disabled = false;
      priceInput.disabled = false;
    }

    function highlightActive() {
      resultsBox.querySelectorAll('.product-search-result').forEach(function (element, index) {
        element.classList.toggle('active', index === activeIndex);
        if (index === activeIndex) {
          element.scrollIntoView({ block: 'nearest' });
        }
      });
    }

    function renderResults(results, message) {
      lastResults = results;
      if (!results.length) {
        resultsBox.innerHTML = '<div class="list-group-item text-muted">' + escapeHtml(message || emptyMessage) + '</div>';
        resultsBox.hidden = false;
        return;
      }

      resultsBox.innerHTML = results.map(function (item, index) {
        return (
          '<button type="button" class="list-group-item list-group-item-action product-search-result" data-index="' + index + '">' +
            '<div class="fw-semibold">' + escapeHtml(item.label) + '</div>' +
            '<div class="small text-muted">$' + escapeHtml(item.price) + '</div>' +
          '</button>'
        );
      }).join('');
      resultsBox.hidden = false;
      activeIndex = -1;
    }

    function selectResult(item) {
      hiddenInput.value = String(item.id);
      searchInput.value = item.label;
      selectedLabel.textContent = item.label;
      selectedLabel.hidden = false;
      populatePriceFields(item);
      hideResults();
      if (qtyInput && !qtyInput.value) {
        qtyInput.value = '1';
      }
      if (qtyInput) {
        qtyInput.focus();
        qtyInput.select();
      }
    }

    function fetchResults(query) {
      var url = new URL(searchUrl, window.location.origin);
      url.searchParams.set('q', query);
      if (pedidoId) {
        url.searchParams.set('pedido_id', pedidoId);
      } else if (cotizacionId) {
        url.searchParams.set('cotizacion_id', cotizacionId);
      }

      fetch(url.toString(), {
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
        credentials: 'same-origin',
      })
        .then(function (response) {
          if (!response.ok) {
            throw new Error('search failed');
          }
          return response.json();
        })
        .then(function (data) {
          renderResults(data.results || [], emptyMessage);
        })
        .catch(function () {
          hideResults();
        });
    }

    function updatePendingRowSubtotal(row) {
      var qtyField = row.querySelector('[data-pending-qty]');
      var priceField = row.querySelector('[data-pending-price]');
      var subtotalCell = row.querySelector('[data-pending-subtotal]');
      var payCell = row.querySelector('[data-pending-pay]');
      var qty = parseDecimal(qtyField ? qtyField.value : 0) || 0;
      var price = parseDecimal(priceField ? priceField.value : 0) || 0;
      var subtotal = qty * price;
      if (subtotalCell) {
        subtotalCell.textContent = '$' + formatMoney(subtotal);
      }
      if (payCell) {
        payCell.textContent = '$' + formatMoney(price);
      }
      updateDisplayedTotal();
    }

    function updateDisplayedTotal() {
      if (!totalDisplay) {
        return;
      }
      var total = 0;
      document.querySelectorAll('.pedido-item-subtotal').forEach(function (cell) {
        var raw = String(cell.textContent || '').replace(/[^0-9.,-]/g, '').replace(',', '.');
        var value = parseDecimal(raw);
        if (value !== null) {
          total += value;
        }
      });
      document.querySelectorAll('[data-pending-product-row]').forEach(function (row) {
        var qtyField = row.querySelector('[data-pending-qty]');
        var priceField = row.querySelector('[data-pending-price]');
        var qty = parseDecimal(qtyField ? qtyField.value : 0) || 0;
        var price = parseDecimal(priceField ? priceField.value : 0) || 0;
        total += qty * price;
      });
      totalDisplay.textContent = formatMoney(total);
    }

    function buildPendingProductRow(presentationId, label, quantity, price) {
      var row = document.createElement('tr');
      row.className = 'table-success';
      row.setAttribute('data-pending-product-row', 'true');
      row.innerHTML = '' +
        '<td>' +
          '<div class="fw-semibold">' + escapeHtml(label) + '</div>' +
          '<span class="badge text-bg-success mt-1">' + escapeHtml(pendingLabel) + '</span>' +
          '<input type="hidden" name="presentacion_nueva[]" value="' + escapeHtml(presentationId) + '">' +
        '</td>' +
        '<td class="small text-muted">—</td>' +
        '<td class="small text-muted">—</td>' +
        '<td>' +
          '<input type="number" min="1" class="form-control form-control-sm" name="cantidad_nueva[]" value="' + escapeHtml(String(quantity)) + '" data-pending-qty>' +
        '</td>' +
        '<td>' +
          '<input type="number" min="0.01" step="0.01" class="form-control form-control-sm" name="precio_nuevo[]" value="' + escapeHtml(formatMoney(price)) + '" data-pending-price>' +
        '</td>' +
        '<td class="small text-muted">—</td>' +
        '<td data-pending-pay>$' + escapeHtml(formatMoney(price)) + '</td>' +
        '<td data-pending-subtotal>$' + escapeHtml(formatMoney(quantity * price)) + '</td>' +
        '<td>' +
          '<button type="button" class="btn btn-outline-danger btn-sm" data-remove-pending-row>' + escapeHtml(removeLabel) + '</button>' +
        '</td>';
      return row;
    }

    function addPendingProduct() {
      var presentationId = String(hiddenInput.value || '').trim();
      var label = String(selectedLabel.textContent || searchInput.value || '').trim();
      var quantity = parseDecimal(qtyInput ? qtyInput.value : '1');
      var price = parseDecimal(priceInput ? priceInput.value : '');

      if (!presentationId) {
        window.alert(addMissingMessage);
        searchInput.focus();
        return;
      }
      if (quantity === null || quantity < 1) {
        window.alert(addQtyMessage);
        if (qtyInput) {
          qtyInput.focus();
        }
        return;
      }
      if (price === null || price <= 0) {
        window.alert(addPriceMessage);
        if (priceInput) {
          priceInput.focus();
        }
        return;
      }
      if (!tableBody) {
        return;
      }

      var row = buildPendingProductRow(presentationId, label, Math.round(quantity), price);
      tableBody.appendChild(row);
      updatePendingRowSubtotal(row);
      clearStagingFields();
      searchInput.focus();
    }

    resetPriceFields();
    if (qtyInput && !qtyInput.value) {
      qtyInput.value = '1';
    }

    if (pricePresetSelect && priceInput) {
      pricePresetSelect.addEventListener('change', function () {
        if (pricePresetSelect.value) {
          priceInput.value = pricePresetSelect.value;
        }
      });

      priceInput.addEventListener('input', syncPresetFromInput);
    }

    if (addButton) {
      addButton.addEventListener('click', function (event) {
        event.preventDefault();
        addPendingProduct();
      });
    }

    if (tableBody) {
      tableBody.addEventListener('click', function (event) {
        var removeButton = event.target.closest('[data-remove-pending-row]');
        if (!removeButton) {
          return;
        }
        var row = removeButton.closest('[data-pending-product-row]');
        if (row) {
          row.remove();
          updateDisplayedTotal();
        }
      });

      tableBody.addEventListener('input', function (event) {
        var field = event.target.closest('[data-pending-qty], [data-pending-price]');
        if (!field) {
          return;
        }
        var row = field.closest('[data-pending-product-row]');
        if (row) {
          updatePendingRowSubtotal(row);
        }
      });
    }

    if (orderForm) {
      orderForm.addEventListener('submit', function (event) {
        if (!hiddenInput || !hiddenInput.value || !tableBody || !addButton) {
          return;
        }
        // If the user selected a product but forgot Add, queue it automatically.
        event.preventDefault();
        addPendingProduct();
        if (!hiddenInput.value) {
          HTMLFormElement.prototype.submit.call(orderForm);
        }
      });
    }

    searchInput.addEventListener('input', function () {
      hiddenInput.value = '';
      selectedLabel.hidden = true;
      selectedLabel.textContent = '';
      resetPriceFields();

      var query = searchInput.value.trim();
      if (query.length < 2) {
        if (query.length) {
          renderResults([], minCharsMessage);
        } else {
          hideResults();
        }
        return;
      }

      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(function () {
        fetchResults(query);
      }, 250);
    });

    resultsBox.addEventListener('click', function (event) {
      var button = event.target.closest('.product-search-result');
      if (!button) {
        return;
      }
      var item = lastResults[Number(button.dataset.index)];
      if (item) {
        selectResult(item);
      }
    });

    document.addEventListener('click', function (event) {
      if (!root.contains(event.target)) {
        hideResults();
      }
    });

    searchInput.addEventListener('keydown', function (event) {
      if (event.key === 'Enter') {
        if (!resultsBox.hidden && lastResults.length && activeIndex >= 0) {
          event.preventDefault();
          selectResult(lastResults[activeIndex]);
          return;
        }
        if (hiddenInput.value && addButton && tableBody) {
          event.preventDefault();
          addPendingProduct();
        }
        return;
      }

      if (resultsBox.hidden || !lastResults.length) {
        return;
      }

      if (event.key === 'ArrowDown') {
        event.preventDefault();
        activeIndex = Math.min(activeIndex + 1, lastResults.length - 1);
        highlightActive();
      } else if (event.key === 'ArrowUp') {
        event.preventDefault();
        activeIndex = Math.max(activeIndex - 1, 0);
        highlightActive();
      } else if (event.key === 'Escape') {
        hideResults();
      }
    });

    if (qtyInput && addButton && tableBody) {
      qtyInput.addEventListener('keydown', function (event) {
        if (event.key === 'Enter') {
          event.preventDefault();
          addPendingProduct();
        }
      });
    }
  });
})();
