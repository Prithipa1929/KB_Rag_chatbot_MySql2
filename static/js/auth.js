const loginForm = document.getElementById('loginForm');
const registerForm = document.getElementById('registerForm');
const formMsg = document.getElementById('formMsg');

if (loginForm) {
  loginForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const btn = loginForm.querySelector('button[type=submit]');
    btn.disabled = true;
    try {
      const d = await api('/api/auth/login', {
        method: 'POST',
        json: { username: loginForm.elements.username.value, password: loginForm.elements.password.value },
      });
      setSession(d);
      location.href = d.role === 'admin' ? '/admin' : '/chat';
    } catch (err) {
      showMsg(formMsg, err.message, false);
      btn.disabled = false;
    }
  });
}

if (registerForm) {
  const codeField = document.getElementById('adminCodeField');
  const currentRole = () => registerForm.querySelector('input[name=role]:checked').value;
  const syncRole = () => { codeField.hidden = currentRole() !== 'admin'; };
  registerForm.querySelectorAll('input[name=role]').forEach((r) => r.addEventListener('change', syncRole));
  syncRole();                                    // browsers remember the chosen radio after a refresh: show the box then too
  window.addEventListener('pageshow', syncRole); // ... and after the Back button

  registerForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (registerForm.elements.password.value !== registerForm.elements.confirm.value) {
      showMsg(formMsg, 'Passwords do not match', false);
      return;
    }
    const btn = registerForm.querySelector('button[type=submit]');
    btn.disabled = true;
    try {
      const d = await api('/api/auth/register', {
        method: 'POST',
        json: {
          username: registerForm.elements.username.value,
          password: registerForm.elements.password.value,
          role: currentRole(),
          admin_code: registerForm.elements.admin_code.value,
        },
      });
      showMsg(formMsg, d.message, true);
      setTimeout(() => { location.href = '/login'; }, 1200);
    } catch (err) {
      showMsg(formMsg, err.message, false);
      btn.disabled = false;
    }
  });
}