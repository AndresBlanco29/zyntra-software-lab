document.addEventListener("DOMContentLoaded", function () {

    const buscador = document.getElementById("buscadorClientes");
    const filtroEstado = document.getElementById("filtroEstado");
    const filas = document.querySelectorAll("tbody tr");

    function filtrarClientes(){

        let texto = buscador.value.toLowerCase();
        let estado = filtroEstado.value;

        filas.forEach(fila => {

            let nombre = fila.dataset.nombre.toLowerCase();
            let empresa = fila.dataset.empresa.toLowerCase();
            let correo = fila.dataset.correo.toLowerCase();
            let telefono = fila.dataset.telefono.toLowerCase();
            let estadoCliente = fila.dataset.estado;

            let coincideTexto =
                nombre.includes(texto) ||
                empresa.includes(texto) ||
                correo.includes(texto) ||
                telefono.includes(texto);

            let coincideEstado =
                estado === "" || estado === estadoCliente;

            fila.style.display = (coincideTexto && coincideEstado)
                ? ""
                : "none";

        });

    }

    buscador.addEventListener("keyup", filtrarClientes);
    filtroEstado.addEventListener("change", filtrarClientes);

});