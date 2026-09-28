"""Network score decoding and import from representative TCP packets."""

import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from core.network_capture import (
    CaptureDecodeError, LiveGuildDecoder, NetworkPlayer, NetworkSnapshot,
    load_guild_snapshot,
)
from db import database
from core import live_capture


def _varint(value):
    result = bytearray()
    while value >= 128:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def _field(number, value):
    if isinstance(value, int):
        return _varint(number << 3) + _varint(value)
    return _varint((number << 3) | 2) + _varint(len(value)) + value


def _snapshot_message(uid=805814, name="Rshish", score=27550):
    profile = _field(1, uid) + _field(2, name.encode()) + _field(13, b"KCCO")
    wrapper = _field(1, profile)
    member = _field(1, wrapper) + _field(7, uid)
    if score:
        member += _field(6, score)
    return _field(1, member)


class NetworkCaptureTests(unittest.TestCase):
    def test_reassembles_repeated_tcp_packets_and_decodes_score(self):
        from scapy.all import IP, TCP, Raw, wrpcap

        protobuf = _snapshot_message()
        body = (0x559D).to_bytes(2, "little") + len(protobuf).to_bytes(2, "little") + protobuf
        frame = (190).to_bytes(3, "little") + len(body).to_bytes(2, "little") + body
        start = 12345
        first = IP(src="10.0.0.2", dst="10.0.0.1") / TCP(sport=7001, dport=50000, seq=start) / Raw(frame[:17])
        second = IP(src="10.0.0.2", dst="10.0.0.1") / TCP(sport=7001, dport=50000, seq=start + 17) / Raw(frame[17:])
        unrelated = IP(src="10.0.0.3", dst="10.0.0.1") / TCP(sport=443, dport=50001, seq=8) / Raw(b"noise")
        with tempfile.TemporaryDirectory() as tmp:
            capture = Path(tmp) / "test.pcap"
            wrpcap(str(capture), [second, unrelated, first, first])
            result = load_guild_snapshot(capture)
            self.assertEqual(result.guild_name, "KCCO")
            self.assertEqual(result.players, (NetworkPlayer(805814, "Rshish", 27550),))

    def test_reassembles_one_response_across_capture_files(self):
        from scapy.all import IP, TCP, Raw, wrpcap

        protobuf = _snapshot_message()
        body = (0x559D).to_bytes(2, "little") + len(protobuf).to_bytes(2, "little") + protobuf
        frame = (190).to_bytes(3, "little") + len(body).to_bytes(2, "little") + body
        first = IP(src="10.0.0.2", dst="10.0.0.1") / TCP(sport=7001, dport=50000, seq=12345) / Raw(frame[:17])
        second = IP(src="10.0.0.2", dst="10.0.0.1") / TCP(sport=7001, dport=50000, seq=12362) / Raw(frame[17:])
        with tempfile.TemporaryDirectory() as tmp:
            one, two = Path(tmp) / "one.pcap", Path(tmp) / "two.pcap"
            wrpcap(str(one), [first])
            wrpcap(str(two), [second])
            with self.assertRaises(CaptureDecodeError):
                load_guild_snapshot(one)
            result = load_guild_snapshot([one, two])
            self.assertEqual(len(result.players), 1)
            self.assertEqual(result.players[0].total_score, 27550)

    def test_live_decoder_finds_response_without_stopping_capture(self):
        from scapy.all import IP, TCP, Raw

        protobuf = _snapshot_message()
        body = (0x559D).to_bytes(2, "little") + len(protobuf).to_bytes(2, "little") + protobuf
        frame = (190).to_bytes(3, "little") + len(body).to_bytes(2, "little") + body
        first = IP(src="10.0.0.2", dst="10.0.0.1") / TCP(sport=7001, dport=50000, seq=12345) / Raw(frame[:17])
        second = IP(src="10.0.0.2", dst="10.0.0.1") / TCP(sport=7001, dport=50000, seq=12362) / Raw(frame[17:])
        decoder = LiveGuildDecoder(Path("live.pcap"))
        self.assertIsNone(decoder.feed(second))
        snapshot = decoder.feed(first)
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.players[0].total_score, 27550)

    def test_automatic_npcap_capture_saves_and_decodes(self):
        from scapy.all import IP, TCP, Raw

        protobuf = _snapshot_message()
        body = (0x559D).to_bytes(2, "little") + len(protobuf).to_bytes(2, "little") + protobuf
        frame = (190).to_bytes(3, "little") + len(body).to_bytes(2, "little") + body
        packets = [
            IP(src="10.0.0.2", dst="10.0.0.1") / TCP(sport=7001, dport=50000, seq=12345) / Raw(frame[:17]),
            IP(src="10.0.0.2", dst="10.0.0.1") / TCP(sport=7001, dport=50000, seq=12362) / Raw(frame[17:]),
        ]

        class FakeSniffer:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                self.running = False
                self.thread = None

            def start(self):
                self.kwargs["started_callback"]()
                for packet in packets:
                    self.kwargs["prn"](packet)

        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(live_capture, "ROOT", Path(tmp)), \
             patch.object(live_capture, "diagnose_live_capture", return_value=[(True, "ok")]), \
             patch.object(live_capture, "_interfaces", return_value=["mock-nic"]), \
             patch("scapy.all.AsyncSniffer", FakeSniffer):
            result = live_capture.capture_guild_snapshot(threading.Event())
            self.assertEqual(result.players[0].total_score, 27550)
            captures = list(Path(tmp).rglob("*.pcap"))
            self.assertEqual(len(captures), 1)
            self.assertEqual(len(load_guild_snapshot(captures[0]).players), 1)

    def test_explains_capture_without_game_traffic(self):
        from scapy.all import IP, TCP, Raw, wrpcap

        with tempfile.TemporaryDirectory() as tmp:
            capture = Path(tmp) / "other.pcap"
            wrpcap(str(capture), [IP() / TCP(sport=443, dport=50000) / Raw(b"other")])
            with self.assertRaisesRegex(CaptureDecodeError, "TCP/7001"):
                load_guild_snapshot(capture)

    def test_rejects_capture_without_complete_snapshot(self):
        from scapy.all import IP, TCP, Raw, wrpcap

        with tempfile.TemporaryDirectory() as tmp:
            capture = Path(tmp) / "empty.pcap"
            wrpcap(str(capture), [IP() / TCP(sport=7001, dport=50000) / Raw(b"partial")])
            with self.assertRaises(CaptureDecodeError):
                load_guild_snapshot(capture)

    def test_reviewed_uid_mapping_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "tracker.db"
            with patch.object(database, "load_settings", return_value={"db_path": str(db_path)}):
                database.init_db()
                season = database.create_season(1, "KCCO")
                old_id = database.upsert_player("Rshis", apply_fuzzy=False)
                snapshot = NetworkSnapshot(
                    Path("capture.pcapng"), datetime.now(timezone.utc), "KCCO",
                    (NetworkPlayer(805814, "Rshish", 27550),),
                )
                with self.assertRaises(ValueError):
                    database.import_network_scores(snapshot, season, 1, {})
                first = database.import_network_scores(snapshot, season, 1, {805814: old_id})
                second = database.import_network_scores(snapshot, season, 1, {805814: old_id})
                self.assertEqual((first["created"], second["created"]), (0, 0))
                conn = database.get_connection()
                try:
                    self.assertEqual(conn.execute(
                        "SELECT game_uid FROM players WHERE id=?", (old_id,)
                    ).fetchone()[0], 805814)
                    self.assertEqual(conn.execute(
                        "SELECT total_score FROM day_scores WHERE season_id=? AND player_id=?",
                        (season, old_id),
                    ).fetchone()[0], 27550)
                    self.assertEqual(conn.execute("SELECT COUNT(*) FROM players").fetchone()[0], 1)
                finally:
                    conn.close()


if __name__ == "__main__":
    unittest.main()
