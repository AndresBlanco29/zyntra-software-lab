document.addEventListener("DOMContentLoaded", function () {
    const form = document.getElementById("tomar-pedido-filter-form");
    const buscador = document.getElementById("buscadorCliente");
    let searchTimer = null;

    if (!form || !buscador) {
        return;
    }

    function submitFilters() {
        form.submit();
    }

    buscador.addEventListener("input", function () {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(submitFilters, 450);
    });

    buscador.addEventListener("keydown", function (event) {
        if (event.key === "Enter") {
            event.preventDefault();
            clearTimeout(searchTimer);
            submitFilters();
        }
    });
});
