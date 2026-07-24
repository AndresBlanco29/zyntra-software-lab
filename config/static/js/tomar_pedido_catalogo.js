
document.addEventListener('DOMContentLoaded', function() {
    const agregarUrl = document.body.dataset.agregarUrl;
    const csrfToken = document.body.dataset.csrf;
    const mobilePriceMediaQuery = window.matchMedia('(max-width: 767.98px)');
    const precioSelectorModalElement = document.getElementById('modalPrecioSelector');
    const precioSelectorLabel = document.getElementById('modalPrecioSelectorLabel');
    const precioSelectorOptions = document.getElementById('modalPrecioOpciones');
    const modalPrecioManualInput = document.getElementById('modalPrecioManualInput');
    const modalPrecioManualMargin = document.getElementById('modalPrecioManualMargin');
    const modalPrecioManualApply = document.getElementById('modalPrecioManualApply');
    const precioSelectorModal = precioSelectorModalElement ? new bootstrap.Modal(precioSelectorModalElement) : null;
    const longPressDelay = 1200;
    let activePrecioCard = null;
    const bulkPriceTierSelect = document.getElementById('bulkPriceTierSelect');
    const applyBulkPriceTierButton = document.getElementById('applyBulkPriceTierButton');
    const bulkPriceTierStorageKey = `vendedor_catalog_bulk_price_tier_${document.body.dataset.clienteId || 'default'}`;
    let activeBulkPriceTier = '';
    const marginLabel = document.body.dataset.labelMargin || 'Margin';

    function getPriceMarginTiers() {
        const raw = document.body.dataset.priceMargins || '';
        return raw.split(',').map(value => parseFloat(value)).filter(value => Number.isFinite(value));
    }

    function getCardCost(card) {
        return parseFloat(card?.dataset.costo || '0') || 0;
    }

    function calculateMarginPercent(cost, price) {
        const costValue = parseFloat(cost);
        const priceValue = parseFloat(price);
        if (!Number.isFinite(costValue) || !Number.isFinite(priceValue) || costValue <= 0 || priceValue <= 0) {
            return null;
        }
        if (priceValue <= costValue) {
            return 0;
        }
        return ((priceValue - costValue) / priceValue) * 100;
    }

    function formatMarginPercent(value) {
        if (value === null || value === undefined || Number.isNaN(value)) {
            return '';
        }
        return `${value.toFixed(1)}%`;
    }

    function resolveOptionMargin(option, cost) {
        const configuredMargin = parseFloat(option?.dataset.margin || '');
        if (Number.isFinite(configuredMargin)) {
            return configuredMargin;
        }
        return calculateMarginPercent(cost, option?.value);
    }

    function buildMarginLabel(marginValue) {
        const formatted = formatMarginPercent(marginValue);
        return formatted ? `${marginLabel}: ${formatted}` : '';
    }

    function removeManualPriceOption(precioSelect) {
        const manualOption = precioSelect?.querySelector('option[data-price-key="manual"]');
        if (manualOption) {
            manualOption.remove();
        }
    }

    function setManualPriceOnCard(card, rawPrice, { closeModal = false } = {}) {
        const precioSelect = card?.querySelector('.precio-select');
        if (!precioSelect) {
            return false;
        }

        const priceValue = parseFloat(rawPrice);
        if (!Number.isFinite(priceValue) || priceValue <= 0) {
            return false;
        }

        const normalizedPrice = priceValue.toFixed(2);
        removeManualPriceOption(precioSelect);

        const manualOption = document.createElement('option');
        manualOption.value = normalizedPrice;
        manualOption.dataset.priceKey = 'manual';
        manualOption.textContent = `${document.body.dataset.labelManualPrice || 'Manual price'} - $${normalizedPrice}`;
        precioSelect.appendChild(manualOption);
        precioSelect.value = normalizedPrice;
        precioSelect.dispatchEvent(new Event('change', { bubbles: true }));

        const manualInput = card.querySelector('.precio-manual-input');
        if (manualInput) {
            manualInput.value = normalizedPrice;
        }

        syncPrecioMask(card);
        updatePrecioMarginHint(card);

        if (closeModal && precioSelectorModal) {
            precioSelectorModal.hide();
        }

        return true;
    }

    function updatePrecioMarginHint(card) {
        const precioSelect = card?.querySelector('.precio-select');
        const hint = card?.querySelector('.precio-margin-hint');
        if (!precioSelect || !hint) {
            return;
        }

        const selectedOption = precioSelect.selectedOptions[0];
        if (!selectedOption || !selectedOption.value) {
            hint.textContent = '';
            hint.classList.add('d-none');
            return;
        }

        const marginValue = resolveOptionMargin(selectedOption, getCardCost(card));
        const marginText = buildMarginLabel(marginValue);
        if (!marginText) {
            hint.textContent = '';
            hint.classList.add('d-none');
            return;
        }

        hint.textContent = marginText;
        hint.classList.remove('d-none');
    }

    function updateModalManualMarginPreview() {
        if (!modalPrecioManualMargin || !activePrecioCard) {
            return;
        }

        const marginValue = calculateMarginPercent(getCardCost(activePrecioCard), modalPrecioManualInput?.value || '');
        const marginText = buildMarginLabel(marginValue);
        if (!marginText) {
            modalPrecioManualMargin.textContent = '';
            modalPrecioManualMargin.classList.add('d-none');
            return;
        }

        modalPrecioManualMargin.textContent = marginText;
        modalPrecioManualMargin.classList.remove('d-none');
    }

    function clientModeHelpers() {
        return window.LTGTakeOrderClientMode || null;
    }

    function isClientModeEnabled() {
        const helpers = clientModeHelpers();
        return helpers ? helpers.isEnabled() : document.body.classList.contains('client-mode');
    }

    function buildTierOptionLabel(tierNumber, precio) {
        const helpers = clientModeHelpers();
        if (helpers) {
            return helpers.buildTierOptionLabel(tierNumber, precio);
        }
        return `Precio ${tierNumber} - $${precio}`;
    }

    function rebuildPrecioOptions(card, optionData) {
        const precioSelect = card.querySelector('.precio-select');
        const marginTiers = getPriceMarginTiers();
        if (!precioSelect) {
            return;
        }

        precioSelect.innerHTML = '<option value="">Seleccionar precio</option>';
        precioSelect.value = '';

        const precios = [
            optionData.precio1,
            optionData.precio2,
            optionData.precio3,
            optionData.precio4,
            optionData.precio5,
        ];

        precios.forEach((precio, index) => {
            if (precio && precio !== '0.00') {
                const priceKey = `precio_${index + 1}`;
                const margin = marginTiers[index];
                const marginAttr = Number.isFinite(margin) ? ` data-margin="${margin}"` : '';
                const marginSuffix = Number.isFinite(margin) ? ` (${formatMarginPercent(margin)})` : '';
                precioSelect.innerHTML += `
                    <option value="${precio}" data-price-key="${priceKey}"${marginAttr}>
                    ${buildTierOptionLabel(index + 1, precio)}${isClientModeEnabled() ? '' : marginSuffix}
                    </option>
                `;
            }
        });

        if (isClientModeEnabled()) {
            const helpers = clientModeHelpers();
            if (helpers) {
                helpers.applyPresentation(true);
            }
        }

        const manualInput = card.querySelector('.precio-manual-input');
        if (manualInput) {
            manualInput.value = '';
        }
    }

    function getStoredBulkPriceTier() {
        try {
            return window.sessionStorage.getItem(bulkPriceTierStorageKey) || '';
        } catch (error) {
            return '';
        }
    }

    function storeBulkPriceTier(priceKey) {
        activeBulkPriceTier = priceKey || '';
        try {
            if (activeBulkPriceTier) {
                window.sessionStorage.setItem(bulkPriceTierStorageKey, activeBulkPriceTier);
            } else {
                window.sessionStorage.removeItem(bulkPriceTierStorageKey);
            }
        } catch (error) {
            // Ignore storage errors and keep the in-memory selection.
        }
    }

    function applyPriceTierToCard(card, priceKey) {
        if (!priceKey || !card) {
            return;
        }

        const precioSelect = card.querySelector('.precio-select');
        if (!precioSelect) {
            return;
        }

        const matchingOption = precioSelect.querySelector(`option[data-price-key="${priceKey}"]`);
        if (!matchingOption || !matchingOption.value) {
            return;
        }

        precioSelect.value = matchingOption.value;
        precioSelect.dispatchEvent(new Event('change', { bubbles: true }));
        syncPrecioMask(card);
    }

    function applyBulkPriceTierToAllCards(priceKey) {
        document.querySelectorAll('.producto-card').forEach(card => {
            applyPriceTierToCard(card, priceKey);
        });
    }

    function restoreBulkPriceTierSelection() {
        const storedPriceKey = getStoredBulkPriceTier();
        if (!storedPriceKey) {
            return;
        }

        activeBulkPriceTier = storedPriceKey;
        if (bulkPriceTierSelect) {
            bulkPriceTierSelect.value = storedPriceKey;
        }
        applyBulkPriceTierToAllCards(storedPriceKey);
    }

    function syncPrecioMask(card) {
        const shell = card.querySelector('.precio-select-shell');
        const precioSelect = card.querySelector('.precio-select');
        const mask = card.querySelector('.precio-select-mask');

        if (!shell || !precioSelect || !mask) {
            return;
        }

        const defaultLabel = shell.dataset.labelDefault || 'Presiona para elegir precio';
        const selectedLabel = shell.dataset.labelSelected || 'Precio seleccionado';
        const hasSelectedPrice = Boolean(precioSelect.value);
        const selectedOption = precioSelect.selectedOptions[0];
        const marginValue = hasSelectedPrice ? resolveOptionMargin(selectedOption, getCardCost(card)) : null;
        const marginSuffix = buildMarginLabel(marginValue);

        shell.dataset.state = hasSelectedPrice ? 'selected' : 'empty';
        if (hasSelectedPrice) {
            const helpers = clientModeHelpers();
            if (helpers) {
                mask.textContent = helpers.buildMaskLabel(precioSelect.value, marginSuffix);
            } else if (marginSuffix) {
                mask.textContent = `$${precioSelect.value} · ${marginSuffix}`;
            } else {
                mask.textContent = `${selectedLabel}: $${precioSelect.value}`;
            }
        } else {
            mask.textContent = defaultLabel;
        }
        updatePrecioMarginHint(card);
    }

    function setPrecioMaskOpen(select, isOpen) {
        const shell = select.closest('.precio-select-shell');

        if (!shell) {
            return;
        }

        shell.dataset.open = isOpen ? 'true' : 'false';
    }

    function isMobilePriceMode() {
        return mobilePriceMediaQuery.matches;
    }

    function clearLongPress(card) {
        if (!card) {
            return;
        }

        const shell = card.querySelector('.precio-select-shell');
        const timerId = Number(card.dataset.longPressTimer || '0');

        if (timerId) {
            window.clearTimeout(timerId);
        }

        delete card.dataset.longPressTimer;

        if (shell) {
            shell.dataset.pressing = 'false';
        }
    }

    function openPrecioModal(card) {
        if (!precioSelectorModal || !precioSelectorOptions) {
            return;
        }

        const shell = card.querySelector('.precio-select-shell');
        const precioSelect = card.querySelector('.precio-select');
        const productName = card.dataset.nombre || '';

        if (!shell || !precioSelect) {
            return;
        }

        const options = Array.from(precioSelect.options).filter(option => option.value);

        if (!options.length) {
            return;
        }

        activePrecioCard = card;
        precioSelectorLabel.textContent = shell.dataset.pickerTitle || 'Seleccionar precio';
        const cost = getCardCost(card);
        precioSelectorOptions.innerHTML = options.map(option => {
            const isSelected = option.value === precioSelect.value ? ' precio-modal-option--selected' : '';
            const marginValue = resolveOptionMargin(option, cost);
            const marginMarkup = marginValue === null || isClientModeEnabled()
                ? ''
                : `<span class="precio-modal-option__margin">${buildMarginLabel(marginValue)}</span>`;
            const optionTitle = isClientModeEnabled()
                ? ((clientModeHelpers() && clientModeHelpers().formatPriceOnly(option.value)) || `$${option.value}`)
                : option.text;
            return `
                <button type="button" class="precio-modal-option${isSelected}" data-value="${option.value}">
                    <span class="precio-modal-option__title">${optionTitle}</span>
                    ${marginMarkup}
                    <span class="precio-modal-option__product">${isClientModeEnabled() ? '' : productName}</span>
                </button>
            `;
        }).join('');

        if (modalPrecioManualInput) {
            const manualInput = card.querySelector('.precio-manual-input');
            modalPrecioManualInput.value = manualInput?.value || '';
        }
        updateModalManualMarginPreview();

        precioSelectorModal.show();
    }

    function startLongPress(card) {
        if (!isMobilePriceMode()) {
            return;
        }

        clearLongPress(card);

        const shell = card.querySelector('.precio-select-shell');
        if (shell) {
            shell.dataset.pressing = 'true';
        }

        const timerId = window.setTimeout(function () {
            if (shell) {
                shell.dataset.pressing = 'false';
            }

            delete card.dataset.longPressTimer;
            openPrecioModal(card);
        }, longPressDelay);

        card.dataset.longPressTimer = String(timerId);
    }

    function submitCatalogFilters() {
        const form = document.getElementById('catalogo-vendedor-filter-form');
        if (form) {
            if (buscador && window.PreserveSearchFocus) {
                window.PreserveSearchFocus.remember(buscador);
            }
            form.submit();
        }
    }

    const buscador = document.getElementById('buscador');
    const filtroCategoria = document.getElementById('filtroCategoria');
    const filtroMarca = document.getElementById('filtroMarca');
    const clearSearchButton = document.getElementById('catalogoSearchClear');
    const filterForm = document.getElementById('catalogo-vendedor-filter-form');

    function syncClearSearchVisibility() {
        if (!buscador || !clearSearchButton) {
            return;
        }
        clearSearchButton.classList.toggle('d-none', !(buscador.value || '').length);
    }

    if (buscador) {
        buscador.addEventListener('input', syncClearSearchVisibility);
        buscador.addEventListener('keydown', function (event) {
            if (event.key === 'Enter') {
                event.preventDefault();
                submitCatalogFilters();
            }
        });
    }

    if (clearSearchButton && buscador) {
        clearSearchButton.addEventListener('click', function () {
            buscador.value = '';
            syncClearSearchVisibility();
            buscador.focus({ preventScroll: true });
            submitCatalogFilters();
        });
    }

    if (filterForm) {
        filterForm.addEventListener('submit', function () {
            if (buscador && window.PreserveSearchFocus) {
                window.PreserveSearchFocus.remember(buscador);
            }
        });
    }

    syncClearSearchVisibility();

    if (filtroCategoria) {
        filtroCategoria.addEventListener('change', function () {
            let categoriaSeleccionada = this.value;
            let marcas = document.querySelectorAll('#filtroMarca option');

            marcas.forEach(function (marca) {
                let categoriaMarca = marca.dataset.categoria;
                marca.style.display = (categoriaSeleccionada === '' || categoriaMarca === categoriaSeleccionada) ? '' : 'none';
            });

            submitCatalogFilters();
        });
    }

    if (filtroMarca) {
        filtroMarca.addEventListener('change', submitCatalogFilters);
    }

    if (applyBulkPriceTierButton && bulkPriceTierSelect) {
        applyBulkPriceTierButton.addEventListener('click', function () {
            const selectedPriceKey = bulkPriceTierSelect.value;
            if (!selectedPriceKey) {
                return;
            }

            storeBulkPriceTier(selectedPriceKey);
            applyBulkPriceTierToAllCards(selectedPriceKey);
        });
    }

    restoreBulkPriceTierSelection();

    function syncOrderHistory(card, select) {
        const historyLabel = card.querySelector('[data-order-history-current-label]');
        const selectedOption = select?.selectedOptions?.[0];
        const selectedPresentationId = selectedOption?.value || '';

        if (historyLabel && selectedOption) {
            historyLabel.textContent = selectedOption.textContent.trim();
        }

        card.querySelectorAll('[data-order-history-list]').forEach(historyBlock => {
            historyBlock.classList.toggle('d-none', historyBlock.dataset.presentacionId !== selectedPresentationId);
        });
    }

    /* filtros dinámicos de opciones de marca (visual antes del submit) */
    if (filtroCategoria && filtroMarca) {
        let categoriaSeleccionada = filtroCategoria.value;
        document.querySelectorAll('#filtroMarca option').forEach(function (marca) {
            let categoriaMarca = marca.dataset.categoria;
            marca.style.display = (categoriaSeleccionada === '' || categoriaMarca === categoriaSeleccionada) ? '' : 'none';
        });
    }

    /* BOTONES + - */
    document.querySelectorAll(".producto-card").forEach(card => {
        window.CatalogQuantity.bindLocalQuantityStepper(card);
        const precioSelect = card.querySelector('.precio-select');
        const precioHoldTrigger = card.querySelector('.precio-hold-trigger');

        syncPrecioMask(card);

        if (precioSelect) {
            precioSelect.addEventListener('focus', function () {
                setPrecioMaskOpen(this, true);
            });

            precioSelect.addEventListener('blur', function () {
                setPrecioMaskOpen(this, false);
            });

            precioSelect.addEventListener('change', function () {
                const manualInput = card.querySelector('.precio-manual-input');
                const selectedOption = precioSelect.selectedOptions[0];
                if (manualInput && selectedOption?.dataset.priceKey !== 'manual') {
                    manualInput.value = '';
                }
                syncPrecioMask(card);
            });
        }

        const manualInput = card.querySelector('.precio-manual-input');
        const manualApplyButton = card.querySelector('.precio-manual-apply');
        if (manualApplyButton && manualInput) {
            manualApplyButton.addEventListener('click', function () {
                setManualPriceOnCard(card, manualInput.value);
            });
            manualInput.addEventListener('keydown', function (event) {
                if (event.key === 'Enter') {
                    event.preventDefault();
                    setManualPriceOnCard(card, manualInput.value);
                }
            });
        }

        updatePrecioMarginHint(card);

        if (precioHoldTrigger) {
            ['pointerdown', 'touchstart', 'mousedown'].forEach(eventName => {
                precioHoldTrigger.addEventListener(eventName, function (event) {
                    if (!isMobilePriceMode()) {
                        return;
                    }

                    event.preventDefault();
                    startLongPress(card);
                }, { passive: false });
            });

            ['pointerup', 'pointerleave', 'pointercancel', 'touchend', 'touchcancel', 'mouseup'].forEach(eventName => {
                precioHoldTrigger.addEventListener(eventName, function () {
                    clearLongPress(card);
                });
            });

            precioHoldTrigger.addEventListener('click', function (event) {
                if (!isMobilePriceMode()) {
                    return;
                }

                event.preventDefault();
            });
        }
    });

    /* AGREGAR AL CARRITO */
    document.querySelectorAll(".agregar-btn").forEach(btn => {
        btn.addEventListener("click", function () {

            let card = this.closest(".producto-card");

            let producto_id = card.dataset.productoId;
            let presentacion_id = card.querySelector(".presentacion-select").value;
            let cantidad = window.CatalogQuantity.getQuantityValue(card.querySelector(".cantidad"));
            let precioSelect = card.querySelector(".precio-select");
            let precio = precioSelect.value;
            const selectedPriceOption = precioSelect.selectedOptions[0];
            const precioKey = selectedPriceOption?.dataset.priceKey || '';

            console.log("DEBUG - Intento agregar:", { precio, precioKey, presentacion_id, cantidad });

            // VALIDACIÓN: Si no hay precio seleccionado, mostrar modal y DETENER
            if (!precio || precio === "" || precio === null) {
                console.log("DEBUG - Precio vacío, mostrando modal");
                const modalPrecio = new bootstrap.Modal(document.getElementById('modalPrecioRequerido'));
                modalPrecio.show();
                return; // DETIENE COMPLETAMENTE
            }

            console.log("DEBUG - Precio válido, enviando fetch...");

            fetch(agregarUrl, {
                method: "POST",
                headers: {
                    "X-CSRFToken": csrfToken,
                    "Content-Type": "application/x-www-form-urlencoded"
                },
                body: `producto_id=${producto_id}&presentacion_id=${presentacion_id}&cantidad=${cantidad}&precio=${precio}&precio_key=${encodeURIComponent(precioKey)}`
            })
            .then(response => {
                console.log("DEBUG - Respuesta del servidor:", response.status);
                
                if (!response.ok) {
                    console.log("DEBUG - Error 400, mostrando modal");
                    const modalPrecio = new bootstrap.Modal(document.getElementById('modalPrecioRequerido'));
                    modalPrecio.show();
                    return Promise.reject("Error: " + response.status);
                }
                return response.json();
            })
            .then(data => {
                console.log("DEBUG - Datos recibidos:", data);
                
                if (data.success) {
                    console.log("DEBUG - Actualizar UI");
                    document.getElementById("contadorPedido").textContent = data.total_items;
                    document.getElementById("pedidoCantidad").textContent = data.total_items;

                    btn.textContent = document.body.dataset.msgAdded || "Added ✔";
                    setTimeout(() => {
                        btn.textContent = document.body.dataset.msgAddDefault || "Add to Order";
                    }, 1200);

                    animarNumero(
                        document.getElementById("pedidoTotal"),
                        data.total
                    );

                    const barra = document.querySelector(".pedido-bar");
                    barra.style.transform = "scale(1.03)";
                    setTimeout(()=>{
                        barra.style.transform = "scale(1)";
                    }, 200);
                } else {
                    console.log("DEBUG - success=false, no actualizar");
                }
            })
            .catch(error => {
                console.error("DEBUG - Error capturado:", error);
                // El modal ya se mostró en el then anterior
            });

            function animarNumero(elemento, nuevoValor){
                let inicio = parseFloat(elemento.textContent) || 0;
                let fin = parseFloat(nuevoValor);

                console.log("DEBUG - Animando de", inicio, "a", fin);

                let duracion = 300;
                let paso = (fin - inicio) / (duracion / 16);
                let contador = inicio;

                let intervalo = setInterval(()=>{
                    contador += paso;

                    if((paso > 0 && contador >= fin) || (paso < 0 && contador <= fin)){
                        contador = fin;
                        clearInterval(intervalo);
                    }

                    elemento.textContent = contador.toFixed(2);
                }, 16);
            }

        });
    });

    /* CAMBIAR TEXTO SEGÚN PRESENTACIÓN */
    document.querySelectorAll(".producto-card").forEach(card => {
        const select = card.querySelector(".presentacion-select");
        const infoTexto = card.querySelector(".info-presentacion");

        select.addEventListener("change", function () {
            const option = this.selectedOptions[0];
            const summary = (option.dataset.summary || '').trim();

            if (summary) {
                infoTexto.textContent = summary;
            } else {
                const unidades = option.dataset.unidades;
                const tipo = option.dataset.tipo;
                const nombre = option.text;
                infoTexto.textContent = unidades + " " + tipo + " por " + nombre.toLowerCase();
            }
            syncOrderHistory(card, this);

            card.dataset.costo = option.dataset.costo || '0';
            rebuildPrecioOptions(card, {
                precio1: option.dataset.precio1,
                precio2: option.dataset.precio2,
                precio3: option.dataset.precio3,
                precio4: option.dataset.precio4,
                precio5: option.dataset.precio5,
            });

            if (activeBulkPriceTier) {
                applyPriceTierToCard(card, activeBulkPriceTier);
            } else {
                const shell = card.querySelector('.precio-select-shell');
                const mask = card.querySelector('.precio-select-mask');

                if (shell) {
                    shell.dataset.state = 'empty';
                    shell.dataset.open = 'false';
                    shell.dataset.pressing = 'false';
                }

                if (mask) {
                    mask.textContent = shell?.dataset.labelDefault || 'Mantén oprimido 1 segundo para elegir precio';
                }
                updatePrecioMarginHint(card);
            }
        });

        syncOrderHistory(card, select);
    });

    if (precioSelectorOptions) {
        precioSelectorOptions.addEventListener('click', function (event) {
            const optionButton = event.target.closest('.precio-modal-option');

            if (!optionButton || !activePrecioCard) {
                return;
            }

            const precioSelect = activePrecioCard.querySelector('.precio-select');

            if (!precioSelect) {
                return;
            }

            precioSelect.value = optionButton.dataset.value || '';
            const manualInput = activePrecioCard.querySelector('.precio-manual-input');
            if (manualInput) {
                manualInput.value = '';
            }
            removeManualPriceOption(precioSelect);
            precioSelect.dispatchEvent(new Event('change', { bubbles: true }));
            precioSelectorModal.hide();
        });
    }

    if (modalPrecioManualInput) {
        modalPrecioManualInput.addEventListener('input', updateModalManualMarginPreview);
    }

    if (modalPrecioManualApply) {
        modalPrecioManualApply.addEventListener('click', function () {
            if (!activePrecioCard || !modalPrecioManualInput) {
                return;
            }
            setManualPriceOnCard(activePrecioCard, modalPrecioManualInput.value, { closeModal: true });
        });
    }

    if (precioSelectorModalElement) {
        precioSelectorModalElement.addEventListener('hidden.bs.modal', function () {
            activePrecioCard = null;
            if (precioSelectorOptions) {
                precioSelectorOptions.innerHTML = '';
            }
            if (modalPrecioManualInput) {
                modalPrecioManualInput.value = '';
            }
            if (modalPrecioManualMargin) {
                modalPrecioManualMargin.textContent = '';
                modalPrecioManualMargin.classList.add('d-none');
            }
        });
    }

    if (typeof initPromoCountdowns === 'function') {
        initPromoCountdowns();
    }

    if (typeof initComboBuilder === 'function') {
        initComboBuilder({
            agregarUrl: document.body.dataset.agregarUrl,
            csrfToken: document.body.dataset.csrf,
            includePrice: true,
            onSubmitComplete: function (result) {
                if (result.totalItems != null) {
                    const counter = document.getElementById('contadorPedido');
                    const counter2 = document.getElementById('pedidoCantidad');
                    if (counter) counter.textContent = result.totalItems;
                    if (counter2) counter2.textContent = result.totalItems;
                }
                if (result.total != null) {
                    const totalEl = document.getElementById('pedidoTotal');
                    if (totalEl) totalEl.textContent = Number(result.total).toFixed(2);
                }
            },
        });
    }

    document.addEventListener('ltg:client-mode-changed', function () {
        document.querySelectorAll('.producto-card').forEach(function (card) {
            syncPrecioMask(card);
        });
    });
});