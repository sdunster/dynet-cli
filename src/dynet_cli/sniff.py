"""Passively capture the DyNet traffic between a gateway and its client.

This watches the TCP connection something else (such as Home Assistant) has
open to a gateway, from the same host, without connecting to the gateway or
sending anything. It needs Linux (``AF_PACKET``) and root.

Each direction of each TCP connection is put back in order by sequence
number. Segments already seen (retransmits, and the one-byte keepalive probes
some gateways send) are dropped. If data is missing, a ``gap`` event is
recorded and framing restarts, so a lost segment can't produce a bogus packet.
"""

from __future__ import annotations

import ipaddress
import socket
import struct
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from aiodynet import PacketFramer

if TYPE_CHECKING:
    from collections.abc import Callable

    from .capture import CaptureWriter, Direction

ETH_P_ALL = 0x0003
_ETHERTYPE_IPV4 = 0x0800
_ETHERTYPE_VLAN = 0x8100
_PROTO_TCP = 6
_TCP_SYN = 0x02
_TCP_FIN = 0x01
_TCP_RST = 0x04
_SEQ_MODULO = 1 << 32


@dataclass(frozen=True, slots=True)
class Segment:
    """The parts of a TCP/IPv4 segment the sniffer needs."""

    src: str
    sport: int
    dst: str
    dport: int
    seq: int
    flags: int
    payload: bytes


def parse_frame(frame: bytes) -> Segment | None:
    """Parse an Ethernet frame. Returns ``None`` unless it is TCP over IPv4."""
    if len(frame) < 14:
        return None
    ethertype = struct.unpack_from("!H", frame, 12)[0]
    offset = 14
    if ethertype == _ETHERTYPE_VLAN:
        if len(frame) < 18:
            return None
        ethertype = struct.unpack_from("!H", frame, 16)[0]
        offset = 18
    if ethertype != _ETHERTYPE_IPV4 or len(frame) < offset + 20:
        return None
    ip = frame[offset:]
    header_length = (ip[0] & 0x0F) * 4
    total_length = struct.unpack_from("!H", ip, 2)[0]
    if ip[9] != _PROTO_TCP or len(ip) < header_length + 20:
        return None
    tcp = ip[header_length:total_length]
    if len(tcp) < 20:
        return None
    sport, dport, seq = struct.unpack_from("!HHI", tcp)
    data_offset = (tcp[12] >> 4) * 4
    return Segment(
        src=str(ipaddress.IPv4Address(ip[12:16])),
        sport=sport,
        dst=str(ipaddress.IPv4Address(ip[16:20])),
        dport=dport,
        seq=seq,
        flags=tcp[13],
        payload=bytes(tcp[data_offset:]),
    )


def _seq_diff(a: int, b: int) -> int:
    """Return ``a - b`` in TCP sequence space, allowing for wraparound."""
    diff = (a - b) % _SEQ_MODULO
    return diff - _SEQ_MODULO if diff >= _SEQ_MODULO // 2 else diff


class _Stream:
    """One direction of one TCP connection."""

    def __init__(self) -> None:
        self.next_seq: int | None = None
        self.framer = PacketFramer()


type StreamKey = tuple[str, int, str, int]


class Reassembler:
    """Turn TCP segments between a client and a gateway into DyNet packets."""

    def __init__(
        self, gateway_host: str, gateway_port: int, writer: CaptureWriter
    ) -> None:
        """Capture traffic to and from `gateway_host`:`gateway_port`."""
        self._host = gateway_host
        self._port = gateway_port
        self._writer = writer
        self._streams: dict[StreamKey, _Stream] = {}
        self.packets = 0

    def direction(self, segment: Segment) -> Direction | None:
        """Return ``rx``/``tx`` for gateway traffic, or ``None`` for anything else."""
        if segment.src == self._host and segment.sport == self._port:
            return "rx"
        if segment.dst == self._host and segment.dport == self._port:
            return "tx"
        return None

    def add(self, segment: Segment, now: float) -> None:
        """Process one segment."""
        direction = self.direction(segment)
        if direction is None:
            return
        key = (segment.src, segment.sport, segment.dst, segment.dport)
        client = (
            f"{segment.dst}:{segment.dport}"
            if direction == "rx"
            else f"{segment.src}:{segment.sport}"
        )
        if segment.flags & _TCP_SYN:
            self._streams[key] = stream = _Stream()
            stream.next_seq = (segment.seq + 1) % _SEQ_MODULO
            if direction == "tx":
                self._writer.event(now, "connect", client=client)
            return
        stream = self._streams.setdefault(key, _Stream())
        self._take_data(stream, segment, direction, client, now)
        if segment.flags & (_TCP_FIN | _TCP_RST):
            self._streams.pop(key, None)
            name = "reset" if segment.flags & _TCP_RST else "close"
            self._writer.event(now, name, dir=direction, client=client)

    def _take_data(
        self,
        stream: _Stream,
        segment: Segment,
        direction: Direction,
        client: str,
        now: float,
    ) -> None:
        data = segment.payload
        if not data:
            return
        if stream.next_seq is None:  # joined mid-connection
            stream.next_seq = segment.seq
        ahead = _seq_diff(segment.seq, stream.next_seq)
        if ahead > 0:
            self._writer.event(now, "gap", dir=direction, client=client, missing=ahead)
            stream.framer.reset()
        elif ahead < 0:
            if len(data) <= -ahead:
                return  # already seen: a retransmit or a keepalive probe
            data = data[-ahead:]
        stream.next_seq = (segment.seq + len(segment.payload)) % _SEQ_MODULO
        before = stream.framer.discarded
        for packet in stream.framer.feed(data):
            self.packets += 1
            self._writer.packet(now, direction, packet)
        if skipped := stream.framer.discarded - before:
            self._writer.event(
                now, "noise", dir=direction, client=client, bytes=skipped
            )


class RawSocket(Protocol):
    """The parts of a raw packet socket the sniffer uses."""

    def recv(self, bufsize: int, /) -> bytes:
        """Read one frame."""
        ...

    def settimeout(self, value: float | None, /) -> None:
        """Set the read timeout."""
        ...

    def close(self) -> None:
        """Close the socket."""
        ...


def open_raw_socket(interface: str) -> RawSocket:  # pragma: no cover - needs Linux root
    """Open a packet socket that sees every frame on `interface`."""
    family = getattr(socket, "AF_PACKET", None)
    if family is None:
        msg = "Sniffing needs Linux (AF_PACKET sockets)"
        raise OSError(msg)
    sock = socket.socket(family, socket.SOCK_RAW, socket.ntohs(ETH_P_ALL))
    sock.bind((interface, 0))
    return sock


def sniff(
    sock: RawSocket,
    reassembler: Reassembler,
    *,
    duration: float | None,
    clock: Callable[[], float] = time.time,
) -> None:
    """Read frames until `duration` seconds pass (or forever if ``None``)."""
    end = None if duration is None else clock() + duration
    sock.settimeout(1.0)
    while end is None or clock() < end:
        try:
            frame = sock.recv(65535)
        except TimeoutError:
            continue
        if (segment := parse_frame(frame)) is not None:
            reassembler.add(segment, clock())
