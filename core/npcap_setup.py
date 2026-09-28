"""Download the official interactive Npcap installer on user request."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, build_opener


NPCAP_DOWNLOAD_PAGE = "https://npcap.com/#download"
MAX_INSTALLER_BYTES = 64 * 1024 * 1024


def npcap_installed() -> bool:
    """Check the driver registration, independently of Scapy's cached state."""
    if sys.platform != "win32":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SYSTEM\CurrentControlSet\Services\npcap"):
            return True
    except FileNotFoundError:
        return False


def _official_url(url: str) -> str:
    parsed = urlparse(url)
    if (parsed.scheme != "https" or parsed.hostname != "npcap.com"
            or parsed.port not in (None, 443) or parsed.username or parsed.password):
        raise RuntimeError("Загрузка разрешена только с официального сайта https://npcap.com.")
    return url


class _OfficialRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return super().redirect_request(req, fp, code, msg, headers, _official_url(newurl))


def download_npcap_installer(cancelled: Callable[[], bool]) -> Path:
    """Resolve the current release, download it and verify its Windows signature."""
    opener = build_opener(_OfficialRedirects())
    with opener.open("https://npcap.com/", timeout=20) as response:
        html = response.read(1024 * 1024).decode("utf-8")
    match = re.search(r'href=[\"\']([^\"\']*npcap-[0-9]+(?:\.[0-9]+)*\.exe)[\"\']', html)
    if match is None:
        raise RuntimeError("Не удалось найти установщик. Откройте официальный сайт Npcap.")
    url = _official_url(urljoin("https://npcap.com/", match.group(1)))
    folder = Path(tempfile.mkdtemp(prefix="guildtracker-npcap-"))
    installer = folder / Path(urlparse(url).path).name
    try:
        total = 0
        with opener.open(url, timeout=20) as response, installer.open("wb") as target:
            while True:
                if cancelled():
                    raise InterruptedError("Загрузка отменена.")
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_INSTALLER_BYTES:
                    raise RuntimeError("Размер установщика превышает допустимый.")
                target.write(chunk)
        if cancelled():
            raise InterruptedError("Загрузка отменена.")
        # Pass the filename as data, never interpolate it into shell code.
        env = dict(os.environ, GUILDTRACKER_NPCAP_INSTALLER=str(installer))
        powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        result = subprocess.run(
            [str(powershell), "-NoProfile", "-NonInteractive", "-Command",
             "$s = Get-AuthenticodeSignature -LiteralPath $env:GUILDTRACKER_NPCAP_INSTALLER; "
             "if ($s.Status -eq 'Valid') { exit 0 } else { exit 1 }"],
            env=env, capture_output=True, timeout=45,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode:
            raise RuntimeError("Цифровая подпись установщика не прошла проверку. Запуск отменён.")
        return installer
    except Exception:
        installer.unlink(missing_ok=True)
        folder.rmdir()
        raise


def launch_npcap_installer(installer: Path) -> None:
    """Use Windows UAC and the normal installer UI; never install silently."""
    os.startfile(str(installer), "runas")
