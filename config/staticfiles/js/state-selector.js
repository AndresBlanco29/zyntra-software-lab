;(function(){
  var fallbackCitiesByState = {
    Alabama: ['Birmingham', 'Montgomery', 'Mobile', 'Huntsville', 'Tuscaloosa'],
    Alaska: ['Anchorage', 'Fairbanks', 'Juneau', 'Wasilla', 'Sitka'],
    Arizona: ['Phoenix', 'Tucson', 'Mesa', 'Scottsdale', 'Glendale'],
    Arkansas: ['Little Rock', 'Fort Smith', 'Fayetteville', 'Springdale', 'Jonesboro'],
    California: ['Los Angeles', 'San Diego', 'San Jose', 'San Francisco', 'Sacramento', 'Fresno'],
    Colorado: ['Denver', 'Colorado Springs', 'Aurora', 'Fort Collins', 'Boulder'],
    Connecticut: ['Bridgeport', 'New Haven', 'Stamford', 'Hartford', 'Waterbury'],
    Delaware: ['Wilmington', 'Dover', 'Newark', 'Middletown', 'Smyrna'],
    Florida: ['Miami', 'Orlando', 'Tampa', 'Jacksonville', 'Tallahassee', 'Fort Lauderdale'],
    Georgia: ['Atlanta', 'Augusta', 'Savannah', 'Columbus', 'Macon', 'Athens'],
    Hawaii: ['Honolulu', 'Hilo', 'Kailua', 'Pearl City', 'Kahului'],
    Idaho: ['Boise', 'Meridian', 'Nampa', 'Idaho Falls', 'Pocatello'],
    Illinois: ['Chicago', 'Aurora', 'Naperville', 'Springfield', 'Peoria'],
    Indiana: ['Indianapolis', 'Fort Wayne', 'Evansville', 'South Bend', 'Carmel'],
    Iowa: ['Des Moines', 'Cedar Rapids', 'Davenport', 'Sioux City', 'Iowa City'],
    Kansas: ['Wichita', 'Overland Park', 'Kansas City', 'Topeka', 'Lawrence'],
    Kentucky: ['Louisville', 'Lexington', 'Bowling Green', 'Owensboro', 'Covington'],
    Louisiana: ['New Orleans', 'Baton Rouge', 'Shreveport', 'Lafayette', 'Lake Charles'],
    Maine: ['Portland', 'Lewiston', 'Bangor', 'South Portland', 'Auburn'],
    Maryland: ['Baltimore', 'Annapolis', 'Frederick', 'Rockville', 'Gaithersburg'],
    Massachusetts: ['Boston', 'Worcester', 'Springfield', 'Cambridge', 'Lowell'],
    Michigan: ['Detroit', 'Grand Rapids', 'Warren', 'Lansing', 'Ann Arbor'],
    Minnesota: ['Minneapolis', 'Saint Paul', 'Rochester', 'Duluth', 'Bloomington'],
    Mississippi: ['Jackson', 'Gulfport', 'Southaven', 'Hattiesburg', 'Biloxi'],
    Missouri: ['Kansas City', 'Saint Louis', 'Springfield', 'Columbia', 'Independence'],
    Montana: ['Billings', 'Missoula', 'Great Falls', 'Bozeman', 'Helena'],
    Nebraska: ['Omaha', 'Lincoln', 'Bellevue', 'Grand Island', 'Kearney'],
    Nevada: ['Las Vegas', 'Henderson', 'Reno', 'North Las Vegas', 'Carson City'],
    'New Hampshire': ['Manchester', 'Nashua', 'Concord', 'Derry', 'Dover'],
    'New Jersey': ['Newark', 'Jersey City', 'Paterson', 'Elizabeth', 'Trenton'],
    'New Mexico': ['Albuquerque', 'Santa Fe', 'Las Cruces', 'Rio Rancho', 'Roswell'],
    'New York': ['New York City', 'Buffalo', 'Rochester', 'Albany', 'Syracuse', 'Yonkers'],
    'North Carolina': ['Charlotte', 'Raleigh', 'Greensboro', 'Durham', 'Asheville'],
    'North Dakota': ['Fargo', 'Bismarck', 'Grand Forks', 'Minot', 'West Fargo'],
    Ohio: ['Columbus', 'Cleveland', 'Cincinnati', 'Toledo', 'Akron'],
    Oklahoma: ['Oklahoma City', 'Tulsa', 'Norman', 'Broken Arrow', 'Edmond'],
    Oregon: ['Portland', 'Salem', 'Eugene', 'Gresham', 'Bend'],
    Pennsylvania: ['Philadelphia', 'Pittsburgh', 'Allentown', 'Harrisburg', 'Erie'],
    'Rhode Island': ['Providence', 'Warwick', 'Cranston', 'Pawtucket', 'Newport'],
    'South Carolina': ['Columbia', 'Charleston', 'North Charleston', 'Greenville', 'Myrtle Beach'],
    'South Dakota': ['Sioux Falls', 'Rapid City', 'Aberdeen', 'Brookings', 'Pierre'],
    Tennessee: ['Nashville', 'Memphis', 'Knoxville', 'Chattanooga', 'Clarksville'],
    Texas: ['Houston', 'Dallas', 'Austin', 'San Antonio', 'Fort Worth', 'El Paso'],
    Utah: ['Salt Lake City', 'West Valley City', 'Provo', 'West Jordan', 'Ogden'],
    Vermont: ['Burlington', 'South Burlington', 'Rutland', 'Montpelier', 'Brattleboro'],
    Virginia: ['Virginia Beach', 'Richmond', 'Norfolk', 'Arlington', 'Alexandria'],
    Washington: ['Seattle', 'Spokane', 'Tacoma', 'Vancouver', 'Olympia'],
    'West Virginia': ['Charleston', 'Huntington', 'Morgantown', 'Parkersburg', 'Wheeling'],
    Wisconsin: ['Milwaukee', 'Madison', 'Green Bay', 'Kenosha', 'Racine'],
    Wyoming: ['Cheyenne', 'Casper', 'Laramie', 'Gillette', 'Jackson']
  };

  function getCitiesByState(){
    return window.__LTG_US_LOCATIONS || fallbackCitiesByState;
  }

  function initSearchSelector(selector, config){
    if(!selector) return null;
    if(selector._searchSelectorApi){
      return selector._searchSelectorApi;
    }
    if(selector.dataset.searchSelectorReady === 'true') return selector._searchSelectorApi || null;

    var input = selector.querySelector(config.inputSelector);
    var toggle = selector.querySelector(config.toggleSelector);
    var menu = selector.querySelector(config.menuSelector);
    var empty = selector.querySelector(config.emptySelector);

    if(!input || !toggle || !menu) return null;

    selector.dataset.searchSelectorReady = 'true';

    function getOptions(){
      return Array.prototype.slice.call(menu.querySelectorAll('.state-selector-option'));
    }

    function visibleOptions(){
      return getOptions().filter(function(option){
        return !option.hidden;
      });
    }

    function closeMenu(){
      selector.classList.remove('is-open');
      menu.hidden = true;
    }

    function filterOptions(){
      var query = input.value.trim().toLowerCase();
      var options = getOptions();
      var hasResults = false;

      options.forEach(function(option){
        var value = (option.dataset.value || '').toLowerCase();
        var match = !query || value.indexOf(query) !== -1;
        option.hidden = !match;
        if(match){
          hasResults = true;
        }
      });

      if(empty){
        empty.hidden = hasResults;
      }
    }

    function openMenu(){
      if(input.disabled){
        return;
      }

      filterOptions();
      selector.classList.add('is-open');
      menu.hidden = false;
    }

    function setOptions(values){
      getOptions().forEach(function(option){
        option.remove();
      });

      values.forEach(function(value){
        var option = document.createElement('button');
        option.type = 'button';
        option.className = 'state-selector-option';
        option.dataset.value = value;
        option.textContent = value;
        menu.insertBefore(option, empty || null);
      });

      filterOptions();
    }

    function setDisabled(disabled, placeholder){
      input.disabled = disabled;
      toggle.disabled = disabled;
      if(typeof placeholder === 'string'){
        input.placeholder = placeholder;
      }
      if(disabled){
        closeMenu();
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
      if(toggle.disabled){
        return;
      }

      if(menu.hidden){
        openMenu();
        input.focus();
      } else {
        closeMenu();
      }
    });

    menu.addEventListener('click', function(event){
      var option = event.target.closest('.state-selector-option');
      if(!option){
        return;
      }

      input.value = option.dataset.value;
      input.dispatchEvent(new Event('change', { bubbles: true }));
      selector.dispatchEvent(new CustomEvent('ltg:selector-selected', {
        bubbles: true,
        detail: { value: option.dataset.value }
      }));
      closeMenu();
      input.focus();
    });

    menu.addEventListener('keydown', function(event){
      var option = event.target.closest('.state-selector-option');
      if(!option){
        return;
      }

      if(event.key === 'Enter' || event.key === ' '){
        event.preventDefault();
        option.click();
      }
    });

    document.addEventListener('click', function(event){
      if(!selector.contains(event.target)){
        closeMenu();
      }
    });

    selector._searchSelectorApi = {
      input: input,
      setOptions: setOptions,
      setDisabled: setDisabled,
      closeMenu: closeMenu
    };

    return selector._searchSelectorApi;
  }

  function getMatchingState(value){
    var normalized = (value || '').trim().toLowerCase();
    return Object.keys(getCitiesByState()).find(function(state){
      return state.toLowerCase() === normalized;
    }) || null;
  }

  function initLocationGroup(group){
    if(!group || group.dataset.locationGroupReady === 'true') return;

    var stateSelectEl = group.querySelector('[data-state-select]');
    var citySelectEl = group.querySelector('[data-city-select]');
    if(!stateSelectEl || !citySelectEl) return;

    group.dataset.locationGroupReady = 'true';

    if(stateSelectEl.options.length <= 1){
      Object.keys(getCitiesByState()).forEach(function(stateName){
        var option = document.createElement('option');
        option.value = stateName;
        option.textContent = stateName;
        stateSelectEl.appendChild(option);
      });
    }

    var placeholderDisabled = citySelectEl.options[0] ? citySelectEl.options[0].textContent : 'Select a state first';
    var placeholderEnabled = citySelectEl.getAttribute('data-placeholder-enabled') || 'Select a city';

    function resetCitySelect(text){
      citySelectEl.innerHTML = '';
      var option = document.createElement('option');
      option.value = '';
      option.textContent = text;
      citySelectEl.appendChild(option);
    }

    function updateCitySelector(){
      var matchedState = getMatchingState(stateSelectEl.value);
      if(!matchedState){
        resetCitySelect(placeholderDisabled);
        citySelectEl.disabled = true;
        return;
      }

      var currentCity = citySelectEl.value;
      resetCitySelect(placeholderEnabled);
      (getCitiesByState()[matchedState] || []).forEach(function(city){
        var option = document.createElement('option');
        option.value = city;
        option.textContent = city;
        citySelectEl.appendChild(option);
      });
      citySelectEl.disabled = false;

      if(currentCity && (getCitiesByState()[matchedState] || []).some(function(city){ return city === currentCity; })){
        citySelectEl.value = currentCity;
      }
    }

    stateSelectEl.addEventListener('change', updateCitySelector);
    stateSelectEl.addEventListener('input', updateCitySelector);
    stateSelectEl.addEventListener('blur', updateCitySelector);
    stateSelectEl.addEventListener('click', function(){
      window.setTimeout(updateCitySelector, 0);
    });

    group._ltgSyncLocation = updateCitySelector;

    updateCitySelector();
    window.setTimeout(updateCitySelector, 0);
    window.setTimeout(updateCitySelector, 300);
  }

  function initAll(root){
    (root || document).querySelectorAll('[data-state-selector]').forEach(function(selector){
      initSearchSelector(selector, {
        inputSelector: '[data-state-input]',
        toggleSelector: '[data-state-toggle]',
        menuSelector: '[data-state-menu]',
        emptySelector: '[data-state-empty]'
      });
    });

    (root || document).querySelectorAll('[data-location-group]').forEach(initLocationGroup);
  }

  if(document.readyState === 'loading'){
    document.addEventListener('DOMContentLoaded', function(){
      initAll(document);
    });
  } else {
    initAll(document);
  }

  var observer = new MutationObserver(function(mutations){
    mutations.forEach(function(mutation){
      mutation.addedNodes.forEach(function(node){
        if(node.nodeType !== 1) return;
        if(node.matches && (node.matches('[data-location-group]') || node.matches('[data-state-selector]'))){
          initAll(node.parentNode || node);
          return;
        }
        if(node.querySelectorAll){
          initAll(node);
        }
      });
    });
  });

  observer.observe(document.documentElement, { childList: true, subtree: true });
})();