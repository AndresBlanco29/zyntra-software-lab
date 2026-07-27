(function () {
  'use strict';

  var STORAGE_KEY = 'ltg_take_order_client_mode';

  function isEnabled() {
    try {
      return window.sessionStorage.getItem(STORAGE_KEY) === '1';
    } catch (error) {
      return false;
    }
  }

  function formatPriceOnly(value) {
    var amount = Number(value || 0);
    if (!amount) {
      return '';
    }
    return '$' + amount.toFixed(2);
  }

  function rememberOptionLabel(option) {
    if (!option.dataset.originalLabel) {
      option.dataset.originalLabel = option.textContent;
    }
  }

  function applyOptionLabels(select, clientMode) {
    if (!select) {
      return;
    }
    Array.from(select.options).forEach(function (option) {
      rememberOptionLabel(option);
      if (clientMode && option.value) {
        option.textContent = formatPriceOnly(option.value);
      } else if (option.dataset.originalLabel) {
        option.textContent = option.dataset.originalLabel;
      }
    });
    if (clientMode) {
      select.setAttribute('disabled', 'disabled');
      select.classList.add('client-mode-price-readonly');
    } else {
      select.removeAttribute('disabled');
      select.classList.remove('client-mode-price-readonly');
    }
  }

  function applyCatalogShellState(shell, select, mask, enabled) {
    if (!shell || !select || !mask) {
      return;
    }

    if (!enabled) {
      delete shell.dataset.clientPending;
      return;
    }

    if (select.value) {
      mask.textContent = formatPriceOnly(select.value);
      shell.dataset.state = 'selected';
      delete shell.dataset.clientPending;
    } else {
      // Hide hold-to-pick / "Select price" guidance from the customer view.
      mask.textContent = '';
      shell.dataset.state = 'client-pending';
      shell.dataset.clientPending = 'true';
    }
  }

  function applyCatalogPresentation(enabled) {
    document.querySelectorAll('.precio-select').forEach(function (select) {
      applyOptionLabels(select, enabled);
    });

    document.querySelectorAll('.precio-select-shell').forEach(function (shell) {
      var select = shell.querySelector('.precio-select');
      var mask = shell.querySelector('.precio-select-mask');
      applyCatalogShellState(shell, select, mask, enabled);
    });

    document.querySelectorAll('.precios-vendedor > label.fw-bold.small').forEach(function (label) {
      if (!label.dataset.originalLabel) {
        label.dataset.originalLabel = label.textContent;
      }
      label.textContent = enabled
        ? (label.dataset.clientLabel || 'Price')
        : label.dataset.originalLabel;
      var shell = label.parentElement
        ? label.parentElement.querySelector('.precio-select-shell')
        : null;
      if (enabled && shell && shell.dataset.state === 'client-pending') {
        label.classList.add('d-none');
      } else {
        label.classList.remove('d-none');
      }
    });
  }

  function applySummaryPresentation(enabled) {
    document.querySelectorAll('.precio-resumen-preset').forEach(function (select) {
      applyOptionLabels(select, enabled);
    });

    document.querySelectorAll('.precio-resumen-manual-wrap').forEach(function (wrap) {
      if (enabled) {
        wrap.classList.add('client-mode-price-display');
      } else {
        wrap.classList.remove('client-mode-price-display');
      }
    });
  }

  function applyPresentation(enabled) {
    applyCatalogPresentation(enabled);
    applySummaryPresentation(enabled);
    document.dispatchEvent(new CustomEvent('ltg:client-mode-changed', { detail: { enabled: enabled } }));
  }

  function setEnabled(enabled) {
    try {
      window.sessionStorage.setItem(STORAGE_KEY, enabled ? '1' : '0');
    } catch (error) {
      // Ignore storage failures (private mode).
    }
    document.body.classList.toggle('client-mode', !!enabled);
    document.querySelectorAll('#clientModeToggleBtn').forEach(function (button) {
      button.classList.toggle('is-active', !!enabled);
      button.setAttribute('aria-pressed', enabled ? 'true' : 'false');
      button.textContent = enabled ? (button.dataset.labelExit || 'Exit Client Mode') : (button.dataset.labelEnter || 'Client Mode');
    });
    applyPresentation(!!enabled);
  }

  window.LTGTakeOrderClientMode = {
    isEnabled: isEnabled,
    formatPriceOnly: formatPriceOnly,
    applyPresentation: applyPresentation,
    buildTierOptionLabel: function (tierNumber, amount) {
      if (isEnabled()) {
        return formatPriceOnly(amount);
      }
      return 'Price ' + tierNumber + ' - ' + formatPriceOnly(amount);
    },
    buildSummaryTierLabel: function (tierNumber, amount) {
      if (isEnabled()) {
        return formatPriceOnly(amount);
      }
      return 'PC' + tierNumber + ' · ' + formatPriceOnly(amount);
    },
    buildMaskLabel: function (amount, marginSuffix) {
      if (isEnabled()) {
        return formatPriceOnly(amount);
      }
      if (marginSuffix) {
        return formatPriceOnly(amount) + ' · ' + marginSuffix;
      }
      return formatPriceOnly(amount);
    },
  };

  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('#clientModeToggleBtn').forEach(function (button) {
      if (!button.dataset.labelEnter) {
        button.dataset.labelEnter = button.textContent.trim() || 'Client Mode';
      }
      if (!button.dataset.labelExit) {
        button.dataset.labelExit = 'Exit Client Mode';
      }
      button.addEventListener('click', function () {
        setEnabled(!document.body.classList.contains('client-mode'));
      });
    });
    setEnabled(isEnabled());
  });
})();
