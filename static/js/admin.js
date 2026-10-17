const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
if (tg) {
  tg.ready();
  tg.expand();
}

let activeTab = 'bookings';
let allBookings = [];
let currentFilter = 'all';
let allServices = [];
let allBarbers = [];

// --- TAB SWITCHING ---
function switchTab(tab) {
  activeTab = tab;
  ['bookings', 'services', 'barbers'].forEach(t => {
    const navBtn = document.getElementById(`nav-${t}`);
    const sec = document.getElementById(`section-${t}`);
    if (t === tab) {
      navBtn.className = 'flex-1 py-1.5 px-2 rounded-lg text-center transition flex items-center justify-center gap-1.5 bg-accent text-white font-semibold shadow-sm shadow-accent/20';
      sec.classList.remove('hidden');
    } else {
      navBtn.className = 'flex-1 py-1.5 px-2 rounded-lg text-center transition flex items-center justify-center gap-1.5 text-gray-400 hover:text-white';
      sec.classList.add('hidden');
    }
  });

  const headerActionText = document.getElementById('header-action-text');
  if (tab === 'bookings') {
    headerActionText.textContent = 'Записать';
    fetchBookings();
  } else if (tab === 'services') {
    headerActionText.textContent = 'Услуга +';
    fetchServices();
  } else if (tab === 'barbers') {
    headerActionText.textContent = 'Барбер +';
    fetchBarbers();
  }
}

function openContextModal() {
  if (activeTab === 'bookings') openManualBookingModal();
  else if (activeTab === 'services') openServiceModal();
  else if (activeTab === 'barbers') openBarberModal();
}

function refreshCurrentTab() {
  if (activeTab === 'bookings') fetchBookings();
  else if (activeTab === 'services') fetchServices();
  else if (activeTab === 'barbers') fetchBarbers();
}

// --- ADMIN SECURITY CHECK ---
async function checkAdminAccess() {
  try {
    const res = await fetch('/api/config');
    const data = await res.json();
    const adminId = data.admin_chat_id;
    const superAdminId = data.super_admin_chat_id;

    // 1. Прямой доступ для Создателя/Суперадмина (по токену, cookie или флагом от сервера)
    if (data.is_super_admin || window.location.search.includes('token=') || document.cookie.includes('super_admin_token')) {
      return true;
    }

    if (tg && tg.initDataUnsafe && tg.initDataUnsafe.user) {
      const currentUserId = tg.initDataUnsafe.user.id;
      if (superAdminId && currentUserId === superAdminId) {
        return true;
      }
      if (adminId && currentUserId !== adminId) {
        document.getElementById('admin-container').classList.add('hidden');
        document.getElementById('access-denied-screen').classList.remove('hidden');
        return false;
      }
    }
  } catch (e) {
    console.warn("Config check error:", e);
  }
  return true;
}

// ==================== 1. ЗАПИСИ (BOOKINGS) ====================
async function fetchBookings() {
  const isAllowed = await checkAdminAccess();
  if (!isAllowed) return;

  const loader = document.getElementById('bookings-loading');
  const list = document.getElementById('bookings-list');
  const emptyState = document.getElementById('bookings-empty-state');

  loader.classList.remove('hidden');
  list.classList.add('hidden');
  emptyState.classList.add('hidden');

  try {
    const response = await fetch('/api/bookings');
    if (!response.ok) throw new Error('Network error');
    allBookings = await response.json();

    updateStats();
    renderBookings();
  } catch (error) {
    console.error('Ошибка загрузки записей:', error);
    loader.classList.add('hidden');
    emptyState.classList.remove('hidden');
    emptyState.querySelector('p').textContent = 'Ошибка загрузки данных';
  } finally {
    loader.classList.add('hidden');
  }
}

function updateStats() {
  document.getElementById('stat-total').textContent = allBookings.length;
  const todayStr = new Date().toISOString().slice(0, 10);
  const todayCount = allBookings.filter(b => {
    const d = (b.booking_time || b.created_at || '').toLowerCase();
    return d.includes('сегодня') || d.includes(todayStr) || d.includes('bugun');
  }).length;
  document.getElementById('stat-today').textContent = todayCount;

  const newCount = allBookings.filter(b => b.status === 'new').length;
  document.getElementById('stat-new').textContent = newCount;
}

function setFilter(filter) {
  currentFilter = filter;
  ['all', 'today', 'upcoming'].forEach(f => {
    const btn = document.getElementById(`filter-${f}`);
    if (f === filter) {
      btn.className = 'flex-1 py-1.5 px-3 rounded-lg text-center transition bg-accent text-white font-semibold shadow-sm shadow-accent/20';
    } else {
      btn.className = 'flex-1 py-1.5 px-3 rounded-lg text-center transition text-gray-400 hover:text-white';
    }
  });
  renderBookings();
}

function renderBookings() {
  const list = document.getElementById('bookings-list');
  const emptyState = document.getElementById('bookings-empty-state');
  list.innerHTML = '';

  let filtered = allBookings;
  const todayStr = new Date().toISOString().slice(0, 10);

  if (currentFilter === 'today') {
    filtered = allBookings.filter(b => {
      const t = (b.booking_time || b.created_at || '').toLowerCase();
      return t.includes('сегодня') || t.includes(todayStr) || t.includes('bugun');
    });
  } else if (currentFilter === 'upcoming') {
    filtered = allBookings.filter(b => b.status !== 'cancelled' && b.status !== 'completed');
  }

  if (filtered.length === 0) {
    emptyState.classList.remove('hidden');
    list.classList.add('hidden');
    return;
  }

  emptyState.classList.add('hidden');
  list.classList.remove('hidden');

  filtered.forEach(booking => {
    const card = document.createElement('div');
    card.className = 'bg-surface border border-surfaceBorder rounded-2xl p-4 transition hover:border-surfaceBorder/90 shadow-md shadow-black/20';

    const cleanUsername = (booking.username || '').replace('@', '').trim();
    const tgLink = cleanUsername ? `https://t.me/${cleanUsername}` : null;
    const phone = booking.phone ? booking.phone.trim() : null;

    let statusBadge = '<span class="px-2 py-0.5 rounded-full text-[11px] font-semibold bg-amber-500/10 text-amber-400 border border-amber-500/30">Новая</span>';
    if (booking.status === 'confirmed') {
      statusBadge = '<span class="px-2 py-0.5 rounded-full text-[11px] font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/30">Подтверждена</span>';
    } else if (booking.status === 'completed') {
      statusBadge = '<span class="px-2 py-0.5 rounded-full text-[11px] font-semibold bg-gray-500/10 text-gray-400 border border-gray-500/30">Завершена</span>';
    } else if (booking.status === 'cancelled') {
      statusBadge = '<span class="px-2 py-0.5 rounded-full text-[11px] font-semibold bg-rose-500/10 text-rose-400 border border-rose-500/30">Отменена</span>';
    }

    const barberName = booking.barber_name ? escapeHtml(booking.barber_name) : 'Любой мастер';

    let actionsHtml = '';
    if (booking.status === 'new') {
      actionsHtml = `
        <div class="flex items-center gap-2 pt-2 border-t border-surfaceBorder/40 mt-2">
          <button onclick="updateBookingStatus(${booking.id}, 'confirmed')" class="flex-1 inline-flex items-center justify-center gap-1.5 py-2 px-3 rounded-xl bg-emerald-500/15 hover:bg-emerald-500/25 text-emerald-400 border border-emerald-500/30 text-xs font-semibold transition active:scale-95">
            <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="20 6 9 17 4 12"/>
            </svg>
            <span>Подтвердить</span>
          </button>
          <button onclick="confirmCancelBooking(${booking.id})" class="flex-1 inline-flex items-center justify-center gap-1.5 py-2 px-3 rounded-xl bg-rose-500/15 hover:bg-rose-500/25 text-rose-400 border border-rose-500/30 text-xs font-semibold transition active:scale-95">
            <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
              <line x1="18" y1="6" x2="6" y2="18"/>
              <line x1="6" y1="6" x2="18" y2="18"/>
            </svg>
            <span>Отменить</span>
          </button>
        </div>
      `;
    } else if (booking.status === 'confirmed') {
      actionsHtml = `
        <div class="flex items-center gap-2 pt-2 border-t border-surfaceBorder/40 mt-2">
          <button onclick="confirmCancelBooking(${booking.id})" class="flex-1 inline-flex items-center justify-center gap-1.5 py-2 px-3 rounded-xl bg-rose-500/10 hover:bg-rose-500/20 text-rose-400 border border-rose-500/25 text-xs font-semibold transition active:scale-95">
            <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
              <line x1="18" y1="6" x2="6" y2="18"/>
              <line x1="6" y1="6" x2="18" y2="18"/>
            </svg>
            <span>Отменить бронь</span>
          </button>
        </div>
      `;
    }

    card.innerHTML = `
      <div class="flex items-start justify-between mb-2">
        <div>
          <div class="font-semibold text-white text-sm flex items-center gap-1.5">
            ${escapeHtml(booking.full_name || 'Гость')}
            ${cleanUsername ? `<span class="text-xs font-normal text-accent">@${escapeHtml(cleanUsername)}</span>` : ''}
          </div>
          <div class="text-xs text-gray-400 mt-1 flex items-center gap-1.5">
            <svg class="w-3.5 h-3.5 text-accent shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><line x1="20" y1="4" x2="8.12" y2="15.88"/><line x1="14.47" y1="14.48" x2="20" y2="20"/><line x1="8.12" y1="8.12" x2="12" y2="12"/>
            </svg>
            <span>${escapeHtml(booking.service || 'Стрижка / Комплекс')}</span>
          </div>
        </div>
        <div>
          ${statusBadge}
        </div>
      </div>

      <!-- Назначенный мастер и филиал бейджи -->
      <div class="mb-2 flex flex-wrap items-center gap-1.5">
        <span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-lg text-[11px] font-medium bg-surfaceBorder/60 text-gray-300 border border-surfaceBorder">
          <svg class="w-3 h-3 text-gold" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/>
          </svg>
          <span>Барбер: <strong class="text-white font-semibold">${barberName}</strong></span>
        </span>
        <span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-lg text-[11px] font-medium bg-accent/10 text-accent border border-accent/25">
          <span>📍 ${escapeHtml(booking.branch_name || 'Миллий бог')}</span>
        </span>
      </div>

      <div class="bg-bgDark/60 rounded-xl p-2.5 mb-3 border border-surfaceBorder/50 space-y-1.5 text-xs">
        <div class="flex items-center text-gray-300 gap-2">
          <svg class="w-3.5 h-3.5 text-gray-500 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>
          </svg>
          <span class="text-gray-500 w-16 shrink-0">Время:</span>
          <span class="font-semibold text-accent">${escapeHtml(booking.booking_time || 'Не указано')}</span>
        </div>
        ${phone ? `
          <div class="flex items-center text-gray-300 gap-2">
            <svg class="w-3.5 h-3.5 text-gray-500 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"/>
            </svg>
            <span class="text-gray-500 w-16 shrink-0">Телефон:</span>
            <span class="font-medium text-white">${escapeHtml(phone)}</span>
          </div>
        ` : ''}
        <div class="flex items-center text-gray-400 text-[11px] pt-1.5 border-t border-surfaceBorder/30 gap-2">
          <svg class="w-3.5 h-3.5 text-gray-500 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
            <polyline points="14 2 14 8 20 8"/>
            <line x1="16" y1="13" x2="8" y2="13"/>
            <line x1="16" y1="17" x2="8" y2="17"/>
          </svg>
          <span class="text-gray-500 w-16 shrink-0">Создано:</span>
          <span class="text-gray-300">${escapeHtml(booking.created_at || '—')}</span>
        </div>
      </div>

      <div class="flex items-center space-x-2 pt-1 border-t border-surfaceBorder/40">
        ${phone ? `
          <a href="tel:${phone}" class="flex-1 inline-flex items-center justify-center gap-1.5 py-2 px-3 rounded-xl bg-surface hover:bg-surfaceBorder text-xs font-semibold text-white border border-surfaceBorder transition active:scale-95">
            <svg class="w-3.5 h-3.5 text-gray-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"/>
            </svg>
            <span>Позвонить</span>
          </a>
        ` : `
          <button disabled class="flex-1 inline-flex items-center justify-center gap-1.5 py-2 px-3 rounded-xl bg-surfaceBorder/30 text-xs font-semibold text-gray-500 cursor-not-allowed">
            <span>Нет тел.</span>
          </button>
        `}

        ${cleanUsername ? `
          <a href="${tgLink}" target="_blank" class="flex-1 inline-flex items-center justify-center gap-1.5 py-2 px-3 rounded-xl bg-accent hover:bg-accentHover text-xs font-semibold text-white transition active:scale-95 shadow-sm shadow-accent/20">
            <svg class="w-3.5 h-3.5 text-white" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="m22 2-7 20-4-9-9-4Z"/>
              <path d="M22 2 11 13"/>
            </svg>
            <span>Написать</span>
          </a>
        ` : `
          <button onclick="alert('У клиента нет публичного @username')" class="flex-1 inline-flex items-center justify-center gap-1.5 py-2 px-3 rounded-xl bg-surfaceBorder/30 text-xs font-semibold text-gray-500">
            <span>Нет TG</span>
          </button>
        `}
      </div>
      ${actionsHtml}
    `;

    list.appendChild(card);
  });
}

async function updateBookingStatus(id, newStatus) {
  try {
    const res = await fetch(`/api/bookings/${id}/status`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({status: newStatus})
    });
    const data = await res.json();
    if (data.ok) {
      if (tg && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred('success');
      fetchBookings();
    } else {
      alert(data.error || 'Ошибка при обновлении статуса');
    }
  } catch (e) {
    alert('Ошибка при смене статуса');
  }
}

function confirmCancelBooking(id) {
  if (!confirm('Вы уверены, что хотите отменить эту запись? Клиент получит уведомление в Telegram.')) {
    return;
  }
  updateBookingStatus(id, 'cancelled');
}

// --- MANUAL BOOKING MODAL ---
async function openManualBookingModal() {
  const modal = document.getElementById('manual-modal');
  modal.classList.remove('hidden');
  const dateInput = document.getElementById('m-date');
  if (!dateInput.value) {
    dateInput.value = new Date().toISOString().slice(0, 10);
  }

  // Загрузить барберов для выбора
  try {
    const res = await fetch('/api/barbers');
    const barbers = await res.json();
    allBarbers = barbers;
    const bSelect = document.getElementById('m-barber');
    bSelect.innerHTML = '<option value="">⚡ Любой свободный мастер (Автовыбор)</option>';
    barbers.forEach(b => {
      const opt = document.createElement('option');
      opt.value = b.id;
      opt.textContent = `${b.name} (${b.role || 'Барбер'})`;
      bSelect.appendChild(opt);
    });
  } catch (e) {
    console.warn("Could not load barbers for selector", e);
  }

  // Загрузить услуги для выбора
  try {
    const res = await fetch('/api/services');
    const services = await res.json();
    allServices = services;
    const sSelect = document.getElementById('m-service');
    sSelect.innerHTML = '';
    services.filter(s => s.is_active).forEach(s => {
      const opt = document.createElement('option');
      opt.value = s.name;
      opt.textContent = `${s.name} (${s.price.toLocaleString()} сум)`;
      sSelect.appendChild(opt);
    });
  } catch (e) {
    console.warn("Could not load services for selector", e);
  }
}

function closeManualBookingModal() {
  document.getElementById('manual-modal').classList.add('hidden');
}

async function submitManualBooking() {
  const name = document.getElementById('m-name').value.trim();
  const phone = document.getElementById('m-phone').value.trim();
  const barberIdVal = document.getElementById('m-barber').value;
  const service = document.getElementById('m-service').value;
  const bDate = document.getElementById('m-date').value;
  const bTime = document.getElementById('m-time').value;
  const branchIdVal = document.getElementById('m-branch') ? document.getElementById('m-branch').value : '1';
  const branchName = branchIdVal === '2' ? 'Чиланзар (2-й кв.)' : 'Миллий бог';

  if (!name) {
    alert('Пожалуйста, введите имя клиента');
    return;
  }
  if (!bDate || !bTime) {
    alert('Пожалуйста, выберите дату и время');
    return;
  }

  const btn = document.getElementById('m-submit-btn');
  btn.disabled = true;
  btn.textContent = 'Сохранение...';

  try {
    const res = await fetch('/api/bookings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        full_name: name,
        phone: phone || null,
        service: service,
        booking_time: `${bDate} ${bTime}`,
        barber_id: barberIdVal ? parseInt(barberIdVal) : null,
        branch_id: parseInt(branchIdVal),
        branch_name: branchName,
        details: 'Ручная запись администратором через TMA'
      })
    });
    const data = await res.json();

    if (res.ok && data.ok) {
      closeManualBookingModal();
      document.getElementById('m-name').value = '';
      document.getElementById('m-phone').value = '';
      fetchBookings();
      if (tg && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred('success');
    } else {
      alert(data.error || 'Ошибка при сохранении записи.');
    }
  } catch (e) {
    alert('Ошибка соединения с сервером.');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Сохранить';
  }
}

// ==================== 2. УСЛУГИ (SERVICES CRUD) ====================
async function fetchServices() {
  const loader = document.getElementById('services-loading');
  const list = document.getElementById('services-list');
  loader.classList.remove('hidden');
  list.classList.add('hidden');

  try {
    const res = await fetch('/api/services');
    allServices = await res.json();
    renderServices();
  } catch (e) {
    console.error("Ошибка загрузки услуг:", e);
  } finally {
    loader.classList.add('hidden');
    list.classList.remove('hidden');
  }
}

function renderServices() {
  const list = document.getElementById('services-list');
  list.innerHTML = '';

  if (allServices.length === 0) {
    list.innerHTML = '<div class="py-6 text-center text-gray-500 text-xs">Услуг пока нет. Нажмите "+ Добавить".</div>';
    return;
  }

  allServices.forEach(s => {
    const card = document.createElement('div');
    card.className = 'bg-surface border border-surfaceBorder rounded-2xl p-3.5 flex items-center justify-between gap-3 shadow-sm';
    
    const badgeHtml = s.badge ? `<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-accent/20 text-accent border border-accent/30">${escapeHtml(s.badge)}</span>` : '';
    const statusDot = s.is_active ? '<span class="w-2 h-2 rounded-full bg-emerald-500" title="Активна"></span>' : '<span class="w-2 h-2 rounded-full bg-gray-600" title="Отключена"></span>';

    card.innerHTML = `
      <div class="flex-1 min-w-0">
        <div class="flex items-center gap-2 mb-1">
          ${statusDot}
          <h4 class="font-semibold text-white text-xs truncate">${escapeHtml(s.name)}</h4>
          ${badgeHtml}
        </div>
        <div class="flex items-center gap-2 text-[11px] text-gray-400">
          <span class="font-bold text-gold">${s.price.toLocaleString()} сум</span>
          <span>•</span>
          <span>${s.duration || 60} мин</span>
        </div>
        ${s.description ? `<p class="text-[11px] text-gray-500 mt-1 truncate">${escapeHtml(s.description)}</p>` : ''}
      </div>
      <button onclick="openServiceModal(${s.id})" class="p-2 rounded-xl bg-surfaceBorder/40 hover:bg-surfaceBorder text-gray-300 hover:text-white transition active:scale-95" title="Редактировать">
        <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
          <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
        </svg>
      </button>
    `;
    list.appendChild(card);
  });
}

function openServiceModal(serviceId = null) {
  const modal = document.getElementById('service-modal');
  const title = document.getElementById('service-modal-title');
  const delBtn = document.getElementById('srv-delete-btn');

  document.getElementById('srv-id').value = serviceId || '';
  if (serviceId) {
    const s = allServices.find(item => item.id === serviceId);
    if (s) {
      title.textContent = 'Редактировать услугу';
      document.getElementById('srv-name').value = s.name;
      document.getElementById('srv-price').value = s.price;
      document.getElementById('srv-duration').value = s.duration || 60;
      document.getElementById('srv-badge').value = s.badge || '';
      document.getElementById('srv-desc').value = s.description || '';
      delBtn.classList.remove('hidden');
    }
  } else {
    title.textContent = 'Новая услуга';
    document.getElementById('srv-name').value = '';
    document.getElementById('srv-price').value = '';
    document.getElementById('srv-duration').value = '60';
    document.getElementById('srv-badge').value = '';
    document.getElementById('srv-desc').value = '';
    delBtn.classList.add('hidden');
  }

  modal.classList.remove('hidden');
}

function closeServiceModal() {
  document.getElementById('service-modal').classList.add('hidden');
}

async function saveService() {
  const sId = document.getElementById('srv-id').value;
  const name = document.getElementById('srv-name').value.trim();
  const price = parseInt(document.getElementById('srv-price').value);
  const duration = parseInt(document.getElementById('srv-duration').value) || 60;
  const badge = document.getElementById('srv-badge').value.trim();
  const description = document.getElementById('srv-desc').value.trim();

  if (!name || isNaN(price)) {
    alert('Пожалуйста, укажите название и цену услуги');
    return;
  }

  const btn = document.getElementById('srv-save-btn');
  btn.disabled = true;
  btn.textContent = 'Сохранение...';

  const payload = { name, price, duration, badge: badge || null, description: description || null, is_active: 1 };
  const url = sId ? `/api/services/${sId}/update` : '/api/services';

  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (data.ok) {
      closeServiceModal();
      fetchServices();
      if (tg && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred('success');
    } else {
      alert(data.error || 'Ошибка при сохранении услуги');
    }
  } catch (e) {
    alert('Ошибка связи с сервером');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Сохранить';
  }
}

async function deleteService() {
  const sId = document.getElementById('srv-id').value;
  if (!sId) return;
  if (!confirm('Вы точно хотите удалить эту услугу из меню?')) return;

  try {
    const res = await fetch(`/api/services/${sId}/delete`, { method: 'POST' });
    const data = await res.json();
    if (data.ok) {
      closeServiceModal();
      fetchServices();
    } else {
      alert(data.error || 'Ошибка при удалении');
    }
  } catch (e) {
    alert('Ошибка при удалении');
  }
}

// ==================== 3. БАРБЕРЫ (BARBERS & SHIFTS) ====================
async function fetchBarbers() {
  const loader = document.getElementById('barbers-loading');
  const list = document.getElementById('barbers-list');
  loader.classList.remove('hidden');
  list.classList.add('hidden');

  try {
    const res = await fetch('/api/barbers');
    allBarbers = await res.json();
    renderBarbers();
  } catch (e) {
    console.error("Ошибка загрузки барберов:", e);
  } finally {
    loader.classList.add('hidden');
    list.classList.remove('hidden');
  }
}

function renderBarbers() {
  const list = document.getElementById('barbers-list');
  list.innerHTML = '';

  if (allBarbers.length === 0) {
    list.innerHTML = '<div class="py-6 text-center text-gray-500 text-xs">Мастеров пока нет. Нажмите "+ Добавить".</div>';
    return;
  }

  const dayNames = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];

  allBarbers.forEach(b => {
    const card = document.createElement('div');
    card.className = 'bg-surface border border-surfaceBorder rounded-2xl p-4 shadow-sm';

    const shiftBadge = b.is_on_shift 
      ? `<button onclick="toggleShift(${b.id})" class="px-2.5 py-1 rounded-xl text-[11px] font-semibold bg-emerald-500/15 text-emerald-400 border border-emerald-500/30 hover:bg-emerald-500/25 transition active:scale-95 flex items-center gap-1.5"><span class="w-2 h-2 rounded-full bg-emerald-400 pulse-dot"></span>На смене</button>`
      : `<button onclick="toggleShift(${b.id})" class="px-2.5 py-1 rounded-xl text-[11px] font-semibold bg-gray-500/15 text-gray-400 border border-gray-500/30 hover:bg-gray-500/25 transition active:scale-95 flex items-center gap-1.5"><span class="w-2 h-2 rounded-full bg-gray-500"></span>Выходной</button>`;

    const workDays = (b.work_days || '0,1,2,3,4,5,6').split(',').map(d => parseInt(d.trim()));
    const daysBadges = dayNames.map((name, idx) => {
      const isWorking = workDays.includes(idx);
      return `<span class="px-1.5 py-0.5 rounded text-[10px] font-medium ${isWorking ? 'bg-accent/20 text-accent border border-accent/30' : 'bg-surfaceBorder/30 text-gray-500'}">${name}</span>`;
    }).join('');

    const initials = (b.name || 'М').trim().slice(0, 2).toUpperCase();

    card.innerHTML = `
      <div class="flex items-start justify-between gap-3 mb-3">
        <div class="flex items-center gap-3">
          <div class="w-10 h-10 rounded-xl bg-gold/10 border border-gold/25 flex items-center justify-center text-gold font-bold text-xs tracking-wider shadow-sm">
            ${escapeHtml(initials)}
          </div>
          <div>
            <h4 class="font-bold text-white text-sm leading-tight">${escapeHtml(b.name)}</h4>
            <div class="inline-flex items-center gap-1 mt-1 px-2 py-0.5 rounded-md text-[10px] font-semibold bg-gold/10 text-gold border border-gold/20">
              <svg class="w-2.5 h-2.5 text-gold" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                <circle cx="6" cy="6" r="3"/>
                <circle cx="6" cy="18" r="3"/>
                <line x1="20" y1="4" x2="8.12" y2="15.88"/>
                <line x1="14.47" y1="14.48" x2="20" y2="20"/>
              </svg>
              <span>${escapeHtml(b.role || 'Барбер')}</span>
            </div>
          </div>
        </div>
        <div>
          ${shiftBadge}
        </div>
      </div>

      <div class="pt-2 border-t border-surfaceBorder/40 flex items-center justify-between">
        <div class="flex items-center gap-1 flex-wrap">
          ${daysBadges}
        </div>
        <button onclick="openBarberModal(${b.id})" class="p-1.5 rounded-lg bg-surfaceBorder/40 hover:bg-surfaceBorder text-gray-300 hover:text-white transition active:scale-95" title="Настройки мастера">
          <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <circle cx="12" cy="12" r="3"/>
            <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/>
          </svg>
        </button>
      </div>
    `;

    list.appendChild(card);
  });
}

async function toggleShift(barberId) {
  try {
    const res = await fetch(`/api/barbers/${barberId}/toggle-shift`, { method: 'POST' });
    const data = await res.json();
    if (data.ok) {
      if (tg && tg.HapticFeedback) tg.HapticFeedback.impactOccurred('medium');
      fetchBarbers();
    } else {
      alert(data.error || 'Ошибка смены статуса');
    }
  } catch (e) {
    alert('Ошибка связи с сервером');
  }
}

function toggleWorkDay(btn) {
  btn.classList.toggle('selected');
  if (btn.classList.contains('selected')) {
    btn.classList.remove('border-surfaceBorder', 'text-gray-400');
    btn.classList.add('bg-accent', 'text-white', 'border-accent');
  } else {
    btn.classList.remove('bg-accent', 'text-white', 'border-accent');
    btn.classList.add('border-surfaceBorder', 'text-gray-400');
  }
}

function openBarberModal(barberId = null) {
  const modal = document.getElementById('barber-modal');
  const title = document.getElementById('barber-modal-title');
  const delBtn = document.getElementById('brb-delete-btn');

  document.getElementById('brb-id').value = barberId || '';

  const dayBtns = document.querySelectorAll('#brb-days-grid .day-btn');
  dayBtns.forEach(btn => {
    btn.classList.remove('selected', 'bg-accent', 'text-white', 'border-accent');
    btn.classList.add('border-surfaceBorder', 'text-gray-400');
  });

  if (barberId) {
    const b = allBarbers.find(item => item.id === barberId);
    if (b) {
      title.textContent = 'Редактировать мастера';
      document.getElementById('brb-name').value = b.name;
      document.getElementById('brb-role').value = b.role || '';
      document.getElementById('brb-phone').value = b.phone || '';
      document.getElementById('brb-tg-id').value = b.telegram_id || '';
      delBtn.classList.remove('hidden');

      const workDays = (b.work_days || '0,1,2,3,4,5,6').split(',').map(d => d.trim());
      dayBtns.forEach(btn => {
        if (workDays.includes(btn.dataset.day)) {
          toggleWorkDay(btn);
        }
      });
    }
  } else {
    title.textContent = 'Новый мастер';
    document.getElementById('brb-name').value = '';
    document.getElementById('brb-role').value = 'Топ-барбер';
    document.getElementById('brb-phone').value = '';
    document.getElementById('brb-tg-id').value = '';
    delBtn.classList.add('hidden');

    // Все дни по умолчанию активны
    dayBtns.forEach(btn => toggleWorkDay(btn));
  }

  modal.classList.remove('hidden');
}

function closeBarberModal() {
  document.getElementById('barber-modal').classList.add('hidden');
}

async function saveBarber() {
  const bId = document.getElementById('brb-id').value;
  const name = document.getElementById('brb-name').value.trim();
  const role = document.getElementById('brb-role').value.trim();
  const phone = document.getElementById('brb-phone').value.trim();
  const tgId = document.getElementById('brb-tg-id').value.trim();

  if (!name) {
    alert('Пожалуйста, введите имя барбера');
    return;
  }

  const selectedDays = [];
  document.querySelectorAll('#brb-days-grid .day-btn.selected').forEach(btn => {
    selectedDays.push(btn.dataset.day);
  });
  const workDaysStr = selectedDays.sort().join(',');

  const btn = document.getElementById('brb-save-btn');
  btn.disabled = true;
  btn.textContent = 'Сохранение...';

  const payload = {
    name,
    role: role || 'Барбер',
    phone: phone || null,
    telegram_id: tgId ? parseInt(tgId) : null,
    work_days: workDaysStr || '0,1,2,3,4,5,6',
    is_on_shift: 1
  };

  const url = bId ? `/api/barbers/${bId}/update` : '/api/barbers';

  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (data.ok) {
      closeBarberModal();
      fetchBarbers();
      if (tg && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred('success');
    } else {
      alert(data.error || 'Ошибка при сохранении мастера');
    }
  } catch (e) {
    alert('Ошибка связи с сервером');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Сохранить';
  }
}

async function deleteBarber() {
  const bId = document.getElementById('brb-id').value;
  if (!bId) return;
  if (!confirm('Вы точно хотите удалить этого мастера?')) return;

  try {
    const res = await fetch(`/api/barbers/${bId}/delete`, { method: 'POST' });
    const data = await res.json();
    if (data.ok) {
      closeBarberModal();
      fetchBarbers();
    } else {
      alert(data.error || 'Ошибка при удалении');
    }
  } catch (e) {
    alert('Ошибка при удалении');
  }
}

function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

// Первый запуск
fetchBookings();
