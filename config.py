"""Configuration management for AutoLaunch.

Handles dynamic resolution of XDG directories without hardcoded user paths.
Manages profiles, application lists, countdown timers, window size, and autostart preferences.
"""

import copy
import json
import os
import re
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from logging_utils import get_logger

_LOGGER = get_logger("autolaunch.config")


def _collapse_path(s: str) -> str:
    """Collapses user home path to ~ or $HOME for portability."""
    if not s:
        return ""
    home_str = str(Path.home()).rstrip(os.sep)
    if not home_str:
        return s
    if s == home_str or s.startswith(home_str + os.sep):
        return "~" + s[len(home_str):]
    pattern = re.compile(rf"(?<![\w./-]){re.escape(home_str)}(?=$|/|[\s\"':;])")
    return pattern.sub("$HOME", s)


def _safe_int(value: Any, default: int, minimum: int = 0) -> int:
    """Converts value to int safely with lower bound enforcement."""
    if isinstance(value, bool) or value is None:
        return max(minimum, default)
    try:
        return max(minimum, int(value))
    except (TypeError, ValueError):
        return max(minimum, default)


@dataclass
class AppEntry:
    """Represents an application configured for autolaunch."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    command: str = ""
    desktop_file: str = ""  # Full path or desktop filename (e.g. zen-youtube.desktop)
    icon: str = ""
    enabled: bool = True
    delay_seconds: int = 0
    start_minimized: bool = False

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AppEntry":
        return cls(
            id=str(data.get("id") or uuid.uuid4()),
            name=str(data.get("name", "")),
            command=str(data.get("command", "")),
            desktop_file=str(data.get("desktop_file", "")),
            icon=str(data.get("icon", "")),
            enabled=bool(data.get("enabled", True)),
            delay_seconds=_safe_int(data.get("delay_seconds", 0), default=0, minimum=0),
            start_minimized=bool(data.get("start_minimized", False)),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "command": _collapse_path(self.command),
            "desktop_file": _collapse_path(self.desktop_file),
            "icon": _collapse_path(self.icon),
            "enabled": self.enabled,
            "delay_seconds": self.delay_seconds,
            "start_minimized": self.start_minimized,
        }


@dataclass
class Profile:
    """Represents a set of applications to launch."""

    name: str = "Default"
    apps: List[AppEntry] = field(default_factory=list)

    @classmethod
    def from_dict(cls, name: str, data: Dict[str, Any]) -> "Profile":
        raw_apps = data.get("apps", [])
        apps = [AppEntry.from_dict(item) for item in raw_apps if isinstance(item, dict)]
        return cls(name=name, apps=apps)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "apps": [app.to_dict() for app in self.apps],
        }


class ConfigManager:
    """Loads, modifies, and saves AutoLaunch configuration."""

    DEFAULT_PROFILE_NAME = "Default"
    DEFAULT_WINDOW_WIDTH = 600
    DEFAULT_WINDOW_HEIGHT = 780

    def __init__(self, config_dir: Optional[Path] = None):
        if config_dir is None:
            xdg_config = os.environ.get("XDG_CONFIG_HOME")
            if xdg_config:
                self.config_dir = Path(xdg_config) / "autolaunch"
            else:
                self.config_dir = Path.home() / ".config" / "autolaunch"
        else:
            self.config_dir = Path(config_dir)

        self.config_file = self.config_dir / "config.json"
        self.active_profile: str = self.DEFAULT_PROFILE_NAME
        self.default_profile: str = self.DEFAULT_PROFILE_NAME
        self.countdown_seconds: int = 5
        self.enable_countdown: bool = True
        self.countdown_autostart_only: bool = True
        self.launch_minimized: bool = False
        self.autostart_enabled: bool = True
        self.dismissed_update_version: Optional[str] = None
        self.theme: str = "modern_minimalist"
        self.window_width: int = self.DEFAULT_WINDOW_WIDTH
        self.window_height: int = self.DEFAULT_WINDOW_HEIGHT
        self.profiles: Dict[str, Profile] = {}

        self.load()

    def get_default_config(self) -> Dict[str, Any]:
        return {
            "active_profile": self.DEFAULT_PROFILE_NAME,
            "default_profile": self.DEFAULT_PROFILE_NAME,
            "countdown_seconds": 5,
            "enable_countdown": True,
            "countdown_autostart_only": True,
            "launch_minimized": False,
            "autostart_enabled": True,
            "dismissed_update_version": None,
            "theme": "modern_minimalist",
            "window_width": self.DEFAULT_WINDOW_WIDTH,
            "window_height": self.DEFAULT_WINDOW_HEIGHT,
            "profiles": {
                self.DEFAULT_PROFILE_NAME: {
                    "apps": []
                }
            },
        }

    def load(self) -> None:
        """Loads configuration from JSON file or initializes default configuration."""
        if not self.config_file.is_file():
            self._apply_dict(self.get_default_config())
            self.save()
            return

        try:
            with open(self.config_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("Configuration root must be a JSON object")
            self._apply_dict(data)
        except Exception as exc:
            _LOGGER.warning("Failed to parse config %s: %s", self.config_file, exc)
            backup_path = self.config_dir / "config.json.corrupt.bak"
            try:
                if self.config_file.is_file() and self.config_file.stat().st_size > 0:
                    shutil.copy2(self.config_file, backup_path)
            except OSError as bak_exc:
                _LOGGER.warning("Could not back up corrupt config: %s", bak_exc)
            self._apply_dict(self.get_default_config())

    def _apply_dict(self, data: Dict[str, Any]) -> None:
        self.active_profile = str(data.get("active_profile", self.DEFAULT_PROFILE_NAME))
        self.default_profile = str(
            data.get("default_profile", data.get("active_profile", self.DEFAULT_PROFILE_NAME))
        )
        self.countdown_seconds = _safe_int(data.get("countdown_seconds", 5), default=5, minimum=0)
        self.enable_countdown = bool(data.get("enable_countdown", True))
        self.countdown_autostart_only = bool(data.get("countdown_autostart_only", True))
        self.launch_minimized = bool(data.get("launch_minimized", False))
        self.autostart_enabled = bool(data.get("autostart_enabled", True))
        raw_dismissed = data.get("dismissed_update_version")
        self.dismissed_update_version = str(raw_dismissed) if raw_dismissed else None
        self.theme = str(data.get("theme", "modern_minimalist"))
        self.window_width = _safe_int(
            data.get("window_width", self.DEFAULT_WINDOW_WIDTH),
            default=self.DEFAULT_WINDOW_WIDTH,
            minimum=480,
        )
        self.window_height = _safe_int(
            data.get("window_height", self.DEFAULT_WINDOW_HEIGHT),
            default=self.DEFAULT_WINDOW_HEIGHT,
            minimum=700,
        )

        profiles_raw = data.get("profiles", {})
        self.profiles = {}
        if isinstance(profiles_raw, dict):
            for name, pdata in profiles_raw.items():
                if isinstance(pdata, dict):
                    self.profiles[name] = Profile.from_dict(name, pdata)

        if not self.profiles:
            self.profiles[self.DEFAULT_PROFILE_NAME] = Profile(
                name=self.DEFAULT_PROFILE_NAME, apps=[]
            )

        if self.active_profile not in self.profiles:
            self.active_profile = next(iter(self.profiles.keys()))

        if self.default_profile not in self.profiles:
            self.default_profile = (
                self.active_profile
                if self.active_profile in self.profiles
                else next(iter(self.profiles.keys()))
            )

    def save(self) -> None:
        """Saves current configuration atomically to file."""
        self.config_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "active_profile": self.active_profile,
            "default_profile": self.default_profile,
            "countdown_seconds": self.countdown_seconds,
            "enable_countdown": self.enable_countdown,
            "countdown_autostart_only": self.countdown_autostart_only,
            "launch_minimized": self.launch_minimized,
            "autostart_enabled": self.autostart_enabled,
            "dismissed_update_version": self.dismissed_update_version,
            "theme": self.theme,
            "window_width": self.window_width,
            "window_height": self.window_height,
            "profiles": {
                name: {"apps": [app.to_dict() for app in prof.apps]}
                for name, prof in self.profiles.items()
            },
        }
        fd, tmp_name = tempfile.mkstemp(
            prefix=".config-", suffix=".tmp", dir=str(self.config_dir)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_name, self.config_file)
        except Exception:
            if os.path.exists(tmp_name):
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
            raise

    def get_current_profile(self) -> Profile:
        """Returns the currently active profile."""
        if self.active_profile not in self.profiles:
            self.active_profile = next(iter(self.profiles.keys()))
        return self.profiles[self.active_profile]

    def set_active_profile(self, name: str) -> bool:
        if name in self.profiles:
            self.active_profile = name
            self.save()
            return True
        return False

    def set_default_profile(self, name: str) -> bool:
        clean_name = name.strip()
        if clean_name in self.profiles:
            self.default_profile = clean_name
            self.save()
            return True
        return False

    def add_profile(self, name: str) -> bool:
        clean_name = name.strip()
        if not clean_name or clean_name in self.profiles:
            return False
        self.profiles[clean_name] = Profile(name=clean_name, apps=[])
        self.save()
        return True

    def duplicate_profile(self, source_name: str, new_name: str) -> bool:
        clean_name = new_name.strip()
        if not clean_name or source_name not in self.profiles or clean_name in self.profiles:
            return False
        copied_apps = []
        for app in self.profiles[source_name].apps:
            cloned = copy.deepcopy(app)
            cloned.id = str(uuid.uuid4())
            copied_apps.append(cloned)
        self.profiles[clean_name] = Profile(name=clean_name, apps=copied_apps)
        self.save()
        return True

    def remove_profile(self, name: str) -> bool:
        if name not in self.profiles or len(self.profiles) <= 1:
            return False
        del self.profiles[name]
        if self.active_profile == name:
            self.active_profile = next(iter(self.profiles.keys()))
        if self.default_profile == name:
            self.default_profile = self.active_profile
        self.save()
        return True

    def rename_profile(self, old_name: str, new_name: str) -> bool:
        clean_name = new_name.strip()
        if not clean_name or old_name not in self.profiles or clean_name in self.profiles:
            return False
        profile = self.profiles.pop(old_name)
        profile.name = clean_name
        self.profiles[clean_name] = profile
        if self.active_profile == old_name:
            self.active_profile = clean_name
        if self.default_profile == old_name:
            self.default_profile = clean_name
        self.save()
        return True

    def add_app_to_profile(self, profile_name: str, app: AppEntry) -> bool:
        if profile_name not in self.profiles:
            return False
        self.profiles[profile_name].apps.append(app)
        self.save()
        return True

    def remove_app_from_profile(self, profile_name: str, app_id: str) -> bool:
        if profile_name not in self.profiles:
            return False
        profile = self.profiles[profile_name]
        initial_count = len(profile.apps)
        profile.apps = [a for a in profile.apps if a.id != app_id]
        if len(profile.apps) != initial_count:
            self.save()
            return True
        return False

    def update_app_in_profile(self, profile_name: str, updated_app: AppEntry) -> bool:
        if profile_name not in self.profiles:
            return False
        profile = self.profiles[profile_name]
        for idx, app in enumerate(profile.apps):
            if app.id == updated_app.id:
                profile.apps[idx] = updated_app
                self.save()
                return True
        return False
