const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
if (tg) {
  tg.ready();
  tg.expand();
}

let barberData = null;
let currentSchedule = [];
let currentView = 'today';

window.addEventListener('DOMContentLoaded', () => {
  fetchSchedule();
  setupEventListeners();
});

function setupEventListeners() {
  const btn = document.getElementById('btn-manual-booking');
  if (btn) {
    btn.addEventListener('click', (e) => {
      if (e) e.preventDefault();
      openManualBookingModal();
    });
  }
}

async function fetchSchedule() {
  const loader = document.getElementById('loader');
  const list = document.getElementById('bookings-list');
  const emptyState = document.getElementById('empty-state');
  loader.classList.remove('hidden');
  list.classList.add('hidden');
  emptyState.classList.add('hidden');

  let tgId = null;
  if (tg && tg.initDataUnsafe && tg.initDataUnsafe.user) {
    tgId = tg.initDataUnsafe.user.id;
  }

  // Для тестирования в браузере поддерживаем параметр ?telegram_id=...
  const urlParams = new URLSearchParams(window.location.search);
  if (urlParams.get('telegram_id')) {
    tgId = parseInt(urlParams.get('telegram_id'));
  }

  if (!tgId) {
    loader.classList.add('hidden');
    document.getElementById('barber-container').classList.add('hidden');
    document.getElementById('unauthorized-screen').classList.remove('hidden');
    return;
  }

  try {
    const headers = {};
    if (tg && tg.initData) {
      headers['X-Telegram-Init-Data'] = tg.initData;
    }
    const res = await fetch(`/api/barber/my-schedule?telegram_id=${tgId}`, { headers });
    const data = await res.json();


    if (!res.ok || !data.barber) {
      document.getElementById('barber-container').classList.add('hidden');
      document.getElementById('unauthorized-screen').classList.remove('hidden');
      return;
    }

    document.getElementById('unauthorized-screen').classList.add('hidden');
    document.getElementById('barber-container').classList.remove('hidden');

    barberData = data.barber;
    currentSchedule = data.schedule || data.bookings || [];

    renderProfile();
    renderBookings();
  } catch (e) {
    console.error("Ошибка загрузки расписания мастера:", e);
  } finally {
    loader.classList.add('hidden');
  }
}

function renderProfile() {
  if (!barberData) return;
  document.getElementById('m-name').textContent = barberData.name;
  document.getElementById('m-role').textContent = barberData.role || 'Барбер';
  document.getElementById('m-phone').textContent = barberData.phone || '';
  const initials = (barberData.name || 'BB').trim().slice(0, 2).toUpperCase();
  const badge = document.getElementById('m-profile-badge');
  if (badge) badge.textContent = initials;

  const isOnShift = (barberData.is_on_shift !== undefined) 
    ? Boolean(barberData.is_on_shift) 
    : Boolean(barberData.is_working_today);
  updateShiftBtn(isOnShift);
}

function updateShiftBtn(isOnShift) {
  const btn = document.getElementById('shift-toggle-btn');
  const text = document.getElementById('shift-btn-text');

  if (isOnShift) {
    btn.className = 'w-full py-3 px-4 rounded-xl text-xs font-bold transition flex items-center justify-center gap-2 bg-emerald-500/15 text-emerald-400 border border-emerald-500/30 hover:bg-emerald-500/25 active:scale-95';
    btn.innerHTML = `
      <span class="w-2.5 h-2.5 rounded-full bg-emerald-400 pulse-dot"></span>
      <span>На смене (принимаю клиентов)</span>
    `;
  } else {
    btn.className = 'w-full py-3 px-4 rounded-xl text-xs font-bold transition flex items-center justify-center gap-2 bg-gray-500/15 text-gray-400 border border-gray-500/30 hover:bg-gray-500/25 active:scale-95';
    btn.innerHTML = `
      <span class="w-2.5 h-2.5 rounded-full bg-gray-500"></span>
      <span>Выходной (запись закрыта)</span>
    `;
  }
}

async function toggleMyShift() {
  if (!barberData) return;
  try {
    const res = await fetch(`/api/barbers/${barberData.id}/toggle-shift`, { method: 'POST' });
    const data = await res.json();
    if (data.ok) {
      barberData.is_on_shift = data.is_on_shift;
      updateShiftBtn(barberData.is_on_shift);
      if (tg && tg.HapticFeedback) tg.HapticFeedback.impactOccurred('medium');
    } else {
      alert(data.error || 'Ошибка смены смены');
    }
  } catch (e) {
    alert('Ошибка связи с сервером');
  }
}

function switchView(view) {
  currentView = view;
  const tabToday = document.getElementById('tab-today');
  const tabUpcoming = document.getElementById('tab-upcoming');

  if (view === 'today') {
    tabToday.className = 'flex-1 py-1.5 px-3 rounded-lg text-center transition bg-accent text-white font-semibold shadow-sm shadow-accent/20';
    tabUpcoming.className = 'flex-1 py-1.5 px-3 rounded-lg text-center transition text-gray-400 hover:text-white';
  } else {
    tabUpcoming.className = 'flex-1 py-1.5 px-3 rounded-lg text-center transition bg-accent text-white font-semibold shadow-sm shadow-accent/20';
    tabToday.className = 'flex-1 py-1.5 px-3 rounded-lg text-center transition text-gray-400 hover:text-white';
  }

  renderBookings();
}

function renderBookings() {
  const list = document.getElementById('bookings-list');
  const emptyState = document.getElementById('empty-state');
  list.innerHTML = '';

  const now = new Date();
  const todayStr = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;

  const todayBookings = currentSchedule.filter(b => {
    const t = (b.booking_time || '').toLowerCase();
    return t.includes('сегодня') || t.includes(todayStr) || t.includes('bugun');
  });

  document.getElementById('today-count').textContent = todayBookings.length;
  document.getElementById('upcoming-count').textContent = currentSchedule.length;

  // Если на сегодня записей нет, но есть записи на другие дни — автоматически переключаем на вкладку "Все записи"
  if (todayBookings.length === 0 && currentSchedule.length > 0 && currentView === 'today') {
    currentView = 'upcoming';
    const tabToday = document.getElementById('tab-today');
    const tabUpcoming = document.getElementById('tab-upcoming');
    if (tabToday && tabUpcoming) {
      tabUpcoming.className = 'flex-1 py-1.5 px-3 rounded-lg text-center transition bg-accent text-white font-semibold shadow-sm shadow-accent/20';
      tabToday.className = 'flex-1 py-1.5 px-3 rounded-lg text-center transition text-gray-400 hover:text-white';
    }
  }

  const filtered = currentView === 'today' ? todayBookings : currentSchedule;

  if (filtered.length === 0) {
    emptyState.classList.remove('hidden');
    list.classList.add('hidden');
    return;
  }

  emptyState.classList.add('hidden');
  list.classList.remove('hidden');

  filtered.forEach(b => {
    const card = document.createElement('div');
    card.className = 'bg-surface border border-surfaceBorder rounded-2xl p-4 shadow-sm';

    let statusBadge = '<span class="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-amber-500/10 text-amber-400 border border-amber-500/30">Новая</span>';
    if (b.status === 'confirmed') {
      statusBadge = '<span class="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/30">Подтверждена</span>';
    } else if (b.status === 'completed') {
      statusBadge = '<span class="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-gray-500/10 text-gray-400 border border-gray-500/30">Завершена</span>';
    } else if (b.status === 'cancelled') {
      statusBadge = '<span class="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-rose-500/10 text-rose-400 border border-rose-500/30">Отменена</span>';
    }

    const isManual = Boolean(b.details && b.details.includes('Ручная запись'));
    const manualBadge = isManual ? '<span class="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-sky-500/15 text-sky-400 border border-sky-500/30">📞 Ручная запись</span>' : '';

    const phone = b.phone ? b.phone.trim() : null;

    let actionButtonsHtml = '';
    if (b.status === 'new') {
      actionButtonsHtml = `
        <div class="grid grid-cols-2 gap-2 mt-2 pt-2 border-t border-surfaceBorder/40">
          <button onclick="changeBookingStatus(${b.id}, 'confirmed')" class="py-2 px-3 rounded-xl bg-emerald-500/15 hover:bg-emerald-500/25 text-emerald-400 border border-emerald-500/30 text-xs font-bold transition active:scale-95 flex items-center justify-center gap-1.5 shadow-sm">
            <span>✅ Подтвердить</span>
          </button>
          <button onclick="changeBookingStatus(${b.id}, 'cancelled')" class="py-2 px-3 rounded-xl bg-rose-500/10 hover:bg-rose-500/20 text-rose-400 border border-rose-500/30 text-xs font-semibold transition active:scale-95 flex items-center justify-center gap-1.5 shadow-sm">
            <span>❌ Отменить</span>
          </button>
        </div>
      `;
    } else if (b.status === 'confirmed') {
      actionButtonsHtml = `
        <div class="grid grid-cols-2 gap-2 mt-2 pt-2 border-t border-surfaceBorder/40">
          <button onclick="changeBookingStatus(${b.id}, 'completed')" class="py-2 px-3 rounded-xl bg-accent/15 hover:bg-accent/25 text-accent border border-accent/40 text-xs font-bold transition active:scale-95 flex items-center justify-center gap-1.5 shadow-sm">
            <span>🏁 Завершить</span>
          </button>
          <button onclick="changeBookingStatus(${b.id}, 'cancelled')" class="py-2 px-3 rounded-xl bg-rose-500/10 hover:bg-rose-500/20 text-rose-400 border border-rose-500/30 text-xs font-semibold transition active:scale-95 flex items-center justify-center gap-1.5 shadow-sm">
            <span>❌ Отменить</span>
          </button>
        </div>
      `;
    }

    card.innerHTML = `
      <div class="flex items-start justify-between mb-2">
        <div>
          <div class="font-bold text-white text-sm">${escapeHtml(b.full_name || 'Гость')}</div>
          <div class="text-xs text-accent font-medium mt-0.5">${escapeHtml(b.service || 'Стрижка')}</div>
        </div>
        <div class="flex flex-col items-end gap-1">
          ${statusBadge}
          ${manualBadge}
        </div>
      </div>

      <div class="bg-bgDark/60 rounded-xl p-2.5 my-2.5 border border-surfaceBorder/40 space-y-1.5 text-xs">
        <div class="flex items-center text-gray-300 gap-2">
          <svg class="w-3.5 h-3.5 text-gray-500 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>
          </svg>
          <span class="text-gray-400 w-14 shrink-0">Время:</span>
          <span class="font-bold text-white">${escapeHtml(b.booking_time || '—')}</span>
        </div>
        ${phone ? `
          <div class="flex items-center text-gray-300 gap-2">
            <svg class="w-3.5 h-3.5 text-gray-500 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"/>
            </svg>
            <span class="text-gray-400 w-14 shrink-0">Телефон:</span>
            <span class="font-medium text-white">${escapeHtml(phone)}</span>
          </div>
        ` : ''}
        ${b.details && !b.details.startsWith('Онлайн-запись') ? `
          <div class="flex items-center text-gray-300 gap-2">
            <svg class="w-3.5 h-3.5 text-gray-500 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/>
            </svg>
            <span class="text-gray-400 w-14 shrink-0">Инфо:</span>
            <span class="text-gray-300 truncate">${escapeHtml(b.details)}</span>
          </div>
        ` : ''}
      </div>

      ${phone ? `
        <div class="pt-1 border-t border-surfaceBorder/40 mb-1">
          <a href="tel:${phone}" class="inline-flex items-center justify-center gap-1.5 w-full py-1.5 px-3 rounded-xl bg-surface hover:bg-surfaceBorder text-xs font-medium text-gray-200 border border-surfaceBorder transition active:scale-95">
            <svg class="w-3.5 h-3.5 text-accent" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"/>
            </svg>
            <span>Позвонить клиенту</span>
          </a>
        </div>
      ` : ''}
      ${actionButtonsHtml}
    `;

    list.appendChild(card);
  });
}

async function changeBookingStatus(bookingId, newStatus) {
  if (newStatus === 'cancelled' && !confirm('Вы уверены, что хотите отменить эту запись? Клиент получит уведомление.')) {
    return;
  }

  try {
    const res = await fetch(`/api/bookings/${bookingId}/status`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: newStatus })
    });
    const data = await res.json();
    if (data.ok) {
      const found = currentSchedule.find(item => item.id === bookingId);
      if (found) {
        found.status = newStatus;
      }
      renderBookings();
      if (tg && tg.HapticFeedback) {
        tg.HapticFeedback.notificationOccurred(newStatus === 'cancelled' ? 'warning' : 'success');
      }
    } else {
      alert(data.error || 'Ошибка смены статуса');
    }
  } catch (e) {
    console.error('Ошибка изменения статуса:', e);
    alert('Ошибка связи с сервером');
  }
}

// =============================================================================
// РУЧНАЯ ЗАПИСЬ КЛИЕНТА МАСТЕРОМ
// =============================================================================

let allServices = [];

async function loadServicesForBarber() {
  if (allServices.length > 0) {
    populateServicesDropdown();
    return;
  }
  try {
    const res = await fetch('/api/services');
    if (res.ok) {
      allServices = await res.json();
      populateServicesDropdown();
    }
  } catch (e) {
    console.error('Ошибка загрузки услуг:', e);
  }
}

function populateServicesDropdown() {
  const select = document.getElementById('mb-service');
  if (!select) return;
  select.innerHTML = '<option value="">Выберите услугу...</option>';
  allServices.forEach(s => {
    const opt = document.createElement('option');
    opt.value = s.id;
    opt.textContent = `${s.name} (${Number(s.price).toLocaleString('ru-RU')} сум • ${s.duration_min || 45} мин)`;
    opt.dataset.name = s.name;
    select.appendChild(opt);
  });
}

function openManualBookingModal() {
  const modal = document.getElementById('manual-booking-modal');
  if (!modal) {
    console.warn('manual-booking-modal not found');
    return;
  }
  const form = document.getElementById('manual-booking-form');
  if (form) form.reset();

  const errEl = document.getElementById('mb-error');
  if (errEl) errEl.classList.add('hidden');

  // Установка сегодняшней даты по умолчанию (Asia/Tashkent)
  const now = new Date();
  const todayStr = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
  const dateInput = document.getElementById('mb-date');
  if (dateInput) dateInput.value = todayStr;

  // Округление времени до ближайших 30 минут
  let nextHour = now.getHours();
  let nextMin = now.getMinutes() < 30 ? 30 : 0;
  if (nextMin === 0) nextHour += 1;
  if (nextHour < 10) nextHour = 10;
  if (nextHour > 20) nextHour = 20;
  const timeInput = document.getElementById('mb-time');
  if (timeInput) timeInput.value = `${String(nextHour).padStart(2, '0')}:${String(nextMin).padStart(2, '0')}`;

  loadServicesForBarber();
  modal.classList.remove('hidden');
  modal.style.display = 'flex';
  if (tg && tg.HapticFeedback && typeof tg.HapticFeedback.impactOccurred === 'function') {
    try {
      tg.HapticFeedback.impactOccurred('light');
    } catch (_) {}
  }
}

function closeManualBookingModal() {
  const modal = document.getElementById('manual-booking-modal');
  if (modal) {
    modal.classList.add('hidden');
    modal.style.display = 'none';
  }
}

async function submitManualBooking(e) {
  e.preventDefault();
  if (!barberData) {
    alert('Мастер не идентифицирован');
    return;
  }

  const errEl = document.getElementById('mb-error');
  const btn = document.getElementById('mb-submit-btn');
  errEl.classList.add('hidden');

  const clientName = document.getElementById('mb-name').value.trim();
  const clientPhone = document.getElementById('mb-phone').value.trim();
  const serviceSelect = document.getElementById('mb-service');
  const serviceId = serviceSelect.value;
  const selectedOption = serviceSelect.options[serviceSelect.selectedIndex];
  const serviceName = selectedOption ? selectedOption.dataset.name : '';
  const bookingDate = document.getElementById('mb-date').value;
  const bookingTime = document.getElementById('mb-time').value;
  const notes = document.getElementById('mb-notes').value.trim();

  if (!clientName || !bookingDate || !bookingTime || !serviceId) {
    errEl.textContent = 'Пожалуйста, заполните все обязательные поля';
    errEl.classList.remove('hidden');
    return;
  }

  btn.disabled = true;
  btn.textContent = 'Сохранение...';

  try {
    const res = await fetch('/api/barber/manual-booking', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        barber_id: barberData.id,
        client_name: clientName,
        client_phone: clientPhone,
        service_id: serviceId,
        service_name: serviceName,
        booking_date: bookingDate,
        booking_time: bookingTime,
        notes: notes,
        branch_id: barberData.branch_id || 1
      })
    });

    const data = await res.json();
    if (res.ok && data.ok) {
      closeManualBookingModal();
      if (tg && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred('success');
      // Обновляем график мастера
      await fetchSchedule();
    } else {
      errEl.textContent = data.message || data.error || 'Ошибка при сохранении';
      errEl.classList.remove('hidden');
      if (tg && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred('error');
    }
  } catch (err) {
    console.error('Ошибка записи:', err);
    errEl.textContent = 'Ошибка связи с сервером. Попробуйте еще раз.';
    errEl.classList.remove('hidden');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Записать';
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

// Явный экспорт глобальных функций для надежности на любых браузерах и ПК
window.openManualBookingModal = openManualBookingModal;
window.closeManualBookingModal = closeManualBookingModal;
window.submitManualBooking = submitManualBooking;
window.toggleMyShift = toggleMyShift;
window.switchView = switchView;
window.fetchSchedule = fetchSchedule;
window.changeBookingStatus = changeBookingStatus;
