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
    AreaLinksReport,
    AreaOff,
    AreaToLevel,
    ChannelLevelReport,
    ChannelOff,
    ChannelOn,
    ClearAreaLinks,
    ClockDate,
    ClockTime,
    DeviceIdentify,
    DeviceStatusReport,
    DeviceStatusType,
    Dynet2BuildNumberReply,
    Dynet2ChannelLevelReport,
    Dynet2Command,
    Dynet2DeviceRequest,
    Dynet2FadeLevelTemperature,
    Dynet2Frame,
    Dynet2MemoryData,
    Dynet2Message,
    Dynet2PcbVersionReply,
    Dynet2ReadMemory,
    Dynet2RequestChannelLevel,
    Dynet2RequestSerialNumber,
    Dynet2SerialNumberReply,
    Dynet2SignonReply,
    Dynet2StatusRequest,
    Dynet2TunnelData,
    Dynet2WriteMemory,
    Dynet2WriteMemoryAck,
    FadeChannelToLevel,
    FadeChannelToPreset,
    HoldTimerAllPresets,
    Level,
    LightControlAllPresets,
    LinearPresetSelect,
    LogicalMessage,
    MotionActivitySync,
    OccupancyDetectionAllPresets,
    Packet,
    Panic,
    PhysicalMessage,
    PhysicalStateReport,
    PresetLevelReport,
    PresetReport,
    PresetSelect,
    ProgramLevelsToPreset,
    RampToLevelExceptOff,
    RawLightLevelReport,
    RecallPreviousPreset,
    RequestAreaLinks,
    RequestChannelLevel,
    RequestDeviceStatus,
    RequestFirmwareVersion,
    RequestPreset,
    RequestPresetLevel,
    RequestSignon,
    RequestUserPreference,
    RestoreSavedPreset,
    ResumeOccupancyDetection,
    SetAreaLinks,
    SetChannelLevel,
    SetJoinLevel,
    SetPhysicalChannelLevel,
    SetPresetBank,
    SetRMask,
    SetUserPreference,
    StatusQuery,
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


STATUS_TYPE_TEXT: dict[int, str] = {
    DeviceStatusType.DO_D2_SIGNON: "sign on",
    DeviceStatusType.BOOTLOADER_RUNNING_REASON: "bootloader running reason",
}

COMMAND_TEXT: dict[int, str] = {
    Dynet2Command.RESET: "reset",
    Dynet2Command.SIGNON: "sign on",
    Dynet2Command.BOOTLOADER_SIGNON: "sign on with the bootloader version",
    Dynet2Command.FIRMWARE_BUILD: "which firmware build?",
    Dynet2Command.BOOTLOADER_BUILD: "which bootloader build?",
    Dynet2Command.SILENCE: "silence, listen to me (flash)",
    Dynet2Command.GO_BACK_TO_WORK: "go back to work",
    Dynet2Command.PCB_VERSION: "which PCB version?",
}

STATUS_QUERY_TEXT: dict[int, str] = {
    StatusQuery.WHO_ARE_YOU: "who are you?",
    StatusQuery.PE_CELL_RAW_LIGHT_LEVEL: "raw light level?",
    StatusQuery.PE_CELL_LIGHT_LEVEL: "light level?",
    StatusQuery.MOTION_POWER_LEVEL: "motion power level?",
}

HEX_PREVIEW = 16
"""How many data bytes a description shows before cutting them short."""


def _status_type(value: int) -> str:
    return STATUS_TYPE_TEXT.get(value, f"status type 0x{value:02x}")


def _hex_preview(data: bytes) -> str:
    text = data[:HEX_PREVIEW].hex(" ")
    return f"{text} …" if len(data) > HEX_PREVIEW else text


def _describe_physical(message: AnyMessage) -> str:  # noqa: C901, PLR0911
    """Describe a physical message, after the device prefix."""
    match message:
        case RequestSignon():
            return "sign on"
        case ClockTime(hour=h, minute=m, second=sec):
            return f"time is {h:02d}:{m:02d}:{sec:02d}"
        case ClockDate(year=y, month=mo, day=d, day_of_week=dow):
            return f"date is {y:04d}-{mo:02d}-{d:02d} (day of week {dow})"
        case DeviceIdentify(boot_raw=boot):
            boot_text = ", after a reset" if message.after_reset else _raw("boot", boot)
            return f"identify, firmware {message.firmware_version}{boot_text}"
        case DeviceStatusReport():
            return _describe_status_report(message)
        case RequestDeviceStatus(status_type=status_type):
            return f"which {_status_type(status_type)}?"
        case RequestFirmwareVersion(processor_id=processor):
            return f"which firmware version?{_raw('processor', processor)}"
        case SetPhysicalChannelLevel(channel=channel, fade_raw=fade):
            return f"channel {channel}: set {message.percent:.0f}%{_raw('fade', fade)}"
        case PhysicalStateReport(state_type=state, value=value):
            if message.lux is not None:
                return f"light level {message.lux} lux"
            return f"physical state 0x{state:02x} is {value}"
        case RawLightLevelReport(level=level):
            return f"raw light level {level}"
    msg = f"not a physical message: {message!r}"
    raise AssertionError(msg)


def _describe_status_report(report: DeviceStatusReport) -> str:
    if report.application_running is True:
        return "running its application (not the bootloader)"
    if report.application_running is False:
        return f"running its bootloader (reason 0x{report.data1_raw:02x})"
    return (
        f"status report, {_status_type(report.sub_opcode)} "
        f"(data 0x{report.data1_raw:02x} 0x{report.data2_raw:02x})"
    )


def describe(message: AnyMessage) -> str:
    """Describe a message in one line, for example ``area 12: select preset 3``."""
    match message:
        case UnknownMessage(packet=packet):
            return describe_unknown(packet)
        case UnknownDynet2Message(frame=frame):
            return f"DyNet 2 frame, {len(frame)} bytes (not decoded)"
        case Dynet2Message():
            return _describe_dynet2(message)
        case PhysicalMessage(device_code=device, box=box):
            return f"{_device(device, box)}: {_describe_physical(message)}"
        case _:
            text = _describe_logical(message)
    join = "" if message.join == JOIN_ALL else f" join 0x{message.join:02x}"
    return f"area {message.area}{text}{join}"


def _describe_logical(message: AnyMessage) -> str:  # noqa: C901, PLR0911, PLR0912, PLR0915
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
        case RecallPreviousPreset(fade=fade):
            return f": recall previous preset{_fade(fade)}"
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
        case LightControlAllPresets(channel=channel, resume=resume):
            action = "resume" if resume else "suspend"
            return f" {_channel(channel)}: {action} light control, all presets"
        case SetPresetBank(bank=bank):
            return f": set preset bank {bank}"
        case SetRMask(mask=mask):
            return f": set RMask 0x{mask:02x}"
        case SetJoinLevel(join_level=join_level):
            return f": set panel join level {join_level}"
        case SetAreaLinks(bits=bits):
            return f": set area link bits 0x{bits:06x}"
        case ClearAreaLinks(bits=bits):
            return f": clear area link bits 0x{bits:06x}"
        case AreaLinksReport(bits=bits):
            return f": area link bits are 0x{bits:06x}"
        case RequestAreaLinks():
            return ": which area link bits?"
        case RampToLevelExceptOff(channel=channel, level_raw=level, ramp_raw=ramp):
            return (
                f" {_channel(channel)}: ramp to level 0x{level:02x} except channels "
                f"at 0% (ramp 0x{ramp:02x})"
            )
        case RequestPresetLevel(channel=channel, preset=preset):
            return f" channel {channel}: which level for preset {preset}?"
        case PresetLevelReport(channel=channel, preset=preset, level=level):
            return f" channel {channel}: preset {preset} is {level}"
        case UnknownMessage() | Dynet2Message() | PhysicalMessage():
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


def _describe_dynet2(message: Dynet2Message) -> str:  # noqa: C901, PLR0911, PLR0912
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
        case Dynet2DeviceRequest(command=command):
            text = COMMAND_TEXT.get(command, f"command 0x{command:02x}")
            return f"{_own(message)}: {text}{_asked_by(message)}"
        case Dynet2BuildNumberReply(bootloader=bootloader, build=build):
            kind = "bootloader" if bootloader else "firmware"
            return f"{_own(message)}: {kind} build {build}{_to(message)}"
        case Dynet2PcbVersionReply(version=version, revision=revision):
            return (
                f"{_own(message)}: PCB version {version}, revision {revision}"
                f"{_to(message)}"
            )
        case Dynet2ReadMemory(address=address, length=length):
            return (
                f"{_own(message)}: read {length} byte(s) at 0x{address:04x}"
                f"{_selector(message.selector_raw)}{_asked_by(message)}"
            )
        case Dynet2MemoryData(address=address, data=data):
            return (
                f"{_own(message)}: {len(data)} byte(s) at 0x{address:04x}"
                f"{_selector(message.selector_raw)}: {_hex_preview(data)}"
            )
        case Dynet2WriteMemory(address=address, data=data):
            return (
                f"{_dynet2_device(message.target_device_code, message.target_box)}: "
                f"write {len(data)} byte(s) at 0x{address:04x}"
                f"{_selector(message.selector_raw)}: {_hex_preview(data)} "
                f"(from {_own(message)})"
            )
        case Dynet2WriteMemoryAck(address=address, result_raw=result):
            outcome = "done" if result == b"\x00\x00" else f"result {result.hex(' ')}"
            return f"{_own(message)}: write at 0x{address:04x} {outcome}"
        case Dynet2StatusRequest(query=query, first=first, last=last):
            text = STATUS_QUERY_TEXT.get(query, f"status 0x{query:04x}?")
            sensors = "" if (first, last) in {(0, 0), (1, 1)} else f" ({first}-{last})"
            return f"{_own(message)}: {text}{sensors}{_asked_by(message)}"
        case Dynet2TunnelData():
            return _describe_tunnel(message)
        case Dynet2RequestChannelLevel(area=area, channel=channel, join=join):
            return (
                f"area {area} channel {channel}{_join(join)}: which level and "
                f"colour temperature?{_asked_by(message)}"
            )
        case Dynet2FadeLevelTemperature(area=area, channel=channel, join=join):
            return (
                f"area {area} channel {channel}{_join(join)}: fade to "
                f"{message.percent:.0f}%, {message.kelvin} K{_fade(message.fade)}"
                f"{_asked_by(message)}"
            )
        case Dynet2ChannelLevelReport(area=area, channel=channel, join=join):
            return (
                f"area {area} channel {channel}{_join(join)}: is at "
                f"{message.current_percent:.0f}%, {message.current_kelvin} K "
                f"(heading to {message.target_percent:.0f}%, "
                f"{message.target_kelvin} K; from {_own(message)})"
            )
    msg = f"not a typed DyNet 2 message: {message!r}"
    raise AssertionError(msg)


def _join(join: int) -> str:
    return "" if join == JOIN_ALL else f" join 0x{join:02x}"


def _own(message: Any) -> str:  # noqa: ANN401
    """Name the device a message is about.

    That's the target of a request and the sender of a reply: aiodynet calls
    both ``device_code`` and ``box``.
    """
    return _dynet2_device(message.device_code, message.box)


def _asked_by(message: Any) -> str:  # noqa: ANN401
    return f" (from {_dynet2_device(message.source_device_code, message.source_box)})"


def _to(message: Any) -> str:  # noqa: ANN401
    return f" (to {_dynet2_device(message.target_device_code, message.target_box)})"


def _selector(selector: bytes) -> str:
    return "" if selector == b"\x00\x00" else f" (memory {selector.hex(' ')})"


def _describe_tunnel(message: Dynet2TunnelData) -> str:
    source = _own(message)
    if (counts := message.motion_event_counts) is not None:
        per = ", ".join(str(count) for count in counts.counts)
        return f"{source}: motion events per {counts.period}s, oldest first: {per}"
    target = _dynet2_device(message.target_device_code, message.target_box)
    if (dali := message.dali) is not None:
        return (
            f"{source} to {target}: DALI universe {message.channel_raw}, "
            f"cookie {dali.cookie}: {_hex_preview(dali.frames)}"
        )
    return (
        f"{source} to {target}: tunnel data on channel {message.channel_raw}: "
        f"{_hex_preview(message.data)}"
    )


def _describe_signon_reply(reply: Dynet2SignonReply) -> str:
    # The 44-byte form comes from the all-devices address, not the sender's.
    source = (
        "a device (no address given)"
        if (reply.device_code, reply.box) == DYNET2_ALL_DEVICES
        else _dynet2_device(reply.device_code, reply.box)
    )
    target = _dynet2_device(reply.target_device_code, reply.target_box)
    ipv6 = "" if reply.ipv6_address is None else f", IPv6 {reply.ipv6_address}"
    kind = "bootloader" if reply.bootloader else "firmware"
    return (
        f"{source}: signed on to {target}, "
        f"{kind} {reply.firmware_version}, max rx {reply.max_rx_size} / "
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
