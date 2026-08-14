from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import app_paths
import update_manager


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class UpdateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.previous_data_dir = os.environ.get("AKSHARE_PP_DATA_DIR")
        os.environ["AKSHARE_PP_DATA_DIR"] = self.temp.name
        app_paths.ensure_data_dirs()

    def tearDown(self) -> None:
        if self.previous_data_dir is None:
            os.environ.pop("AKSHARE_PP_DATA_DIR", None)
        else:
            os.environ["AKSHARE_PP_DATA_DIR"] = self.previous_data_dir
        self.temp.cleanup()

    def response(self, releases: dict) -> FakeResponse:
        return FakeResponse(json.dumps({"info": {"version": "9.9.9rc1"}, "releases": releases}).encode())

    def test_check_ignores_prerelease_and_yanked_release(self) -> None:
        releases = {
            "1.18.84": [{"yanked": False}],
            "1.18.85": [{"yanked": True}],
            "1.18.86rc1": [{"yanked": False}],
        }
        with mock.patch.object(update_manager, "installed_akshare_version", return_value="1.18.83"), mock.patch.object(
            update_manager.urllib.request, "urlopen", return_value=self.response(releases)
        ):
            result = update_manager.check_latest_version(force=True)
        self.assertEqual(result["latest_version"], "1.18.84")
        self.assertTrue(result["update_available"])

    def test_check_network_failure_keeps_current_version(self) -> None:
        with mock.patch.object(update_manager, "installed_akshare_version", return_value="1.18.84"), mock.patch.object(
            update_manager.urllib.request, "urlopen", side_effect=OSError("offline")
        ):
            result = update_manager.check_latest_version(force=True)
        self.assertEqual(result["current_version"], "1.18.84")
        self.assertIn("offline", result["error"])

    def test_install_validation_failure_does_not_switch(self) -> None:
        completed = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(update_manager.subprocess, "run", return_value=completed), mock.patch.object(
            update_manager, "_validate_runtime", side_effect=RuntimeError("incompatible")
        ):
            with self.assertRaisesRegex(RuntimeError, "incompatible"):
                update_manager.install_version("1.18.85")
        self.assertFalse(app_paths.runtime_state_file().exists())

    def test_restart_failure_restores_previous_runtime(self) -> None:
        old_runtime = app_paths.runtimes_dir() / "1.18.84"
        old_runtime.mkdir()
        original = {"active": {"version": "1.18.84", "path": str(old_runtime)}, "previous": None}
        app_paths.write_json(app_paths.runtime_state_file(), original)
        completed = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(update_manager.subprocess, "run", return_value=completed), mock.patch.object(
            update_manager, "_validate_runtime"
        ), mock.patch.object(update_manager, "restart_service", side_effect=[RuntimeError("health failed"), {"status": "ok"}]):
            with self.assertRaisesRegex(RuntimeError, "health failed"):
                update_manager.install_version("1.18.85")
        restored = app_paths.read_json(app_paths.runtime_state_file())
        self.assertEqual(restored, original)


if __name__ == "__main__":
    unittest.main()
