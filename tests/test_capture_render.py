import io

import pytest
from aiodynet import (
    AreaCommand,
    AreaCommandType,
    AreaOff,
    AreaToLevel,
    ChannelLevelReport,
    ChannelOff,
    ChannelOn,
    ClockDate,
    ClockTime,
    Dynet2Frame,
    Dynet2SignonReply,
    FadeChannelToLevel,
    FadeChannelToPreset,
    FadeScale,
    Level,
    LinearPresetSelect,
    MotionActivitySync,
    Packet,
    Panic,
    PresetLevelReport,
    PresetReport,
    PresetSelect,
    RequestChannelLevel,
    RequestPreset,
    RequestPresetLevel,
    RequestUserPreference,
    ResetPreset,
    RestoreSavedPreset,
    SetChannelLevel,
    SetUserPreference,
    StopFade,
    UnknownMessage,
    Unpanic,
    UserPreferenceReport,
    decode,
)

from dynet_cli.capture import (
    CaptureFormatError,
    CaptureWriter,
    Dynet2Record,
    EventRecord,
    PacketRecord,
    Record,
    Summary,
    read_records,
)
from dynet_cli.render import (
    _describe_logical,
    describe,
    describe_dynet2,
    dynet2_json,
    message_area,
    message_kind,
    to_json,
)


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
            UnknownMessage(packet=Packet.from_hex("1c0700200000ff")),
            "area 7: opcode 0x20 (not decoded)",
        ),
        (
            AreaCommand(area=2, command=AreaCommandType.DECREMENT_LEVEL),
            "area 2: step level down",
        ),
        (
            AreaCommand(area=2, command=AreaCommandType.INCREMENT_LEVEL),
            "area 2: step level up",
        ),
        (
            AreaCommand(area=2, command=AreaCommandType.PROGRAM_CURRENT_PRESET),
            "area 2: program current preset",
        ),
        (
            AreaCommand(area=2, command=AreaCommandType.DISABLE_PANELS, join=1),
            "area 2: disable panels join 0x01",
        ),
        (
            AreaCommand(area=2, command=AreaCommandType.ENABLE_PANELS),
            "area 2: enable panels",
        ),
        (
            AreaCommand(area=2, command=AreaCommandType.SAVE_CURRENT_PRESET),
            "area 2: save current preset",
        ),
        (AreaOff(area=2, fade=2), "area 2: all off fade 2s"),
        (ResetPreset(area=2), "area 2: reset to current preset"),
        (Panic(area=2), "area 2: panic"),
        (Unpanic(area=2), "area 2: end panic"),
        (AreaToLevel(area=2, level=Level.OFF), "area 2: all channels to 0%"),
        (ChannelOff(area=2, channel=4, fade=1), "area 2 channel 4: off fade 1s"),
        (ChannelOn(area=2, channel=None), "area 2 all channels: on"),
        (StopFade(area=2, channel=None), "area 2 all channels: stop fading"),
        (
            FadeChannelToLevel(
                area=2, channel=1, level=Level.FULL, scale=FadeScale.TENTHS, fade_raw=5
            ),
            "area 2 channel 1: fade to 100% (fade 5 tenths)",
        ),
        (RestoreSavedPreset(area=2), "area 2: restore saved preset"),
        (
            RestoreSavedPreset(area=2, fade_raw=9),
            "area 2: restore saved preset fade byte 0x09",
        ),
        (
            MotionActivitySync(area=28, channel=None, time_raw=39),
            "area 28 all channels: motion activity (time 39)",
        ),
        (
            SetUserPreference(area=2, preference=1, value=2),
            "area 2: set preference 0x01 to 0x0002",
        ),
        (
            RequestUserPreference(area=2, preference=1),
            "area 2: which value for preference 0x01?",
        ),
        (
            UserPreferenceReport(area=23, preference=0x0C, value=0x1B3B),
            "area 23: preference 0x0c is 0x1b3b",
        ),
        (
            RequestPresetLevel(area=2, channel=1, preset=3),
            "area 2 channel 1: which level for preset 3?",
        ),
        (
            PresetLevelReport(area=2, channel=1, preset=3, level=Level.OFF),
            "area 2 channel 1: preset 3 is 0%",
        ),
        (
            ClockTime(device_code=0xAA, box=0x55, hour=7, minute=5, second=9),
            "device 0xaa box 85: time is 07:05:09",
        ),
        (
            ClockDate(
                device_code=0xAA, box=0x55, year=2026, month=11, day=18, day_of_week=3
            ),
            "device 0xaa box 85: date is 2026-11-18 (day of week 3)",
        ),
        (
            UnknownMessage(packet=Packet.from_hex("5caa5551187126")),
            "device 0xaa box 85: physical opcode 0x51 (not decoded)",
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
        ("1c0700200000ff", "Unknown logical 0x20", 7),
        ("1c0700040000ff", "AreaOff", 7),
        ("5caa5553187126", "ClockDate", None),
        ("5c010200000000", "Unknown physical 0x00", None),
        ("6c000000000000", "Unknown sync 0x6c", None),
    ],
)
def test_kind_and_area(raw: str, kind: str, area: int | None) -> None:
    message = decode(Packet.from_hex(raw))
    assert message_kind(message) == kind
    assert message_area(message) == area


def test_describe_logical_rejects_other_messages() -> None:
    message = UnknownMessage(packet=Packet.from_hex("5c010200000000"))
    with pytest.raises(AssertionError, match="handles these itself"):
        _describe_logical(message)


def test_to_json_enum_and_none() -> None:
    message = FadeChannelToLevel(
        area=1, channel=None, level=Level.OFF, scale=FadeScale.MINUTES
    )
    assert to_json(message) == {
        "type": "FadeChannelToLevel",
        "area": 1,
        "join": 255,
        "channel": None,
        "level": {"raw": 255, "percent": 0.0},
        "scale": "MINUTES",
        "fade_raw": 0,
    }


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
        ('{"t": 1, "dir": "rx", "dynet2": "ac0280"}', "means 12 bytes"),
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


GREETING = Dynet2Frame.from_hex(
    "ac 04 82 dc 00 5a aa 55 55 00 0c 00 00 00 00 00 00 00 43 c8"
)
AREA_20 = Packet.from_hex("1c 14 ff 2f 00 00 ff a3")


def test_describe_dynet2() -> None:
    assert describe_dynet2(GREETING) == "DyNet 2 frame, 20 bytes (not decoded)"
    assert dynet2_json(GREETING) == {
        "type": "Dynet2Frame",
        "length": 20,
        "dynet2": GREETING.hex(""),
    }


def test_dynet2_round_trip() -> None:
    out = io.StringIO()
    CaptureWriter(out).dynet2(5.0, "rx", GREETING)
    assert out.getvalue() == f'{{"t":5.0,"dir":"rx","dynet2":"{GREETING.hex("")}"}}\n'
    assert list(read_records(out.getvalue().splitlines())) == [
        Dynet2Record(time=5.0, direction="rx", frame=GREETING)
    ]


def test_reader_frames_dynet2_in_plain_hex() -> None:
    raw = (bytes(GREETING) + bytes(AREA_20)).hex()
    records = list(read_records([GREETING.hex(), raw[:10], raw[10:]]))
    assert records == [
        Dynet2Record(time=None, direction=None, frame=GREETING),
        Dynet2Record(time=None, direction=None, frame=GREETING),
        PacketRecord(time=None, direction=None, packet=AREA_20),
    ]


def test_summary_lists_dynet2_frames() -> None:
    summary = Summary()
    for _ in range(3):
        summary.add(Dynet2Record(time=None, direction="rx", frame=GREETING))
    text = summary.render()
    assert text.startswith("0 packet(s)\n")
    assert "DyNet 2 frames (3, 1 distinct):" in text
    assert f"      3  {GREETING.hex('')}  DyNet 2 frame, 20 bytes (not decoded)" in text


def test_signon_reply_is_described_and_converted() -> None:
    frame = Dynet2Frame.from_hex(
        "ac 04 81 b3 00 2a aa 55 55 01 00 00 02 11 1f 1f 00 00 3f b4"
    )
    assert describe_dynet2(frame) == (
        "device 0xb3 box 42: signed on to all devices, firmware 02.11, "
        "max rx 128 / tx 128 bytes, port 0, slave 0"
    )
    data = dynet2_json(frame)
    assert data["type"] == "Dynet2SignonReply"
    assert data["firmware_major"] == 2
    assert data["max_rx_size"] == 128
    assert data["reserved_raw"] == "0000"
    assert data["dynet2"] == frame.hex("")
    to_one = Dynet2SignonReply(
        device_code=0xB3,
        box=42,
        target_device_code=0xDA,
        target_box=1,
        firmware_major=2,
        firmware_minor=0x11,
        max_rx_size=128,
        max_tx_size=64,
    ).to_frame()
    assert "signed on to device 0xda box 1" in describe_dynet2(to_one)
