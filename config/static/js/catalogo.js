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

    function applyPromoDiscountsPanelPlacement(panel) {
        const useSidePanel = window.matchMedia('(min-width: 768px)').matches;
        panel.classList.remove('offcanvas-bottom', 'offcanvas-end');
        panel.classList.add(useSidePanel ? 'offcanvas-end' : 'offcanvas-bottom');
    }

    function getPromoTierCount(card) {
        const template = card.querySelector('.js-promo-discounts-template');
        if (!template || !template.content) {
            return 0;
        }
        return template.content.querySelectorAll('.js-promo-tier-option').length;
    }

    function applyPromoTierToCard(card, minimum) {
        const quantityInput = card.querySelector('.cantidad');
        const promoPresentation = card.dataset.promoPresentation;
        const presentationSelect = card.querySelector('.presentacion-select');

        if (quantityInput && Number.isFinite(minimum) && minimum > 0) {
            quantityInput.value = String(minimum);
        }
        if (promoPresentation && presentationSelect) {
            presentationSelect.value = promoPresentation;
            presentationSelect.dispatchEvent(new Event('change', { bubbles: true }));
        }
    }

    function markSelectedPromoTier(panelList, minimum) {
        panelList.querySelectorAll('.js-promo-tier-option').forEach(function (button) {
            const isSelected = parseInt(button.dataset.minimum, 10) === minimum;
            button.classList.toggle('is-selected', isSelected);
            button.setAttribute('aria-pressed', isSelected ? 'true' : 'false');
        });
    }

    function createPromoDiscountsPanelController(addToCartFn) {
        const panel = document.getElementById('promoDiscountsPanel');
        const panelTitle = document.getElementById('promoDiscountsPanelTitle');
        const panelHint = document.getElementById('promoDiscountsPanelHint');
        const panelDesc = document.getElementById('promoDiscountsPanelDesc');
        const panelList = document.getElementById('promoDiscountsPanelList');
        const viewButtons = document.querySelectorAll('.js-promo-discounts-btn');

        if (!panel || !panelTitle || !panelHint || !panelDesc || !panelList) {
            return {
                openForCard: function () {},
            };
        }

        const viewTitle = document.body.dataset.promoDiscountsTitle || 'Available discounts';
        const addTitle = document.body.dataset.promoAddSelectTitle || 'Which promotion would you like to add?';
        const viewHint = document.body.dataset.promoTierHint || 'Tap a discount to set the quantity.';
        const addHint = document.body.dataset.promoAddTierHint || 'Select a promotion to add it to your order.';

        let offcanvasInstance = null;
        let activeCard = null;
        let panelMode = 'view';
        let activeFeedbackButton = null;

        function getOffcanvasInstance() {
            if (!offcanvasInstance) {
                offcanvasInstance = bootstrap.Offcanvas.getOrCreateInstance(panel);
            }
            return offcanvasInstance;
        }

        function openForCard(card, options) {
            const template = card.querySelector('.js-promo-discounts-template');
            if (!template) {
                return;
            }

            activeCard = card;
            panelMode = options.mode || 'view';
            activeFeedbackButton = options.feedbackButton || null;

            const productName = (options.productName || card.dataset.nombre || '').trim();
            const baseTitle = panelMode === 'add' ? addTitle : viewTitle;
            panelTitle.textContent = productName ? baseTitle + ' · ' + productName : baseTitle;
            panelHint.textContent = panelMode === 'add' ? addHint : viewHint;
            panelHint.hidden = false;

            const description = (options.promoDescription || '').trim();
            if (description) {
                panelDesc.textContent = description;
                panelDesc.hidden = false;
            } else {
                panelDesc.textContent = '';
                panelDesc.hidden = true;
            }

            panelList.innerHTML = template.innerHTML.trim();
            const currentQty = window.CatalogQuantity.getQuantityValue(card.querySelector('.cantidad'));
            markSelectedPromoTier(panelList, currentQty);
            applyPromoDiscountsPanelPlacement(panel);
            getOffcanvasInstance().show();
        }

        panelList.addEventListener('click', function (event) {
            const tierButton = event.target.closest('.js-promo-tier-option');
            if (!tierButton || !activeCard) {
                return;
            }

            const minimum = parseInt(tierButton.dataset.minimum, 10);
            if (!Number.isFinite(minimum) || minimum < 1) {
                return;
            }

            applyPromoTierToCard(activeCard, minimum);
            markSelectedPromoTier(panelList, minimum);

            if (panelMode === 'add' && activeFeedbackButton) {
                getOffcanvasInstance().hide();
                addToCartFn(activeCard, activeFeedbackButton, { ensurePromotionMinimum: true });
                return;
            }

            getOffcanvasInstance().hide();
            activeCard.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        });

        viewButtons.forEach(function (button) {
            button.addEventListener('click', function () {
                const card = button.closest('.producto-card');
                if (!card) {
                    return;
                }
                openForCard(card, {
                    mode: 'view',
                    productName: button.dataset.productName || '',
                    promoDescription: button.dataset.promoDescription || '',
                });
            });
        });

        window.addEventListener('resize', function () {
            if (panel.classList.contains('show')) {
                applyPromoDiscountsPanelPlacement(panel);
            }
        });

        return { openForCard: openForCard };
    }

    function submitCatalogFilters() {
        const form = document.getElementById('catalogo-filter-form');
        if (form) {
            form.submit();
        }
    }

    const filtroCategoria = document.getElementById('filtroCategoria');
    const filtroMarca = document.getElementById('filtroMarca');

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
    const promoDiscountsPanel = createPromoDiscountsPanelController(addCardToCart);

    document.querySelectorAll(".promo-add-btn").forEach(btn => {
        btn.addEventListener("click", function () {
            if (this.classList.contains('js-combo-add-btn')) {
                return;
            }
            const card = this.closest(".producto-card");
            const tierCount = getPromoTierCount(card);

            if (tierCount > 1) {
                const viewBtn = card.querySelector('.js-promo-discounts-btn');
                promoDiscountsPanel.openForCard(card, {
                    mode: 'add',
                    feedbackButton: this,
                    productName: card.dataset.nombre || '',
                    promoDescription: viewBtn ? (viewBtn.dataset.promoDescription || '') : '',
                });
                return;
            }

            const minimum = parseInt(card.dataset.promoMinimum, 10);
            applyPromoTierToCard(card, minimum);
            addCardToCart(card, this, { ensurePromotionMinimum: true });
        });
    });

    /* CONSTRUCTOR DE COMBO (elegir cuántos de cada producto) */
    (function initComboBuilder() {
        const modalEl = document.getElementById('comboModal');
        if (!modalEl) {
            return;
        }
        const comboUrlTemplate = document.body.dataset.comboUrlTemplate || '';
        const titleEl = modalEl.querySelector('[data-role="combo-title"]');
        const hintEl = modalEl.querySelector('[data-role="combo-hint"]');
        const membersEl = modalEl.querySelector('[data-role="combo-members"]');
        const loadingEl = modalEl.querySelector('[data-role="combo-loading"]');
        const progressEl = modalEl.querySelector('[data-role="combo-progress"]');
        const addBtn = modalEl.querySelector('[data-role="combo-add"]');

        const needLabel = document.body.dataset.comboNeedLabel || 'units in total to unlock the discount';
        const totalLabel = document.body.dataset.comboTotalLabel || 'Combo total';
        const readyLabel = document.body.dataset.comboReadyLabel || 'The combo discount will be applied!';
        const missingLabel = document.body.dataset.comboMissingLabel || 'Add more units to reach the combo';
        const addLabel = document.body.dataset.comboAddLabel || 'Add combo to my order';
        const addedLabel = document.body.dataset.comboAddedLabel || 'Combo added ✔';
        const qtyLabel = document.body.dataset.comboQtyLabel || 'Quantity';
        const presentationLabel = document.body.dataset.comboPresentationLabel || 'Presentation';

        const totalInput = modalEl.querySelector('[data-role="combo-total-input"]');
        const distributeBtn = modalEl.querySelector('[data-role="combo-distribute-btn"]');
        const tiersEl = modalEl.querySelector('[data-role="combo-tiers"]');

        let modalInstance = null;
        let currentData = null;

        function getModal() {
            if (!modalInstance) {
                modalInstance = bootstrap.Modal.getOrCreateInstance(modalEl);
            }
            return modalInstance;
        }

        function esc(text) {
            return String(text == null ? '' : text)
                .replace(/&/g, '&amp;').replace(/</g, '&lt;')
                .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
        }

        function currentTotal() {
            let total = 0;
            membersEl.querySelectorAll('.combo-member__qty').forEach(function (input) {
                total += Math.max(0, parseInt(input.value, 10) || 0);
            });
            return total;
        }

        function refreshProgress() {
            if (!currentData) {
                return;
            }
            const total = currentTotal();
            const minimum = currentData.minimum || 0;
            const ready = total >= minimum && minimum > 0;
            const missing = Math.max(0, minimum - total);
            progressEl.classList.toggle('combo-modal__progress--ready', ready);
            if (ready) {
                progressEl.innerHTML = '<i class="bi bi-check-circle-fill"></i> ' +
                    esc(totalLabel) + ': <strong>' + total + '</strong> — ' + esc(readyLabel);
            } else {
                progressEl.innerHTML = '<i class="bi bi-exclamation-triangle-fill"></i> ' +
                    esc(totalLabel) + ': <strong>' + total + '</strong> / ' + minimum + ' ' +
                    esc(needLabel) + ' (' + esc(missingLabel) + ': <strong>' + missing + '</strong>)';
            }
            addBtn.disabled = !ready;
        }

        function distributeEqually(total) {
            const rows = Array.prototype.slice.call(membersEl.querySelectorAll('.combo-member'));
            const n = rows.length;
            if (!n || !(total > 0)) {
                return;
            }
            const base = Math.floor(total / n);
            let remainder = total - (base * n);
            rows.forEach(function (row) {
                const input = row.querySelector('.combo-member__qty');
                let qty = base;
                if (remainder > 0) {
                    qty += 1;
                    remainder -= 1;
                }
                input.value = String(qty);
            });
            refreshProgress();
        }

        function renderTiers(escalas) {
            if (!tiersEl) {
                return;
            }
            if (!escalas || !escalas.length) {
                tiersEl.innerHTML = '';
                return;
            }
            tiersEl.innerHTML = escalas.map(function (e) {
                return '<button type="button" class="combo-tier-chip" data-min="' + e.minimo + '">' +
                    '<span class="combo-tier-chip__min">' + e.minimo + '+</span>' +
                    '<span class="combo-tier-chip__benefit">' + esc(e.beneficio) + '</span>' +
                    '</button>';
            }).join('');
            tiersEl.querySelectorAll('.combo-tier-chip').forEach(function (chip) {
                chip.addEventListener('click', function () {
                    const min = parseInt(this.dataset.min, 10) || 0;
                    if (totalInput) {
                        totalInput.value = String(min);
                    }
                    tiersEl.querySelectorAll('.combo-tier-chip').forEach(function (c) {
                        c.classList.remove('combo-tier-chip--active');
                    });
                    this.classList.add('combo-tier-chip--active');
                    distributeEqually(min);
                });
            });
        }

        function renderMembers(data) {
            const rows = (data.miembros || []).map(function (miembro, index) {
                const options = (miembro.presentaciones || []).map(function (p) {
                    const priceTxt = (p.precio != null) ? ' — $' + p.precio : '';
                    return '<option value="' + p.id + '" data-price="' + (p.precio != null ? p.precio : '') + '">' +
                        esc(p.nombre) + priceTxt + '</option>';
                }).join('');
                return '' +
                '<div class="combo-member" data-producto-id="' + miembro.producto_id + '">' +
                    '<div class="combo-member__name"><i class="bi bi-check-circle-fill"></i> ' + esc(miembro.nombre) + '</div>' +
                    '<div class="combo-member__controls">' +
                        '<div class="combo-member__field">' +
                            '<label class="combo-member__label">' + esc(presentationLabel) + '</label>' +
                            '<select class="form-select form-select-sm combo-member__presentation">' + options + '</select>' +
                        '</div>' +
                        '<div class="combo-member__field combo-member__field--qty">' +
                            '<label class="combo-member__label">' + esc(qtyLabel) + '</label>' +
                            '<div class="combo-member__stepper">' +
                                '<button type="button" class="qty-btn combo-member__minus">-</button>' +
                                '<input type="number" min="0" step="1" value="0" inputmode="numeric" class="cantidad-input combo-member__qty">' +
                                '<button type="button" class="qty-btn combo-member__plus">+</button>' +
                            '</div>' +
                        '</div>' +
                    '</div>' +
                '</div>';
            }).join('');
            membersEl.innerHTML = rows;

            membersEl.querySelectorAll('.combo-member').forEach(function (row) {
                const input = row.querySelector('.combo-member__qty');
                row.querySelector('.combo-member__minus').addEventListener('click', function () {
                    input.value = String(Math.max(0, (parseInt(input.value, 10) || 0) - 1));
                    refreshProgress();
                });
                row.querySelector('.combo-member__plus').addEventListener('click', function () {
                    input.value = String(Math.max(0, (parseInt(input.value, 10) || 0) + 1));
                    refreshProgress();
                });
                input.addEventListener('input', refreshProgress);
            });
        }

        function openCombo(promoId) {
            const url = comboUrlTemplate.replace(/\/0\/miembros\/$/, '/' + promoId + '/miembros/');
            currentData = null;
            membersEl.innerHTML = '';
            progressEl.innerHTML = '';
            addBtn.disabled = true;
            addBtn.textContent = addLabel;
            loadingEl.hidden = false;
            getModal().show();

            fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' }, credentials: 'same-origin' })
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (data) {
                    loadingEl.hidden = true;
                    if (!data) {
                        membersEl.innerHTML = '<p class="text-danger">Error</p>';
                        return;
                    }
                    currentData = data;
                    if (titleEl) titleEl.textContent = data.nombre || (document.body.dataset.comboTitle || 'Build your combo');
                    if (hintEl) {
                        hintEl.textContent = (data.descripcion ? data.descripcion + ' · ' : '') +
                            (data.minimum || 0) + ' ' + needLabel + '.';
                    }
                    renderMembers(data);
                    renderTiers(data.escalas);
                    if (totalInput) {
                        totalInput.value = String(data.minimum || 0);
                    }
                    refreshProgress();
                })
                .catch(function () {
                    loadingEl.hidden = true;
                    membersEl.innerHTML = '<p class="text-danger">Error</p>';
                });
        }

        function submitCombo() {
            const rows = Array.prototype.slice.call(membersEl.querySelectorAll('.combo-member'));
            const payloads = [];
            rows.forEach(function (row) {
                const qty = Math.max(0, parseInt(row.querySelector('.combo-member__qty').value, 10) || 0);
                if (qty <= 0) {
                    return;
                }
                const select = row.querySelector('.combo-member__presentation');
                payloads.push({
                    producto_id: row.dataset.productoId,
                    presentacion_id: select.value,
                    cantidad: qty,
                });
            });
            if (!payloads.length) {
                return;
            }
            addBtn.disabled = true;

            const requests = payloads.map(function (p) {
                return fetch(agregarUrl, {
                    method: 'POST',
                    headers: { 'X-CSRFToken': csrfToken, 'Content-Type': 'application/x-www-form-urlencoded' },
                    body: new URLSearchParams(p).toString(),
                }).then(function (r) { return r.json(); });
            });

            Promise.all(requests).then(function (results) {
                let lastTotal = null;
                results.forEach(function (data) {
                    if (data && typeof data.total_items !== 'undefined') {
                        lastTotal = data.total_items;
                    }
                });
                if (lastTotal != null) {
                    syncMyOrderAttention(lastTotal);
                }
                addBtn.textContent = addedLabel;
                setTimeout(function () {
                    getModal().hide();
                    addBtn.textContent = addLabel;
                }, 900);
            }).catch(function () {
                addBtn.disabled = false;
            });
        }

        addBtn.addEventListener('click', submitCombo);

        if (distributeBtn) {
            distributeBtn.addEventListener('click', function () {
                distributeEqually(parseInt(totalInput ? totalInput.value : '0', 10) || 0);
            });
        }

        document.querySelectorAll('.js-combo-add-btn').forEach(function (btn) {
            btn.addEventListener('click', function () {
                const isAuthenticated = document.body.dataset.auth === 'true';
                if (!isAuthenticated) {
                    const loginModal = new bootstrap.Modal(document.getElementById('loginModal'));
                    loginModal.show();
                    return;
                }
                const promoId = this.dataset.promoId;
                if (promoId) {
                    openCombo(promoId);
                }
            });
        });
    })();

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
