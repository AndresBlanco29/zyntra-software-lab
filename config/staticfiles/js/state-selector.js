;(function(){
  function initStateSelector(selector){
    if(!selector || selector.dataset.stateSelectorReady === 'true') return;

    var input = selector.querySelector('[data-state-input]');
    var toggle = selector.querySelector('[data-state-toggle]');
    var menu = selector.querySelector('[data-state-menu]');
    var empty = selector.querySelector('[data-state-empty]');
    var options = Array.prototype.slice.call(selector.querySelectorAll('[data-state-option]'));

    if(!input || !toggle || !menu || !options.length) return;

    selector.dataset.stateSelectorReady = 'true';

    function visibleOptions(){
      return options.filter(function(option){
        return !option.hidden;
      });
    }

    function openMenu(){
      filterOptions();
      selector.classList.add('is-open');
      menu.hidden = false;
    }

    function closeMenu(){
      selector.classList.remove('is-open');
      menu.hidden = true;
    }

    function filterOptions(){
      var query = input.value.trim().toLowerCase();
      var hasResults = false;

      options.forEach(function(option){
        var match = !query || option.dataset.value.toLowerCase().indexOf(query) !== -1;
        option.hidden = !match;
        if(match){
          hasResults = true;
        }
      });

      if(empty){
        empty.hidden = hasResults;
      }
    }

    input.addEventListener('focus', openMenu);
    input.addEventListener('click', openMenu);
    input.addEventListener('input', function(){
      input.classList.remove('is-invalid');
      openMenu();
    });

    input.addEventListener('keydown', function(event){
      if(event.key === 'Escape'){
        closeMenu();
      }

      if(event.key === 'ArrowDown'){
        event.preventDefault();
        openMenu();
        var first = visibleOptions()[0];
        if(first){
          first.focus();
        }
      }
    });

    toggle.addEventListener('click', function(){
      if(menu.hidden){
        openMenu();
        input.focus();
      } else {
        closeMenu();
      }
    });

    options.forEach(function(option){
      option.addEventListener('click', function(){
        input.value = option.dataset.value;
        input.dispatchEvent(new Event('change', { bubbles: true }));
        closeMenu();
        input.focus();
      });

      option.addEventListener('keydown', function(event){
        if(event.key === 'Enter' || event.key === ' '){
          event.preventDefault();
          option.click();
        }
      });
    });

    document.addEventListener('click', function(event){
      if(!selector.contains(event.target)){
        closeMenu();
      }
    });
  }

  function initAllStateSelectors(root){
    (root || document).querySelectorAll('[data-state-selector]').forEach(initStateSelector);
  }

  if(document.readyState === 'loading'){
    document.addEventListener('DOMContentLoaded', function(){
      initAllStateSelectors(document);
    });
  } else {
    initAllStateSelectors(document);
  }

  var observer = new MutationObserver(function(mutations){
    mutations.forEach(function(mutation){
      mutation.addedNodes.forEach(function(node){
        if(node.nodeType !== 1) return;
        if(node.matches && node.matches('[data-state-selector]')){
          initStateSelector(node);
          return;
        }
        if(node.querySelectorAll){
          initAllStateSelectors(node);
        }
      });
    });
  });

  observer.observe(document.documentElement, { childList: true, subtree: true });
})();