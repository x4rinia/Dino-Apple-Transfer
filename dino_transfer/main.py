"""Einstiegspunkt für Dino-Apple-Transfer (Fokus auf reine, direkte USB-Kabel-Übertragung)."""
from __future__ import annotations

import json
import logging
import os
import queue
import socket
import subprocess
import sys
import threading
from functools import wraps
from pathlib import Path

import webview
from webview.dom import DOMEventHandler

from . import APP_NAME, DISPLAY_NAME, VERSION
from .config import LOG_FILE, UI_DIR, Settings, setup_logging
from .usb import (
    Job,
    UsbBridge,
    UsbError,
    check_apple_devices,
    get_apple_diagnostics,
    launch_apple_devices_app,
    open_device_in_explorer,
    restart_apple_services,
)

log = logging.getLogger("dino")


def api_method(fn):
    @wraps(fn)
    def wrapper(self, *args, **kwargs):
        try:
            return {"ok": True, "data": fn(self, *args, **kwargs)}
        except UsbError as e:
            return {"ok": False, "error": e.message, "code": e.code, "details": getattr(e, "details", "")}
        except Exception as e:
            log.exception("Fehler in %s", fn.__name__)
            return {"ok": False, "error": str(e) or e.__class__.__name__, "code": "error"}

    return wrapper


def path_info(p: Path) -> dict:
    is_dir = p.is_dir()
    size = 0
    count = 1
    if is_dir:
        count = 0
        for root, _dirs, files in os.walk(p):
            for f in files:
                try:
                    size += (Path(root) / f).stat().st_size
                    count += 1
                except OSError:
                    pass
    elif p.exists():
        size = p.stat().st_size
    return {"path": str(p), "name": p.name, "isDir": is_dir, "size": size, "count": count, "exists": p.exists()}


class Api:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._window = None
        self._events: "queue.Queue[dict]" = queue.Queue()
        self._ready = threading.Event()
        self._usb = UsbBridge(self._emit, allow_wireless=True)
        self._dnd_bound = False
        threading.Thread(target=self._pump, name="dino-events", daemon=True).start()

    # ------------------------------------------------------------ Events -> JS
    def _emit(self, event: dict) -> None:
        self._events.put(event)

    def _pump(self) -> None:
        self._ready.wait()
        while True:
            event = self._events.get()
            try:
                self._window.evaluate_js(f"window.Dino && window.Dino.onEvent({json.dumps(event, ensure_ascii=False)})")
            except Exception:
                log.debug("Event konnte nicht zugestellt werden", exc_info=True)

    def _on_loaded(self) -> None:
        self._ready.set()
        log.info("pywebview on_loaded Event gefeuert")
        try:
            loc = self._window.evaluate_js("window.location.href")
            log.info("Window location: %s", loc)
            t_init = self._window.evaluate_js("typeof init")
            log.info("typeof init: %s", t_init)
            # Init direkt anstoßen falls Event verpasst
            self._window.evaluate_js("if (typeof init === 'function') init();")
        except Exception as e:
            log.warning("evaluate_js Fehler: %s", e)
        if not self._dnd_bound:
            self._dnd_bound = True
            try:
                self._window.dom.document.events.drop += DOMEventHandler(self._on_drop, prevent_default=True)
            except Exception:
                log.warning("DOM drop handler konnte nicht gebunden werden", exc_info=True)

    def _on_drop(self, event: dict) -> None:
        files = (event.get("dataTransfer") or {}).get("files") or []
        paths = [f.get("pywebviewFullPath") for f in files if f.get("pywebviewFullPath")]
        if paths:
            self._emit({"type": "drop", "items": [path_info(Path(p)) for p in paths]})
        elif files:
            self._emit({"type": "toast", "kind": "warn", "text": "Dateien bitte über die Buttons hinzufügen."})

    def _shutdown(self) -> None:
        pass

    # ------------------------------------------------------------ Allgemein
    @api_method
    def get_state(self):
        log.info("API get_state aufgerufen!")
        return {
            "app": APP_NAME,
            "displayName": DISPLAY_NAME,
            "version": VERSION,
            "baseDir": str(self._settings.base_dir),
            "fromDeviceDir": str(self._settings.from_device_dir),
            "lastApp": self._settings.get("last_app"),
            "pcName": socket.gethostname(),
            "logFile": str(LOG_FILE),
        }

    @api_method
    def set_pref(self, key: str, value):
        if key not in ("last_app",):
            raise ValueError("Unbekannte Einstellung")
        self._settings.set(key, value)
        return True

    @api_method
    def pick_files(self):
        res = self._window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=True)
        return [path_info(Path(p)) for p in (res or [])]

    @api_method
    def pick_folder(self):
        res = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        if not res:
            return None
        p = res[0] if isinstance(res, (list, tuple)) else res
        return path_info(Path(p))

    @api_method
    def choose_base_dir(self):
        res = self._window.create_file_dialog(webview.FileDialog.FOLDER, directory=str(self._settings.base_dir))
        if not res:
            return None
        p = res[0] if isinstance(res, (list, tuple)) else res
        self._settings.set("base_dir", str(Path(p)))
        self._settings.ensure_dirs()
        return str(self._settings.base_dir)

    @api_method
    def open_folder(self, which: str = "from"):
        target = self._settings.from_device_dir if which == "from" else self._settings.base_dir
        target.mkdir(parents=True, exist_ok=True)
        os.startfile(str(target))
        return True

    @api_method
    def open_path(self, path: str, reveal: bool = True):
        p = Path(path)
        if reveal and p.exists():
            subprocess.Popen(["explorer", "/select,", str(p)])
        elif p.exists():
            os.startfile(str(p))
        return True

    @api_method
    def open_url(self, url: str):
        if url.startswith(("ms-windows-store:", "https://", "http://")):
            os.startfile(url)
        return True

    # ------------------------------------------------------------ USB & Dateimanager
    @api_method
    def usb_status(self):
        res = self._usb.status()
        log.info("API usb_status -> %s (Gerät: %s)", res.get("state"), (res.get("device") or {}).get("name"))
        return res

    @api_method
    def usb_apps(self):
        return self._usb.list_apps()

    @api_method
    def usb_ensure_dino_folder(self):
        return self._usb.ensure_dino_folder()

    @api_method
    def usb_icons(self, bundles: list):
        return self._usb.app_icons(list(bundles))

    @api_method
    def usb_list(self, bundle: str, path: str = "/Documents"):
        return self._usb.list_dir(bundle, path)

    @api_method
    def usb_mkdir(self, bundle: str, parent: str, name: str):
        return self._usb.mkdir(bundle, parent, name)

    @api_method
    def usb_delete(self, bundle: str, paths: list):
        return self._usb.delete(bundle, list(paths))

    @api_method
    def usb_rename(self, bundle: str, path: str, new_name: str):
        return self._usb.rename(bundle, path, new_name)

    @api_method
    def usb_push(self, bundle: str, app_name: str, paths: list, remote_dir: str):
        return self._usb.start_push(bundle, app_name, list(paths), remote_dir)

    @api_method
    def usb_pull(self, bundle: str, app_name: str, remote_paths: list, local_dir: str = ""):
        return self.usb_pull_to(bundle, app_name, remote_paths)

    @api_method
    def usb_pull_to(self, bundle: str, app_name: str, remote_paths: list):
        res = self._window.create_file_dialog(webview.FileDialog.FOLDER, directory=str(self._settings.base_dir))
        if not res:
            return None
        p = res[0] if isinstance(res, (list, tuple)) else res
        chosen = Path(p)
        if chosen.is_dir():
            self._settings.set("base_dir", str(chosen))
        return self._usb.start_pull(bundle, app_name, list(remote_paths), str(chosen))

    @api_method
    def set_wireless_mode(self, allow: bool):
        self._settings.set("allow_wireless", bool(allow))
        return self._usb.set_allow_wireless(bool(allow))

    @api_method
    def disconnect_wireless(self):
        self._settings.set("allow_wireless", False)
        return self._usb.disconnect_wireless()

    @api_method
    def usb_devices(self):
        return self._usb.get_available_devices()

    @api_method
    def usb_select_device(self, serial: str):
        return self._usb.select_device(serial)

    @api_method
    def stop_connection_service(self):
        return self._usb.stop_connection_service()

    @api_method
    def start_connection_service(self):
        return self._usb.start_connection_service()

    @api_method
    def job_cancel(self, job_id: str):
        self._usb.cancel(job_id)
        return True

    @api_method
    def apple_devices_status(self):
        return check_apple_devices()

    @api_method
    def apple_diagnostics(self):
        return get_apple_diagnostics()

    @api_method
    def launch_apple_devices(self):
        return launch_apple_devices_app()

    @api_method
    def restart_apple_services(self):
        return restart_apple_services()

    @api_method
    def open_device_in_explorer(self):
        return open_device_in_explorer()


def get_webview_storage_path() -> str:
    base = Path(os.environ.get("APPDATA", Path.home())) / APP_NAME / "webview"
    lock_file = base / "EBWebView" / "lockfile"
    if lock_file.exists():
        try:
            with open(lock_file, "r+b"):
                pass
        except (OSError, IOError):
            # Ordner ist durch eine laufende Instanz gesperrt -> Ausweichordner nutzen
            alt = base.parent / f"webview_{os.getpid()}"
            alt.mkdir(parents=True, exist_ok=True)
            return str(alt)
    base.mkdir(parents=True, exist_ok=True)
    return str(base)


def _apply_windows_icon(window_title: str) -> None:
    """Setzt das Taskleisten- und Titelleisten-Icon unter Windows sauber."""
    if sys.platform != "win32":
        return
    import ctypes
    import time

    def _setter():
        ico = UI_DIR / "app.ico"
        if not ico.exists():
            return
        user32 = ctypes.windll.user32
        for _ in range(20):  # bis zu 2 Sekunden warten, bis das Fenster da ist
            hwnd = user32.FindWindowW(None, window_title)
            if hwnd:
                try:
                    IMAGE_ICON = 1
                    LR_LOADFROMFILE = 0x00000010
                    LR_DEFAULTSIZE = 0x00000040
                    WM_SETICON = 0x0080
                    h_icon_sm = user32.LoadImageW(None, str(ico), IMAGE_ICON, 32, 32, LR_LOADFROMFILE)
                    h_icon_lg = user32.LoadImageW(None, str(ico), IMAGE_ICON, 256, 256, LR_LOADFROMFILE)
                    if h_icon_sm:
                        user32.SendMessageW(hwnd, WM_SETICON, 0, h_icon_sm)  # ICON_SMALL
                    if h_icon_lg:
                        user32.SendMessageW(hwnd, WM_SETICON, 1, h_icon_lg)  # ICON_BIG
                    log.info("Windows Taskleisten-Icon erfolgreich zugewiesen")
                    break
                except Exception:
                    pass
            time.sleep(0.1)

    threading.Thread(target=_setter, name="dino-icon-setter", daemon=True).start()


def main() -> None:
    try:
        setup_logging()
        log.info("%s %s startet (USB-Kabel)", DISPLAY_NAME, VERSION)
        log.info("UI_DIR: %s (exists: %s)", UI_DIR, (UI_DIR / "index.html").exists())

        if sys.platform == "win32":
            try:
                import ctypes
                # Windows App ID setzen, damit das Taskleisten-Icon nicht das Standard-Python-Icon nutzt
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Dino.Apple.Transfer.Mascot.2.0")
            except Exception:
                pass

        settings = Settings()
        api = Api(settings)
        sp = get_webview_storage_path()
        log.info("Storage Path: %s", sp)
        win_title = f"{DISPLAY_NAME} 🦕 – USB-Kabelverbindung für iPhone & iPad"
        window = webview.create_window(
            win_title,
            url=(UI_DIR / "index.html").as_uri(),
            js_api=api,
            width=1340,
            height=890,
            min_size=(980, 680),
            background_color="#121212",
            text_select=False,
        )
        api._window = window
        window.events.loaded += api._on_loaded
        window.events.closed += api._shutdown

        _apply_windows_icon(win_title)

        webview.start(
            debug="--debug" in sys.argv,
            private_mode=False,
            storage_path=sp,
        )
        log.info("webview.start() normal beendet")
    except Exception:
        log.exception("FATALER FEHLER IN MAIN:")
        raise


if __name__ == "__main__":
    main()
