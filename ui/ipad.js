'use strict';

const $ = (s) => document.querySelector(s);

function fmtBytes(b) {
  if (!b) return '0 B';
  const u = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.min(Math.floor(Math.log(b) / Math.log(1024)), u.length - 1);
  return (b / 1024 ** i).toLocaleString('de-DE', { maximumFractionDigits: i ? 1 : 0 }) + ' ' + u[i];
}

const EXT_ICONS = {
  mp3: '🎵', flac: '🎵', m4a: '🎵', wav: '🎵', ogg: '🎵',
  mp4: '🎬', mkv: '🎬', mov: '🎬', avi: '🎬',
  pdf: '📄', epub: '📚', mobi: '📚',
  jpg: '🖼️', jpeg: '🖼️', png: '🖼️', heic: '🖼️',
  zip: '📦', '7z': '📦', rar: '📦'
};

function getFileIcon(name) {
  const ext = name.split('.').pop().toLowerCase();
  return EXT_ICONS[ext] || '📄';
}

async function loadFiles() {
  const list = $('#fileList');
  try {
    const res = await fetch('/api/files');
    if (!res.ok) throw new Error('Fehler beim Laden');
    const data = await res.json();
    
    if (data.pcName) {
      $('#pcName').textContent = data.pcName;
    }

    const files = data.toDevice || data.toIpad || [];
    if (!files.length) {
      list.innerHTML = '<div class="empty-state">🦕 Auf dem PC liegen noch keine Dateien im Bereitstellungs-Ordner.</div>';
      return;
    }

    list.innerHTML = files.map(f => {
      const isDir = f.isDir;
      const icon = isDir ? '📁' : getFileIcon(f.name);
      const meta = isDir ? 'Ordner' : `${fmtBytes(f.size)} ${f.folder ? '· 📁 ' + f.folder : ''}`;
      const actionBtn = isDir
        ? `<span class="badge" style="margin-right:4px">Ordner</span>`
        : `<a class="btn-dl" href="/dl/${encodeURIComponent(f.rel)}" download="${f.name}">Laden</a>`;

      return `
        <div class="file-item">
          <div class="file-left">
            <span class="file-icon">${icon}</span>
            <div style="min-width:0">
              <div class="file-name" title="${f.name}">${f.name}</div>
              <div class="file-meta">${meta}</div>
            </div>
          </div>
          <div style="display:flex;align-items:center;gap:6px">
            ${actionBtn}
            <button class="btn-del" onclick="deleteRemoteItem('${encodeURIComponent(f.rel)}', '${f.name}', ${isDir})" title="Löschen">🗑</button>
          </div>
        </div>
      `;
    }).join('');
  } catch (err) {
    list.innerHTML = `<div class="empty-state" style="color:#ef4444">⚠️ Verbindung zum PC unterbrochen: ${err.message}</div>`;
  }
}

// Ordner anlegen
$('#btnIpadMkdir')?.addEventListener('click', async () => {
  const name = prompt('Name für den neuen Ordner:', 'Neuer Ordner');
  if (!name || !name.trim()) return;
  try {
    const res = await fetch(`/api/mkdir?name=${encodeURIComponent(name.trim())}`, { method: 'POST' });
    if (!res.ok) throw new Error('Konnte Ordner nicht anlegen');
    loadFiles();
  } catch (e) {
    alert('Fehler: ' + e.message);
  }
});

// Löschen
window.deleteRemoteItem = async function(relEncoded, name, isDir) {
  const rel = decodeURIComponent(relEncoded);
  const type = isDir ? 'den Ordner' : 'die Datei';
  if (!confirm(`Möchtest du ${type} „${name}“ wirklich löschen?`)) return;
  try {
    const res = await fetch(`/api/delete?rel=${encodeURIComponent(rel)}`, { method: 'POST' });
    if (!res.ok) throw new Error('Konnte nicht gelöscht werden');
    loadFiles();
  } catch (e) {
    alert('Fehler: ' + e.message);
  }
};

// Upload handling
const fileInput = $('#fileInput');
const dropZone = $('#uploadDropZone');
const progressWrap = $('#progressWrap');
const progressFill = $('#progressFill');
const progressText = $('#progressText');

dropZone.addEventListener('click', () => fileInput.click());

fileInput.addEventListener('change', async () => {
  const files = Array.from(fileInput.files);
  if (!files.length) return;

  progressWrap.hidden = false;
  
  for (let i = 0; i < files.length; i++) {
    const file = files[i];
    progressText.textContent = `Sende (${i + 1}/${files.length}): ${file.name} …`;
    progressFill.style.width = '0%';

    try {
      await uploadFile(file);
    } catch (e) {
      alert(`Fehler beim Hochladen von ${file.name}: ` + e.message);
    }
  }

  progressText.textContent = '✅ Alle Dateien erfolgreich zum PC gesendet!';
  progressFill.style.width = '100%';
  setTimeout(() => {
    progressWrap.hidden = true;
    progressFill.style.width = '0%';
  }, 4000);

  fileInput.value = '';
  loadFiles();
});

function uploadFile(file) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `/api/upload?name=${encodeURIComponent(file.name)}`, true);
    
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) {
        const pct = Math.round((e.loaded / e.total) * 100);
        progressFill.style.width = `${pct}%`;
        progressText.textContent = `${pct}% (${fmtBytes(e.loaded)} / ${fmtBytes(e.total)}) - ${file.name}`;
      }
    };

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve();
      } else {
        reject(new Error(`Server Status ${xhr.status}`));
      }
    };

    xhr.onerror = () => reject(new Error('Netzwerkfehler'));
    xhr.send(file);
  });
}

$('#btnRefresh').addEventListener('click', loadFiles);

loadFiles();
setInterval(loadFiles, 6000);
