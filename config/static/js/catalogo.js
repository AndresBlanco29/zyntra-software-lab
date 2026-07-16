
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

        caja: { es: "CS", en: "CS" },
        cajas: { es: "CS", en: "CS" },
        box: { es: "CS", en: "CS" },
        boxes: { es: "CS", en: "CS" },

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
        window.PreserveSearchFocus.bindEnterOnlySearch(buscador, submitCatalogFilters);
    }

    function syncCatalogSearchStickyTop() {
        var nav = document.querySelector('.navbar-custom');
        var sticky = document.querySelector('.catalog-search-sticky');
        if (!nav || !sticky) {
            return;
        }
        sticky.style.top = nav.offsetHeight + 'px';
    }

    syncCatalogSearchStickyTop();
    window.addEventListener('resize', syncCatalogSearchStickyTop);
    if (window.visualViewport) {
        window.visualViewport.addEventListener('resize', syncCatalogSearchStickyTop);
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
        window.CatalogQuantity.bindLocalQuantityStepper(card);
    });

    function addCardToCart(card, feedbackButton, options = {}) {
        const isAuthenticated = document.body.dataset.auth === 'true';
        if (!isAuthenticated) {
            const loginModal = new bootstrap.Modal(document.getElementById('loginModal'));
            loginModal.show();
            return;
        }

        const productoId = card.dataset.productoId;
        const presentacionId = card.querySelector(".presentacion-select").value;
        const cantidad = window.CatalogQuantity.getQuantityValue(card.querySelector(".cantidad"));
        const defaultLabel = feedbackButton.textContent.trim();
        feedbackButton.disabled = true;

        const requestBody = new URLSearchParams({
            producto_id: productoId,
            presentacion_id: presentacionId,
            cantidad: cantidad,
        });
        if (options.ensurePromotionMinimum) {
            requestBody.set("promo_minimum", "1");
        }

        fetch(agregarUrl, {
                method: "POST",
                headers: {
                    "X-CSRFToken": csrfToken,
                    "Content-Type": "application/x-www-form-urlencoded"
                },
                body: requestBody.toString()
            })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        document.getElementById("contadorCarrito").textContent = data.total_items;
                        feedbackButton.textContent = addedButtonLabel;
                        setTimeout(() => {
                            feedbackButton.textContent = defaultLabel;
                            feedbackButton.disabled = false;
                        }, 1200);
                    } else {
                        feedbackButton.disabled = false;
                    }
                })
                .catch(() => {
                    feedbackButton.disabled = false;
                });
    }

    /* AGREGAR AL CARRITO */
    document.querySelectorAll(".agregar-btn").forEach(btn => {
        btn.addEventListener("click", function () {
            addCardToCart(this.closest(".producto-card"), this);
        });
    });

    /* AGREGAR LA CANTIDAD MÍNIMA DE LA PROMOCIÓN CON UN CLIC */
    document.querySelectorAll(".promo-add-btn").forEach(btn => {
        btn.addEventListener("click", function () {
            const card = this.closest(".producto-card");
            const minimum = parseInt(card.dataset.promoMinimum, 10);
            const promoPresentation = card.dataset.promoPresentation;
            const quantityInput = card.querySelector(".cantidad");
            const presentationSelect = card.querySelector(".presentacion-select");

            if (Number.isFinite(minimum) && minimum > 0) {
                quantityInput.value = String(minimum);
            }
            if (promoPresentation && presentationSelect) {
                presentationSelect.value = promoPresentation;
                presentationSelect.dispatchEvent(new Event("change", { bubbles: true }));
            }

            addCardToCart(card, this, { ensurePromotionMinimum: true });
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
