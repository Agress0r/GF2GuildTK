"""Continuous, automatic guild capture through the local Npcap driver."""

from __future__ import annotations

import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable
from uuid import uuid4

from core.network_capture import LiveGuildDecoder, NetworkSnapshot, load_guild_snapshot
from core.npcap_setup import npcap_installed


ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
CAPTURE_SECONDS = 180


class CaptureCancelled(Exception):
    pass


def _interfaces() -> list[str]:
    from scapy.all import get_working_ifaces

    # The game may use a VPN, virtual NIC, or loopback adapter. Listen on all
    # interfaces, with a kernel BPF filter limiting traffic to the game port.
    return list(dict.fromkeys(iface.network_name for iface in get_working_ifaces()
                              if iface.network_name))


def diagnose_live_capture() -> list[tuple[bool, str]]:
    if sys.platform != "win32":
        return [(False, "Автоматический сетевой захват доступен только в Windows.")]
    try:
        from scapy.all import conf
        npc = bool(conf.use_pcap)
        interfaces = _interfaces() if npc else []
    except Exception as exc:
        return [(False, f"Не удалось проверить Npcap: {exc}")]
    return [
        (npc, "Драйвер Npcap доступен." if npc else (
            "Npcap установлен, но недоступен. После установки перезапустите приложение; "
            "если это не поможет, проверьте драйвер и права доступа."
            if npcap_installed() else "Установите Npcap для автоматического захвата."
        )),
        (bool(interfaces), f"Сетевых интерфейсов для прослушивания: {len(interfaces)}."),
        (os.access(ROOT, os.W_OK), "Каталог приложения доступен для диагностического файла."),
    ]


def capture_guild_snapshot(
    stop_event: threading.Event,
    on_ready: Callable[[], None] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    timeout: int = CAPTURE_SECONDS,
) -> NetworkSnapshot:
    """Listen without gaps until a full guild response arrives or time expires."""
    from scapy.all import AsyncSniffer, IP, PcapWriter

    failed = [message for ok, message in diagnose_live_capture() if not ok]
    if failed:
        raise RuntimeError("\n".join(failed))
    session = ROOT / "captures" / "live" / (
        datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid4().hex[:8]
    )
    session.mkdir(parents=True)
    capture_path = session / "game_7001.pcap"
    decoder = LiveGuildDecoder(capture_path)
    ready = threading.Event()
    found = threading.Event()
    writer = PcapWriter(str(capture_path), linktype=228, sync=True)

    def handle_packet(packet):
        if IP not in packet:
            return
        writer.write(packet[IP])
        if decoder.feed(packet) is not None:
            found.set()

    sniffer = AsyncSniffer(
        iface=_interfaces(), filter="tcp port 7001", store=False,
        prn=handle_packet, started_callback=ready.set,
    )
    try:
        sniffer.start()
        if not ready.wait(8):
            if sniffer.thread is not None and not sniffer.thread.is_alive():
                sniffer.join()
            raise RuntimeError("Не удалось запустить Npcap. Проверьте драйвер и сетевые адаптеры.")
        if on_ready:
            on_ready()
        deadline = time.monotonic() + timeout
        last_progress = (-1, -1)
        while not stop_event.is_set() and not found.is_set() and time.monotonic() < deadline:
            if sniffer.thread is not None and not sniffer.thread.is_alive():
                sniffer.join()
                break
            progress = (decoder.game_packets, decoder.server_bytes)
            if on_progress and progress != last_progress:
                on_progress(*progress)
                last_progress = progress
            found.wait(0.25)
    finally:
        try:
            if getattr(sniffer, "running", False):
                sniffer.stop(join=True)
            elif getattr(sniffer, "thread", None) is not None:
                sniffer.join()
        finally:
            writer.close()
    if stop_event.is_set():
        raise CaptureCancelled()
    if decoder.snapshot is not None:
        return decoder.snapshot
    # A final whole-file read also gives a specific diagnosis when the game
    # sent traffic but no guild response (or the response was incomplete).
    return load_guild_snapshot(capture_path)
