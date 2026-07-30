(function () {
  "use strict";

  function csrfToken() {
    const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : "";
  }

  function jsonFetch(url, options) {
    return fetch(url, Object.assign({
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": csrfToken(),
        "X-Requested-With": "XMLHttpRequest"
      }
    }, options || {})).then(function (response) {
      return response.json().then(function (payload) {
        if (!response.ok) throw new Error(payload.error || "Assistant request failed.");
        return payload;
      });
    });
  }

  function appendMessage(container, text, isUser) {
    const message = document.createElement("div");
    message.className = "ai-assistant-message" + (isUser ? " is-user" : "");
    message.textContent = text;
    container.appendChild(message);
    container.scrollTop = container.scrollHeight;
  }

  function renderActions(container, actions) {
    container.replaceChildren();
    (actions || []).forEach(function (action) {
      const link = document.createElement("a");
      link.className = "btn btn-outline-primary btn-sm";
      link.href = action.url || "#";
      link.textContent = action.label || "Continuar";
      if (action.tour_id) {
        link.dataset.aiTourStart = action.tour_id;
        if (Number.isInteger(action.resume_index)) {
          link.addEventListener("click", function (event) {
            event.preventDefault();
            if (window.TortillaAssistantTours) {
              window.TortillaAssistantTours.start(action.tour_id, action.resume_index);
            }
          });
        } else if (action.url && action.url !== "#") {
          const targetUrl = new URL(action.url, window.location.origin);
          targetUrl.searchParams.set("ai_tour", action.tour_id);
          link.href = targetUrl.pathname + targetUrl.search + targetUrl.hash;
        } else {
          link.addEventListener("click", function (event) {
            event.preventDefault();
            if (window.TortillaAssistantTours) {
              window.TortillaAssistantTours.start(action.tour_id);
            }
          });
        }
      }
      if (action.kind === "dismiss_proactive") {
        link.addEventListener("click", function (event) {
          event.preventDefault();
          container.replaceChildren();
          appendMessage(
            document.querySelector("[data-ai-assistant] [data-ai-messages]"),
            "Perfecto. Explora con calma; estaré disponible cuando me necesites.",
            false
          );
        });
      }
      container.appendChild(link);
    });
  }

  function renderConfirmations(container, confirmations, root, messages) {
    (confirmations || []).forEach(function (confirmation) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "btn btn-success btn-sm";
      button.textContent = confirmation.label || "Confirm";
      button.addEventListener("click", function () {
        button.disabled = true;
        jsonFetch(root.dataset.confirmActionUrl.replace("__action__", confirmation.id), { method: "POST", body: "{}" })
          .then(function (result) {
            appendMessage(messages, result.message, false);
            button.remove();
          })
          .catch(function (error) {
            button.disabled = false;
            appendMessage(messages, error.message || "This action could not be completed.", false);
          });
      });
      container.appendChild(button);
    });
  }

  function renderPendingEvent(root, context, messages, actions) {
    const event = context.pending_event;
    if (!event) return;
    const messagesByType = {
      REGISTRATION_SUBMITTED: "Gracias. Recibimos tu solicitud y nuestro equipo revisará tus documentos. Te enviaremos una respuesta pronto.",
      ACCOUNT_APPROVED: "Tu cuenta fue aprobada. Puedo guiarte para iniciar sesión.",
      ACCOUNT_NEEDS_CORRECTION: "Tu solicitud necesita una corrección. Puedo ayudarte a revisar el siguiente paso.",
      QUOTE_READY: "Tengo buenas noticias: tu cotización está lista para revisar.",
      ORDER_DISPATCHED: "Tu pedido fue despachado. Puedes consultar su estado.",
      ORDER_DELIVERED: "Tu pedido fue marcado como entregado.",
    };
    appendMessage(messages, messagesByType[event.type] || "Tienes una actualización de tu cuenta.", false);
    const action = document.createElement("button");
    action.type = "button";
    action.className = "btn btn-primary btn-sm";
    action.textContent = "Ver actualización";
    action.addEventListener("click", function () {
      jsonFetch(root.dataset.consumeEventUrl.replace("__event__", event.id), { method: "POST", body: "{}" })
        .then(function () {
          const tourId = (event.payload || {}).tour_id;
          if (tourId && window.TortillaAssistantTours) window.TortillaAssistantTours.start(tourId);
          action.remove();
        })
        .catch(function () {});
    });
    actions.appendChild(action);
  }

  document.addEventListener("DOMContentLoaded", function () {
    const root = document.querySelector("[data-ai-assistant]");
    if (!root) return;
    const launcher = root.querySelector("[data-ai-launcher]");
    const panel = root.querySelector("[data-ai-panel]");
    const form = root.querySelector("[data-ai-form]");
    const input = root.querySelector("[data-ai-input]");
    const messages = root.querySelector("[data-ai-messages]");
    const actions = root.querySelector("[data-ai-actions]");
    const deleteHistory = root.querySelector("[data-ai-delete-history]");
    let conversationId = "";
    let booted = false;

    function ensureConversation() {
      if (conversationId) return Promise.resolve(conversationId);
      return jsonFetch(root.dataset.conversationUrl, {
        method: "POST",
        body: JSON.stringify({ page: root.dataset.page || window.location.pathname, language: document.documentElement.lang || "es" })
      }).then(function (payload) {
        conversationId = payload.conversation_id;
        return conversationId;
      });
    }

    function boot(options) {
      options = options || {};
      if (booted) return;
      booted = true;
      fetch(root.dataset.contextUrl + "?page=" + encodeURIComponent(root.dataset.page || ""), { credentials: "same-origin" })
        .then(function (response) { return response.json(); })
        .then(function (context) {
          if (!context.enabled) {
            launcher.hidden = true;
            return;
          }
          root.querySelector("[data-ai-name]").textContent = context.assistant_name;
          if (context.proactive) {
            appendMessage(messages, context.proactive.message, false);
            renderActions(actions, context.proactive.actions);
            if (options.autoOpen) {
              panel.classList.add("is-open");
              launcher.setAttribute("aria-expanded", "true");
              input.focus();
            }
          } else {
            appendMessage(messages, context.welcome_message, false);
            const initialActions = Array.isArray(context.actions) && context.actions.length
              ? context.actions
              : [context.next_recommended_action].filter(Boolean);
            renderActions(actions, initialActions);
          }
          renderPendingEvent(root, context, messages, actions);
        })
        .catch(function () {});
    }

    launcher.addEventListener("click", function () {
      boot();
      panel.classList.toggle("is-open");
      launcher.setAttribute("aria-expanded", panel.classList.contains("is-open") ? "true" : "false");
      if (panel.classList.contains("is-open")) input.focus();
    });

    window.setTimeout(function () { boot({ autoOpen: true }); }, 4000);

    window.addEventListener("tortilla-assistant-tour-started", function (event) {
      const tourId = event.detail && event.detail.tourId;
      if (!["registration", "login", "password-recovery", "approved-login"].includes(tourId)) return;
      panel.classList.remove("is-open");
      launcher.setAttribute("aria-expanded", "false");
    });

    window.addEventListener("tortilla-assistant-tour-dismissed", function (event) {
      const tourId = event.detail && event.detail.tourId;
      const resumeIndex = event.detail && event.detail.resumeIndex;
      if (!tourId) return;
      boot();
      panel.classList.add("is-open");
      launcher.setAttribute("aria-expanded", "true");
      appendMessage(messages, "La guía se pausó. Puedes retomarla cuando quieras.", false);
      renderActions(actions, [{
        label: "Reanudar guía paso a paso",
        url: "#",
        tour_id: tourId,
        resume_index: Number.isInteger(resumeIndex) ? resumeIndex : 0
      }]);
      input.focus();
    });

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      const value = input.value.trim();
      if (!value) return;
      input.value = "";
      appendMessage(messages, value, true);
      ensureConversation()
        .then(function (id) {
          return jsonFetch(root.dataset.messageUrl.replace("__conversation__", id), {
            method: "POST",
            body: JSON.stringify({ message: value })
          });
        })
        .then(function (result) {
          appendMessage(messages, result.message, false);
          renderActions(actions, result.suggested_actions);
          renderConfirmations(actions, result.confirmation_actions, root, messages);
          if (result.tour_id && window.TortillaAssistantTours) {
            window.TortillaAssistantTours.start(result.tour_id);
          }
        })
        .catch(function (error) {
          appendMessage(messages, error.message || "No pude responder ahora. Inténtalo de nuevo.", false);
        });
    });

    deleteHistory.addEventListener("click", function () {
      jsonFetch(root.dataset.deleteHistoryUrl, { method: "POST", body: "{}" })
        .then(function () {
          conversationId = "";
          messages.replaceChildren();
          actions.replaceChildren();
          appendMessage(messages, "Your assistant history was cleared.", false);
        })
        .catch(function (error) {
          appendMessage(messages, error.message || "Could not clear history.", false);
        });
    });

  });
}());
