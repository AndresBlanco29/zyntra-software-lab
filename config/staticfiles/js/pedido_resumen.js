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
                location.reload()
            } else {
                alert('Error al eliminar: ' + (data.message || 'Error desconocido'))
            }
        })
        .catch(error => {
            console.error('Error completo:', error)
            alert('Error en la petición: ' + error.message)
        })
    }
})


document.querySelector(".btn-yellow").addEventListener("click",function(e){

e.preventDefault()

let tipoOrden = document.querySelector('input[name="tipo_orden"]:checked')

if(!tipoOrden){

alert("Debes indicar cómo se tomó la orden")
return

}

fetch(enviarURL,{

method:"POST",

headers:{
"X-CSRFToken": csrf,
"Content-Type":"application/x-www-form-urlencoded"
},

body:`tipo_orden=${tipoOrden.value}`

})
.then(res=>res.json())
.then(data=>{

console.log(data)

const toast = document.getElementById("toastPedido")

toast.classList.add("show")

setTimeout(()=>{

window.location.href = "/vendedores/tomar-pedido/"

},1500)

})
.catch(error => console.error('Error:', error))
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

precioSelect.innerHTML = `
<option value="${precio1}">Precio 1 - $${precio1}</option>
<option value="${precio2}">Precio 2 - $${precio2}</option>
<option value="${precio3}">Precio 3 - $${precio3}</option>
<option value="${precio4}">Precio 4 - $${precio4}</option>
<option value="${precio5}">Precio 5 - $${precio5}</option>
`

// 🔥 Seleccionamos automáticamente precio 1
precioSelect.value = precio1

// 🔥 Guardamos presentación
fetch(actualizarURL,{
method:"POST",
headers:{
"X-CSRFToken":csrf,
"Content-Type":"application/x-www-form-urlencoded"
},
body:`producto_id=${id}&presentacion_id=${this.value}&accion=cambiar_presentacion`
})

// 🔥 Guardamos precio automáticamente
fetch(actualizarURL,{
method:"POST",
headers:{
"X-CSRFToken":csrf,
"Content-Type":"application/x-www-form-urlencoded"
},
body:`producto_id=${id}&precio=${precio1}&accion=cambiar_precio`
})
.then(res=>res.json())
.then(data=>{

document.getElementById(`subtotal-${id}`).innerText = "$"+data.subtotal
const total1 = document.getElementById("totalPedido")
const total2 = document.getElementById("totalFinal")

total1.innerText = "$"+data.total
total2.innerText = "$"+data.total

total1.classList.add("total-update")
total2.classList.add("total-update")

setTimeout(()=>{
total1.classList.remove("total-update")
total2.classList.remove("total-update")
},350)



})

})

})

document.querySelectorAll(".precio-resumen").forEach(select => {

select.addEventListener("change", function(){

let id = this.dataset.id
let precio = this.value

fetch(actualizarURL,{

method:"POST",

headers:{
"X-CSRFToken":csrf,
"Content-Type":"application/x-www-form-urlencoded"
},

body:`producto_id=${id}&precio=${precio}&accion=cambiar_precio`

})
.then(res=>res.json())
.then(data=>{

document.getElementById(`subtotal-${id}`).innerText = "$"+data.subtotal
const total1 = document.getElementById("totalPedido")
const total2 = document.getElementById("totalFinal")

total1.innerText = "$"+data.total
total2.innerText = "$"+data.total

total1.classList.add("total-update")
total2.classList.add("total-update")

setTimeout(()=>{
total1.classList.remove("total-update")
total2.classList.remove("total-update")
},350)


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

document.getElementById(`subtotal-${id}`).innerText = "$"+data.subtotal

const total1 = document.getElementById("totalPedido")
const total2 = document.getElementById("totalFinal")

total1.innerText = "$"+data.total
total2.innerText = "$"+data.total

total1.classList.add("total-update")
total2.classList.add("total-update")

setTimeout(()=>{
total1.classList.remove("total-update")
total2.classList.remove("total-update")
},350)


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

document.getElementById(`subtotal-${id}`).innerText = "$"+data.subtotal

const total1 = document.getElementById("totalPedido")
const total2 = document.getElementById("totalFinal")

total1.innerText = "$"+data.total
total2.innerText = "$"+data.total

total1.classList.add("total-update")
total2.classList.add("total-update")

setTimeout(()=>{
total1.classList.remove("total-update")
total2.classList.remove("total-update")
},350)


})

}

// Fin de DOMContentLoaded
})