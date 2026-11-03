"""Lokaler WLAN & USB-Tethering Server für Dino-apple-transfer.

Ermöglicht den schnellen Dateiaustausch mit iPhone und iPad über Safari:
1. Im lokalen WLAN (Heimnetzwerk)
2. Per USB-Tethering (Persönlicher Hotspot über USB-Kabel) – maximale USB-Geschwindigkeit ohne Router.

Vollständige Unterstützung für iOS/iPadOS Safari (Range-Requests, Video-Streaming, Uploads).
"""
from __future__ import annotations

import json
import logging
import mimetypes
import os
import re
import socket
import threading
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Optional

from .config import UI_DIR, Settings, safe_name, unique_local_path

log = logging.getLogger("dino.wlan")

CHUNK = 1024 * 1024
STATIC_FILES = {
    "ipad.css", "ipad.js", "favicon.svg", "favicon.png",
    "dino_hero.png", "dino_full.png", "app.ico", "anky.svg"
}

for ext, mime in {
    ".mkv": "video/x-matroska",
    ".mp4": "video/mp4",
    ".m4v": "video/x-m4v",
    ".mov": "video/quicktime",
    ".flac": "audio/flac",
    ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".epub": "application/epub+zip",
    ".pdf": "application/pdf",
    ".webp": "image/webp",
    ".heic": "image/heic",
}.items():
    mimetypes.add_type(mime, ext)


def local_addresses() -> list[dict]:
    """Alle IPv4-Adressen des PCs: USB-Tethering zuerst, dann WLAN, dann LAN."""
    primary = None
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            primary = s.getsockname()[0]
    except OSError:
        pass

    result: list[dict] = []
    seen = set()

    try:
        import ifaddr

        for adapter in ifaddr.get_adapters():
            name = (adapter.nice_name or adapter.name).lower()
            for ip in adapter.ips:
                if not isinstance(ip.ip, str):
                    continue
                ip_str = ip.ip
                if ip_str.startswith(("127.", "169.254.")) or ip_str in seen:
                    continue
                seen.add(ip_str)

                label = adapter.nice_name or adapter.name

                # Erkennung von Apple USB-Tethering
                is_apple_tether = (
                    "apple" in name
                    or "mobile device ethernet" in name
                    or ip_str.startswith("172.20.10.")
                )
                if is_apple_tether:
                    kind = "tethering"
                    label = "Apple USB-Kabel (Tethering - Highspeed)"
                elif re.search(r"wi-?fi|wlan|wireless|802\.11", name):
                    kind = "wlan"
                    label = f"WLAN: {label}"
                else:
                    kind = "lan"
                    label = f"LAN: {label}"

                result.append({"ip": ip_str, "label": label, "kind": kind})
    except Exception:
        log.exception("Netzwerkadapter konnten nicht ermittelt werden")

    if primary and primary not in seen and not primary.startswith("127."):
        result.append({"ip": primary, "label": "Standard-Netzwerkverbindung", "kind": "lan"})

    # Priorität: 1. USB-Tethering, 2. WLAN (da iPad meist im WLAN ist), 3. Primäre IP / LAN, 4. Virtuelle (WSL, Docker)
    virt = re.compile(r"virtual|vmware|vbox|hyper-v|vethernet|wsl|docker|loopback", re.I)

    def sort_key(a):
        is_tether = a["kind"] == "tethering"
        is_wifi = a["kind"] == "wlan"
        is_virt = bool(virt.search(a["label"]))
        is_prim = a["ip"] == primary
        return (not is_tether, not is_wifi, is_virt, not is_prim)

    result.sort(key=sort_key)
    return result


def list_folder(root: Path) -> list[dict]:
    files = []
    if not root.is_dir():
        return files
    for p in root.rglob("*"):
        rel = p.relative_to(root)
        if any(part.startswith(".") for part in rel.parts) or p.name.endswith(".dinopart"):
            continue
        if p.name.lower() in ("thumbs.db", "desktop.ini"):
            continue
        try:
            st = p.stat()
            is_dir = p.is_dir()
            files.append({
                "name": p.name,
                "rel": rel.as_posix(),
                "folder": rel.parent.as_posix() if rel.parent.parts else "",
                "size": 0 if is_dir else st.st_size,
                "isDir": is_dir,
                "mtime": st.st_mtime,
            })
        except OSError:
            pass
    files.sort(key=lambda f: (not f["isDir"], f["name"].lower()))
    return files


def resolve_inside(root: Path, rel: str) -> Optional[Path]:
    try:
        root = root.resolve()
        target = (root / rel).resolve()
        target.relative_to(root)
        return target
    except (ValueError, OSError):
        return None


class DinoHandler(BaseHTTPRequestHandler):
    server_version = "DinoAppleTransfer/2.0"
    protocol_version = "HTTP/1.1"
    app: "WlanServer"

    def log_message(self, fmt, *args):
        log.debug("%s - %s", self.address_string(), fmt % args)

    def _send_json(self, data, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, data: bytes, ctype: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _send_file(self, path: Path, download: bool) -> None:
        size = path.stat().st_size
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        start, end = 0, size - 1
        status = HTTPStatus.OK

        rng = self.headers.get("Range")
        m = re.match(r"bytes=(\d*)-(\d*)$", rng or "")
        if m and size > 0:
            if m.group(1):
                start = int(m.group(1))
                if m.group(2):
                    end = min(int(m.group(2)), size - 1)
            elif m.group(2):
                start = max(size - int(m.group(2)), 0)
            if start > end or start >= size:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            status = HTTPStatus.PARTIAL_CONTENT

        length = end - start + 1 if size else 0
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")

        disp = "attachment" if download else "inline"
        quoted = urllib.parse.quote(path.name)
        ascii_name = path.name.encode("ascii", "replace").decode().replace('"', "_")
        self.send_header("Content-Disposition", f"{disp}; filename=\"{ascii_name}\"; filename*=UTF-8''{quoted}")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        if self.command == "HEAD":
            return

        with open(path, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(CHUNK, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

        if download and status == HTTPStatus.OK:
            self.app.notify({"type": "wlan", "event": "downloaded", "name": path.name})

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        parts = urllib.parse.urlsplit(self.path)
        path = urllib.parse.unquote(parts.path)
        query = urllib.parse.parse_qs(parts.query)
        settings = self.app.settings

        try:
            if path in ("/", "/index.html", "/mobile", "/ipad"):
                html_path = UI_DIR / "ipad.html"
                if not html_path.is_file():
                    html_path = UI_DIR / "index.html"
                return self._send_bytes(html_path.read_bytes(), "text/html; charset=utf-8")

            if path.startswith("/static/"):
                name = path[len("/static/"):]
                if (UI_DIR / name).is_file():
                    ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
                    if ctype.startswith("text/") or name.endswith(".js"):
                        ctype += "; charset=utf-8"
                    return self._send_bytes((UI_DIR / name).read_bytes(), ctype)
                return self._send_json({"error": "Datei nicht gefunden"}, 404)

            if path == "/api/files":
                return self._send_json({
                    "toDevice": list_folder(settings.to_device_dir),
                    "fromDevice": list_folder(settings.from_device_dir),
                    "toIpad": list_folder(settings.to_device_dir),
                    "fromIpad": list_folder(settings.from_device_dir),
                    "pcName": socket.gethostname(),
                })

            if path.startswith("/dl/"):
                target = resolve_inside(settings.to_device_dir, path[len("/dl/"):])
                if target and target.is_file():
                    return self._send_file(target, download=query.get("inline") != ["1"])
                return self._send_json({"error": "Datei nicht gefunden"}, 404)

            return self._send_json({"error": "Route nicht gefunden"}, 404)
        except (ConnectionError, BrokenPipeError):
            pass

    def do_POST(self):
        parts = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(parts.query)
        settings = self.app.settings

        # Ordner anlegen
        if parts.path == "/api/mkdir":
            name = safe_name(query.get("name", ["Neuer Ordner"])[0], "Neuer Ordner")
            target_key = query.get("target", ["to"])[0]
            root = settings.to_device_dir if target_key == "to" else settings.from_device_dir
            parent_rel = query.get("parent", [""])[0].strip()
            dest_dir = resolve_inside(root, parent_rel) if parent_rel else root
            if not dest_dir:
                dest_dir = root
            new_folder = unique_local_path(dest_dir / name)
            try:
                new_folder.mkdir(parents=True, exist_ok=True)
                self.app.notify({"type": "wlan", "event": "mkdir", "name": new_folder.name})
                return self._send_json({"ok": True, "name": new_folder.name})
            except Exception as e:
                return self._send_json({"error": str(e)}, 500)

        # Datei oder Ordner löschen
        if parts.path == "/api/delete":
            rel = query.get("rel", [""])[0].strip()
            target_key = query.get("target", ["to"])[0]
            root = settings.to_device_dir if target_key == "to" else settings.from_device_dir
            target = resolve_inside(root, rel)
            if not target or not target.exists():
                return self._send_json({"error": "Nicht gefunden"}, 404)
            try:
                import shutil
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
                self.app.notify({"type": "wlan", "event": "deleted", "name": target.name})
                return self._send_json({"ok": True, "name": target.name})
            except Exception as e:
                return self._send_json({"error": str(e)}, 500)

        # Datei-Upload
        if parts.path != "/api/upload":
            return self._send_json({"error": "Nicht gefunden"}, 404)
        name = safe_name(query.get("name", ["upload"])[0], "upload")
        try:
            length = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            length = -1

        if length < 0:
            return self._send_json({"error": "Content-Length fehlt"}, 411)

        dest_dir = self.app.settings.from_device_dir
        dest_dir.mkdir(parents=True, exist_ok=True)
        target = unique_local_path(dest_dir / name)
        part = target.with_name(target.name + ".dinopart")
        remaining = length

        try:
            with open(part, "wb") as f:
                while remaining > 0:
                    chunk = self.rfile.read(min(CHUNK, remaining))
                    if not chunk:
                        raise ConnectionError("Upload abgebrochen")
                    f.write(chunk)
                    remaining -= len(chunk)
            target = unique_local_path(target)
            os.replace(part, target)
        except Exception as e:
            part.unlink(missing_ok=True)
            log.warning("Upload fehlgeschlagen: %s", e)
            self.close_connection = True
            try:
                return self._send_json({"error": str(e)}, 500)
            except OSError:
                return

        log.info("Vom Apple-Gerät empfangen: %s (%d Bytes)", target.name, length)
        self.app.notify({"type": "wlan", "event": "received", "name": target.name, "size": length})
        return self._send_json({"ok": True, "name": target.name, "size": length}, 201)


class WlanServer:
    def __init__(self, settings: Settings, notify: Callable[[dict], None]):
        self.settings = settings
        self.notify = notify
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def running(self) -> bool:
        return self._httpd is not None

    @property
    def port(self) -> Optional[int]:
        return self._httpd.server_address[1] if self._httpd else None

    def start(self, port: int) -> int:
        if self._httpd:
            return self.port
        handler = type("BoundDinoHandler", (DinoHandler,), {"app": self})
        try:
            httpd = ThreadingHTTPServer(("0.0.0.0", port), handler)
        except OSError as e:
            raise RuntimeError(f"Port {port} ist belegt oder gesperrt ({e.strerror or e}). Bitte anderen Port wählen.") from e

        httpd.daemon_threads = True
        self._httpd = httpd
        self._thread = threading.Thread(target=httpd.serve_forever, name="dino-wlan", daemon=True)
        self._thread.start()
        log.info("Dino HTTP-Server läuft auf 0.0.0.0:%d", port)
        return port

    def stop(self) -> None:
        httpd, self._httpd = self._httpd, None
        if httpd:
            httpd.shutdown()
            httpd.server_close()
            log.info("Dino HTTP-Server gestoppt")
