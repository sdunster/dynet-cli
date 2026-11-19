import io

import pytest
from aiodynet import (
    AnyMessage,
    AreaCascadingSync,
    AreaCommand,
    AreaCommandType,
    AreaOff,
    AreaToLevel,
    ChannelLevelReport,
    ChannelOff,
    ChannelOn,
    ClockDate,
    ClockTime,
    DeviceIdentify,
    DeviceStatusReport,
    Dynet2Frame,
    Dynet2SignonReply,
    FadeChannelToLevel,
    FadeChannelToPreset,
    FadeScale,
    HoldTimerAllPresets,
    Level,
    LinearPresetSelect,
    MotionActivitySync,
    OccupancyDetectionAllPresets,
    Packet,
    Panic,
    PresetLevelReport,
    PresetReport,
    PresetSelect,
    ProgramLevelsToPreset,
    RequestChannelLevel,
    RequestFirmwareVersion,
    RequestPreset,
    RequestPresetLevel,
    RequestSignon,
    RequestUserPreference,
    ResetPreset,
    RestoreSavedPreset,
    ResumeOccupancyDetection,
    SetChannelLevel,
    SetUserPreference,
    StopFade,
    SuspendOccupancyDetection,
    UnknownMessage,
    Unpanic,
    UserPreferenceReport,
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
from dynet_cli.render import (
    _describe_logical,
    _describe_physical,
    describe,
    describe_noise,
    message_area,
    message_kind,
    noise_json,
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
            "area 23: temperature is 27.59 °C (preference 0x0c)",
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
            "all devices: time is 07:05:09",
        ),
        (
            ClockDate(
                device_code=0xAA, box=0x55, year=2026, month=11, day=18, day_of_week=3
            ),
            "all devices: date is 2026-11-18 (day of week 3)",
        ),
        (
            UnknownMessage(packet=Packet.from_hex("5caa5551187126")),
            "all devices: physical opcode 0x51 (not decoded)",
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
        ("5c010202000000", "Unknown physical 0x02", None),
        ("6c000000000000", "Unknown sync 0x6c", None),
    ],
)
def test_kind_and_area(raw: str, kind: str, area: int | None) -> None:
    message = decode(Packet.from_hex(raw))
    assert message_kind(message) == kind
    assert message_area(message) == area


NEW_OPCODES: list[tuple[AnyMessage, str]] = [
    (
        UserPreferenceReport(area=23, preference=0x0D, value=0x1B3B),
        "area 23: preference 0x0d is 0x1b3b",
    ),
    (
        ProgramLevelsToPreset(area=19, preset=3),
        "area 19: store current levels as preset 3",
    ),
    (AreaCascadingSync(area=24), "area 24 all channels: cascading timer sync"),
    (
        HoldTimerAllPresets(area=5, channel=2, state_raw=1),
        "area 5 channel 2: suspend/resume hold timer (0x01)",
    ),
    (
        OccupancyDetectionAllPresets(area=26, resume=True),
        "area 26 all channels: resume occupancy detection, all presets",
    ),
    (
        OccupancyDetectionAllPresets(area=26, resume=False),
        "area 26 all channels: suspend occupancy detection, all presets",
    ),
    (
        SuspendOccupancyDetection(area=5),
        "area 5 all channels: suspend occupancy detection",
    ),
    (
        ResumeOccupancyDetection(area=5),
        "area 5 all channels: resume occupancy detection",
    ),
    (
        DeviceIdentify(device_code=0xB3, box=42, firmware_major=2, firmware_minor=17),
        "device 0xb3 box 42: identify, firmware 02.11",
    ),
    (
        DeviceIdentify(
            device_code=0xB3, box=42, firmware_major=2, firmware_minor=17, boot_raw=1
        ),
        "device 0xb3 box 42: identify, firmware 02.11 boot 0x01",
    ),
    (
        DeviceStatusReport(device_code=0xAA, box=0x55, sub_opcode=0x0A),
        "all devices: status report, sub-opcode 0x0a (data 0x00 0x00)",
    ),
    (
        RequestFirmwareVersion(device_code=0xB3, box=42),
        "device 0xb3 box 42: which firmware version?",
    ),
    (
        RequestFirmwareVersion(device_code=0xB3, box=42, processor_id=2),
        "device 0xb3 box 42: which firmware version? processor 0x02",
    ),
]


@pytest.mark.parametrize(("message", "text"), NEW_OPCODES)
def test_describe_new_opcodes(message: AnyMessage, text: str) -> None:
    assert describe(message) == text


def test_describe_physical_rejects_other_messages() -> None:
    with pytest.raises(AssertionError, match="not a physical message"):
        _describe_physical(PresetSelect(area=1, preset=1))


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
        PacketRecord(time=12.346, direction="tx", raw=packet),
        EventRecord(time=13, event="gap", details={"dir": "rx", "missing": 4}),
    ]


def test_reader_frames_a_raw_stream() -> None:
    a = bytes(PresetSelect(area=1, preset=1).to_packet()).hex()
    b = bytes(PresetSelect(area=1, preset=2).to_packet()).hex()
    lines = [a[:6], a[6:] + b[:4], b[4:]]
    assert [r.raw for r in read_records(lines) if isinstance(r, PacketRecord)] == [
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
        PacketRecord(time=None, direction=None, raw=Packet.from_hex("1c0700000000ff")),
        PacketRecord(time=None, direction="rx", raw=Packet.from_hex("5c0102030405ff")),
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
    assert text == "0 packet(s)\nBy type:\nEverything decoded to a typed message."


GREETING = Dynet2Frame.from_hex(
    "ac 04 82 dc 00 5a aa 55 55 00 0c 00 00 00 00 00 00 00 43 c8"
)
AREA_20 = Packet.from_hex("1c 14 ff 2f 00 00 ff a3")


def test_describe_dynet2() -> None:
    message = decode(GREETING)
    assert describe(message) == "DyNet 2 frame, 20 bytes (not decoded)"
    assert message_kind(message) == "Unknown DyNet 2 frame"
    assert message_area(message) is None
    assert to_json(message) == {
        "type": "UnknownDynet2Message",
        "frame": GREETING.hex(""),
    }


def test_describe_signon_request() -> None:
    assert describe(RequestSignon(device_code=0xB3, box=42)) == (
        "device 0xb3 box 42: sign on"
    )
    assert describe(RequestSignon(device_code=0xAA, box=0x55)) == "all devices: sign on"


def test_dynet2_round_trip() -> None:
    out = io.StringIO()
    CaptureWriter(out).packet(5.0, "rx", GREETING)
    assert out.getvalue() == f'{{"t":5.0,"dir":"rx","dynet2":"{GREETING.hex("")}"}}\n'
    assert list(read_records(out.getvalue().splitlines())) == [
        PacketRecord(time=5.0, direction="rx", raw=GREETING)
    ]


def test_reader_frames_dynet2_in_plain_hex() -> None:
    raw = (bytes(GREETING) + bytes(AREA_20)).hex()
    records = list(read_records([GREETING.hex(), raw[:10], raw[10:]]))
    assert records == [
        PacketRecord(time=None, direction=None, raw=GREETING),
        PacketRecord(time=None, direction=None, raw=GREETING),
        PacketRecord(time=None, direction=None, raw=AREA_20),
    ]


def test_summary_counts_frames_and_lists_unknown_ones() -> None:
    summary = Summary()
    for _ in range(3):
        summary.add(PacketRecord(time=None, direction="rx", raw=GREETING))
    odd = Packet.from_hex("1c 03 00 99 00 00 ff")  # an opcode nobody decodes
    summary.add(PacketRecord(time=None, direction="rx", raw=odd))
    text = summary.render()
    assert text.startswith("1 packet(s) and 3 DyNet 2 frame(s)\n")
    assert "      3  Unknown DyNet 2 frame" in text
    assert "Undecoded (2 distinct):" in text
    assert f"      3  {GREETING.hex('')}" in text


def test_signon_reply_is_described_and_converted() -> None:
    frame = Dynet2Frame.from_hex(
        "ac 04 81 b3 00 2a aa 55 55 01 00 00 02 11 1f 1f 00 00 3f b4"
    )
    message = decode(frame)
    assert describe(message) == (
        "device 0xb3 box 42: signed on to all devices, firmware 02.11, "
        "max rx 128 / tx 128 bytes, port 0, slave 0"
    )
    data = to_json(message)
    assert data["type"] == "Dynet2SignonReply"
    assert data["firmware_major"] == 2
    assert data["max_rx_size"] == 128
    assert data["reserved_raw"] == "0000"
    to_one = Dynet2SignonReply(
        device_code=0xB3,
        box=42,
        target_device_code=0xDA,
        target_box=1,
        firmware_major=2,
        firmware_minor=0x11,
        max_rx_size=128,
        max_tx_size=64,
    )
    assert "signed on to device 0xda box 1" in describe(to_one)


def test_reader_keeps_noise_in_a_raw_stream() -> None:
    a = bytes(PresetSelect(area=1, preset=1).to_packet())
    records = list(read_records(["0909" + a.hex() + "07" + a[:3].hex()]))
    assert records == [
        EventRecord(time=None, event="noise", details={"bytes": 2, "hex": "0909"}),
        PacketRecord(time=None, direction=None, raw=Packet.from_bytes(a)),
        EventRecord(time=None, event="noise", details={"bytes": 1, "hex": "07"}),
        EventRecord(time=None, event="noise", details={"bytes": 3, "hex": a[:3].hex()}),
    ]


def test_noise_writer_and_summary() -> None:
    out = io.StringIO()
    CaptureWriter(out).noise(7.0, "rx", b"\x09\x09", client="192.0.2.1:5")
    assert out.getvalue() == (
        '{"t":7.0,"event":"noise","dir":"rx","client":"192.0.2.1:5",'
        '"bytes":2,"hex":"0909"}\n'
    )
    summary = Summary()
    for record in read_records(out.getvalue().splitlines()):
        summary.add(record)
    summary.add(EventRecord(time=None, event="noise", details={}))  # no byte count
    assert "Noise: 2 byte(s), not in any packet or frame" in summary.render()


def test_describe_noise() -> None:
    assert describe_noise(b"\x09\x09") == "noise, 2 byte(s) (not a packet or frame)"
    assert noise_json(b"\x09") == {"type": "Noise", "length": 1}
