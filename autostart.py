"""Autostart configuration and desktop entry management for AutoLaunch.

Ensures no hardcoded user paths are written into configuration or desktop entries.
Uses dynamic environment resolution ($HOME / Path.home()) and standard XDG locations.
"""

import configparser
import os
import shutil
import subprocess
import sys
from pathlib import Path

from logging_utils import get_logger

_LOGGER = get_logger("autolaunch.autostart")


def _format_exec_arg(arg: str) -> str:
    """Quotes a Desktop Entry Exec argument safely if it contains whitespace or special characters."""
    if any(ch in arg for ch in (' ', '\t', '"', '\\', '$', '`')):
        escaped = arg.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return arg


class AutostartManager:
    """Manages the ~/.config/autostart/autolaunch.desktop entry."""

    @staticmethod
    def get_autostart_file() -> Path:
        xdg_config = os.environ.get("XDG_CONFIG_HOME")
        base = Path(xdg_config) if xdg_config else (Path.home() / ".config")
        return base / "autostart" / "autolaunch.desktop"

    @staticmethod
    def get_applications_desktop_file() -> Path:
        xdg_data = os.environ.get("XDG_DATA_HOME")
        base = Path(xdg_data) if xdg_data else (Path.home() / ".local" / "share")
        return base / "applications" / "autolaunch.desktop"

    @classmethod
    def _ensure_icons_installed(cls) -> None:
        """Installs scalable/PNG AutoLaunch icons into the user hicolor icon theme."""
        assets_dir = Path(__file__).resolve().parent / "assets"
        xdg_data = os.environ.get("XDG_DATA_HOME")
        data_base = Path(xdg_data) if xdg_data else (Path.home() / ".local" / "share")
        targets = [
            (assets_dir / "autolaunch.svg", data_base / "icons" / "hicolor" / "scalable" / "apps" / "autolaunch.svg"),
            (assets_dir / "autolaunch.png", data_base / "icons" / "hicolor" / "512x512" / "apps" / "autolaunch.png"),
        ]
        copied = False
        for src, dst in targets:
            if src.is_file():
                try:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    if not dst.is_file() or dst.stat().st_size != src.stat().st_size:
                        shutil.copy2(src, dst)
                        copied = True
                except OSError as exc:
                    _LOGGER.debug("Failed copying icon %s to %s: %s", src, dst, exc)

        if copied and shutil.which("gtk-update-icon-cache"):
            try:
                subprocess.run(
                    ["gtk-update-icon-cache", "-f", "-t", str(data_base / "icons" / "hicolor")],
                    capture_output=True,
                    check=False,
                    timeout=5,
                )
            except Exception as exc:
                _LOGGER.debug("gtk-update-icon-cache failed: %s", exc)

    @classmethod
    def is_autostart_enabled(cls) -> bool:
        autostart_file = cls.get_autostart_file()
        if not autostart_file.is_file():
            return False

        cfg = configparser.ConfigParser(interpolation=None, strict=False)
        try:
            cfg.read(str(autostart_file), encoding="utf-8")
            if "Desktop Entry" in cfg:
                section = cfg["Desktop Entry"]
                if section.getboolean("Hidden", fallback=False):
                    return False
                if not section.getboolean("X-GNOME-Autostart-enabled", fallback=True):
                    return False
        except Exception as exc:
            _LOGGER.debug("Could not parse autostart file %s: %s", autostart_file, exc)

        return True

    @classmethod
    def set_autostart(cls, enabled: bool) -> bool:
        autostart_file = cls.get_autostart_file()
        if enabled:
            try:
                cls._ensure_icons_installed()
                autostart_file.parent.mkdir(parents=True, exist_ok=True)
                content = cls.generate_desktop_content(autostart=True)
                with open(autostart_file, "w", encoding="utf-8") as f:
                    f.write(content)
                autostart_file.chmod(0o644)
                return True
            except Exception as exc:
                _LOGGER.error("Failed enabling autostart at %s: %s", autostart_file, exc)
                return False
        else:
            try:
                if autostart_file.is_file():
                    autostart_file.unlink()
                return True
            except Exception as exc:
                _LOGGER.error("Failed disabling autostart at %s: %s", autostart_file, exc)
                return False

    @classmethod
    def install_desktop_entry(cls) -> bool:
        """Installs desktop entry into ~/.local/share/applications for system menus."""
        app_file = cls.get_applications_desktop_file()
        try:
            cls._ensure_icons_installed()
            app_file.parent.mkdir(parents=True, exist_ok=True)
            content = cls.generate_desktop_content(autostart=False)
            with open(app_file, "w", encoding="utf-8") as f:
                f.write(content)
            app_file.chmod(0o644)
            return True
        except Exception as exc:
            _LOGGER.error("Failed installing desktop entry at %s: %s", app_file, exc)
            return False

    @staticmethod
    def generate_desktop_content(autostart: bool = False) -> str:
        """Generates desktop file content without any hardcoded usernames."""
        script_path = str(Path(__file__).resolve().parent / "autolaunch.py")
        py_bin = sys.executable or "/usr/bin/python3"
        flag = " --autostart" if autostart else ""
        exec_line = f"Exec={_format_exec_arg(py_bin)} {_format_exec_arg(script_path)}{flag}"
        icon_line = "Icon=autolaunch"

        return f"""[Desktop Entry]
Type=Application
Version=1.0
Name=AutoLaunch
GenericName=Startup Application Launcher
Comment=Configure Launch Apps
{exec_line}
{icon_line}
Terminal=false
StartupNotify=true
Categories=LocalTools;
X-GNOME-Autostart-enabled=true
StartupWMClass=autolaunch
X-KDE-Wayland-AppId=autolaunch
"""
