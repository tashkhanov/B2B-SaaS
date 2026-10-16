let authToken = localStorage.getItem('super_admin_token') || '';
let allTenants = [];

// Чтение токена из query ?token=...
const urlParams = new URLSearchParams(window.location.search);
const queryToken = urlParams.get('token');
if (queryToken) {
  authToken = queryToken;
  localStorage.setItem('super_admin_token', queryToken);
}

// Инициализация Telegram WebApp
const tg = window.Telegram?.WebApp;
if (tg) {
  tg.ready();
  tg.expand();
  try {
    tg.setHeaderColor('#0a0a0c');
    tg.setBackgroundColor('#0a0a0c');
  } catch (e) {}
}

function showToast(msg, iconSvg = '') {
  const toast = document.getElementById('toast');
  document.getElementById('toast-msg').textContent = msg;
  document.getElementById('toast-icon').innerHTML = iconSvg || `
    <svg class="w-4 h-4 text-accent" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      <polyline points="20 6 9 17 4 12"></polyline>
    </svg>
  `;
  toast.classList.remove('translate-y-[-100px]', 'opacity-0');
  if (tg && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred('success');
  setTimeout(() => {
    toast.classList.add('translate-y-[-100px]', 'opacity-0');
  }, 2800);
}

async function checkAuthAndLoad() {
  if (!authToken) {
    document.getElementById('auth-modal').classList.remove('hidden');
    return;
  }

  try {
    const res = await fetch('/api/super/tenants', {
      headers: { 'X-Super-Admin-Token': authToken }
    });
    if (res.status === 401) {
      localStorage.removeItem('super_admin_token');
      authToken = '';
      document.getElementById('auth-modal').classList.remove('hidden');
      return;
    }
    document.getElementById('auth-modal').classList.add('hidden');
    allTenants = await res.json();
    renderTenants(allTenants);
    updateKPIs(allTenants);
  } catch (e) {
    console.error('Ошибка загрузки данных сети:', e);
  }
}

async function handleAuthSubmit(e) {
  e.preventDefault();
  const pwInput = document.getElementById('auth-password');
  const errEl = document.getElementById('auth-error');
  const btn = document.getElementById('auth-btn');

  errEl.classList.add('hidden');
  btn.disabled = true;
  btn.textContent = 'Проверка...';

  try {
    const res = await fetch('/api/super/auth', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password: pwInput.value.trim() })
    });
    const data = await res.json();
    if (res.ok && data.ok) {
      authToken = data.token;
      localStorage.setItem('super_admin_token', authToken);
      document.getElementById('auth-modal').classList.add('hidden');
      showToast('Авторизован в Super-Admin Hub');
      loadTenants();
    } else {
      errEl.classList.remove('hidden');
      errEl.textContent = data.error || 'Неверный мастер-пароль';
    }
  } catch (err) {
    errEl.classList.remove('hidden');
    errEl.textContent = 'Ошибка связи с сервером';
  } finally {
    btn.disabled = false;
    btn.textContent = 'Войти в панель';
  }
}

async function loadTenants() {
  const reloadBtn = document.getElementById('reload-btn');
  if (reloadBtn) reloadBtn.classList.add('animate-spin');

  try {
    const res = await fetch('/api/super/tenants', {
      headers: { 'X-Super-Admin-Token': authToken }
    });
    if (!res.ok) {
      if (res.status === 401) {
        document.getElementById('auth-modal').classList.remove('hidden');
      }
      return;
    }
    allTenants = await res.json();
    renderTenants(allTenants);
    updateKPIs(allTenants);
    showToast('Данные сети обновлены');
  } catch (e) {
    console.error(e);
  } finally {
    if (reloadBtn) reloadBtn.classList.remove('animate-spin');
  }
}

function updateKPIs(tenants) {
  document.getElementById('stat-total').textContent = tenants.length;
  document.getElementById('tenants-count-badge').textContent = tenants.length;

  let active = 0;
  let grace = 0;
  let locked = 0;

  tenants.forEach(t => {
    if (t.is_locked) {
      locked++;
    } else if (t.status === 'grace_period' || t.status === 'warning') {
      grace++;
    } else {
      active++;
    }
  });

  document.getElementById('stat-active').textContent = active;
  document.getElementById('stat-grace').textContent = grace;
  document.getElementById('stat-locked').textContent = locked;
}

function renderTenants(tenants) {
  const container = document.getElementById('tenants-container');
  if (!tenants || tenants.length === 0) {
    container.innerHTML = `
      <div class="bg-surface border border-surfaceBorder rounded-2xl p-8 text-center text-gray-500 text-xs">
        Заведений в сети не найдено
      </div>
    `;
    return;
  }

  container.innerHTML = tenants.map(t => {
    // Статус бейдж
    let badgeHtml = '';
    let statusDot = '';
    if (t.is_locked) {
      badgeHtml = `
        <span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-rose-500/15 border border-rose-500/30 text-rose-400 font-bold text-[10px]">
          <span class="w-1.5 h-1.5 rounded-full bg-rose-400 pulse-red"></span>
          <span>Заблокирован</span>
        </span>
      `;
    } else if (t.status === 'grace_period') {
      badgeHtml = `
        <span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-amber-500/15 border border-amber-500/30 text-amber-400 font-bold text-[10px]">
          <span class="w-1.5 h-1.5 rounded-full bg-amber-400 pulse-amber"></span>
          <span>Grace: ${t.grace_hours_left || 0} ч.</span>
        </span>
      `;
    } else if (t.status === 'warning') {
      badgeHtml = `
        <span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-yellow-500/15 border border-yellow-500/30 text-yellow-300 font-bold text-[10px]">
          <span class="w-1.5 h-1.5 rounded-full bg-yellow-400"></span>
          <span>Истекает (${t.days_left} дн.)</span>
        </span>
      `;
    } else {
      badgeHtml = `
        <span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-emerald-500/15 border border-emerald-500/30 text-emerald-400 font-bold text-[10px]">
          <span class="w-1.5 h-1.5 rounded-full bg-emerald-400 pulse-dot"></span>
          <span>Активна (${t.days_left} дн.)</span>
        </span>
      `;
    }

    const isLocked = t.is_locked;
    const killBtnClass = isLocked
      ? 'bg-emerald-500/15 hover:bg-emerald-500/25 border-emerald-500/40 text-emerald-400'
      : 'bg-rose-500/15 hover:bg-rose-500/25 border-rose-500/40 text-rose-400';
    const killBtnText = isLocked ? 'Разблокировать' : 'Блокировать';
    const killBtnIcon = isLocked
      ? `<svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"></rect><path d="M7 11V7a5 5 0 0 1 9.9-1"></path></svg>`
      : `<svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"></rect><path d="M7 11V7a5 5 0 0 1 10 0v4"></path></svg>`;

    return `
      <div class="bg-surface border border-surfaceBorder hover:border-surfaceBorder/80 rounded-2xl p-4 transition shadow-sm">
        
        <!-- ВЕРХНЯЯ СТРОКА: Имя, Слаг и Статус-бейдж -->
        <div class="flex items-start justify-between gap-2.5 mb-2.5">
          <div class="min-w-0">
            <div class="flex items-center gap-1.5 flex-wrap">
              <h3 class="text-sm font-bold text-white tracking-tight truncate">${t.name}</h3>
              <span class="px-1.5 py-0.2 rounded-md bg-white/5 border border-white/10 text-[10px] font-mono text-accent">@${t.tenant_slug}</span>
              <span class="text-[10px] text-gray-500 font-mono">#${t.id}</span>
            </div>
            <div class="text-[11px] text-gray-400 mt-1 flex items-center gap-2 flex-wrap">
              <span class="flex items-center gap-1">
                <svg class="w-3 h-3 text-accent shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"></path>
                </svg>
                <span>${t.contact_phone || 'Без телефона'}</span>
              </span>
              ${t.contact_name ? `<span class="text-gray-500">• ${t.contact_name}</span>` : ''}
              ${t.admin_chat_id ? `<span class="text-gray-500">• TG: ${t.admin_chat_id}</span>` : ''}
            </div>
          </div>
          <div class="shrink-0">
            ${badgeHtml}
          </div>
        </div>

        <!-- ИНФО-ПЛАШКА СРОКА ПОДПИСКИ -->
        <div class="bg-bgDark/80 rounded-xl px-3 py-2 mb-3 flex items-center justify-between text-xs border border-surfaceBorder/60">
          <div class="flex items-center gap-1.5 text-gray-400 text-[11px]">
            <svg class="w-3.5 h-3.5 text-accent shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <rect x="3" y="4" width="18" height="18" rx="2" ry="2"></rect>
              <line x1="16" y1="2" x2="16" y2="6"></line>
              <line x1="8" y1="2" x2="8" y2="6"></line>
              <line x1="3" y1="10" x2="21" y2="10"></line>
            </svg>
            <span>Срок до:</span>
            <span class="font-semibold text-white font-mono">${t.expires_at || '—'}</span>
          </div>
          <div class="text-[11px] font-bold ${isLocked ? 'text-rose-400' : 'text-emerald-400'}">
            ${isLocked ? 'Доступ закрыт' : `${t.days_left} дн. активна`}
          </div>
        </div>

        <!-- СТРОКА ДЕЙСТВИЙ (Аккуратные кнопки, никогда не вылезают за экран) -->
        <div class="grid grid-cols-2 sm:flex sm:items-center sm:justify-end gap-2 pt-1 border-t border-surfaceBorder/50">
          
          <!-- 1. Кнопка +30 дней -->
          <button onclick="extendTenant30(${t.id}, '${t.name}')"
                  class="px-3 py-2 rounded-xl bg-accent/15 border border-accent/40 text-accent hover:bg-accent hover:text-bgDark font-bold text-xs transition flex items-center justify-center gap-1.5 active:scale-95 shadow-sm">
            <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
              <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon>
            </svg>
            <span>+30 дней</span>
          </button>

          <!-- 2. Kill Switch кнопка -->
          <button onclick="toggleTenantKillSwitch(${t.id}, '${t.name}', ${t.is_active})"
                  class="px-3 py-2 rounded-xl border ${killBtnClass} font-semibold text-xs transition flex items-center justify-center gap-1.5 active:scale-95">
            ${killBtnIcon}
            <span>${killBtnText}</span>
          </button>

          <!-- 3. Кнопки входа для инспекции -->
          <div class="col-span-2 sm:col-auto flex items-center gap-2">
            <a href="/admin?token=${encodeURIComponent(authToken)}" target="_blank"
               title="Открыть панель администратора салона"
               class="flex-1 sm:flex-none px-3 py-2 rounded-xl bg-bgDark hover:bg-white/5 border border-surfaceBorder text-gray-300 hover:text-white transition text-xs font-medium flex items-center justify-center gap-1.5 active:scale-95">
              <svg class="w-3.5 h-3.5 text-accent" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <line x1="4" y1="21" x2="4" y2="14"></line>
                <line x1="4" y1="10" x2="4" y2="3"></line>
                <line x1="12" y1="21" x2="12" y2="12"></line>
                <line x1="12" y1="8" x2="12" y2="3"></line>
                <line x1="20" y1="21" x2="20" y2="16"></line>
                <line x1="20" y1="12" x2="20" y2="3"></line>
                <line x1="1" y1="14" x2="7" y2="14"></line>
                <line x1="9" y1="8" x2="15" y2="8"></line>
                <line x1="17" y1="16" x2="23" y2="16"></line>
              </svg>
              <span>Админка</span>
            </a>

            <a href="/book?token=${encodeURIComponent(authToken)}" target="_blank"
               title="Открыть онлайн-запись клиента"
               class="flex-1 sm:flex-none px-3 py-2 rounded-xl bg-bgDark hover:bg-white/5 border border-surfaceBorder text-gray-300 hover:text-white transition text-xs font-medium flex items-center justify-center gap-1.5 active:scale-95">
              <svg class="w-3.5 h-3.5 text-gray-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path>
                <circle cx="12" cy="12" r="3"></circle>
              </svg>
              <span>Запись</span>
            </a>
          </div>

        </div>
      </div>
    `;
  }).join('');
}

async function extendTenant30(id, name) {
  if (!confirm(`Продлить подписку для "${name}" на 30 дней?`)) return;
  try {
    const res = await fetch(`/api/super/tenants/${id}/extend-30`, {
      method: 'POST',
      headers: { 'X-Super-Admin-Token': authToken }
    });
    const data = await res.json();
    if (res.ok && data.ok) {
      showToast(`Подписка "${name}" продлена на 30 дней!`);
      loadTenants();
    } else {
      alert(data.error || 'Ошибка при продлении');
    }
  } catch (e) {
    alert('Ошибка связи с сервером');
  }
}

async function toggleTenantKillSwitch(id, name, currentActive) {
  const actionText = currentActive === 1 ? 'ЗАБЛОКИРОВАТЬ доступ (Kill Switch)' : 'РАЗБЛОКИРОВАТЬ доступ';
  if (!confirm(`Вы действительно хотите ${actionText} для "${name}"?`)) return;

  try {
    const res = await fetch(`/api/super/tenants/${id}/toggle-active`, {
      method: 'POST',
      headers: { 'X-Super-Admin-Token': authToken }
    });
    const data = await res.json();
    if (res.ok && data.ok) {
      showToast(`Kill Switch для "${name}" обновлен`);
      loadTenants();
    } else {
      alert(data.error || 'Ошибка при изменении статуса');
    }
  } catch (e) {
    alert('Ошибка связи с сервером');
  }
}

function filterTenants() {
  const query = document.getElementById('search-input').value.toLowerCase().trim();
  if (!query) {
    renderTenants(allTenants);
    return;
  }
  const filtered = allTenants.filter(t => 
    t.name.toLowerCase().includes(query) ||
    t.tenant_slug.toLowerCase().includes(query) ||
    (t.contact_phone && t.contact_phone.toLowerCase().includes(query))
  );
  renderTenants(filtered);
}

function openNewTenantModal() {
  document.getElementById('new-tenant-modal').classList.remove('hidden');
}

function closeNewTenantModal() {
  document.getElementById('new-tenant-modal').classList.add('hidden');
}

async function handleCreateTenantSubmit(e) {
  e.preventDefault();
  const btn = document.getElementById('create-tenant-btn');
  btn.disabled = true;
  btn.textContent = 'Подключение...';

  const payload = {
    name: document.getElementById('new-name').value.trim(),
    tenant_slug: document.getElementById('new-slug').value.trim().toLowerCase(),
    admin_chat_id: document.getElementById('new-admin-id').value ? parseInt(document.getElementById('new-admin-id').value) : null,
    contact_phone: document.getElementById('new-phone').value.trim(),
    contact_name: document.getElementById('new-contact-name').value.trim(),
    days: parseInt(document.getElementById('new-days').value) || 30,
  };

  try {
    const res = await fetch('/api/super/tenants', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Super-Admin-Token': authToken,
      },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (res.ok && data.ok) {
      showToast(`Салон "${payload.name}" успешно подключен!`);
      closeNewTenantModal();
      document.getElementById('new-name').value = '';
      document.getElementById('new-slug').value = '';
      document.getElementById('new-admin-id').value = '';
      document.getElementById('new-phone').value = '';
      document.getElementById('new-contact-name').value = '';
      loadTenants();
    } else {
      alert(data.error || 'Не удалось создать заведение');
    }
  } catch (err) {
    alert('Ошибка связи с сервером');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Подключить салон';
  }
}

// Запуск при старте
window.addEventListener('DOMContentLoaded', checkAuthAndLoad);
