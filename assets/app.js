(() => {
  const src = location.pathname === '/qr/' ? 'bag01' : (/^[a-z0-9_-]{1,32}$/.test(new URLSearchParams(location.search).get('src') || '') ? new URLSearchParams(location.search).get('src') : 'site');
  const yes = document.getElementById('interest-yes');
  const no = document.getElementById('interest-no');
  const signup = document.getElementById('signup');
  const declined = document.getElementById('declined');
  const success = document.getElementById('success');
  const error = document.getElementById('form-error');
  const button = document.getElementById('submit');
  const phoneField = document.getElementById('contact-phone-field');
  const emailField = document.getElementById('contact-email-field');
  const phone = document.getElementById('phone');
  const email = document.getElementById('email');
  const contactRadios = document.querySelectorAll('input[name="contact_type"]');
  let choice = '';
  function changeContact() {
    const byEmail = document.getElementById('contact-email').checked;
    emailField.hidden = !byEmail;
    phoneField.hidden = byEmail;
    email.disabled = !byEmail;
    email.required = byEmail;
    phone.disabled = byEmail;
    phone.required = !byEmail;
    error.hidden = true;
  }
  contactRadios.forEach(radio => radio.addEventListener('change', changeContact));
  changeContact();
  async function send(path, payload) {
    const res = await fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({...payload, source: src}), credentials: 'same-origin'});
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.message || 'Попробуйте ещё раз чуть позже.');
    return data;
  }
  send('/api/view', {}).catch(() => {});
  function decide(value) {
    signup.hidden = value !== 'yes';
    declined.hidden = value !== 'no';
    if (choice === value) return;
    choice = value;
    send('/api/choice', {answer: value}).catch(() => {});
  }
  yes.addEventListener('change', () => { if (yes.checked) decide('yes'); });
  no.addEventListener('change', () => { if (no.checked) decide('no'); });
  signup.addEventListener('submit', async (event) => {
    event.preventDefault();
    error.hidden = true;
    const role = signup.querySelector('input[name="role"]:checked');
    const consent = document.getElementById('consent');
    const method = document.getElementById('contact-email').checked ? 'email' : 'phone';
    const digits = phone.value.replace(/\D/g, '');
    if (!role) { error.textContent = 'Выберите, как хотите участвовать.'; error.hidden = false; return; }
    if (method === 'phone' && !/^[78]\d{10}$/.test(digits)) { error.textContent = 'Проверьте номер телефона: +7 и ещё 10 цифр.'; error.hidden = false; phone.focus(); return; }
    if (method === 'email' && (!email.validity.valid || !email.value.trim())) { error.textContent = 'Проверьте адрес электронной почты.'; error.hidden = false; email.focus(); return; }
    if (!consent.checked) { error.textContent = 'Для приглашения нужно согласие на обработку контактных данных.'; error.hidden = false; consent.focus(); return; }
    button.disabled = true;
    try {
      const result = await send('/api/lead', {role: role.value, contact_type: method, phone: method === 'phone' ? phone.value : '', email: method === 'email' ? email.value.trim() : '', consent: consent.checked, website: document.getElementById('website').value});
      if (result && result.edit_token) {
        sessionStorage.setItem('pazme_profile_token', result.edit_token);
        sessionStorage.setItem('pazme_profile_role', role.value);
        location.assign(role.value === 'business' ? '/business/' : '/participant/');
        return;
      }
      signup.hidden = true;
      document.getElementById('interest').hidden = true;
      success.hidden = false;
      success.scrollIntoView({behavior:'smooth',block:'nearest'});
    } catch (e) {
      error.textContent = e.message || 'Попробуйте ещё раз чуть позже.';
      error.hidden = false;
    } finally { button.disabled = false; }
  });
})();
