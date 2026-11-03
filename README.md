# Dino-Apple-Transfer 🦕📱

> **100% lokale, direkte USB-Kabel-Dateiübertragung für iPhone & iPad (iOS/iPadOS)**  
> Mit dem niedlichen Ankylosaurus-Maskottchen, dunklem Tech-Theme (`#121212`) und leuchtend gelben Akzenten (`#eab308`).

---

## ✨ Features

- **🔌 Direkte USB-Kabelverbindung:** Nutzt Apples natives `house_arrest`- und `AFC`-Protokoll (über `pymobiledevice3`). Keine Cloud, kein WLAN, kein Internet nötig.
- **🦕 Dino-Transfer Hauptordner:** Alle Dateien und neu angelegten Ordner landen direkt im dedizierten `Dino-Transfer`-Verzeichnis auf dem iPad/iPhone.
- **📁 Volle Ordnerverwaltung:** 
  - Beliebig viele Unterordner direkt auf dem iPad erstellen (`＋📁 Neuer Ordner auf iPad`).
  - Ordner samt Inhalt mit einem Klick löschen (`🗑`).
  - Dateien und Ordner hin- und herkopieren (PC ➔ iPad und iPad ➔ PC).
- **📂 Schneller iPad-Zugriff:** Auf dem iPad findest du deine Dateien direkt in der **Dateien-App** unter:  
  `Auf meinem iPad › Chrome › Dino-Transfer`
- **🖥️ Drag & Drop:** Ziehe Dateien oder Ordner direkt aus dem Windows-Explorer in das Anwendungsfenster.
- **🚀 Apple-Geräte-Integration:** Erkennt automatisch, ob die Windows Store App *„Apple Geräte“* installiert ist, und bietet direkte Start- bzw. Download-Möglichkeiten.
- **💡 iPad-App-Tipps:** Empfehlungen für kostenlose Player wie **VLC for Mobile** (spielt MP3, FLAC, MKV, MP4 ohne iTunes ab) und **Documents von Readdle**.

---

## 🚀 Installation & Schnellstart

### 1. Voraussetzungen (Windows 10 / 11)
- **Python 3.10+** (getestet mit Python 3.12)
- **Apple Geräte App** aus dem Microsoft Store (installiert den benötigten Apple-USB-Treiber):  
  [Apple Geräte im Microsoft Store](ms-windows-store://pdp/?productid=9NP83LWLPZ9K)

### 2. Schnelle Einrichtung
```powershell
# Virtuelle Umgebung erstellen
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Abhängigkeiten installieren
pip install -r requirements.txt

# Starten
python run.py
```
Oder einfach doppelt auf **`start.bat`** klicken.

### 3. Als eigenständige `.exe` bauen (PyInstaller)
```powershell
.\.venv\Scripts\pyinstaller.exe --name "Dino-Apple-Transfer" --noconfirm --onedir --windowed --icon "ui/app.ico" --add-data "ui;ui" --collect-all pymobiledevice3 --collect-all webview run.py
```

---

## 🔌 Wichtige Hinweise zum Datenkabel

> ⚠️ **Achtung vor reinen Ladekabeln:**  
> Viele günstige USB-Kabel übertragen nur Strom (+5V / GND), aber keine Daten. Windows erkennt das iPad dann überhaupt nicht. Verwende immer ein vollwertiges **USB-Datenkabel** (z. B. das Original-Apple-Kabel).

1. Kabel anstecken.
2. iPhone/iPad **entsperren**.
3. Auf dem Bildschirm bei **„Diesem Computer vertrauen?“** auf **Vertrauen** tippen und den Geräte-Code eingeben.

---

## 📜 Lizenz
MIT License · Erstellt für die unkomplizierte Dateiübertragung zwischen Windows und Apple-Geräten 🦕
