(function () {
  "use strict";

  const tours = {
    registration: [
      { element: "[data-ai-tour='login']", title: "Inicia sesión", description: "Haz clic aquí si ya tienes una cuenta.", advanceOnClick: true },
      { element: "[data-ai-tour='signup']", title: "Crea tu cuenta", description: "Haz clic en Sign Up para iniciar tu solicitud.", advanceOnClick: true },
      { element: "[data-ai-tour='first-name']", title: "Nombre", description: "Escribe tu primer nombre." },
      { element: "[data-ai-tour='last-name']", title: "Apellido", description: "Escribe tu apellido." },
      { element: "[data-ai-tour='business-id']", title: "Business ID", description: "Ingresa tu identificación comercial." },
      { element: "[data-ai-tour='upload-license']", title: "Upload License", description: "Carga tu certificado o licencia." },
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
      { element: "[data-ai-tour='cart']", title: "Mi orden", description: "Revisa comentarios y envía tu solicitud." }
    ],
    "quote-ready": [
      { element: "[data-ai-tour='quotes']", title: "Cotizaciones", description: "Abre tu cotización respondida." },
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

  function start(tourKey) {
    const steps = (tours[tourKey] || []).filter(function (step) {
      return document.querySelector(step.element);
    });
    if (!steps.length || typeof window.driver !== "function") {
      return false;
    }
    const driverObj = window.driver({
      animate: true,
      allowClose: true,
      showProgress: true,
      steps: steps.map(function (step) {
        return {
          element: step.element,
          advanceOnClick: !!step.advanceOnClick,
          waitForElement: 5000,
          popover: {
            title: step.title,
            description: step.description,
            showButtons: step.advanceOnClick ? ["close"] : ["next", "previous", "close"]
          }
        };
      }),
      onDestroyed: function () {
        const completed = driverObj.getActiveIndex() === steps.length - 1;
        persist(tourKey, driverObj.getActiveIndex() || 0, completed, !completed);
      }
    });
    driverObj.drive();
    return true;
  }

  window.TortillaAssistantTours = { start: start };
  document.addEventListener("click", function (event) {
    const target = event.target.closest("[data-ai-tour-start]");
    if (target) start(target.dataset.aiTourStart);
  });
}());
