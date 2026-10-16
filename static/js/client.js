const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
if (tg) {
  tg.ready();
  tg.expand();
}

let selectedBranchId = 1;
let selectedBranchName = "Миллий бог";
let selectedBranchAddress = "г. Ташкент, метро «Миллий бог», 8B";

function selectBranch(id, name, address, btnEl) {
  selectedBranchId = id;
  selectedBranchName = name;
  selectedBranchAddress = address;

  document.querySelectorAll('.branch-pill').forEach(btn => {
    btn.className = 'branch-pill text-left p-2.5 rounded-xl border transition-all duration-150 border-surfaceBorder bg-bgDark/60 text-gray-400 hover:border-gray-500 hover:text-gray-200 active:scale-95';
    const dot = btn.querySelector('.rounded-full');
    if (dot) dot.className = 'w-2 h-2 rounded-full bg-emerald-400';
  });
  if (btnEl) {
    btnEl.className = 'branch-pill text-left p-2.5 rounded-xl border transition-all duration-150 border-accent bg-accent/15 text-white shadow-sm shadow-accent/20 active:scale-95';
    const dot = btnEl.querySelector('.rounded-full');
    if (dot) dot.className = 'w-2 h-2 rounded-full bg-emerald-400 pulse-dot';
  }

  const badge = document.getElementById('header-branch-name-badge');
  if (badge) badge.textContent = name;

  const headerSub = document.getElementById('header-branch-address');
  if (headerSub) headerSub.textContent = `Мужской салон • ${name}`;

  if (tg && tg.HapticFeedback) {
    tg.HapticFeedback.selectionChanged();
  }
}

let selectedService = "Стрижка волос";
let selectedServiceId = null;
let selectedDuration = 60;
let selectedBarberId = null;
let selectedBarberName = "Любой свободный мастер";
let selectedDate = "";
let selectedSlot = "";

let allServices = [];
let allBarbers = [];

const daysRu = ["Вс", "Пн", "Вт", "Ср", "Чт", "Пт", "Сб"];
const monthsRuShort = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"];

window.addEventListener('DOMContentLoaded', async () => {
  if (tg && tg.initDataUnsafe && tg.initDataUnsafe.user) {
    const u = tg.initDataUnsafe.user;
    const nameInput = document.getElementById('cust-name');
    if (nameInput && !nameInput.value) {
      nameInput.value = `${u.first_name || ''} ${u.last_name || ''}`.trim();
    }

    try {
      const roleRes = await fetch(`/api/user-role?user_id=${u.id}`);
      const roleData = await roleRes.json();
      if (roleData.role === 'barber' && roleData.barber) {
        const bBanner = document.getElementById('barber-role-banner');
        if (bBanner) {
          bBanner.classList.remove('hidden');
          document.getElementById('barber-banner-name').textContent = roleData.barber.name;
        }
      }
    } catch (e) {}
  }

  await loadServices();
  await loadBarbers();
  initDates();
  loadMyBooking();
});

let currentCategory = 'hair';

function getServiceCategory(service) {
  const name = (service.name || "").toLowerCase();
  if (name.includes("маск") || name.includes("воск") || name.includes("wax") || name.includes("массаж") || (name.includes("стайлинг") && !name.includes("стрижка"))) {
    return "spa";
  }
  if (name.includes("бород") && !name.includes("комплекс")) {
    return "beard";
  }
  if (name.includes("ус") || name.includes("брить") || name.includes("бритв") || name.includes("камуфляж")) {
    return "beard";
  }
  return "hair"; // Стрижка мужская, Комплекс, Стрижка детская, Премиум Стрижка Mr. Akmal
}

function filterServices(cat, buttonEl) {
  currentCategory = cat;
  document.querySelectorAll('.cat-pill').forEach(btn => {
    btn.className = 'cat-pill flex-shrink-0 whitespace-nowrap px-3.5 py-2 rounded-xl text-xs font-medium transition-all border bg-surface text-gray-300 border-surfaceBorder hover:border-gray-500';
  });
  if (buttonEl) {
    buttonEl.className = 'cat-pill flex-shrink-0 whitespace-nowrap px-3.5 py-2 rounded-xl text-xs font-semibold transition-all border bg-accent text-white border-accent shadow-sm shadow-accent/20';
  }
  renderServicesList();
}

function renderServicesList() {
  const group = document.getElementById('services-group');
  if (!allServices || allServices.length === 0) {
    group.innerHTML = '<div class="text-xs text-gray-500 py-3 text-center">Каталог услуг обновляется</div>';
    return;
  }

  const filtered = currentCategory === 'all' 
    ? allServices 
    : allServices.filter(s => getServiceCategory(s) === currentCategory);

  if (filtered.length === 0) {
    group.innerHTML = '<div class="text-xs text-gray-400 py-4 text-center">В этой категории пока нет услуг</div>';
    return;
  }

  group.innerHTML = '';
  filtered.forEach((s) => {
    const isSelected = selectedServiceId ? (selectedServiceId === s.id) : (selectedService === s.name);

    const card = document.createElement('div');
    card.onclick = () => selectServiceItem(s, card);
    card.className = `service-card cursor-pointer bg-surface rounded-xl p-3 transition-all duration-150 ${
      isSelected 
        ? 'border-2 border-accent/90 shadow-sm shadow-accent/15 bg-accent/5' 
        : 'border border-surfaceBorder hover:border-gray-500'
    }`;

    const badgeHtml = s.badge 
      ? `<span class="text-[9px] font-semibold bg-accent/15 text-accent border border-accent/40 px-2 py-0.5 rounded-full tracking-wider uppercase">${escapeHtml(s.badge)}</span>` 
      : '';

    card.innerHTML = `
      <div class="flex items-center justify-between gap-2">
        <div class="font-semibold text-xs text-white flex items-center gap-2">
          <svg class="w-3.5 h-3.5 text-accent shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
            <path d="m2 4 3 12h14l3-12-6 7-4-7-4 7-6-7zm3 16h14"/>
          </svg>
          <span class="leading-tight">${escapeHtml(s.name)}</span>
        </div>
        ${badgeHtml}
      </div>
      ${s.description ? `<div class="text-[11px] text-gray-400 mt-1 pl-5.5 leading-snug">${escapeHtml(s.description)}</div>` : ''}
      <div class="flex items-center justify-between text-xs mt-2 pl-5.5 pt-1.5 border-t border-surfaceBorder/40">
        <div class="flex items-center gap-2">
          <span class="text-accent font-bold text-xs">${s.price.toLocaleString()} сум</span>
          <span class="text-gray-500">•</span>
          <span class="text-gray-400 text-[11px]">${s.duration || 60} мин</span>
        </div>
        <span class="text-[10px] text-gray-400 ${isSelected ? 'text-accent font-semibold' : ''}">
          ${isSelected ? '✓ Выбрано' : 'Выбрать'}
        </span>
      </div>
    `;
    group.appendChild(card);
  });
}

// 1. Динамическая загрузка услуг
async function loadServices() {
  try {
    const res = await fetch('/api/services');
    const services = await res.json();
    allServices = services.filter(s => s.is_active);

    const pool = allServices.filter(s => getServiceCategory(s) === currentCategory);
    const defaultPool = pool.length > 0 ? pool : allServices;
    if (defaultPool.length > 0 && !selectedServiceId) {
      selectedService = defaultPool[0].name;
      selectedServiceId = defaultPool[0].id;
      selectedDuration = defaultPool[0].duration || 60;
    }

    renderServicesList();
  } catch (e) {
    console.error("Ошибка загрузки услуг:", e);
  }
}

function selectServiceItem(service, element) {
  selectedService = service.name;
  selectedServiceId = service.id;
  selectedDuration = service.duration || 60;

  renderServicesList();

  if (selectedDate) {
    loadSlots(selectedDate);
  }
}

// 2. Динамическая загрузка мастеров
async function loadBarbers() {
  const container = document.getElementById('barbers-list-container');
  try {
    const res = await fetch('/api/barbers');
    allBarbers = await res.json();

    container.innerHTML = '';
    allBarbers.forEach(b => {
      const card = document.createElement('div');
      card.onclick = () => selectBarber(b.id, b.name, card);
      card.className = 'barber-card flex-shrink-0 w-[136px] cursor-pointer bg-surface border border-surfaceBorder rounded-2xl p-3 flex flex-col justify-between text-center transition-all duration-150 hover:border-gray-500 active:scale-95';

      const initials = (b.name || 'М').trim().slice(0, 2).toUpperCase();
      const isShiftOn = (b.is_on_shift !== undefined) ? Boolean(b.is_on_shift) : Boolean(b.is_working_today);
      const shiftBadge = isShiftOn 
        ? '<span class="text-[9px] font-semibold text-emerald-400 bg-emerald-500/10 border border-emerald-500/30 px-2 py-0.5 rounded-full inline-flex items-center gap-1"><span class="w-1.5 h-1.5 rounded-full bg-emerald-400 pulse-dot"></span>На смене</span>'
        : '<span class="text-[9px] font-medium text-gray-400 bg-surfaceBorder/40 px-2 py-0.5 rounded-full inline-block">Выходной</span>';

      const roleText = b.role_title || b.role || 'Барбер';
      const isVip = roleText.toLowerCase().includes('шеф') || roleText.toLowerCase().includes('директор');
      const roleBadge = isVip
        ? `<span class="text-[9px] text-gold font-bold px-1.5 py-0.5 rounded bg-gold/10 border border-gold/30">Шеф-мастер</span>`
        : `<span class="text-[10px] text-gray-400 font-medium">Стилист</span>`;

      card.innerHTML = `
        <div class="flex flex-col items-center">
          <div class="w-11 h-11 rounded-2xl bg-gold/10 border border-gold/25 text-gold flex items-center justify-center font-bold text-xs tracking-wider shadow-sm">
            ${escapeHtml(initials)}
          </div>
          <div class="font-semibold text-xs text-white mt-2 leading-snug truncate w-full px-1" title="${escapeHtml(b.name)}">
            ${escapeHtml(b.name)}
          </div>
          <div class="mt-1 w-full flex justify-center">
            ${roleBadge}
          </div>
        </div>
        <div class="mt-2.5">
          ${shiftBadge}
        </div>
      `;
      container.appendChild(card);
    });
  } catch (e) {
    console.error("Ошибка загрузки мастеров:", e);
  }
}

function selectBarber(barberId, barberName, element) {
  selectedBarberId = barberId;
  selectedBarberName = barberName;

  document.querySelectorAll('.barber-card').forEach(c => {
    c.classList.remove('border-accent/80', 'border-accent/90', 'border-2', 'shadow-sm', 'shadow-accent/15', 'bg-accent/5');
    c.classList.add('border-surfaceBorder', 'border');
  });
  element.classList.remove('border-surfaceBorder', 'border');
  element.classList.add('border-accent/80', 'border-2', 'shadow-sm', 'shadow-accent/15');

  if (selectedDate) {
    loadSlots(selectedDate);
  }
}

// 3. Генерация дат на 7 дней вперед
function initDates() {
  const container = document.getElementById('dates-container');
  container.innerHTML = '';
  const now = new Date();

  for (let i = 0; i < 7; i++) {
    const d = new Date();
    d.setDate(now.getDate() + i);

    const iso = d.toISOString().slice(0, 10);
    const dayNum = d.getDate();
    const monthShort = monthsRuShort[d.getMonth()];
    const dayOfWeek = daysRu[d.getDay()];

    let bottomLabel = `${dayNum} ${monthShort}`;
    if (i === 0) bottomLabel = `Сегодня`;
    else if (i === 1) bottomLabel = `Завтра`;

    const pill = document.createElement('button');
    const isSelected = i === 0;
    pill.className = `flex-shrink-0 px-3 py-2 rounded-xl text-xs font-medium border transition-all text-center min-w-[70px] ${
      isSelected 
        ? 'bg-accent text-white border-accent shadow-sm shadow-accent/25' 
        : 'bg-surface text-gray-300 border-surfaceBorder hover:border-gray-500'
    }`;
    pill.dataset.date = iso;
    pill.innerHTML = `
      <span class="block text-[10px] uppercase font-semibold ${isSelected ? 'text-white/80' : 'text-gray-400'}">${dayOfWeek}</span>
      <span class="block text-xs font-semibold mt-0.5">${bottomLabel}</span>
    `;
    pill.onclick = () => selectDate(iso, pill);
    container.appendChild(pill);

    if (i === 0) {
      selectedDate = iso;
    }
  }

  loadSlots(selectedDate);
}

function selectDate(isoDate, element) {
  selectedDate = isoDate;
  selectedSlot = "";
  document.querySelectorAll('#dates-container button').forEach(b => {
    const d = new Date(b.dataset.date);
    const dayOfWeek = daysRu[d.getDay()];
    const dayNum = d.getDate();
    const monthShort = monthsRuShort[d.getMonth()];
    const todayIso = new Date().toISOString().slice(0, 10);
    const tmrw = new Date(); tmrw.setDate(tmrw.getDate() + 1);
    const tmrwIso = tmrw.toISOString().slice(0, 10);

    let bottomLabel = `${dayNum} ${monthShort}`;
    if (b.dataset.date === todayIso) bottomLabel = 'Сегодня';
    else if (b.dataset.date === tmrwIso) bottomLabel = 'Завтра';

    b.className = 'flex-shrink-0 px-3 py-2 rounded-xl text-xs font-medium border transition-all text-center min-w-[70px] bg-surface text-gray-300 border-surfaceBorder hover:border-gray-500';
    b.innerHTML = `
      <span class="block text-[10px] uppercase font-semibold text-gray-400">${dayOfWeek}</span>
      <span class="block text-xs font-semibold mt-0.5">${bottomLabel}</span>
    `;
  });

  const d = new Date(isoDate);
  const dayOfWeek = daysRu[d.getDay()];
  const dayNum = d.getDate();
  const monthShort = monthsRuShort[d.getMonth()];
  const todayIso = new Date().toISOString().slice(0, 10);
  const tmrw = new Date(); tmrw.setDate(tmrw.getDate() + 1);
  const tmrwIso = tmrw.toISOString().slice(0, 10);

  let bottomLabel = `${dayNum} ${monthShort}`;
  if (isoDate === todayIso) bottomLabel = 'Сегодня';
  else if (isoDate === tmrwIso) bottomLabel = 'Завтра';

  element.className = 'flex-shrink-0 px-3 py-2 rounded-xl text-xs font-medium border transition-all text-center min-w-[70px] bg-accent text-white border-accent shadow-sm shadow-accent/25';
  element.innerHTML = `
    <span class="block text-[10px] uppercase font-semibold text-white/80">${dayOfWeek}</span>
    <span class="block text-xs font-semibold mt-0.5">${bottomLabel}</span>
  `;

  loadSlots(selectedDate);
}

let currentSlotsRequestId = 0;
let slotsAbortController = null;

// 4. Загрузка свободных окон с сервера с учетом мастера и длительности
async function loadSlots(isoDate) {
  const requestId = ++currentSlotsRequestId;

  if (slotsAbortController) {
    try { slotsAbortController.abort(); } catch (e) {}
  }
  slotsAbortController = new AbortController();
  const signal = slotsAbortController.signal;

  const grid = document.getElementById('slots-grid');
  const loader = document.getElementById('slots-loader');
  loader.classList.remove('hidden');
  grid.innerHTML = '';

  try {
    let url = `/api/slots?date=${isoDate}&duration=${selectedDuration || 60}`;
    if (selectedBarberId) {
      url += `&barber_id=${selectedBarberId}`;
    }

    const res = await fetch(url, { signal });
    const data = await res.json();

    if (requestId !== currentSlotsRequestId || selectedDate !== isoDate) {
      return;
    }

    loader.classList.add('hidden');
    grid.innerHTML = '';

    if (!data.slots || data.slots.length === 0) {
      grid.innerHTML = '<div class="col-span-4 text-xs text-gray-500 py-3 text-center">Окон на этот день нет</div>';
      return;
    }

    data.slots.forEach(slot => {
      const btn = document.createElement('button');
      if (slot.is_available) {
        btn.className = 'slot-btn py-2 px-1 rounded-xl text-xs font-semibold bg-surface border border-surfaceBorder text-gray-200 hover:border-accent hover:text-white transition text-center';
        btn.textContent = slot.time;
        btn.onclick = () => selectSlot(slot.time, btn);
      } else {
        btn.className = 'py-2 px-1 rounded-xl text-[11px] font-medium bg-bgDark/40 border border-surfaceBorder/30 text-gray-600 cursor-not-allowed text-center';
        btn.innerHTML = `<span class="line-through decoration-rose-500/60">${slot.time}</span>`;
        btn.title = slot.reason || 'Занято';
        btn.disabled = true;
      }
      grid.appendChild(btn);
    });

  } catch (e) {
    if (e.name === 'AbortError') return;
    if (requestId === currentSlotsRequestId) {
      loader.classList.add('hidden');
      grid.innerHTML = '<div class="col-span-4 text-xs text-rose-400 py-3 text-center">Ошибка загрузки расписания</div>';
    }
  }
}

function selectSlot(time, element) {
  selectedSlot = time;
  document.querySelectorAll('.slot-btn').forEach(b => {
    b.className = 'slot-btn py-2 px-1 rounded-xl text-xs font-semibold bg-surface border border-surfaceBorder text-gray-200 hover:border-accent hover:text-white transition text-center';
  });
  element.className = 'slot-btn py-2 px-1 rounded-xl text-xs font-semibold bg-accent text-white border-accent shadow-sm shadow-accent/25 text-center';
}

// 5. Отправка записи на сервер
async function submitBooking() {
  const name = document.getElementById('cust-name').value.trim();
  const phone = document.getElementById('cust-phone').value.trim();

  if (!selectedService) {
    alert('Пожалуйста, выберите услугу');
    return;
  }
  if (!selectedDate || !selectedSlot) {
    alert('Пожалуйста, выберите день и свободное время');
    return;
  }
  if (!name) {
    alert('Пожалуйста, укажите ваше имя');
    document.getElementById('cust-name').focus();
    return;
  }

  const submitBtn = document.getElementById('submit-btn');
  submitBtn.disabled = true;
  submitBtn.innerHTML = `
    <svg class="animate-spin h-4 w-4 text-white" viewBox="0 0 24 24" fill="none">
      <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
      <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"></path>
    </svg>
    <span>Оформляем запись...</span>
  `;

  const bookingPayload = {
    full_name: name,
    phone: phone || null,
    service: selectedService,
    service_id: selectedServiceId,
    barber_id: selectedBarberId,
    branch_id: selectedBranchId,
    branch_name: selectedBranchName,
    booking_time: `${selectedDate} ${selectedSlot}`,
    user_id: tg && tg.initDataUnsafe && tg.initDataUnsafe.user ? tg.initDataUnsafe.user.id : null,
    username: tg && tg.initDataUnsafe && tg.initDataUnsafe.user ? `@${tg.initDataUnsafe.user.username || ''}` : null,
    details: `Онлайн-запись через Client TMA`
  };

  try {
    const res = await fetch('/api/bookings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(bookingPayload)
    });
    const result = await res.json();

    if (res.ok && result.ok) {
      document.getElementById('booking-flow').classList.add('hidden');
      const myBookingBanner = document.getElementById('my-booking-banner');
      if (myBookingBanner) myBookingBanner.classList.add('hidden');
      const barberBanner = document.getElementById('barber-role-banner');
      if (barberBanner) barberBanner.classList.add('hidden');

      const screen = document.getElementById('success-screen');
      screen.classList.remove('hidden');
      window.scrollTo({ top: 0, behavior: 'smooth' });

      document.getElementById('res-name').textContent = name;
      document.getElementById('res-service').textContent = selectedService;
      document.getElementById('res-barber').textContent = result.barber_name || selectedBarberName;
      document.getElementById('res-time').textContent = result.booking_time || `${selectedDate} в ${selectedSlot}`;
      document.getElementById('res-branch').textContent = selectedBranchName;
      document.getElementById('res-address').textContent = selectedBranchAddress;

      if (tg && tg.HapticFeedback) {
        tg.HapticFeedback.notificationOccurred('success');
      }
    } else {
      alert(result.error || 'Не удалось оформить запись. Попробуйте другое время.');
      submitBtn.disabled = false;
      submitBtn.innerHTML = `
        <span>Подтвердить запись</span>
        <svg class="w-4 h-4 text-white" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="9 18 15 12 9 6"/>
        </svg>
      `;
      loadSlots(selectedDate);
    }
  } catch (err) {
    alert('Ошибка соединения с сервером. Повторите попытку.');
    submitBtn.disabled = false;
    submitBtn.innerHTML = `
      <span>Подтвердить запись</span>
      <svg class="w-4 h-4 text-white" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="9 18 15 12 9 6"/>
      </svg>
    `;
  }
}

async function loadMyBooking() {
  const userId = tg && tg.initDataUnsafe && tg.initDataUnsafe.user ? tg.initDataUnsafe.user.id : null;
  if (!userId) return;

  try {
    const res = await fetch(`/api/my-booking?user_id=${userId}`);
    const data = await res.json();
    const banner = document.getElementById('my-booking-banner');

    if (data && data.booking) {
      banner.classList.remove('hidden');
      document.getElementById('my-booking-time').textContent = data.booking.booking_time;
      document.getElementById('my-booking-service').textContent = data.booking.service;

      if (data.booking.barber_name) {
        const bRow = document.getElementById('my-booking-barber-row');
        bRow.classList.remove('hidden');
        document.getElementById('my-booking-barber').textContent = data.booking.barber_name;
      }

      if (data.booking.branch_name) {
        const brRow = document.getElementById('my-booking-branch-row');
        if (brRow) brRow.classList.remove('hidden');
        document.getElementById('my-booking-branch').textContent = data.booking.branch_name;
      }

      const statusEl = document.getElementById('my-booking-status');
      if (data.booking.status === 'confirmed') {
        statusEl.className = 'px-2 py-0.5 rounded-full text-[10px] font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/30';
        statusEl.textContent = 'Подтверждена';
      } else {
        statusEl.className = 'px-2 py-0.5 rounded-full text-[10px] font-semibold bg-amber-500/10 text-amber-400 border border-amber-500/30';
        statusEl.textContent = 'Новая';
      }
      banner.dataset.bookingId = data.booking.id;
    } else {
      banner.classList.add('hidden');
    }
  } catch (e) {
    console.warn("Ошибка загрузки активной брони:", e);
  }
}

async function cancelMyBooking() {
  const userId = tg && tg.initDataUnsafe && tg.initDataUnsafe.user ? tg.initDataUnsafe.user.id : null;
  if (!userId) return;

  const banner = document.getElementById('my-booking-banner');
  const bookingId = banner.dataset.bookingId;
  if (!confirm("Вы уверены, что хотите отменить эту запись?")) return;

  const btn = document.getElementById('my-cancel-btn');
  btn.disabled = true;
  btn.textContent = 'Отменяем запись...';

  try {
    const res = await fetch('/api/my-booking/cancel', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_id: userId, booking_id: bookingId })
    });
    const data = await res.json();

    if (res.ok && data.ok) {
      banner.classList.add('hidden');
      const flow = document.getElementById('booking-flow');
      if (flow) flow.classList.remove('hidden');
      const screen = document.getElementById('success-screen');
      if (screen) screen.classList.add('hidden');
      alert('Ваша запись успешно отменена!');
      if (tg && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred('success');
      loadSlots(selectedDate);
    } else {
      alert(data.error || 'Не удалось отменить запись');
    }
  } catch (e) {
    alert('Ошибка соединения с сервером');
  } finally {
    btn.disabled = false;
    btn.innerHTML = `
      <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
      </svg>
      <span>Отменить эту запись</span>
    `;
  }
}

function bookAnother() {
  const screen = document.getElementById('success-screen');
  if (screen) screen.classList.add('hidden');
  const flow = document.getElementById('booking-flow');
  if (flow) flow.classList.remove('hidden');
  loadSlots(selectedDate);
  loadMyBooking();
  window.scrollTo({ top: 0, behavior: 'smooth' });
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

function closeApp() {
  if (tg) {
    tg.close();
  } else {
    window.location.reload();
  }
}
