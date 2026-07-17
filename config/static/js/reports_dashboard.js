(function () {
  'use strict';

  var payloadEl = document.getElementById('reports-chart-payloads');
  var payload = {};
  try {
    payload = payloadEl ? JSON.parse(payloadEl.textContent) : {};
  } catch (err) {
    payload = {};
  }

  var palette = ['#0f5c91', '#2b9f5a', '#d58918', '#7552cc', '#39a7d8', '#db5b6b', '#0d9488', '#64748b'];

  function hasChart() {
    return typeof window.Chart !== 'undefined';
  }

  function moneyTicks(value) {
    if (value >= 1000) return '$' + (value / 1000).toFixed(1) + 'k';
    return '$' + Number(value).toFixed(0);
  }

  function makeLine(canvasId, labels, datasets) {
    var canvas = document.getElementById(canvasId);
    if (!canvas || !hasChart() || !labels || !labels.length) return;
    new Chart(canvas, {
      type: 'line',
      data: { labels: labels, datasets: datasets },
      options: {
        responsive: true,
        maintainAspectRatio: true,
        interaction: { mode: 'index', intersect: false },
        plugins: { legend: { position: 'bottom' } },
        scales: {
          y: {
            ticks: { callback: moneyTicks },
            grid: { color: 'rgba(15, 42, 99, 0.06)' },
          },
          x: { grid: { display: false } },
        },
      },
    });
  }

  function makeBar(canvasId, labels, data, color, horizontal) {
    var canvas = document.getElementById(canvasId);
    if (!canvas || !hasChart() || !labels || !labels.length) return;
    new Chart(canvas, {
      type: 'bar',
      data: {
        labels: labels,
        datasets: [{
          data: data,
          backgroundColor: color || palette[0],
          borderRadius: 6,
          maxBarThickness: 28,
        }],
      },
      options: {
        indexAxis: horizontal ? 'y' : 'x',
        responsive: true,
        plugins: { legend: { display: false } },
        scales: {
          x: horizontal
            ? { ticks: { callback: moneyTicks }, grid: { color: 'rgba(15, 42, 99, 0.06)' } }
            : { grid: { display: false } },
          y: horizontal
            ? { grid: { display: false } }
            : { ticks: { callback: moneyTicks }, grid: { color: 'rgba(15, 42, 99, 0.06)' } },
        },
      },
    });
  }

  function makeDoughnut(canvasId, chartPayload) {
    var canvas = document.getElementById(canvasId);
    if (!canvas || !hasChart() || !chartPayload) return;
    var labels = chartPayload.labels || [];
    var values = chartPayload.values || chartPayload.data || [];
    if (!labels.length) return;
    new Chart(canvas, {
      type: 'doughnut',
      data: {
        labels: labels,
        datasets: [{
          data: values,
          backgroundColor: (chartPayload.colors && chartPayload.colors.length) ? chartPayload.colors : palette,
          borderWidth: 0,
        }],
      },
      options: {
        responsive: true,
        plugins: {
          legend: { position: 'bottom', labels: { boxWidth: 12, font: { size: 11 } } },
        },
        cutout: '62%',
      },
    });
  }

  function renderCharts() {
    var trend = payload.trend || {};
    makeLine('chart-trend', trend.labels || [], [
      {
        label: 'Sales',
        data: trend.sales || trend.sales_amount || [],
        borderColor: '#1d5cb8',
        backgroundColor: 'rgba(29, 92, 184, 0.12)',
        fill: true,
        tension: 0.35,
      },
      {
        label: 'Collected',
        data: trend.collected || trend.collected_amount || [],
        borderColor: '#2b9f5a',
        backgroundColor: 'rgba(43, 159, 90, 0.10)',
        fill: true,
        tension: 0.35,
      },
    ]);

    makeDoughnut('chart-categories', payload.categories);
    makeDoughnut('chart-payments', payload.payments);

    var brands = payload.brands || {};
    makeBar('chart-brands', brands.labels || [], brands.revenue || [], '#7552cc', true);

    var products = payload.products || {};
    makeBar('chart-products', products.labels || [], products.revenue || [], '#0f5c91', true);

    var customers = payload.customers || {};
    makeBar('chart-customers', customers.labels || [], customers.sales || [], '#39a7d8', true);

    var vendors = payload.vendors || {};
    makeBar(
      'chart-vendors',
      vendors.labels || [],
      vendors.sales || vendors.values || [],
      '#d58918',
      true
    );

    var drivers = payload.drivers || {};
    makeBar(
      'chart-drivers',
      drivers.labels || [],
      drivers.collected || drivers.values || drivers.amounts || [],
      '#2b9f5a',
      true
    );
  }

  function applyFocus(focus) {
    var shell = document.querySelector('.bi-dashboard');
    if (!shell) return;
    focus = focus || 'summary';
    shell.setAttribute('data-focus', focus);
    document.querySelectorAll('.reports-focus-btn').forEach(function (btn) {
      btn.classList.toggle('is-active', btn.getAttribute('data-focus') === focus);
    });
    var focusInput = document.getElementById('bi-focus-input');
    if (focusInput) focusInput.value = focus;

    var overview = focus === 'summary';
    document.querySelectorAll('.bi-section').forEach(function (section) {
      var keys = (section.getAttribute('data-section') || '').split(/\s+/).filter(Boolean);
      var show;
      if (overview) {
        show = keys.indexOf('summary') !== -1;
      } else if (focus === 'margins') {
        show = keys.indexOf('products') !== -1 || keys.indexOf('margins') !== -1;
      } else {
        show = keys.indexOf(focus) !== -1;
      }
      section.classList.toggle('is-hidden', !show);
    });

    if (!overview) {
      var map = { margins: 'products', trends: 'trends', finance: 'finance' };
      var targetId = 'section-' + (map[focus] || focus);
      var target = document.getElementById(targetId);
      if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }

  function bindFocusNav() {
    document.querySelectorAll('.reports-focus-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var focus = btn.getAttribute('data-focus') || 'summary';
        applyFocus(focus);
        var url = new URL(window.location.href);
        url.searchParams.set('focus', focus);
        window.history.replaceState({}, '', url.toString());
      });
    });
  }

  function bindSmartSearch() {
    var input = document.getElementById('bi-smart-input');
    var go = document.getElementById('bi-smart-go');
    if (!input || !go) return;

    var rules = [
      { re: /product|sku|sold most|más vendido|vendio/i, focus: 'products', period: 'month' },
      { re: /margin|utilidad|margen|profit/i, focus: 'margins', period: 'month' },
      { re: /customer|cliente|debt|debe|debe más|owes/i, focus: 'customers', period: 'month' },
      { re: /seller|vendedor|rep|sales rep/i, focus: 'vendors', period: 'month' },
      { re: /driver|conductor|ruta|route/i, focus: 'drivers', period: 'week' },
      { re: /stock|inventario|agotado|out of stock|low stock/i, focus: 'inventory', period: 'today' },
      { re: /yesterday|ayer|today|hoy|vendimos/i, focus: 'summary', period: 'today' },
      { re: /finance|ingreso|ganancia|cash|financ/i, focus: 'finance', period: 'month' },
      { re: /trend|tendencia|evolución/i, focus: 'trends', period: 'month' },
    ];

    function run() {
      var q = (input.value || '').trim();
      var match = rules.find(function (rule) { return rule.re.test(q); });
      var url = new URL(window.location.href);
      if (match) {
        url.searchParams.set('focus', match.focus);
        if (!url.searchParams.get('period') || url.searchParams.get('period') === 'today') {
          url.searchParams.set('period', match.period);
        }
      } else if (q) {
        url.searchParams.set('focus', 'summary');
      }
      window.location.href = url.toString();
    }

    go.addEventListener('click', run);
    input.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') {
        e.preventDefault();
        run();
      }
    });
  }

  function bindTableFilters() {
    document.querySelectorAll('.reports-table-filter').forEach(function (input) {
      input.addEventListener('input', function () {
        var table = document.querySelector(input.getAttribute('data-target'));
        if (!table) return;
        var q = (input.value || '').toLowerCase();
        table.querySelectorAll('tbody tr').forEach(function (row) {
          row.style.display = !q || row.textContent.toLowerCase().indexOf(q) !== -1 ? '' : 'none';
        });
      });
    });
  }

  function bindSortableTables() {
    document.querySelectorAll('table.reports-sortable').forEach(function (table) {
      table.querySelectorAll('th[data-sort]').forEach(function (th, index) {
        th.style.cursor = 'pointer';
        th.addEventListener('click', function () {
          var type = th.getAttribute('data-sort');
          var tbody = table.tBodies[0];
          if (!tbody) return;
          var rows = Array.prototype.slice.call(tbody.rows);
          var asc = th.getAttribute('data-asc') !== '1';
          table.querySelectorAll('th[data-sort]').forEach(function (other) {
            other.removeAttribute('data-asc');
          });
          th.setAttribute('data-asc', asc ? '1' : '0');
          rows.sort(function (a, b) {
            var aCell = a.cells[index];
            var bCell = b.cells[index];
            var aVal = aCell.getAttribute('data-value') || aCell.textContent.trim();
            var bVal = bCell.getAttribute('data-value') || bCell.textContent.trim();
            if (type === 'num') {
              aVal = parseFloat(String(aVal).replace(/[^0-9.-]/g, '')) || 0;
              bVal = parseFloat(String(bVal).replace(/[^0-9.-]/g, '')) || 0;
              return asc ? aVal - bVal : bVal - aVal;
            }
            aVal = String(aVal).toLowerCase();
            bVal = String(bVal).toLowerCase();
            if (aVal < bVal) return asc ? -1 : 1;
            if (aVal > bVal) return asc ? 1 : -1;
            return 0;
          });
          rows.forEach(function (row) { tbody.appendChild(row); });
        });
      });
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    renderCharts();
    bindFocusNav();
    bindSmartSearch();
    bindTableFilters();
    bindSortableTables();
    var shell = document.querySelector('.bi-dashboard');
    applyFocus(shell ? shell.getAttribute('data-focus') : 'summary');
  });
})();
