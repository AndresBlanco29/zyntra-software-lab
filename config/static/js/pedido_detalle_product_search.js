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

  document.addEventListener('DOMContentLoaded', function () {
    var root = document.getElementById('newProductSearchRoot');
    if (!root) {
      return;
    }

    var searchInput = document.getElementById('buscadorProductoPedido');
    var hiddenInput = document.getElementById('presentacionNuevaId');
    var resultsBox = document.getElementById('newProductSearchResults');
    var selectedLabel = document.getElementById('newProductSelectedLabel');
    var pricePresetSelect = document.getElementById('precioNuevoPedidoPreset');
    var priceInput = document.getElementById('precioNuevoPedido');
    var searchUrl = root.dataset.searchUrl;
    var pedidoId = root.dataset.pedidoId;
    var emptyMessage = root.dataset.emptyMessage || 'No products found.';
    var minCharsMessage = root.dataset.minCharsMessage || 'Type at least 2 characters to search.';
    var manualPriceLabel = root.dataset.manualPriceLabel || 'Manual price';
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
    }

    function fetchResults(query) {
      var url = new URL(searchUrl, window.location.origin);
      url.searchParams.set('q', query);
      if (pedidoId) {
        url.searchParams.set('pedido_id', pedidoId);
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

    resetPriceFields();

    if (pricePresetSelect && priceInput) {
      pricePresetSelect.addEventListener('change', function () {
        if (pricePresetSelect.value) {
          priceInput.value = pricePresetSelect.value;
        }
      });

      priceInput.addEventListener('input', syncPresetFromInput);
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
      } else if (event.key === 'Enter' && activeIndex >= 0) {
        event.preventDefault();
        selectResult(lastResults[activeIndex]);
      } else if (event.key === 'Escape') {
        hideResults();
      }
    });
  });
})();
