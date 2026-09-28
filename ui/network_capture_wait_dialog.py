"""Wait for an automatically decoded guild response from Npcap."""

from __future__ import annotations

import threading
import sys

from PyQt6.QtCore import QThread, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QDialog, QLabel, QPushButton, QVBoxLayout

from core.live_capture import CaptureCancelled, capture_guild_snapshot, diagnose_live_capture
from core.network_capture import CaptureDecodeError
from core.npcap_setup import (
    NPCAP_DOWNLOAD_PAGE, download_npcap_installer, launch_npcap_installer, npcap_installed,
)
from ui.style_utils import mb_question
from ui.theme import DIALOG_STYLE, style_for


class _NpcapDownloadWorker(QThread):
    ready = pyqtSignal(object)
    error = pyqtSignal(str)

    def run(self):
        try:
            installer = download_npcap_installer(self.isInterruptionRequested)
            if not self.isInterruptionRequested():
                self.ready.emit(installer)
        except InterruptedError:
            pass
        except Exception as exc:
            self.error.emit(str(exc))


class _LiveCaptureWorker(QThread):
    snapshot_ready = pyqtSignal(object)
    capture_ready = pyqtSignal()
    progress = pyqtSignal(int, int)
    missing = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def run(self):
        try:
            snapshot = capture_guild_snapshot(
                self._stop_event,
                on_ready=self.capture_ready.emit,
                on_progress=self.progress.emit,
            )
            self.snapshot_ready.emit(snapshot)
        except CaptureCancelled:
            pass
        except CaptureDecodeError as exc:
            self.missing.emit(str(exc))
        except Exception as exc:
            self.error.emit(str(exc))


class NetworkCaptureWaitDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Ожидание данных клана")
        self.setStyleSheet(style_for(DIALOG_STYLE))
        self.setMinimumWidth(560)
        self.snapshot = None
        self._worker: _LiveCaptureWorker | None = None
        self._download_worker: _NpcapDownloadWorker | None = None
        self._closing = False

        layout = QVBoxLayout(self)
        checks = diagnose_live_capture()
        self.diagnostics = QLabel("\n".join(
            f"{'✓' if ok else '⚠'} {message}" for ok, message in checks
        ))
        self.diagnostics.setWordWrap(True)
        layout.addWidget(self.diagnostics)
        self.status = QLabel("Подключаюсь к сетевым интерфейсам…")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        layout.addWidget(QLabel(
            "Откройте в игре список участников клана. Когда придёт полный ответ, предпросмотр появится автоматически."
        ))
        self.retry_button = QPushButton("Повторить захват")
        self.retry_button.hide()
        self.retry_button.clicked.connect(self._start)
        layout.addWidget(self.retry_button)
        self.install_button = QPushButton("Скачать и установить Npcap")
        self.install_button.hide()
        self.install_button.clicked.connect(self._offer_install)
        layout.addWidget(self.install_button)
        self.website_button = QPushButton("Открыть официальный сайт Npcap")
        self.website_button.hide()
        self.website_button.clicked.connect(self._open_website)
        layout.addWidget(self.website_button)
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(self.reject)
        layout.addWidget(cancel)

        if all(ok for ok, _ in checks):
            self._start()
        else:
            self._show_failed_checks()
            if self.install_button.isHidden() is False:
                QTimer.singleShot(0, self._offer_initial_install)

    def _show_failed_checks(self):
        self.status.setText("Исправьте отмеченные условия и повторите захват.")
        self.retry_button.show()
        missing = sys.platform == "win32" and not npcap_installed()
        self.install_button.setVisible(missing)
        self.website_button.setVisible(missing)
        if missing:
            self.status.setText("Для сетевого импорта нужен Npcap. Можно скачать и запустить установщик.")

    def _offer_initial_install(self):
        if self.isVisible() and not self._closing:
            self._offer_install()

    def _open_website(self):
        if not QDesktopServices.openUrl(QUrl(NPCAP_DOWNLOAD_PAGE)):
            self.status.setText(f"Не удалось открыть браузер. Сайт загрузки: {NPCAP_DOWNLOAD_PAGE}")

    def _offer_install(self):
        if self._download_worker is not None and self._download_worker.isRunning():
            return
        if not mb_question(
            self, "Установка Npcap",
            "Скачать Npcap с официального сайта и запустить установщик?\n\n"
            "Windows запросит права администратора. Пройдите шаги установки, "
            "затем нажмите «Проверить после установки»."
        ):
            return
        self.status.setText("Скачиваю установщик Npcap и проверяю цифровую подпись…")
        self.install_button.setEnabled(False)
        self.retry_button.setEnabled(False)
        self._download_worker = _NpcapDownloadWorker(self)
        self._download_worker.ready.connect(self._launch_installer)
        self._download_worker.error.connect(self._installation_error)
        self._download_worker.finished.connect(self._download_finished)
        self._download_worker.start()

    def _launch_installer(self, installer):
        if self._closing:
            return
        try:
            launch_npcap_installer(installer)
        except OSError as exc:
            self._installation_error(f"Установщик не запущен (возможно, запрос прав отменён): {exc}")
            return
        self.status.setText(
            "Завершите установку Npcap, затем нажмите «Проверить после установки». "
            "Если драйвер не станет доступен, перезапустите приложение."
        )
        self.retry_button.setText("Проверить после установки")

    def _installation_error(self, message):
        if not self._closing:
            self.status.setText(f"Не удалось установить Npcap: {message}\nМожно скачать его вручную с официального сайта.")

    def _download_finished(self):
        self.install_button.setEnabled(True)
        self.retry_button.setEnabled(True)
        if self._closing:
            super().reject()

    def _start(self):
        if self._worker is not None and self._worker.isRunning():
            return
        checks = diagnose_live_capture()
        self.diagnostics.setText("\n".join(
            f"{'✓' if ok else '⚠'} {message}" for ok, message in checks
        ))
        if not all(ok for ok, _ in checks):
            self._show_failed_checks()
            return
        self.install_button.hide()
        self.website_button.hide()
        self.retry_button.setText("Повторить захват")
        self.retry_button.hide()
        self.status.setText("Подключаюсь к сетевым интерфейсам…")
        self._worker = _LiveCaptureWorker(self)
        self._worker.capture_ready.connect(self._on_ready)
        self._worker.progress.connect(self._on_progress)
        self._worker.snapshot_ready.connect(self._on_found)
        self._worker.missing.connect(self._on_missing)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_ready(self):
        self.status.setText("Захват идёт. Откройте список участников клана в игре…")

    def _on_progress(self, packets: int, server_bytes: int):
        if server_bytes:
            self.status.setText(
                f"Получено {server_bytes:,} байт от игры; собираю полный ответ клана…"
            )
        elif packets:
            self.status.setText("Соединение игры замечено; ожидаю ответ сервера…")

    def _on_found(self, snapshot):
        self.snapshot = snapshot
        if self._worker is not None:
            self._worker.wait()
        self.accept()

    def _on_missing(self, reason: str):
        if self._worker is not None:
            self._worker.wait()
        self.status.setText(reason)
        self.retry_button.show()

    def _on_error(self, message: str):
        if self._worker is not None:
            self._worker.wait()
        self.status.setText(f"Не удалось захватить данные: {message}")
        self.retry_button.show()

    def reject(self):
        if self._download_worker is not None and self._download_worker.isRunning():
            self._closing = True
            self._download_worker.requestInterruption()
            self.status.setText("Отменяю загрузку…")
            return
        if self._worker is not None and self._worker.isRunning():
            self._worker.stop()
            self._worker.wait()
        super().reject()
