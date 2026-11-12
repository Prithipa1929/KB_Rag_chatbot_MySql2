function el(tag, text, cls) {
  const e = document.createElement(tag);
  if (text !== undefined) e.textContent = text;
  if (cls) e.className = cls;
  return e;
}
const fmt = (iso) => new Date(iso).toLocaleString();

/* ---- tabs ---- */
document.querySelectorAll('.tab').forEach((t) =>
  t.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach((x) => x.classList.toggle('active', x === t));
    document.querySelectorAll('.panel').forEach((p) => { p.hidden = p.id !== 'panel-' + t.dataset.tab; });
    if (t.dataset.tab === 'users') loadUsers();
    if (t.dataset.tab === 'chats') loadChats();
    if (t.dataset.tab === 'unanswered') loadUnanswered();
  })
);

/* ---- documents ---- */
const docsBody = document.getElementById('docsBody');
const docsEmpty = document.getElementById('docsEmpty');
const uploadMsg = document.getElementById('uploadMsg');

async function loadDocs() {
  const { documents } = await api('/api/admin/documents');
  docsBody.replaceChildren();
  docsEmpty.hidden = documents.length > 0;
  documents.forEach((d) => {
    const tr = el('tr');
    tr.append(el('td', '📄 ' + d.name), el('td', String(d.chunks)));
    const td = el('td');
    const del = el('button', 'Delete', 'btn btn-danger');
    del.addEventListener('click', async () => {
      if (!confirm('Delete ' + d.name + ' from the knowledge base?')) return;
      try { await api('/api/admin/documents/' + encodeURIComponent(d.name), { method: 'DELETE' }); loadDocs(); }
      catch (err) { showMsg(uploadMsg, err.message, false); }
    });
    td.append(del); tr.append(td); docsBody.append(tr);
  });
}

async function uploadFiles(files) {
  if (!files.length) return;
  const fd = new FormData();
  [...files].forEach((f) => fd.append('files', f));
  showMsg(uploadMsg, 'Uploading and indexing… this can take a moment.', true);
  try {
    const { results } = await api('/api/admin/upload', { method: 'POST', body: fd });
    const failed = results.filter((r) => r.error);
    const okCount = results.length - failed.length;
    const text = okCount + ' file(s) indexed.' + failed.map((f) => ' ' + f.name + ': ' + f.error + '.').join('');
    showMsg(uploadMsg, text, failed.length === 0);
    loadDocs();
  } catch (err) { showMsg(uploadMsg, err.message, false); }
}

const dz = document.getElementById('dropzone');
const fileInput = document.getElementById('fileInput');
fileInput.addEventListener('change', () => { uploadFiles(fileInput.files); fileInput.value = ''; });
['dragenter', 'dragover'].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add('drag'); }));
['dragleave', 'drop'].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove('drag'); }));
dz.addEventListener('drop', (e) => uploadFiles(e.dataTransfer.files));

document.getElementById('rebuildBtn').addEventListener('click', async (e) => {
  e.target.disabled = true;
  showMsg(uploadMsg, 'Rebuilding from company_docs/…', true);
  try {
    const { results } = await api('/api/admin/rebuild', { method: 'POST' });
    showMsg(uploadMsg, 'Re-indexed ' + Object.keys(results).length + ' file(s).', true);
    loadDocs();
  } catch (err) { showMsg(uploadMsg, err.message, false); }
  e.target.disabled = false;
});

/* ---- users ---- */
async function loadUsers() {
  const { users } = await api('/api/admin/users');
  const body = document.getElementById('usersBody');
  body.replaceChildren();
  users.forEach((u) => {
    const tr = el('tr');
    const role = el('td'); role.append(el('span', u.role, 'badge' + (u.role === 'admin' ? ' admin' : '')));
    tr.append(el('td', u.username), role, el('td', String(u.questions)), el('td', fmt(u.created_at)));
    body.append(tr);
  });
}

/* ---- chat history ---- */
async function loadChats() {
  const { chats } = await api('/api/admin/chats');
  const body = document.getElementById('chatsBody');
  body.replaceChildren();
  document.getElementById('chatsEmpty').hidden = chats.length > 0;
  chats.forEach((c) => {
    const tr = el('tr');
    const answer = c.answer.replace(/\*\*/g, '').replace(/\n+/g, ' ');
    tr.append(el('td', fmt(c.created_at)), el('td', c.username + ' (' + c.role + ')'), el('td', c.question),
              el('td', answer.length > 160 ? answer.slice(0, 160) + '…' : answer), el('td', c.answered ? 'Yes' : 'No'));
    body.append(tr);
  });
}

/* ---- unanswered ---- */
async function loadUnanswered() {
  const { items } = await api('/api/admin/unanswered');
  const body = document.getElementById('unansweredBody');
  body.replaceChildren();
  document.getElementById('unansweredEmpty').hidden = items.length > 0;
  items.forEach((i) => {
    const tr = el('tr');
    tr.append(el('td', i.question), el('td', i.username), el('td', fmt(i.created_at)));
    body.append(tr);
  });
}

loadDocs().catch((err) => showMsg(uploadMsg, err.message, false));
