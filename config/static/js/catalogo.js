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

            fetch(agregarUrl, {
                method: "POST",
                headers: {
                    "X-CSRFToken": csrfToken,
                    "Content-Type": "application/x-www-form-urlencoded"
                },
                body: `producto_id=${producto_id}&presentacion_id=${presentacion_id}&cantidad=${cantidad}`
            })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        document.getElementById("contadorCarrito").textContent = data.total_items;

                        // Animación visual
                        btn.textContent = "Añadido ✔";
                        setTimeout(() => {
                            btn.textContent = "Añadir a cotización";
                        }, 1200);
                    }
                });
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