/* The tailoring window.

   Apply opens it instead of the posting: it asks whether to tailor the CV first, walks
   through the review, and in every case ends by opening the employer's posting and
   recording the application, exactly as a plain Apply would. Closing it at any point
   discards an unsaved draft. The steps themselves are server-rendered and swapped in by
   htmx; this file only opens, closes and wires them. */
(function () {
  'use strict';

  var modal = document.getElementById('tailor');
  if (!modal) return;
  var body = document.getElementById('tailor-body');
  var reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
  var state = null;       // {job, mark, opener}
  var lastFocus = null;

  function open(job, mark) {
    state = { job: job, mark: mark };
    lastFocus = document.activeElement;
    body.innerHTML = '';
    modal.hidden = false;
    document.documentElement.classList.add('tailor-open');
    requestAnimationFrame(function () { modal.classList.add('is-open'); });
    var query = new URLSearchParams({
      title: job.title || '', company: job.company || '', url: job.url || '',
      advert_key: job.advert_key || '', job_id: job.job_id || ''
    });
    htmx.ajax('GET', '/tailor/offer?' + query.toString(), { target: body, swap: 'innerHTML' });
  }

  function current() {
    var step = body.querySelector('[data-tailor-step]');
    return step ? { id: step.getAttribute('data-tailor-id'),
                    status: step.getAttribute('data-tailor-status') } : {};
  }

  function close(discard) {
    var cur = current();
    // An unsaved draft is thrown away; a saved one, or one being viewed, is not.
    if (discard !== false && cur.id && cur.status === 'draft') {
      htmx.ajax('POST', '/tailor/' + cur.id + '/cancel', { swap: 'none' });
    }
    modal.classList.remove('is-open', 'htmx-request');
    document.documentElement.classList.remove('tailor-open');
    setTimeout(function () { modal.hidden = true; body.innerHTML = ''; }, reduced.matches ? 0 : 220);
    if (lastFocus && lastFocus.focus) lastFocus.focus({ preventScroll: true });
  }

  // Straight to the posting, recording the application as a plain Apply does.
  function go() {
    if (!state) return;
    var job = state.job;
    window.open(job.url, '_blank', 'noopener');
    if (state.mark && document.body.contains(state.mark)) {
      htmx.ajax('POST', '/applications', {
        target: state.mark, swap: 'outerHTML',
        values: { advert_key: job.advert_key, title: job.title, company: job.company, url: job.url }
      });
    }
    close(false);
  }

  // Apply, intercepted before htmx or the external-link helper see it. A modified
  // click (new tab, new window) is left alone: that person has decided already.
  document.addEventListener('click', function (e) {
    var link = e.target.closest && e.target.closest('a[data-tailor]');
    if (!link || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    e.stopPropagation();
    open(JSON.parse(link.getAttribute('data-tailor')), link.closest('.applied-mark'));
  }, true);

  modal.addEventListener('click', function (e) {
    if (e.target.closest('[data-tailor-close]')) { close(); return; }
    if (e.target.closest('[data-tailor-go]')) { go(); return; }
    var cancel = e.target.closest('[data-tailor-cancel]');
    if (cancel) { close(); return; }
    // A missing keyword or a gap: start a sentence about it in the suggestion box.
    var fact = e.target.closest('[data-add-fact]');
    if (fact) {
      var box = body.querySelector('textarea[name=suggestion]');
      if (!box) return;
      var line = 'I have ' + fact.getAttribute('data-add-fact') + ' experience: ';
      box.value = (box.value.trim() ? box.value.trim() + '\n' : '') + line;
      box.focus();
      box.setSelectionRange(box.value.length, box.value.length);
      fact.classList.add('is-picked');
      return;
    }
    var tab = e.target.closest('[data-tws-tab]');
    if (tab) {
      var name = tab.getAttribute('data-tws-tab');
      body.querySelectorAll('[data-tws-tab]').forEach(function (t) {
        t.setAttribute('aria-selected', t === tab ? 'true' : 'false');
      });
      body.querySelectorAll('[data-tws-pane]').forEach(function (p) {
        p.hidden = p.getAttribute('data-tws-pane') !== name;
      });
    }
  });

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && !modal.hidden && !modal.classList.contains('htmx-request')) close();
  });

  // While a step is working: what it is doing, and for how long.
  var clock = null;
  document.body.addEventListener('htmx:beforeRequest', function (e) {
    if (!modal.contains(e.detail.elt)) return;
    var trigger = e.detail.elt.closest('[data-working]');
    var title = modal.querySelector('[data-working-title]');
    if (title) title.textContent = trigger ? trigger.getAttribute('data-working') : 'Working';
    var started = Date.now(), el = modal.querySelector('[data-working-clock]');
    clearInterval(clock);
    if (el) {
      el.textContent = '0s';
      clock = setInterval(function () { el.textContent = Math.floor((Date.now() - started) / 1000) + 's'; }, 1000);
    }
  });
  document.body.addEventListener('htmx:afterRequest', function (e) {
    if (modal.contains(e.detail.elt)) clearInterval(clock);
  });

  // Each step arrives at the top of the window, and the score counts up to itself.
  document.body.addEventListener('htmx:afterSwap', function (e) {
    if (e.detail.target !== body) return;
    body.scrollTop = 0;
    var bar = document.getElementById('tailor-bar');
    var title = body.querySelector('#tailor-title');
    if (bar && title) bar.textContent = 'tailor: ' + title.textContent.trim().toLowerCase();
    var num = body.querySelector('.is-fresh [data-count-to]');
    if (num && !reduced.matches) {
      var to = parseInt(num.getAttribute('data-count-to'), 10), t0 = null;
      var from = parseInt(getComputedStyle(body.querySelector('.score')).getPropertyValue('--b'), 10) || 0;
      (function step(ts) {
        if (!t0) t0 = ts;
        var p = Math.min(1, (ts - t0) / 1100), eased = 1 - Math.pow(1 - p, 4);
        num.textContent = Math.round(from + (to - from) * eased);
        if (p < 1) requestAnimationFrame(step);
      })(performance.now());
    }
    var first = body.querySelector('button:not([disabled]), textarea, a[href]');
    if (first && !body.querySelector('textarea:focus')) first.focus({ preventScroll: true });
  });

  // On its own page, the review's tabs and keyword chips work the same way.
  document.addEventListener('click', function (e) {
    if (modal.contains(e.target)) return;
    var tab = e.target.closest && e.target.closest('[data-tws-tab]');
    if (!tab) return;
    var root = tab.closest('.tws');
    var name = tab.getAttribute('data-tws-tab');
    root.querySelectorAll('[data-tws-tab]').forEach(function (t) {
      t.setAttribute('aria-selected', t === tab ? 'true' : 'false');
    });
    root.querySelectorAll('[data-tws-pane]').forEach(function (p) {
      p.hidden = p.getAttribute('data-tws-pane') !== name;
    });
  });
})();
