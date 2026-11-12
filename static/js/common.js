/* Shared helpers: token storage, authenticated fetch, logout. */
const TOKEN_KEY = 'token';

function setSession(d) {
  localStorage.setItem(TOKEN_KEY, d.token);
  localStorage.setItem('role', d.role);
  localStorage.setItem('username', d.username);
}

async function clearSession() {
  localStorage.clear();
  try { await fetch('/api/auth/logout', { method: 'POST' }); } catch (e) { /* ignore */ }
}

/* Every API call sends the token as: Authorization: Bearer <token> */
async function api(path, opts = {}) {
  const headers = Object.assign({}, opts.headers || {});
  const token = localStorage.getItem(TOKEN_KEY);
  if (token) headers['Authorization'] = 'Bearer ' + token;
  if (opts.json !== undefined) {
    headers['Content-Type'] = 'application/json';
    opts = Object.assign({}, opts, { body: JSON.stringify(opts.json) });
  }
  const res = await fetch(path, Object.assign({}, opts, { headers }));
  let data = {};
  try { data = await res.json(); } catch (e) { /* non-JSON */ }
  if (res.status === 401 && !path.startsWith('/api/auth/')) {
    await clearSession();
    location.href = '/login';
    throw new Error('Session expired. Please log in again.');
  }
  if (!res.ok) throw new Error(data.error || 'Request failed');
  return data;
}

function showMsg(el, text, ok) {
  el.textContent = text;
  el.className = 'alert' + (ok ? ' ok' : '');
  el.hidden = false;
}

document.querySelectorAll('[data-logout]').forEach((b) =>
  b.addEventListener('click', async () => { await clearSession(); location.href = '/login'; })
);
