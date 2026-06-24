(function (window) {
    function normalizeQuantityInput(input, min = 1) {
        if (!input) {
            return min;
        }

        const parsed = parseInt(input.value, 10);
        const value = Number.isFinite(parsed) && parsed >= min ? parsed : min;
        input.value = String(value);
        return value;
    }

    function getQuantityValue(input, min = 1) {
        if (!input) {
            return min;
        }

        const parsed = parseInt(input.value, 10);
        return Number.isFinite(parsed) && parsed >= min ? parsed : min;
    }

    function bindLocalQuantityStepper(container, selector = '.cantidad') {
        const input = container.querySelector(selector);
        if (!input) {
            return;
        }

        const sumar = container.querySelector('.sumar');
        const restar = container.querySelector('.restar');

        if (sumar) {
            sumar.addEventListener('click', function () {
                input.value = String(getQuantityValue(input) + 1);
            });
        }

        if (restar) {
            restar.addEventListener('click', function () {
                const current = getQuantityValue(input);
                if (current > 1) {
                    input.value = String(current - 1);
                }
            });
        }

        input.addEventListener('blur', function () {
            normalizeQuantityInput(input);
        });

        input.addEventListener('change', function () {
            normalizeQuantityInput(input);
        });
    }

    window.CatalogQuantity = {
        normalizeQuantityInput,
        getQuantityValue,
        bindLocalQuantityStepper,
    };
})(window);
