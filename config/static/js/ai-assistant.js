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
      if (action.external) {
        link.target = "_blank";
        link.rel = "noopener noreferrer";
      }
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
      if (action.kind === "contact_handoff") {
        link.addEventListener("click", function (event) {
          event.preventDefault();
          appendMessage(
            document.querySelector("[data-ai-assistant] [data-ai-messages]"),
            "Claro. Puedes contactar a nuestro equipo por WhatsApp, llamada, SMS o correo.",
            false
          );
          const contactLink = document.querySelector("[data-ai-whatsapp]");
          if (contactLink && !contactLink.hidden) {
            const whatsapp = contactLink.cloneNode(true);
            whatsapp.hidden = false;
            container.appendChild(whatsapp);
          }
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
            highlightAddedCatalogProduct(
              result.presentation_id || confirmation.presentation_id,
              result.quantity_added || confirmation.quantity
            );
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

  function highlightAddedCatalogProduct(presentationId, quantity) {
    if (!presentationId) return;
    const option = document.querySelector(".presentacion-select option[value='" + CSS.escape(String(presentationId)) + "']");
    if (!option) return;
    const card = option.closest(".producto-card");
    if (!card) return;
    const presentationSelect = card.querySelector(".presentacion-select");
    const quantityInput = card.querySelector(".cantidad-input");
    if (presentationSelect) {
      presentationSelect.value = String(presentationId);
      presentationSelect.dispatchEvent(new Event("change", { bubbles: true }));
    }
    if (quantityInput && quantity) quantityInput.value = quantity;
    card.classList.remove("ai-assistant-product-added");
    void card.offsetWidth;
    card.classList.add("ai-assistant-product-added");
    window.setTimeout(function () {
      card.classList.remove("ai-assistant-product-added");
    }, 2400);
    card.scrollIntoView({ behavior: "smooth", block: "center", inline: "nearest" });
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
    const whatsappFloat = document.querySelector(".whatsapp-float");
    const whatsappAction = root.querySelector("[data-ai-whatsapp]");
    let conversationId = "";
    let booted = false;

    if (whatsappFloat && whatsappAction) {
      whatsappAction.href = whatsappFloat.href;
      whatsappAction.hidden = false;
    }

    function setPanelOpen(open) {
      panel.classList.toggle("is-open", open);
      launcher.setAttribute("aria-expanded", open ? "true" : "false");
      document.body.classList.toggle("ai-assistant-open", open);
      if (open) input.focus();
    }

    function rememberContinuation(message) {
      try {
        window.sessionStorage.setItem("tortilla-assistant-continuation", JSON.stringify({
          message: message,
          createdAt: Date.now()
        }));
      } catch (_error) {}
    }

    function consumeContinuation() {
      try {
        const raw = window.sessionStorage.getItem("tortilla-assistant-continuation");
        if (!raw) return null;
        window.sessionStorage.removeItem("tortilla-assistant-continuation");
        const continuation = JSON.parse(raw);
        return Date.now() - continuation.createdAt < 10 * 60 * 1000 ? continuation : null;
      } catch (_error) {
        return null;
      }
    }

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
              setPanelOpen(true);
            }
          } else {
            appendMessage(messages, context.welcome_message, false);
            const initialActions = Array.isArray(context.actions) && context.actions.length
              ? context.actions
              : [context.next_recommended_action].filter(Boolean);
            renderActions(actions, initialActions);
          }
          renderPendingEvent(root, context, messages, actions);
          const continuation = consumeContinuation();
          if (continuation && !requestedTour) {
            appendMessage(messages, continuation.message, false);
            setPanelOpen(true);
          }
        })
        .catch(function () {});
    }

    launcher.addEventListener("click", function () {
      boot();
      setPanelOpen(!panel.classList.contains("is-open"));
    });

    actions.addEventListener("click", function (event) {
      const link = event.target.closest("a[href]");
      if (!link || link.target === "_blank" || link.getAttribute("href") === "#") return;
      rememberContinuation("Excelente, ya estamos en esta sección. ¿Quieres que te ayude con el siguiente paso?");
      setPanelOpen(false);
    });

    const requestedTour = new URLSearchParams(window.location.search).get("ai_tour");
    window.setTimeout(function () {
      if (!requestedTour && root.dataset.tourActive !== "true") boot({ autoOpen: true });
    }, 4000);

    window.addEventListener("tortilla-assistant-tour-started", function (event) {
      root.dataset.tourActive = "true";
      setPanelOpen(false);
    });

    window.addEventListener("tortilla-assistant-tour-dismissed", function (event) {
      root.dataset.tourActive = "false";
      const tourId = event.detail && event.detail.tourId;
      const resumeIndex = event.detail && event.detail.resumeIndex;
      if (!tourId) return;
      boot();
      setPanelOpen(true);
      appendMessage(messages, "La guía se pausó. Puedes retomarla cuando quieras.", false);
      renderActions(actions, [{
        label: "Reanudar guía paso a paso",
        url: "#",
        tour_id: tourId,
        resume_index: Number.isInteger(resumeIndex) ? resumeIndex : 0
      }]);
    });

    window.addEventListener("tortilla-assistant-tour-completed", function () {
      root.dataset.tourActive = "false";
      window.setTimeout(function () {
        boot();
        setPanelOpen(true);
        appendMessage(messages, "Excelente, completaste la guía. ¿Quieres que te ayude a continuar con tu compra?", false);
      }, 300);
    });

    window.addEventListener("tortilla-login-failed", function () {
      if (!root.dataset.loginFailureUrl) return;
      jsonFetch(root.dataset.loginFailureUrl, { method: "POST", body: "{}" })
        .then(function (result) {
          if (!result.intervene) return;
          const showHelp = function () {
            boot();
            setPanelOpen(true);
            appendMessage(messages, result.message, false);
            renderActions(actions, result.actions);
          };
          const errorModal = document.getElementById("loginErrorModal");
          if (errorModal && errorModal.classList.contains("show")) {
            errorModal.addEventListener("hidden.bs.modal", showHelp, { once: true });
          } else {
            showHelp();
          }
        })
        .catch(function () {});
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

    input.addEventListener("keydown", function (event) {
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        if (typeof form.requestSubmit === "function") form.requestSubmit();
        else form.dispatchEvent(new Event("submit", { cancelable: true }));
      }
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
