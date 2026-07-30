(function () {
  'use strict';

  var backdrop = document.getElementById('auditDrawerBackdrop');
  var drawer = document.getElementById('auditDrawer');
  var body = document.getElementById('auditDrawerBody');
  var title = document.getElementById('auditDrawerTitle');
  var closeBtn = document.getElementById('auditDrawerClose');

  function escapeHtml(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function kv(label, value) {
    return (
      '<div class="audit-kv"><div class="k">' +
      escapeHtml(label) +
      '</div><div class="v">' +
      escapeHtml(value || '—') +
      '</div></div>'
    );
  }

  function renderChanges(changes) {
    if (!changes || !changes.length) {
      return '<p class="text-muted small mb-0">No field-level changes were captured for this event.</p>';
    }
    return changes
      .map(function (change) {
        return (
          '<div class="audit-diff-card">' +
          '<div class="field">' +
          escapeHtml(change.field || 'Field') +
          '</div>' +
          '<div class="pair">' +
          '<div class="before"><span class="hint">Before</span>' +
          escapeHtml(change.before || '—') +
          '</div>' +
          '<div class="after"><span class="hint">After</span>' +
          escapeHtml(change.after || '—') +
          '</div>' +
          '</div></div>'
        );
      })
      .join('');
  }

  function renderTimeline(items) {
    if (!items || !items.length) {
      return '<p class="text-muted small mb-0">No related entity history.</p>';
    }
    return (
      '<ul class="audit-timeline">' +
      items
        .map(function (item) {
          return (
            '<li class="' +
            (item.is_current ? 'is-current' : '') +
            '">' +
            '<div class="when">' +
            escapeHtml(item.when) +
            ' · ' +
            escapeHtml(item.actor_display) +
            '</div>' +
            '<div class="label">' +
            escapeHtml(item.action_label) +
            '</div>' +
            '</li>'
          );
        })
        .join('') +
      '</ul>'
    );
  }

  function renderDetail(data) {
    title.textContent = data.action_label || 'Audit event';
    var duration = data.duration_ms != null ? data.duration_ms + ' ms' : '—';
    var html = '';
    html += '<div class="audit-kv-grid">';
    html += kv('User', data.actor_display);
    html += kv('Username', data.actor_username);
    html += kv('Role', data.actor_role);
    html += kv('When', data.when);
    html += kv('IP', data.ip_address);
    html += kv('Location', data.location);
    html += kv('Device', data.device);
    html += kv('OS', data.os_name);
    html += kv('Browser', data.browser);
    html += kv('Module', data.module);
    html += kv('Action type', data.action_category_display);
    html += kv('HTTP method', data.http_method);
    html += kv('Endpoint', data.path);
    html += kv('Route', data.route_name);
    html += kv('Result', data.result_label + ' (' + data.status_code + ')');
    html += kv('Duration', duration);
    html += kv('Entity', data.entity_label || data.entity_type);
    html += kv('Entity ID', data.entity_id);
    html += '</div>';

    html += '<h3 class="h6 fw-bold mb-2">What changed</h3>';
    html += renderChanges(data.changes);
    html += '<h3 class="h6 fw-bold mt-4 mb-2">Entity timeline</h3>';
    html += renderTimeline(data.timeline);

    if (data.metadata && Object.keys(data.metadata).length) {
      html += '<h3 class="h6 fw-bold mt-4 mb-2">Raw context</h3>';
      html += '<pre class="audit-meta-pre">' + escapeHtml(JSON.stringify(data.metadata, null, 2)) + '</pre>';
    }
    if (data.user_agent) {
      html += '<p class="small text-muted mt-3 mb-0"><strong>User agent:</strong> ' + escapeHtml(data.user_agent) + '</p>';
    }
    body.innerHTML = html;
  }

  function openDrawer() {
    if (backdrop) backdrop.hidden = false;
    if (drawer) {
      drawer.classList.add('is-open');
      drawer.setAttribute('aria-hidden', 'false');
    }
  }

  function closeDrawer() {
    if (backdrop) backdrop.hidden = true;
    if (drawer) {
      drawer.classList.remove('is-open');
      drawer.setAttribute('aria-hidden', 'true');
    }
  }

  function loadDetail(logId) {
    var template = window.AUDIT_DETAIL_URL_TEMPLATE || '';
    var url = template.replace('__ID__', String(logId));
    body.innerHTML = '<p class="text-muted">Loading…</p>';
    openDrawer();
    fetch(url, { headers: { Accept: 'application/json' } })
      .then(function (response) {
        if (!response.ok) throw new Error('Failed');
        return response.json();
      })
      .then(renderDetail)
      .catch(function () {
        body.innerHTML = '<p class="text-danger">Could not load event details.</p>';
      });
  }

  document.querySelectorAll('.audit-open-detail').forEach(function (btn) {
    btn.addEventListener('click', function () {
      loadDetail(btn.getAttribute('data-log-id'));
    });
  });

  if (closeBtn) closeBtn.addEventListener('click', closeDrawer);
  if (backdrop) backdrop.addEventListener('click', closeDrawer);
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') closeDrawer();
  });
})();
