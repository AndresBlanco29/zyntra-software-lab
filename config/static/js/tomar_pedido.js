document.addEventListener("DOMContentLoaded", function () {
    const form = document.getElementById("tomar-pedido-filter-form");
    const buscador = document.getElementById("buscadorCliente");

    if (!form || !buscador) {
        return;
    }

    function submitFilters() {
        form.submit();
    }

    if (window.PreserveSearchFocus) {
        window.PreserveSearchFocus.bindDebouncedSearch(buscador, submitFilters);
    }
});
