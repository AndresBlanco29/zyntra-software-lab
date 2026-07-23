const actualizarURL = document.body.dataset.actualizarUrl;
const eliminarUrl = document.body.dataset.eliminarUrl;
const presentacionURL = document.body.dataset.presentacionUrl;
const csrf = document.body.dataset.csrf;
const promoProductLabel = document.body.dataset.msgPromoProduct || "Product on promotion";
const promoActiveMessage = document.body.dataset.msgPromoActive || "Promotion active: your discount will be applied when you submit the order.";
const promoMinimumTemplate = document.body.dataset.msgPromoMinimum || "This promotion requires a minimum purchase of {minimum} units. You currently have {current}, so the discount will not be applied.";

function syncAppNavbarOffset() {
    const navbar = document.getElementById("appNavbar") || document.querySelector(".navbar-custom");
    if (!navbar) {
        return;
    }
    const height = Math.ceil(navbar.getBoundingClientRect().height);
    document.documentElement.style.setProperty("--app-navbar-height", height + "px");
}

syncAppNavbarOffset();
window.addEventListener("resize", syncAppNavbarOffset);
if (window.visualViewport) {
    window.visualViewport.addEventListener("resize", syncAppNavbarOffset);
}
window.addEventListener("load", syncAppNavbarOffset);

console.log("URL actualizar:", actualizarURL);
console.log("URL eliminar:", eliminarUrl);
console.log("URL presentación:", presentacionURL);

const buscadorInput = document.getElementById("buscador");
if (buscadorInput) {
    function filterProductRows(query) {
        const filtro = (query || "").toLowerCase();
        const filas = document.querySelectorAll("#tablaProductos tbody tr");

        filas.forEach(function (fila) {
            const nombre = fila.querySelector(".nombre-producto").textContent.toLowerCase();
            fila.style.display = nombre.includes(filtro) ? "" : "none";
        });
    }

    if (window.PreserveSearchFocus) {
        window.PreserveSearchFocus.bindDebouncedInput(buscadorInput, function (_input, query) {
            filterProductRows(query);
        });
    } else {
        buscadorInput.addEventListener("input", function () {
            filterProductRows(this.value);
        });
    }
}

function actualizarEstadoPromocion(productoId, promo) {
    const fila = document.querySelector(`#tablaProductos tbody tr[data-id="${productoId}"]`);
    if (!fila) {
        return;
    }

    const container = fila.querySelector('[data-role="promo-status"]');
    if (!container) {
        return;
    }

    container.replaceChildren();
    const available = Boolean(promo && promo.available);
    fila.classList.toggle("cart-promo-row", available);
    if (!available) {
        return;
    }

    const label = document.createElement("span");
    label.className = "cart-promo-label";
    const labelIcon = document.createElement("i");
    labelIcon.className = "bi bi-tag-fill";
    labelIcon.setAttribute("aria-hidden", "true");
    label.append(labelIcon, document.createTextNode(" " + promoProductLabel));
    container.appendChild(label);

    const message = document.createElement("div");
    message.className = promo.applied
        ? "cart-promo-message cart-promo-message--active"
        : "cart-promo-message cart-promo-message--warning";

    const icon = document.createElement("i");
    icon.className = promo.applied
        ? "bi bi-check-circle-fill"
        : "bi bi-exclamation-triangle-fill";
    icon.setAttribute("aria-hidden", "true");

    const messageText = promo.applied
        ? promoActiveMessage
        : promoMinimumTemplate
            .replace("{minimum}", String(promo.minimum))
            .replace("{current}", String(promo.current));
    message.append(icon, document.createTextNode(" " + messageText));
    container.appendChild(message);
}

// SUMAR / RESTAR / INGRESO MANUAL
function actualizarCantidad(productoId, accion, cantidad) {
    const body = new URLSearchParams({
        producto_id: productoId,
        accion: accion,
    });

    if (accion === "set") {
        body.set("cantidad", cantidad);
    }

    return fetch(actualizarURL, {
        method: "POST",
        headers: {
            "X-CSRFToken": csrf,
            "Content-Type": "application/x-www-form-urlencoded"
        },
        body: body.toString()
    })
    .then(res => res.json())
    .then(data => {
        if (data && data.reload) {
            window.location.reload();
            return data;
        }
        const input = document.querySelector(`.cantidad-input[data-id="${productoId}"]`);
        if (input) {
            input.value = data.cantidad;
        }
        actualizarEstadoPromocion(productoId, data.promo);
        return data;
    });
}

document.querySelectorAll(".sumar, .restar").forEach(btn => {
    btn.addEventListener("click", function () {
        const productoId = this.dataset.id;
        const accion = this.classList.contains("sumar") ? "sumar" : "restar";
        actualizarCantidad(productoId, accion);
    });
});

document.querySelectorAll(".cantidad-input").forEach(input => {
    input.addEventListener("blur", function () {
        window.CatalogQuantity.normalizeQuantityInput(this);
    });

    input.addEventListener("change", function () {
        const productoId = this.dataset.id;
        const cantidad = window.CatalogQuantity.normalizeQuantityInput(this);
        actualizarCantidad(productoId, "set", cantidad);
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
            actualizarEstadoPromocion(producto_id, data.promo);
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

        fetch(eliminarUrl, {
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
            
            // Actualizar el total de productos en el panel lateral
            const totalProductosElement = document.getElementById("totalProductos");
            if (totalProductosElement) {
                // Obtener el idioma del elemento html
                const lang = document.documentElement.lang || 'es';
                const labelText = lang === 'en' ? 'Total products' : 'Total de productos';
                totalProductosElement.textContent = labelText + ": " + data.total_items;
            }

        });

    });

});
