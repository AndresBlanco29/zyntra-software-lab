(function (window) {
    'use strict';

    var STORAGE_KEY = 'preserveSearchFocusState';
    var DEFAULT_DEBOUNCE_MS = 1000;
    var BOUND_ATTR = 'data-search-bound';
    var LAST_QUERY_ATTR = 'data-search-last-query';

    var EXCLUDED_INPUT_IDS = {
        panelSearchInput: true,
        buscadorProductoPedido: true,
        promoProductoBuscador: true,
        filtroProductoPromoBuscador: true,
        'bi-smart-input': true,
        directInvoiceProductSearch: true,
    };

    function normalizeSearchQuery(value) {
        return (value || '').replace(/^\s+|\s+$/g, '');
    }

    function shouldDeferSearch(value, force) {
        if (force) {
            return false;
        }
        // On mobile, a trailing space usually means the user is still typing the next word.
        return (value || '').endsWith(' ');
    }

    function rememberSearchFocus(input) {
        if (!input || !input.id) {
            return;
        }

        try {
            sessionStorage.setItem(STORAGE_KEY, JSON.stringify({
                id: input.id,
                start: typeof input.selectionStart === 'number' ? input.selectionStart : null,
                end: typeof input.selectionEnd === 'number' ? input.selectionEnd : null,
            }));
        } catch (error) {
            // sessionStorage may be unavailable in private mode.
        }
    }

    function restoreSearchFocus() {
        var raw;

        try {
            raw = sessionStorage.getItem(STORAGE_KEY);
        } catch (error) {
            return;
        }

        if (!raw) {
            return;
        }

        try {
            sessionStorage.removeItem(STORAGE_KEY);
        } catch (error) {
            // ignore
        }

        var data;

        try {
            data = JSON.parse(raw);
        } catch (error) {
            return;
        }

        var input = document.getElementById(data.id);
        if (!input) {
            return;
        }

        function applyFocus() {
            input.focus({ preventScroll: true });

            var length = (input.value || '').length;
            var start = typeof data.start === 'number' ? data.start : length;
            var end = typeof data.end === 'number' ? data.end : start;

            if (start > length) {
                start = length;
            }
            if (end > length) {
                end = length;
            }

            if (typeof input.setSelectionRange === 'function') {
                try {
                    input.setSelectionRange(start, end);
                } catch (error) {
                    // Some input types do not support selection ranges.
                }
            }
        }

        applyFocus();
        requestAnimationFrame(applyFocus);
    }

    function isExcludedSearchInput(input) {
        if (!input) {
            return true;
        }
        if (input.getAttribute(BOUND_ATTR) === 'true') {
            return true;
        }
        if (input.dataset.searchManual === 'true') {
            return true;
        }
        if (input.classList.contains('navbar-search-input')) {
            return true;
        }
        if (input.classList.contains('select2-search__field')) {
            return true;
        }
        if (input.classList.contains('qb-outbound-search')) {
            return true;
        }
        if (input.classList.contains('reports-table-filter')) {
            return true;
        }
        if (input.hasAttribute('data-picker-product-search')) {
            return true;
        }
        if (EXCLUDED_INPUT_IDS[input.id]) {
            return true;
        }
        if (input.closest('.ts-wrapper') || input.closest('.select2-container')) {
            return true;
        }
        return false;
    }

    function isSearchLikeInput(input) {
        if (!input || input.tagName !== 'INPUT') {
            return false;
        }
        if (input.type === 'search') {
            return true;
        }
        if (input.name === 'q') {
            return true;
        }
        if (input.dataset.searchAsYouType === 'true') {
            return true;
        }
        return false;
    }

    function trimSearchInputs(form) {
        if (!form) {
            return;
        }
        form.querySelectorAll('input[type="search"], input[name="q"]').forEach(function (input) {
            if (isSearchLikeInput(input)) {
                input.value = normalizeSearchQuery(input.value);
            }
        });
    }

    function bindSearchAsYouType(input, submitFn, options) {
        if (!input || typeof submitFn !== 'function' || isExcludedSearchInput(input)) {
            return;
        }

        options = options || {};
        var delay = typeof options.delayMs === 'number' ? options.delayMs : DEFAULT_DEBOUNCE_MS;
        var timer = null;

        input.setAttribute(BOUND_ATTR, 'true');

        function submitWithFocus(force) {
            var rawValue = input.value || '';
            if (shouldDeferSearch(rawValue, force)) {
                return;
            }

            var normalized = normalizeSearchQuery(rawValue);
            var lastQuery = input.getAttribute(LAST_QUERY_ATTR) || '';

            if (!force && normalized === lastQuery) {
                return;
            }

            input.setAttribute(LAST_QUERY_ATTR, normalized);
            rememberSearchFocus(input);
            submitFn(normalized, input);
        }

        input.addEventListener('input', function () {
            clearTimeout(timer);
            timer = setTimeout(function () {
                submitWithFocus(false);
            }, delay);
        });

        input.addEventListener('keydown', function (event) {
            if (event.key === 'Enter') {
                event.preventDefault();
                clearTimeout(timer);
                submitWithFocus(true);
            }
        });

        input.addEventListener('search', function () {
            clearTimeout(timer);
            submitWithFocus(true);
        });
    }

    function bindDebouncedInput(input, handler, delayMs) {
        if (!input || typeof handler !== 'function') {
            return;
        }

        var delay = typeof delayMs === 'number' ? delayMs : DEFAULT_DEBOUNCE_MS;
        var timer = null;

        input.addEventListener('input', function () {
            clearTimeout(timer);
            timer = setTimeout(function () {
                var rawValue = input.value || '';
                if (shouldDeferSearch(rawValue, false)) {
                    return;
                }
                handler(input, normalizeSearchQuery(rawValue));
            }, delay);
        });
    }

    function bindFormSearchInput(input, form) {
        bindSearchAsYouType(input, function () {
            trimSearchInputs(form);
            if (typeof form.requestSubmit === 'function') {
                form.requestSubmit();
            } else {
                form.submit();
            }
        });
    }

    function autoInitSearchForms() {
        document.querySelectorAll('form').forEach(function (form) {
            var method = (form.getAttribute('method') || 'get').toLowerCase();
            if (method === 'post' && form.dataset.searchAsYouTypeForm !== 'true') {
                return;
            }

            form.querySelectorAll('input[type="search"], input[name="q"]').forEach(function (input) {
                if (isExcludedSearchInput(input) || !isSearchLikeInput(input)) {
                    return;
                }
                bindFormSearchInput(input, form);
            });
        });

        document.querySelectorAll('input[data-search-as-you-type="true"]').forEach(function (input) {
            if (isExcludedSearchInput(input) || input.form) {
                return;
            }
            bindDebouncedInput(input, function (element, query) {
                element.dispatchEvent(new CustomEvent('search-as-you-type', {
                    bubbles: true,
                    detail: { query: query, normalizedQuery: query.toLowerCase() },
                }));
            });
        });
    }

    window.PreserveSearchFocus = {
        DEFAULT_DEBOUNCE_MS: DEFAULT_DEBOUNCE_MS,
        normalizeSearchQuery: normalizeSearchQuery,
        remember: rememberSearchFocus,
        restore: restoreSearchFocus,
        bindSearchAsYouType: bindSearchAsYouType,
        bindDebouncedSearch: bindSearchAsYouType,
        bindEnterOnlySearch: bindSearchAsYouType,
        bindDebouncedInput: bindDebouncedInput,
        autoInit: autoInitSearchForms,
    };

    document.addEventListener('DOMContentLoaded', function () {
        restoreSearchFocus();
        autoInitSearchForms();
    });
})(window);
