(() => {
  const form = document.getElementById('profile-form');
  const kind = document.body.dataset.profile;
  const key = sessionStorage.getItem('pazme_profile_token');
  const savedRole = sessionStorage.getItem('pazme_profile_role');
  const error = document.getElementById('profile-error');
  const btn = document.getElementById('profile-submit');
  if (!key || kind !== savedRole) {
    form.innerHTML = '<p>Рады знакомству! Начните с небольшой заявки на главной странице, и продолжим здесь.</p><a class="submit profile-return" href="/">Перейти к знакомству ↗</a>';
    return;
  }
  form.addEventListener('submit', async event => {
    event.preventDefault();
    error.hidden = true;
    const get = id => (document.getElementById(id)?.value || '').trim();
    const payload = {edit_token:key, role:kind, name:get('name'), city:get('city')};
    if (!payload.city) { error.textContent = 'Укажите город или города.'; error.hidden = false; return; }
    if (kind === 'business') {
      payload.business_name = get('business_name');
      payload.business_activity = get('business_activity');
      payload.business_offer = get('business_offer');
      payload.business_site = get('business_site');
      if (!payload.business_name || !payload.business_activity) {error.textContent = 'Укажите название и направление бизнеса.';error.hidden = false;return;}
    }
    btn.disabled = true;
    try {
      const res = await fetch('/api/profile', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload),credentials:'same-origin'});
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.message || 'Попробуйте отправить ещё раз.');
      form.hidden = true;
      document.getElementById('profile-success').hidden = false;
      sessionStorage.removeItem('pazme_profile_token');
      sessionStorage.removeItem('pazme_profile_role');
      window.scrollTo({top:0,behavior:'smooth'});
    } catch(e) {error.textContent=e.message || 'Попробуйте ещё раз.';error.hidden=false;}
    finally {btn.disabled = false;}
  });
})();
