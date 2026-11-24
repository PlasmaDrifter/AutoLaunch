"""Tests for the AutoLaunch dual-mode self-updater."""

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from config import ConfigManager
from updater import (
    UPDATE_CACHE,
    apply_self_update,
    check_github_update,
    get_cached_update_status,
    is_newer_version,
    parse_version_tuple,
)


class TestUpdater(unittest.TestCase):
    def test_version_tuple_parsing(self):
        self.assertEqual(parse_version_tuple("0.1.5"), (0, 1, 5))
        self.assertEqual(parse_version_tuple("v0.1.5"), (0, 1, 5))
        self.assertEqual(parse_version_tuple("v0.2.0"), (0, 2, 0))
        self.assertEqual(parse_version_tuple("1.0.0"), (1, 0, 0))
        self.assertEqual(parse_version_tuple("v0.8.12"), (0, 8, 12))

    def test_version_comparison(self):
        self.assertTrue(is_newer_version("v0.1.6", "0.1.5"))
        self.assertTrue(is_newer_version("0.2.0", "0.1.5"))
        self.assertTrue(is_newer_version("v1.0.0", "0.1.5"))

        self.assertFalse(is_newer_version("v0.1.5", "0.1.5"))
        self.assertFalse(is_newer_version("0.1.4", "0.1.5"))
        self.assertFalse(is_newer_version("0.1.0", "0.1.5"))

    def test_check_github_update_cached(self):
        with UPDATE_CACHE["lock"]:
            UPDATE_CACHE["last_checked"] = 9999999999
            UPDATE_CACHE["has_update"] = False
            UPDATE_CACHE["latest_version"] = "v0.1.5"
            UPDATE_CACHE["release_url"] = "https://github.com/PlasmaDrifter/AutoLaunch/releases"

        cached = get_cached_update_status()
        self.assertIsNotNone(cached)
        res = check_github_update(force=False)
        self.assertTrue(res["ok"])
        self.assertFalse(res["has_update"])
        self.assertEqual(res["latest_version"], "v0.1.5")

    def test_get_cached_update_status_none_when_uncached(self):
        with UPDATE_CACHE["lock"]:
            prev = dict(UPDATE_CACHE)
            UPDATE_CACHE["last_checked"] = 0
        try:
            self.assertIsNone(get_cached_update_status())
        finally:
            with UPDATE_CACHE["lock"]:
                UPDATE_CACHE.update(prev)

    def test_git_update_failure_raises_without_archive_overwrite(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / ".git").mkdir()
            failed = subprocess.CompletedProcess(["git"], returncode=1, stdout="", stderr="network down")
            ok = subprocess.CompletedProcess(["git", "--version"], returncode=0, stdout="git version 2", stderr="")
            with patch("updater.BASE_DIR", tmpdir), \
                 patch("updater.subprocess.run", side_effect=[ok, failed, failed]):
                with self.assertRaises(RuntimeError):
                    apply_self_update("v0.4.3")

    def test_config_dismissed_update_persistence(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = ConfigManager(config_dir=Path(tmpdir))
            self.assertIsNone(cfg.dismissed_update_version)

            cfg.dismissed_update_version = "v0.1.6"
            cfg.save()

            cfg2 = ConfigManager(config_dir=Path(tmpdir))
            self.assertEqual(cfg2.dismissed_update_version, "v0.1.6")

    def test_dismissed_update_filtering(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = ConfigManager(config_dir=Path(tmpdir))
            latest = "v0.1.6"

            has_notification = (cfg.dismissed_update_version != latest)
            self.assertTrue(has_notification)

            cfg.dismissed_update_version = latest
            cfg.save()
            has_notification = (cfg.dismissed_update_version != latest)
            self.assertFalse(has_notification)

            new_latest = "v0.1.7"
            has_notification = (cfg.dismissed_update_version != new_latest)
            self.assertTrue(has_notification)


if __name__ == "__main__":
    unittest.main()
