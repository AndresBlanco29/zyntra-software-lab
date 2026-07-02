(function () {
  'use strict';

  function restoreFormDraft(root, draftNodeId) {
    if (!root || !draftNodeId) {
      return;
    }
    const draftNode = document.getElementById(draftNodeId);
    if (!draftNode) {
      return;
    }

    let draft = {};
    try {
      draft = JSON.parse(draftNode.textContent || '{}');
    } catch (_error) {
      return;
    }

    Object.entries(draft).forEach(function ([name, value]) {
      if (value === null || value === undefined) {
        return;
      }
      root.querySelectorAll('[name="' + name.replace(/\\/g, '\\\\').replace(/"/g, '\\"') + '"]').forEach(function (field) {
        if (field.type === 'file') {
          return;
        }
        if (field.type === 'checkbox') {
          field.checked = ['on', 'true', '1'].includes(String(value).toLowerCase());
          return;
        }
        field.value = value;
      });
    });
  }

  function restoreSignatureFromHiddenInput(canvas, signatureInput) {
    if (!canvas || !signatureInput || !signatureInput.value) {
      return;
    }
    const context = canvas.getContext('2d');
    if (!context) {
      return;
    }
    const image = new Image();
    image.onload = function () {
      const ratio = window.devicePixelRatio || 1;
      const width = canvas.offsetWidth || canvas.parentElement.offsetWidth;
      const height = 220;
      canvas.width = width * ratio;
      canvas.height = height * ratio;
      context.setTransform(1, 0, 0, 1, 0, 0);
      context.scale(ratio, ratio);
      context.lineWidth = 2;
      context.lineCap = 'round';
      context.drawImage(image, 0, 0, width, height);
    };
    image.src = signatureInput.value;
  }

  window.LTGFormDraftRestore = {
    restoreFormDraft: restoreFormDraft,
    restoreSignatureFromHiddenInput: restoreSignatureFromHiddenInput,
  };
})();
