(function (global) {
    'use strict';

    function initComboBuilder(options) {
        options = options || {};
        const modalEl = document.getElementById('comboModal');
        if (!modalEl) {
            return;
        }

        const agregarUrl = options.agregarUrl || document.body.dataset.agregarUrl || '';
        const csrfToken = options.csrfToken || document.body.dataset.csrf || '';
        const includePrice = Boolean(options.includePrice);
        const onSubmitComplete = typeof options.onSubmitComplete === 'function'
            ? options.onSubmitComplete
            : null;
        const beforeOpenCombo = typeof options.beforeOpenCombo === 'function'
            ? options.beforeOpenCombo
            : null;

        const comboUrlTemplate = document.body.dataset.comboUrlTemplate || '';
        const titleEl = modalEl.querySelector('[data-role="combo-title"]');
        const hintEl = modalEl.querySelector('[data-role="combo-hint"]');
        const membersEl = modalEl.querySelector('[data-role="combo-members"]');
        const giftsEl = modalEl.querySelector('[data-role="combo-gifts"]');
        const loadingEl = modalEl.querySelector('[data-role="combo-loading"]');
        const progressEl = modalEl.querySelector('[data-role="combo-progress"]');
        const addBtn = modalEl.querySelector('[data-role="combo-add"]');

        const needLabel = document.body.dataset.comboNeedLabel || 'units in total to unlock the discount';
        const totalLabel = document.body.dataset.comboTotalLabel || 'Combo total';
        const readyLabel = document.body.dataset.comboReadyLabel || 'The combo discount will be applied!';
        const missingLabel = document.body.dataset.comboMissingLabel || 'Add more units to reach the combo';
        const addLabel = document.body.dataset.comboAddLabel || 'Add combo to the order';
        const addedLabel = document.body.dataset.comboAddedLabel || 'Combo added ✔';
        const qtyLabel = document.body.dataset.comboQtyLabel || 'Quantity';
        const presentationLabel = document.body.dataset.comboPresentationLabel || 'Presentation';
        const freeLabel = document.body.dataset.comboFreeLabel || 'FREE';
        const freeSectionLabel = document.body.dataset.comboFreeSectionLabel || 'Free product';
        const freePreviewLabel = document.body.dataset.comboFreePreviewLabel || 'Included when you reach this tier';

        const totalInput = modalEl.querySelector('[data-role="combo-total-input"]');
        const distributeBtn = modalEl.querySelector('[data-role="combo-distribute-btn"]');
        const tiersEl = modalEl.querySelector('[data-role="combo-tiers"]');

        let modalInstance = null;
        let currentData = null;
        let pinnedTierMin = null;

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

        function paidMemberRows() {
            return Array.prototype.slice.call(
                membersEl.querySelectorAll('.combo-member:not(.combo-member--gift)')
            );
        }

        function currentTotal() {
            let total = 0;
            paidMemberRows().forEach(function (row) {
                const input = row.querySelector('.combo-member__qty');
                total += Math.max(0, parseInt(input.value, 10) || 0);
            });
            return total;
        }

        function resolveDisplayTier(total) {
            const escalas = (currentData && currentData.escalas) || [];
            if (pinnedTierMin != null) {
                const pinned = escalas.find(function (escala) {
                    return escala.minimo === pinnedTierMin;
                });
                if (pinned) {
                    return pinned;
                }
            }
            let best = null;
            escalas.forEach(function (escala) {
                if (total >= escala.minimo && (!best || escala.minimo > best.minimo)) {
                    best = escala;
                }
            });
            return best;
        }

        function calcGiftState(escala, total) {
            if (!escala || !escala.regalo) {
                return null;
            }
            const minimo = escala.minimo || 1;
            const unidades = escala.unidades_gratis || 0;
            if (unidades <= 0) {
                return null;
            }
            let qtyForCalc = 0;
            let preview = false;
            if (total >= minimo) {
                qtyForCalc = total;
            } else if (pinnedTierMin === minimo) {
                qtyForCalc = minimo;
                preview = true;
            } else {
                return null;
            }
            const cantidad = Math.floor(qtyForCalc / minimo) * unidades;
            if (cantidad <= 0) {
                return null;
            }
            return {
                regalo: escala.regalo,
                cantidad: cantidad,
                preview: preview,
            };
        }

        function updateTierChips(total) {
            if (!tiersEl || !currentData) {
                return;
            }
            const displayTier = resolveDisplayTier(total);
            const activeMin = displayTier ? displayTier.minimo : null;
            tiersEl.querySelectorAll('.combo-tier-chip').forEach(function (chip) {
                const min = parseInt(chip.dataset.min, 10);
                chip.classList.toggle('combo-tier-chip--active', activeMin !== null && min === activeMin);
            });
        }

        function renderGiftRow(giftState) {
            if (!giftsEl) {
                return;
            }
            if (!giftState) {
                giftsEl.hidden = true;
                giftsEl.innerHTML = '';
                return;
            }

            const regalo = giftState.regalo;
            const label = esc(regalo.nombre) +
                (regalo.presentacion_nombre ? ' (' + esc(regalo.presentacion_nombre) + ')' : '');
            giftsEl.hidden = false;
            giftsEl.innerHTML =
                '<p class="combo-gifts__heading">' + esc(freeSectionLabel) + '</p>' +
                '<div class="combo-member combo-member--gift' + (giftState.preview ? ' combo-member--gift-preview' : '') + '" ' +
                    'data-presentacion-id="' + regalo.presentacion_id + '">' +
                    '<div class="combo-member__name">' +
                        '<i class="bi bi-gift-fill" aria-hidden="true"></i> ' +
                        label +
                        ' <span class="combo-gift-badge">' + esc(freeLabel) + '</span>' +
                    '</div>' +
                    '<div class="combo-member__controls">' +
                        '<div class="combo-member__field combo-member__field--qty">' +
                            '<label class="combo-member__label">' + esc(qtyLabel) + '</label>' +
                            '<div class="combo-member__stepper combo-member__stepper--readonly">' +
                                '<input type="number" readonly class="form-control form-control-sm combo-member__qty" value="' +
                                    giftState.cantidad + '">' +
                            '</div>' +
                        '</div>' +
                    '</div>' +
                    (giftState.preview
                        ? '<p class="combo-gifts__preview-note">' + esc(freePreviewLabel) + '</p>'
                        : '') +
                '</div>';
        }

        function refreshProgress() {
            if (!currentData) {
                return;
            }
            const total = currentTotal();
            const minimum = currentData.minimum || 0;
            const ready = total >= minimum && minimum > 0;
            const missing = Math.max(0, minimum - total);
            const displayTier = resolveDisplayTier(total);
            const giftState = calcGiftState(displayTier, total);

            progressEl.classList.toggle('combo-modal__progress--ready', ready);
            if (ready) {
                let message = esc(totalLabel) + ': <strong>' + total + '</strong> — ' + esc(readyLabel);
                if (giftState && !giftState.preview) {
                    message += ' · <strong>' + esc(freeLabel) + '</strong>: ' +
                        esc(giftState.regalo.nombre) + ' × ' + giftState.cantidad;
                }
                progressEl.innerHTML = '<i class="bi bi-check-circle-fill"></i> ' + message;
            } else {
                progressEl.innerHTML = '<i class="bi bi-exclamation-triangle-fill"></i> ' +
                    esc(totalLabel) + ': <strong>' + total + '</strong> / ' + minimum + ' ' +
                    esc(needLabel) + ' (' + esc(missingLabel) + ': <strong>' + missing + '</strong>)';
            }
            addBtn.disabled = !ready;
            updateTierChips(total);
            renderGiftRow(giftState);
        }

        function distributeEqually(total) {
            const rows = paidMemberRows();
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
            tiersEl.innerHTML = escalas.map(function (escala) {
                return '<button type="button" class="combo-tier-chip" data-min="' + escala.minimo + '">' +
                    '<span class="combo-tier-chip__min">' + escala.minimo + '+</span>' +
                    '<span class="combo-tier-chip__benefit">' + esc(escala.beneficio) + '</span>' +
                    '</button>';
            }).join('');
            tiersEl.querySelectorAll('.combo-tier-chip').forEach(function (chip) {
                chip.addEventListener('click', function () {
                    const min = parseInt(this.dataset.min, 10) || 0;
                    pinnedTierMin = min;
                    if (totalInput) {
                        totalInput.value = String(min);
                    }
                    distributeEqually(min);
                });
            });
        }

        function renderMembers(data) {
            const rows = (data.miembros || []).map(function (miembro) {
                const options = (miembro.presentaciones || []).map(function (presentacion) {
                    const priceTxt = (presentacion.precio != null) ? ' — $' + presentacion.precio : '';
                    const priceKeyAttr = presentacion.precio_key
                        ? ' data-price-key="' + esc(presentacion.precio_key) + '"'
                        : '';
                    return '<option value="' + presentacion.id + '" data-price="' +
                        (presentacion.precio != null ? presentacion.precio : '') + '"' + priceKeyAttr + '>' +
                        esc(presentacion.nombre) + priceTxt + '</option>';
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

            paidMemberRows().forEach(function (row) {
                const input = row.querySelector('.combo-member__qty');
                function onQtyEdited() {
                    pinnedTierMin = null;
                    refreshProgress();
                }
                row.querySelector('.combo-member__minus').addEventListener('click', function () {
                    input.value = String(Math.max(0, (parseInt(input.value, 10) || 0) - 1));
                    onQtyEdited();
                });
                row.querySelector('.combo-member__plus').addEventListener('click', function () {
                    input.value = String(Math.max(0, (parseInt(input.value, 10) || 0) + 1));
                    onQtyEdited();
                });
                input.addEventListener('input', onQtyEdited);
            });
        }

        function buildPayload(row) {
            const qty = Math.max(0, parseInt(row.querySelector('.combo-member__qty').value, 10) || 0);
            if (qty <= 0) {
                return null;
            }
            const select = row.querySelector('.combo-member__presentation');
            const option = select.selectedOptions[0];
            const payload = {
                producto_id: row.dataset.productoId,
                presentacion_id: select.value,
                cantidad: String(qty),
            };
            if (includePrice) {
                payload.precio = (option && option.dataset.price) || '';
                payload.precio_key = (option && option.dataset.priceKey) || '';
            }
            return payload;
        }

        function openCombo(promoId) {
            if (beforeOpenCombo && beforeOpenCombo() === false) {
                return;
            }
            const url = comboUrlTemplate.replace(/\/0\/miembros\/$/, '/' + promoId + '/miembros/');
            currentData = null;
            pinnedTierMin = null;
            membersEl.innerHTML = '';
            if (giftsEl) {
                giftsEl.hidden = true;
                giftsEl.innerHTML = '';
            }
            progressEl.innerHTML = '';
            addBtn.disabled = true;
            addBtn.textContent = addLabel;
            loadingEl.hidden = false;
            getModal().show();

            fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' }, credentials: 'same-origin' })
                .then(function (response) { return response.ok ? response.json() : null; })
                .then(function (data) {
                    loadingEl.hidden = true;
                    if (!data) {
                        membersEl.innerHTML = '<p class="text-danger">Error</p>';
                        return;
                    }
                    currentData = data;
                    if (titleEl) {
                        titleEl.textContent = data.nombre || (document.body.dataset.comboTitle || 'Build your combo');
                    }
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
            const payloads = paidMemberRows()
                .map(buildPayload)
                .filter(Boolean);
            if (!payloads.length || !agregarUrl) {
                return;
            }
            addBtn.disabled = true;

            let chain = Promise.resolve();
            let lastItems = null;
            let lastTotal = null;

            payloads.forEach(function (payload) {
                chain = chain.then(function () {
                    return fetch(agregarUrl, {
                        method: 'POST',
                        headers: {
                            'X-CSRFToken': csrfToken,
                            'Content-Type': 'application/x-www-form-urlencoded',
                        },
                        body: new URLSearchParams(payload).toString(),
                    }).then(function (response) {
                        return response.ok ? response.json() : null;
                    });
                }).then(function (data) {
                    if (data && typeof data.total_items !== 'undefined') {
                        lastItems = data.total_items;
                    }
                    if (data && typeof data.total !== 'undefined') {
                        lastTotal = data.total;
                    }
                });
            });

            chain.then(function () {
                if (onSubmitComplete) {
                    onSubmitComplete({ totalItems: lastItems, total: lastTotal });
                }
                addBtn.textContent = addedLabel;
                setTimeout(function () {
                    getModal().hide();
                    addBtn.disabled = false;
                    addBtn.textContent = addLabel;
                }, 900);
            }).catch(function () {
                addBtn.disabled = false;
            });
        }

        addBtn.addEventListener('click', submitCombo);

        if (distributeBtn) {
            distributeBtn.addEventListener('click', function () {
                pinnedTierMin = null;
                distributeEqually(parseInt(totalInput ? totalInput.value : '0', 10) || 0);
            });
        }

        if (totalInput) {
            totalInput.addEventListener('change', function () {
                pinnedTierMin = null;
                distributeEqually(parseInt(totalInput.value, 10) || 0);
            });
        }

        document.querySelectorAll('.js-combo-add-btn').forEach(function (btn) {
            btn.addEventListener('click', function () {
                const promoId = this.dataset.promoId;
                if (promoId) {
                    openCombo(promoId);
                }
            });
        });
    }

    global.initComboBuilder = initComboBuilder;
}(window));
