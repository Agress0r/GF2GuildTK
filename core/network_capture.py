"""Decode guild score snapshots from the game's TCP/7001 protobuf traffic.

The observed wire format is a 3-byte little-endian sequence number, a 2-byte
little-endian frame length, a 2-byte message ID, a 2-byte protobuf length, and
the protobuf payload. Message 0x559d contains the guild member score snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence


GUILD_SCORE_MESSAGE = 0x559D
DEFAULT_GAME_PORT = 7001


@dataclass(frozen=True)
class NetworkPlayer:
    uid: int
    name: str
    total_score: int


@dataclass(frozen=True)
class NetworkSnapshot:
    source: Path
    captured_at: datetime
    guild_name: str
    players: tuple[NetworkPlayer, ...]


class CaptureDecodeError(ValueError):
    pass


def _varint(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    for shift in range(0, 70, 7):
        if offset >= len(data):
            raise CaptureDecodeError("Truncated protobuf varint")
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, offset
    raise CaptureDecodeError("Protobuf varint is too long")


def _fields(data: bytes) -> list[tuple[int, int, int | bytes]]:
    result = []
    offset = 0
    while offset < len(data):
        tag, offset = _varint(data, offset)
        number, wire = tag >> 3, tag & 7
        if number == 0 or wire not in (0, 1, 2, 5):
            raise CaptureDecodeError("Invalid protobuf field")
        if wire == 0:
            value, offset = _varint(data, offset)
        elif wire == 1:
            if offset + 8 > len(data):
                raise CaptureDecodeError("Truncated fixed64 field")
            value = int.from_bytes(data[offset:offset + 8], "little")
            offset += 8
        elif wire == 5:
            if offset + 4 > len(data):
                raise CaptureDecodeError("Truncated fixed32 field")
            value = int.from_bytes(data[offset:offset + 4], "little")
            offset += 4
        else:
            size, offset = _varint(data, offset)
            if offset + size > len(data):
                raise CaptureDecodeError("Truncated length-delimited field")
            value = data[offset:offset + size]
            offset += size
        result.append((number, wire, value))
    return result


def _one(fields: list[tuple[int, int, int | bytes]], number: int,
         wire: int, default=None):
    values = [value for field, kind, value in fields if field == number and kind == wire]
    if len(values) > 1:
        raise CaptureDecodeError(f"Repeated singular field {number}")
    return values[0] if values else default


def decode_guild_score_message(payload: bytes, source: Path,
                               captured_at: datetime) -> NetworkSnapshot:
    root = _fields(payload)
    member_blobs = [value for number, wire, value in root if number == 1 and wire == 2]
    if not member_blobs:
        raise CaptureDecodeError("Guild score message contains no players")

    players = []
    guild_names = set()
    seen_uids = set()
    for blob in member_blobs:
        member = _fields(blob)
        wrapper_blob = _one(member, 1, 2)
        if wrapper_blob is None:
            raise CaptureDecodeError("Member has no profile")
        wrapper = _fields(wrapper_blob)
        profile_blob = _one(wrapper, 1, 2)
        if profile_blob is None:
            raise CaptureDecodeError("Member has no player details")
        profile = _fields(profile_blob)
        uid = _one(profile, 1, 0)
        name_bytes = _one(profile, 2, 2)
        if not isinstance(uid, int) or uid <= 0 or not isinstance(name_bytes, bytes):
            raise CaptureDecodeError("Member UID or name is missing")
        name = name_bytes.decode("utf-8")
        if not name.strip() or uid in seen_uids:
            raise CaptureDecodeError("Empty name or duplicate UID in guild snapshot")
        row_uid = _one(member, 7, 0)
        if row_uid is not None and row_uid != uid:
            raise CaptureDecodeError("Member UID does not match profile UID")
        score = _one(member, 6, 0, 0)  # Protobuf omits a zero score.
        if not isinstance(score, int) or score < 0:
            raise CaptureDecodeError("Invalid member score")
        guild_bytes = _one(profile, 13, 2)
        if isinstance(guild_bytes, bytes):
            guild_names.add(guild_bytes.decode("utf-8"))
        seen_uids.add(uid)
        players.append(NetworkPlayer(uid, name, score))

    if len(guild_names) != 1:
        raise CaptureDecodeError("Guild names in snapshot do not agree")
    return NetworkSnapshot(source, captured_at, guild_names.pop(), tuple(players))


def _contiguous_chunks(segments: dict[int, bytes]):
    chunk = bytearray()
    for sequence, data in sorted(segments.items()):
        if not chunk:
            start = sequence
            chunk.extend(data)
            continue
        overlap = start + len(chunk) - sequence
        if overlap < 0:
            yield bytes(chunk)
            start = sequence
            chunk = bytearray(data)
        elif len(data) > overlap:
            chunk.extend(data[max(0, overlap):])
    if chunk:
        yield bytes(chunk)


def _guild_payloads(stream: bytes):
    offset = 0
    while offset + 9 <= len(stream):
        frame_size = int.from_bytes(stream[offset + 3:offset + 5], "little")
        end = offset + 5 + frame_size
        if frame_size >= 4 and end <= len(stream):
            body = stream[offset + 5:end]
            message_id = int.from_bytes(body[:2], "little")
            protobuf_size = int.from_bytes(body[2:4], "little")
            if protobuf_size == frame_size - 4:
                if message_id == GUILD_SCORE_MESSAGE:
                    yield body[4:]
                offset = end
                continue
        offset += 1  # Capture may start part-way through a frame.


class LiveGuildDecoder:
    """Reassemble game TCP traffic as packets arrive and find a complete reply."""

    MAX_FLOW_BYTES = 4 * 1024 * 1024

    def __init__(self, source: Path, game_port: int = DEFAULT_GAME_PORT):
        self.source = source
        self.game_port = game_port
        self.streams: dict[tuple[str, int, str, int], dict[int, bytes]] = {}
        self.game_packets = 0
        self.server_bytes = 0
        self.snapshot: NetworkSnapshot | None = None
        self._bad_payloads: set[tuple[int, int]] = set()

    def feed(self, packet) -> NetworkSnapshot | None:
        if self.snapshot is not None:
            return self.snapshot
        from scapy.all import IP, TCP

        if IP not in packet or TCP not in packet:
            return None
        ip, tcp = packet[IP], packet[TCP]
        if tcp.sport != self.game_port and tcp.dport != self.game_port:
            return None
        self.game_packets += 1
        if tcp.sport != self.game_port:
            return None
        data = bytes(tcp.payload)
        if not data:
            return None
        self.server_bytes += len(data)
        key = (ip.src, tcp.sport, ip.dst, tcp.dport)
        segments = self.streams.setdefault(key, {})
        if len(data) <= len(segments.get(tcp.seq, b"")):
            return None
        segments[tcp.seq] = data
        total = sum(map(len, segments.values()))
        if total > self.MAX_FLOW_BYTES:
            for sequence in sorted(segments):
                total -= len(segments.pop(sequence))
                if total <= self.MAX_FLOW_BYTES // 2:
                    break
        at = datetime.fromtimestamp(float(packet.time), tz=timezone.utc)
        for chunk in _contiguous_chunks(segments):
            for payload in _guild_payloads(chunk):
                signature = (len(payload), hash(payload))
                if signature in self._bad_payloads:
                    continue
                try:
                    self.snapshot = decode_guild_score_message(payload, self.source, at)
                    return self.snapshot
                except (CaptureDecodeError, UnicodeError):
                    self._bad_payloads.add(signature)
        return None


def load_guild_snapshot(
    path: str | Path | Sequence[str | Path], game_port: int = DEFAULT_GAME_PORT
) -> NetworkSnapshot:
    """Return the latest complete snapshot across one or more consecutive captures."""
    try:
        from scapy.all import IP, TCP, PcapReader
    except ImportError as exc:
        raise RuntimeError("Для импорта PCAP установите scapy (pip install scapy).") from exc

    sources = [Path(path)] if isinstance(path, (str, Path)) else [Path(p) for p in path]
    if not sources:
        raise CaptureDecodeError("Нет файлов захвата для проверки.")
    streams: dict[tuple[str, int, str, int], dict[int, bytes]] = {}
    last_seen: dict[tuple[str, int, str, int], float] = {}
    game_packets = 0
    server_bytes = 0
    for source in sources:
        with PcapReader(str(source)) as packets:
            for packet in packets:
                if IP not in packet or TCP not in packet:
                    continue
                ip, tcp = packet[IP], packet[TCP]
                if tcp.sport != game_port and tcp.dport != game_port:
                    continue
                game_packets += 1
                if tcp.sport != game_port:
                    continue
                data = bytes(tcp.payload)
                if not data:
                    continue
                server_bytes += len(data)
                key = (ip.src, tcp.sport, ip.dst, tcp.dport)
                segments = streams.setdefault(key, {})
                if len(data) > len(segments.get(tcp.seq, b"")):
                    segments[tcp.seq] = data
                last_seen[key] = max(last_seen.get(key, 0), float(packet.time))

    snapshots = []
    damaged_messages = 0
    for key, segments in streams.items():
        at = datetime.fromtimestamp(last_seen[key], tz=timezone.utc)
        for chunk in _contiguous_chunks(segments):
            for payload in _guild_payloads(chunk):
                try:
                    snapshots.append(decode_guild_score_message(payload, sources[-1], at))
                except (CaptureDecodeError, UnicodeError):
                    damaged_messages += 1
    if not snapshots:
        if not game_packets:
            reason = "Во время записи не было пакетов игры TCP/7001. Дождитесь статуса «Захват идёт», затем откройте вкладку клана."
        elif not server_bytes:
            reason = "Пакеты игры были, но сервер не прислал данные. Перейдите на другую вкладку игры и снова откройте список клана."
        elif damaged_messages:
            reason = "Ответ клана пришёл, но оказался неполным или повреждённым. Повторите захват после загрузки вкладки."
        else:
            reason = (
                f"Получено {server_bytes:,} байт от сервера игры, но полного ответа клана 0x559d нет. "
                "Перейдите на другую вкладку игры и снова откройте список участников."
            )
        raise CaptureDecodeError(reason)
    return max(enumerate(snapshots), key=lambda item: (item[1].captured_at, item[0]))[1]
