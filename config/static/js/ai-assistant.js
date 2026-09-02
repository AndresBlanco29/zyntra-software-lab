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
        if (!response.ok) {
          const error = new Error(payload.error || "Assistant request failed.");
          error.status = response.status;
          throw error;
        }
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

  const PROMOTION_CARDS_SHOWN = 4;

  // Cards belong in the conversation stream: rendering them in the actions bar
  // made it grow until it covered the previous messages and could not scroll.
  function renderPromotionCards(container, cards, catalogUrl) {
    const all = cards || [];
    all.slice(0, PROMOTION_CARDS_SHOWN).forEach(function (card) {
      const panel = document.createElement("article");
      panel.className = "ai-assistant-promotion-card";
      const title = document.createElement("strong");
      title.textContent = "🎁 PROMOCIÓN · " + card.product_name;
      const detail = document.createElement("div");
      detail.className = "ai-assistant-promotion-card__detail";
      detail.textContent = (card.benefits || []).join(" · ") || card.description || card.promotion_name;
      if (card.expires_at) {
        const validity = document.createElement("div");
        validity.className = "ai-assistant-promotion-card__validity";
        validity.textContent = "Vigente hasta " + new Date(card.expires_at).toLocaleDateString();
        panel.append(validity);
      }
      const link = document.createElement("a");
      link.className = "btn btn-outline-primary btn-sm";
      link.href = card.catalog_url;
      link.textContent = "Ver producto";
      const quoteLink = document.createElement("a");
      quoteLink.className = "btn btn-primary btn-sm";
      quoteLink.href = card.catalog_url;
      quoteLink.textContent = "Agregar a cotización";
      panel.prepend(title, detail);
      panel.append(link, quoteLink);
      container.appendChild(panel);
    });
    if (all.length > PROMOTION_CARDS_SHOWN && catalogUrl) {
      const more = document.createElement("a");
      more.className = "ai-assistant-promotion-more";
      more.href = catalogUrl;
      more.textContent = "Ver las " + all.length + " promociones en el catálogo";
      container.appendChild(more);
    }
    if (all.length) {
      container.scrollTop = container.scrollHeight;
    }
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
          const rootEl = document.querySelector("[data-ai-assistant]");
          container.replaceChildren();
          appendMessage(
            rootEl.querySelector("[data-ai-messages]"),
            "Perfecto. Explora con calma; estaré disponible cuando me necesites.",
            false
          );
          if (rootEl && typeof rootEl._aiDismissProactive === "function") {
            rootEl._aiDismissProactive();
          }
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
      if (action.kind === "promotion_access") {
        link.addEventListener("click", function (event) {
          event.preventDefault();
          container.replaceChildren();
          appendMessage(
            document.querySelector("[data-ai-assistant] [data-ai-messages]"),
            "Puedes ver nuestras promociones como invitado o iniciar sesión para solicitar una cotización.",
            false
          );
          const guestLink = document.createElement("a");
          guestLink.className = "btn btn-outline-primary btn-sm";
          guestLink.href = action.guest_url;
          guestLink.textContent = "👀 Ver catálogo como invitado";
          const loginButton = document.createElement("button");
          loginButton.type = "button";
          loginButton.className = "btn btn-primary btn-sm";
          loginButton.textContent = "🔑 Iniciar sesión";
          loginButton.addEventListener("click", function () {
            const modal = document.getElementById("loginModal");
            if (!modal || !window.bootstrap) return;
            modal.dataset.nextUrl = action.login_next;
            new window.bootstrap.Modal(modal).show();
          });
          container.append(guestLink, loginButton);
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
    const closeButton = root.querySelector("[data-ai-close]");
    const whatsappFloat = document.querySelector(".whatsapp-float");
    const whatsappAction = root.querySelector("[data-ai-whatsapp]");
    const langButtons = root.querySelectorAll("[data-ai-lang]");
    let conversationId = "";
    let booted = false;
    const DISMISS_KEY = "tortilla-assistant-dismissed";
    const CONTINUATION_KEY = "tortilla-assistant-continuation";
    const LANGUAGE_KEY = "ltg-ai-language";
    const requestedTour = new URLSearchParams(window.location.search).get("ai_tour");
    const UI_COPY = {
      en: {
        clear: "Clear",
        close: "Close",
        send: "Send",
        placeholder: "How can I help you?",
        whatsapp: "Talk with sales manager on WhatsApp",
        historyCleared: "Your assistant history was cleared.",
        historyError: "Could not clear history.",
        connectionRetry: "I'm updating my connection to help you. Please try again in a few seconds.",
        languageSwitched: "Sure — I'll keep helping you in English.",
        tourPaused: "The guide was paused. You can resume it whenever you want.",
        resumeTour: "Resume step-by-step guide",
        tourCompleted: "Great, you finished the guide. Want help continuing with your order?",
        continuation: "Great, we're in this section now. Want help with the next step?"
      },
      es: {
        clear: "Borrar",
        close: "Cerrar",
        send: "Enviar",
        placeholder: "¿En qué te puedo ayudar?",
        whatsapp: "Hablar con el gerente de ventas por WhatsApp",
        historyCleared: "Se borró el historial del asistente.",
        historyError: "No se pudo borrar el historial.",
        connectionRetry: "Estoy actualizando mi conexión para ayudarte. Inténtalo nuevamente en unos segundos.",
        languageSwitched: "Claro — seguiré ayudándote en español.",
        tourPaused: "La guía se pausó. Puedes retomarla cuando quieras.",
        resumeTour: "Reanudar guía paso a paso",
        tourCompleted: "Excelente, completaste la guía. ¿Quieres que te ayude a continuar con tu compra?",
        continuation: "Excelente, ya estamos en esta sección. ¿Quieres que te ayude con el siguiente paso?"
      }
    };

    function normalizeLanguage(value) {
      const raw = String(value || "").toLowerCase();
      return raw.indexOf("en") === 0 ? "en" : "es";
    }

    function readStoredLanguage() {
      try {
        const stored = window.localStorage.getItem(LANGUAGE_KEY);
        if (stored) return normalizeLanguage(stored);
      } catch (_error) {}
      return normalizeLanguage(document.documentElement.lang || "es");
    }

    let aiLanguage = readStoredLanguage();

    function uiCopy() {
      return UI_COPY[aiLanguage] || UI_COPY.es;
    }

    function persistLanguage(language) {
      aiLanguage = normalizeLanguage(language);
      try {
        window.localStorage.setItem(LANGUAGE_KEY, aiLanguage);
      } catch (_error) {}
    }

    function applyChromeLanguage() {
      const copy = uiCopy();
      root.querySelectorAll("[data-ai-i18n]").forEach(function (el) {
        const key = el.getAttribute("data-ai-i18n");
        if (copy[key]) el.textContent = copy[key];
      });
      root.querySelectorAll("[data-ai-i18n-placeholder]").forEach(function (el) {
        const key = el.getAttribute("data-ai-i18n-placeholder");
        if (copy[key]) el.setAttribute("placeholder", copy[key]);
      });
      langButtons.forEach(function (button) {
        button.setAttribute("aria-pressed", button.getAttribute("data-ai-lang") === aiLanguage ? "true" : "false");
      });
    }

    applyChromeLanguage();

    if (whatsappFloat && whatsappAction) {
      whatsappAction.href = whatsappFloat.href;
      whatsappAction.hidden = false;
    }

    function isTouchDevice() {
      return window.matchMedia("(hover: none), (pointer: coarse)").matches
        || ("ontouchstart" in window)
        || (navigator.maxTouchPoints || 0) > 0;
    }

    function isActivelyShopping() {
      try {
        const params = new URLSearchParams(window.location.search);
        // Catalog search reloads the page; never steal focus mid-order.
        if ((params.get("q") || "").trim()) return true;
      } catch (_error) {}
      return false;
    }

    function wasDismissedThisSession() {
      try {
        return window.sessionStorage.getItem(DISMISS_KEY) === "1";
      } catch (_error) {
        return false;
      }
    }

    function markDismissedThisSession() {
      try {
        window.sessionStorage.setItem(DISMISS_KEY, "1");
        window.sessionStorage.removeItem(CONTINUATION_KEY);
      } catch (_error) {}
    }

    function clearDismissedThisSession() {
      try {
        window.sessionStorage.removeItem(DISMISS_KEY);
      } catch (_error) {}
    }

    function setPanelOpen(open) {
      panel.classList.toggle("is-open", open);
      launcher.setAttribute("aria-expanded", open ? "true" : "false");
      document.body.classList.toggle("ai-assistant-open", open);
      // Autofocus on iOS opens the keyboard and jumps the fixed panel mid-screen.
      if (open && !isTouchDevice()) {
        try { input.focus({ preventScroll: true }); } catch (_error) { input.focus(); }
      } else if (!open && document.activeElement === input) {
        input.blur();
      }
    }

    function dismissProactive() {
      markDismissedThisSession();
      setPanelOpen(false);
      // Closing the sheet must leave the circular launcher visible for reopen.
      if (launcher) launcher.hidden = false;
      const url = root.dataset.dismissUrl;
      if (!url) return;
      jsonFetch(url, { method: "POST", body: "{}" }).catch(function () {});
    }

    root._aiDismissProactive = dismissProactive;

    function rememberContinuation(message) {
      try {
        if (wasDismissedThisSession()) return;
        window.sessionStorage.setItem(CONTINUATION_KEY, JSON.stringify({
          message: message,
          createdAt: Date.now()
        }));
      } catch (_error) {}
    }

    function consumeContinuation() {
      try {
        if (wasDismissedThisSession()) {
          window.sessionStorage.removeItem(CONTINUATION_KEY);
          return null;
        }
        const raw = window.sessionStorage.getItem(CONTINUATION_KEY);
        if (!raw) return null;
        window.sessionStorage.removeItem(CONTINUATION_KEY);
        const continuation = JSON.parse(raw);
        return Date.now() - continuation.createdAt < 10 * 60 * 1000 ? continuation : null;
      } catch (_error) {
        return null;
      }
    }

    function shouldAutoOpen(context) {
      if (wasDismissedThisSession()) return false;
      if (isActivelyShopping()) return false;
      if (root.dataset.tourActive === "true") return false;
      if (requestedTour) return false;
      const proactive = context && context.proactive;
      if (!proactive) return false;
      // Backend marks welcome / critical events; polite return greetings stay closed.
      return proactive.auto_open === true;
    }

    let resumedHistory = null;

    function ensureConversation(forceLanguageSync) {
      if (conversationId && !forceLanguageSync) return Promise.resolve(conversationId);
      return jsonFetch(root.dataset.conversationUrl, {
        method: "POST",
        body: JSON.stringify({
          page: root.dataset.page || window.location.pathname,
          language: aiLanguage
        })
      }).then(function (payload) {
        conversationId = payload.conversation_id;
        if (!forceLanguageSync) {
          resumedHistory = Array.isArray(payload.messages) ? payload.messages : [];
        }
        return conversationId;
      });
    }

    function switchLanguage(language) {
      const next = normalizeLanguage(language);
      if (next === aiLanguage) return;
      persistLanguage(next);
      applyChromeLanguage();
      const threadIsEmpty = !messages.querySelector(".ai-assistant-message");
      ensureConversation(true).catch(function () {});
      if (threadIsEmpty) {
        messages.replaceChildren();
        actions.replaceChildren();
        booted = false;
        boot({ autoOpen: panel.classList.contains("is-open") });
        return;
      }
      appendMessage(messages, uiCopy().languageSwitched, false);
    }

    langButtons.forEach(function (button) {
      button.addEventListener("click", function () {
        switchLanguage(button.getAttribute("data-ai-lang"));
      });
    });

    function renderResumedThread() {
      if (!resumedHistory || !resumedHistory.length) return false;
      resumedHistory.forEach(function (item) {
        appendMessage(messages, item.content, item.role === "user");
      });
      return true;
    }

    function boot(options) {
      options = options || {};
      if (booted) return;
      booted = true;
      applyChromeLanguage();
      const contextUrl = root.dataset.contextUrl
        + "?page=" + encodeURIComponent(root.dataset.page || "")
        + "&language=" + encodeURIComponent(aiLanguage);
      fetch(contextUrl, { credentials: "same-origin" })
        .then(function (response) { return response.json(); })
        .then(function (context) {
          if (!context.enabled) {
            // Hide only if the visitor never opened the panel; never strip the FAB
            // out from under an already-open (or just-closed) chat session.
            if (!panel.classList.contains("is-open") && !conversationId) {
              launcher.hidden = true;
              setPanelOpen(false);
            }
            return;
          }
          launcher.hidden = false;
          root.querySelector("[data-ai-name]").textContent = context.assistant_name;
          if (context.language) {
            persistLanguage(context.language);
            applyChromeLanguage();
          }
          if (context.authenticated) {
            // Keep one thread for the whole signed-in session, across pages.
            return ensureConversation()
              .then(function () {
                if (renderResumedThread()) {
                  renderPendingEvent(root, context, messages, actions);
                  // Existing thread = customer already met the assistant; stay minimized.
                  return;
                }
                renderInitialMessage(context, options);
              })
              .catch(function () { renderInitialMessage(context, options); });
          }
          renderInitialMessage(context, options);
        })
        .catch(function () {});
    }

    function maybeAutoOpen(context, options) {
      options = options || {};
      if (!options.autoOpen) return;
      if (!shouldAutoOpen(context)) return;
      setPanelOpen(true);
    }

    function renderInitialMessage(context, options) {
      options = options || {};
      if (context.proactive) {
        appendMessage(messages, context.proactive.message, false);
        renderActions(actions, context.proactive.actions);
      } else {
        appendMessage(messages, context.welcome_message, false);
        const initialActions = Array.isArray(context.actions) && context.actions.length
          ? context.actions
          : [context.next_recommended_action].filter(Boolean);
        renderActions(actions, initialActions);
      }
      renderPendingEvent(root, context, messages, actions);
      const continuation = consumeContinuation();
      if (continuation && !requestedTour && !wasDismissedThisSession() && !isActivelyShopping()) {
        appendMessage(messages, continuation.message, false);
        setPanelOpen(true);
        return;
      }
      maybeAutoOpen(context, options);
    }

    launcher.addEventListener("click", function () {
      const opening = !panel.classList.contains("is-open");
      if (opening) clearDismissedThisSession();
      launcher.hidden = false;
      boot();
      setPanelOpen(opening);
      if (!opening) dismissProactive();
    });

    if (closeButton) {
      closeButton.addEventListener("click", function () {
        dismissProactive();
      });
    }

    actions.addEventListener("click", function (event) {
      const link = event.target.closest("a[href]");
      if (!link || link.target === "_blank" || link.getAttribute("href") === "#") return;
      const href = String(link.getAttribute("href") || "");
      const goingToCatalog = /catalogo|catalog|product/i.test(href);
      if (goingToCatalog) {
        // Customer chose to shop — keep the assistant available via the bubble, not as a popup.
        markDismissedThisSession();
        if (root.dataset.dismissUrl) {
          jsonFetch(root.dataset.dismissUrl, { method: "POST", body: "{}" }).catch(function () {});
        }
      } else {
        rememberContinuation(uiCopy().continuation);
      }
      setPanelOpen(false);
    });

    window.setTimeout(function () {
      if (!requestedTour && root.dataset.tourActive !== "true") {
        boot({ autoOpen: true });
      } else {
        boot();
      }
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
      appendMessage(messages, uiCopy().tourPaused, false);
      renderActions(actions, [{
        label: uiCopy().resumeTour,
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
        appendMessage(messages, uiCopy().tourCompleted, false);
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

    document.addEventListener("show.bs.modal", function (event) {
      if (event.target && event.target.id === "loginModal") {
        setPanelOpen(false);
      }
    });

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      const value = input.value.trim();
      if (!value) return;
      input.value = "";
      appendMessage(messages, value, true);
      function sendMessage(retryAfterMissingConversation) {
        return ensureConversation()
        .then(function (id) {
          return jsonFetch(root.dataset.messageUrl.replace("__conversation__", id), {
            method: "POST",
            body: JSON.stringify({ message: value, page: root.dataset.page || window.location.pathname })
          });
        }).catch(function (error) {
          if (error.status === 404 && !retryAfterMissingConversation) {
            conversationId = "";
            return sendMessage(true);
          }
          throw error;
        });
      }
      sendMessage(false)
        .then(function (result) {
          if (result.language) {
            persistLanguage(result.language);
            applyChromeLanguage();
          }
          appendMessage(messages, result.message, false);
          renderPromotionCards(messages, result.promotion_cards, root.dataset.catalogUrl);
          renderActions(actions, result.suggested_actions);
          renderConfirmations(actions, result.confirmation_actions, root, messages);
          if (result.tour_id && window.TortillaAssistantTours) {
            window.TortillaAssistantTours.start(result.tour_id);
          }
        })
        .catch(function () {
          appendMessage(messages, uiCopy().connectionRetry, false);
        });
    });

    input.addEventListener("keydown", function (event) {
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        if (typeof form.requestSubmit === "function") form.requestSubmit();
        else form.dispatchEvent(new Event("submit", { cancelable: true }));
      }
    });

    if (deleteHistory) {
      deleteHistory.addEventListener("click", function () {
        jsonFetch(root.dataset.deleteHistoryUrl, { method: "POST", body: "{}" })
          .then(function () {
            conversationId = "";
            messages.replaceChildren();
            actions.replaceChildren();
            appendMessage(messages, uiCopy().historyCleared, false);
          })
          .catch(function (error) {
            appendMessage(messages, error.message || uiCopy().historyError, false);
          });
      });
    }

  });
}());
