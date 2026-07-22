document.addEventListener("DOMContentLoaded", function () {
    const form = document.getElementById("clientes-filter-form");
    const filtroEstado = document.getElementById("filtroEstado");

    if (!form || !filtroEstado) {
        return;
    }

    function submitFilters() {
        form.submit();
    }

    filtroEstado.addEventListener("change", submitFilters);
});

let clienteEstadoId = null;
let accionEstadoCliente = null;
let nombreEstadoCliente = "";

function getCustomerPageMessages() {
    const source = document.getElementById('clientesPage');
    const dataset = source ? source.dataset : {};

    return {
        fillAllFields: dataset.msgFillAllFields || 'Please complete all fields.',
        invalidEmail: dataset.msgInvalidEmail || 'Please enter a valid email address.',
        invalidPhone: dataset.msgInvalidPhone || 'Phone number must contain exactly 10 digits.',
        updateErrorPrefix: dataset.msgUpdateErrorPrefix || 'Error updating:',
        unknownError: dataset.msgUnknownError || 'Unknown error.',
        requestError: dataset.msgRequestError || 'Error processing the request.',
        deactivateCustomerTitle: dataset.msgDeactivateCustomerTitle || 'Deactivate customer',
        deactivateCustomerQuestion: dataset.msgDeactivateCustomerQuestion || 'Do you want to deactivate {name}?',
        deactivateAction: dataset.msgDeactivateAction || 'Deactivate',
        activateCustomerTitle: dataset.msgActivateCustomerTitle || 'Activate customer',
        activateCustomerQuestion: dataset.msgActivateCustomerQuestion || 'Do you want to activate {name}?',
        activateAction: dataset.msgActivateAction || 'Activate',
        actionErrorPrefix: dataset.msgActionErrorPrefix || 'Error:',
        actionIncomplete: dataset.msgActionIncomplete || 'Could not complete the action.',
        customerFallbackName: dataset.msgCustomerFallbackName || 'customer',
        customerDeactivatedTitle: dataset.msgCustomerDeactivatedTitle || 'Customer deactivated',
        customerDeactivatedBody: dataset.msgCustomerDeactivatedBody || 'The customer was deactivated successfully.',
        customerActivatedTitle: dataset.msgCustomerActivatedTitle || 'Customer activated',
        customerActivatedBody: dataset.msgCustomerActivatedBody || 'The customer was activated successfully.',
        accessFillAllFields: dataset.msgAccessFillAllFields || 'Please complete username and both password fields.',
        accessPasswordMismatch: dataset.msgAccessPasswordMismatch || 'Passwords do not match.',
        accessErrorPrefix: dataset.msgAccessErrorPrefix || 'Error configuring access:',
        accessViewErrorPrefix: dataset.msgAccessViewErrorPrefix || 'Error loading access:',
        accessPasswordUnavailable: dataset.msgAccessPasswordUnavailable || 'The password was set before this feature. Enter a new password below to update it and make it visible.',
        accessSuccessTitle: dataset.msgAccessSuccessTitle || 'Access configured',
        accessSuccessBody: dataset.msgAccessSuccessBody || 'The customer can now sign in with the username and password you set.',
        accessUpdateSuccessTitle: dataset.msgAccessUpdateSuccessTitle || 'Access updated',
        accessUpdateSuccessBody: dataset.msgAccessUpdateSuccessBody || 'The customer web access credentials were updated successfully.',
        accessPasswordRules: dataset.msgAccessPasswordRules || 'The password must meet all requirements listed below.',
        accessShowPassword: dataset.msgAccessShowPassword || 'Show password',
        accessHidePassword: dataset.msgAccessHidePassword || 'Hide password',
        accessViewTitle: dataset.msgAccessViewTitle || 'View web access',
        accessViewHint: dataset.msgAccessViewHint || 'Username and password currently configured for this customer. You can update them below.',
        accessUpdateButton: dataset.msgAccessUpdateButton || 'Update access',
        accessCreateTitle: dataset.msgAccessCreateTitle || 'Configurar acceso web',
        accessCreateHint: dataset.msgAccessCreateHint || 'Define the username and password the customer will use to sign in on the website.',
        accessCreateButton: dataset.msgAccessCreateButton || 'Guardar acceso',
        termsErrorPrefix: dataset.msgTermsErrorPrefix || 'Error saving payment terms:',
        termsSuccessTitle: dataset.msgTermsSuccessTitle || 'Payment terms updated',
        termsSuccessBody: dataset.msgTermsSuccessBody || 'The customer payment terms were saved successfully.',
        termsSelectRequired: dataset.msgTermsSelectRequired || 'Please select a payment term.',
        creditLimitErrorPrefix: dataset.msgCreditLimitErrorPrefix || 'Error saving credit limit:',
        creditLimitSuccessTitle: dataset.msgCreditLimitSuccessTitle || 'Credit limit updated',
        creditLimitSuccessBody: dataset.msgCreditLimitSuccessBody || 'The customer credit limit was saved successfully.',
        creditLimitInvalid: dataset.msgCreditLimitInvalid || 'Enter a valid credit limit amount.',
        usernameRequired: dataset.msgUsernameRequired || 'Username is required.',
        usernameInvalid: dataset.msgUsernameInvalid || 'Username can only use letters, numbers, dots, underscores and hyphens (min 3 characters).',
        canChangeUsername: dataset.canChangeUsername === '1',
        canViewWebPassword: dataset.canViewWebPassword === '1'
    };
}

function getAccessPasswordChecks(password) {
    return {
        uppercase: /[A-Z]/.test(password),
        lowercase: /[a-z]/.test(password),
        number: /\d/.test(password),
        special: /[^A-Za-z0-9]/.test(password),
    };
}

function updateAccessPasswordRules() {
    const passwordInput = document.getElementById('accesoPassword');
    const confirmInput = document.getElementById('accesoPasswordConfirm');
    const rulesList = document.getElementById('accesoPasswordRules');

    if (!passwordInput || !confirmInput || !rulesList) {
        return;
    }

    const password = passwordInput.value;
    const confirmPassword = confirmInput.value;
    const checks = getAccessPasswordChecks(password);

    rulesList.querySelectorAll('li[data-rule]').forEach(function (item) {
        const rule = item.dataset.rule;
        if (rule === 'match') {
            item.classList.toggle('is-met', Boolean(password) && password === confirmPassword);
            return;
        }
        item.classList.toggle('is-met', checks[rule]);
    });
}

function accessPasswordMeetsRequirements(password, confirmPassword) {
    const checks = getAccessPasswordChecks(password);
    return Object.values(checks).every(Boolean) && password === confirmPassword;
}

function bindAccessPasswordValidation() {
    const passwordInput = document.getElementById('accesoPassword');
    const confirmInput = document.getElementById('accesoPasswordConfirm');

    if (!passwordInput || !confirmInput || passwordInput.dataset.rulesBound === 'true') {
        return;
    }

    passwordInput.dataset.rulesBound = 'true';
    passwordInput.addEventListener('input', updateAccessPasswordRules);
    confirmInput.addEventListener('input', updateAccessPasswordRules);
}

const customerMessages = getCustomerPageMessages();

function getEditLocationElements() {
    return {
        countryInput: document.getElementById('paisCliente'),
        stateManual: document.getElementById('estadoClienteManual'),
        cityManual: document.getElementById('ciudadClienteManual')
    };
}

function collectLocationValues() {
    const elements = getEditLocationElements();

    return {
        manual_location: true,
        pais: elements.countryInput.value.trim(),
        estado: elements.stateManual.value.trim(),
        ciudad: elements.cityManual.value.trim()
    };
}

function sanitizePhoneDigits(value) {
    return String(value || '').replace(/\D/g, '').slice(0, 10);
}

// Función para abrir el modal de editar cliente
function abrirEditarCliente(button) {
    const elements = getEditLocationElements();

    document.getElementById('clienteId').value = button.dataset.clienteId;
    document.getElementById('nombreCliente').value = button.dataset.clienteNombre;
    document.getElementById('empresaCliente').value = button.dataset.clienteEmpresa;
    const usernameInput = document.getElementById('usernameCliente');
    if (usernameInput) {
        usernameInput.value = button.dataset.clienteUsername || '';
    }
    document.getElementById('correoCliente').value = button.dataset.clienteCorreo;
    document.getElementById('telefonoCliente').value = sanitizePhoneDigits(button.dataset.clienteTelefono);
    document.getElementById('direccionCliente').value = button.dataset.clienteDireccion || '';
    document.getElementById('codigoPostalCliente').value = button.dataset.clienteCodigoPostal || '';
    elements.countryInput.value = button.dataset.clientePais || 'USA';
    elements.stateManual.value = button.dataset.clienteEstado || '';
    elements.cityManual.value = button.dataset.clienteCiudad || '';
}

// Función para guardar los cambios del cliente
function guardarEditarCliente() {
    const clienteId = document.getElementById('clienteId').value;
    const empresa = document.getElementById('empresaCliente').value;
    const correo = document.getElementById('correoCliente').value;
    const telefono = sanitizePhoneDigits(document.getElementById('telefonoCliente').value);
    document.getElementById('telefonoCliente').value = telefono;
    const direccion = document.getElementById('direccionCliente').value;
    const codigoPostal = document.getElementById('codigoPostalCliente').value;
    const locationValues = collectLocationValues();
    const usernameInput = document.getElementById('usernameCliente');
    const username = usernameInput ? String(usernameInput.value || '').trim().toLowerCase() : '';
    if (usernameInput) {
        usernameInput.value = username;
    }

    // Validaciones básicas
    if (!empresa || !correo || !telefono || !direccion || !locationValues.estado || !locationValues.ciudad || !locationValues.pais) {
        alert(customerMessages.fillAllFields);
        return;
    }

    if (customerMessages.canChangeUsername) {
        if (!username) {
            alert(customerMessages.usernameRequired);
            return;
        }
        if (!/^[a-z0-9._-]{3,}$/.test(username)) {
            alert(customerMessages.usernameInvalid);
            return;
        }
    }

    // Validar email
    const regexEmail = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    if (!regexEmail.test(correo)) {
        alert(customerMessages.invalidEmail);
        return;
    }

    // Validar teléfono (exactamente 10 dígitos)
    if (!/^\d{10}$/.test(telefono)) {
        alert(customerMessages.invalidPhone);
        return;
    }

    const payload = {
        cliente_id: clienteId,
        empresa: empresa,
        correo: correo,
        telefono: telefono,
        direccion: direccion,
        codigo_postal: codigoPostal,
        pais: locationValues.pais,
        estado: locationValues.estado,
        ciudad: locationValues.ciudad,
        manual_location: locationValues.manual_location
    };
    if (customerMessages.canChangeUsername) {
        payload.username = username;
    }

    // Enviar cambios al servidor
    fetch('/vendedores/editar-cliente/', {
        method: 'POST',
        headers: {
            'X-CSRFToken': document.querySelector('[name=csrfmiddlewaretoken]').value,
            'Content-Type': 'application/json'
        },
        body: JSON.stringify(payload)
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
            alert(`${customerMessages.updateErrorPrefix} ${data.message || customerMessages.unknownError}`);
        }
    })
    .catch(error => {
        console.error('Error:', error);
        alert(customerMessages.requestError);
    });
}

function abrirModalEstadoCliente(clienteId, nombreCompleto, accion) {
    clienteEstadoId = clienteId;
    accionEstadoCliente = accion;
    nombreEstadoCliente = nombreCompleto || customerMessages.customerFallbackName;

    const titulo = document.getElementById('tituloConfirmarEstadoCliente');
    const texto = document.getElementById('textoConfirmarEstadoCliente');
    const btnConfirmar = document.getElementById('btnConfirmarEstadoCliente');

    if (!titulo || !texto || !btnConfirmar) {
        return;
    }

    if (accion === 'desactivar') {
        titulo.textContent = customerMessages.deactivateCustomerTitle;
        texto.textContent = customerMessages.deactivateCustomerQuestion.replace('{name}', nombreEstadoCliente);
        btnConfirmar.textContent = customerMessages.deactivateAction;
        btnConfirmar.style.background = 'linear-gradient(to right, #b91c1c, #dc2626)';
    } else {
        titulo.textContent = customerMessages.activateCustomerTitle;
        texto.textContent = customerMessages.activateCustomerQuestion.replace('{name}', nombreEstadoCliente);
        btnConfirmar.textContent = customerMessages.activateAction;
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
            alert(`${customerMessages.actionErrorPrefix} ${data.message || customerMessages.actionIncomplete}`);
            return;
        }

        const modalConfirmar = bootstrap.Modal.getInstance(document.getElementById('confirmarEstadoClienteModal'));
        if (modalConfirmar) {
            modalConfirmar.hide();
        }

        const tituloExito = document.getElementById('tituloExitoEstadoCliente');
        const textoExito = document.getElementById('textoExitoEstadoCliente');

        if (accionEstadoCliente === 'desactivar') {
            tituloExito.textContent = customerMessages.customerDeactivatedTitle;
            textoExito.textContent = customerMessages.customerDeactivatedBody;
        } else {
            tituloExito.textContent = customerMessages.customerActivatedTitle;
            textoExito.textContent = customerMessages.customerActivatedBody;
        }

        const modalExito = new bootstrap.Modal(document.getElementById('exitoEstadoClienteModal'));
        modalExito.show();

        setTimeout(() => {
            location.reload();
        }, 1600);
    })
    .catch(error => {
        console.error('Error:', error);
        alert(customerMessages.requestError);
    });
}

window.abrirModalEstadoCliente = abrirModalEstadoCliente;
window.confirmarCambioEstadoCliente = confirmarCambioEstadoCliente;
window.abrirEditarCliente = abrirEditarCliente;

function abrirModalTerminosCliente(clienteId, nombreCliente, terminosActuales) {
    document.getElementById('terminosClienteId').value = clienteId;
    document.getElementById('terminosClienteNombre').textContent = nombreCliente || customerMessages.customerFallbackName;

    const form = document.getElementById('formConfigurarTerminosCliente');
    if (!form) {
        return;
    }

    form.querySelectorAll('input[name="terminos_pago"]').forEach(function (input) {
        input.checked = input.value === terminosActuales;
    });

    const modal = new bootstrap.Modal(document.getElementById('configurarTerminosClienteModal'));
    modal.show();
}

function guardarTerminosCliente() {
    const clienteId = document.getElementById('terminosClienteId').value;
    const selectedInput = document.querySelector('#formConfigurarTerminosCliente input[name="terminos_pago"]:checked');

    if (!clienteId || !selectedInput) {
        alert(customerMessages.termsSelectRequired);
        return;
    }

    fetch('/vendedores/configurar-terminos-cliente/', {
        method: 'POST',
        headers: {
            'X-CSRFToken': document.querySelector('[name=csrfmiddlewaretoken]').value,
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({
            cliente_id: clienteId,
            terminos_pago: selectedInput.value
        })
    })
    .then(response => response.json())
    .then(data => {
        if (!data.success) {
            alert(`${customerMessages.termsErrorPrefix} ${data.message || customerMessages.unknownError}`);
            return;
        }

        const modalTerminos = bootstrap.Modal.getInstance(document.getElementById('configurarTerminosClienteModal'));
        if (modalTerminos) {
            modalTerminos.hide();
        }

        document.getElementById('tituloExitoTerminosCliente').textContent = customerMessages.termsSuccessTitle;
        document.getElementById('textoExitoTerminosCliente').textContent = customerMessages.termsSuccessBody;

        const modalExito = new bootstrap.Modal(document.getElementById('exitoTerminosClienteModal'));
        modalExito.show();

        setTimeout(function () {
            location.reload();
        }, 1600);
    })
    .catch(function (error) {
        console.error('Error:', error);
        alert(customerMessages.requestError);
    });
}

window.abrirModalTerminosCliente = abrirModalTerminosCliente;
window.guardarTerminosCliente = guardarTerminosCliente;

function formatMoney(value) {
    const amount = Number(value || 0);
    if (Number.isNaN(amount)) {
        return '$0.00';
    }
    return '$' + amount.toFixed(2);
}

function updateCreditLimitRemainingPreview() {
    const limitInput = document.getElementById('limiteCreditoMonto');
    const totalOpenLabel = document.getElementById('limiteCreditoTotalOpen');
    const remainingLabel = document.getElementById('limiteCreditoRemaining');
    if (!limitInput || !totalOpenLabel || !remainingLabel) {
        return;
    }

    const totalOpen = Number(totalOpenLabel.dataset.totalOpen || '0');
    const limitValue = limitInput.value.trim();
    if (!limitValue) {
        remainingLabel.textContent = '-';
        return;
    }

    const limit = Number(limitValue);
    if (Number.isNaN(limit)) {
        remainingLabel.textContent = '-';
        return;
    }

    remainingLabel.textContent = formatMoney(Math.max(limit - totalOpen, 0));
}

function abrirModalLimiteCreditoCliente(clienteId, nombreCliente, limiteActual, dueBalance, totalOpenBalance, exceedsCreditLimit) {
    document.getElementById('limiteCreditoClienteId').value = clienteId;
    document.getElementById('limiteCreditoClienteNombre').textContent = nombreCliente || customerMessages.customerFallbackName;
    document.getElementById('limiteCreditoMonto').value = limiteActual || '';
    const dueBalanceLabel = document.getElementById('limiteCreditoDueBalance');
    const totalOpenLabel = document.getElementById('limiteCreditoTotalOpen');
    dueBalanceLabel.textContent = formatMoney(dueBalance);
    dueBalanceLabel.dataset.dueBalance = dueBalance || '0';
    totalOpenLabel.textContent = formatMoney(totalOpenBalance);
    totalOpenLabel.dataset.totalOpen = totalOpenBalance || '0';
    updateCreditLimitRemainingPreview();

    const modal = new bootstrap.Modal(document.getElementById('configurarLimiteCreditoClienteModal'));
    modal.show();
}

function guardarLimiteCreditoCliente() {
    const clienteId = document.getElementById('limiteCreditoClienteId').value;
    const limitInput = document.getElementById('limiteCreditoMonto');
    const limitValue = limitInput ? limitInput.value.trim() : '';

    if (!clienteId) {
        return;
    }

    if (limitValue && Number.isNaN(Number(limitValue))) {
        alert(customerMessages.creditLimitInvalid);
        return;
    }

    fetch('/vendedores/configurar-limite-credito-cliente/', {
        method: 'POST',
        headers: {
            'X-CSRFToken': document.querySelector('[name=csrfmiddlewaretoken]').value,
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({
            cliente_id: clienteId,
            credit_limit: limitValue
        })
    })
    .then(response => response.json())
    .then(data => {
        if (!data.success) {
            alert(`${customerMessages.creditLimitErrorPrefix} ${data.message || customerMessages.unknownError}`);
            return;
        }

        const modalLimite = bootstrap.Modal.getInstance(document.getElementById('configurarLimiteCreditoClienteModal'));
        if (modalLimite) {
            modalLimite.hide();
        }

        document.getElementById('tituloExitoLimiteCreditoCliente').textContent = customerMessages.creditLimitSuccessTitle;
        document.getElementById('textoExitoLimiteCreditoCliente').textContent = customerMessages.creditLimitSuccessBody;

        const modalExito = new bootstrap.Modal(document.getElementById('exitoLimiteCreditoClienteModal'));
        modalExito.show();

        setTimeout(function () {
            location.reload();
        }, 1600);
    })
    .catch(function (error) {
        console.error('Error:', error);
        alert(customerMessages.requestError);
    });
}

document.addEventListener('DOMContentLoaded', function () {
    const limitInput = document.getElementById('limiteCreditoMonto');
    if (limitInput) {
        limitInput.addEventListener('input', updateCreditLimitRemainingPreview);
    }
});

window.abrirModalLimiteCreditoCliente = abrirModalLimiteCreditoCliente;
window.guardarLimiteCreditoCliente = guardarLimiteCreditoCliente;

function resetAccesoPasswordVisibility() {
    const passwordInput = document.getElementById('accesoPassword');
    const toggleBtn = document.getElementById('accesoPasswordToggle');
    if (passwordInput) {
        passwordInput.type = 'password';
    }
    if (toggleBtn) {
        toggleBtn.setAttribute('aria-label', customerMessages.accessShowPassword);
        const icon = toggleBtn.querySelector('i');
        if (icon) {
            icon.className = 'bi bi-eye';
        }
    }
}

function toggleAccesoPasswordVisibility() {
    const passwordInput = document.getElementById('accesoPassword');
    const toggleBtn = document.getElementById('accesoPasswordToggle');
    if (!passwordInput || !toggleBtn) {
        return;
    }
    const showing = passwordInput.type === 'text';
    passwordInput.type = showing ? 'password' : 'text';
    toggleBtn.setAttribute(
        'aria-label',
        showing ? customerMessages.accessShowPassword : customerMessages.accessHidePassword
    );
    const icon = toggleBtn.querySelector('i');
    if (icon) {
        icon.className = showing ? 'bi bi-eye' : 'bi bi-eye-slash';
    }
}

function setAccesoModalMode(mode) {
    const modeInput = document.getElementById('accesoMode');
    const title = document.getElementById('accesoModalTitle');
    const hint = document.getElementById('accesoModalHint');
    const saveBtn = document.getElementById('accesoSaveButton');
    const unavailable = document.getElementById('accesoPasswordUnavailable');

    if (modeInput) {
        modeInput.value = mode;
    }
    if (unavailable) {
        unavailable.classList.add('d-none');
        unavailable.textContent = '';
    }

    if (mode === 'view') {
        if (title) {
            title.textContent = customerMessages.accessViewTitle;
        }
        if (hint) {
            hint.textContent = customerMessages.accessViewHint;
        }
        if (saveBtn) {
            saveBtn.textContent = customerMessages.accessUpdateButton;
        }
    } else {
        if (title) {
            title.textContent = customerMessages.accessCreateTitle;
        }
        if (hint) {
            hint.textContent = customerMessages.accessCreateHint;
        }
        if (saveBtn) {
            saveBtn.textContent = customerMessages.accessCreateButton;
        }
    }
}

function abrirModalAccesoCliente(clienteId, nombreCliente) {
    document.getElementById('accesoClienteId').value = clienteId;
    document.getElementById('accesoClienteNombre').textContent = nombreCliente || customerMessages.customerFallbackName;
    document.getElementById('accesoUsername').value = '';
    document.getElementById('accesoPassword').value = '';
    document.getElementById('accesoPasswordConfirm').value = '';
    setAccesoModalMode('create');
    resetAccesoPasswordVisibility();

    bindAccessPasswordValidation();
    updateAccessPasswordRules();

    const modal = new bootstrap.Modal(document.getElementById('configurarAccesoClienteModal'));
    modal.show();
}

function abrirModalVerAccesoCliente(clienteId) {
    if (!customerMessages.canViewWebPassword) {
        return;
    }

    fetch('/vendedores/acceso-cliente/' + encodeURIComponent(clienteId) + '/', {
        method: 'GET',
        headers: {
            'X-Requested-With': 'XMLHttpRequest'
        }
    })
    .then(function (response) { return response.json(); })
    .then(function (data) {
        if (!data.success) {
            alert((customerMessages.accessViewErrorPrefix || '') + ' ' + (data.message || customerMessages.unknownError));
            return;
        }

        document.getElementById('accesoClienteId').value = clienteId;
        document.getElementById('accesoClienteNombre').textContent = data.customer_name || customerMessages.customerFallbackName;
        document.getElementById('accesoUsername').value = data.username || '';
        document.getElementById('accesoPassword').value = data.password || '';
        document.getElementById('accesoPasswordConfirm').value = data.password || '';
        setAccesoModalMode('view');
        resetAccesoPasswordVisibility();

        const unavailable = document.getElementById('accesoPasswordUnavailable');
        if (unavailable && !data.password_available) {
            unavailable.textContent = customerMessages.accessPasswordUnavailable;
            unavailable.classList.remove('d-none');
            document.getElementById('accesoPassword').value = '';
            document.getElementById('accesoPasswordConfirm').value = '';
        }

        bindAccessPasswordValidation();
        updateAccessPasswordRules();

        const modal = new bootstrap.Modal(document.getElementById('configurarAccesoClienteModal'));
        modal.show();
    })
    .catch(function (error) {
        console.error('Error:', error);
        alert(customerMessages.requestError);
    });
}

function guardarAccesoCliente() {
    const clienteId = document.getElementById('accesoClienteId').value;
    const username = document.getElementById('accesoUsername').value.trim().toLowerCase();
    const password = document.getElementById('accesoPassword').value;
    const passwordConfirm = document.getElementById('accesoPasswordConfirm').value;
    const mode = (document.getElementById('accesoMode') || {}).value || 'create';

    if (!clienteId || !username || !password || !passwordConfirm) {
        alert(customerMessages.accessFillAllFields);
        return;
    }

    if (!accessPasswordMeetsRequirements(password, passwordConfirm)) {
        alert(customerMessages.accessPasswordRules);
        updateAccessPasswordRules();
        return;
    }

    fetch('/vendedores/configurar-acceso-cliente/', {
        method: 'POST',
        headers: {
            'X-CSRFToken': document.querySelector('[name=csrfmiddlewaretoken]').value,
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({
            cliente_id: clienteId,
            username: username,
            password: password,
            password_confirm: passwordConfirm
        })
    })
    .then(response => response.json())
    .then(data => {
        if (!data.success) {
            alert(`${customerMessages.accessErrorPrefix} ${data.message || customerMessages.unknownError}`);
            return;
        }

        const modalAcceso = bootstrap.Modal.getInstance(document.getElementById('configurarAccesoClienteModal'));
        if (modalAcceso) {
            modalAcceso.hide();
        }

        const isUpdate = mode === 'view';
        document.getElementById('tituloExitoAccesoCliente').textContent = isUpdate
            ? customerMessages.accessUpdateSuccessTitle
            : customerMessages.accessSuccessTitle;
        document.getElementById('textoExitoAccesoCliente').textContent = isUpdate
            ? customerMessages.accessUpdateSuccessBody
            : customerMessages.accessSuccessBody;

        const modalExito = new bootstrap.Modal(document.getElementById('exitoAccesoClienteModal'));
        modalExito.show();

        setTimeout(() => {
            location.reload();
        }, 1600);
    })
    .catch(error => {
        console.error('Error:', error);
        alert(customerMessages.requestError);
    });
}

window.abrirModalAccesoCliente = abrirModalAccesoCliente;
window.abrirModalVerAccesoCliente = abrirModalVerAccesoCliente;
window.guardarAccesoCliente = guardarAccesoCliente;
window.toggleAccesoPasswordVisibility = toggleAccesoPasswordVisibility;

const telefonoInput = document.getElementById('telefonoCliente');
if (telefonoInput) {
    telefonoInput.addEventListener('input', function() {
        this.value = this.value.replace(/[^0-9]/g, '');
        if (this.value.length > 10) {
            this.value = this.value.slice(0, 10);
        }
    });
}

