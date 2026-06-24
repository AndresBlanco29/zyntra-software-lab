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
const orderTypeMessage = document.body.dataset.msgOrderType || 'You must indicate how the order was taken.'
const submitErrorMessage = document.body.dataset.msgSubmitError || 'The order could not be created.'
const removeErrorMessage = document.body.dataset.msgRemoveError || 'The product could not be removed from the order.'
const requestErrorMessage = document.body.dataset.msgRequestError || 'An error occurred while processing the request.'
const feedbackBox = document.getElementById('pedidoFeedback')

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
    const wrap = document.querySelector(`.descuento-input-wrap input.descuento-monto[data-id="${id}"]`)?.closest('.descuento-input-wrap')
    const resumen = document.querySelector(`.descuento-resumen[data-id="${id}"]`)
    const netPrice = document.querySelector(`.precio-neto[data-id="${id}"]`)
    const savings = document.querySelector(`.ahorro-linea[data-id="${id}"]`)
    const listPriceSelect = document.querySelector(`.precio-resumen[data-id="${id}"]`)
    const listPrice = listPriceSelect ? Number(listPriceSelect.value || 0) : 0
    const discountApplied = Boolean(data ? data.discount_applied : document.querySelector(`.descuento-toggle[data-id="${id}"]`)?.checked)
    const discountAmount = Number(data ? data.discount_amount : document.querySelector(`.descuento-monto[data-id="${id}"]`)?.value || 0)

    if (wrap) {
        wrap.classList.toggle('d-none', !discountApplied)
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

fetch(enviarURL,{

method:"POST",

headers:{
"X-CSRFToken": csrf,
"Content-Type":"application/x-www-form-urlencoded"
},

body:`tipo_orden=${tipoOrden.value}`

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

toast.classList.add("show")

setTimeout(()=>{

window.location.href = "/vendedores/tomar-pedido/"

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

let precioSelect = document.querySelector(`.precio-resumen[data-id="${id}"]`)
const currentPriceKey = precioSelect?.selectedOptions?.[0]?.dataset.priceKey || 'precio_1'

precioSelect.innerHTML = `
<option value="${precio1}" data-price-key="precio_1">Precio 1 - $${precio1}</option>
<option value="${precio2}" data-price-key="precio_2">Precio 2 - $${precio2}</option>
<option value="${precio3}" data-price-key="precio_3">Precio 3 - $${precio3}</option>
<option value="${precio4}" data-price-key="precio_4">Precio 4 - $${precio4}</option>
<option value="${precio5}" data-price-key="precio_5">Precio 5 - $${precio5}</option>
`

const matchingOption = precioSelect.querySelector(`option[data-price-key="${currentPriceKey}"]`)
precioSelect.value = matchingOption ? matchingOption.value : precio1
const selectedPriceKey = matchingOption ? currentPriceKey : 'precio_1'

fetch(actualizarURL,{
method:"POST",
headers:{
"X-CSRFToken":csrf,
"Content-Type":"application/x-www-form-urlencoded"
},
body:`producto_id=${id}&presentacion_id=${this.value}&accion=cambiar_presentacion`
})
.then(() => fetch(actualizarURL,{
method:"POST",
headers:{
"X-CSRFToken":csrf,
"Content-Type":"application/x-www-form-urlencoded"
},
body:`producto_id=${id}&precio=${precioSelect.value}&precio_key=${encodeURIComponent(selectedPriceKey)}&accion=cambiar_precio`
}))
.then(res=>res.json())
.then(data=>{

document.getElementById(`subtotal-${id}`).innerText = "$"+formatMoney(data.subtotal)
updateOrderTotals(data.total)
refreshDiscountRow(id, data)

})

})

})

document.querySelectorAll(".precio-resumen").forEach(select => {

select.addEventListener("change", function(){

let id = this.dataset.id
let precio = this.value
const precioKey = this.selectedOptions[0]?.dataset.priceKey || ''

fetch(actualizarURL,{

method:"POST",

headers:{
"X-CSRFToken":csrf,
"Content-Type":"application/x-www-form-urlencoded"
},

body:`producto_id=${id}&precio=${precio}&precio_key=${encodeURIComponent(precioKey)}&accion=cambiar_precio`

})
.then(res=>res.json())
.then(data=>{

document.getElementById(`subtotal-${id}`).innerText = "$"+formatMoney(data.subtotal)
updateOrderTotals(data.total)
refreshDiscountRow(id, data)

})

})

})

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
        const wrap = document.querySelector(`.descuento-monto[data-id="${id}"]`)?.closest('.descuento-input-wrap')
        if (wrap) {
            wrap.classList.toggle('d-none', !this.checked)
        }
        persistDiscount(id)
    })
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