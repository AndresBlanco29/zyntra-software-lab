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

    function pad2(value) {
        return String(Math.max(0, value)).padStart(2, '0');
    }

    function syncMyOrderAttention(totalItems) {
        const count = Math.max(0, parseInt(totalItems, 10) || 0);
        document.body.dataset.cartCount = String(count);

        const counter = document.getElementById('contadorCarrito');
        if (counter) {
            counter.textContent = String(count);
        }

        document.querySelectorAll('.js-cart-badge').forEach(function (badge) {
            badge.textContent = String(count);
            badge.classList.toggle('d-none', count < 1);
        });

        const onMyOrderPage = document.body.dataset.page === 'my-order';
        document.querySelectorAll('.js-my-order-cta').forEach(function (el) {
            el.classList.toggle('my-order-cta--attention', count > 0 && !onMyOrderPage);
        });
    }

    function formatCountdown(remainingMs) {
        const totalSeconds = Math.max(0, Math.floor(remainingMs / 1000));
        const days = Math.floor(totalSeconds / 86400);
        const hours = Math.floor((totalSeconds % 86400) / 3600);
        const minutes = Math.floor((totalSeconds % 3600) / 60);
        const seconds = totalSeconds % 60;
        const prefix = document.body.dataset.countdownPrefix || 'Promotion ends in:';
        const daysLabel = document.body.dataset.countdownDays || 'days';
        const hoursLabel = document.body.dataset.countdownHours || 'hours';
        const minutesLabel = document.body.dataset.countdownMinutes || 'minutes';
        const secondsLabel = document.body.dataset.countdownSeconds || 'seconds';
        return prefix + ' ' +
            pad2(days) + ' ' + daysLabel + ' · ' +
            pad2(hours) + ' ' + hoursLabel + ' · ' +
            pad2(minutes) + ' ' + minutesLabel + ' · ' +
            pad2(seconds) + ' ' + secondsLabel;
    }

    function tickPromoCountdowns() {
        const endedLabel = document.body.dataset.countdownEnded || 'Promotion ended';
        document.querySelectorAll('.js-promo-countdown').forEach(function (el) {
            const endsAt = Date.parse(el.dataset.endsAt || '');
            const textEl = el.querySelector('.promo-countdown__text');
            if (!textEl || Number.isNaN(endsAt)) {
                el.hidden = true;
                return;
            }
            const remaining = endsAt - Date.now();
            if (remaining <= 0) {
                el.classList.add('promo-countdown--ended');
                textEl.textContent = endedLabel;
                return;
            }
            el.classList.remove('promo-countdown--ended');
            textEl.textContent = formatCountdown(remaining);
        });
    }

    function initPromoCountdowns() {
        if (!document.querySelector('.js-promo-countdown')) {
            return;
        }
        tickPromoCountdowns();
        window.setInterval(tickPromoCountdowns, 1000);
    }

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
                        syncMyOrderAttention(data.total_items);
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

    syncMyOrderAttention(document.body.dataset.cartCount || 0);
    initPromoCountdowns();
});
