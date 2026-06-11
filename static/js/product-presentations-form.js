(function () {
  function renumberPresentationRows(container) {
    container.querySelectorAll('[data-presentation-row]:not([hidden])').forEach(function (row, index) {
      var numberNode = row.querySelector('[data-presentation-number]');
      if (numberNode) {
        numberNode.textContent = String(index + 1);
      }
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

  function initPresentationForms() {
    document.querySelectorAll('[data-presentations-form]').forEach(function (form) {
      var container = form.querySelector('[data-presentations-list]');
      var template = form.querySelector('#presentacion-row-template');
      var addButton = form.querySelector('[data-add-presentation]');
      if (!container || !template || !addButton) {
        return;
      }

      bindRemoveButtons(container, form);

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
      });
    });
  }

  document.addEventListener('DOMContentLoaded', initPresentationForms);
})();
