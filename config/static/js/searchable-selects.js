(function () {
  'use strict';

  var i18n = window.LTG_SEARCHABLE_SELECT_I18N || {
    placeholder: 'Type to search...',
    noResults: 'No results found',
  };

  function getTomSelectClass(select) {
    var classes = ['form-select'];
    if (select.classList.contains('form-select-sm')) {
      classes.push('form-select-sm');
    }
    return classes.join(' ');
  }

  function meaningfulOptionCount(select) {
    var count = 0;
    for (var i = 0; i < select.options.length; i++) {
      var option = select.options[i];
      if (option.value || (option.textContent || '').trim()) {
        count += 1;
      }
    }
    return count;
  }

  function shouldEnhance(select) {
    if (!select || select.tagName !== 'SELECT') {
      return false;
    }
    if (select.disabled || select.hidden) {
      return false;
    }
    if (select.dataset.noSearchSelect === 'true') {
      return false;
    }
    if (select.classList.contains('no-search-select')) {
      return false;
    }
    if (select.classList.contains('search-select-remote')) {
      return false;
    }
    if (select.closest('.ts-wrapper')) {
      return false;
    }
    if (typeof TomSelect !== 'undefined' && TomSelect.getInstance(select)) {
      return false;
    }
    if (meaningfulOptionCount(select) <= 2) {
      return false;
    }
    return true;
  }

  function buildPlugins(select) {
    var plugins = ['dropdown_input'];
    if (select.multiple) {
      plugins.unshift('remove_button');
    }
    return plugins;
  }

  function buildTomSelectOptions(select) {
    return {
      create: false,
      allowEmptyOption: true,
      maxOptions: null,
      openOnFocus: true,
      dropdownParent: 'body',
      sortField: { field: 'text', direction: 'asc' },
      placeholder: i18n.placeholder,
      plugins: buildPlugins(select),
      render: {
        no_results: function () {
          return '<div class="no-results px-3 py-2 text-muted">' + i18n.noResults + '</div>';
        },
      },
    };
  }

  function initSearchableSelect(select) {
    if (!shouldEnhance(select) || typeof TomSelect === 'undefined') {
      return null;
    }

    try {
      var instance = new TomSelect(select, buildTomSelectOptions(select));
      var wrapper = instance.wrapper;
      if (wrapper) {
        getTomSelectClass(select).split(' ').filter(Boolean).forEach(function (className) {
          wrapper.classList.add(className);
        });
      }
      select.dataset.searchSelectInitialized = 'true';
      return instance;
    } catch (error) {
      console.warn('LTGSearchableSelect: could not enhance select', select.name || select.id, error);
      return null;
    }
  }

  function initAll(root) {
    var scope = root && root.querySelectorAll ? root : document;
    scope.querySelectorAll('select').forEach(function (select) {
      initSearchableSelect(select);
    });
  }

  function destroySearchableSelect(select) {
    if (typeof TomSelect === 'undefined') {
      return;
    }
    var instance = TomSelect.getInstance(select);
    if (instance) {
      instance.destroy();
    }
    delete select.dataset.searchSelectInitialized;
  }

  window.LTGSearchableSelect = {
    init: initSearchableSelect,
    initAll: initAll,
    destroy: destroySearchableSelect,
  };

  document.addEventListener('DOMContentLoaded', function () {
    initAll(document);

    var observer = new MutationObserver(function (mutations) {
      mutations.forEach(function (mutation) {
        mutation.addedNodes.forEach(function (node) {
          if (!node || node.nodeType !== 1) {
            return;
          }
          if (node.tagName === 'SELECT') {
            initSearchableSelect(node);
          }
          if (node.querySelectorAll) {
            node.querySelectorAll('select').forEach(initSearchableSelect);
          }
        });
      });
    });

    observer.observe(document.body, { childList: true, subtree: true });
  });
})();
