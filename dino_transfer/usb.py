"""USB-Brücke zum iPhone und iPad über Apples AFC/house_arrest-Protokoll (via pymobiledevice3).

Unterstützt alle iOS- und iPadOS-Geräte.
Ermöglicht direkten Zugriff auf den Dokumenten-Ordner jeder App mit Dateifreigabe
(z. B. VLC, Documents, Infuse, Acrobat …), die in der Dateien-App unter
„Auf meinem iPhone“ bzw. „Auf meinem iPad“ zu finden sind.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import posixpath
import socket
import subprocess
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable, Optional

from .config import safe_name, unique_local_path

log = logging.getLogger("dino.usb")

DOCS_ROOT = "/Documents"
CHUNK = 1024 * 1024  # 1 MB

FAVORITE_APPS = {
    "org.videolan.vlc-ios": "Musik & Videos – spielt fast jedes Format (MP3, FLAC, MKV, MP4 …)",
    "com.readdle.ReaddleDocsIPad": "Dateimanager mit PDF-Viewer und Mediaplayer",
    "com.readdle.ReaddleDocs": "Dateimanager mit PDF-Viewer und Mediaplayer",
    "com.firecore.infuse": "Videoplayer mit Mediathek",
    "com.firecore.Infuse": "Videoplayer mit Mediathek",
    "com.iqqi.nplayer": "Videoplayer",
    "com.adobe.Adobe-Reader": "PDF-Reader",
    "com.readdle.PDFExpert5": "PDF-Reader und Editor",
    "com.goodiware.GoodReaderIPad": "PDF-Reader und Dateimanager",
    "com.foobar2000.mobile": "Musikplayer (FLAC, MP3, ALAC)",
}


class UsbError(Exception):
    def __init__(self, code: str, message: str, details: str = ""):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


def _normalize_remote(path: str, allow_root_paths: bool = False) -> str:
    path = (path or ("" if allow_root_paths else DOCS_ROOT)).replace("\\", "/")
    if allow_root_paths:
        path = path.strip("/")
        return path
    if not path.startswith("/"):
        path = posixpath.join(DOCS_ROOT, path)
    path = posixpath.normpath(path)
    if path != DOCS_ROOT and not path.startswith(DOCS_ROOT + "/"):
        raise UsbError("bad_path", "Ungültiger Pfad auf dem Gerät.")
    return path


class Job:
    def __init__(self, kind: str, title: str, emit: Callable[[dict], None]):
        self.id = uuid.uuid4().hex[:10]
        self.kind = kind
        self.title = title
        self.cancelled = False
        self.bytes_total = 0
        self.bytes_done = 0
        self.files_total = 0
        self.files_done = 0
        self.current = ""
        self.started = time.time()
        self._emit = emit
        self._last_emit = 0.0

    def as_dict(self, state: str = "running", **extra) -> dict:
        elapsed = max(time.time() - self.started, 0.001)
        return {
            "type": "job",
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "state": state,
            "bytesTotal": self.bytes_total,
            "bytesDone": self.bytes_done,
            "filesTotal": self.files_total,
            "filesDone": self.files_done,
            "current": self.current,
            "speed": self.bytes_done / elapsed,
            **extra,
        }

    def report(self, force: bool = False) -> None:
        now = time.time()
        if force or now - self._last_emit > 0.15:
            self._last_emit = now
            self._emit(self.as_dict())

    def finish(self, state: str, **extra) -> None:
        self._emit(self.as_dict(state, **extra))


class UsbBridge:
    def __init__(self, emit: Callable[[dict], None], allow_wireless: bool = True):
        self._emit = emit
        self._ld_lock = None
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, name="dino-usb", daemon=True)
        self._thread.start()
        self._lockdown = None
        self._serial: Optional[str] = None
        self._selected_serial: Optional[str] = None
        self._conn_mode: str = "usb"
        self._cable_plugged: bool = False
        self._wifi_available: bool = False
        self._had_cable_while_paused: bool = False
        self.allow_wireless: bool = allow_wireless
        self.service_paused: bool = False
        self._icons: dict[str, Optional[str]] = {}
        self._device_cache: dict[str, dict] = {}
        self.jobs: dict[str, Job] = {}
        self._active_transfer = False

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    @property
    def ld_lock(self) -> asyncio.Lock:
        if self._ld_lock is None:
            self._ld_lock = asyncio.Lock()
        return self._ld_lock

    def _call(self, coro, timeout: Optional[float] = 60):
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
        try:
            return fut.result(timeout)
        except UsbError:
            raise
        except Exception as e:
            raise self._translate(e) from e

    def _translate(self, e: BaseException) -> UsbError:
        from pymobiledevice3 import exceptions as ex

        if isinstance(e, UsbError):
            return e
        if isinstance(e, ex.ConnectionFailedToUsbmuxdError):
            return UsbError(
                "no_driver",
                "Apple-Dienst antwortet nicht.",
                "Der Apple-Hintergrunddienst (usbmuxd) läuft nicht. Bitte 'Apple Geräte' oder iTunes öffnen oder im Diagnose-Menü den Dienst neu starten.",
            )
        if isinstance(e, ex.PasswordRequiredError):
            return UsbError("locked", "Bitte dein Apple-Gerät entsperren.", "Das Gerät ist mit Code gesperrt.")
        if isinstance(e, ex.PairingDialogResponsePendingError):
            return UsbError(
                "trust",
                "Auf dem iPhone/iPad 'Diesem Computer vertrauen?' bestätigen.",
                "Tippe auf deinem Bildschirm auf 'Vertrauen' und gib deinen Geräte-Code ein.",
            )
        if isinstance(e, ex.UserDeniedPairingError):
            return UsbError(
                "denied",
                "Vertrauen wurde abgelehnt.",
                "Kabel kurz abziehen, wieder anstecken und 'Diesem Computer vertrauen' wählen.",
            )
        if isinstance(e, (ex.NoDeviceConnectedError, ex.DeviceNotFoundError)):
            return UsbError("no_device", "Kein Apple-Gerät (iPhone/iPad) per USB gefunden.")
        if isinstance(e, ex.AppNotInstalledError):
            return UsbError("app_missing", "Diese App ist nicht mehr auf dem Gerät installiert.")
        if isinstance(e, ex.AfcFileNotFoundError):
            return UsbError("not_found", "Datei oder Ordner wurde auf dem Gerät nicht gefunden.")
        if isinstance(e, ex.AfcException):
            return UsbError("afc", f"Zugriff verweigert ({e}).")
        if isinstance(e, (asyncio.TimeoutError, TimeoutError)):
            return UsbError("timeout", "Das Gerät hat nicht rechtzeitig geantwortet.")

        # Bei Verbindungsabbruch Lockdown zurücksetzen
        self._lockdown = None
        log.warning("USB-Verbindungsfehler: %s (%s)", e.__class__.__name__, e)
        return UsbError("error", f"USB-Verbindung kurz gestört ({e.__class__.__name__}). Dino versucht eine automatische Wiederverbindung.")

    async def _get_lockdown(self):
        from pymobiledevice3 import usbmux
        from pymobiledevice3.lockdown import create_using_usbmux

        devices = await usbmux.list_devices()
        usb_devices = [d for d in devices if d.is_usb]
        net_devices = [d for d in devices if not d.is_usb]

        # Wenn zuvor ein Gerät verbunden war und list_devices() kurzzeitig leer zurückkommt:
        if not usb_devices and (not net_devices or not self.allow_wireless) and self._lockdown is not None and not self.service_paused:
            await asyncio.sleep(0.35)
            try:
                devices = await usbmux.list_devices()
                usb_devices = [d for d in devices if d.is_usb]
                net_devices = [d for d in devices if not d.is_usb]
            except Exception:
                pass

        self._cable_plugged = len(usb_devices) > 0
        self._wifi_available = len(net_devices) > 0

        if self.service_paused:
            await self._drop_lockdown()
            # Wenn das Kabel während der Pause abgezogen und danach frisch wieder eingesteckt wird, automatisch freigeben:
            if not self._cable_plugged:
                self._had_cable_while_paused = False
                raise UsbError(
                    "paused",
                    "Verbindung getrennt & USB-Kabel ist abgezogen ✅",
                    "Das Kabel wurde sicher entfernt. Stecke das USB-Kabel wieder an, wähle ein Gerät aus oder klicke auf „Jetzt wieder verbinden“.",
                )
            raise UsbError(
                "paused",
                "Verbindung manuell getrennt ✅",
                "Die Sitzung zum Apple-Gerät ist beendet. Du kannst das USB-Kabel jetzt sicher abziehen, ein anderes Gerät wählen oder auf „Jetzt wieder verbinden“ klicken.",
            )

        target = None
        desired_conn = "USB"
        conn_mode = "usb"

        # 1. Bevorzugt das vom Nutzer ausgewählte Gerät berücksichtigen
        if self._selected_serial:
            matched_usb = next((d for d in usb_devices if d.serial == self._selected_serial), None)
            matched_net = next((d for d in net_devices if d.serial == self._selected_serial), None) if self.allow_wireless else None
            if matched_usb:
                target = matched_usb
                desired_conn = "USB"
                conn_mode = "usb"
            elif matched_net:
                target = matched_net
                desired_conn = "Network"
                conn_mode = "wifi"

        # 2. Standardauswahl: USB zuerst, sonst Funk (falls erlaubt)
        if target is None:
            if usb_devices:
                target = usb_devices[0]
                desired_conn = "USB"
                conn_mode = "usb"
            elif net_devices and self.allow_wireless:
                target = net_devices[0]
                desired_conn = "Network"
                conn_mode = "wifi"
            else:
                await self._drop_lockdown()
                if net_devices and not self.allow_wireless:
                    raise UsbError(
                        "no_device",
                        "Apple-Funk ist ausgeschaltet (Nur USB-Kabel aktiv).",
                        "Stecke ein USB-Kabel an, wähle ein Gerät aus oder schalte Apple-Funk ein, um kabellos zu verbinden.",
                    )
                raise UsbError("no_device", "Kein Apple-Gerät per USB-Kabel oder Apple-Funk verbunden.")

        serial = target.serial

        if self._lockdown is not None and self._serial == serial and self._conn_mode == conn_mode:
            return self._lockdown

        await self._drop_lockdown()
        async with self.ld_lock:
            self._lockdown = await create_using_usbmux(serial=serial, autopair=True, connection_type=desired_conn)
        self._serial = serial
        self._selected_serial = serial
        self._conn_mode = conn_mode
        dev_name = self._lockdown.short_info.get("DeviceName") or "Apple-Gerät"
        dev_class = self._lockdown.short_info.get("DeviceClass") or "Gerät"
        dev_model = self._lockdown.short_info.get("ProductType") or ""
        self._device_cache[serial] = {"name": dev_name, "class": dev_class, "model": dev_model}
        log.info("Verbunden mit %s (%s)", dev_name, conn_mode.upper())
        return self._lockdown

    async def _resolve_device_info(self, serial: str, conn_type: str) -> dict:
        if serial in self._device_cache:
            return self._device_cache[serial]
        from pymobiledevice3.lockdown import create_using_usbmux
        try:
            ld = await create_using_usbmux(serial=serial, connection_type=conn_type)
            info = {
                "name": ld.short_info.get("DeviceName") or "Apple-Gerät",
                "model": ld.short_info.get("ProductType") or "",
                "class": ld.short_info.get("DeviceClass") or "Gerät",
            }
            try:
                await ld.close()
            except Exception:
                pass
            self._device_cache[serial] = info
            return info
        except Exception:
            return {"name": "Apple-Gerät", "model": "", "class": "Gerät"}

    async def _collect_available_devices(self) -> list[dict]:
        from pymobiledevice3 import usbmux
        try:
            devices = await usbmux.list_devices()
        except Exception:
            return []

        result = []
        seen = set()
        for d in sorted(devices, key=lambda x: not x.is_usb):
            if d.serial in seen:
                continue
            seen.add(d.serial)
            conn_type = "USB" if d.is_usb else "Network"
            info = self._device_cache.get(d.serial)
            if not info and (d.is_usb or self.allow_wireless):
                info = await self._resolve_device_info(d.serial, conn_type)
            if not info:
                info = {"name": "Apple-Gerät", "model": "", "class": "Gerät"}
            dev_class = info.get("class", "Gerät")
            is_active = (d.serial == self._serial and not self.service_paused and self._lockdown is not None)
            result.append({
                "serial": d.serial,
                "isUsb": d.is_usb,
                "connMode": "usb" if d.is_usb else "wifi",
                "name": info.get("name") or "Apple-Gerät",
                "model": info.get("model") or "",
                "class": dev_class,
                "isIPhone": "iphone" in dev_class.lower(),
                "active": is_active,
            })
        return result

    def get_available_devices(self) -> list[dict]:
        return self._call(self._collect_available_devices(), timeout=10)

    def select_device(self, serial: str) -> dict:
        log.info("Gerätewechsel zu: %s", serial)
        self._selected_serial = serial
        self.allow_wireless = True
        self.service_paused = False
        try:
            self._call(self._drop_lockdown(), timeout=5)
        except Exception:
            pass
        return self.status()

    def disconnect_wireless(self) -> dict:
        log.info("Apple-Funk wird getrennt & ausgeschaltet")
        self.allow_wireless = False
        self.service_paused = False
        if self._conn_mode == "wifi":
            try:
                self._call(self._drop_lockdown(), timeout=5)
            except Exception:
                pass
        return self.status()

    def set_allow_wireless(self, allow: bool) -> dict:
        self.allow_wireless = bool(allow)
        if not self.allow_wireless and self._conn_mode == "wifi":
            try:
                self._call(self._drop_lockdown(), timeout=5)
            except Exception:
                pass
        return {"ok": True, "allowWireless": self.allow_wireless}

    def stop_connection_service(self) -> dict:
        """Trennt die aktive USB-Sitzung sofort, damit man testweise neu verbinden oder das Kabel sicher abziehen kann."""
        self.service_paused = True
        self._had_cable_while_paused = self._cable_plugged
        try:
            self._call(self._drop_lockdown(), timeout=5)
        except Exception:
            pass
        return {"ok": True, "servicePaused": True}

    def start_connection_service(self) -> dict:
        """Hebt die manuelle Trennung auf und verbindet sich wieder (startet bei Bedarf auch den Apple-Hintergrunddienst)."""
        self.service_paused = False
        self._had_cable_while_paused = False
        # Prüfen ob Port 27015 (AppleMobileDeviceProcess) offen ist, sonst starten
        if not self._is_driver_running():
            try:
                cmd = (
                    "$pkg = Get-AppxPackage *AppleDevices* -ErrorAction SilentlyContinue; "
                    "if ($pkg) { "
                    "  $exe = Join-Path $pkg.InstallLocation 'AppleMobileDeviceProcess.exe'; "
                    "  if (Test-Path $exe) { Start-Process -FilePath $exe -WindowStyle Hidden } "
                    "}"
                )
                subprocess.run(["powershell", "-NoProfile", "-Command", cmd], timeout=5)
            except Exception:
                pass
        return {"ok": True, "servicePaused": False}

    @staticmethod
    def _is_driver_running() -> bool:
        try:
            with socket.create_connection(("127.0.0.1", 27015), timeout=0.3):
                return True
        except OSError:
            return False

    async def _drop_lockdown(self) -> None:
        ld, self._lockdown, self._serial = self._lockdown, None, None
        self._icons.clear()
        if ld is not None:
            try:
                await ld.close()
            except Exception:
                pass

    async def _start(self, svc):
        async with self.ld_lock:
            await svc.connect()
        return svc

    @asynccontextmanager
    async def _house(self, bundle_id: str):
        if bundle_id == "__photos__":
            from pymobiledevice3.services.afc import AfcService

            ld = await self._get_lockdown()
            svc = AfcService(ld)
            await self._start(svc)
            try:
                await svc.__aenter__()
                yield svc
            finally:
                try:
                    await svc.aclose()
                except Exception:
                    pass
        else:
            from pymobiledevice3.services.house_arrest import VEND_DOCUMENTS, HouseArrestService

            ld = await self._get_lockdown()
            svc = HouseArrestService(ld, documents_only=True)
            await self._start(svc)
            try:
                await svc.send_command(bundle_id, VEND_DOCUMENTS)
                await svc.__aenter__()
                yield svc
            finally:
                try:
                    await svc.aclose()
                except Exception:
                    pass

    # ------------------------------------------------------------------ Status
    def status(self) -> dict:
        driver_running = self._is_driver_running()
        try:
            res = self._call(self._status(), timeout=15)
        except UsbError as e:
            res = {"state": e.code, "message": e.message, "details": e.details}
        except Exception as e:
            res = {"state": "error", "message": str(e)}

        try:
            res["availableDevices"] = self._call(self._collect_available_devices(), timeout=5)
        except Exception:
            res["availableDevices"] = []

        res["allowWireless"] = self.allow_wireless
        res["servicePaused"] = self.service_paused
        res["selectedSerial"] = self._selected_serial
        res["cablePlugged"] = self._cable_plugged
        res["wifiAvailable"] = self._wifi_available
        res["driverRunning"] = driver_running
        if "device" in res and isinstance(res["device"], dict):
            res["device"]["driverRunning"] = driver_running
            res["device"]["cablePlugged"] = self._cable_plugged
            res["device"]["allowWireless"] = self.allow_wireless
            res["device"]["connMode"] = self._conn_mode
            res["device"]["selectedSerial"] = self._selected_serial
        return res

    async def _status(self) -> dict:
        # Während eines aktiven Transfers keine störenden Abfragen senden
        if self._active_transfer and self._lockdown is not None:
            return {
                "state": "ready",
                "message": "Übertragung aktiv",
                "device": {
                    "name": self._lockdown.short_info.get("DeviceName") or "Apple-Gerät",
                    "class": self._lockdown.short_info.get("DeviceClass") or "Gerät",
                    "ios": self._lockdown.short_info.get("ProductVersion") or "",
                    "connMode": self._conn_mode,
                    "wifiAvailable": self._wifi_available,
                    "allowWireless": self.allow_wireless,
                    "transferActive": True,
                },
            }

        ld = await self._get_lockdown()
        info = dict(ld.short_info)
        name = info.get("DeviceName")
        if not name:
            try:
                name = await ld.get_value(key="DeviceName")
            except Exception:
                name = None

        dev_class = info.get("DeviceClass") or "Apple-Gerät"
        device = {
            "name": name or info.get("DeviceName") or dev_class,
            "model": info.get("ProductType") or "",
            "ios": info.get("ProductVersion") or "",
            "class": dev_class,
            "isIPhone": "iphone" in dev_class.lower(),
            "connMode": self._conn_mode,
            "wifiAvailable": self._wifi_available,
            "allowWireless": self.allow_wireless,
            "transferActive": self._active_transfer,
        }
        try:
            device["battery"] = await ld.get_value(domain="com.apple.mobile.battery", key="BatteryCurrentCapacity")
        except Exception:
            pass
        try:
            disk = await ld.get_value(domain="com.apple.disk_usage")
            device["diskTotal"] = disk.get("TotalDataCapacity") or disk.get("TotalDiskCapacity")
            device["diskFree"] = disk.get("TotalDataAvailable")
        except Exception:
            pass
        return {"state": "ready", "message": "Verbunden", "device": device}

    def list_apps(self) -> list[dict]:
        return self._call(self._list_apps(), timeout=45)

    async def _list_apps(self) -> list[dict]:
        from pymobiledevice3.services.installation_proxy import InstallationProxyService

        ld = await self._get_lockdown()
        svc = await self._start(InstallationProxyService(lockdown=ld))
        try:
            apps = await svc.get_apps(application_type="User")
        finally:
            await svc.close()
        result = []
        for bundle_id, info in apps.items():
            if not info.get("UIFileSharingEnabled"):
                continue
            name = info.get("CFBundleDisplayName") or info.get("CFBundleName") or bundle_id
            result.append({
                "bundle": bundle_id,
                "name": name,
                "version": info.get("CFBundleShortVersionString") or "",
                "favorite": bundle_id in FAVORITE_APPS,
                "hint": FAVORITE_APPS.get(bundle_id, ""),
            })
        result.sort(key=lambda a: (not a["favorite"], a["name"].lower()))
        return result

    def app_icons(self, bundle_ids: list[str]) -> dict[str, Optional[str]]:
        return self._call(self._app_icons(bundle_ids), timeout=60)

    async def _app_icons(self, bundle_ids: list[str]) -> dict[str, Optional[str]]:
        from pymobiledevice3.services.springboard import SpringBoardServicesService

        missing = [b for b in bundle_ids if b not in self._icons]
        if missing:
            ld = await self._get_lockdown()
            svc = await self._start(SpringBoardServicesService(lockdown=ld))
            try:
                for b in missing:
                    try:
                        png = await svc.get_icon_pngdata(b)
                        self._icons[b] = "data:image/png;base64," + base64.b64encode(png).decode() if png else None
                    except Exception:
                        self._icons[b] = None
            finally:
                await svc.close()
        return {b: self._icons.get(b) for b in bundle_ids}

    def ensure_dino_folder(self) -> dict:
        """Stellt sicher, dass der Dino-Transfer-Hauptordner auf dem iPad existiert."""
        apps = self.list_apps()
        if not apps:
            raise UsbError("no_apps", "Keine App mit Dateifreigabe auf dem iPad gefunden.")

        # Bevorzuge bekannte Apps (Chrome, Documents, VLC) als Host-Container
        primary = apps[0]
        fav_candidates = ("org.videolan.vlc-ios", "com.readdle.ReaddleDocsIPad", "com.readdle.ReaddleDocs", "com.google.chrome.ios")
        for fav in fav_candidates:
            matched = next((a for a in apps if a["bundle"] == fav), None)
            if matched:
                primary = matched
                break

        dino_path = "/Documents/Dino-Transfer"
        try:
            self.mkdir(primary["bundle"], "/Documents", "Dino-Transfer")
        except Exception:
            pass

        return {
            "bundle": primary["bundle"],
            "appName": primary["name"],
            "path": dino_path,
            "name": "Dino-Transfer",
        }

    # ------------------------------------------------------------------ Dateien
    def list_dir(self, bundle_id: str, path: str) -> dict:
        is_photos = bundle_id == "__photos__"
        return self._call(self._list_dir(bundle_id, _normalize_remote(path, allow_root_paths=is_photos)), timeout=45)

    async def _list_dir(self, bundle_id: str, path: str) -> dict:
        entries = []
        async with self._house(bundle_id) as afc:
            for name in await afc.listdir(path):
                if name in (".", "..") or name.startswith("."):
                    continue
                full = posixpath.join(path, name)
                try:
                    st = await afc.stat(full)
                except Exception:
                    continue
                is_dir = st.get("st_ifmt") == "S_IFDIR"
                entries.append({
                    "name": name,
                    "path": full,
                    "isDir": is_dir,
                    "size": 0 if is_dir else st.get("st_size", 0),
                    "mtime": st["st_mtime"].timestamp() if st.get("st_mtime") else None,
                })
        entries.sort(key=lambda e: (not e["isDir"], e["name"].lower()))
        return {"path": path, "entries": entries}

    def mkdir(self, bundle_id: str, parent: str, name: str) -> str:
        is_photos = bundle_id == "__photos__"
        norm_parent = _normalize_remote(parent, allow_root_paths=is_photos)
        sub = posixpath.join(norm_parent, safe_name(name, "Neuer Ordner"))
        path = _normalize_remote(sub, allow_root_paths=is_photos)
        return self._call(self._mkdir(bundle_id, path))

    async def _mkdir(self, bundle_id: str, path: str) -> str:
        async with self._house(bundle_id) as afc:
            await afc.makedirs(path)
        return path

    def delete(self, bundle_id: str, paths: list[str]) -> int:
        is_photos = bundle_id == "__photos__"
        norm = [_normalize_remote(p, allow_root_paths=is_photos) for p in paths]
        if not is_photos and DOCS_ROOT in norm:
            raise UsbError("bad_path", "Der Hauptordner der App kann nicht gelöscht werden.")
        if is_photos and any(p in ("", "/", "DCIM", "DCIM/") for p in norm):
            raise UsbError("bad_path", "Der Hauptordner der Mediengalerie kann nicht gelöscht werden.")
        return self._call(self._delete(bundle_id, norm), timeout=300)

    async def _delete(self, bundle_id: str, paths: list[str]) -> int:
        async with self._house(bundle_id) as afc:
            for p in paths:
                await afc.rm(p, force=True)
        return len(paths)

    def rename(self, bundle_id: str, path: str, new_name: str) -> str:
        is_photos = bundle_id == "__photos__"
        src = _normalize_remote(path, allow_root_paths=is_photos)
        dst = _normalize_remote(posixpath.join(posixpath.dirname(src), safe_name(new_name)), allow_root_paths=is_photos)
        return self._call(self._rename(bundle_id, src, dst))

    async def _rename(self, bundle_id: str, src: str, dst: str) -> str:
        async with self._house(bundle_id) as afc:
            if await afc.exists(dst):
                raise UsbError("exists", "Es gibt schon eine Datei mit diesem Namen.")
            await afc.rename(src, dst)
        return dst

    # ------------------------------------------------------------------ Übertragungen
    def start_push(self, bundle_id: str, app_name: str, local_paths: list[str], remote_dir: str) -> str:
        is_photos = bundle_id == "__photos__"
        remote_dir = _normalize_remote(remote_dir, allow_root_paths=is_photos)
        count = len(local_paths)
        title = f"{count} Element{'e' if count != 1 else ''} → {app_name}"
        job = Job("push", title, self._emit)
        self.jobs[job.id] = job
        asyncio.run_coroutine_threadsafe(self._run_job(job, self._push(job, bundle_id, local_paths, remote_dir)), self.loop)
        return job.id

    def start_pull(self, bundle_id: str, app_name: str, remote_paths: list[str], local_dir: str) -> str:
        is_photos = bundle_id == "__photos__"
        norm = [_normalize_remote(p, allow_root_paths=is_photos) for p in remote_paths]
        count = len(norm)
        title = f"{count} Element{'e' if count != 1 else ''} von {app_name} → PC"
        job = Job("pull", title, self._emit)
        self.jobs[job.id] = job
        asyncio.run_coroutine_threadsafe(self._run_job(job, self._pull(job, bundle_id, norm, Path(local_dir))), self.loop)
        return job.id

    def cancel(self, job_id: str) -> None:
        job = self.jobs.get(job_id)
        if job:
            job.cancelled = True

    async def _run_job(self, job: Job, coro) -> None:
        try:
            job.report(force=True)
            self._active_transfer = True
            extra = await coro or {}
            job.finish("cancelled" if job.cancelled else "done", **extra)
        except Exception as e:
            err = self._translate(e)
            job.finish("error", error=err.message)
        finally:
            self._active_transfer = False
            self.jobs.pop(job.id, None)

    async def _unique_remote(self, afc, path: str) -> str:
        if not await afc.exists(path):
            return path
        root, ext = posixpath.splitext(path)
        i = 1
        while await afc.exists(f"{root} ({i}){ext}"):
            i += 1
        return f"{root} ({i}){ext}"

    async def _push(self, job: Job, bundle_id: str, local_paths: list[str], remote_dir: str) -> dict:
        dirs: list[str] = []
        files: list[tuple[Path, str, int]] = []
        for raw in local_paths:
            p = Path(raw)
            if p.is_dir():
                base = posixpath.join(remote_dir, safe_name(p.name, "Ordner"))
                dirs.append(base)
                for root, subdirs, names in os.walk(p):
                    subdirs[:] = [d for d in subdirs if not d.startswith(".")]
                    rel_parts = [safe_name(x) for x in Path(root).relative_to(p).parts]
                    remote_root = posixpath.join(base, *rel_parts) if rel_parts else base
                    if rel_parts:
                        dirs.append(remote_root)
                    for n in names:
                        if n.startswith(".") or n.lower() in ("thumbs.db", "desktop.ini"):
                            continue
                        lp = Path(root) / n
                        files.append((lp, posixpath.join(remote_root, safe_name(n)), lp.stat().st_size))
            elif p.is_file():
                files.append((p, posixpath.join(remote_dir, safe_name(p.name)), p.stat().st_size))
        job.files_total = len(files)
        job.bytes_total = sum(f[2] for f in files)
        job.report(force=True)

        async with self._house(bundle_id) as afc:
            for d in dirs:
                await afc.makedirs(d)
            for local, remote, _size in files:
                if job.cancelled:
                    break
                remote = await self._unique_remote(afc, remote)
                job.current = local.name
                job.report(force=True)
                handle = await afc.fopen(remote, "w")
                ok = False
                try:
                    with open(local, "rb") as f:
                        while True:
                            if job.cancelled:
                                break
                            chunk = f.read(CHUNK)
                            if not chunk:
                                ok = True
                                break
                            await afc.fwrite(handle, chunk)
                            job.bytes_done += len(chunk)
                            job.report()
                finally:
                    await afc.fclose(handle)
                if not ok:
                    try:
                        await afc.rm_single(remote, force=True)
                    except Exception:
                        pass
                    break
                job.files_done += 1
        return {"remoteDir": remote_dir}

    async def _pull(self, job: Job, bundle_id: str, remote_paths: list[str], local_dir: Path) -> dict:
        local_dir.mkdir(parents=True, exist_ok=True)
        async with self._house(bundle_id) as afc:
            files: list[tuple[str, Path, int]] = []
            for rp in remote_paths:
                st = await afc.stat(rp)
                name = safe_name(posixpath.basename(rp))
                if st.get("st_ifmt") == "S_IFDIR":
                    target_root = unique_local_path(local_dir / name)
                    async for root, _dirs, names in afc.walk(rp):
                        rel = posixpath.relpath(root, rp)
                        rel_parts = [] if rel == "." else [safe_name(x) for x in rel.split("/")]
                        for n in names:
                            if n.startswith("."):
                                continue
                            full = posixpath.join(root, n)
                            size = (await afc.stat(full)).get("st_size", 0)
                            files.append((full, target_root.joinpath(*rel_parts, safe_name(n)), size))
                    target_root.mkdir(parents=True, exist_ok=True)
                else:
                    files.append((rp, unique_local_path(local_dir / name), st.get("st_size", 0)))
            job.files_total = len(files)
            job.bytes_total = sum(f[2] for f in files)
            job.report(force=True)

            for remote, local, size in files:
                if job.cancelled:
                    break
                local.parent.mkdir(parents=True, exist_ok=True)
                local = unique_local_path(local)
                part = local.with_name(local.name + ".dinopart")
                job.current = local.name
                job.report(force=True)
                handle = await afc.fopen(remote, "r")
                ok = False
                try:
                    with open(part, "wb") as f:
                        remaining = size
                        while remaining > 0 and not job.cancelled:
                            chunk = await afc.fread(handle, min(CHUNK, remaining))
                            if not chunk:
                                break
                            f.write(chunk)
                            remaining -= len(chunk)
                            job.bytes_done += len(chunk)
                            job.report()
                        ok = not job.cancelled
                finally:
                    await afc.fclose(handle)
                if ok:
                    os.replace(part, local)
                    job.files_done += 1
                else:
                    part.unlink(missing_ok=True)
                    break
        return {"localDir": str(local_dir)}


# ---------------------------------------------------------------------- Diagnose & Steuerung
def check_usbmuxd_port() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 27015), timeout=1.0):
            return True
    except OSError:
        return False


def get_apple_diagnostics() -> dict:
    port_open = check_usbmuxd_port()

    # Prozesse prüfen
    running_processes = []
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", "Get-Process | Where-Object { $_.ProcessName -match 'Apple|iTunes|usbmux' } | Select-Object -ExpandProperty ProcessName"],
            text=True,
            timeout=5,
        )
        running_processes = list(set(p.strip() for p in out.splitlines() if p.strip()))
    except Exception:
        pass

    # Windows-Dienst prüfen
    service_status = "Nicht vorhanden"
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", "(Get-Service -Name 'Apple Mobile Device Service' -ErrorAction SilentlyContinue).Status"],
            text=True,
            timeout=5,
        ).strip()
        if out:
            service_status = out
    except Exception:
        pass

    # Hardware/PnP Geräte prüfen
    pnp_devices = []
    try:
        cmd = "Get-PnpDevice -FriendlyName '*Apple*' -ErrorAction SilentlyContinue | Where-Object { $_.Status -eq 'OK' } | Select-Object -ExpandProperty FriendlyName"
        out = subprocess.check_output(["powershell", "-NoProfile", "-Command", cmd], text=True, timeout=5)
        pnp_devices = [d.strip() for d in out.splitlines() if d.strip()]
    except Exception:
        pass

    # Prüfen ob Apple Devices Store App installiert ist
    apple_devices_installed = False
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", "(Get-AppxPackage *AppleDevices* -ErrorAction SilentlyContinue).Name"],
            text=True,
            timeout=5,
        ).strip()
        apple_devices_installed = bool(out)
    except Exception:
        pass

    # USB Tethering Adapter prüfen
    tethering_active = False
    try:
        import ifaddr
        for adapter in ifaddr.get_adapters():
            name = (adapter.nice_name or adapter.name).lower()
            if "apple" in name or "mobile device ethernet" in name or "iphone" in name or "ipad" in name:
                if any(isinstance(ip.ip, str) and not ip.ip.startswith("169.254.") for ip in adapter.ips):
                    tethering_active = True
                    break
    except Exception:
        pass

    return {
        "port27015": port_open,
        "runningProcesses": running_processes,
        "serviceStatus": service_status,
        "pnpDevices": pnp_devices,
        "appleDevicesInstalled": apple_devices_installed,
        "tetheringActive": tethering_active,
    }


def check_apple_devices() -> dict:
    installed = False
    running = False
    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "(Get-AppxPackage *AppleDevices* -ErrorAction SilentlyContinue).Name"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        installed = bool(res.stdout.strip())
    except Exception:
        pass

    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "@(Get-Process -Name AppleDevices -ErrorAction SilentlyContinue).Count"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        running = int(res.stdout.strip() or 0) > 0
    except Exception:
        pass

    return {"installed": installed, "running": running}


def launch_apple_devices_app() -> bool:
    try:
        # 1. Startet die installierte Windows Store App "Apple Geräte" sauber über die App-ID
        res = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "$pkg = Get-AppxPackage *AppleDevices* -ErrorAction SilentlyContinue; if ($pkg) { Start-Process \"shell:AppsFolder\\$($pkg.PackageFamilyName)!App\" }",
            ],
            capture_output=True,
            text=True,
            timeout=6,
        )
        # Prüfen ob Prozess gestartet wurde
        chk = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "@(Get-Process -Name AppleDevices -ErrorAction SilentlyContinue).Count"],
            capture_output=True,
            text=True,
            timeout=4,
        )
        if int(chk.stdout.strip() or 0) > 0:
            return True
    except Exception:
        pass

    try:
        # 2. Fallback: Microsoft Store App direkt auf der "Apple Geräte" Seite öffnen
        os.startfile("ms-windows-store://pdp/?productid=9NP83LWLPZ9K")
        return True
    except Exception:
        pass

    try:
        # 3. Web-Fallback
        os.startfile("https://apps.microsoft.com/detail/9np83lwlpz9k")
        return True
    except Exception:
        return False


def restart_apple_services() -> dict:
    """Beendet hängende Apple-Prozesse, damit usbmux neu anbinden kann."""
    killed = []
    try:
        cmd = "Stop-Process -Name AppleDevices,AppleMobileDeviceProcess,AppleMobileDeviceHelper -Force -ErrorAction SilentlyContinue"
        subprocess.run(["powershell", "-NoProfile", "-Command", cmd], timeout=5)
        killed.append("Apple-Prozesse zurückgesetzt")
    except Exception:
        pass

    # Falls Dienst existiert, neu starten
    try:
        cmd = "Restart-Service -Name 'Apple Mobile Device Service' -ErrorAction SilentlyContinue"
        subprocess.run(["powershell", "-NoProfile", "-Command", cmd], timeout=5)
        killed.append("Apple Mobile Device Service neu gestartet")
    except Exception:
        pass

    # Starte Apple Devices wieder an
    try:
        launch_apple_devices_app()
    except Exception:
        pass

    return {"ok": True, "actions": killed}


def open_device_in_explorer() -> bool:
    """Öffnet 'Dieser PC', wo das iPhone/iPad im MTP-Modus für DCIM-Fotos zu sehen ist."""
    try:
        subprocess.Popen(["explorer", "shell:MyComputerFolder"])
        return True
    except Exception:
        return False
