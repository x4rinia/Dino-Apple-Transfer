/* 🦕 Dino-Apple-Transfer – Desktop-Oberfläche
 * Direkte USB-Kabelverbindung für iPhone & iPad (iOS/iPadOS)
 * 100% lokal per USB – Hauptarbeitsbereich: Dino-Transfer Ordner
 */
'use strict';

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

const S = {
  view: 'usb',
  cfg: {},
  usb: {
    state: null,
    device: null,
    deviceKey: null,
    apps: [],
    icons: {},
    dinoInfo: null,     // { bundle, appName, path, name }
    mode: 'dino',       // 'dino', 'root', 'app'
    app: null,          // Aktive App bzw. Container
    path: '/Documents/Dino-Transfer',
    entries: [],
    selected: new Set(),
    polling: false,
    loadingDir: false,
    consecutiveErrors: 0,
    appleStatus: { installed: false, running: false }
  },
  sidebarCollapsed: false,
  jobs: new Map(),
};

/* ================================================================ Helfer */
function api(name, ...args) {
  if (!window.pywebview || !window.pywebview.api || typeof window.pywebview.api[name] !== 'function') {
    return Promise.reject(new Error(`Backend-Funktion '${name}' ist noch nicht bereit`));
  }
  return window.pywebview.api[name](...args).then((r) => {
    if (!r || !r.ok) {
      const e = new Error((r && r.error) || 'Unbekannter Fehler');
      e.code = r && r.code;
      e.details = r && r.details;
      throw e;
    }
    return r.data;
  });
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') node.className = v;
    else if (k === 'text') node.textContent = v;
    else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
    else if (k === 'dataset') Object.assign(node.dataset, v);
    else node.setAttribute(k, v === true ? '' : v);
  }
  for (const c of children.flat()) {
    if (c != null && c !== false) {
      node.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
  }
  return node;
}

function fmtBytes(b) {
  if (!b) return '0 B';
  const u = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.min(Math.floor(Math.log(b) / Math.log(1024)), u.length - 1);
  return (b / 1024 ** i).toLocaleString('de-DE', { maximumFractionDigits: i ? 1 : 0 }) + ' ' + u[i];
}
const fmtDate = (ts) => (ts ? new Date(ts * 1000).toLocaleString('de-DE', { dateStyle: 'short', timeStyle: 'short' }) : '');

const TYPES = [
  { icon: '🎵', label: 'Musik', ext: ['mp3', 'm4a', 'aac', 'flac', 'wav', 'ogg', 'opus', 'alac', 'aiff', 'wma'] },
  { icon: '🎬', label: 'Video', ext: ['mp4', 'm4v', 'mov', 'mkv', 'avi', 'webm', 'wmv', 'flv', 'mpg', 'mpeg', 'ts'] },
  { icon: '📄', label: 'PDF', ext: ['pdf'] },
  { icon: '📚', label: 'E-Book', ext: ['epub', 'mobi', 'cbz', 'cbr', 'azw', 'azw3', 'kfx'] },
  { icon: '🖼️', label: 'Bild', ext: ['jpg', 'jpeg', 'png', 'gif', 'heic', 'webp', 'bmp', 'tif', 'tiff', 'svg'] },
  { icon: '📦', label: 'Archiv', ext: ['zip', '7z', 'rar', 'tar', 'gz', 'bz2', 'xz'] },
  { icon: '🎮', label: 'ROM', ext: ['nes', 'sfc', 'smc', 'gba', 'gb', 'gbc', 'nds', 'n64', 'z64', 'iso', 'cue', 'bin', 'gen', 'md', '3ds', 'cso', 'chd'] },
  { icon: '📝', label: 'Dokument', ext: ['txt', 'doc', 'docx', 'rtf', 'odt', 'pages', 'xls', 'xlsx', 'csv', 'numbers', 'ppt', 'pptx', 'key'] },
  { icon: '🔤', label: 'Untertitel', ext: ['srt', 'ass', 'ssa', 'vtt', 'sub'] },
];

function fileIcon(name, isDir) {
  if (isDir) return '📁';
  const ext = name.includes('.') ? name.split('.').pop().toLowerCase() : '';
  return (TYPES.find((t) => t.ext.includes(ext)) || { icon: '📄' }).icon;
}

function toast(text, kind = 'info', action = null, ms = 4200) {
  const container = $('#toasts');
  if (!container) return;
  // Duplicate Toast verhindern
  const existing = Array.from(container.children).find((t) => t.textContent.trim().startsWith(text.trim()));
  if (existing) {
    existing.remove();
  }
  // Höchstens 3 Toasts gleichzeitig anzeigen
  while (container.children.length >= 3) {
    container.firstElementChild.remove();
  }
  const node = el('div', { class: `toast toast--${kind}` }, el('span', { text }));
  if (action) {
    node.append(el('button', { class: 'btn btn--small', text: action.label, onclick: () => { action.fn(); node.remove(); } }));
  }
  container.append(node);
  setTimeout(() => { node.classList.add('out'); setTimeout(() => node.remove(), 260); }, ms);
}

function dialog({ title, text = '', okLabel = 'OK', danger = false, input = null }) {
  const dlg = $('#dialog');
  $('#dlgTitle').textContent = title;
  $('#dlgText').textContent = text;
  $('#dlgText').hidden = !text;

  const selWrap = $('#dlgSelectWrap');
  if (selWrap) selWrap.setAttribute('hidden', '');

  const inp = $('#dlgInput');
  if (input != null) {
    inp.removeAttribute('hidden');
    inp.value = input;
  } else {
    inp.setAttribute('hidden', '');
    inp.value = '';
  }

  const ok = $('#dlgOk');
  ok.textContent = okLabel;
  ok.className = 'btn ' + (danger ? 'btn--danger' : 'btn--primary');
  dlg.returnValue = '';
  dlg.showModal();
  if (input != null) { inp.focus(); inp.select(); }

  return new Promise((resolve) => {
    dlg.addEventListener('close', () => {
      const yes = dlg.returnValue === 'ok';
      resolve(input != null ? (yes ? inp.value.trim() : null) : yes);
    }, { once: true });
  });
}

$('#dlgInput')?.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') { e.preventDefault(); $('#dialog').close('ok'); }
});

function setPill(id, cls, text) {
  const p = $(id);
  p.classList.remove('is-ok', 'is-warn', 'is-bad');
  if (cls) p.classList.add(cls);
  p.querySelector('.pill__text').textContent = text;
}

/* ================================================================ Ansichten & Tabs */
function setView(view) {
  S.view = view;
  $$('.tab').forEach((t) => t.classList.toggle('is-active', t.dataset.view === view));
  $$('.view').forEach((v) => v.classList.toggle('is-active', v.id === `view-${view}`));
}
$$('.tab').forEach((t) => t.addEventListener('click', () => setView(t.dataset.view)));
$$('[data-goto]').forEach((b) => b.addEventListener('click', () => setView(b.dataset.goto)));
$$('[data-open]').forEach((b) => b.addEventListener('click', () => api('open_url', b.dataset.open).catch((e) => toast(e.message, 'error'))));

/* ================================================================ Seitenleiste Toggle */
function setSidebarCollapsed(collapsed) {
  S.sidebarCollapsed = collapsed;
  $('#usbReady').classList.toggle('sidebar-collapsed', collapsed);
  $('#btnExpandSidebar').hidden = !collapsed;
  try { localStorage.setItem('dino_sidebar_collapsed', collapsed ? '1' : '0'); } catch (_) {}
}
$('#btnCollapseSidebar')?.addEventListener('click', () => setSidebarCollapsed(true));
$('#btnExpandSidebar')?.addEventListener('click', () => setSidebarCollapsed(false));

/* ================================================================ USB Status & Erkennung */
let _appleCheckTime = 0;
async function checkAppleDevices(force = false) {
  const now = Date.now();
  if (!force && S.usb.appleStatus && (now - _appleCheckTime < 60000)) {
    return S.usb.appleStatus;
  }
  try {
    _appleCheckTime = now;
    S.usb.appleStatus = await api('apple_devices_status');
  } catch (_) {
    S.usb.appleStatus = { installed: false, running: false };
  }
  return S.usb.appleStatus;
}

async function pollUsb() {
  if (S.usb.polling || S.jobs.size > 0) return;
  S.usb.polling = true;
  try {
    const res = await api('usb_status');
    S.usb.consecutiveErrors = 0;
    await applyUsbStatus(res);
  } catch (e) {
    S.usb.consecutiveErrors++;
    if (S.usb.consecutiveErrors >= 2 || !S.usb.device) {
      await applyUsbStatus({ state: 'error', message: e.message, details: e.details });
    }
  } finally {
    S.usb.polling = false;
  }
}

async function applyUsbStatus(st) {
  const prev = S.usb.state;
  const prevMode = S.usb.device ? S.usb.device.connMode : null;

  if (st.state === 'ready') {
    S.usb.disconnectTicks = 0;
    S.usb.state = st.state;
  } else {
    // Wenn zuvor verbunden war und kein manueller Pause-Befehl vorliegt:
    // mindestens 2 aufeinanderfolgende Nicht-Ready-Ticks abwarten, um kurze Usbmuxd-Glitchs abzufangen
    if (prev === 'ready' && st.state !== 'paused') {
      S.usb.disconnectTicks = (S.usb.disconnectTicks || 0) + 1;
      if (S.usb.disconnectTicks < 2) {
        return;
      }
    }
    S.usb.disconnectTicks = 0;
    S.usb.state = st.state;
  }

  const allowWifi = st.allowWireless !== false;
  if ($('#chkAllowWireless')) $('#chkAllowWireless').checked = allowWifi;
  if ($('#chkAllowWirelessStart')) $('#chkAllowWirelessStart').checked = allowWifi;

  // Treiber-Status ("Apple Geräte") aktualisieren
  const isInstalled = (S.usb.appleStatus && S.usb.appleStatus.installed) || st.driverRunning;
  const drvBadge = $('#driverInstalledBadge');
  const btnStore = $('#btnStoreApple');
  if (drvBadge && btnStore) {
    if (isInstalled) {
      drvBadge.className = 'driver-badge is-ok';
      drvBadge.textContent = '✅ „Apple Geräte“ (Treiber) wurde bereits installiert';
      drvBadge.hidden = false;
      btnStore.hidden = true;
    } else {
      drvBadge.className = 'driver-badge is-warn';
      drvBadge.textContent = '⚠️ Treiber „Apple Geräte“ ist noch nicht installiert:';
      drvBadge.hidden = false;
      btnStore.hidden = false;
    }
  }

  if (st.state === 'ready') {
    const d = st.device;
    S.usb.device = d;
    if (d.connMode === 'wifi') {
      setPill('#pillUsb', 'is-wifi', `Apple-Funk: ${d.name}`);
    } else {
      setPill('#pillUsb', 'is-ok', `USB-Kabel: ${d.name}`);
    }
    renderDevice(d);

    if (prev === 'ready' && prevMode && prevMode !== d.connMode) {
      if (d.connMode === 'wifi') {
        toast(`🔵 USB-Kabel abgezogen – ${d.name} ist jetzt über Apple-Funk verbunden!`, 'ok');
      } else {
        toast(`🟢 USB-Kabel eingesteckt – Volles USB-Tempo aktiv!`, 'ok');
      }
    }

    const key = `${d.name}|${d.model}`;
    if (key !== S.usb.deviceKey || prev !== 'ready') {
      S.usb.deviceKey = key;
      $('#usbState').hidden = true;
      $('#usbReady').hidden = false;
      if (prev && prev !== 'ready') {
        toast(d.connMode === 'wifi'
          ? `🔵 ${d.name} per Apple-Funk (kabellos) verbunden!`
          : `🟢 ${d.name} per USB-Kabel verbunden!`, 'ok');
      }
      await loadAppsAndInitDino();
    }
    return;
  }

  if (prev === 'ready') {
    toast(
      st.state === 'paused'
        ? '🔌 Verbindung getrennt – Du kannst das Kabel abziehen oder testweise neu verbinden'
        : '🔌 Verbindung getrennt',
      st.state === 'paused' ? 'ok' : 'warn'
    );
  }

  S.usb.deviceKey = null;
  $('#usbReady').hidden = true;
  $('#usbState').hidden = false;

  // Hintergrund-Check, falls noch nicht erfolgt
  if (!S.usb.appleStatus) {
    checkAppleDevices().then(() => {
      renderStoreActions();
    }).catch(() => {});
  }

  renderStoreActions();

  let title = 'Apple-Gerät per USB-Kabel verbinden';
  let text = 'Dino wartet am USB-Anschluss. Sobald das Gerät per Kabel angeschlossen wird, erkennt er es automatisch.';
  let pillState = [null, 'USB: kein Gerät'];

  const isPaused = st.state === 'paused' || st.servicePaused;
  if ($('#btnStartService')) $('#btnStartService').hidden = !isPaused;
  if ($('#btnManualSearch')) $('#btnManualSearch').hidden = isPaused;

  if (isPaused) {
    title = st.message || 'Verbindung manuell getrennt ✅';
    text = st.details || 'Die Sitzung zum Gerät ist beendet. Du kannst das USB-Kabel jetzt sicher abziehen – oder klicke unten auf „Jetzt wieder verbinden“, um testweise neu zu verbinden.';
    pillState = ['is-warn', st.cablePlugged ? 'Getrennt (Kabel abziehbar)' : 'Getrennt (Kabel abgezogen)'];
  } else if (st.state === 'locked') {
    title = 'Gerät bitte entsperren 🔓';
    text = 'Das Gerät ist gesperrt. Bitte einmal den Bildschirm entsperren.';
    pillState = ['is-warn', 'USB: entsperren'];
  } else if (st.state === 'trust') {
    title = 'Auf dem Gerät „Vertrauen“ tippen 🤝';
    text = 'Auf dem Display deines Apple-Geräts erscheint „Diesem Computer vertrauen?“. Tippe auf Vertrauen und gib deinen Code ein.';
    pillState = ['is-warn', 'USB: Vertrauen bestätigen'];
  } else if (st.state === 'denied') {
    title = 'Vertrauen wurde abgelehnt';
    text = 'USB-Kabel kurz abziehen, wieder anstecken und auf dem Display „Vertrauen“ antippen.';
    pillState = ['is-bad', 'USB: abgelehnt'];
  } else if (st.state === 'no_driver') {
    title = 'Apple-Dienst antwortet nicht';
    text = 'Der Hintergrunddienst (usbmuxd) läuft noch nicht. Klicke auf „Apple Geräte öffnen“, um den Dienst zu starten.';
    pillState = ['is-bad', 'USB: Dienst fehlt'];
  } else if (st.message && st.details) {
    title = st.message;
    text = st.details;
  }

  setPill('#pillUsb', pillState[0], pillState[1]);
  $('#usbStateTitle').textContent = title;
  $('#usbStateText').textContent = text;
}

function renderStoreActions() {
  const isStoreInstalled = S.usb.appleStatus && S.usb.appleStatus.installed;
  const drvBadge = $('#driverInstalledBadge');
  const btnStore = $('#btnStoreApple');
  if (drvBadge && btnStore && isStoreInstalled) {
    drvBadge.className = 'driver-badge is-ok';
    drvBadge.textContent = '✅ „Apple Geräte“ (Treiber) wurde bereits installiert';
    drvBadge.hidden = false;
    btnStore.hidden = true;
  }
}

function getDeviceLabel(d = S.usb.device) {
  if (!d) return 'Apple-Gerät';
  const cls = (d.class || '').toLowerCase();
  if (d.isIPhone || cls.includes('iphone')) return 'iPhone';
  if (cls.includes('ipad')) return 'iPad';
  return d.name || 'Apple-Gerät';
}

function getDeviceRootLabel(d = S.usb.device) {
  if (!d) return 'Auf meinem Apple-Gerät';
  return `Auf meinem ${getDeviceLabel(d)}`;
}

function renderDevice(d) {
  const devLabel = getDeviceLabel(d);
  const rootLabel = getDeviceRootLabel(d);

  if ($('#devName')) $('#devName').textContent = d.name;
  if ($('#devIcon')) $('#devIcon').textContent = d.isIPhone ? '📱' : '📲';
  if ($('#sidebarHeading')) $('#sidebarHeading').textContent = rootLabel;
  if ($('#rootViewLabel')) $('#rootViewLabel').textContent = `Alle App-Ordner auf dem ${devLabel}`;
  if ($('#tabUsb')) $('#tabUsb').textContent = `🔌 ${rootLabel} (USB)`;
  if ($('#btnUsbMkdir')) $('#btnUsbMkdir').title = `Einen neuen Ordner auf dem ${devLabel} erstellen`;

  const parts = [
    d.class || 'Apple-Gerät',
    d.ios ? `iOS ${d.ios}` : '',
    d.battery != null ? `🔋 ${d.battery} %` : ''
  ].filter(Boolean);
  if ($('#devMeta')) $('#devMeta').textContent = parts.join(' · ');

  if (d.diskTotal && d.diskFree != null) {
    const used = d.diskTotal - d.diskFree;
    if ($('#devStorage')) $('#devStorage').hidden = false;
    if ($('#devStorageFill')) $('#devStorageFill').style.width = `${Math.min(100, (used / d.diskTotal) * 100).toFixed(1)}%`;
    if ($('#devStorageText')) $('#devStorageText').textContent = `${fmtBytes(d.diskFree)} frei von ${fmtBytes(d.diskTotal)}`;
  } else {
    if ($('#devStorage')) $('#devStorage').hidden = true;
  }

  // Verbindungs- & Kabel-Status Box aktualisieren
  const badge = $('#connModeBadge');
  const hint = $('#connModeHint');
  const allowWifi = d.allowWireless !== false;
  if ($('#chkAllowWireless')) $('#chkAllowWireless').checked = allowWifi;
  if ($('#chkAllowWirelessStart')) $('#chkAllowWirelessStart').checked = allowWifi;

  if (badge && hint) {
    if (d.connMode === 'wifi') {
      badge.className = 'conn-box__badge is-wifi';
      badge.textContent = '🔵 Apple-Funk aktiv (Kabellos)';
      hint.innerHTML = `<b>Ohne Kabel verbunden:</b> Läuft kabellos über den Apple-Treiber (kein USB-Kabel eingesteckt). Für maximales Tempo einfach das USB-Kabel anstecken – oder unten auf <b>„Verbindung trennen“</b> klicken.`;
    } else {
      badge.className = 'conn-box__badge is-usb';
      badge.textContent = '🟢 USB-Kabel aktiv (Volles Tempo)';
      hint.innerHTML = `<b>Mit USB-Kabel verbunden:</b> Maximale Geschwindigkeit! Ziehst du das Kabel ab, bleibt dein ${devLabel} nahtlos per <b>🔵 Apple-Funk</b> verbunden – oder klicke unten auf <b>„Verbindung trennen“</b>.`;
    }
  }
}

/* ================================================================ Apps & Dino-Transfer Setup */
async function loadAppsAndInitDino() {
  if ($('#appList')) $('#appList').replaceChildren(el('li', { class: 'muted small', text: 'Lade Apps …' }));
  try {
    S.usb.apps = await api('usb_apps');
  } catch (e) {
    S.usb.apps = [];
    toast(e.message, 'error');
  }
  renderApps();

  // Dino-Transfer Ordner sicherstellen
  try {
    S.usb.dinoInfo = await api('usb_ensure_dino_folder');
  } catch (e) {
    console.warn('ensure_dino_folder', e);
  }

  const bundles = S.usb.apps.map((a) => a.bundle).filter((b) => !(b in S.usb.icons));
  if (bundles.length) {
    api('usb_icons', bundles)
      .then((icons) => { Object.assign(S.usb.icons, icons); renderApps(); if (S.usb.mode === 'root') renderBrowser(); })
      .catch(() => {});
  }

  // Standardmäßig immer direkt in Dino-Transfer oder vorherige Auswahl starten
  if (S.usb.mode === 'photos') {
    selectPhotosFolder(true);
  } else if (S.usb.mode === 'app' && S.usb.app) {
    selectApp(S.usb.app);
  } else {
    selectDinoFolder(true);
  }
}

function renderApps() {
  const q = ($('#appSearch')?.value || '').trim().toLowerCase();
  const apps = S.usb.apps.filter((a) => !q || a.name.toLowerCase().includes(q) || a.bundle.toLowerCase().includes(q));
  if ($('#appsEmpty')) $('#appsEmpty').hidden = S.usb.apps.length > 0;
  
  $('#appList')?.replaceChildren(...apps.map((a) => {
    const icon = S.usb.icons[a.bundle];
    const isActive = S.usb.mode === 'app' && S.usb.app && S.usb.app.bundle === a.bundle;
    return el('li', {
      class: `app-item ${isActive ? 'is-active' : ''}`,
      title: a.bundle,
      onclick: () => selectApp(a),
    },
    el('div', { class: 'app-item__icon' }, icon ? el('img', { src: icon, alt: '' }) : '📦'),
    el('div', { class: 'app-item__text' },
      el('div', { class: 'app-item__name' }, a.name, a.favorite ? el('span', { class: 'star', text: ' ★' }) : null),
      el('div', { class: 'app-item__hint', text: a.hint || a.bundle })
    ));
  }));
}
$('#appSearch')?.addEventListener('input', renderApps);
$('#appsReload')?.addEventListener('click', () => { S.usb.icons = {}; loadAppsAndInitDino(); });

/* ================================================================ Navigation */
// Toggle für Info-Boxen (Dino-Transfer oder Fotogalerie)
let currentHintMode = null;
function showTabletHint(type = 'dino', ev) {
  if (ev) ev.stopPropagation();
  const hintEl = $('#dinoTabletHint');
  if (!hintEl) return;
  const label = getDeviceLabel();
  const rootLabel = getDeviceRootLabel();

  if (currentHintMode === type && !hintEl.hidden) {
    hintEl.hidden = true;
    currentHintMode = null;
    $('#btnToggleTabletHint')?.classList.remove('is-active');
    $('#btnDinoInfoInline')?.classList.remove('is-active');
    $('#btnPhotosInfoInline')?.classList.remove('is-active');
    return;
  }

  currentHintMode = type;
  hintEl.hidden = false;
  $('#btnToggleTabletHint')?.classList.add('is-active');

  if (type === 'photos') {
    $('#btnDinoInfoInline')?.classList.remove('is-active');
    $('#btnPhotosInfoInline')?.classList.add('is-active');
    if ($('#dinoHintTitle')) $('#dinoHintTitle').textContent = `📸 Fotos & Bilder auf dem ${label}:`;
    if ($('#dinoHintText')) {
      $('#dinoHintText').innerHTML = `Hier hast du direkten Zugriff auf den <b>Kamera-Ordner (DCIM)</b> deines ${label}. Du kannst Fotos/Videos sofort auf deinen PC sichern oder neue Bilder vom PC übertragen!`;
    }
  } else {
    $('#btnDinoInfoInline')?.classList.add('is-active');
    $('#btnPhotosInfoInline')?.classList.remove('is-active');
    if ($('#dinoHintTitle')) $('#dinoHintTitle').textContent = `Auf deinem ${label} finden:`;
    if ($('#dinoHintText')) {
      $('#dinoHintText').innerHTML = `Öffne die <b>Dateien-App</b> ➔ <b>${rootLabel}</b> ➔ <b>Chrome</b> ➔ <b>Dino-Transfer</b>. Dort liegen all deine übertragenen Ordner &amp; Dateien!`;
    }
  }
}
$('#btnToggleTabletHint')?.addEventListener('click', (e) => showTabletHint(S.usb.mode === 'photos' ? 'photos' : 'dino', e));
$('#btnDinoInfoInline')?.addEventListener('click', (e) => showTabletHint('dino', e));
$('#btnPhotosInfoInline')?.addEventListener('click', (e) => showTabletHint('photos', e));

// Toggle für optionale andere Apps
let otherAppsVisible = false;
$('#btnToggleOtherApps')?.addEventListener('click', () => {
  otherAppsVisible = !otherAppsVisible;
  if ($('#otherAppsSection')) $('#otherAppsSection').hidden = !otherAppsVisible;
  if ($('#btnToggleOtherApps')) {
    $('#btnToggleOtherApps').textContent = otherAppsVisible
      ? '▲ Andere App-Ordner verbergen'
      : '▼ Andere App-Ordner anzeigen (optional)';
  }
});

// 1. Dino-Transfer Hauptordner (Standard)
function selectDinoFolder(keepSubpath = false) {
  if (!S.usb.dinoInfo) {
    // Fallback falls dinoInfo noch lädt
    if (S.usb.apps.length) {
      S.usb.dinoInfo = {
        bundle: S.usb.apps[0].bundle,
        appName: S.usb.apps[0].name,
        path: '/Documents/Dino-Transfer',
        name: 'Dino-Transfer'
      };
    } else {
      return;
    }
  }

  S.usb.mode = 'dino';
  S.usb.app = {
    bundle: S.usb.dinoInfo.bundle,
    name: 'Dino-Transfer',
    isDino: true,
    hostAppName: S.usb.dinoInfo.appName,
  };

  if (!keepSubpath || !S.usb.path || !S.usb.path.startsWith('/Documents/Dino-Transfer')) {
    S.usb.path = '/Documents/Dino-Transfer';
  }

  $('#btnDinoFolder')?.classList.add('is-active');
  $('#btnPhotosFolder')?.classList.remove('is-active');
  $('#btnRootView')?.classList.remove('is-active');
  renderApps();
  loadDir(S.usb.path);
}
$('#btnDinoFolder')?.addEventListener('click', () => selectDinoFolder(false));

// 1b. Dino-Bilder-Transfer (Direkter Zugriff auf DCIM)
function selectPhotosFolder(keepSubpath = false) {
  S.usb.mode = 'photos';
  S.usb.app = {
    bundle: '__photos__',
    name: 'Fotos & Bilder',
    isPhotos: true,
  };

  if (!keepSubpath || !S.usb.path || !S.usb.path.startsWith('DCIM')) {
    S.usb.path = 'DCIM';
  }

  $('#btnPhotosFolder')?.classList.add('is-active');
  $('#btnDinoFolder')?.classList.remove('is-active');
  $('#btnRootView')?.classList.remove('is-active');
  renderApps();
  loadDir(S.usb.path);
}
$('#btnPhotosFolder')?.addEventListener('click', () => selectPhotosFolder(false));

// 2. Übersicht aller Ordner auf dem Gerät
function selectRootView() {
  S.usb.mode = 'root';
  S.usb.app = null;
  S.usb.path = null;
  S.usb.entries = [];
  S.usb.selected.clear();

  $('#btnDinoFolder')?.classList.remove('is-active');
  $('#btnPhotosFolder')?.classList.remove('is-active');
  $('#btnRootView')?.classList.add('is-active');
  renderApps();
  renderBrowser();
}
$('#btnRootView')?.addEventListener('click', selectRootView);

// 3. Spezifische App auswählen (vom Nutzer selbst angeklickt)
function selectApp(app) {
  S.usb.mode = 'app';
  S.usb.app = app;
  S.usb.path = '/Documents';

  $('#btnDinoFolder')?.classList.remove('is-active');
  $('#btnPhotosFolder')?.classList.remove('is-active');
  $('#btnRootView')?.classList.remove('is-active');
  renderApps();
  loadDir('/Documents');
}

async function loadDir(path) {
  const app = S.usb.app;
  if (!app) return;
  S.usb.loadingDir = true;
  $('#usbLoading').hidden = false;
  try {
    const res = await api('usb_list', app.bundle, path);
    if (S.usb.app !== app) return;
    S.usb.path = res.path;
    S.usb.entries = res.entries;
  } catch (e) {
    toast(e.message, 'error');
    if (S.usb.mode === 'dino' && path !== '/Documents/Dino-Transfer') {
      return loadDir('/Documents/Dino-Transfer');
    }
    if (S.usb.mode === 'photos' && path !== 'DCIM') {
      return loadDir('DCIM');
    }
    if (S.usb.mode === 'app' && path !== '/Documents') {
      return loadDir('/Documents');
    }
    S.usb.entries = [];
  } finally {
    S.usb.loadingDir = false;
    $('#usbLoading').hidden = true;
  }
  S.usb.selected.clear();
  renderBrowser();
}

function relDinoParts() {
  if (S.usb.mode !== 'dino') return [];
  const rel = (S.usb.path || '').replace(/^\/Documents\/Dino-Transfer\/?/, '');
  return rel ? rel.split('/').filter(Boolean) : [];
}

function relPhotosParts() {
  if (S.usb.mode !== 'photos') return [];
  const rel = (S.usb.path || '').replace(/^DCIM\/?/, '');
  return rel ? rel.split('/').filter(Boolean) : [];
}

function relAppParts() {
  if (S.usb.mode !== 'app' || !S.usb.app) return [];
  const rel = (S.usb.path || '').replace(/^\/Documents\/?/, '');
  return rel ? rel.split('/').filter(Boolean) : [];
}

/* ================================================================ Browser rendern */
function renderBrowser() {
  const rootLabel = getDeviceRootLabel();

  // 1) ROOT-ÜBERSICHT: Zeigt Dino-Transfer und alle Apps
  if (S.usb.mode === 'root') {
    $('#crumbs').replaceChildren(
      el('button', { class: 'is-root', text: `🏠 ${rootLabel}`, onclick: selectRootView })
    );
    $('#whereHint').textContent = `📍 Hauptverzeichnis „${rootLabel}“ – Wähle deinen Dino-Transfer Ordner oder eine installierte App`;

    const rows = [];
    
    // Oberster Eintrag: Dino-Transfer Hauptordner
    rows.push(el('tr', {
      class: 'is-dir-row is-dino-row',
      ondblclick: () => selectDinoFolder(false),
      onclick: () => selectDinoFolder(false),
      style: 'background: rgba(234, 179, 8, 0.06); font-weight: bold;'
    },
    el('td', { class: 'col-check' }),
    el('td', {}, el('div', { class: 'fname' },
      el('span', { class: 'fname__icon is-dir' }, el('img', { src: 'dino_hero.png', alt: '' })),
      el('span', { class: 'fname__text', text: '🦕 Dino-Transfer', style: 'color: var(--accent);' }),
      el('span', { class: 'badge is-on', text: 'Dein Hauptordner für alle Dateien', style: 'margin-left:8px;font-size:11.5px' })
    )),
    el('td', { class: 'col-size', text: '—' }),
    el('td', { class: 'col-date', text: 'Hauptverzeichnis' }),
    el('td', {}, el('div', { class: 'row-actions', style: 'opacity:1' },
      el('button', { class: 'btn btn--primary btn--small', text: '📂 Öffnen', onclick: (ev) => { ev.stopPropagation(); selectDinoFolder(false); } })
    ))));

    // Weitere Apps
    for (const a of S.usb.apps) {
      const icon = S.usb.icons[a.bundle];
      rows.push(el('tr', {
        class: 'is-dir-row is-app-row',
        ondblclick: () => selectApp(a),
        onclick: () => selectApp(a),
      },
      el('td', { class: 'col-check' }),
      el('td', {}, el('div', { class: 'fname', title: a.name },
        el('span', { class: 'fname__icon is-dir' }, icon ? el('img', { src: icon, alt: '' }) : '📁'),
        el('span', { class: 'fname__text', text: a.name }),
        el('span', { class: 'badge', text: 'App-Ordner', style: 'margin-left:8px;font-size:11.5px' })
      )),
      el('td', { class: 'col-size', text: '—' }),
      el('td', { class: 'col-date', text: a.version ? `Version ${a.version}` : 'Installierte App' }),
      el('td', {}, el('div', { class: 'row-actions', style: 'opacity:1' },
        el('button', { class: 'btn btn--small', text: '📂 Öffnen', onclick: (ev) => { ev.stopPropagation(); selectApp(a); } })
      ))));
    }

    $('#usbRows').replaceChildren(...rows);
    $('#usbEmpty').hidden = true;
    $('#usbTable').hidden = false;
    updateToolbar();
    return;
  }

  // 2) DINO-TRANSFER HAUPTORDNER (Der zentrale Ort für Dateien & Ordner)
  if (S.usb.mode === 'dino') {
    const parts = relDinoParts();
    const crumbs = [
      el('button', { text: '🦕 Dino-Transfer', onclick: () => selectDinoFolder(false), style: 'color: var(--accent); font-weight:800;' })
    ];
    parts.forEach((p, i) => {
      const target = '/Documents/Dino-Transfer/' + parts.slice(0, i + 1).join('/');
      crumbs.push(el('span', { class: 'sep', text: '›' }), el('button', { text: p, onclick: () => loadDir(target) }));
    });
    $('#crumbs').replaceChildren(...crumbs);
    
    const devLabel = getDeviceLabel();
    $('#whereHint').innerHTML = `📍 <b>Auf deinem ${devLabel} finden:</b> Öffne die <b>Dateien-App</b> ➔ <b>${rootLabel}</b> ➔ <b>Chrome</b> ➔ <b>Dino-Transfer</b>`;

    const rows = [];
    if (parts.length > 0) {
      const up = '/Documents/Dino-Transfer' + (parts.length > 1 ? '/' + parts.slice(0, -1).join('/') : '');
      rows.push(el('tr', { class: 'is-dir-row', ondblclick: () => loadDir(up) },
        el('td'),
        el('td', {}, el('div', { class: 'fname', onclick: () => loadDir(up) },
          el('span', { class: 'fname__icon is-dir', text: '↩' }),
          el('span', { class: 'fname__text', text: parts.length > 1 ? '.. (zurück)' : '.. (zurück zu Dino-Transfer)' })
        )),
        el('td'), el('td', { class: 'col-date' }), el('td')
      ));
    }

    renderDirEntries(rows);
    return;
  }

  // 2b) DINO-BILDER-TRANSFER (Direkter Zugriff auf DCIM Kamera & Bilder)
  if (S.usb.mode === 'photos') {
    const devLabel = getDeviceLabel();
    const parts = relPhotosParts();
    const crumbs = [
      el('button', { text: '📸 Fotos & Bilder (DCIM)', onclick: () => selectPhotosFolder(false), style: 'color: var(--accent); font-weight:800;' })
    ];
    parts.forEach((p, i) => {
      const target = 'DCIM/' + parts.slice(0, i + 1).join('/');
      crumbs.push(el('span', { class: 'sep', text: '›' }), el('button', { text: p, onclick: () => loadDir(target) }));
    });
    $('#crumbs').replaceChildren(...crumbs);
    $('#whereHint').innerHTML = `📸 <b>Fotos &amp; Bilder:</b> Direkter Zugriff auf die Kamera-Galerie (DCIM) deines ${devLabel}.`;

    const rows = [];
    if (parts.length > 0) {
      const up = 'DCIM' + (parts.length > 1 ? '/' + parts.slice(0, -1).join('/') : '');
      rows.push(el('tr', { class: 'is-dir-row', ondblclick: () => loadDir(up) },
        el('td'),
        el('td', {}, el('div', { class: 'fname', onclick: () => loadDir(up) },
          el('span', { class: 'fname__icon is-dir', text: '↩' }),
          el('span', { class: 'fname__text', text: parts.length > 1 ? '.. (zurück)' : '.. (zurück zu DCIM)' })
        )),
        el('td'), el('td', { class: 'col-date' }), el('td')
      ));
    }

    renderDirEntries(rows);
    return;
  }

  // 3) VORGEFERTIGTER APP-ORDNER (z. B. Kindle oder Sketchbook, vom Nutzer selbst geöffnet)
  const app = S.usb.app;
  const parts = relAppParts();
  const crumbs = [
    el('button', { text: `🏠 ${rootLabel}`, onclick: selectRootView }),
    el('span', { class: 'sep', text: '›' }),
    el('button', { text: app.name, onclick: () => loadDir('/Documents') })
  ];
  parts.forEach((p, i) => {
    const target = '/Documents/' + parts.slice(0, i + 1).join('/');
    crumbs.push(el('span', { class: 'sep', text: '›' }), el('button', { text: p, onclick: () => loadDir(target) }));
  });
  $('#crumbs').replaceChildren(...crumbs);
  $('#whereHint').textContent = `📍 In der Dateien-App unter: ${rootLabel} › ${[app.name, ...parts].join(' › ')}`;

  const rows = [];
  const onUp = parts.length > 0
    ? () => loadDir('/Documents' + (parts.length > 1 ? '/' + parts.slice(0, -1).join('/') : ''))
    : selectRootView;

  rows.push(el('tr', { class: 'is-dir-row', ondblclick: onUp },
    el('td'),
    el('td', {}, el('div', { class: 'fname', onclick: onUp },
      el('span', { class: 'fname__icon is-dir', text: '↩' }),
      el('span', { class: 'fname__text', text: parts.length > 0 ? '.. (zurück)' : `.. (zurück zu „${rootLabel}“)` })
    )),
    el('td'), el('td', { class: 'col-date' }), el('td')
  ));

  renderDirEntries(rows);
}

function renderDirEntries(rows) {
  for (const e of S.usb.entries) {
    const sel = S.usb.selected.has(e.path);
    const cb = el('input', { type: 'checkbox', checked: sel, onclick: (ev) => { ev.stopPropagation(); toggleSel(e.path); } });
    const open = e.isDir ? () => loadDir(e.path) : null;
    rows.push(el('tr', {
      class: `${sel ? 'is-selected' : ''} ${e.isDir ? 'is-dir-row' : ''}`,
      onclick: () => toggleSel(e.path),
      ondblclick: open || (() => pullItems([e.path], true)),
    },
    el('td', { class: 'col-check' }, cb),
    el('td', {}, el('div', { class: 'fname', title: e.name },
      el('span', { class: `fname__icon ${e.isDir ? 'is-dir' : ''}`, text: fileIcon(e.name, e.isDir) }),
      el('span', { class: 'fname__text', text: e.name, onclick: open ? (ev) => { ev.stopPropagation(); open(); } : null }),
      e.isDir ? el('span', { class: 'badge', text: 'Ordner (Doppelklick zum Öffnen)', style: 'margin-left:8px;font-size:11px' }) : null
    )),
    el('td', { class: 'col-size', text: e.isDir ? '—' : fmtBytes(e.size) }),
    el('td', { class: 'col-date', text: fmtDate(e.mtime) }),
    el('td', {}, el('div', { class: 'row-actions' },
      el('button', { class: 'icon-btn', title: 'Speichern unter …', text: '⬇', onclick: (ev) => { ev.stopPropagation(); pullItems([e.path], true); } }),
      el('button', { class: 'icon-btn', title: 'Umbenennen', text: '✏️', onclick: (ev) => { ev.stopPropagation(); renameItem(e); } }),
      el('button', { class: 'icon-btn', title: e.isDir ? 'Ordner samt Inhalt löschen' : 'Löschen', text: '🗑', onclick: (ev) => { ev.stopPropagation(); deleteItems([e.path]); } })
    ))));
  }

  $('#usbRows').replaceChildren(...rows);
  $('#usbEmpty').hidden = S.usb.entries.length > 0;
  $('#usbTable').hidden = false;
  updateToolbar();
}

function toggleSel(path) {
  if (S.usb.selected.has(path)) S.usb.selected.delete(path);
  else S.usb.selected.add(path);
  renderBrowser();
}

function updateToolbar() {
  const isRoot = S.usb.mode === 'root';
  const n = S.usb.selected.size;

  ['#btnUsbPullTo', '#btnUsbDelete'].forEach((id) => {
    const btn = $(id);
    if (btn) btn.disabled = isRoot || n === 0;
  });
  if ($('#btnUsbPullTo')) {
    $('#btnUsbPullTo').textContent = n > 1 ? `⬇ ${n} speichern unter …` : '⬇ Speichern unter …';
  }

  const all = $('#usbCheckAll');
  if (all) {
    all.disabled = isRoot || S.usb.entries.length === 0;
    all.checked = !isRoot && n > 0 && n === S.usb.entries.length;
    all.indeterminate = !isRoot && n > 0 && n < S.usb.entries.length;
  }
}

$('#usbCheckAll')?.addEventListener('change', (e) => {
  if (S.usb.mode === 'root') return;
  S.usb.selected = new Set(e.target.checked ? S.usb.entries.map((x) => x.path) : []);
  renderBrowser();
});

/* ================================================================ Dateiübertragungen (Push & Pull) */
function getActiveTarget() {
  if (S.usb.mode === 'photos') {
    return {
      bundle: '__photos__',
      name: 'Fotos & Bilder',
      path: S.usb.path || 'DCIM',
    };
  }
  if (S.usb.mode === 'app' && S.usb.app) {
    return { bundle: S.usb.app.bundle, name: S.usb.app.name, path: S.usb.path };
  }
  if (!S.usb.dinoInfo) {
    if (S.usb.apps.length) {
      return { bundle: S.usb.apps[0].bundle, name: 'Dino-Transfer', path: '/Documents/Dino-Transfer' };
    }
    return null;
  }
  return {
    bundle: S.usb.dinoInfo.bundle,
    name: 'Dino-Transfer',
    path: S.usb.mode === 'dino' ? S.usb.path : '/Documents/Dino-Transfer'
  };
}

async function pushPaths(paths) {
  if (!paths || !paths.length) return;
  const target = getActiveTarget();
  const devLabel = getDeviceLabel();
  if (!target) {
    toast(`Kein Ziel auf dem ${devLabel} gefunden.`, 'error');
    return;
  }
  // Falls im Root-Modus, zuerst zu Dino-Transfer wechseln
  if (S.usb.mode === 'root') {
    selectDinoFolder(false);
  }
  try {
    await api('usb_push', target.bundle, target.name, paths, target.path);
  } catch (e) {
    toast(e.message, 'error');
  }
}

async function pullItems(paths, choose = false) {
  const target = getActiveTarget();
  if (!target || !paths.length) return;
  try {
    await api(choose ? 'usb_pull_to' : 'usb_pull', target.bundle, target.name, paths);
  } catch (e) {
    toast(e.message, 'error');
  }
}

async function deleteItems(paths) {
  const target = getActiveTarget();
  if (!target || !paths.length) return;
  const devLabel = getDeviceLabel();
  const names = paths.map((p) => p.split('/').pop());
  const isSingleDir = paths.length === 1 && S.usb.entries.find((e) => e.path === paths[0] && e.isDir);

  const ok = await dialog({
    title: isSingleDir ? `Ordner „${names[0]}“ löschen?` : `${paths.length === 1 ? `„${names[0]}“` : `${paths.length} Elemente`} löschen?`,
    text: isSingleDir
      ? `Der gesamte Ordner samt Inhalt wird direkt vom ${devLabel} gelöscht.`
      : `Wird direkt vom ${devLabel} gelöscht.`,
    okLabel: '🗑 Löschen',
    danger: true,
  });
  if (!ok) return;
  try {
    await api('usb_delete', target.bundle, paths);
    toast(`🗑 ${isSingleDir ? `Ordner „${names[0]}“` : `${paths.length} Elemente`} gelöscht`, 'ok');
  } catch (e) {
    toast(e.message, 'error');
  }
  loadDir(S.usb.path);
}

async function renameItem(entry) {
  const target = getActiveTarget();
  if (!target) return;
  const name = await dialog({ title: 'Umbenennen', input: entry.name, okLabel: 'Speichern' });
  if (!name || name === entry.name) return;
  try {
    await api('usb_rename', target.bundle, entry.path, name);
  } catch (e) {
    toast(e.message, 'error');
  }
  loadDir(S.usb.path);
}

/* ================================================================ Buttons & Toolbar Events */
$('#btnUsbAdd')?.addEventListener('click', async () => {
  try {
    const files = await api('pick_files');
    if (files && files.length) pushPaths(files.map((f) => f.path));
  } catch (e) {
    toast(e.message, 'error');
  }
});

$('#btnUsbAddFolder')?.addEventListener('click', async () => {
  try {
    const f = await api('pick_folder');
    if (f) pushPaths([f.path]);
  } catch (e) {
    toast(e.message, 'error');
  }
});

// Neuer Ordner: Wird direkt im aktuellen Ordner angelegt
$('#btnUsbMkdir')?.addEventListener('click', async () => {
  const target = getActiveTarget();
  const devLabel = getDeviceLabel();
  if (!target) {
    toast(`${devLabel} ist nicht bereit.`, 'warn');
    return;
  }

  // Wenn wir in Root waren, in Dino-Transfer wechseln
  if (S.usb.mode === 'root') {
    selectDinoFolder(false);
  }

  const name = await dialog({
    title: `Neuer Ordner auf dem ${devLabel}`,
    text: `Wird direkt in „${target.name}“ auf deinem ${devLabel} erstellt.`,
    input: 'Neuer Ordner',
    okLabel: 'Ordner anlegen'
  });
  if (!name) return;

  try {
    await api('usb_mkdir', target.bundle, target.path, name);
    toast(`📁 Ordner „${name}“ angelegt`, 'ok');
  } catch (e) {
    toast(e.message, 'error');
  }
  loadDir(target.path);
});

$('#btnUsbPullTo')?.addEventListener('click', () => pullItems([...S.usb.selected], true));
$('#btnUsbDelete')?.addEventListener('click', () => deleteItems([...S.usb.selected]));
$('#btnUsbRefresh')?.addEventListener('click', () => {
  if (S.usb.mode === 'root') renderBrowser();
  else loadDir(S.usb.path);
});

async function onToggleWireless(e) {
  const allow = !!e.target.checked;
  if ($('#chkAllowWireless')) $('#chkAllowWireless').checked = allow;
  if ($('#chkAllowWirelessStart')) $('#chkAllowWirelessStart').checked = allow;
  try {
    await api('set_wireless_mode', allow);
    if (S.usb.device) {
      S.usb.device.allowWireless = allow;
      renderDevice(S.usb.device);
    }
    toast(allow
      ? `📡 Apple-Funk aktiviert – ${getDeviceLabel()} bleibt auch ohne Kabel verbunden`
      : '🔌 Strikt nur USB-Kabel aktiv – Verbindung ohne Kabel getrennt', 'ok');
    await pollUsb();
  } catch (err) {
    toast(err.message, 'error');
  }
}

$('#chkAllowWireless')?.addEventListener('change', onToggleWireless);
$('#chkAllowWirelessStart')?.addEventListener('change', onToggleWireless);

$('#btnStopService')?.addEventListener('click', async () => {
  try {
    await api('stop_connection_service');
    await pollUsb();
  } catch (err) {
    toast(err.message, 'error');
  }
});

$('#btnStartService')?.addEventListener('click', async () => {
  try {
    toast('▶ Starte Dienst & Verbindung …', 'ok');
    await api('start_connection_service');
    await pollUsb();
  } catch (err) {
    toast(err.message, 'error');
  }
});

$('#btnHelpLaunchApple')?.addEventListener('click', () => {
  api('launch_apple_devices').then(() => {
    toast('🚀 Apple Geräte aufgerufen', 'ok');
    setTimeout(pollUsb, 1500);
  }).catch((e) => toast(e.message, 'error'));
});

document.addEventListener('keydown', (e) => {
  if (S.view !== 'usb' || $('#dialog').open || e.target.matches('input')) return;
  if (e.key === 'Delete' && S.usb.selected.size && S.usb.mode !== 'root') {
    deleteItems([...S.usb.selected]);
  } else if (e.key === 'F5') {
    e.preventDefault();
    if (S.usb.mode === 'root') renderBrowser();
    else loadDir(S.usb.path);
  } else if (e.key === 'Backspace') {
    if (S.usb.mode === 'dino') {
      const parts = relDinoParts();
      if (parts.length > 0) {
        loadDir('/Documents/Dino-Transfer' + (parts.length > 1 ? '/' + parts.slice(0, -1).join('/') : ''));
      } else {
        selectRootView();
      }
    } else if (S.usb.mode === 'app') {
      const parts = relAppParts();
      if (parts.length > 0) {
        loadDir('/Documents' + (parts.length > 1 ? '/' + parts.slice(0, -1).join('/') : ''));
      } else {
        selectRootView();
      }
    }
  } else if (e.key.toLowerCase() === 'a' && e.ctrlKey && S.usb.mode !== 'root') {
    e.preventDefault();
    S.usb.selected = new Set(S.usb.entries.map((x) => x.path));
    renderBrowser();
  }
});

/* ================================================================ Drag & Drop */
let dragDepth = 0;
const hasFiles = (e) => e.dataTransfer && [...e.dataTransfer.types].includes('Files');

function dropTargetNode() {
  const rootLabel = getDeviceRootLabel();
  if (S.usb.state === 'ready') {
    if (S.usb.mode === 'photos') {
      const loc = [rootLabel, '📸 Fotos & Bilder (DCIM)', ...relPhotosParts()].join(' › ');
      return [document.createTextNode('wird direkt kopiert nach '), el('b', { text: loc })];
    }
    if (S.usb.mode === 'app' && S.usb.app) {
      return [document.createTextNode('wird per USB-Kabel kopiert nach '), el('b', { text: [rootLabel, S.usb.app.name, ...relAppParts()].join(' › ') })];
    }
    const dinoSub = relDinoParts();
    const loc = [rootLabel, 'Dino-Transfer', ...dinoSub].join(' › ');
    return [document.createTextNode('wird per USB-Kabel kopiert nach '), el('b', { text: loc })];
  }
  return [document.createTextNode('Bitte zuerst dein Apple-Gerät per USB anschließen.')];
}

window.addEventListener('dragenter', (e) => {
  if (!hasFiles(e)) return;
  if (dragDepth++ === 0) {
    $('#dropTarget').replaceChildren(...dropTargetNode());
    document.body.classList.add('is-dragging');
  }
});
window.addEventListener('dragleave', (e) => {
  if (!hasFiles(e)) return;
  if (--dragDepth <= 0) {
    dragDepth = 0;
    document.body.classList.remove('is-dragging');
  }
});
window.addEventListener('dragover', (e) => {
  e.preventDefault();
  if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy';
});
window.addEventListener('drop', (e) => {
  e.preventDefault();
  dragDepth = 0;
  document.body.classList.remove('is-dragging');
});

function handleDrop(items) {
  const paths = items.filter((i) => i.exists).map((i) => i.path);
  if (!paths.length) return;
  if (S.usb.state === 'ready') {
    pushPaths(paths);
  } else {
    toast('Bitte zuerst dein Apple-Gerät per USB-Kabel anschließen.', 'warn');
  }
}

/* ================================================================ Jobs */
function renderJob(j) {
  let li = $(`#job-${j.id}`);
  if (!li) {
    li = el('li', { class: 'job', id: `job-${j.id}` },
      el('div', { class: 'job__top' },
        el('div', { class: 'job__title' }),
        el('button', { class: 'icon-btn', title: 'Abbrechen', text: '✕', onclick: () => api('job_cancel', j.id) })
      ),
      el('div', { class: 'job__sub' }),
      el('div', { class: 'bar' }, el('span'))
    );
    $('#jobList').prepend(li);
  }
  const icon = { push: '⬆', pull: '⬇' }[j.kind] || '🦕';
  li.querySelector('.job__title').textContent = `${icon} ${j.title}`;
  const pct = j.bytesTotal ? Math.round((j.bytesDone / j.bytesTotal) * 100) : (j.state === 'done' ? 100 : 0);
  let sub;
  if (j.state === 'running') {
    sub = `${j.filesDone}/${j.filesTotal} Dateien · ${pct} % · ${fmtBytes(j.speed)}/s${j.current ? ' · ' + j.current : ''}`;
  } else if (j.state === 'done') {
    sub = `✅ Fertig – ${j.filesDone} Datei${j.filesDone !== 1 ? 'en' : ''}, ${fmtBytes(j.bytesDone)}`;
  } else if (j.state === 'cancelled') {
    sub = `⏹ Abgebrochen nach ${j.filesDone} Datei${j.filesDone !== 1 ? 'en' : ''}`;
  } else {
    sub = `❌ ${j.error || 'Fehler'}`;
  }
  li.querySelector('.job__sub').textContent = sub;
  li.querySelector('.bar span').style.width = `${j.state === 'done' ? 100 : pct}%`;
  li.classList.toggle('is-done', j.state === 'done');
  li.classList.toggle('is-error', j.state === 'error');
  li.querySelector('.icon-btn').hidden = j.state !== 'running';
  $('#jobs').hidden = false;
}

function onJob(j) {
  S.jobs.set(j.id, j);
  renderJob(j);
  if (j.state === 'running') return;

  setTimeout(() => {
    $(`#job-${j.id}`)?.remove();
    S.jobs.delete(j.id);
    if (!$('#jobList').children.length) $('#jobs').hidden = true;
  }, j.state === 'error' ? 8000 : 4000);

  if (j.state === 'error') {
    toast(`❌ ${j.error}`, 'error', null, 7000);
  }
  if (j.kind === 'push') {
    if (S.usb.mode !== 'root') loadDir(S.usb.path);
    if (j.state === 'done') toast(`🦕 ${j.filesDone} Datei${j.filesDone !== 1 ? 'en' : ''} per USB übertragen`, 'ok');
  } else if (j.kind === 'pull' && j.state === 'done') {
    toast(`💾 ${j.filesDone} Datei${j.filesDone !== 1 ? 'en' : ''} auf dem PC gespeichert`, 'ok', {
      label: '📂 Ordner öffnen',
      fn: () => api('open_path', j.localDir, false)
    }, 7000);
  }
}

/* ================================================================ Events & Initialisierung */
window.Dino = {
  onEvent(ev) {
    switch (ev.type) {
      case 'job': onJob(ev); break;
      case 'drop': handleDrop(ev.items); break;
      case 'toast': toast(ev.text, ev.kind); break;
      default: break;
    }
  },
};

async function waitForApi(maxMs = 6000) {
  const t0 = Date.now();
  while (Date.now() - t0 < maxMs) {
    if (window.pywebview && window.pywebview.api && typeof window.pywebview.api.get_state === 'function') {
      return true;
    }
    await new Promise((r) => setTimeout(r, 80));
  }
  return false;
}

function bindStaticUi() {
  $('#btnBaseOpen')?.addEventListener('click', () => api('open_folder', 'from'));
  $('#btnBaseChange')?.addEventListener('click', async () => {
    try {
      const dir = await api('choose_base_dir');
      if (dir) {
        S.cfg.baseDir = dir;
        if ($('#baseDir')) $('#baseDir').textContent = dir;
        toast('📁 Ordner geändert', 'ok');
      }
    } catch (e) { toast(e.message, 'error'); }
  });

  // Buttons auf dem Startbildschirm
  $('#btnManualSearch')?.addEventListener('click', async () => {
    const btn = $('#btnManualSearch');
    if (btn && btn.disabled) return;
    if (btn) {
      btn.disabled = true;
      btn.innerHTML = '<span class="btn__icon">⏳</span> Suche läuft …';
    }
    toast('🔍 Suche nach Apple-Gerät …', 'info');
    try {
      await pollUsb();
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.innerHTML = '<span class="btn__icon">↻</span> Jetzt nach Apple-Gerät suchen';
      }
    }
  });

  $('#btnLaunchApple')?.addEventListener('click', async () => {
    toast('🚀 Öffne Apple Geräte …', 'info');
    try {
      await api('launch_apple_devices');
      setTimeout(pollUsb, 1500);
    } catch (e) {
      toast(e.message, 'error');
    }
  });

  $('#btnStoreApple')?.addEventListener('click', () => {
    api('open_url', 'ms-windows-store://pdp/?productid=9NP83LWLPZ9K').catch(() => {
      window.open('https://apps.microsoft.com/detail/9np83lwlpz9k', '_blank');
    });
  });

  // Wiederherstellung des Seitenleisten-Status
  try {
    const saved = localStorage.getItem('dino_sidebar_collapsed');
    if (saved === '1') setSidebarCollapsed(true);
  } catch (_) {}
}

async function init() {
  bindStaticUi();

  const ready = await waitForApi();
  if (!ready) {
    toast('⚠️ Backend-Verbindung verzögert. Dino versucht weiter zu verbinden …', 'warn');
  }

  try {
    S.cfg = await api('get_state');
    if ($('#baseDir')) $('#baseDir').textContent = S.cfg.baseDir;
    if ($('#versionInfo')) $('#versionInfo').textContent = `${S.cfg.displayName || 'Dino-Apple-Transfer'} ${S.cfg.version} · PC: ${S.cfg.pcName}`;
  } catch (_) {}

  // Initiale Hintergrundprüfung der Apple-Dienste (nicht-blockierend)
  checkAppleDevices().then(renderStoreActions).catch(() => {});

  // Adaptive Polling-Schleife: 1200ms wenn nicht verbunden, 3500ms wenn verbunden
  async function pollLoop() {
    try {
      await pollUsb();
    } catch (_) {}
    const delay = (S.usb.state === 'ready') ? 3500 : 1200;
    setTimeout(pollLoop, delay);
  }
  pollLoop();
}

function start() {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init, { once: true });
  } else {
    init();
  }
}

start();
window.addEventListener('pywebviewready', () => {
  // Falls pywebview nachträglich bereit wird
  if (!S.cfg || !S.cfg.app) {
    init();
  }
});
