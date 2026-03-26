
document.addEventListener('DOMContentLoaded', function() {
    const agregarUrl = document.body.dataset.agregarUrl;
    const csrfToken = document.body.dataset.csrf;

    function filtrarProductos() {
        let texto = document.getElementById("buscador").value.toLowerCase();
        let categoria = document.getElementById("filtroCategoria").value;
        let marca = document.getElementById("filtroMarca").value;

        let productos = document.querySelectorAll(".producto-card");

        productos.forEach(function (producto) {
            let nombre = producto.dataset.nombre.toLowerCase();
            let categoriaProducto = producto.dataset.categoria;
            let marcaProducto = producto.dataset.marca;

            let coincideTexto = nombre.includes(texto);
            let coincideCategoria = categoria === "" || categoriaProducto === categoria;
            let coincideMarca = marca === "" || marcaProducto === marca;

            producto.parentElement.style.display = (coincideTexto && coincideCategoria && coincideMarca) ? "" : "none";
        });
    }

    document.getElementById("buscador").addEventListener("keyup", filtrarProductos);
    document.getElementById("filtroCategoria").addEventListener("change", filtrarProductos);
    document.getElementById("filtroMarca").addEventListener("change", filtrarProductos);

    /* filtros dinámicos de opciones de marca */
    document.getElementById("filtroCategoria").addEventListener("change", function () {
        let categoriaSeleccionada = this.value;
        let marcas = document.querySelectorAll("#filtroMarca option");

        marcas.forEach(function (marca) {
            let categoriaMarca = marca.dataset.categoria;
            marca.style.display = (categoriaSeleccionada === "" || categoriaMarca === categoriaSeleccionada) ? "" : "none";
        });
    });

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
        btn.addEventListener("click", function () {

            let card = this.closest(".producto-card");

            let producto_id = card.dataset.productoId;
            let presentacion_id = card.querySelector(".presentacion-select").value;
            let cantidad = card.querySelector(".cantidad").textContent;
            let precio = card.querySelector(".precio-select").value;

            console.log("DEBUG - Intento agregar:", { precio, presentacion_id, cantidad });

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
                body: `producto_id=${producto_id}&presentacion_id=${presentacion_id}&cantidad=${cantidad}&precio=${precio}`
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

                    btn.textContent = "Añadido ✔";
                    setTimeout(() => {
                        btn.textContent = "Agregar al Pedido";
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
            let unidades = this.options[this.selectedIndex].dataset.unidades;
            let tipo = this.options[this.selectedIndex].dataset.tipo;
            let nombre = this.options[this.selectedIndex].text;

            infoTexto.textContent = unidades + " " + tipo + " por " + nombre.toLowerCase();
        });
    });
});

/* =========================
PRECIO SEGUN PRESENTACION
========================= */

document.querySelectorAll(".presentacion-select").forEach(select => {

select.addEventListener("change", function () {

const card = this.closest(".producto-card")

const option = this.selectedOptions[0]

const precios = [
option.dataset.precio1,
option.dataset.precio2,
option.dataset.precio3,
option.dataset.precio4,
option.dataset.precio5
]

const precioSelect = card.querySelector(".precio-select")

precioSelect.innerHTML = '<option value="">Seleccionar precio</option>'

precios.forEach((precio, index) => {

if (precio && precio !== "0.00") {

precioSelect.innerHTML += `
<option value="${precio}">
Precio ${index + 1} - $${precio}
</option>
`

}

})

})

})

const presentacion = card.querySelector(".presentacion-select").value;
const precio = card.querySelector(".precio-select").value;
const cantidad = card.querySelector(".cantidad").innerText;

fetch(urlAgregar, {
    method: "POST",
    headers: {
        "X-CSRFToken": csrf,
        "Content-Type": "application/x-www-form-urlencoded"
    },
    body: new URLSearchParams({
        presentacion_id: presentacion,
        cantidad: cantidad,
        precio: precio
    })
})