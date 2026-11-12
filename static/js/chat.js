const box = document.getElementById('messages');
const form = document.getElementById('chatForm');
const input = document.getElementById('question');
const sendBtn = document.getElementById('sendBtn');

/* Safe mini-renderer: **bold** titles and "- " bullets, built with DOM nodes (never innerHTML). */
function inline(el, text) {
  text.split(/(\*\*[^*]+\*\*)/g).forEach((part) => {
    if (/^\*\*[^*]+\*\*$/.test(part)) {
      const s = document.createElement('strong');
      s.textContent = part.slice(2, -2);
      el.appendChild(s);
    } else if (part) {
      el.appendChild(document.createTextNode(part));
    }
  });
}

function renderRich(container, text) {
  let list = null;
  text.split('\n').forEach((raw) => {
    const line = raw.trim();
    if (!line) { list = null; return; }
    if (/^[-•*]\s+/.test(line)) {
      if (!list) { list = document.createElement('ul'); container.appendChild(list); }
      const li = document.createElement('li');
      inline(li, line.replace(/^[-•*]\s+/, ''));
      list.appendChild(li);
    } else {
      list = null;
      const t = document.createElement('div');
      t.className = 'title';
      inline(t, line);
      container.appendChild(t);
    }
  });
}

function addMessage(role, text, sources, extraClass) {
  const div = document.createElement('div');
  div.className = 'msg ' + role + (extraClass ? ' ' + extraClass : '');
  if (role === 'bot' && !extraClass) {
    div.classList.add('rich');
    renderRich(div, text);
  } else {
    div.textContent = text;
  }
  if (sources && sources.length) {
    const s = document.createElement('div');
    s.className = 'sources';
    s.textContent = 'Sources: ' + sources.join(', ');
    div.appendChild(s);
  }
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
  return div;
}

function typingBubble() {
  const div = document.createElement('div');
  div.className = 'msg bot';
  div.innerHTML = '<span class="typing"><span></span><span></span><span></span></span>';
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
  return div;
}

addMessage('bot', '**Hi ' + (localStorage.getItem('username') || '') + '!**\n' +
  '- Ask me anything about company policies, products or processes.');

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const q = input.value.trim();
  if (!q) return;
  addMessage('user', q);
  input.value = '';
  sendBtn.disabled = true;
  const typing = typingBubble();
  try {
    const d = await api('/api/chat', { method: 'POST', json: { question: q } });
    typing.remove();
    addMessage('bot', d.answer, d.sources);
  } catch (err) {
    typing.remove();
    addMessage('bot', err.message, null, 'error');
  } finally {
    sendBtn.disabled = false;
    input.focus();
  }
});
