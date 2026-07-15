(function (window) {
    'use strict';

    var STORAGE_KEY = 'preserveSearchFocusState';

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

    function bindDebouncedSearch(input, submitFn, delayMs) {
        if (!input || typeof submitFn !== 'function') {
            return;
        }

        var delay = typeof delayMs === 'number' ? delayMs : 450;
        var timer = null;

        function submitWithFocus() {
            rememberSearchFocus(input);
            submitFn();
        }

        input.addEventListener('input', function () {
            clearTimeout(timer);
            timer = setTimeout(function () {
                // Keep trailing spaces while the user is typing multi-word queries.
                if ((input.value || '').endsWith(' ')) {
                    return;
                }
                submitWithFocus();
            }, delay);
        });

        input.addEventListener('keydown', function (event) {
            if (event.key === 'Enter') {
                event.preventDefault();
                clearTimeout(timer);
                submitWithFocus();
            }
        });
    }

    function bindEnterOnlySearch(input, submitFn) {
        if (!input || typeof submitFn !== 'function') {
            return;
        }

        function submitWithFocus() {
            rememberSearchFocus(input);
            submitFn();
        }

        input.addEventListener('keydown', function (event) {
            if (event.key === 'Enter') {
                event.preventDefault();
                submitWithFocus();
            }
        });
    }

    window.PreserveSearchFocus = {
        remember: rememberSearchFocus,
        restore: restoreSearchFocus,
        bindDebouncedSearch: bindDebouncedSearch,
        bindEnterOnlySearch: bindEnterOnlySearch,
    };

    document.addEventListener('DOMContentLoaded', restoreSearchFocus);
})(window);
