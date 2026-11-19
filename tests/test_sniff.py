import contextlib
import io
import ipaddress
import json
import struct
from typing import Any

from aiodynet import Dynet2Frame, PresetReport, PresetSelect, RequestPreset

from dynet_cli.capture import CaptureWriter
from dynet_cli.sniff import Reassembler, Segment, parse_frame, sniff

GATEWAY = ("192.0.2.10", 50000)
CLIENT = ("192.0.2.20", 51234)
SYN, FIN, RST, ACK, PSH = 0x02, 0x01, 0x04, 0x10, 0x08


def frame(
    src: tuple[str, int],
    dst: tuple[str, int],
    seq: int,
    payload: bytes = b"",
    flags: int = ACK | PSH,
    *,
    vlan: bool = False,
    protocol: int = 6,
) -> bytes:
    tcp = struct.pack("!HHIIBBHHH", src[1], dst[1], seq, 0, 5 << 4, flags, 65535, 0, 0)
    tcp += payload
    ip = struct.pack(
        "!BBHHHBBH4s4s",
        0x45,
        0,
        20 + len(tcp),
        0,
        0,
        64,
        protocol,
        0,
        ipaddress.IPv4Address(src[0]).packed,
        ipaddress.IPv4Address(dst[0]).packed,
    )
    ethernet = b"\x00" * 12
    if vlan:
        ethernet += b"\x81\x00\x00\x05"
    # Pad like a real NIC does for short frames; the IP length must be honoured.
    return ethernet + b"\x08\x00" + ip + tcp + b"\x00" * 6


def run_segments(*frames: bytes) -> tuple[Reassembler, list[dict[str, Any]]]:
    out = io.StringIO()
    reassembler = Reassembler(*GATEWAY, CaptureWriter(out))
    for number, raw in enumerate(frames):
        segment = parse_frame(raw)
        assert segment is not None
        reassembler.add(segment, now=1000.0 + number)
    return reassembler, [json.loads(line) for line in out.getvalue().splitlines()]


PRESET = bytes(PresetSelect(area=5, preset=2).to_packet())
REQUEST = bytes(RequestPreset(area=5).to_packet())
REPORT = bytes(PresetReport(area=5, preset=2).to_packet())


def test_parse_frame() -> None:
    segment = parse_frame(frame(CLIENT, GATEWAY, 7, b"abc", vlan=True))
    assert segment == Segment(
        src="192.0.2.20",
        sport=51234,
        dst="192.0.2.10",
        dport=50000,
        seq=7,
        flags=ACK | PSH,
        payload=b"abc",
    )


def test_parse_frame_ignores_other_traffic() -> None:
    assert parse_frame(b"\x00" * 10) is None
    assert parse_frame(b"\x00" * 12 + b"\x81\x00") is None  # truncated VLAN
    assert parse_frame(b"\x00" * 12 + b"\x86\xdd" + b"\x00" * 60) is None  # IPv6
    assert parse_frame(frame(CLIENT, GATEWAY, 0, protocol=17)) is None  # UDP
    truncated = frame(CLIENT, GATEWAY, 0, b"x")
    assert parse_frame(truncated[:40]) is None
    # An IP length too short to hold a TCP header.
    bad_length = bytearray(frame(CLIENT, GATEWAY, 0))
    struct.pack_into("!H", bad_length, 16, 30)
    assert parse_frame(bytes(bad_length)) is None


def test_both_directions_from_syn() -> None:
    reassembler, records = run_segments(
        frame(CLIENT, GATEWAY, 99, flags=SYN),
        frame(GATEWAY, CLIENT, 499, flags=SYN | ACK),
        frame(CLIENT, GATEWAY, 100, REQUEST),
        frame(GATEWAY, CLIENT, 500, REPORT),
    )
    assert records == [
        {"t": 1000.0, "event": "connect", "client": "192.0.2.20:51234"},
        {"t": 1002.0, "dir": "tx", "hex": REQUEST.hex()},
        {"t": 1003.0, "dir": "rx", "hex": REPORT.hex()},
    ]
    assert reassembler.packets == 2


def test_joins_mid_stream_and_splits_packets() -> None:
    _, records = run_segments(
        frame(GATEWAY, CLIENT, 1000, PRESET[:3]),
        frame(GATEWAY, CLIENT, 1003, PRESET[3:] + REPORT),
    )
    assert [r["hex"] for r in records] == [PRESET.hex(), REPORT.hex()]


def test_drops_keepalive_probes_and_retransmits() -> None:
    _, records = run_segments(
        frame(GATEWAY, CLIENT, 1000, PRESET),
        frame(GATEWAY, CLIENT, 1007, PRESET[-1:]),  # keepalive probe: old byte
        frame(GATEWAY, CLIENT, 1000, PRESET),  # retransmit
        frame(GATEWAY, CLIENT, 1004, PRESET[4:] + REPORT),  # overlap, then new data
        frame(GATEWAY, CLIENT, 1016, flags=ACK),  # no data
    )
    assert [r["hex"] for r in records] == [PRESET.hex(), REPORT.hex()]


def test_gap_restarts_framing() -> None:
    _, records = run_segments(
        frame(GATEWAY, CLIENT, 1000, PRESET[:5]),
        frame(GATEWAY, CLIENT, 1010, REPORT),  # 5 bytes lost
    )
    assert records == [
        {
            "t": 1001.0,
            "event": "gap",
            "dir": "rx",
            "client": "192.0.2.20:51234",
            "missing": 5,
        },
        {"t": 1001.0, "dir": "rx", "hex": REPORT.hex()},
    ]


def test_sequence_wraparound() -> None:
    top = (1 << 32) - 3
    _, records = run_segments(
        frame(GATEWAY, CLIENT, top, PRESET[:3]),
        frame(GATEWAY, CLIENT, 0, PRESET[3:]),
    )
    assert [r["hex"] for r in records] == [PRESET.hex()]


def test_noise_is_reported() -> None:
    _, records = run_segments(frame(GATEWAY, CLIENT, 1, b"\x09\x09" + PRESET))
    assert records[0]["hex"] == PRESET.hex()
    assert records[1] == {
        "t": 1000.0,
        "event": "noise",
        "dir": "rx",
        "client": "192.0.2.20:51234",
        "bytes": 2,
        "hex": "0909",
    }


GREETING = Dynet2Frame.from_hex(
    "ac 04 82 dc 00 5a aa 55 55 00 0c 00 00 00 00 00 00 00 43 c8"
)


def test_dynet2_frames_are_recorded_not_noise() -> None:
    reassembler, records = run_segments(
        frame(GATEWAY, CLIENT, 1, bytes(GREETING)[:5]),
        frame(GATEWAY, CLIENT, 6, bytes(GREETING)[5:] + PRESET),
    )
    assert records == [
        {"t": 1001.0, "dir": "rx", "dynet2": GREETING.hex("")},
        {"t": 1001.0, "dir": "rx", "hex": PRESET.hex()},
    ]
    assert (reassembler.packets, reassembler.frames) == (1, 1)


def test_close_and_reset() -> None:
    _, records = run_segments(
        frame(CLIENT, GATEWAY, 1, REQUEST, flags=ACK | PSH | FIN),
        frame(GATEWAY, CLIENT, 1, b"", flags=RST),
    )
    assert records[0]["dir"] == "tx"
    assert records[1] == {
        "t": 1000.0,
        "event": "close",
        "dir": "tx",
        "client": "192.0.2.20:51234",
    }
    assert records[2] == {
        "t": 1001.0,
        "event": "reset",
        "dir": "rx",
        "client": "192.0.2.20:51234",
    }


def test_ignores_other_hosts_and_ports() -> None:
    _, records = run_segments(
        frame(CLIENT, ("192.0.2.11", 50000), 1, PRESET),
        frame(CLIENT, ("192.0.2.10", 80), 1, PRESET),
    )
    assert records == []


def test_sniff_loop_reads_until_the_duration_ends() -> None:
    frames = [frame(GATEWAY, CLIENT, 1, PRESET), b"\x00" * 10]
    ticks = iter([0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 5.0, 11.0])

    class FakeSocket:
        timeout: float | None = None

        def recv(self, _bufsize: int, /) -> bytes:
            if frames:
                return frames.pop(0)
            raise TimeoutError

        def settimeout(self, value: float | None, /) -> None:
            self.timeout = value

        def close(self) -> None:
            pass

    out = io.StringIO()
    reassembler = Reassembler(*GATEWAY, CaptureWriter(out))
    sock = FakeSocket()
    sniff(sock, reassembler, duration=10, clock=lambda: next(ticks))
    assert reassembler.packets == 1
    assert sock.timeout == 1.0


def test_sniff_loop_without_duration_stops_when_the_source_does() -> None:
    class OneFrame:
        def __init__(self) -> None:
            self.sent = False

        def recv(self, _bufsize: int, /) -> bytes:
            if self.sent:
                raise KeyboardInterrupt
            self.sent = True
            return frame(GATEWAY, CLIENT, 1, PRESET)

        def settimeout(self, value: float | None, /) -> None:
            pass

        def close(self) -> None:
            pass

    reassembler = Reassembler(*GATEWAY, CaptureWriter(io.StringIO()))
    with contextlib.suppress(KeyboardInterrupt):
        sniff(OneFrame(), reassembler, duration=None)
    assert reassembler.packets == 1
