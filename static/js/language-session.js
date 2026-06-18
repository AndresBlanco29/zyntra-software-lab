(function () {
  const STORAGE_KEY = "siteLanguage";
  const DEFAULT_LANGUAGE = "en";

  function getCurrentLanguage() {
    const htmlLang = document.documentElement.getAttribute("lang") || DEFAULT_LANGUAGE;
    return htmlLang.slice(0, 2).toLowerCase();
  }

  function getLanguageForm() {
    return document.querySelector("form[data-language-form]");
  }

  function buildHiddenInput(name, value) {
    const input = document.createElement("input");
    input.type = "hidden";
    input.name = name;
    input.value = value;
    return input;
  }

  function submitLanguage(form, language) {
    const csrfToken = form.querySelector('input[name="csrfmiddlewaretoken"]');
    const nextInput = form.querySelector('input[name="next"]');
    if (!csrfToken) {
      return;
    }

    const tempForm = document.createElement("form");
    tempForm.method = "post";
    tempForm.action = form.action;
    tempForm.style.display = "none";

    tempForm.appendChild(buildHiddenInput("csrfmiddlewaretoken", csrfToken.value));
    tempForm.appendChild(buildHiddenInput("language", language));
    tempForm.appendChild(
      buildHiddenInput(
        "next",
        nextInput ? nextInput.value : window.location.pathname + window.location.search + window.location.hash
      )
    );

    document.body.appendChild(tempForm);
    tempForm.submit();
  }

  document.addEventListener("DOMContentLoaded", function () {
    const form = getLanguageForm();
    if (!form) {
      return;
    }

    form.querySelectorAll('button[name="language"]').forEach(function (button) {
      button.addEventListener("click", function (event) {
        event.preventDefault();
        const selectedLanguage = button.value || DEFAULT_LANGUAGE;
        window.sessionStorage.setItem(STORAGE_KEY, selectedLanguage);
        submitLanguage(form, selectedLanguage);
      });
    });

    const storedLanguage = window.sessionStorage.getItem(STORAGE_KEY);
    const currentLanguage = getCurrentLanguage();
    const targetLanguage = storedLanguage || currentLanguage || DEFAULT_LANGUAGE;

    if (!storedLanguage) {
      window.sessionStorage.setItem(STORAGE_KEY, targetLanguage);
    }

    if (currentLanguage !== targetLanguage) {
      submitLanguage(form, targetLanguage);
    }
  });
})();