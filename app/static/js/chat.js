/* Sprout chat: async send, streaming-free but with a typing indicator.
   Used by both the floating widget and the full-page view. */
(function () {
  'use strict';

  var form = document.querySelector('[data-chat-form]');
  var log = document.getElementById('chat-log');
  if (!form || !log) return;

  var input = form.querySelector('input[name=message]');
  var sendUrl = form.getAttribute('data-send-url');
  var clearUrl = form.getAttribute('data-clear-url');
  var csrf = form.getAttribute('data-csrf');
  var emptyState = log.querySelector('[data-chat-empty]');
  var busy = false;

  /* Escape first, then re-introduce only the small subset of Markdown the
     model is asked to produce. Never assign unescaped model output to
     innerHTML — a reply could otherwise inject script into the page. */
  function escapeHtml(text) {
    var div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }

  function renderMarkdown(text) {
    var safe = escapeHtml(text);
    safe = safe.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    safe = safe.replace(/(^|[\s(])\*([^*\n]+)\*/g, '$1<em>$2</em>');
    safe = safe.replace(/`([^`]+)`/g, '<code>$1</code>');

    var lines = safe.split('\n');
    var html = '';
    var inList = false;
    lines.forEach(function (line) {
      var bullet = line.match(/^\s*[-*]\s+(.*)$/);
      if (bullet) {
        if (!inList) { html += '<ul>'; inList = true; }
        html += '<li>' + bullet[1] + '</li>';
      } else {
        if (inList) { html += '</ul>'; inList = false; }
        if (line.trim()) html += '<p>' + line + '</p>';
      }
    });
    if (inList) html += '</ul>';
    return html || '<p>' + safe + '</p>';
  }

  function scrollDown() { log.scrollTop = log.scrollHeight; }

  function addMessage(role, text, opts) {
    if (emptyState) emptyState.hidden = true;
    var wrap = document.createElement('div');
    wrap.className = 'msg msg--' + (role === 'user' ? 'user' : 'bot');

    if (role !== 'user') {
      var who = document.createElement('div');
      who.className = 'msg__who';
      who.textContent = 'Sprout';
      wrap.appendChild(who);
    }

    var bubble = document.createElement('div');
    bubble.className = 'msg__bubble';
    if (opts && opts.typing) {
      bubble.innerHTML = '<span class="typing"><span></span><span></span><span></span></span>';
      wrap.setAttribute('data-typing', '');
    } else if (role === 'user') {
      bubble.textContent = text;
    } else {
      bubble.innerHTML = renderMarkdown(text);
    }

    wrap.appendChild(bubble);
    log.appendChild(wrap);
    scrollDown();
    return wrap;
  }

  form.addEventListener('submit', function (event) {
    event.preventDefault();
    if (busy) return;

    var message = (input.value || '').trim();
    if (!message) return;

    busy = true;
    input.value = '';
    input.disabled = true;
    addMessage('user', message);
    var placeholder = addMessage('bot', '', { typing: true });

    fetch(sendUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
      body: JSON.stringify({ message: message }),
      credentials: 'same-origin'
    })
      .then(function (response) {
        return response.json().then(function (data) {
          return { ok: response.ok, data: data };
        });
      })
      .then(function (result) {
        placeholder.remove();
        if (result.ok) {
          addMessage('bot', result.data.reply);
        } else {
          addMessage('bot', result.data.error || 'Something went wrong. Please try again.');
        }
      })
      .catch(function () {
        placeholder.remove();
        addMessage('bot', "I couldn't reach the server. Check your connection and try again.");
      })
      .finally(function () {
        busy = false;
        input.disabled = false;
        input.focus();
      });
  });

  document.querySelectorAll('[data-chat-suggestion]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      input.value = btn.textContent.trim();
      form.dispatchEvent(new Event('submit', { cancelable: true }));
    });
  });

  var clearBtn = document.querySelector('[data-chat-clear]');
  if (clearBtn && clearUrl) {
    clearBtn.addEventListener('click', function () {
      if (!window.confirm('Clear this conversation?')) return;
      fetch(clearUrl, {
        method: 'POST',
        headers: { 'X-CSRFToken': csrf },
        credentials: 'same-origin'
      }).then(function () {
        log.querySelectorAll('.msg').forEach(function (m) { m.remove(); });
        if (emptyState) emptyState.hidden = false;
      });
    });
  }

  /* --- the floating panel ------------------------------------------------ */
  var fab = document.querySelector('[data-chat-toggle]');
  var panel = document.getElementById('chat-panel');
  if (fab && panel) {
    fab.addEventListener('click', function () {
      var open = panel.hidden;
      panel.hidden = !open;
      fab.setAttribute('aria-expanded', String(open));
      if (open) { scrollDown(); input.focus(); }
    });
    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape' && !panel.hidden) {
        panel.hidden = true;
        fab.setAttribute('aria-expanded', 'false');
        fab.focus();
      }
    });
  }

  scrollDown();
})();
