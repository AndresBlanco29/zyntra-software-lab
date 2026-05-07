;(function(){
  var highlightRects = {
    salesTax: { x: 50.2, y: 42.6, w: 18.9, h: 9.5, labelPosition: 'right' },
    georgia: { x: 33.8, y: 19.8, w: 27.8, h: 14.3, labelPosition: 'right' }
  };
  var redactionRects = {
    address: { x: 8.5, y: 73.0, w: 22.5, h: 11.2 }
  };
  var backdropEl = null;
  var currentExampleMode = 'number';

  function getModalEl(){
    var modals = document.querySelectorAll('#taxExampleModal');
    if(!modals.length) return null;
    return modals[modals.length - 1];
  }

  function removeDuplicateModals(activeModalEl){
    document.querySelectorAll('#taxExampleModal').forEach(function(modalEl){
      if(modalEl !== activeModalEl){
        modalEl.remove();
      }
    });
  }

  function resetTaxExampleState(){
    document.body.classList.remove('tax-example-open');

    if(backdropEl && document.body.contains(backdropEl)){
      backdropEl.classList.remove('show');
      backdropEl.remove();
    }

    backdropEl = null;
    window._taxExampleParentModalId = null;
    window._taxExampleLastTrigger = null;

    document.querySelectorAll('#taxExampleModal').forEach(function(modalEl){
      modalEl.classList.remove('show');
      modalEl.setAttribute('aria-hidden', 'true');
      modalEl.style.display = 'none';
    });
  }

  function moveModalToBody(modalEl){
    if(!modalEl || modalEl.parentElement === document.body) return modalEl;
    document.body.appendChild(modalEl);
    return modalEl;
  }

  function ensureBackdrop(){
    if(backdropEl && document.body.contains(backdropEl)) return backdropEl;

    backdropEl = document.createElement('div');
    backdropEl.className = 'tax-example-backdrop';
    backdropEl.addEventListener('click', function(){
      window.closeTaxExample();
    });
    document.body.appendChild(backdropEl);
    return backdropEl;
  }

  function showOverlay(modalEl){
    ensureBackdrop();
    document.body.classList.add('tax-example-open');
    backdropEl.classList.add('show');
    modalEl.style.display = 'block';
    modalEl.removeAttribute('aria-hidden');
    modalEl.classList.add('show');
    modalEl.setAttribute('aria-modal', 'true');
    modalEl.setAttribute('role', 'dialog');
  }

  function hideOverlay(modalEl){
    if(backdropEl){
      backdropEl.classList.remove('show');
    }
    document.body.classList.remove('tax-example-open');
    modalEl.classList.remove('show');
    modalEl.setAttribute('aria-hidden', 'true');
    modalEl.style.display = 'none';
  }

  function applyExampleContent(opener){
    var modalEl = getModalEl();
    if(!modalEl) return;

    var titleEl = document.getElementById('taxExampleLabel');
    var noteEl = document.getElementById('taxExampleNote');
    var labelEl = document.getElementById('taxLabel');

    currentExampleMode = opener && opener.dataset.taxExampleMode ? opener.dataset.taxExampleMode : 'number';

    if(titleEl && opener && opener.dataset.taxExampleTitle){
      titleEl.textContent = opener.dataset.taxExampleTitle;
    }

    if(noteEl && opener && opener.dataset.taxExampleNote){
      noteEl.textContent = opener.dataset.taxExampleNote;
    }

    if(labelEl && opener && opener.dataset.taxExampleLabel){
      labelEl.textContent = opener.dataset.taxExampleLabel;
    }

    document.querySelectorAll('[data-tax-highlight]').forEach(function(highlightEl){
      highlightEl.style.display = currentExampleMode === 'certificate' ? 'none' : 'block';
    });
  }

  function focusSalesTaxInput(){
    window.setTimeout(function(){
      var targetInput = document.getElementById('id_sales_tax_modal') || document.getElementById('id_sales_tax');
      if(targetInput){
        targetInput.focus();
      }
    }, 150);
  }

  function positionHighlight(){
    document.querySelectorAll('[data-tax-mask]').forEach(function(mask){
      var key = mask.getAttribute('data-tax-mask');
      var rect = redactionRects[key];
      if(!rect) return;

      mask.style.left = rect.x + '%';
      mask.style.top = rect.y + '%';
      mask.style.width = rect.w + '%';
      mask.style.height = rect.h + '%';
    });

    document.querySelectorAll('[data-tax-highlight]').forEach(function(highlight){
      var key = highlight.getAttribute('data-tax-highlight');
      var rect = highlightRects[key];
      if(!rect) return;

      highlight.style.left = rect.x + '%';
      highlight.style.top = rect.y + '%';
      highlight.style.width = rect.w + '%';
      highlight.style.height = rect.h + '%';

      var label = highlight.querySelector('.tax-label');
      if(!label) return;

      if(window.innerWidth <= 576){
        if(rect.labelPosition === 'inside'){
          label.style.left = '50%';
          label.style.top = '50%';
          label.style.transform = 'translate(-50%, -50%)';
          return;
        }

        label.style.left = '50%';
        label.style.top = '-30px';
        label.style.transform = 'translateX(-50%)';
        return;
      }

      if(rect.labelPosition === 'inside'){
        label.style.left = '50%';
        label.style.top = '50%';
        label.style.transform = 'translate(-50%, -50%)';
        return;
      }

      if(rect.labelPosition === 'top'){
        label.style.left = '50%';
        label.style.top = '-42px';
        label.style.transform = 'translateX(-50%)';
        return;
      }

      label.style.left = key === 'georgia' ? 'calc(100% + 4px)' : 'calc(100% + 14px)';
      label.style.top = '50%';
      label.style.transform = 'translateY(-50%)';
    });
  }

  function initTaxExample(){
    var modalEl = getModalEl();
    if(!modalEl || modalEl.dataset.taxExampleReady === 'true') return;

    modalEl = moveModalToBody(modalEl);
    removeDuplicateModals(modalEl);

    modalEl.dataset.taxExampleReady = 'true';

    hideOverlay(modalEl);
  }

  window.openTaxExample = function(event){
    if(event && typeof event.preventDefault === 'function'){
      event.preventDefault();
    }

    var modalEl = getModalEl();
    if(!modalEl){
      return;
    }

    modalEl = moveModalToBody(modalEl);
    removeDuplicateModals(modalEl);
    initTaxExample();

    var opener = event && event.currentTarget ? event.currentTarget : document.activeElement;
    window._taxExampleLastTrigger = opener || null;
    applyExampleContent(opener);

    var parent = opener && opener.closest ? opener.closest('.modal.show') : document.querySelector('.modal.show');
    window._taxExampleParentModalId = parent && parent.id !== 'taxExampleModal' ? parent.id : null;

    showOverlay(modalEl);
    positionHighlight();
  };

  window.closeTaxExample = function(){
    var modalEl = getModalEl();
    if(!modalEl){
      resetTaxExampleState();
      return;
    }

    modalEl = moveModalToBody(modalEl);

    hideOverlay(modalEl);

    if(window._taxExampleParentModalId){
      var parentEl = document.getElementById(window._taxExampleParentModalId);
      if(parentEl){
        parentEl.dataset.preserveContentOnShow = 'true';
      }
      window._taxExampleParentModalId = null;
    }

    if(window._taxExampleLastTrigger && typeof window._taxExampleLastTrigger.focus === 'function'){
      window._taxExampleLastTrigger.focus();
    }

    if(currentExampleMode === 'number'){
      focusSalesTaxInput();
    }
  };

  window.resetTaxExampleModal = function(){
    resetTaxExampleState();
    removeDuplicateModals(getModalEl());
  };

  window.addEventListener('resize', function(){
    var modalEl = getModalEl();
    if(modalEl && modalEl.classList.contains('show')){
      positionHighlight();
    }
  });

  document.addEventListener('keydown', function(event){
    if(event.key === 'Escape'){
      var modalEl = getModalEl();
      if(modalEl && modalEl.classList.contains('show')){
        event.preventDefault();
        window.closeTaxExample();
      }
    }
  });

  if(document.readyState === 'loading'){
    document.addEventListener('DOMContentLoaded', initTaxExample);
  } else {
    initTaxExample();
  }

})();
