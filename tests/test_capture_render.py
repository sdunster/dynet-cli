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
    Dynet2RequestSerialNumber,
    Dynet2SerialNumberReply,
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
    RecallPreviousPreset,
    RequestChannelLevel,
    RequestFirmwareVersion,
    RequestPreset,
    RequestPresetLevel,
    RequestSignon,
    RequestUserPreference,
    RestoreSavedPreset,
    ResumeOccupancyDetection,
    SetChannelLevel,
    SetUserPreference,
    StopFade,
    SuspendOccupancyDetection,
    UnknownDynet2Message,
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
    _describe_dynet2,
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
        (RecallPreviousPreset(area=2), "area 2: recall previous preset"),
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
        ("1c0700990000ff", "Unknown logical 0x99", 7),
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
        "all devices: status report, sign on (data 0x00 0x00)",
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


# The gateway's answer to "who are you": a frame aiodynet has no type for.
WHO_REPLY = Dynet2Frame.from_hex(
    "ac 04 83 dc 00 5a dc 00 5a 00 00 0d 00 00 00 00 00 00 31 ac"
)
AREA_20 = Packet.from_hex("1c 14 ff 2f 00 00 ff a3")


def test_describe_greeting() -> None:
    greeting = Dynet2Frame.from_hex(
        "ac 04 82 dc 00 5a aa 55 55 00 0c 00 00 00 00 00 00 00 43 c8"
    )
    assert describe(decode(greeting)) == (
        "all devices: who are you? (from device 0xdc box 90)"
    )


def test_describe_dynet2() -> None:
    message = decode(WHO_REPLY)
    assert describe(message) == "DyNet 2 frame, 20 bytes (not decoded)"
    assert message_kind(message) == "Unknown DyNet 2 frame"
    assert message_area(message) is None
    assert to_json(message) == {
        "type": "UnknownDynet2Message",
        "frame": WHO_REPLY.hex(""),
    }


def test_describe_signon_request() -> None:
    assert describe(RequestSignon(device_code=0xB3, box=42)) == (
        "device 0xb3 box 42: sign on"
    )
    assert describe(RequestSignon(device_code=0xAA, box=0x55)) == "all devices: sign on"


def test_dynet2_round_trip() -> None:
    out = io.StringIO()
    CaptureWriter(out).packet(5.0, "rx", WHO_REPLY)
    assert out.getvalue() == f'{{"t":5.0,"dir":"rx","dynet2":"{WHO_REPLY.hex("")}"}}\n'
    assert list(read_records(out.getvalue().splitlines())) == [
        PacketRecord(time=5.0, direction="rx", raw=WHO_REPLY)
    ]


def test_reader_frames_dynet2_in_plain_hex() -> None:
    raw = (bytes(WHO_REPLY) + bytes(AREA_20)).hex()
    records = list(read_records([WHO_REPLY.hex(), raw[:10], raw[10:]]))
    assert records == [
        PacketRecord(time=None, direction=None, raw=WHO_REPLY),
        PacketRecord(time=None, direction=None, raw=WHO_REPLY),
        PacketRecord(time=None, direction=None, raw=AREA_20),
    ]


def test_summary_counts_frames_and_lists_unknown_ones() -> None:
    summary = Summary()
    for _ in range(3):
        summary.add(PacketRecord(time=None, direction="rx", raw=WHO_REPLY))
    odd = Packet.from_hex("1c 03 00 99 00 00 ff")  # an opcode nobody decodes
    summary.add(PacketRecord(time=None, direction="rx", raw=odd))
    text = summary.render()
    assert text.startswith("1 packet(s) and 3 DyNet 2 frame(s)\n")
    assert "      3  Unknown DyNet 2 frame" in text
    assert "Undecoded (2 distinct):" in text
    assert f"      3  {WHO_REPLY.hex('')}" in text


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


def test_long_signon_reply_has_no_sender_address() -> None:
    # The 44-byte form, with its IPv6 address from the documentation range.
    frame = Dynet2Frame.from_hex(
        "ac 0a 81 aa 55 55 aa 55 55 01 00 00 01 11 86 86 00 00 03 00 00 00 00"
        " 20 01 0d b8 00 00 00 00 00 00 00 00 00 00 00 01 00 00 00"
    )
    assert describe(decode(frame)) == (
        "a device (no address given): signed on to all devices, firmware 01.11, "
        "max rx 540 / tx 540 bytes, port 0, slave 0, IPv6 2001:db8::1"
    )


def test_serial_number_request_and_reply() -> None:
    request = decode(Dynet2Frame.from_hex("ac 02 80 da 00 01 b3 00 2a 0a ef f0"))
    assert isinstance(request, Dynet2RequestSerialNumber)
    assert describe(request) == (
        "device 0xb3 box 42: what is your serial number? (asked by device 0xda box 1)"
    )
    reply = Dynet2SerialNumberReply(
        device_code=0xB3,
        box=42,
        target_device_code=0xDA,
        target_box=1,
        serial_number=12345678,
    )
    assert describe(reply) == (
        "device 0xb3 box 42: serial number 12345678 (to device 0xda box 1)"
    )
    assert message_kind(reply) == "Dynet2SerialNumberReply"
    assert message_area(reply) is None
    assert to_json(reply) == {
        "type": "Dynet2SerialNumberReply",
        "device_code": 0xB3,
        "box": 42,
        "target_device_code": 0xDA,
        "target_box": 1,
        "serial_number": 12345678,
        "reserved_raw": "0a",
        "padding_raw": "000000",
    }


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


# Device messages typed in aiodynet 0.4.0, from captures on 2026-11-27.
DEVICE_MESSAGES = [
    ("5cb31d40000016", "device 0xb3 box 29: which bootloader running reason?"),
    ("5cb31d40000004", "device 0xb3 box 29: which status type 0x04?"),
    (
        "5cb31d4155ff16",
        "device 0xb3 box 29: running its application (not the bootloader)",
    ),
    ("5cb31d41010016", "device 0xb3 box 29: running its bootloader (reason 0x01)"),
    ("5cb33600021180", "device 0xb3 box 54: identify, firmware 02.11, after a reset"),
    ("5c4c059203fe00", "device 0x4c box 5: channel 3: set 100%"),
    ("5c4c0592030005", "device 0x4c box 5: channel 3: set 0% fade 0x05"),
    ("5cb32ab801065b", "device 0xb3 box 42: light level 1627 lux"),
    ("5cb32ab80d0001", "device 0xb3 box 42: physical state 0x0d is 1"),
    ("5cb31d0a000010", "device 0xb3 box 29: raw light level 16"),
    ("1c08ff110001ff", "area 8 all channels: resume light control, all presets"),
    ("1c08ff110000ff", "area 8 all channels: suspend light control, all presets"),
    ("1c0800640000ff", "area 8: set preset bank 0"),
    ("1c0800400000ff", "area 8: set RMask 0x00"),
    ("1c0800140000ff", "area 8: set panel join level 0"),
    ("1c08ff20ffffff", "area 8: set area link bits 0xffffff"),
    ("1c08ff21ffffff", "area 8: clear area link bits 0xffffff"),
    ("1c0800230000ff", "area 8: which area link bits?"),
    ("1c0800220000ff", "area 8: area link bits are 0x000000"),
    (
        "1c08ff5f0000ff",
        "area 8 all channels: ramp to level 0x00 except channels at 0% (ramp 0x00)",
    ),
    (
        "ac 05 10 da 00 01 00 08 ff 06 00 01 7f ff 00 00 c8 0d a2 00 00 00",
        ("area 8 channel 1: fade to 50%, 3490 K fade 2s (from device 0xda box 1)"),
    ),
    (
        "ac 03 22 da 00 01 00 08 ff 03 00 01 00 00",
        (
            "area 8 channel 1: which level and colour temperature? "
            "(from device 0xda box 1)"
        ),
    ),
    (
        "ac 03 22 da 00 01 00 08 02 03 00 01 00 00",
        (
            "area 8 channel 1 join 0x02: which level and colour temperature? "
            "(from device 0xda box 1)"
        ),
    ),
    (
        (
            "ac 07 23 64 00 2a 00 08 ff 03 00 01 00 00 7f 7f 0c 85 0c 80"
            " 00 00 00 00 00 00 00 00 00 00"
        ),
        (
            "area 8 channel 1: is at 50%, 3205 K (heading to 50%, 3200 K; "
            "from device 0x64 box 42)"
        ),
    ),
    (
        "ac 02 80 da 00 01 b3 00 1d 08",
        "device 0xb3 box 29: silence, listen to me (flash) (from device 0xda box 1)",
    ),
    (
        "ac 02 80 da 00 01 b3 00 1d 33",
        "device 0xb3 box 29: command 0x33 (from device 0xda box 1)",
    ),
    (
        "ac 04 81 b3 00 1d aa 55 55 02 00 00 75 05 1f 1f 00 00",
        (
            "device 0xb3 box 29: signed on to all devices, bootloader 75.05, "
            "max rx 128 / tx 128 bytes, port 0, slave 0"
        ),
    ),
    (
        "ac 03 81 b3 00 1d da 00 01 07 06 01 00 01",
        "device 0xb3 box 29: bootloader build 1 (to device 0xda box 1)",
    ),
    (
        "ac 03 81 b3 00 1d da 00 01 06 06 00 00 01",
        "device 0xb3 box 29: firmware build 1 (to device 0xda box 1)",
    ),
    (
        "ac 03 81 b3 00 1d da 00 01 0f 0f 00 01 02",
        "device 0xb3 box 29: PCB version 1, revision 2 (to device 0xda box 1)",
    ),
    (
        "ac 04 9a da 00 01 b3 00 2a 00 00 00 1a 00 05 00 00 00",
        "device 0xb3 box 42: read 5 byte(s) at 0x001a (from device 0xda box 1)",
    ),
    (
        "ac 04 9b b3 00 00 da 00 01 02 00 00 18 00 02 ff ff 00",
        "device 0xb3 box 0: 2 byte(s) at 0x0018 (memory 02 00): ff ff",
    ),
    (
        "ac 04 9c da 00 01 b3 00 36 00 00 02 23 00 01 f8 00 00",
        "device 0xb3 box 54: write 1 byte(s) at 0x0223: f8 (from device 0xda box 1)",
    ),
    (
        "ac 04 9d b3 00 36 da 00 01 00 00 02 23 00 00 00 00 00",
        "device 0xb3 box 54: write at 0x0223 done",
    ),
    (
        "ac 04 9d b3 00 36 da 00 01 00 00 02 23 00 07 00 00 00",
        "device 0xb3 box 54: write at 0x0223 result 00 07",
    ),
    (
        "ac 04 82 da 00 01 b3 00 2a 04 03 00 01 00 01 00 00 00",
        "device 0xb3 box 42: light level? (from device 0xda box 1)",
    ),
    (
        "ac 04 82 da 00 01 b3 00 2a 04 07 00 01 00 04 00 00 00",
        "device 0xb3 box 42: status 0x0407? (1-4) (from device 0xda box 1)",
    ),
    (
        (
            "ac 09 a3 b3 00 36 aa 55 55 07 00 18 00 00 01 01 01 b3 00 36 01 00 00 00"
            " 03 00 13 02 01 08 00 3c 05 00 00 00 00 04"
        ),
        "device 0xb3 box 54: motion events per 60s, oldest first: 0, 0, 0, 0, 4",
    ),
    (
        "ac 06 a3 da 00 01 64 00 2a 02 00 09 00 00 10 20 01 00 01 a5 09 a5 09 00 00 00",
        (
            "device 0xda box 1 to device 0x64 box 42: DALI universe 2, cookie 1: "
            "a5 09 a5 09"
        ),
    ),
    (
        "ac 04 a3 da 00 01 64 00 2a 05 00 01 00 00 42 00 00 00",
        "device 0xda box 1 to device 0x64 box 42: tunnel data on channel 5: 42",
    ),
]


@pytest.mark.parametrize(("text", "expected"), DEVICE_MESSAGES)
def test_describe_device_messages(text: str, expected: str) -> None:
    raw = Dynet2Frame.from_hex(text) if text.startswith("ac") else Packet.from_hex(text)
    assert describe(decode(raw)) == expected


def test_long_data_is_cut_short() -> None:
    frame = Dynet2Frame.build(
        bytes.fromhex("9b b3 00 36 da 00 01 00 00 00 90 00 13") + bytes(range(19))
    )
    assert describe(decode(frame)).endswith(
        "19 byte(s) at 0x0090: 00 01 02 03 04 05 06 07 08 09 0a 0b 0c 0d 0e 0f …"
    )


def test_describe_dynet2_rejects_other_messages() -> None:
    with pytest.raises(AssertionError, match="not a typed DyNet 2 message"):
        _describe_dynet2(UnknownDynet2Message(frame=WHO_REPLY))
