"""Einstellungen, Pfade und kleine Helfer für Dino-apple-transfer."""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from pathlib import Path

from . import APP_NAME

log = logging.getLogger("dino")


def find_ui_dir() -> Path:
    """Sucht den Ordner mit den Web-Dateien (index.html etc.) über verschiedene Pfade."""
    candidates = []
    base = getattr(sys, "_MEIPASS", None)
    if base:
        candidates.extend([Path(base) / "ui", Path(base), Path(base) / "_internal" / "ui"])

    exe_dir = Path(sys.executable).resolve().parent
    candidates.extend([
        exe_dir / "ui",
        exe_dir / "_internal" / "ui",
    ])

    module_ui = Path(__file__).resolve().parent.parent / "ui"
    candidates.extend([
        module_ui,
        Path.cwd() / "ui",
        Path.cwd() / "_internal" / "ui",
    ])

    for c in candidates:
        if c.is_dir() and (c / "index.html").is_file():
            return c

    return module_ui


UI_DIR = find_ui_dir()
DATA_DIR = Path(os.environ.get("APPDATA", Path.home())) / APP_NAME
SETTINGS_FILE = DATA_DIR / "settings.json"
LOG_FILE = DATA_DIR / "dino.log"


def _default_base_dir() -> Path:
    docs = Path.home() / "Documents"
    return (docs if docs.is_dir() else Path.home()) / "Dino-Transfer"


DEFAULTS = {
    "base_dir": str(_default_base_dir()),
    "last_app": None,
}


class Settings:
    def __init__(self) -> None:
        self.data = dict(DEFAULTS)
        # Migrate old settings if present
        old_data_dir = Path(os.environ.get("APPDATA", Path.home())) / "Dino-iPad-Transfer"
        old_settings = old_data_dir / "settings.json"
        try:
            if SETTINGS_FILE.is_file():
                self.data.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
            elif old_settings.is_file():
                self.data.update(json.loads(old_settings.read_text(encoding="utf-8")))
        except Exception:
            log.exception("Einstellungen konnten nicht gelesen werden")
        self.ensure_dirs()

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value) -> None:
        self.data[key] = value
        self.save()

    def save(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")

    @property
    def base_dir(self) -> Path:
        return Path(self.data["base_dir"])

    @property
    def to_device_dir(self) -> Path:
        # Fallback to existing "Zum iPad" folder if present
        legacy = self.base_dir / "Zum iPad"
        modern = self.base_dir / "Zum Apple-Gerät"
        if legacy.is_dir() and not modern.exists():
            return legacy
        return modern

    @property
    def from_device_dir(self) -> Path:
        legacy = self.base_dir / "Vom iPad"
        modern = self.base_dir / "Vom Apple-Gerät"
        if legacy.is_dir() and not modern.exists():
            return legacy
        return modern

    # Aliases
    @property
    def to_ipad_dir(self) -> Path:
        return self.to_device_dir

    @property
    def from_ipad_dir(self) -> Path:
        return self.from_device_dir

    def ensure_dirs(self) -> None:
        for d in (self.to_device_dir, self.from_device_dir):
            try:
                d.mkdir(parents=True, exist_ok=True)
            except OSError:
                log.exception("Ordner konnte nicht angelegt werden: %s", d)


_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_name(name: str, fallback: str = "datei") -> str:
    """Dateinamen für Windows/iOS entschärfen (keine Pfade, keine verbotenen Zeichen)."""
    name = name.replace("\\", "/").split("/")[-1].strip()
    name = _BAD_CHARS.sub("_", name).strip(". ")
    return (name or fallback)[:200]


def unique_local_path(path: Path) -> Path:
    """Gibt einen freien Pfad zurück: 'Datei.pdf' -> 'Datei (1).pdf' usw."""
    if not path.exists():
        return path
    stem, suffix, i = path.stem, path.suffix, 1
    while True:
        candidate = path.with_name(f"{stem} ({i}){suffix}")
        if not candidate.exists():
            return candidate
        i += 1


def setup_logging() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(LOG_FILE.with_suffix(".stderr.log"), "a", encoding="utf-8")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler(sys.stderr)],
    )
    for noisy in ("pymobiledevice3", "asyncio", "urllib3", "pywebview"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
