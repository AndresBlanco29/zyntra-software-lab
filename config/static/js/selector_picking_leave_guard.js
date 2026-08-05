(function () {
  'use strict';

  function boot() {
  var form = document.getElementById('pickerVerificationForm');
  if (!form) {
    return;
  }

  var modalEl = document.getElementById('pickerLeaveGuardModal');
  var cfg = window.LTG_PICKER_LEAVE_GUARD || {};
  var listUrl = cfg.listUrl || '/';
  var draftKey = cfg.draftKey || '';
  var baseline = '';
  var dirty = false;
  var allowLeave = false;
  var discardChanges = false;
  var pendingHref = '';
  var historyGuardActive = false;
  var modalInstance = null;

  function serializeFormState() {
    var data = new FormData(form);
    var pairs = [];
    data.forEach(function (value, key) {
      if (key === 'csrfmiddlewaretoken' || key === 'submit_action' || key === 'next' || key === 'ajax') {
        return;
      }
      pairs.push(key + '=' + String(value));
    });
    pairs.sort();
    return pairs.join('&');
  }

  function refreshDirtyState() {
    dirty = serializeFormState() !== baseline;
    form.dataset.hasUnsavedChanges = dirty ? '1' : '0';
    return dirty;
  }

  function markClean() {
    baseline = serializeFormState();
    dirty = false;
    form.dataset.hasUnsavedChanges = '0';
    clearDraft();
  }

  function persistDraft() {
    if (!draftKey || !window.sessionStorage) {
      return;
    }
    try {
      var payload = {
        savedAt: Date.now(),
        fields: {},
      };
      var data = new FormData(form);
      data.forEach(function (value, key) {
        if (key === 'csrfmiddlewaretoken' || key === 'submit_action' || key === 'next' || key === 'ajax') {
          return;
        }
        if (Object.prototype.hasOwnProperty.call(payload.fields, key)) {
          if (!Array.isArray(payload.fields[key])) {
            payload.fields[key] = [payload.fields[key]];
          }
          payload.fields[key].push(String(value));
        } else {
          payload.fields[key] = String(value);
        }
      });
      window.sessionStorage.setItem(draftKey, JSON.stringify(payload));
    } catch (err) {
      // Ignore storage failures (private mode / quota).
    }
  }

  function clearDraft() {
    if (!draftKey || !window.sessionStorage) {
      return;
    }
    try {
      window.sessionStorage.removeItem(draftKey);
    } catch (err) {
      // Ignore storage failures.
    }
  }

  function draftMatchesBaseline(payload) {
    if (!payload || !payload.fields) {
      return true;
    }
    var pairs = [];
    Object.keys(payload.fields).forEach(function (key) {
      var value = payload.fields[key];
      if (Array.isArray(value)) {
        value.forEach(function (entry) {
          pairs.push(key + '=' + String(entry));
        });
      } else {
        pairs.push(key + '=' + String(value));
      }
    });
    pairs.sort();
    return pairs.join('&') === baseline;
  }

  function applyDraftIfPresent() {
    if (!draftKey || !window.sessionStorage) {
      return false;
    }
    var raw;
    try {
      raw = window.sessionStorage.getItem(draftKey);
    } catch (err) {
      return false;
    }
    if (!raw) {
      return false;
    }
    var payload;
    try {
      payload = JSON.parse(raw);
    } catch (err) {
      clearDraft();
      return false;
    }
    if (!payload || !payload.fields) {
      clearDraft();
      return false;
    }
    if (draftMatchesBaseline(payload)) {
      clearDraft();
      return false;
    }

    form.querySelectorAll('input[type="checkbox"]').forEach(function (checkbox) {
      if (!Object.prototype.hasOwnProperty.call(payload.fields, checkbox.name)) {
        checkbox.checked = false;
      }
    });

    Object.keys(payload.fields).forEach(function (name) {
      var value = payload.fields[name];
      var escapedName = (window.CSS && CSS.escape) ? CSS.escape(name) : String(name).replace(/\\/g, '\\\\').replace(/"/g, '\\"');
      var controls = form.querySelectorAll('[name="' + escapedName + '"]');
      if (!controls.length) {
        return;
      }
      var values = Array.isArray(value) ? value : [value];
      var checkboxOrRadio = controls[0].type === 'checkbox' || controls[0].type === 'radio';
      if (checkboxOrRadio) {
        controls.forEach(function (control) {
          control.checked = values.indexOf(control.value || 'on') !== -1 || values.indexOf('on') !== -1;
        });
        return;
      }
      controls.forEach(function (control, index) {
        if (values[index] !== undefined) {
          control.value = values[index];
        } else if (values.length === 1 && controls.length === 1) {
          control.value = values[0];
        }
      });
    });
    form.dispatchEvent(new Event('change', { bubbles: true }));
    return true;
  }

  function getModal() {
    if (!modalEl || typeof bootstrap === 'undefined' || !bootstrap.Modal) {
      return null;
    }
    if (!modalInstance) {
      modalInstance = bootstrap.Modal.getOrCreateInstance(modalEl);
    }
    return modalInstance;
  }

  function showLeaveModal(href) {
    pendingHref = href || listUrl;
    var modal = getModal();
    if (!modal) {
      var leave = window.confirm(cfg.confirmMessage || 'Leave without saving changes?');
      if (leave) {
        allowLeave = true;
        discardChanges = true;
        clearDraft();
        window.location.href = pendingHref;
      }
      return;
    }
    modal.show();
  }

  function navigateToPending() {
    allowLeave = true;
    window.location.href = pendingHref || listUrl;
  }

  function saveAndLeave() {
    allowLeave = true;
    discardChanges = false;
    clearDraft();
    var nextInput = document.getElementById('pickerLeaveNextInput') || form.querySelector('input[name="next"]');
    if (!nextInput) {
      nextInput = document.createElement('input');
      nextInput.type = 'hidden';
      nextInput.name = 'next';
      nextInput.id = 'pickerLeaveNextInput';
      form.appendChild(nextInput);
    }
    nextInput.value = pendingHref || listUrl;

    // Prefer the existing Save progress button so browser includes submit_action.
    var saveButton = form.querySelector('button[name="submit_action"][value="save_progress"]');
    if (saveButton && typeof form.requestSubmit === 'function') {
      form.requestSubmit(saveButton);
      return;
    }
    var actionInput = form.querySelector('input[name="submit_action"][data-leave-guard="1"]');
    if (!actionInput) {
      actionInput = document.createElement('input');
      actionInput.type = 'hidden';
      actionInput.name = 'submit_action';
      actionInput.setAttribute('data-leave-guard', '1');
      form.appendChild(actionInput);
    }
    actionInput.value = 'save_progress';
    form.submit();
  }

  function emergencySaveProgress() {
    if (!dirty || allowLeave || discardChanges) {
      return;
    }
    try {
      var data = new FormData(form);
      data.set('submit_action', 'save_progress');
      data.set('ajax', '1');
      if (navigator.sendBeacon) {
        navigator.sendBeacon(form.getAttribute('action') || window.location.href, data);
      }
    } catch (err) {
      // Best-effort only.
    }
  }

  function isInternalNavigableLink(anchor) {
    if (!anchor || !anchor.getAttribute) {
      return false;
    }
    var href = anchor.getAttribute('href');
    if (!href || href.charAt(0) === '#') {
      return false;
    }
    if (anchor.target && anchor.target !== '_self') {
      return false;
    }
    if (anchor.hasAttribute('download')) {
      return false;
    }
    if (modalEl && modalEl.contains(anchor)) {
      return false;
    }
    return true;
  }

  form.addEventListener('input', function () {
    refreshDirtyState();
    if (dirty) {
      persistDraft();
    }
  });
  form.addEventListener('change', function () {
    refreshDirtyState();
    if (dirty) {
      persistDraft();
    }
  });

  form.addEventListener('submit', function () {
    allowLeave = true;
    discardChanges = false;
    clearDraft();
  });

  document.addEventListener('click', function (event) {
    if (allowLeave || !refreshDirtyState()) {
      return;
    }

    var logoutTrigger = event.target.closest('[data-bs-target="#logoutModal"], [href="#logoutModal"]');
    if (logoutTrigger) {
      event.preventDefault();
      event.stopPropagation();
      var logoutLink = document.querySelector('#logoutModal a[href]');
      showLeaveModal(logoutLink ? logoutLink.href : listUrl);
      return;
    }

    var anchor = event.target.closest('a[href]');
    if (!isInternalNavigableLink(anchor)) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    showLeaveModal(anchor.href);
  }, true);

  window.addEventListener('beforeunload', function (event) {
    if (allowLeave || discardChanges || !refreshDirtyState()) {
      return;
    }
    persistDraft();
    event.preventDefault();
    event.returnValue = '';
  });

  window.addEventListener('pagehide', function () {
    if (discardChanges) {
      clearDraft();
      return;
    }
    if (dirty && !allowLeave) {
      persistDraft();
      emergencySaveProgress();
    }
  });

  if (modalEl) {
    var stayBtn = document.getElementById('pickerLeaveStayBtn');
    var leaveBtn = document.getElementById('pickerLeaveWithoutSavingBtn');
    var saveLeaveBtn = document.getElementById('pickerSaveAndLeaveBtn');

    if (stayBtn) {
      stayBtn.addEventListener('click', function () {
        pendingHref = '';
        var modal = getModal();
        if (modal) {
          modal.hide();
        }
      });
    }
    if (leaveBtn) {
      leaveBtn.addEventListener('click', function () {
        discardChanges = true;
        clearDraft();
        var modal = getModal();
        if (modal) {
          modal.hide();
        }
        navigateToPending();
      });
    }
    if (saveLeaveBtn) {
      saveLeaveBtn.addEventListener('click', function () {
        var modal = getModal();
        if (modal) {
          modal.hide();
        }
        saveAndLeave();
      });
    }
  }

  // Soft-block browser Back while dirty (re-push current URL and show modal).
  try {
    history.pushState({ pickerLeaveGuard: 1 }, '', window.location.href);
    historyGuardActive = true;
  } catch (err) {
    historyGuardActive = false;
  }

  window.addEventListener('popstate', function () {
    if (!historyGuardActive) {
      return;
    }
    if (allowLeave || discardChanges || !refreshDirtyState()) {
      return;
    }
    try {
      history.pushState({ pickerLeaveGuard: 1 }, '', window.location.href);
    } catch (err) {
      // Ignore history failures.
    }
    showLeaveModal(listUrl);
  });

  baseline = serializeFormState();
  dirty = false;
  form.dataset.hasUnsavedChanges = '0';
  if (applyDraftIfPresent()) {
    refreshDirtyState();
  }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
