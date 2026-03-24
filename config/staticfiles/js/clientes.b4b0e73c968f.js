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

let clienteEstadoId = null;
let accionEstadoCliente = null;
let nombreEstadoCliente = "";

// Función para abrir el modal de editar cliente
function abrirEditarCliente(clienteId, nombre, empresa, correo, telefono) {
    document.getElementById('clienteId').value = clienteId;
    document.getElementById('nombreCliente').value = nombre;
    document.getElementById('empresaCliente').value = empresa;
    document.getElementById('correoCliente').value = correo;
    document.getElementById('telefonoCliente').value = telefono;
    
    // Agregar filtro de solo números al input de teléfono
    const telefonoInput = document.getElementById('telefonoCliente');
    telefonoInput.addEventListener('input', function(e) {
        // Solo permitir números
        this.value = this.value.replace(/[^0-9]/g, '');
        // Limitar a 10 dígitos
        if (this.value.length > 10) {
            this.value = this.value.slice(0, 10);
        }
    });
}

// Función para guardar los cambios del cliente
function guardarEditarCliente() {
    const form = document.getElementById('formEditarCliente');
    const clienteId = document.getElementById('clienteId').value;
    const empresa = document.getElementById('empresaCliente').value;
    const correo = document.getElementById('correoCliente').value;
    const telefono = document.getElementById('telefonoCliente').value;

    // Validaciones básicas
    if (!empresa || !correo || !telefono) {
        alert('Por favor completa todos los campos');
        return;
    }

    // Validar email
    const regexEmail = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    if (!regexEmail.test(correo)) {
        alert('Por favor ingresa un correo válido');
        return;
    }

    // Validar teléfono (exactamente 10 dígitos)
    if (!/^\d{10}$/.test(telefono)) {
        alert('El teléfono debe tener exactamente 10 dígitos');
        return;
    }

    // Enviar cambios al servidor
    fetch('/vendedores/editar-cliente/', {
        method: 'POST',
        headers: {
            'X-CSRFToken': document.querySelector('[name=csrfmiddlewaretoken]').value,
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({
            cliente_id: clienteId,
            empresa: empresa,
            correo: correo,
            telefono: telefono
        })
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            // Cerrar modal de editar
            const editarModal = bootstrap.Modal.getInstance(document.getElementById('editarClienteModal'));
            if (editarModal) {
                editarModal.hide();
            }
            
            // Mostrar modal de éxito
            const exitoModal = new bootstrap.Modal(document.getElementById('exitoEditarClienteModal'));
            exitoModal.show();
            
            // Recargar página después de 2 segundos
            setTimeout(() => {
                location.reload();
            }, 2000);
        } else {
            alert('Error al actualizar: ' + (data.message || 'Error desconocido'));
        }
    })
    .catch(error => {
        console.error('Error:', error);
        alert('Error al procesar la solicitud');
    });
}

function abrirModalEstadoCliente(clienteId, nombreCompleto, accion) {
    clienteEstadoId = clienteId;
    accionEstadoCliente = accion;
    nombreEstadoCliente = nombreCompleto || "cliente";

    const titulo = document.getElementById('tituloConfirmarEstadoCliente');
    const texto = document.getElementById('textoConfirmarEstadoCliente');
    const btnConfirmar = document.getElementById('btnConfirmarEstadoCliente');

    if (!titulo || !texto || !btnConfirmar) {
        return;
    }

    if (accion === 'desactivar') {
        titulo.textContent = 'Desactivar cliente';
        texto.textContent = `¿Deseas desactivar a ${nombreEstadoCliente}?`;
        btnConfirmar.textContent = 'Desactivar';
        btnConfirmar.style.background = 'linear-gradient(to right, #b91c1c, #dc2626)';
    } else {
        titulo.textContent = 'Activar cliente';
        texto.textContent = `¿Deseas activar a ${nombreEstadoCliente}?`;
        btnConfirmar.textContent = 'Activar';
        btnConfirmar.style.background = 'linear-gradient(to right, #0b3d91, #1565c0)';
    }

    const modal = new bootstrap.Modal(document.getElementById('confirmarEstadoClienteModal'));
    modal.show();
}

function confirmarCambioEstadoCliente() {
    if (!clienteEstadoId || !accionEstadoCliente) {
        return;
    }

    const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]')?.value;
    const endpoint = accionEstadoCliente === 'desactivar'
        ? '/vendedores/desactivar-cliente/'
        : '/vendedores/activar-cliente/';

    fetch(endpoint, {
        method: 'POST',
        headers: {
            'X-CSRFToken': csrfToken,
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({
            cliente_id: clienteEstadoId
        })
    })
    .then(response => response.json())
    .then(data => {
        if (!data.success) {
            alert('Error: ' + (data.message || 'No se pudo completar la accion'));
            return;
        }

        const modalConfirmar = bootstrap.Modal.getInstance(document.getElementById('confirmarEstadoClienteModal'));
        if (modalConfirmar) {
            modalConfirmar.hide();
        }

        const tituloExito = document.getElementById('tituloExitoEstadoCliente');
        const textoExito = document.getElementById('textoExitoEstadoCliente');

        if (accionEstadoCliente === 'desactivar') {
            tituloExito.textContent = 'Cliente desactivado';
            textoExito.textContent = 'El cliente se ha desactivado correctamente.';
        } else {
            tituloExito.textContent = 'Cliente activado';
            textoExito.textContent = 'El cliente se ha activado correctamente.';
        }

        const modalExito = new bootstrap.Modal(document.getElementById('exitoEstadoClienteModal'));
        modalExito.show();

        setTimeout(() => {
            location.reload();
        }, 1600);
    })
    .catch(error => {
        console.error('Error:', error);
        alert('Error al procesar la solicitud');
    });
}

window.abrirModalEstadoCliente = abrirModalEstadoCliente;
window.confirmarCambioEstadoCliente = confirmarCambioEstadoCliente;