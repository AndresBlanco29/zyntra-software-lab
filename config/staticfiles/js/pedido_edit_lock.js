(function () {
  'use strict';

  function getCsrfToken(form) {
    var input = form ? form.querySelector('input[name="csrfmiddlewaretoken"]') : null;
    return input ? input.value : '';
  }

  document.addEventListener('DOMContentLoaded', function () {
    var form = document.getElementById('backofficeOrderForm');
    if (!form || form.dataset.pedidoEditLockActive !== 'true') {
      return;
    }

    var pingUrl = form.dataset.pedidoLockPingUrl;
    var releaseUrl = form.dataset.pedidoLockReleaseUrl;
    var csrfToken = getCsrfToken(form);
    var heartbeatMs = 60000;
    var lockConflictHandled = false;

    function releaseLock() {
      if (!releaseUrl || !csrfToken) {
        return;
      }

      var body = new URLSearchParams();
      body.set('csrfmiddlewaretoken', csrfToken);

      if (navigator.sendBeacon) {
        var blob = new Blob([body.toString()], { type: 'application/x-www-form-urlencoded;charset=UTF-8' });
        navigator.sendBeacon(releaseUrl, blob);
        return;
      }

      fetch(releaseUrl, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/x-www-form-urlencoded',
          'X-CSRFToken': csrfToken,
          'X-Requested-With': 'XMLHttpRequest',
        },
        credentials: 'same-origin',
        body: body.toString(),
        keepalive: true,
      });
    }

    function handleLockConflict(response) {
      if (lockConflictHandled) {
        return;
      }
      lockConflictHandled = true;

      response.json().then(function (payload) {
        var message = (payload && payload.error) || 'This sales order is being edited by another user.';
        window.alert(message);
      }).catch(function () {
        window.alert('This sales order is being edited by another user.');
      });

      var fieldset = form.querySelector('fieldset');
      if (fieldset) {
        fieldset.disabled = true;
      }
    }

    function pingLock() {
      if (!pingUrl || !csrfToken || lockConflictHandled) {
        return;
      }

      fetch(pingUrl, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/x-www-form-urlencoded',
          'X-CSRFToken': csrfToken,
          'X-Requested-With': 'XMLHttpRequest',
        },
        credentials: 'same-origin',
        body: 'csrfmiddlewaretoken=' + encodeURIComponent(csrfToken),
      }).then(function (response) {
        if (response.status === 409) {
          handleLockConflict(response);
        }
      }).catch(function () {
        // Ignore transient network errors; stale lock timeout will recover access.
      });
    }

    pingLock();
    window.setInterval(pingLock, heartbeatMs);
    window.addEventListener('beforeunload', releaseLock);
    form.addEventListener('submit', function () {
      window.removeEventListener('beforeunload', releaseLock);
    });
  });
})();
