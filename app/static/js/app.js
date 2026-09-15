/* Gardening Companion — progressive enhancement.
   Everything here is optional: each form works as a plain POST if JS is off.
   No framework, no build step. */
(function () {
  'use strict';

  /* ---------------------------------------------------------------- theme */
  var root = document.documentElement;

  function currentTheme() {
    var explicit = root.getAttribute('data-theme');
    if (explicit) return explicit;
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }

  document.querySelectorAll('[data-theme-toggle]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var next = currentTheme() === 'dark' ? 'light' : 'dark';
      root.setAttribute('data-theme', next);
      try { localStorage.setItem('gc-theme', next); } catch (e) { /* private mode */ }
    });
  });

  /* ------------------------------------------------------------------ nav */
  var navToggle = document.querySelector('[data-nav-toggle]');
  var nav = document.getElementById('primary-nav');
  if (navToggle && nav) {
    navToggle.addEventListener('click', function () {
      var open = nav.classList.toggle('is-open');
      navToggle.setAttribute('aria-expanded', String(open));
    });
  }

  /* --------------------------------------------------------------- flashes */
  document.querySelectorAll('[data-flash-close]').forEach(function (btn) {
    btn.addEventListener('click', function () { btn.closest('.flash').remove(); });
  });
  // Auto-dismiss only the non-critical ones; errors stay until read.
  document.querySelectorAll('.flash--success, .flash--info').forEach(function (el) {
    setTimeout(function () {
      el.style.transition = 'opacity 300ms';
      el.style.opacity = '0';
      setTimeout(function () { el.remove(); }, 300);
    }, 6000);
  });

  /* ---------------------------------------------------------------- modals */
  document.querySelectorAll('[data-modal-open]').forEach(function (trigger) {
    trigger.addEventListener('click', function () {
      var dialog = document.getElementById(trigger.getAttribute('data-modal-open'));
      if (!dialog) return;
      if (typeof dialog.showModal === 'function') dialog.showModal();
      else dialog.setAttribute('open', '');
      var first = dialog.querySelector('input:not([type=hidden]), select, textarea');
      if (first) setTimeout(function () { first.focus(); }, 40);
    });
  });
  document.querySelectorAll('[data-modal-close]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var dialog = btn.closest('dialog');
      if (!dialog) return;
      if (typeof dialog.close === 'function') dialog.close();
      else dialog.removeAttribute('open');
    });
  });

  /* ------------------------------------------------- confirm before submit */
  document.querySelectorAll('[data-confirm]').forEach(function (form) {
    form.addEventListener('submit', function (event) {
      if (!window.confirm(form.getAttribute('data-confirm'))) event.preventDefault();
    });
  });

  /* ------------------------------------------- double-submit / busy states */
  document.querySelectorAll('form').forEach(function (form) {
    form.addEventListener('submit', function () {
      var submit = form.querySelector('button[type=submit]');
      if (!submit || form.hasAttribute('data-no-busy')) return;
      // Defer so the button's own value still reaches the server.
      setTimeout(function () {
        submit.classList.add('is-loading');
        submit.disabled = true;
      }, 0);
    });
  });

  /* ------------------------------------------------- plant picker combobox */
  document.querySelectorAll('[data-combo]').forEach(function (combo) {
    var input = combo.querySelector('[data-combo-input]');
    var list = combo.querySelector('[data-combo-list]');
    var hidden = combo.querySelector('[data-combo-value]');
    if (!input || !list || !hidden) return;

    var options = Array.prototype.slice.call(list.querySelectorAll('[data-value]'));
    var activeIndex = -1;

    function visible() {
      return options.filter(function (o) { return !o.hidden; });
    }

    function open() { list.hidden = false; input.setAttribute('aria-expanded', 'true'); }
    function close() {
      list.hidden = true;
      input.setAttribute('aria-expanded', 'false');
      activeIndex = -1;
      options.forEach(function (o) { o.classList.remove('is-active'); });
    }

    function filter() {
      var q = input.value.trim().toLowerCase();
      var shown = 0;
      options.forEach(function (option) {
        var match = !q || option.textContent.toLowerCase().indexOf(q) !== -1;
        option.hidden = !match;
        if (match) shown++;
      });
      var empty = list.querySelector('[data-combo-empty]');
      if (empty) empty.hidden = shown > 0;
      open();
    }

    function choose(option) {
      hidden.value = option.getAttribute('data-value');
      input.value = option.textContent.trim();
      close();
      input.dispatchEvent(new Event('combo:selected', { bubbles: true }));
    }

    function highlight(delta) {
      var items = visible();
      if (!items.length) return;
      activeIndex = (activeIndex + delta + items.length) % items.length;
      items.forEach(function (o) { o.classList.remove('is-active'); });
      var active = items[activeIndex];
      active.classList.add('is-active');
      active.scrollIntoView({ block: 'nearest' });
    }

    input.addEventListener('input', function () {
      hidden.value = '';  // typing invalidates the previous pick
      filter();
    });
    input.addEventListener('focus', filter);

    input.addEventListener('keydown', function (event) {
      if (event.key === 'ArrowDown') { event.preventDefault(); open(); highlight(1); }
      else if (event.key === 'ArrowUp') { event.preventDefault(); highlight(-1); }
      else if (event.key === 'Enter') {
        var items = visible();
        if (!list.hidden && activeIndex >= 0 && items[activeIndex]) {
          event.preventDefault();
          choose(items[activeIndex]);
        }
      } else if (event.key === 'Escape') { close(); }
    });

    list.addEventListener('click', function (event) {
      var option = event.target.closest('[data-value]');
      if (option) choose(option);
    });

    document.addEventListener('click', function (event) {
      if (!combo.contains(event.target)) close();
    });
  });

  /* ------------------------------------------------- catalog search filter */
  var catalogSearch = document.querySelector('[data-filter-input]');
  if (catalogSearch) {
    var targets = document.querySelectorAll('[data-filter-item]');
    var countEl = document.querySelector('[data-filter-count]');
    catalogSearch.addEventListener('input', function () {
      var q = catalogSearch.value.trim().toLowerCase();
      var shown = 0;
      targets.forEach(function (item) {
        var hay = (item.getAttribute('data-filter-text') || item.textContent).toLowerCase();
        var match = !q || hay.indexOf(q) !== -1;
        item.hidden = !match;
        if (match) shown++;
      });
      if (countEl) countEl.textContent = shown;
      var empty = document.querySelector('[data-filter-empty]');
      if (empty) empty.hidden = shown > 0;
    });
  }
})();
