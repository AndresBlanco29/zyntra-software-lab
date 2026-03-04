const actualizarURL = document.body.dataset.actualizarUrl;
const eliminarURL = document.body.dataset.eliminarUrl;
const presentacionURL = document.body.dataset.presentacionUrl;
const csrf = document.body.dataset.csrf;

console.log("URL actualizar:", actualizarURL);
console.log("URL eliminar:", eliminarURL);
console.log("URL presentación:", presentacionURL);

document.getElementById("buscador").addEventListener("keyup", function() {

    let filtro = this.value.toLowerCase();
    let filas = document.querySelectorAll("#tablaProductos tbody tr");

    filas.forEach(function(fila) {

        let nombre = fila.querySelector(".nombre-producto").textContent.toLowerCase();

        if (nombre.includes(filtro)) {
            fila.style.display = "";
        } else {
            fila.style.display = "none";
        }

    });

});

// SUMAR / RESTAR
document.querySelectorAll(".sumar, .restar").forEach(btn => {

    btn.addEventListener("click", function(){

        let fila = this.closest("tr");
        let producto_id = fila.dataset.id;
        let accion = this.classList.contains("sumar") ? "sumar" : "restar";

        fetch(actualizarURL, {
            method: "POST",
            headers: {
                "X-CSRFToken": csrf,
                "Content-Type": "application/x-www-form-urlencoded"
            },
            body: `producto_id=${producto_id}&accion=${accion}`
        })
        .then(res => res.json())
        .then(data => {

            fila.querySelector(".cantidad").textContent = data.cantidad;

        });

    });

});


// CAMBIAR PRESENTACIÓN
document.querySelectorAll(".presentacion-select").forEach(select => {

    select.addEventListener("change", function(){

        let fila = this.closest("tr");
        let producto_id = fila.dataset.id;
        let presentacion_id = this.value;

        fetch(presentacionURL, {
            method: "POST",
            headers: {
                "X-CSRFToken": csrf,
                "Content-Type": "application/x-www-form-urlencoded"
            },
            body: `producto_id=${producto_id}&presentacion_id=${presentacion_id}`
        })
        .then(response => response.json())
        .then(data => {


        });

    });

});

document.querySelectorAll(".presentacion-select").forEach(select => {

    select.addEventListener("change", function(){

        let option = this.options[this.selectedIndex];
        let unidades = option.dataset.unidades;
        let tipo = option.dataset.tipo;
        let nombre = option.text.toLowerCase();

        let info = this.closest("td").querySelector(".info-presentacion");

        info.textContent = unidades + " " + tipo + " por " + nombre;

    });

});

// ELIMINAR PRODUCTO
document.querySelectorAll(".eliminar-btn").forEach(btn => {

    btn.addEventListener("click", function(){

        let fila = this.closest("tr");
        let producto_id = fila.dataset.id;

        fetch(eliminarURL, {
            method: "POST",
            headers: {
                "X-CSRFToken": csrf,
                "Content-Type": "application/x-www-form-urlencoded"
            },
            body: `producto_id=${producto_id}`
        })
        .then(res => res.json())
        .then(data => {

            fila.remove();
            document.getElementById("contadorCarrito").textContent = data.total_items;

        });

    });

});
