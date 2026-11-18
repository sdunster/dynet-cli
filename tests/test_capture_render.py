import io

import pytest
from aiodynet import (
    ChannelLevelReport,
    FadeChannelToPreset,
    Level,
    LinearPresetSelect,
    Packet,
    PresetReport,
    PresetSelect,
    RequestChannelLevel,
    RequestPreset,
    SetChannelLevel,
    UnknownMessage,
    decode,
)

from dynet_cli.capture import (
    CaptureFormatError,
    CaptureWriter,
    EventRecord,
    PacketRecord,
    Record,
    Summary,
    read_records,
)
from dynet_cli.render import describe, message_area, message_kind, to_json


@pytest.mark.parametrize(
    ("message", "text"),
    [
        (PresetSelect(area=1, preset=2, fade=1.5), "area 1: select preset 2 fade 1.5s"),
        (LinearPresetSelect(area=1, preset=2), "area 1: select preset 2 (linear)"),
        (PresetReport(area=1, preset=2, join=0x0F), "area 1: is at preset 2 join 0x0f"),
        (RequestPreset(area=1, channel=3), "area 1: which preset? (channel 3)"),
        (
            SetChannelLevel(area=1, channel=3, level=Level.from_percent(40), fade=1),
            "area 1 channel 3: set 40.2% fade 1s",
        ),
        (
            ChannelLevelReport(
                area=1, channel=3, target_level=Level.OFF, actual_level=Level.OFF
            ),
            "area 1 channel 3: is at 0%",
        ),
        (RequestChannelLevel(area=1, channel=3), "area 1 channel 3: which level?"),
        (
            FadeChannelToPreset(area=1, channel=None, preset=4),
            "area 1 all channels: fade to preset 4",
        ),
        (
            FadeChannelToPreset(area=1, channel=2, preset=4, fade_raw=3),
            "area 1 channel 2: fade to preset 4 fade byte 0x03",
        ),
        (
            UnknownMessage(packet=Packet.from_hex("1c0700040000ff")),
            "area 7: opcode 0x04 (not decoded)",
        ),
        (
            UnknownMessage(packet=Packet.from_hex("5caa5553187126")),
            "device 0xaa box 85: physical opcode 0x53 (not decoded)",
        ),
        (
            UnknownMessage(packet=Packet.from_hex("6c000000000000")),
            "sync 0x6c (not decoded)",
        ),
    ],
)
def test_describe(message: PresetSelect, text: str) -> None:
    assert describe(message) == text


@pytest.mark.parametrize(
    ("raw", "kind", "area"),
    [
        ("1c0700000000ff", "PresetSelect", 7),
        ("1c0700040000ff", "Unknown logical 0x04", 7),
        ("5caa5553187126", "Unknown physical 0x53", None),
        ("6c000000000000", "Unknown sync 0x6c", None),
    ],
)
def test_kind_and_area(raw: str, kind: str, area: int | None) -> None:
    message = decode(Packet.from_hex(raw))
    assert message_kind(message) == kind
    assert message_area(message) == area


def test_to_json_unknown() -> None:
    message = UnknownMessage(packet=Packet.from_hex("5c0102030405ff"))
    assert to_json(message) == {"type": "UnknownMessage", "packet": "5c0102030405ff96"}


def test_writer_and_reader_round_trip() -> None:
    out = io.StringIO()
    writer = CaptureWriter(out)
    packet = PresetSelect(area=1, preset=1).to_packet()
    writer.packet(12.3456, "tx", packet)
    writer.event(13, "gap", dir="rx", missing=4)
    records = list(read_records(out.getvalue().splitlines()))
    assert records == [
        PacketRecord(time=12.346, direction="tx", packet=packet),
        EventRecord(time=13, event="gap", details={"dir": "rx", "missing": 4}),
    ]


def test_reader_frames_a_raw_stream() -> None:
    a = bytes(PresetSelect(area=1, preset=1).to_packet()).hex()
    b = bytes(PresetSelect(area=1, preset=2).to_packet()).hex()
    lines = [a[:6], a[6:] + b[:4], b[4:]]
    assert [r.packet for r in read_records(lines) if isinstance(r, PacketRecord)] == [
        PresetSelect(area=1, preset=1).to_packet(),
        PresetSelect(area=1, preset=2).to_packet(),
    ]


@pytest.mark.parametrize(
    ("line", "error"),
    [
        ('{"t": 1, "dir": "up", "hex": "1c0100000000ff"}', "unknown direction"),
        ('{"t": 1, "dir": "rx"}', "'hex'"),
        ('{"t": 1, "dir": "rx", "hex": "1c01"}', "packet is 8 bytes"),
        ("{not json", "Line 1"),
        ('["a list"]', "Line 1"),
        ("1c0100000000ff00", "Bad checksum"),
    ],
)
def test_reader_errors(line: str, error: str) -> None:
    with pytest.raises(CaptureFormatError, match=error):
        list(read_records([line]))


def test_summary() -> None:
    summary = Summary()
    records: list[Record] = [
        PacketRecord(
            time=None, direction=None, packet=Packet.from_hex("1c0700000000ff")
        ),
        PacketRecord(
            time=None, direction="rx", packet=Packet.from_hex("5c0102030405ff")
        ),
        EventRecord(time=None, event="gap", details={}),
    ]
    for record in records:
        summary.add(record)
    text = summary.render()
    assert text.startswith("2 packet(s)\n")
    assert "direction: ? 1, rx 1" in text
    assert "Areas: 7 (1)" in text
    assert "1  gap" in text
    assert "5c0102030405ff96" in text


def test_summary_all_decoded() -> None:
    summary = Summary()
    text = summary.render()
    assert text == "0 packet(s)\nBy type:\nEvery packet decoded to a typed message."
