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
  }

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
