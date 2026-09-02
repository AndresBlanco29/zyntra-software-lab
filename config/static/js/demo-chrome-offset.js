/**
 * Keep DEMO chrome offsets in sync (SOFTWARE LAB banner + aurora navbar).
 * Safe no-op when banner/navbar are missing.
 */
(function () {
  function syncDemoChromeOffsets() {
    var nav = document.querySelector('.navbar-custom');
    var banner = document.querySelector('.demo-environment-banner');
    var root = document.documentElement;
    var bannerHeight = banner ? Math.max(Math.round(banner.getBoundingClientRect().height), 0) : 0;

    if (bannerHeight) {
      root.style.setProperty('--demo-banner-height', bannerHeight + 'px');
    }

    var chromeBottom = nav
      ? Math.max(Math.ceil(nav.getBoundingClientRect().bottom), 56)
      : bannerHeight + 70;

    root.style.setProperty('--panel-navbar-offset', chromeBottom + 'px');
    root.style.setProperty('--app-navbar-height', chromeBottom + 'px');
  }

  function bind() {
    syncDemoChromeOffsets();
    window.addEventListener('resize', syncDemoChromeOffsets);
    window.addEventListener('load', syncDemoChromeOffsets);
    if (window.visualViewport) {
      window.visualViewport.addEventListener('resize', syncDemoChromeOffsets);
    }
    if (typeof ResizeObserver !== 'undefined') {
      var navEl = document.querySelector('.navbar-custom');
      var bannerEl = document.querySelector('.demo-environment-banner');
      var ro = new ResizeObserver(syncDemoChromeOffsets);
      if (navEl) ro.observe(navEl);
      if (bannerEl) ro.observe(bannerEl);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bind);
  } else {
    bind();
  }
})();
