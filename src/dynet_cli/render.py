"""Turn DyNet messages into text for people and JSON for tools."""

from __future__ import annotations

from dataclasses import fields
from typing import TYPE_CHECKING, Any, assert_never

from aiodynet import (
    JOIN_ALL,
    ChannelLevelReport,
    FadeChannelToPreset,
    Level,
    LinearPresetSelect,
    PresetReport,
    PresetSelect,
    RequestChannelLevel,
    RequestPreset,
    SetChannelLevel,
    SyncType,
    UnknownMessage,
)

if TYPE_CHECKING:
    from aiodynet import AnyMessage, Packet


def _fade(seconds: float) -> str:
    return f" fade {seconds:g}s" if seconds else ""


def _join(join: int) -> str:
    return "" if join == JOIN_ALL else f" join 0x{join:02x}"


def describe(message: AnyMessage) -> str:
    """Describe a message in one line, for example ``area 12 preset 3 fade 2s``."""
    match message:
        case PresetSelect(area=area, preset=preset, fade=fade, join=join):
            text = f"area {area}: select preset {preset}{_fade(fade)}"
        case LinearPresetSelect(area=area, preset=preset, fade=fade, join=join):
            text = f"area {area}: select preset {preset} (linear){_fade(fade)}"
        case PresetReport(area=area, preset=preset, join=join):
            text = f"area {area}: is at preset {preset}"
        case RequestPreset(area=area, channel=channel, join=join):
            text = f"area {area}: which preset? (channel {channel})"
        case SetChannelLevel(
            area=area, channel=channel, level=level, fade=fade, join=join
        ):
            text = f"area {area} channel {channel}: set {level}{_fade(fade)}"
        case ChannelLevelReport(
            area=area,
            channel=channel,
            target_level=target,
            actual_level=actual,
            join=join,
        ):
            moving = "" if target == actual else f", heading to {target}"
            text = f"area {area} channel {channel}: is at {actual}{moving}"
        case RequestChannelLevel(area=area, channel=channel, join=join):
            text = f"area {area} channel {channel}: which level?"
        case FadeChannelToPreset(
            area=area, channel=channel, preset=preset, fade_raw=fade_raw, join=join
        ):
            who = "all channels" if channel is None else f"channel {channel}"
            fade_text = f" fade byte 0x{fade_raw:02x}" if fade_raw else ""
            text = f"area {area} {who}: fade to preset {preset}{fade_text}"
        case UnknownMessage(packet=packet):
            return describe_unknown(packet)
        case _:
            assert_never(message)
    return text + _join(join)


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
    if isinstance(message, UnknownMessage):
        packet = message.packet
        return packet.data[1] if packet.sync == SyncType.LOGICAL else None
    return message.area


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
        result[field.name] = value
    return result
