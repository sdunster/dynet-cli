"""Capture files: one JSON object per line, written by ``monitor`` and ``sniff``.

A packet line looks like::

    {"t": 1794969681.956, "dir": "rx", "hex": "1c0c0000000000ffd5"}

where ``dir`` is ``rx`` (from the gateway) or ``tx`` (to the gateway). A DyNet 2
frame has its hex under ``dynet2`` instead of ``hex``. Lines with an ``event``
key record something that isn't a packet, such as a connection, a gap in a
sniffed stream or noise. ``decode`` also accepts plain hex packets and frames,
one per line.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, TextIO

from aiodynet import (
    PACKET_LENGTH,
    Dynet2Frame,
    InvalidPacketError,
    Packet,
    PacketFramer,
    UnknownMessage,
    decode,
)

from .render import describe_dynet2, message_area, message_kind

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

type Direction = Literal["rx", "tx"]


@dataclass(frozen=True, slots=True)
class PacketRecord:
    """A packet seen on the wire."""

    time: float | None
    direction: Direction | None
    packet: Packet


@dataclass(frozen=True, slots=True)
class Dynet2Record:
    """A DyNet 2 frame seen on the wire."""

    time: float | None
    direction: Direction | None
    frame: Dynet2Frame


@dataclass(frozen=True, slots=True)
class EventRecord:
    """Something in a capture that isn't a packet."""

    time: float | None
    event: str
    details: dict[str, Any]


type Record = PacketRecord | Dynet2Record | EventRecord


class CaptureWriter:
    """Write capture records as JSON lines, flushing each one."""

    def __init__(self, stream: TextIO) -> None:
        """Write to `stream`."""
        self._stream = stream

    def packet(self, time: float, direction: Direction, packet: Packet) -> None:
        """Record a packet."""
        self._write({"t": round(time, 3), "dir": direction, "hex": packet.hex("")})

    def dynet2(self, time: float, direction: Direction, frame: Dynet2Frame) -> None:
        """Record a DyNet 2 frame."""
        self._write({"t": round(time, 3), "dir": direction, "dynet2": frame.hex("")})

    def event(self, time: float, event: str, **details: Any) -> None:  # noqa: ANN401
        """Record an event, such as ``connect`` or ``gap``."""
        self._write({"t": round(time, 3), "event": event, **details})

    def _write(self, record: dict[str, Any]) -> None:
        self._stream.write(json.dumps(record, separators=(",", ":")) + "\n")
        self._stream.flush()


class CaptureFormatError(ValueError):
    """A line in a capture file that can't be read."""


def read_records(lines: Iterable[str]) -> Iterator[Record]:
    """Read records from capture lines or plain hex packets.

    A plain hex line of 7 or 8 bytes is one packet (the checksum is added or
    checked). Any other length is treated as part of a raw byte stream and
    framed the way the client frames a TCP stream, DyNet 2 frames included.
    """
    framer = PacketFramer()
    for number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("{"):
            yield _parse_json(line, number)
            continue
        try:
            data = bytes.fromhex("".join(line.replace(":", " ").split()))
        except ValueError as err:
            msg = f"Line {number}: not JSON or hex: {line[:60]!r}"
            raise CaptureFormatError(msg) from err
        if len(data) in {PACKET_LENGTH - 1, PACKET_LENGTH} and not framer.pending:
            # One packet per line, as people type them: with or without checksum.
            try:
                packet = Packet.from_hex(data.hex())
            except InvalidPacketError as err:
                msg = f"Line {number}: {err}"
                raise CaptureFormatError(msg) from err
            yield PacketRecord(time=None, direction=None, packet=packet)
            continue
        for item in framer.feed_all(data):
            if isinstance(item, Packet):
                yield PacketRecord(time=None, direction=None, packet=item)
            else:
                yield Dynet2Record(time=None, direction=None, frame=item)


def _parse_json(line: str, number: int) -> Record:
    try:
        record = json.loads(line)
        time = record.get("t")
        if "event" in record:
            details = {k: v for k, v in record.items() if k not in {"t", "event"}}
            return EventRecord(time=time, event=str(record["event"]), details=details)
        direction = record.get("dir")
        if direction not in {"rx", "tx", None}:
            msg = f"unknown direction {direction!r}"
            raise ValueError(msg)  # noqa: TRY301
        if "dynet2" in record:
            return Dynet2Record(
                time=time,
                direction=direction,
                frame=Dynet2Frame.from_hex(record["dynet2"]),
            )
        return PacketRecord(
            time=time, direction=direction, packet=Packet.from_hex(record["hex"])
        )
    except (ValueError, KeyError, AttributeError, InvalidPacketError) as err:
        msg = f"Line {number}: {err}"
        raise CaptureFormatError(msg) from err


@dataclass
class Summary:
    """Counts over a capture, to spot anything unexpected."""

    packets: int = 0
    by_kind: Counter[str] = field(default_factory=Counter)
    by_direction: Counter[str] = field(default_factory=Counter)
    areas: Counter[int] = field(default_factory=Counter)
    unknown: Counter[str] = field(default_factory=Counter)
    """Undecoded packets by hex, so each distinct one is listed once."""
    events: Counter[str] = field(default_factory=Counter)
    dynet2: Counter[str] = field(default_factory=Counter)
    """DyNet 2 frames by hex, so each distinct one is listed once."""
    first_time: float | None = None
    last_time: float | None = None

    def add(self, record: Record) -> None:
        """Count one record."""
        if record.time is not None:
            if self.first_time is None:
                self.first_time = record.time
            self.last_time = record.time
        if isinstance(record, EventRecord):
            self.events[record.event] += 1
            return
        if isinstance(record, Dynet2Record):
            self.dynet2[record.frame.hex("")] += 1
            return
        message = decode(record.packet)
        self.packets += 1
        self.by_kind[message_kind(message)] += 1
        self.by_direction[record.direction or "?"] += 1
        if (area := message_area(message)) is not None:
            self.areas[area] += 1
        if isinstance(message, UnknownMessage):
            self.unknown[record.packet.hex("")] += 1

    def render(self) -> str:
        """Return a readable report."""
        lines = [f"{self.packets} packet(s)"]
        if self.first_time is not None and self.last_time is not None:
            hours = (self.last_time - self.first_time) / 3600
            lines[0] += f" over {hours:.2f} h"
        if self.by_direction:
            lines.append(
                "  direction: "
                + ", ".join(f"{k} {v}" for k, v in sorted(self.by_direction.items()))
            )
        lines.append("By type:")
        lines.extend(
            f"  {count:>7}  {kind}" for kind, count in self.by_kind.most_common()
        )
        if self.areas:
            lines.append(
                "Areas: "
                + ", ".join(f"{a} ({n})" for a, n in sorted(self.areas.items()))
            )
        if self.events:
            lines.append("Events:")
            lines.extend(
                f"  {count:>7}  {event}" for event, count in self.events.most_common()
            )
        if self.dynet2:
            lines.append(
                f"DyNet 2 frames ({self.dynet2.total()}, {len(self.dynet2)} distinct):"
            )
            lines.extend(
                f"  {count:>7}  {raw}  {describe_dynet2(Dynet2Frame.from_hex(raw))}"
                for raw, count in self.dynet2.most_common()
            )
        if self.unknown:
            lines.append(f"Undecoded packets ({len(self.unknown)} distinct):")
            lines.extend(
                f"  {count:>7}  {raw}" for raw, count in self.unknown.most_common()
            )
        else:
            lines.append("Every packet decoded to a typed message.")
        return "\n".join(lines)
