"""Turn DyNet messages into text for people and JSON for tools."""

from __future__ import annotations

from dataclasses import fields
from enum import Enum
from typing import TYPE_CHECKING, Any, assert_never

from aiodynet import (
    DYNET2_ALL_DEVICES,
    JOIN_ALL,
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
    HoldTimerAllPresets,
    Level,
    LinearPresetSelect,
    LogicalMessage,
    MotionActivitySync,
    OccupancyDetectionAllPresets,
    Packet,
    Panic,
    PhysicalMessage,
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
    SyncType,
    UnknownDynet2Message,
    UnknownMessage,
    Unpanic,
    UserPreferenceReport,
)

if TYPE_CHECKING:
    from aiodynet import AnyMessage

AREA_COMMAND_TEXT = {
    AreaCommandType.DECREMENT_LEVEL: "step level down",
    AreaCommandType.INCREMENT_LEVEL: "step level up",
    AreaCommandType.PROGRAM_CURRENT_PRESET: "program current preset",
    AreaCommandType.DISABLE_PANELS: "disable panels",
    AreaCommandType.ENABLE_PANELS: "enable panels",
    AreaCommandType.SAVE_CURRENT_PRESET: "save current preset",
}


def _fade(seconds: float) -> str:
    return f" fade {seconds:g}s" if seconds else ""


def _raw(name: str, value: int) -> str:
    return f" {name} 0x{value:02x}" if value else ""


def _channel(channel: int | None) -> str:
    return "all channels" if channel is None else f"channel {channel}"


ALL_PHYSICAL_DEVICES = (0xAA, 0x55)
"""The physical address (device code, box) that means every device."""


def _device(device_code: int, box: int) -> str:
    if (device_code, box) == ALL_PHYSICAL_DEVICES:
        return "all devices"
    return f"device 0x{device_code:02x} box {box}"


def _dynet2_device(device_code: int, box: int) -> str:
    if (device_code, box) == DYNET2_ALL_DEVICES:
        return "all devices"
    return f"device 0x{device_code:02x} box {box}"


def _describe_physical(message: AnyMessage) -> str:
    """Describe a physical message, after the device prefix."""
    match message:
        case RequestSignon():
            return "sign on"
        case ClockTime(hour=h, minute=m, second=sec):
            return f"time is {h:02d}:{m:02d}:{sec:02d}"
        case ClockDate(year=y, month=mo, day=d, day_of_week=dow):
            return f"date is {y:04d}-{mo:02d}-{d:02d} (day of week {dow})"
        case DeviceIdentify(boot_raw=boot):
            return f"identify, firmware {message.firmware_version}{_raw('boot', boot)}"
        case DeviceStatusReport(sub_opcode=sub, data1_raw=d1, data2_raw=d2):
            return f"status report, sub-opcode 0x{sub:02x} (data 0x{d1:02x} 0x{d2:02x})"
        case RequestFirmwareVersion(processor_id=processor):
            return f"which firmware version?{_raw('processor', processor)}"
    msg = f"not a physical message: {message!r}"
    raise AssertionError(msg)


def describe(message: AnyMessage) -> str:
    """Describe a message in one line, for example ``area 12: select preset 3``."""
    match message:
        case UnknownMessage(packet=packet):
            return describe_unknown(packet)
        case UnknownDynet2Message(frame=frame):
            return f"DyNet 2 frame, {len(frame)} bytes (not decoded)"
        case (
            Dynet2SignonReply()
            | Dynet2RequestSerialNumber()
            | Dynet2SerialNumberReply()
        ):
            return _describe_dynet2(message)
        case PhysicalMessage(device_code=device, box=box):
            return f"{_device(device, box)}: {_describe_physical(message)}"
        case _:
            text = _describe_logical(message)
    join = "" if message.join == JOIN_ALL else f" join 0x{message.join:02x}"
    return f"area {message.area}{text}{join}"


def _describe_logical(message: AnyMessage) -> str:  # noqa: C901, PLR0911, PLR0912
    """Describe a logical message, after the ``area N`` prefix."""
    match message:
        case PresetSelect(preset=preset, fade=fade):
            return f": select preset {preset}{_fade(fade)}"
        case LinearPresetSelect(preset=preset, fade=fade):
            return f": select preset {preset} (linear){_fade(fade)}"
        case PresetReport(preset=preset):
            return f": is at preset {preset}"
        case RequestPreset(channel=channel):
            return f": which preset? (channel {channel})"
        case SetChannelLevel(channel=channel, level=level, fade=fade):
            return f" channel {channel}: set {level}{_fade(fade)}"
        case ChannelLevelReport(channel=channel, target_level=target, actual_level=now):
            moving = "" if target == now else f", heading to {target}"
            return f" channel {channel}: is at {now}{moving}"
        case RequestChannelLevel(channel=channel):
            return f" channel {channel}: which level?"
        case FadeChannelToPreset(channel=channel, preset=preset, fade_raw=fade_raw):
            fade_text = _raw("fade byte", fade_raw)
            return f" {_channel(channel)}: fade to preset {preset}{fade_text}"
        case AreaCommand(command=command):
            return f": {AREA_COMMAND_TEXT[command]}"
        case AreaOff(fade=fade):
            return f": all off{_fade(fade)}"
        case ResetPreset(fade=fade):
            return f": reset to current preset{_fade(fade)}"
        case Panic(fade=fade):
            return f": panic{_fade(fade)}"
        case Unpanic(fade=fade):
            return f": end panic{_fade(fade)}"
        case AreaToLevel(level=level, fade=fade):
            return f": all channels to {level}{_fade(fade)}"
        case ChannelOff(channel=channel, fade=fade):
            return f" {_channel(channel)}: off{_fade(fade)}"
        case ChannelOn(channel=channel, fade=fade):
            return f" {_channel(channel)}: on{_fade(fade)}"
        case StopFade(channel=channel):
            return f" {_channel(channel)}: stop fading"
        case FadeChannelToLevel(
            channel=channel, level=level, scale=scale, fade_raw=raw
        ):
            unit = scale.name.lower()
            return f" {_channel(channel)}: fade to {level} (fade {raw} {unit})"
        case RestoreSavedPreset(fade_raw=fade_raw):
            return f": restore saved preset{_raw('fade byte', fade_raw)}"
        case MotionActivitySync(channel=channel, time_raw=time_raw):
            return f" {_channel(channel)}: motion activity (time {time_raw})"
        case SetUserPreference(preference=pref, value=value):
            return f": set preference 0x{pref:02x} to 0x{value:04x}"
        case RequestUserPreference(preference=pref):
            return f": which value for preference 0x{pref:02x}?"
        case UserPreferenceReport(preference=pref, value=value):
            if (celsius := message.temperature) is not None:
                return f": temperature is {celsius:.2f} °C (preference 0x{pref:02x})"
            return f": preference 0x{pref:02x} is 0x{value:04x}"
        case ProgramLevelsToPreset(preset=preset):
            return f": store current levels as preset {preset}"
        case AreaCascadingSync(channel=channel):
            return f" {_channel(channel)}: cascading timer sync"
        case HoldTimerAllPresets(channel=channel, state_raw=state):
            return f" {_channel(channel)}: suspend/resume hold timer (0x{state:02x})"
        case OccupancyDetectionAllPresets(channel=channel, resume=resume):
            action = "resume" if resume else "suspend"
            return f" {_channel(channel)}: {action} occupancy detection, all presets"
        case SuspendOccupancyDetection(channel=channel):
            return f" {_channel(channel)}: suspend occupancy detection"
        case ResumeOccupancyDetection(channel=channel):
            return f" {_channel(channel)}: resume occupancy detection"
        case RequestPresetLevel(channel=channel, preset=preset):
            return f" channel {channel}: which level for preset {preset}?"
        case PresetLevelReport(channel=channel, preset=preset, level=level):
            return f" channel {channel}: preset {preset} is {level}"
        case (
            UnknownMessage()
            | UnknownDynet2Message()
            | Dynet2SignonReply()
            | Dynet2RequestSerialNumber()
            | Dynet2SerialNumberReply()
            | RequestSignon()
            | ClockTime()
            | ClockDate()
            | DeviceIdentify()
            | DeviceStatusReport()
            | RequestFirmwareVersion()
        ):
            msg = "describe() handles these itself"
            raise AssertionError(msg)
        case _:
            assert_never(message)


def describe_unknown(packet: Packet) -> str:
    """Describe a packet aiodynet has no typed message for."""
    data = packet.data
    if packet.sync == SyncType.LOGICAL:
        return f"area {data[1]}: opcode 0x{data[3]:02x} (not decoded)"
    if packet.sync == SyncType.PHYSICAL:
        device = _device(data[1], data[2])
        return f"{device}: physical opcode 0x{data[3]:02x} (not decoded)"
    return f"sync 0x{packet.sync:02x} (not decoded)"


def _describe_dynet2(
    message: Dynet2SignonReply | Dynet2RequestSerialNumber | Dynet2SerialNumberReply,
) -> str:
    """Describe a typed DyNet 2 message."""
    match message:
        case Dynet2SignonReply():
            return _describe_signon_reply(message)
        case Dynet2RequestSerialNumber(
            device_code=device, box=box, source_device_code=src, source_box=src_box
        ):
            return (
                f"{_dynet2_device(device, box)}: what is your serial number? "
                f"(asked by {_dynet2_device(src, src_box)})"
            )
        case Dynet2SerialNumberReply(
            device_code=device,
            box=box,
            target_device_code=target,
            target_box=target_box,
            serial_number=serial,
        ):
            return (
                f"{_dynet2_device(device, box)}: serial number {serial} "
                f"(to {_dynet2_device(target, target_box)})"
            )
        case _:
            assert_never(message)


def _describe_signon_reply(reply: Dynet2SignonReply) -> str:
    # The 44-byte form comes from the all-devices address, not the sender's.
    source = (
        "a device (no address given)"
        if (reply.device_code, reply.box) == DYNET2_ALL_DEVICES
        else _dynet2_device(reply.device_code, reply.box)
    )
    target = _dynet2_device(reply.target_device_code, reply.target_box)
    ipv6 = "" if reply.ipv6_address is None else f", IPv6 {reply.ipv6_address}"
    return (
        f"{source}: signed on to {target}, "
        f"firmware {reply.firmware_version}, max rx {reply.max_rx_size} / "
        f"tx {reply.max_tx_size} bytes, port {reply.port}, slave {reply.slave}{ipv6}"
    )


def describe_noise(data: bytes) -> str:
    """Describe bytes that weren't part of any packet or frame."""
    return f"noise, {len(data)} byte(s) (not a packet or frame)"


def noise_json(data: bytes) -> dict[str, Any]:
    """Convert noise to a JSON-ready dict, like `to_json` does for messages."""
    return {"type": "Noise", "length": len(data)}


def message_kind(message: AnyMessage) -> str:
    """Name the message type, refining unknown ones by sync byte and opcode."""
    if isinstance(message, UnknownDynet2Message):
        return "Unknown DyNet 2 frame"
    if isinstance(message, UnknownMessage):
        packet = message.packet
        if packet.sync in {SyncType.LOGICAL, SyncType.PHYSICAL}:
            label = "logical" if packet.sync == SyncType.LOGICAL else "physical"
            return f"Unknown {label} 0x{packet.data[3]:02x}"
        return f"Unknown sync 0x{packet.sync:02x}"
    return type(message).__name__


def message_area(message: AnyMessage) -> int | None:
    """Return the area a message is about, if it is a logical message."""
    if isinstance(message, LogicalMessage):
        return message.area
    if isinstance(message, UnknownMessage) and message.packet.sync == SyncType.LOGICAL:
        return message.packet.data[1]
    return None


def to_json(message: AnyMessage) -> dict[str, Any]:
    """Convert a message to a JSON-ready dict: its type and its fields."""
    result: dict[str, Any] = {"type": type(message).__name__}
    for field in fields(message):
        value = getattr(message, field.name)
        if isinstance(value, (Packet, Dynet2Frame)):
            value = value.hex("")
        elif isinstance(value, bytes):
            value = value.hex()
        elif isinstance(value, Level):
            value = {"raw": value.raw, "percent": value.percent}
        elif isinstance(value, Enum):
            value = value.name
        result[field.name] = value
    return result
