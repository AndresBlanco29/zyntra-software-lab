(function () {
  function renumberPresentationRows(container) {
    container.querySelectorAll('[data-presentation-row]:not([hidden])').forEach(function (row, index) {
      var numberNode = row.querySelector('[data-presentation-number]');
      if (numberNode) {
        numberNode.textContent = String(index + 1);
      }
    });
  }

  function parsePositiveInt(value) {
    var parsed = parseInt(String(value || '').trim(), 10);
    return Number.isFinite(parsed) && parsed >= 1 ? parsed : null;
  }

  function syncPalletQuantity(row) {
    if (!row) {
      return;
    }
    var tieInput = row.querySelector('[data-pallet-tie]');
    var highInput = row.querySelector('[data-pallet-high]');
    var quantityInput = row.querySelector('[data-pallet-quantity]');
    if (!quantityInput) {
      return;
    }
    var tie = parsePositiveInt(tieInput && tieInput.value);
    var high = parsePositiveInt(highInput && highInput.value);
    quantityInput.value = tie && high ? String(tie * high) : '';
  }

  function bindPalletQuantityInputs(container) {
    container.querySelectorAll('[data-presentation-row]').forEach(function (row) {
      if (row.dataset.boundPallet === '1') {
        return;
      }
      row.dataset.boundPallet = '1';
      row.querySelectorAll('[data-pallet-tie], [data-pallet-high]').forEach(function (input) {
        input.addEventListener('input', function () {
          syncPalletQuantity(row);
        });
      });
      syncPalletQuantity(row);
    });
  }

  function ensureDeleteInput(form, presentationId) {
    var inputName = 'presentacion_eliminar';
    var existing = form.querySelector('input[name="' + inputName + '"][value="' + presentationId + '"]');
    if (existing) {
      return;
    }

    var input = document.createElement('input');
    input.type = 'hidden';
    input.name = inputName;
    input.value = String(presentationId);
    form.appendChild(input);
  }

  function bindRemoveButtons(container, form) {
    container.querySelectorAll('[data-remove-presentation]').forEach(function (button) {
      if (button.dataset.boundRemove === '1') {
        return;
      }
      button.dataset.boundRemove = '1';

      button.addEventListener('click', function () {
        var row = button.closest('[data-presentation-row]');
        if (!row) {
          return;
        }

        var visibleRows = container.querySelectorAll('[data-presentation-row]:not([hidden])');
        if (visibleRows.length <= 1) {
          var message = form.getAttribute('data-min-rows-message') || 'Keep at least one presentation.';
          window.alert(message);
          return;
        }

        var presentationId = row.getAttribute('data-presentation-id');
        if (presentationId && form) {
          ensureDeleteInput(form, presentationId);
          row.hidden = true;
        } else {
          row.remove();
        }

        renumberPresentationRows(container);
      });
    });
  }

  function readStaticPackagingDefaults(form) {
    try {
      return JSON.parse(form.getAttribute('data-packaging-defaults') || '{}');
    } catch (error) {
      return {};
    }
  }

  function fetchPackagingDefaults(form) {
    var parseUrl = form.getAttribute('data-parse-packaging-url');
    var nameInput = form.querySelector('input[name="nombre"]');
    var productName = nameInput ? nameInput.value.trim() : '';

    if (parseUrl) {
      if (!productName) {
        window.alert('Ingresa el nombre del producto primero.');
        return Promise.resolve(null);
      }

      return fetch(parseUrl + '?nombre=' + encodeURIComponent(productName), {
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
      })
        .then(function (response) {
          return response.json();
        })
        .then(function (data) {
          if (data && data.ok && data.defaults) {
            return data.defaults;
          }
          window.alert(
            (data && data.error) ||
              'No se detecto un formato de caja en el nombre del producto.'
          );
          return null;
        })
        .catch(function () {
          window.alert('No se pudo detectar el empaque. Intenta de nuevo.');
          return null;
        });
    }

    var defaults = readStaticPackagingDefaults(form);
    return Promise.resolve(defaults && defaults.units_per_case ? defaults : null);
  }

  function initPresentationForms() {
    document.querySelectorAll('[data-presentations-form]').forEach(function (form) {
      var container = form.querySelector('[data-presentations-list]');
      var template = form.querySelector('#presentacion-row-template');
      var addButton = form.querySelector('[data-add-presentation]');
      var detectButton = form.querySelector('[data-detect-packaging-from-name]');
      if (!container || !template || !addButton) {
        return;
      }

      function applyPackagingDefaults(defaults) {
        if (!defaults || !defaults.units_per_case) {
          window.alert('No se detecto un formato de caja en el nombre del producto.');
          return;
        }

        var row = container.querySelector('[data-presentation-row]:not([hidden])');
        if (!row) {
          return;
        }

        var nameInput = row.querySelector(
          '[name^="presentacion_nombre_"], [name="presentacion_nueva_nombre"]'
        );
        var typeInput = row.querySelector(
          '[name^="tipo_contenido_"], [name="presentacion_nueva_tipo_contenido"]'
        );
        var unitsInput = row.querySelector(
          '[name^="unidades_"], [name="presentacion_nueva_unidades"]'
        );

        if (nameInput) {
          nameInput.value = defaults.presentation_name || 'Caja';
        }
        if (typeInput) {
          typeInput.value = defaults.content_type || defaults.unit_size_label || typeInput.value;
        }
        if (unitsInput) {
          unitsInput.value = String(defaults.units_per_case);
        }
      }

      if (detectButton) {
        detectButton.addEventListener('click', function () {
          fetchPackagingDefaults(form).then(function (defaults) {
            if (defaults) {
              applyPackagingDefaults(defaults);
            }
          });
        });
      }

      bindRemoveButtons(container, form);
      bindPalletQuantityInputs(container);

      addButton.addEventListener('click', function () {
        var clone = template.content.firstElementChild.cloneNode(true);
        container.appendChild(clone);
        renumberPresentationRows(container);

        if (window.ProductPriceFormula && typeof window.ProductPriceFormula.initContainer === 'function') {
          var formulaContainer = clone.querySelector('[data-price-formula]');
          if (formulaContainer) {
            window.ProductPriceFormula.initContainer(formulaContainer);
          }
        }

        bindRemoveButtons(container, form);
        bindPalletQuantityInputs(container);
      });
    });
  }

  document.addEventListener('DOMContentLoaded', initPresentationForms);
})();
