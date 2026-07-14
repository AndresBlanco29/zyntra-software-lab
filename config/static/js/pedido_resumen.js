// Esperar a que el DOM esté completamente cargado
document.addEventListener('DOMContentLoaded', function() {

// Obtener token CSRF de forma más segura
function getCookie(name) {
    let cookieValue = null;
    if (document.cookie && document.cookie !== '') {
        const cookies = document.cookie.split(';');
        for (let i = 0; i < cookies.length; i++) {
            const cookie = cookies[i].trim();
            if (cookie.substring(0, name.length + 1) === (name + '=')) {
                cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                break;
            }
        }
    }
    return cookieValue;
}

// Obtener CSRF de meta tag, body dataset, o cookie de Django
const csrf = (
    document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') ||
    document.body.dataset.csrf || 
    getCookie('csrftoken') || 
    document.querySelector('[name=csrfmiddlewaretoken]')?.value
)

const actualizarURL = document.body.dataset.actualizarUrl
const eliminarURL = document.body.dataset.eliminarUrl
const enviarURL = document.body.dataset.enviarUrl
const guardarNotaURL = document.body.dataset.guardarNotaUrl
const orderTypeMessage = document.body.dataset.msgOrderType || 'You must indicate how the order was taken.'
const submitErrorMessage = document.body.dataset.msgSubmitError || 'The order could not be created.'
const removeErrorMessage = document.body.dataset.msgRemoveError || 'The product could not be removed from the order.'
const requestErrorMessage = document.body.dataset.msgRequestError || 'An error occurred while processing the request.'
const manualPriceLabel = document.body.dataset.manualPriceLabel || 'Manual price'
const feedbackBox = document.getElementById('pedidoFeedback')
const notaField = document.getElementById('pedidoNotaCliente')
let notaSaveTimer = null
let lastSavedNota = notaField ? String(notaField.value || '') : ''

function persistOrderNote(options) {
    const force = Boolean(options && options.force)
    const nota = notaField ? String(notaField.value || '') : ''
    if (!guardarNotaURL) {
        return Promise.resolve(nota)
    }
    if (!force && nota === lastSavedNota) {
        return Promise.resolve(nota)
    }
    return fetch(guardarNotaURL, {
        method: 'POST',
        headers: {
            'X-CSRFToken': csrf,
            'Content-Type': 'application/x-www-form-urlencoded'
        },
        body: `nota=${encodeURIComponent(nota)}`
    })
    .then(async res => {
        let data = {}
        try {
            data = await res.json()
        } catch (error) {
            data = {}
        }
        if (!res.ok || !(data && data.success)) {
            // Never surface draft-note save issues in the order UI.
            console.error('Order note autosave failed', data)
            return nota
        }
        // Keep the typed value as-is (including spaces). Do not rewrite the textarea.
        lastSavedNota = nota
        return lastSavedNota
    })
    .catch(function (error) {
        console.error('Order note autosave failed:', error)
        return nota
    })
}

function schedulePersistOrderNote() {
    if (!notaField) {
        return
    }
    if (notaSaveTimer) {
        clearTimeout(notaSaveTimer)
    }
    notaSaveTimer = setTimeout(function () {
        persistOrderNote({ force: true })
    }, 400)
}

if (notaField) {
    notaField.addEventListener('input', schedulePersistOrderNote)
    notaField.addEventListener('blur', function () {
        persistOrderNote({ force: true })
    })
}

document.querySelectorAll('.pedido-back-to-catalog').forEach(function (link) {
    link.addEventListener('click', function (event) {
        event.preventDefault()
        const targetUrl = link.getAttribute('href')
        persistOrderNote({ force: true }).finally(function () {
            window.location.href = targetUrl
        })
    })
})

function formatMoney(value) {
    const numericValue = Number(value)

    if (Number.isNaN(numericValue)) {
        return '0.00'
    }

    return numericValue.toFixed(2)
}

function updateOrderTotals(total) {
    const total1 = document.getElementById('totalPedido')
    const total2 = document.getElementById('totalFinal')

    if (!total1 || !total2) {
        return
    }

    total1.innerText = '$' + formatMoney(total)
    total2.innerText = '$' + formatMoney(total)
    total1.classList.add('total-update')
    total2.classList.add('total-update')
    setTimeout(() => {
        total1.classList.remove('total-update')
        total2.classList.remove('total-update')
    }, 350)
}

function refreshDiscountRow(id, data) {
    const fieldsWrap = document.querySelector(`.pedido-discount-cell[data-id="${id}"] .descuento-fields-wrap`)
    const resumen = document.querySelector(`.descuento-resumen[data-id="${id}"]`)
    const netPrice = document.querySelector(`.precio-neto[data-id="${id}"]`)
    const savings = document.querySelector(`.ahorro-linea[data-id="${id}"]`)
    const listPriceInput = document.querySelector(`.precio-resumen-manual[data-id="${id}"]`)
    const listPrice = listPriceInput ? Number(listPriceInput.value || 0) : 0
    const discountApplied = Boolean(data ? data.discount_applied : document.querySelector(`.descuento-toggle[data-id="${id}"]`)?.checked)
    const discountAmount = Number(data ? data.discount_amount : document.querySelector(`.descuento-monto[data-id="${id}"]`)?.value || 0)

    if (fieldsWrap) {
        fieldsWrap.classList.toggle('d-none', !discountApplied)
    }
    if (resumen) {
        if (discountApplied && discountAmount > 0) {
            const netUnit = Math.max(0, listPrice - discountAmount)
            resumen.textContent = `Lista $${formatMoney(listPrice)} - $${formatMoney(discountAmount)} = $${formatMoney(netUnit)} / unidad`
        } else {
            resumen.textContent = ''
        }
    }
    if (netPrice && data && data.net_unit_price) {
        netPrice.textContent = '$' + formatMoney(data.net_unit_price)
    }
    if (savings) {
        const lineSavings = data && data.line_savings ? Number(data.line_savings) : 0
        savings.classList.toggle('d-none', !(discountApplied && lineSavings > 0))
        if (discountApplied && lineSavings > 0) {
            savings.textContent = `Ahorras $${formatMoney(lineSavings)}`
        }
    }
}

function parseDecimalValue(value) {
    const normalized = String(value || '').trim().replace(',', '.')
    if (!normalized) {
        return null
    }
    const parsed = Number(normalized)
    return Number.isFinite(parsed) ? parsed : null
}

function syncDiscountPresetFromInput(presetSelect, amountInput) {
    if (!presetSelect || !amountInput) {
        return
    }
    const inputValue = parseDecimalValue(amountInput.value)
    let matchedValue = ''
    Array.from(presetSelect.options).forEach(function (option) {
        if (!option.value) {
            return
        }
        const optionValue = parseDecimalValue(option.value)
        if (inputValue !== null && optionValue !== null && optionValue === inputValue) {
            matchedValue = option.value
        }
    })
    presetSelect.value = matchedValue
}

function getPriceControls(id) {
    return {
        presetSelect: document.querySelector(`.precio-resumen-preset[data-id="${id}"]`),
        priceInput: document.querySelector(`.precio-resumen-manual[data-id="${id}"]`),
    }
}

function formatPriceTierLabel(tierNumber, amount) {
    return `PC${tierNumber} · $${formatMoney(amount)}`
}

function buildPricePresetOptions(precio1, precio2, precio3, precio4, precio5, selectedKey) {
    const tiers = [
        ['precio_1', precio1, 1],
        ['precio_2', precio2, 2],
        ['precio_3', precio3, 3],
        ['precio_4', precio4, 4],
        ['precio_5', precio5, 5],
    ]
    let html = `<option value="">${manualPriceLabel}</option>`
    tiers.forEach(function (tier) {
        const key = tier[0]
        const amount = tier[1]
        const number = tier[2]
        const selected = selectedKey === key ? ' selected' : ''
        html += `<option value="${amount}" data-price-key="${key}"${selected}>${formatPriceTierLabel(number, amount)}</option>`
    })
    return html
}

function syncPricePresetFromInput(presetSelect, priceInput) {
    if (!presetSelect || !priceInput) {
        return
    }
    const inputValue = parseDecimalValue(priceInput.value)
    let matchedValue = ''
    Array.from(presetSelect.options).forEach(function (option) {
        if (!option.value) {
            return
        }
        const optionValue = parseDecimalValue(option.value)
        if (inputValue !== null && optionValue !== null && optionValue === inputValue) {
            matchedValue = option.value
        }
    })
    presetSelect.value = matchedValue
}

function resolvePriceKey(presetSelect) {
    if (!presetSelect || !presetSelect.value) {
        return ''
    }
    return presetSelect.selectedOptions[0]?.dataset.priceKey || ''
}

function persistPrice(id) {
    const controls = getPriceControls(id)
    if (!controls.priceInput) {
        return Promise.resolve()
    }
    syncPricePresetFromInput(controls.presetSelect, controls.priceInput)
    return applyPriceChange(id, controls.priceInput.value, resolvePriceKey(controls.presetSelect))
}

function persistDiscount(id) {
    const toggle = document.querySelector(`.descuento-toggle[data-id="${id}"]`)
    const amountInput = document.querySelector(`.descuento-monto[data-id="${id}"]`)
    if (!toggle || !amountInput) {
        return Promise.resolve()
    }

    return fetch(actualizarURL, {
        method: 'POST',
        headers: {
            'X-CSRFToken': csrf,
            'Content-Type': 'application/x-www-form-urlencoded'
        },
        body: `producto_id=${id}&accion=cambiar_descuento&descuento_aplicado=${toggle.checked ? '1' : '0'}&descuento_monto=${encodeURIComponent(amountInput.value || '0')}`
    })
    .then(res => res.json())
    .then(data => {
        document.getElementById(`subtotal-${id}`).innerText = '$' + formatMoney(data.subtotal)
        updateOrderTotals(data.total)
        refreshDiscountRow(id, data)
        syncDiscountPresetFromInput(
            document.querySelector(`.descuento-preset[data-id="${id}"]`),
            document.querySelector(`.descuento-monto[data-id="${id}"]`)
        )
        return data
    })
}

function hideFeedback() {
    if (!feedbackBox) return
    feedbackBox.classList.add('d-none')
    feedbackBox.textContent = ''
}

function showFeedback(message) {
    if (!feedbackBox) return
    feedbackBox.textContent = message
    feedbackBox.classList.remove('d-none')
    feedbackBox.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
}

console.log('CSRF Token:', csrf)
console.log('Eliminar URL:', eliminarURL)

// ELIMINAR PRODUCTO - Usar event delegation
document.addEventListener('click', function(e) {
    if (e.target.classList.contains('eliminar') || e.target.closest('.eliminar')) {
        const btn = e.target.closest('.eliminar')
        if (!btn) return
        
        e.preventDefault()
        let id = btn.dataset.id
        
        console.log('Eliminando producto:', id)
        console.log('URL:', eliminarURL)
        console.log('CSRF:', csrf)
        
        fetch(eliminarURL, {
            method: "POST",
            headers: {
                "Content-Type": "application/x-www-form-urlencoded",
                "X-CSRFToken": csrf
            },
            body: `producto_id=${id}`
        })
        .then(res => {
            console.log('Status:', res.status)
            return res.json()
        })
        .then(data => {
            console.log('Respuesta completa:', data)
            if(data.success) {
                hideFeedback()
                location.reload()
            } else {
                showFeedback(data.message || removeErrorMessage)
            }
        })
        .catch(error => {
            console.error('Error completo:', error)
            showFeedback(error.message || requestErrorMessage)
        })
    }
})


document.querySelector(".btn-yellow").addEventListener("click",function(e){

e.preventDefault()

let tipoOrden = document.querySelector('input[name="tipo_orden"]:checked')

if(!tipoOrden){

showFeedback(orderTypeMessage)
return

}

hideFeedback()

const nota = notaField ? String(notaField.value || '').trim() : ''

fetch(enviarURL,{

method:"POST",

headers:{
"X-CSRFToken": csrf,
"Content-Type":"application/x-www-form-urlencoded"
},

body:`tipo_orden=${encodeURIComponent(tipoOrden.value)}&nota=${encodeURIComponent(nota)}`

})
.then(async res => {
const data = await res.json()

if(!res.ok || !data.success){
throw new Error(data.error || data.message || submitErrorMessage)
}

return data
})
.then(data=>{

console.log(data)

hideFeedback()

const toast = document.getElementById("toastPedido")
const pedidoId = data.pedido_id
if (toast && pedidoId) {
    const template = document.body.dataset.msgOrderSent || 'Order #{id} sent successfully'
    toast.textContent = template.replace('{id}', String(pedidoId)) + ' ✔'
}

if (toast) {
    toast.classList.add("show")
}

const redirectUrl = data.redirect_url || "/vendedores/"

setTimeout(()=>{

window.location.href = redirectUrl

},1500)

})
.catch(error => {
console.error('Error:', error)
showFeedback(error.message || submitErrorMessage)
})
})




document.querySelectorAll(".presentacion-resumen").forEach(select => {

select.addEventListener("change", function(){

let option = this.options[this.selectedIndex]

let precio1 = option.dataset.precio1
let precio2 = option.dataset.precio2
let precio3 = option.dataset.precio3
let precio4 = option.dataset.precio4
let precio5 = option.dataset.precio5

let id = this.dataset.id
const controls = getPriceControls(id)
const currentPriceKey = resolvePriceKey(controls.presetSelect) || 'precio_1'

if (controls.presetSelect) {
    controls.presetSelect.innerHTML = buildPricePresetOptions(
        precio1,
        precio2,
        precio3,
        precio4,
        precio5,
        currentPriceKey
    )
}

const matchingOption = controls.presetSelect?.querySelector(`option[data-price-key="${currentPriceKey}"]`)
const selectedPriceKey = matchingOption ? currentPriceKey : 'precio_1'
const nextPrice = matchingOption ? matchingOption.value : precio1

if (controls.presetSelect) {
    controls.presetSelect.value = matchingOption ? matchingOption.value : ''
    syncPricePresetFromInput(controls.presetSelect, controls.priceInput)
}
if (controls.priceInput) {
    controls.priceInput.value = formatMoney(nextPrice)
}

fetch(actualizarURL,{
method:"POST",
headers:{
"X-CSRFToken":csrf,
"Content-Type":"application/x-www-form-urlencoded"
},
body:`producto_id=${id}&presentacion_id=${this.value}&accion=cambiar_presentacion`
})
.then(() => applyPriceChange(id, nextPrice, matchingOption ? selectedPriceKey : ''))
.then(data=>{

document.getElementById(`subtotal-${id}`).innerText = "$"+formatMoney(data.subtotal)
updateOrderTotals(data.total)
refreshDiscountRow(id, data)

})

})

})

function applyPriceChange(id, precio, precioKey) {
    return fetch(actualizarURL, {
        method: 'POST',
        headers: {
            'X-CSRFToken': csrf,
            'Content-Type': 'application/x-www-form-urlencoded'
        },
        body: `producto_id=${id}&precio=${precio}&precio_key=${encodeURIComponent(precioKey)}&accion=cambiar_precio`
    })
    .then(res => res.json())
    .then(data => {
        document.getElementById(`subtotal-${id}`).innerText = '$' + formatMoney(data.subtotal)
        updateOrderTotals(data.total)
        refreshDiscountRow(id, data)
        return data
    })
}

function applyBulkPriceTier(priceKey) {
    let chain = Promise.resolve()
    document.querySelectorAll('.precio-resumen-preset').forEach(presetSelect => {
        const matchingOption = presetSelect.querySelector(`option[data-price-key="${priceKey}"]`)
        if (!matchingOption) {
            return
        }
        const id = presetSelect.dataset.id
        const controls = getPriceControls(id)
        presetSelect.value = matchingOption.value
        if (controls.priceInput) {
            controls.priceInput.value = formatMoney(matchingOption.value)
        }
        chain = chain.then(() => applyPriceChange(id, matchingOption.value, priceKey))
    })
    return chain
}

function applyBulkDiscount(discountValue) {
    let chain = Promise.resolve()
    document.querySelectorAll('.descuento-toggle').forEach(toggle => {
        const id = toggle.dataset.id
        const amountInput = document.querySelector(`.descuento-monto[data-id="${id}"]`)
        const presetSelect = document.querySelector(`.descuento-preset[data-id="${id}"]`)
        if (!amountInput) {
            return
        }
        toggle.checked = true
        amountInput.value = discountValue
        if (presetSelect) {
            presetSelect.value = discountValue
            syncDiscountPresetFromInput(presetSelect, amountInput)
        }
        const fieldsWrap = document.querySelector(`.pedido-discount-cell[data-id="${id}"] .descuento-fields-wrap`)
        if (fieldsWrap) {
            fieldsWrap.classList.remove('d-none')
        }
        chain = chain.then(() => persistDiscount(id))
    })
    return chain
}

document.querySelectorAll('.precio-resumen-preset').forEach(presetSelect => {
    const id = presetSelect.dataset.id
    const controls = getPriceControls(id)
    if (!controls.priceInput) {
        return
    }

    presetSelect.addEventListener('change', function () {
        if (presetSelect.value) {
            controls.priceInput.value = formatMoney(presetSelect.value)
        }
        persistPrice(id)
    })

    controls.priceInput.addEventListener('input', function () {
        syncPricePresetFromInput(presetSelect, controls.priceInput)
    })

    controls.priceInput.addEventListener('change', function () {
        persistPrice(id)
    })

    syncPricePresetFromInput(presetSelect, controls.priceInput)
})

const applyBulkPriceTierButton = document.getElementById('applyBulkPriceTierButton')
const bulkPriceTierSelect = document.getElementById('bulkPriceTierSelect')
if (applyBulkPriceTierButton && bulkPriceTierSelect) {
    applyBulkPriceTierButton.addEventListener('click', function () {
        const selectedPriceKey = bulkPriceTierSelect.value
        if (!selectedPriceKey) {
            return
        }
        applyBulkPriceTier(selectedPriceKey)
    })
}

const applyBulkDiscountButton = document.getElementById('applyBulkDiscountButton')
const bulkDiscountPresetSelect = document.getElementById('bulkDiscountPresetSelect')
if (applyBulkDiscountButton && bulkDiscountPresetSelect) {
    applyBulkDiscountButton.addEventListener('click', function () {
        const selectedDiscountValue = bulkDiscountPresetSelect.value
        if (!selectedDiscountValue) {
            return
        }
        applyBulkDiscount(selectedDiscountValue)
    })
}


// BOTON SUMAR

document.querySelectorAll(".sumar").forEach(btn=>{

btn.addEventListener("click",function(){

let id = this.dataset.id

actualizarCantidad(id,"sumar")

})

})


// BOTON RESTAR

document.querySelectorAll(".restar").forEach(btn=>{

btn.addEventListener("click",function(){

let id = this.dataset.id

actualizarCantidad(id,"restar")

})

})



// INPUT MANUAL

document.querySelectorAll(".cantidad-input").forEach(input=>{

input.addEventListener("input",function(){

let id = this.dataset.id
let cantidad = this.value

fetch(actualizarURL,{

method:"POST",

headers:{
"X-CSRFToken":csrf,
"Content-Type":"application/x-www-form-urlencoded"
},

body:`producto_id=${id}&accion=set&cantidad=${cantidad}`

})
.then(res=>res.json())
.then(data=>{

document.getElementById(`subtotal-${id}`).innerText = "$"+formatMoney(data.subtotal)
updateOrderTotals(data.total)
refreshDiscountRow(id, data)

})

})

})


// FUNCION GENERAL

function actualizarCantidad(id,accion){

fetch(actualizarURL,{

method:"POST",

headers:{
"X-CSRFToken":csrf,
"Content-Type":"application/x-www-form-urlencoded"
},

body:`producto_id=${id}&accion=${accion}`

})
.then(res=>res.json())
.then(data=>{

document.querySelector(`.cantidad-input[data-id="${id}"]`).value = data.cantidad

document.getElementById(`subtotal-${id}`).innerText = "$"+formatMoney(data.subtotal)
updateOrderTotals(data.total)
refreshDiscountRow(id, data)

})

}

document.querySelectorAll('.descuento-toggle').forEach(toggle => {
    toggle.addEventListener('change', function () {
        const id = this.dataset.id
        const fieldsWrap = document.querySelector(`.pedido-discount-cell[data-id="${id}"] .descuento-fields-wrap`)
        if (fieldsWrap) {
            fieldsWrap.classList.toggle('d-none', !this.checked)
        }
        persistDiscount(id)
    })
})

document.querySelectorAll('.descuento-preset').forEach(presetSelect => {
    const id = presetSelect.dataset.id
    const amountInput = document.querySelector(`.descuento-monto[data-id="${id}"]`)
    if (!amountInput) {
        return
    }

    presetSelect.addEventListener('change', function () {
        if (presetSelect.value) {
            amountInput.value = presetSelect.value
        }
        persistDiscount(id)
    })

    amountInput.addEventListener('input', function () {
        syncDiscountPresetFromInput(presetSelect, amountInput)
    })

    syncDiscountPresetFromInput(presetSelect, amountInput)
})

document.querySelectorAll('.descuento-monto').forEach(input => {
    input.addEventListener('change', function () {
        persistDiscount(this.dataset.id)
    })
})

document.querySelectorAll('.descuento-toggle').forEach(toggle => {
    refreshDiscountRow(toggle.dataset.id)
})

// Fin de DOMContentLoaded
})