"""Turn DyNet messages into text for people and JSON for tools."""

from __future__ import annotations

from dataclasses import fields
from enum import Enum
from typing import TYPE_CHECKING, Any, assert_never

from aiodynet import (
    JOIN_ALL,
    AreaCommand,
    AreaCommandType,
    AreaOff,
    AreaToLevel,
    ChannelLevelReport,
    ChannelOff,
    ChannelOn,
    ClockDate,
    ClockTime,
    FadeChannelToLevel,
    FadeChannelToPreset,
    Level,
    LinearPresetSelect,
    LogicalMessage,
    MotionActivitySync,
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
    SyncType,
    UnknownMessage,
    Unpanic,
    UserPreferenceReport,
)

if TYPE_CHECKING:
    from aiodynet import AnyMessage, Dynet2Frame, Packet

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


def describe(message: AnyMessage) -> str:
    """Describe a message in one line, for example ``area 12: select preset 3``."""
    match message:
        case UnknownMessage(packet=packet):
            return describe_unknown(packet)
        case ClockTime(device_code=device, box=box, hour=h, minute=m, second=sec):
            return f"device 0x{device:02x} box {box}: time is {h:02d}:{m:02d}:{sec:02d}"
        case ClockDate(
            device_code=device, box=box, year=y, month=mo, day=d, day_of_week=dow
        ):
            return (
                f"device 0x{device:02x} box {box}: "
                f"date is {y:04d}-{mo:02d}-{d:02d} (day of week {dow})"
            )
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
            return f": preference 0x{pref:02x} is 0x{value:04x}"
        case RequestPresetLevel(channel=channel, preset=preset):
            return f" channel {channel}: which level for preset {preset}?"
        case PresetLevelReport(channel=channel, preset=preset, level=level):
            return f" channel {channel}: preset {preset} is {level}"
        case UnknownMessage() | ClockTime() | ClockDate():
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
        return (
            f"device 0x{data[1]:02x} box {data[2]}: "
            f"physical opcode 0x{data[3]:02x} (not decoded)"
        )
    return f"sync 0x{packet.sync:02x} (not decoded)"


def describe_dynet2(frame: Dynet2Frame) -> str:
    """Describe a DyNet 2 frame, which aiodynet passes through raw."""
    return f"DyNet 2 frame, {len(frame)} bytes (not decoded)"


def dynet2_json(frame: Dynet2Frame) -> dict[str, Any]:
    """Convert a DyNet 2 frame to a JSON-ready dict."""
    return {"type": "Dynet2Frame", "length": len(frame), "dynet2": frame.hex("")}


def message_kind(message: AnyMessage) -> str:
    """Name the message type, refining unknown ones by sync byte and opcode."""
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
    if isinstance(message, UnknownMessage):
        result["packet"] = message.packet.hex("")
        return result
    for field in fields(message):
        value = getattr(message, field.name)
        if isinstance(value, Level):
            value = {"raw": value.raw, "percent": value.percent}
        elif isinstance(value, Enum):
            value = value.name
        result[field.name] = value
    return result
