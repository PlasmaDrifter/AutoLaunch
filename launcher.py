"""Process launcher for AutoLaunch.

Spawns applications in detached sessions so they continue running after AutoLaunch exits.
Prefers gtk-launch / gio launch for desktop entries so desktop environments (such as KDE Wayland)
correctly associate running windows with their .desktop file and display custom icons.
"""

import configparser
import functools
import re
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from config import AppEntry
from desktop_scanner import DesktopScanner
from logging_utils import get_logger

_LOGGER = get_logger("autolaunch.launcher")


def _double_quote_sh(value: str) -> str:
    """Safely wraps a string in POSIX shell double quotes."""
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("$", "\\$")
        .replace("`", "\\`")
    )
    return f'"{escaped}"'


def _parse_int_pair(raw: str, allow_negative: bool = True) -> Optional[Tuple[int, int]]:
    """Parses 'x,y' or 'w,h' strings strictly into a 2-tuple of integers."""
    if not raw:
        return None
    pattern = r"-?\d+" if allow_negative else r"\d+"
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) != 2:
        return None
    if not all(re.fullmatch(pattern, p) for p in parts):
        return None
    return (int(parts[0]), int(parts[1]))


class AppLauncher:
    """Manages spawning applications in decoupled sessions."""

    @staticmethod
    @functools.lru_cache(maxsize=32)
    def _has_binary(name: str) -> bool:
        return shutil.which(name) is not None

    @classmethod
    def _get_qdbus_bin(cls) -> str:
        if cls._has_binary("qdbus-qt6"):
            return "qdbus-qt6"
        if cls._has_binary("qdbus"):
            return "qdbus"
        return ""

    @staticmethod
    def _is_in_xdg_applications(desktop_path: Path) -> bool:
        """Returns True if desktop_path is inside a standard XDG applications directory."""
        try:
            resolved = desktop_path.expanduser().resolve()
        except OSError:
            return False

        for app_dir in DesktopScanner.get_search_directories():
            try:
                resolved.relative_to(app_dir.resolve())
                return True
            except ValueError:
                continue
        return False

    @classmethod
    def validate_app(cls, app: AppEntry) -> Optional[str]:
        """Checks whether the configured desktop file or command exists. Returns warning or None."""
        desktop_target = app.desktop_file.strip()
        if desktop_target:
            if "/" in desktop_target or desktop_target.startswith("~"):
                desktop_path = Path(desktop_target).expanduser()
                if desktop_path.is_file():
                    return None
                return "Missing .desktop file"
            for app_dir in DesktopScanner.get_search_directories():
                if (app_dir / desktop_target).is_file():
                    return None
            return "Missing .desktop entry"

        cmd = app.command.strip()
        if not cmd:
            return "Empty command"

        try:
            tokens = shlex.split(cmd)
        except ValueError:
            tokens = cmd.split()

        if not tokens:
            return "Empty command"

        executable = tokens[0]
        if executable == "env" and len(tokens) > 1:
            for tok in tokens[1:]:
                if "=" not in tok:
                    executable = tok
                    break

        expanded = Path(executable).expanduser()
        if "/" in executable or executable.startswith("~"):
            return None if expanded.is_file() else "Executable not found"

        return None if shutil.which(executable) else "Command not in PATH"

    @classmethod
    def get_launch_command(
        cls,
        app: AppEntry,
        target_activity: Optional[str] = None,
        current_activity: Optional[str] = None,
    ) -> Tuple[List[str], bool]:
        """Returns the command arguments and whether shell=True should be used.

        For .desktop files:
        Uses gtk-launch or gio launch so the desktop environment sets the proper
        Wayland app_id and startup notification token for custom icons.
        """
        desktop_target = app.desktop_file.strip()
        desktop_path = Path(desktop_target).expanduser() if desktop_target else Path()
        desktop_id = desktop_path.name if desktop_target else ""
        is_bare_desktop_id = bool(desktop_target) and "/" not in desktop_target and not desktop_target.startswith("~")

        has_gtk_launch = cls._has_binary("gtk-launch")
        has_gio = cls._has_binary("gio")

        cmd_str = ""

        if target_activity is None:
            target_activity = cls.get_app_target_activity(app)
        if current_activity is None:
            current_activity = cls.get_current_activity()

        if desktop_id and has_gtk_launch and (
            is_bare_desktop_id
            or (desktop_path.is_file() and cls._is_in_xdg_applications(desktop_path))
        ):
            cmd_str = f"gtk-launch {_double_quote_sh(desktop_id)}"
        elif desktop_target and desktop_path.is_file() and has_gio:
            cmd_str = f"gio launch {_double_quote_sh(str(desktop_path))}"
        elif app.command.strip():
            cmd_str = app.command.strip()
        elif desktop_target and desktop_path.is_file():
            parsed = DesktopScanner.parse_desktop_file(desktop_path)
            if parsed and parsed.clean_command:
                cmd_str = parsed.clean_command

        if not cmd_str:
            raise ValueError(f"No valid command or desktop file found for app: {app.name}")

        qdbus_bin = cls._get_qdbus_bin()
        if (
            target_activity
            and current_activity
            and target_activity != current_activity
            and qdbus_bin
        ):
            switch_and_launch = (
                f"{qdbus_bin} org.kde.ActivityManager /ActivityManager/Activities "
                f"SetCurrentActivity {_double_quote_sh(target_activity)} 2>/dev/null; "
                f"{cmd_str}; "
                f"sleep 0.2; "
                f"{qdbus_bin} org.kde.ActivityManager /ActivityManager/Activities "
                f"SetCurrentActivity {_double_quote_sh(current_activity)} 2>/dev/null"
            )
            cmd_str = switch_and_launch
            exec_prefix = ""
        else:
            exec_prefix = "exec "

        delay = max(0, int(app.delay_seconds))
        if delay > 0:
            full_shell_cmd = f"sleep {delay} && {exec_prefix}{cmd_str}"
        else:
            full_shell_cmd = f"{exec_prefix}{cmd_str}"

        return ["/bin/sh", "-c", full_shell_cmd], False

    @classmethod
    def get_target_window_classes(cls, app: AppEntry) -> List[str]:
        """Resolves probable window classes for the given app entry."""
        classes: List[str] = []
        desktop_target = app.desktop_file.strip()
        desktop_path = Path(desktop_target).expanduser() if desktop_target else Path()

        if desktop_target and desktop_path.is_file():
            parsed = DesktopScanner.parse_desktop_file(desktop_path)
            if parsed:
                if parsed.startup_wm_class:
                    classes.append(parsed.startup_wm_class)
                if parsed.kde_app_id and parsed.kde_app_id not in classes:
                    classes.append(parsed.kde_app_id)
            stem = desktop_path.stem
            if stem.endswith(".desktop"):
                stem = stem[:-8]
            if stem and stem not in classes:
                classes.append(stem)
        elif desktop_target:
            stem = Path(desktop_target).stem
            if stem.endswith(".desktop"):
                stem = stem[:-8]
            if stem:
                classes.append(stem)

        if app.command:
            m = re.search(r"--class(?:=|\s+)([^\s\"']+)", app.command)
            if m and m.group(1) not in classes:
                classes.append(m.group(1))
            parts = app.command.split()
            if parts:
                cmd_stem = Path(parts[0]).name
                if cmd_stem and cmd_stem not in classes:
                    classes.append(cmd_stem)

        return classes

    @classmethod
    def _build_class_regex(cls, app: AppEntry) -> Optional[str]:
        classes = cls.get_target_window_classes(app)
        if not classes:
            return None
        inner_regex = "|".join(re.escape(c) for c in classes)
        return f"^({inner_regex})$"

    @classmethod
    def _spawn_detached_script(cls, script: str, description: str) -> None:
        """Runs a background helper shell script decoupled from AutoLaunch's cgroup."""
        cmd = ["/bin/sh", "-c", script]
        if cls._has_binary("systemd-run"):
            run_args = [
                "systemd-run",
                "--user",
                "--slice=app.slice",
                "--collect",
                "-p",
                "KillMode=none",
                f"--description={description}",
                *cmd,
            ]
            try:
                res = subprocess.run(
                    run_args,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=5,
                )
                if res.returncode == 0:
                    return
                _LOGGER.debug(
                    "systemd-run helper failed (%s): %s", res.returncode, res.stderr.strip()
                )
            except Exception as exc:
                _LOGGER.debug("systemd-run helper error: %s", exc)

        try:
            subprocess.Popen(
                cmd,
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
        except Exception as exc:
            _LOGGER.warning("Failed spawning helper '%s': %s", description, exc)

    @classmethod
    def minimize_window_deferred(cls, app: AppEntry, delay_seconds: float = 0.0) -> None:
        """Polls for the app window's appearance and minimizes it via kdotool."""
        if not cls._has_binary("kdotool"):
            return

        class_regex = cls._build_class_regex(app)
        if not class_regex:
            return

        total_delay = max(0.0, float(app.delay_seconds)) + max(0.0, float(delay_seconds))
        quoted_regex = shlex.quote(class_regex)

        # Poll up to 15s (60 * 0.25s); after first match, keep checking briefly so
        # splash screens don't stop minimization of the main window.
        script = (
            f"sleep {total_delay}; "
            f'seen=""; settle=0; '
            f"for i in $(seq 1 60); do "
            f"wids=$(kdotool search --class {quoted_regex} 2>/dev/null); "
            f'if [ -n "$wids" ]; then '
            f'for wid in $wids; do '
            f'case " $seen " in *" $wid "*) ;; *) '
            f'kdotool windowminimize "$wid" 2>/dev/null; '
            f'seen="$seen $wid"; '
            f";; esac; "
            f"done; "
            f"settle=$((settle + 1)); "
            f'if [ "$settle" -ge 4 ]; then break; fi; '
            f"fi; "
            f"sleep 0.25; "
            f"done"
        )
        cls._spawn_detached_script(script, f"AutoLaunch minimize {app.name}")

    @classmethod
    def clear_attention_deferred(cls, app: AppEntry, delay_seconds: float = 0.0) -> None:
        """Polls for the app window's appearance and strips DEMANDS_ATTENTION via kdotool."""
        if not cls._has_binary("kdotool"):
            return

        class_regex = cls._build_class_regex(app)
        if not class_regex:
            return

        total_delay = max(0.0, float(app.delay_seconds)) + max(0.0, float(delay_seconds))
        quoted_regex = shlex.quote(class_regex)

        script = (
            f"sleep {total_delay}; "
            f"for i in $(seq 1 40); do "
            f"wids=$(kdotool search --class {quoted_regex} 2>/dev/null); "
            f'if [ -n "$wids" ]; then '
            f'for wid in $wids; do '
            f'kdotool windowstate --remove DEMANDS_ATTENTION "$wid" 2>/dev/null; '
            f"done; "
            f"fi; "
            f"sleep 0.25; "
            f"done"
        )
        cls._spawn_detached_script(script, f"AutoLaunch clear attention {app.name}")

    @classmethod
    def enforce_geometry_deferred(
        cls,
        app: AppEntry,
        target_pos: Optional[Tuple[int, int]] = None,
        target_size: Optional[Tuple[int, int]] = None,
        delay_seconds: float = 0.0,
    ) -> None:
        """Ensures a window whose startup geometry snapped or maximized incorrectly is adjusted."""
        if not cls._has_binary("kdotool"):
            return

        class_regex = cls._build_class_regex(app)
        if not class_regex or (not target_pos and not target_size):
            return

        total_delay = max(0.0, float(app.delay_seconds)) + max(0.0, float(delay_seconds))
        quoted_regex = shlex.quote(class_regex)

        tx = str(target_pos[0]) if target_pos else ""
        ty = str(target_pos[1]) if target_pos else ""
        tw = str(target_size[0]) if target_size else ""
        th = str(target_size[1]) if target_size else ""

        pos_cmd = f'kdotool windowmove "$wid" {tx} {ty} 2>/dev/null; ' if target_pos else ""
        size_cmd = f'kdotool windowsize "$wid" {tw} {th} 2>/dev/null; ' if target_size else ""

        script = (
            f"sleep {total_delay}; "
            f"applied=0; "
            f"for i in $(seq 1 48); do "
            f"wids=$(kdotool search --class {quoted_regex} 2>/dev/null); "
            f'if [ -n "$wids" ]; then '
            f"needs_fix=0; "
            f'for wid in $wids; do '
            f'geom=$(kdotool getwindowgeometry "$wid" 2>/dev/null); '
            f'pos_pair=$(printf "%s\\n" "$geom" | sed -nE "s/.*Position:[[:space:]]*(-?[0-9]+)(\\.[0-9]+)?,(-?[0-9]+)(\\.[0-9]+)?.*/\\1 \\3/p"); '
            f'size_pair=$(printf "%s\\n" "$geom" | sed -nE "s/.*Geometry:[[:space:]]*([0-9]+)(\\.[0-9]+)?x([0-9]+)(\\.[0-9]+)?.*/\\1 \\3/p"); '
            f"set -- $pos_pair; x_pos=$1; y_pos=$2; "
            f"set -- $size_pair; width=$1; height=$2; "
            f"mismatch=0; "
            f'if [ -n "{tx}" ] && [ -n "$x_pos" ] && {{ [ "$x_pos" != "{tx}" ] || [ "$y_pos" != "{ty}" ]; }}; then mismatch=1; fi; '
            f'if [ -n "{tw}" ] && [ -n "$width" ] && {{ [ "$width" != "{tw}" ] || [ "$height" != "{th}" ]; }}; then mismatch=1; fi; '
            f'if [ "$mismatch" = "1" ]; then '
            f"needs_fix=1; "
            f'kdotool windowstate --remove MAXIMIZED_VERT --remove MAXIMIZED_HORZ "$wid" 2>/dev/null; '
            f"{pos_cmd}"
            f"{size_cmd}"
            f"fi; "
            f"done; "
            f'if [ "$needs_fix" = "0" ]; then '
            f"applied=$((applied + 1)); "
            f'if [ "$applied" -ge 3 ]; then break; fi; '
            f"else applied=0; fi; "
            f"fi; "
            f"sleep 0.25; "
            f"done"
        )
        cls._spawn_detached_script(script, f"AutoLaunch geometry {app.name}")

    @classmethod
    def _post_launch_actions(
        cls,
        app: AppEntry,
        global_launch_minimized: bool,
        is_cross_activity: bool,
        geom: Optional[Dict[str, Optional[Tuple[int, int]]]],
    ) -> None:
        should_minimize = app.start_minimized or global_launch_minimized
        if should_minimize:
            cls.minimize_window_deferred(app)
        if is_cross_activity:
            cls.clear_attention_deferred(app)
        if geom and (geom.get("position") or geom.get("size")) and not should_minimize:
            cls.enforce_geometry_deferred(
                app, geom.get("position"), geom.get("size"), delay_seconds=1.5
            )

    @classmethod
    def launch(
        cls,
        app: AppEntry,
        global_launch_minimized: bool = False,
        current_activity: Optional[str] = None,
    ) -> bool:
        """Launches a single app in an isolated background session."""
        try:
            target_act = cls.get_app_target_activity(app)
            curr_act = cls.get_current_activity() if current_activity is None else current_activity
            args, use_shell = cls.get_launch_command(
                app,
                target_activity=target_act,
                current_activity=curr_act,
            )

            is_cross_activity = bool(target_act and curr_act and target_act != curr_act)
            geom = cls.get_app_target_geometry(app)

            if cls._has_binary("systemd-run"):
                desc = app.name or "AutoLaunch app"
                cmd_to_run = args if isinstance(args, list) else [args]
                run_args = [
                    "systemd-run",
                    "--user",
                    "--slice=app.slice",
                    "--collect",
                    "-p",
                    "KillMode=none",
                    f"--description={desc}",
                    *cmd_to_run,
                ]
                try:
                    res = subprocess.run(
                        run_args,
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=5,
                    )
                    if res.returncode == 0:
                        cls._post_launch_actions(app, global_launch_minimized, is_cross_activity, geom)
                        return True
                    _LOGGER.warning(
                        "systemd-run failed for '%s' (%s): %s",
                        app.name,
                        res.returncode,
                        res.stderr.strip(),
                    )
                except Exception as exc:
                    _LOGGER.warning("systemd-run error for '%s': %s", app.name, exc)

            subprocess.Popen(
                args,
                shell=use_shell,
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
            cls._post_launch_actions(app, global_launch_minimized, is_cross_activity, geom)
            return True
        except Exception as exc:
            _LOGGER.error("Failed launching '%s': %s", app.name, exc)
            return False

    @classmethod
    def _build_app_match_candidates(cls, app: AppEntry) -> List[str]:
        candidates: List[str] = []
        desktop_target = app.desktop_file.strip()
        if desktop_target:
            p = Path(desktop_target).expanduser()
            candidates.append(p.name.lower())
            candidates.append(p.stem.lower())
            if p.stem.lower().endswith(".desktop"):
                candidates.append(p.stem[:-8].lower())

        if app.command.strip():
            parts = app.command.strip().split()
            if parts:
                candidates.append(Path(parts[0]).name.lower())

        if app.id.strip():
            cid = app.id.strip().lower()
            candidates.append(cid)
            if cid.endswith(".desktop"):
                candidates.append(cid[:-8])

        for c in cls.get_target_window_classes(app):
            candidates.append(c.lower())

        return list(dict.fromkeys(c for c in candidates if c))

    @classmethod
    def _load_kwin_rules(cls) -> Tuple[Optional[configparser.ConfigParser], List[str]]:
        kwinrules = Path.home() / ".config" / "kwinrulesrc"
        if not kwinrules.is_file():
            return None, []

        cfg = configparser.ConfigParser(interpolation=None, strict=False)
        try:
            cfg.read(str(kwinrules), encoding="utf-8")
        except Exception as exc:
            _LOGGER.debug("Failed reading kwinrulesrc: %s", exc)
            return None, []

        ordered: List[str] = []
        seen: set[str] = set()
        if cfg.has_section("General"):
            raw_order = cfg.get("General", "Order", fallback="")
            for item in raw_order.split(","):
                sec = item.strip()
                if sec and sec in cfg and sec not in seen:
                    ordered.append(sec)
                    seen.add(sec)

        for sec in cfg.sections():
            if sec != "General" and sec not in seen:
                ordered.append(sec)
                seen.add(sec)

        enabled_sections = [
            sec for sec in ordered if cfg.getboolean(sec, "Enabled", fallback=True)
        ]
        return cfg, enabled_sections

    @classmethod
    def _matching_kwin_sections(cls, app: AppEntry) -> Tuple[Optional[configparser.ConfigParser], List[str]]:
        cfg, sections = cls._load_kwin_rules()
        if cfg is None or not sections:
            return None, []

        candidates = cls._build_app_match_candidates(app)
        if not candidates:
            return cfg, []

        app_name_low = app.name.strip().lower()
        exact_or_general: List[str] = []
        title_matched: List[str] = []
        other_title_rules: List[str] = []

        for section in sections:
            wmclass = cfg.get(section, "wmclass", fallback="").strip().lower()
            desktopfile = cfg.get(section, "desktopfile", fallback="").strip().lower()
            desc = cfg.get(section, "description", fallback="").strip().lower()
            wmclass_parts = wmclass.split() if wmclass else []

            matched = any(
                (wmclass and (cand == wmclass or cand in wmclass_parts))
                or (desktopfile and (cand == desktopfile or cand == f"{desktopfile}.desktop"))
                or (desc and cand == desc)
                for cand in candidates
            )
            if not matched:
                continue

            title_val = cfg.get(section, "title", fallback="").strip().lower()
            title_rule = cfg.get(section, "titlematch", fallback="0").strip()
            if not title_val or title_rule in ("", "0"):
                exact_or_general.append(section)
            elif "(?!" not in title_val and app_name_low and (
                title_val in app_name_low or app_name_low in title_val
            ):
                title_matched.append(section)
            else:
                other_title_rules.append(section)

        return cfg, exact_or_general + title_matched + other_title_rules

    @classmethod
    def get_app_target_activity(cls, app: AppEntry) -> str:
        """Determines if the app is assigned to a specific KDE activity in kwinrulesrc."""
        cfg, matched_sections = cls._matching_kwin_sections(app)
        if cfg is None:
            return ""

        for section in matched_sections:
            act = cfg.get(section, "activity", fallback="").strip()
            if not act or act == "00000000-0000-0000-0000-000000000000":
                continue
            return act
        return ""

    @classmethod
    def get_app_target_geometry(cls, app: AppEntry) -> Optional[Dict[str, Optional[Tuple[int, int]]]]:
        """Extracts configured target position and size for the app from kwinrulesrc if specified."""
        cfg, matched_sections = cls._matching_kwin_sections(app)
        if cfg is None:
            return None

        for section in matched_sections:
            if cfg.getboolean(section, "maximizevert", fallback=False) or cfg.getboolean(
                section, "maximizehoriz", fallback=False
            ):
                return None
            pos = cfg.get(section, "position", fallback="").strip()
            size = cfg.get(section, "size", fallback="").strip()
            if not pos and not size:
                continue

            parsed_pos = _parse_int_pair(pos, allow_negative=True)
            parsed_size = _parse_int_pair(size, allow_negative=False)
            if parsed_pos is not None or parsed_size is not None:
                return {"position": parsed_pos, "size": parsed_size}

        return None

    @classmethod
    def get_current_activity(cls) -> str:
        """Returns the current active KDE Plasma activity UUID if available."""
        qdbus_bin = cls._get_qdbus_bin()
        if not qdbus_bin:
            return ""
        try:
            res = subprocess.run(
                [qdbus_bin, "org.kde.ActivityManager", "/ActivityManager/Activities", "CurrentActivity"],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            if res.returncode == 0:
                return res.stdout.strip()
        except Exception as exc:
            _LOGGER.debug("Failed querying current KDE activity: %s", exc)
        return ""

    @classmethod
    def restore_activity_deferred(cls, activity_id: str, duration_seconds: float = 12.0) -> None:
        """Ensures the desktop view stays on the starting activity after apps on secondary activities spawn."""
        if not activity_id:
            return
        qdbus_bin = cls._get_qdbus_bin()
        if not qdbus_bin:
            return

        steps = max(1, int(round(max(0.5, float(duration_seconds)) / 0.5)))
        quoted_id = _double_quote_sh(activity_id)
        script = (
            f"for i in $(seq 1 {steps}); do "
            f"sleep 0.5; "
            f"curr=$({qdbus_bin} org.kde.ActivityManager /ActivityManager/Activities CurrentActivity 2>/dev/null); "
            f'if [ -n "$curr" ] && [ "$curr" != {quoted_id} ]; then '
            f"{qdbus_bin} org.kde.ActivityManager /ActivityManager/Activities SetCurrentActivity {quoted_id} 2>/dev/null; "
            f"fi; "
            f"done"
        )
        cls._spawn_detached_script(script, "AutoLaunch restore activity")

    @classmethod
    def launch_many(cls, apps: List[AppEntry], global_launch_minimized: bool = False) -> int:
        """Launches all enabled applications in the list. Returns count launched."""
        initial_activity = cls.get_current_activity()
        launched_count = 0
        max_cross_delay = 0
        has_cross_activity = False

        for app in apps:
            if not app.enabled:
                continue
            target_act = cls.get_app_target_activity(app)
            if target_act and initial_activity and target_act != initial_activity:
                has_cross_activity = True
                max_cross_delay = max(max_cross_delay, max(0, int(app.delay_seconds)))

            if cls.launch(
                app,
                global_launch_minimized=global_launch_minimized,
                current_activity=initial_activity,
            ):
                launched_count += 1

        if initial_activity and has_cross_activity:
            cls.restore_activity_deferred(
                initial_activity, duration_seconds=max(12.0, float(max_cross_delay) + 8.0)
            )

        return launched_count
