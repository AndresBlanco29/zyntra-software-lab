(function () {
  'use strict';

  var TIPO_FREE_UNITS = 'FREE_UNITS';
  var TIPO_PERCENT = 'PERCENT';

  function escapeHtml(text) {
    return String(text)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function debounceMs() {
    return (window.PreserveSearchFocus && window.PreserveSearchFocus.DEFAULT_DEBOUNCE_MS) || 1000;
  }

  function bindProductSearch(root, options) {
    if (!root) {
      return;
    }

    var searchInput = options.searchInput || root.querySelector('.promo-combo-buscador, #promoProductoBuscador');
    var resultsBox = options.resultsBox || root.querySelector('.promo-combo-resultados, #promoProductoResultados');
    var hiddenProducto = options.hiddenProducto || root.querySelector('[name$="-producto"], #id_producto');
    var presentacionVisible = options.presentacionVisible || root.querySelector('.promo-combo-presentacion, #promoPresentacionVisible');
    var hiddenPresentacion = options.hiddenPresentacion || root.querySelector('[name$="-presentacion"], #id_presentacion');
    var searchUrl = root.dataset.searchUrl;
    var presentacionesUrlTemplate = root.dataset.presentacionesUrlTemplate;

    if (!searchInput || !resultsBox || !hiddenProducto) {
      return;
    }

    var debounceTimer = null;
    var lastResults = [];

    function hideResults() {
      resultsBox.hidden = true;
      resultsBox.innerHTML = '';
      lastResults = [];
    }

    function renderResults(results) {
      lastResults = results;
      if (!results.length) {
        resultsBox.innerHTML = '<div class="list-group-item text-muted">No matching products.</div>';
        resultsBox.hidden = false;
        return;
      }
      resultsBox.innerHTML = results.map(function (item, index) {
        var extra = item.codigo_barras ? ' — ' + escapeHtml(item.codigo_barras) : '';
        return (
          '<button type="button" class="list-group-item list-group-item-action" data-result-index="' + index + '">' +
            escapeHtml(item.label) + extra +
          '</button>'
        );
      }).join('');
      resultsBox.hidden = false;
    }

    function loadPresentaciones(productoId, preselectId) {
      if (!presentacionVisible) {
        return;
      }
      presentacionVisible.innerHTML = '<option value="">Any presentation</option>';
      presentacionVisible.disabled = true;
      if (!productoId || !presentacionesUrlTemplate) {
        return;
      }
      var url = presentacionesUrlTemplate.replace('__ID__', String(productoId));
      fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' }, credentials: 'same-origin' })
        .then(function (response) { return response.ok ? response.json() : { results: [] }; })
        .then(function (data) {
          (data.results || []).forEach(function (presentacion) {
            var option = document.createElement('option');
            option.value = presentacion.id;
            option.textContent = presentacion.nombre;
            presentacionVisible.appendChild(option);
          });
          if (preselectId) {
            presentacionVisible.value = String(preselectId);
          }
          presentacionVisible.disabled = false;
        })
        .catch(function () {
          presentacionVisible.disabled = false;
        });
    }

    function selectProduct(item) {
      hiddenProducto.value = String(item.id);
      searchInput.value = item.label;
      hideResults();
      if (hiddenPresentacion) {
        hiddenPresentacion.value = '';
      }
      loadPresentaciones(item.id, null);
    }

    if (hiddenProducto.value) {
      loadPresentaciones(hiddenProducto.value, hiddenPresentacion ? hiddenPresentacion.value : null);
    }

    function fetchResults(query) {
      var url = new URL(searchUrl, window.location.origin);
      url.searchParams.set('q', query);
      fetch(url.toString(), { headers: { 'X-Requested-With': 'XMLHttpRequest' }, credentials: 'same-origin' })
        .then(function (response) { return response.ok ? response.json() : { results: [] }; })
        .then(function (data) { renderResults(data.results || []); })
        .catch(function () { hideResults(); });
    }

    searchInput.addEventListener('input', function () {
      hiddenProducto.value = '';
      var rawValue = searchInput.value || '';
      if (rawValue.endsWith(' ')) {
        return;
      }
      var query = rawValue.trim();
      if (query.length < 2) {
        hideResults();
        return;
      }
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(function () { fetchResults(query); }, debounceMs());
    });

    resultsBox.addEventListener('click', function (event) {
      var button = event.target.closest('[data-result-index]');
      if (!button) {
        return;
      }
      var item = lastResults[Number(button.dataset.resultIndex)];
      if (item) {
        selectProduct(item);
      }
    });

    document.addEventListener('click', function (event) {
      if (!root.contains(event.target)) {
        hideResults();
      }
    });

    if (presentacionVisible) {
      presentacionVisible.addEventListener('change', function () {
        if (hiddenPresentacion) {
          hiddenPresentacion.value = presentacionVisible.value;
        }
      });
    }
  }

  function initProductSearch() {
    bindProductSearch(document.getElementById('promoProductSearchRoot'));
  }

  function bindComboRow(row) {
    var searchRoot = row.querySelector('[data-combo-search-root]');
    if (searchRoot) {
      bindProductSearch(searchRoot, {
        searchInput: row.querySelector('.promo-combo-buscador'),
        resultsBox: row.querySelector('.promo-combo-resultados'),
        hiddenProducto: row.querySelector('[name$="-producto"]'),
        presentacionVisible: row.querySelector('.promo-combo-presentacion'),
        hiddenPresentacion: row.querySelector('[name$="-presentacion"]'),
      });
    }
    var removeBtn = row.querySelector('[data-remove-combo-product]');
    if (removeBtn) {
      removeBtn.addEventListener('click', function () {
        var deleteCheckbox = row.querySelector('[name$="-DELETE"]');
        if (deleteCheckbox) {
          deleteCheckbox.checked = true;
        }
        row.hidden = true;
      });
    }
  }

  function initComboProducts(productosPrefix) {
    var container = document.getElementById('promoComboProductosContainer');
    var addButton = document.getElementById('promoAddComboProductBtn');
    var template = document.getElementById('promoComboProductEmptyTemplate');
    var totalFormsInput = document.getElementById('id_' + productosPrefix + '-TOTAL_FORMS');
    if (!container) {
      return;
    }

    Array.prototype.forEach.call(container.querySelectorAll('[data-combo-row]'), bindComboRow);

    if (addButton && template && totalFormsInput) {
      addButton.addEventListener('click', function () {
        var index = parseInt(totalFormsInput.value, 10) || 0;
        var html = template.innerHTML.split('__prefix__').join(String(index));
        var wrapper = document.createElement('div');
        wrapper.innerHTML = html.trim();
        var newRow = wrapper.firstElementChild;
        container.appendChild(newRow);
        bindComboRow(newRow);
        totalFormsInput.value = String(index + 1);
      });
    }
  }

  function initAlcanceToggle(alcanceIndividual, alcanceGrupo) {
    var individualSection = document.getElementById('promoIndividualSection');
    var comboSection = document.getElementById('promoComboSection');
    var radios = document.querySelectorAll('input[name="alcance"]');
    if (!individualSection || !comboSection || !radios.length) {
      return;
    }

    function syncSections() {
      var selected = document.querySelector('input[name="alcance"]:checked');
      var isGrupo = selected && selected.value === alcanceGrupo;
      individualSection.hidden = isGrupo;
      comboSection.hidden = !isGrupo;
    }

    Array.prototype.forEach.call(radios, function (radio) {
      radio.addEventListener('change', syncSections);
    });
    syncSections();
  }

  function toggleEscalaRowFields(row) {
    var tipoSelect = row.querySelector('[name$="-tipo_beneficio"]');
    var valorWrap = row.querySelector('[data-escala-field="valor_beneficio"]');
    var unidadesWrap = row.querySelector('[data-escala-field="unidades_gratis"]');
    var presetInput = row.querySelector('[data-preset-input="percent"]');
    if (!tipoSelect) {
      return;
    }
    var isFree = tipoSelect.value === TIPO_FREE_UNITS;
    if (valorWrap) {
      valorWrap.hidden = isFree;
    }
    if (unidadesWrap) {
      unidadesWrap.hidden = !isFree;
    }
    if (presetInput) {
      presetInput.setAttribute('list', tipoSelect.value === TIPO_PERCENT ? 'promoPercentPresets' : 'promoFixedPresets');
    }
  }

  function bindEscalaRow(row) {
    toggleEscalaRowFields(row);
    var tipoSelect = row.querySelector('[name$="-tipo_beneficio"]');
    if (tipoSelect) {
      tipoSelect.addEventListener('change', function () { toggleEscalaRowFields(row); });
    }
    var presetInput = row.querySelector('[data-preset-input="percent"]');
    var valorInput = row.querySelector('[name$="-valor_beneficio"]');
    if (presetInput && valorInput) {
      presetInput.addEventListener('input', function () {
        if (presetInput.value) {
          valorInput.value = presetInput.value;
        }
      });
    }
    var removeBtn = row.querySelector('[data-remove-escala]');
    if (removeBtn) {
      removeBtn.addEventListener('click', function () {
        var deleteCheckbox = row.querySelector('[name$="-DELETE"]');
        if (deleteCheckbox) {
          deleteCheckbox.checked = true;
        }
        row.hidden = true;
      });
    }
  }

  function initEscalas(formPrefix) {
    var container = document.getElementById('promoEscalasContainer');
    var addButton = document.getElementById('promoAddEscalaBtn');
    var template = document.getElementById('promoEscalaEmptyTemplate');
    var totalFormsInput = document.getElementById('id_' + formPrefix + '-TOTAL_FORMS');
    if (!container) {
      return;
    }

    Array.prototype.forEach.call(container.querySelectorAll('[data-escala-row]'), bindEscalaRow);

    if (addButton && template && totalFormsInput) {
      addButton.addEventListener('click', function () {
        var index = parseInt(totalFormsInput.value, 10) || 0;
        var html = template.innerHTML.split('__prefix__').join(String(index));
        var wrapper = document.createElement('div');
        wrapper.innerHTML = html.trim();
        var newRow = wrapper.firstElementChild;
        container.appendChild(newRow);
        bindEscalaRow(newRow);
        totalFormsInput.value = String(index + 1);
      });
    }
  }

  window.PromocionForm = {
    init: function (options) {
      options = options || {};
      initProductSearch();
      initEscalas(options.formPrefix || 'escalas');
      initComboProducts(options.productosPrefix || 'productos');
      initAlcanceToggle(options.alcanceIndividual || 'INDIVIDUAL', options.alcanceGrupo || 'GRUPO');
    },
  };
})();
