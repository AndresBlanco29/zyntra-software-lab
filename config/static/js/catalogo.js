
document.addEventListener('DOMContentLoaded', function() {
    const lang = document.body.dataset.lang.slice(0,2);
    const agregarUrl = document.body.dataset.agregarUrl;
    const csrfToken = document.body.dataset.csrf;
    const addedButtonLabel = document.body.dataset.msgAdded || 'Added ✔';

    const traducciones = {

        unidad: { es: "unidad", en: "unit" },
        unidades: { es: "unidades", en: "units" },

        litro: { es: "litro", en: "liter" },
        litros: { es: "litros", en: "liters" },

        gramo: { es: "gramo", en: "gram" },
        gramos: { es: "gramos", en: "grams" },

        caja: { es: "caja", en: "box" },
        cajas: { es: "cajas", en: "boxes" },

        pallet: { es: "pallet", en: "pallet" },
        pallets: { es: "pallets", en: "pallets" }

    };

    function submitCatalogFilters() {
        const form = document.getElementById('catalogo-filter-form');
        if (form) {
            form.submit();
        }
    }

    const buscador = document.getElementById('buscador');
    const filtroCategoria = document.getElementById('filtroCategoria');
    const filtroMarca = document.getElementById('filtroMarca');

    if (buscador && window.PreserveSearchFocus) {
        window.PreserveSearchFocus.bindDebouncedSearch(buscador, submitCatalogFilters);
    }

    if (filtroCategoria) {
        filtroCategoria.addEventListener('change', function () {
            let categoriaSeleccionada = this.value;
            let marcas = document.querySelectorAll('#filtroMarca option');

            marcas.forEach(function (marca) {
                let categoriaMarca = (marca.dataset.categoria || '').trim();
                let categoriasMarca = categoriaMarca ? categoriaMarca.split(/\s+/) : [];
                marca.style.display = (categoriaSeleccionada === '' || categoriasMarca.includes(categoriaSeleccionada)) ? '' : 'none';
            });

            submitCatalogFilters();
        });
    }

    if (filtroMarca) {
        filtroMarca.addEventListener('change', submitCatalogFilters);
    }

    if (filtroCategoria && filtroMarca) {
        let categoriaSeleccionada = filtroCategoria.value;
        document.querySelectorAll('#filtroMarca option').forEach(function (marca) {
            let categoriaMarca = (marca.dataset.categoria || '').trim();
            let categoriasMarca = categoriaMarca ? categoriaMarca.split(/\s+/) : [];
            marca.style.display = (categoriaSeleccionada === '' || categoriasMarca.includes(categoriaSeleccionada)) ? '' : 'none';
        });
    }

    /* BOTONES + - */
    document.querySelectorAll(".producto-card").forEach(card => {
        let cantidadSpan = card.querySelector(".cantidad");

        card.querySelector(".sumar").addEventListener("click", () => {
            cantidadSpan.textContent = parseInt(cantidadSpan.textContent) + 1;
        });

        card.querySelector(".restar").addEventListener("click", () => {
            let actual = parseInt(cantidadSpan.textContent);
            if (actual > 1) {
                cantidadSpan.textContent = actual - 1;
            }
        });
    });

    /* AGREGAR AL CARRITO */
    document.querySelectorAll(".agregar-btn").forEach(btn => {
        if (!btn.dataset.defaultLabel) {
            btn.dataset.defaultLabel = btn.textContent.trim();
        }

        btn.addEventListener("click", function () {
            const isAuthenticated = document.body.dataset.auth === 'true';

            if (!isAuthenticated) {
                const loginModal = new bootstrap.Modal(document.getElementById('loginModal'));
                loginModal.show();
                return;
            }

            let card = this.closest(".producto-card");

            let producto_id = card.dataset.productoId;
            let presentacion_id = card.querySelector(".presentacion-select").value;
            let cantidad = card.querySelector(".cantidad").textContent;

            fetch(agregarUrl, {
                method: "POST",
                headers: {
                    "X-CSRFToken": csrfToken,
                    "Content-Type": "application/x-www-form-urlencoded"
                },
                body: `producto_id=${producto_id}&presentacion_id=${presentacion_id}&cantidad=${cantidad}`
            })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        document.getElementById("contadorCarrito").textContent = data.total_items;

                        btn.textContent = addedButtonLabel;
                        setTimeout(() => {
                            btn.textContent = btn.dataset.defaultLabel;
                        }, 1200);
                    }
                });
        });
    });

    /* BOTÓN LOGIN PARA INVITADOS */
    document.querySelectorAll(".agregar-btn-login").forEach(btn => {
        btn.addEventListener("click", function () {
            const loginModal = new bootstrap.Modal(document.getElementById('loginModal'));
            loginModal.show();
        });
    });

    /* CAMBIAR TEXTO SEGÚN PRESENTACIÓN */
    document.querySelectorAll(".producto-card").forEach(card => {

        const select = card.querySelector(".presentacion-select");
        const infoTexto = card.querySelector(".info-presentacion");
        const priceText = card.querySelector(".product-price");

        select.addEventListener("change", function () {

            let option = this.options[this.selectedIndex];
            let summary = (option.dataset.summary || '').trim();

            if (summary) {
                infoTexto.textContent = summary;
            } else {
                let unidades = option.dataset.unidades;
                let tipo = (option.dataset.tipo || "").trim().toLowerCase();
                let nombre = option.text.trim().toLowerCase();
                let tipoTraducido = traducciones[tipo]?.[lang] || tipo;
                let por = lang === "en" ? "per" : "por";
                infoTexto.textContent = `${unidades} ${tipoTraducido} ${por} ${nombre}`;
            }

            if (priceText && option.dataset.price) {
                priceText.textContent = `$${option.dataset.price}`;
            }

        });

    });
});
