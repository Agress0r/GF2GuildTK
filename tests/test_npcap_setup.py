"""Npcap setup checks without downloading or installing a system driver."""

import io
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from core import npcap_setup
from ui import network_capture_wait_dialog as capture_ui


class NpcapDownloadTests(unittest.TestCase):
    def test_only_official_https_downloads_are_allowed(self):
        for url in ("http://npcap.com/a.exe", "https://example.com/a.exe",
                    "https://npcap.com.evil.test/a.exe", "https://user@npcap.com/a.exe"):
            with self.subTest(url=url), self.assertRaises(RuntimeError):
                npcap_setup._official_url(url)
        self.assertEqual(npcap_setup._official_url("https://npcap.com/dist/npcap-1.89.exe"),
                         "https://npcap.com/dist/npcap-1.89.exe")

    def _download(self, folder, signature_result=0, cancelled=lambda: False):
        opener = Mock()
        opener.open.side_effect = [
            io.BytesIO(b'<a href="/dist/npcap-1.89.exe">Installer</a>'),
            io.BytesIO(b"installer bytes"),
        ]
        with patch.object(npcap_setup, "build_opener", return_value=opener), \
             patch.object(npcap_setup.tempfile, "mkdtemp", return_value=str(folder)), \
             patch.object(npcap_setup.subprocess, "run",
                          return_value=SimpleNamespace(returncode=signature_result)) as verify:
            result = npcap_setup.download_npcap_installer(cancelled)
        return result, verify

    def test_download_is_verified_before_it_can_be_launched(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "download"
            folder.mkdir()
            installer, verify = self._download(folder)
            self.assertEqual(installer.read_bytes(), b"installer bytes")
            self.assertEqual(verify.call_args.kwargs["env"]["GUILDTRACKER_NPCAP_INSTALLER"],
                             str(installer))
            self.assertIn("Get-AuthenticodeSignature", verify.call_args.args[0][-1])
            with patch.object(npcap_setup.os, "startfile") as launch:
                npcap_setup.launch_npcap_installer(installer)
            launch.assert_called_once_with(str(installer), "runas")

    def test_invalid_signature_removes_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "download"
            folder.mkdir()
            with self.assertRaisesRegex(RuntimeError, "подпись"):
                self._download(folder, signature_result=1)
            self.assertFalse(folder.exists())

    def test_cancelled_download_is_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "download"
            folder.mkdir()
            with self.assertRaises(InterruptedError):
                self._download(folder, cancelled=lambda: True)
            self.assertFalse(folder.exists())


class NpcapSetupUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_missing_driver_offers_install_and_decline_keeps_retry(self):
        with patch.object(capture_ui, "sys", SimpleNamespace(platform="win32")), \
             patch.object(capture_ui, "npcap_installed", return_value=False), \
             patch.object(capture_ui, "diagnose_live_capture", return_value=[(False, "Нет Npcap")]), \
             patch.object(capture_ui, "mb_question", return_value=False) as question, \
             patch.object(capture_ui, "download_npcap_installer") as download:
            dialog = capture_ui.NetworkCaptureWaitDialog()
            dialog.show()
            self.app.processEvents()
            question.assert_called_once()
            download.assert_not_called()
            self.assertTrue(dialog.install_button.isVisible())
            self.assertTrue(dialog.retry_button.isVisible())
            dialog.reject()

    def test_installed_but_unavailable_driver_does_not_offer_reinstall(self):
        with patch.object(capture_ui, "sys", SimpleNamespace(platform="win32")), \
             patch.object(capture_ui, "npcap_installed", return_value=True), \
             patch.object(capture_ui, "diagnose_live_capture", return_value=[(False, "Перезапустите")]), \
             patch.object(capture_ui, "mb_question") as question:
            dialog = capture_ui.NetworkCaptureWaitDialog()
            dialog.show()
            self.app.processEvents()
            question.assert_not_called()
            self.assertTrue(dialog.install_button.isHidden())
            self.assertFalse(dialog.retry_button.isHidden())
            dialog.reject()

    def test_install_launch_and_recheck_do_not_start_capture_while_driver_unavailable(self):
        with patch.object(capture_ui, "sys", SimpleNamespace(platform="win32")), \
             patch.object(capture_ui, "npcap_installed", return_value=False), \
             patch.object(capture_ui, "diagnose_live_capture", return_value=[(False, "Нет Npcap")]), \
             patch.object(capture_ui, "mb_question", return_value=True), \
             patch.object(capture_ui, "download_npcap_installer", return_value=Path("npcap.exe")), \
             patch.object(capture_ui, "launch_npcap_installer") as launch, \
             patch.object(capture_ui, "capture_guild_snapshot") as capture:
            dialog = capture_ui.NetworkCaptureWaitDialog()
            dialog._offer_install()
            deadline = time.monotonic() + 2
            while not launch.called and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(.01)
            dialog._download_worker.wait()
            self.app.processEvents()
            launch.assert_called_once_with(Path("npcap.exe"))
            self.assertEqual(dialog.retry_button.text(), "Проверить после установки")
            dialog._start()
            capture.assert_not_called()
            dialog.reject()


if __name__ == "__main__":
    unittest.main()
