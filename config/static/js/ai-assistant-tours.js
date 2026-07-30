(function () {
  "use strict";

  const tours = {
    registration: [
      { element: "[data-ai-tour='login']", title: "¿Ya tienes cuenta?", description: "Si ya fuiste aprobado, haz clic en Login. Si eres nuevo, presiona Next para crear una cuenta." },
      { element: "[data-ai-tour='signup']", title: "Crea una cuenta", description: "Haz clic aquí para abrir el formulario de registro.", advanceOnClick: true },
      { element: "[data-ai-tour='first-name']", title: "Nombre", description: "Escribe tu primer nombre." },
      { element: "[data-ai-tour='last-name']", title: "Apellido", description: "Escribe tu apellido." },
      { element: "[data-ai-tour='business-id']", title: "Business ID", description: "Ingresa tu identificación comercial." },
      { element: "[data-ai-tour='mobile-contact-number']", title: "Número de contacto móvil", description: "Ingresa tu número móvil de 10 dígitos." },
      { element: "[data-ai-tour='continue-personal']", title: "Continuar", description: "Cuando termines estos datos, presiona Continuar.", advanceOnClick: true },
      { element: "[data-ai-tour='username']", title: "Usuario", description: "Crea el usuario para ingresar al portal." },
      { element: "[data-ai-tour='email']", title: "Correo electrónico", description: "Ingresa un correo al que tengas acceso." },
      { element: "[data-ai-tour='password']", title: "Contraseña", description: "Crea y confirma una contraseña segura." },
      { element: "[data-ai-tour='confirm-password']", title: "Confirmar contraseña", description: "Escribe nuevamente la misma contraseña para confirmarla." },
      { element: "[data-ai-tour='continue-credentials']", title: "Continuar", description: "Avanza a la información comercial.", advanceOnClick: true },
      { element: "[data-ai-tour='business-name']", title: "Nombre legal del negocio", description: "Ingresa el nombre legal de tu empresa." },
      { element: "[data-ai-tour='sales-tax-example']", title: "Ejemplo de Sales Tax", description: "Haz clic en View example. Al cerrar el ejemplo, te mostraré el campo donde debes escribir ese número.", pauseForModal: "#taxExampleModal" },
      { element: "[data-ai-tour='sales-tax']", title: "Sales Tax Number", description: "Escribe aquí el número que viste en el ejemplo." },
      { element: "[data-ai-tour='business-phone']", title: "Teléfono comercial", description: "Ingresa el teléfono principal de tu negocio." },
      { element: "[data-ai-tour='business-email']", title: "Correo comercial", description: "Ingresa el correo de contacto de tu negocio." },
      { element: "[data-ai-tour='primary-address']", title: "Dirección principal", description: "Ingresa la dirección física principal del negocio." },
      { element: "[data-ai-tour='address-line-2']", title: "Dirección adicional", description: "Este campo es opcional, por ejemplo apartamento o local." },
      { element: "[data-ai-tour='state']", title: "Estado", description: "Ingresa el estado o departamento del negocio." },
      { element: "[data-ai-tour='city']", title: "Ciudad", description: "Ingresa la ciudad del negocio." },
      { element: "[data-ai-tour='zip-code']", title: "Código postal", description: "Ingresa el ZIP code de la dirección." },
      { element: "[data-ai-tour='country']", title: "País", description: "El país está preconfigurado como USA." },
      { element: "[data-ai-tour='certificate-example']", title: "Ejemplo de certificado", description: "Haz clic en View example. Al cerrar el ejemplo, te mostraré dónde cargar tu archivo.", pauseForModal: "#taxExampleModal" },
      { element: "[data-ai-tour='upload-license']", title: "Certificado o licencia", description: "Haz clic aquí para cargar el certificado requerido.", advanceOnClick: true },
      { element: "[data-ai-tour='certify-information']", title: "Confirmar información", description: "Marca esta casilla después de verificar que toda la información es correcta." },
      { element: "[data-ai-tour='submit-registration']", title: "Enviar solicitud", description: "Envía la solicitud para aprobación." }
    ],
    "approved-login": [
      { element: "input[name='username']", title: "Usuario", description: "Ingresa tu usuario." },
      { element: "input[name='password']", title: "Contraseña", description: "Ingresa tu contraseña." },
      { element: "button[type='submit']", title: "Iniciar sesión", description: "Haz clic para entrar al catálogo.", advanceOnClick: true }
    ],
    "first-order": [
      { element: "#buscador", title: "Busca productos", description: "Busca por nombre o categoría." },
      { element: "[data-ai-tour='promotions']", title: "Promociones", description: "Revisa las promociones aplicables antes de agregar." },
      { element: "[data-ai-tour='add-order']", title: "Agregar a la orden", description: "Agrega el producto y ajusta la cantidad." },
      { element: "[data-ai-tour='cart']", title: "Mi orden", description: "Revisa comentarios y envía tu solicitud." },
      { element: "[data-ai-tour='submit-order']", title: "Enviar solicitud", description: "Cuando revises cantidades, envía tu solicitud para que nuestro equipo prepare la cotización." }
    ],
    "quote-ready": [
      { element: "[data-ai-tour='quote-open']", title: "Tu cotización está lista", description: "Haz clic para abrirla y revisar productos, cantidades y precios.", advanceOnClick: true },
    ],
    "quote-detail": [
      { element: "[data-ai-tour='quote-lines']", title: "Cantidades", description: "Revisa o ajusta tus cantidades." },
      { element: "[data-ai-tour='quote-accept']", title: "Aceptar", description: "Acepta la cotización cuando estés listo." }
    ],
    reorder: [
      { element: "[data-ai-tour='order-history']", title: "Historial", description: "Abre un pedido anterior." },
      { element: "[data-ai-tour='reorder']", title: "Reordenar", description: "Carga el pedido en Mi orden para revisarlo." }
    ]
  };

  function persist(tourKey, step, completed, dismissed) {
    const token = (document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/) || [])[1];
    fetch("/ai-assistant/tours/" + encodeURIComponent(tourKey) + "/progress/", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": decodeURIComponent(token || "") },
      body: JSON.stringify({ current_step: step || 0, completed: !!completed, dismissed: !!dismissed })
    }).catch(function () {});
  }

  function findVisibleElement(selector) {
    return Array.from(document.querySelectorAll(selector)).find(function (element) {
      return element.offsetParent !== null;
    });
  }

  function start(tourKey, startIndex) {
    const steps = tours[tourKey] || [];
    let lastActiveIndex = 0;
    let resumingAfterModal = false;
    let modalTriggerBoundForIndex = null;
    let refreshQueued = false;
    const refreshTourPosition = function () {
      if (refreshQueued) return;
      refreshQueued = true;
      window.requestAnimationFrame(function () {
        refreshQueued = false;
        if (driverObj.isActive()) driverObj.refresh();
      });
    };
    const createDriver = window.driver && window.driver.js && typeof window.driver.js.driver === "function"
      ? window.driver.js.driver
      : (typeof window.driver === "function" ? window.driver : null);
    if (!steps.length || !createDriver) {
      return false;
    }
    const driverObj = createDriver({
      animate: true,
      allowClose: true,
      overlayClickBehavior: "close",
      allowScroll: true,
      showProgress: true,
      steps: steps.map(function (step) {
        return {
          element: function () { return findVisibleElement(step.element); },
          advanceOnClick: !!step.advanceOnClick,
          waitForElement: 5000,
          popover: {
            title: step.title,
            description: step.description,
            showButtons: step.advanceOnClick ? ["previous", "close"] : ["next", "previous", "close"]
          }
        };
      }),
      onDestroyed: function () {
        window.removeEventListener("scroll", refreshTourPosition, true);
        const completed = lastActiveIndex === steps.length - 1;
        persist(tourKey, lastActiveIndex, completed, !completed);
        if (!completed && !resumingAfterModal) {
          window.dispatchEvent(new CustomEvent("tortilla-assistant-tour-dismissed", {
            detail: { tourId: tourKey, resumeIndex: lastActiveIndex }
          }));
        }
      },
      onHighlightStarted: function (_element, _step, context) {
        lastActiveIndex = typeof context.state?.activeIndex === "number"
          ? context.state.activeIndex
          : (driverObj.getActiveIndex() || 0);
        modalTriggerBoundForIndex = null;
      },
      onPrevClick: function (_element, _step, context) {
        const activeIndex = typeof context.state?.activeIndex === "number"
          ? context.state.activeIndex
          : (driverObj.getActiveIndex() || 0);
        const formStepToRestore = {
          12: 3, // Business information -> platform access.
          7: 2   // Platform access -> personal details.
        }[activeIndex];
        if (formStepToRestore && typeof window.prevStepModal === "function") {
          window.prevStepModal(formStepToRestore);
          window.setTimeout(function () { driverObj.movePrevious(); }, 200);
          return;
        }
        driverObj.movePrevious();
      },
      onHighlighted: function () {
        const activeElement = driverObj.getActiveElement();
        if (activeElement) {
          activeElement.scrollIntoView({ block: "center", inline: "nearest", behavior: "smooth" });
          window.setTimeout(refreshTourPosition, 250);
        }
        const step = steps[lastActiveIndex];
        if (!step || !step.pauseForModal) return;
        const trigger = findVisibleElement(step.element);
        const modal = document.querySelector(step.pauseForModal);
        if (!trigger || !modal || modalTriggerBoundForIndex === lastActiveIndex) return;
        modalTriggerBoundForIndex = lastActiveIndex;
        trigger.addEventListener("click", function () {
          const resumeIndex = lastActiveIndex + 1;
          resumingAfterModal = true;
          let resumed = false;
          const resumeGuide = function () {
            if (resumed) return;
            resumed = true;
            window.setTimeout(function () { start(tourKey, resumeIndex); }, 250);
          };
          modal.addEventListener("hidden.bs.modal", resumeGuide, { once: true });
          modal.querySelectorAll(".btn-close, [data-bs-dismiss='modal'], .modal-footer button").forEach(function (closeButton) {
            closeButton.addEventListener("click", function () {
              window.setTimeout(resumeGuide, 350);
            }, { once: true });
          });
          driverObj.destroy();
        }, { once: true });
      }
    });
    window.addEventListener("scroll", refreshTourPosition, true);
    driverObj.drive(Number.isInteger(startIndex) ? startIndex : 0);
    return true;
  }

  window.TortillaAssistantTours = { start: start };
  document.addEventListener("click", function (event) {
    const quoteLink = event.target.closest("[data-ai-tour='quote-open']");
    if (quoteLink && quoteLink.href) {
      const quoteUrl = new URL(quoteLink.href, window.location.origin);
      quoteUrl.searchParams.set('ai_tour', 'quote-detail');
      quoteLink.href = quoteUrl.toString();
    }
  });
  document.addEventListener("DOMContentLoaded", function () {
    const requestedTour = new URLSearchParams(window.location.search).get("ai_tour");
    if (requestedTour && Object.prototype.hasOwnProperty.call(tours, requestedTour)) {
      window.setTimeout(function () { start(requestedTour); }, 450);
    }
  });
}());
