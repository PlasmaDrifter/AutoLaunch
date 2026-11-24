"""Desktop application scanner and metadata parser for AutoLaunch.

Scans standard FreeDesktop / XDG application directories to discover installed apps,
extracting names, clean exec commands, icons, and desktop file paths.
Strictly complies with dynamic path resolution (no hardcoded usernames).
"""

import configparser
import functools
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from logging_utils import get_logger

_LOGGER = get_logger("autolaunch.scanner")


@dataclass
class DesktopAppInfo:
    """Information discovered from an installed .desktop file."""

    desktop_file: str  # Full path to .desktop file
    desktop_id: str    # e.g. zen-youtube.desktop
    name: str
    generic_name: str
    comment: str
    exec_command: str
    clean_command: str
    icon_name: str
    icon_path: Optional[str] = None
    startup_wm_class: str = ""
    kde_app_id: str = ""
    categories: Optional[List[str]] = None
    no_display: bool = False
    hidden: bool = False
    working_dir: str = ""
    terminal: bool = False
    try_exec: str = ""
    _search_cache: Optional[str] = None

    def search_text(self) -> str:
        """Returns consolidated lowercase string for fuzzy searching (cached)."""
        if self._search_cache is None:
            cats = " ".join(self.categories or [])
            self._search_cache = (
                f"{self.name} {self.generic_name} {self.comment} "
                f"{self.clean_command} {self.desktop_id} {cats}"
            ).lower()
        return self._search_cache


def clean_desktop_exec(raw_exec: str) -> str:
    """Removes FreeDesktop field codes and Flatpak forwarding placeholders from Exec lines."""
    # Replace %% with literal %, strip standard %f/%F/%u/%U/%d/%D/%n/%N/%i/%c/%k/%v/%m
    cleaned = re.sub(
        r"%(?:(%)|[fFuUdDnNickvm])",
        lambda m: "%" if m.group(1) else "",
        raw_exec,
    )
    # Strip Flatpak --file-forwarding tokens (e.g. '@@u @@' or '@@ @@')
    had_forwarding = bool(re.search(r"@@[uUfF]?\b|@@", cleaned))
    cleaned = re.sub(r"@@[uUfF]?\b|@@", "", cleaned)
    cleaned = " ".join(cleaned.split()).strip()
    if had_forwarding and cleaned.endswith(" --"):
        cleaned = cleaned[:-3].strip()
    return cleaned


@functools.lru_cache(maxsize=512)
def find_icon_file(icon_name: str) -> Optional[str]:
    """Finds an absolute file path for an icon if not already an absolute path."""
    if not icon_name:
        return None

    path_obj = Path(icon_name).expanduser()
    if path_obj.is_file():
        return str(path_obj)

    # Check for direct file with common extensions
    for ext in [".svg", ".png", ".xpm"]:
        candidate = Path(str(path_obj) + ext)
        if candidate.is_file():
            return str(candidate)

    # Search in common user and system icon locations
    icon_sizes = ["scalable", "512x512", "256x256", "128x128", "64x64", "48x48"]
    hicolor_roots = [
        Path.home() / ".local" / "share" / "icons" / "hicolor",
        Path.home() / ".local" / "share" / "flatpak" / "exports" / "share" / "icons" / "hicolor",
        Path("/var/lib/flatpak/exports/share/icons/hicolor"),
        Path("/usr/share/icons/hicolor"),
    ]

    search_dirs: List[Path] = [
        Path.home() / ".local" / "share" / "icons",
        Path.home() / ".icons",
        Path.home() / "Pictures" / "Avatar",
        Path("/usr/share/pixmaps"),
    ]
    for root in hicolor_roots:
        for size in icon_sizes:
            search_dirs.append(root / size / "apps")

    for sdir in search_dirs:
        if not sdir.is_dir():
            continue
        for ext in ["", ".svg", ".png", ".xpm"]:
            direct_file = sdir / f"{icon_name}{ext}"
            if direct_file.is_file():
                return str(direct_file)

    return None


class DesktopScanner:
    """Scans and parses system and user .desktop files."""

    @staticmethod
    def get_search_directories() -> List[Path]:
        """Returns standard desktop file directories dynamically in XDG precedence order."""
        dirs: List[Path] = []
        seen: set[Path] = set()

        def _add_dir(candidate: Path) -> None:
            resolved = candidate.expanduser()
            if resolved.is_dir() and resolved not in seen:
                seen.add(resolved)
                dirs.append(resolved)

        xdg_data_home = os.environ.get("XDG_DATA_HOME")
        if xdg_data_home:
            _add_dir(Path(xdg_data_home) / "applications")
        else:
            _add_dir(Path.home() / ".local" / "share" / "applications")

        xdg_data_dirs = os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share")
        for p in xdg_data_dirs.split(":"):
            if p:
                _add_dir(Path(p) / "applications")

        # Flatpak user and system directories
        _add_dir(Path.home() / ".local" / "share" / "flatpak" / "exports" / "share" / "applications")
        _add_dir(Path("/var/lib/flatpak/exports/share/applications"))

        return dirs

    _cached_apps: Optional[List[DesktopAppInfo]] = None
    _cache_lock = threading.Lock()

    @classmethod
    def _iter_desktop_entries(cls, directory: Path) -> List[Tuple[str, Path]]:
        """Yields (desktop_id, path) pairs with top-level files before nested subdirectories."""
        top_level: List[Tuple[str, Path]] = []
        nested: List[Tuple[str, Path]] = []
        try:
            for entry in directory.rglob("*.desktop"):
                if not entry.is_file():
                    continue
                rel = entry.relative_to(directory)
                if len(rel.parts) == 1:
                    top_level.append((entry.name, entry))
                else:
                    nested_id = "-".join(rel.parts)
                    nested.append((nested_id, entry))
        except (PermissionError, FileNotFoundError, OSError) as exc:
            _LOGGER.debug("Skipping inaccessible desktop directory %s: %s", directory, exc)
        return top_level + nested

    @classmethod
    def scan_all(cls, include_no_display: bool = False, force_refresh: bool = False) -> List[DesktopAppInfo]:
        """Scans all desktop directories and returns sorted list of applications (cached in-memory)."""
        if not include_no_display and not force_refresh:
            with cls._cache_lock:
                if cls._cached_apps is not None:
                    return cls._cached_apps

        seen_ids: set[str] = set()
        apps: List[DesktopAppInfo] = []

        for directory in cls.get_search_directories():
            for desktop_id, entry in cls._iter_desktop_entries(directory):
                if desktop_id in seen_ids:
                    continue

                app_info = cls.parse_desktop_file(entry, desktop_id=desktop_id)
                if app_info is None:
                    continue

                # Claim desktop_id even when Hidden=true or NoDisplay=true so user
                # overrides shadow lower-precedence system entries.
                seen_ids.add(desktop_id)

                if app_info.hidden:
                    continue
                if not include_no_display and app_info.no_display:
                    continue

                apps.append(app_info)

        # Sort alphabetically by display name (case-insensitive)
        apps.sort(key=lambda a: a.name.lower())
        if not include_no_display:
            with cls._cache_lock:
                cls._cached_apps = apps
        return apps

    @classmethod
    def parse_desktop_file(
        cls, path: Path, desktop_id: Optional[str] = None
    ) -> Optional[DesktopAppInfo]:
        """Parses a single .desktop file into DesktopAppInfo."""
        config = configparser.ConfigParser(interpolation=None, strict=False)
        try:
            config.read(str(path), encoding="utf-8")
        except Exception:
            try:
                config.read(str(path), encoding="latin-1")
            except Exception as exc:
                _LOGGER.debug("Failed to parse desktop file %s: %s", path, exc)
                return None

        if "Desktop Entry" not in config:
            return None

        section = config["Desktop Entry"]
        entry_type = section.get("Type", "").strip()
        if entry_type and entry_type.lower() != "application":
            return None

        no_display = section.getboolean("NoDisplay", fallback=False)
        hidden = section.getboolean("Hidden", fallback=False)

        name = section.get("Name", "").strip()
        exec_cmd = section.get("Exec", "").strip()

        if hidden:
            return DesktopAppInfo(
                desktop_file=str(path),
                desktop_id=desktop_id or path.name,
                name=name,
                generic_name="",
                comment="",
                exec_command=exec_cmd,
                clean_command="",
                icon_name="",
                hidden=True,
                no_display=no_display,
            )

        if not name or not exec_cmd:
            return None

        generic_name = section.get("GenericName", "").strip()
        comment = section.get("Comment", "").strip()
        icon = section.get("Icon", "").strip()
        startup_wm_class = section.get("StartupWMClass", "").strip()
        kde_app_id = section.get("X-KDE-Wayland-AppId", "").strip()
        working_dir = section.get("Path", "").strip()
        terminal = section.getboolean("Terminal", fallback=False)
        try_exec = section.get("TryExec", "").strip()
        categories_raw = section.get("Categories", "").strip()
        categories = [c.strip() for c in categories_raw.split(";") if c.strip()]

        clean_cmd = clean_desktop_exec(exec_cmd)
        icon_path = find_icon_file(icon)

        return DesktopAppInfo(
            desktop_file=str(path),
            desktop_id=desktop_id or path.name,
            name=name,
            generic_name=generic_name,
            comment=comment,
            exec_command=exec_cmd,
            clean_command=clean_cmd,
            icon_name=icon,
            icon_path=icon_path,
            startup_wm_class=startup_wm_class,
            kde_app_id=kde_app_id,
            categories=categories,
            no_display=no_display,
            hidden=hidden,
            working_dir=working_dir,
            terminal=terminal,
            try_exec=try_exec,
        )
