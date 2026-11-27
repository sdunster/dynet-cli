"""Talk to one device: interrogate it, read its memory, flash it, set a channel.

These use DyNet 2 device requests, which carry a source address that replies
are addressed back to. By default that is System Builder's (0xDA box 1),
which devices are known to answer.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Final

from aiodynet import (
    MAIN_EEPROM,
    MAX_MEMORY_CHUNK,
    DeviceIdentify,
    DeviceStatusReport,
    DeviceStatusType,
    Dynet2BuildNumberReply,
    Dynet2Command,
    Dynet2DeviceRequest,
    Dynet2PcbVersionReply,
    Dynet2ReadMemory,
    Dynet2RequestSerialNumber,
    Dynet2SerialNumberReply,
    Dynet2SignonReply,
    Dynet2StatusRequest,
    DynetError,
    RequestDeviceStatus,
    StatusQuery,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from aiodynet import DynetClient, Received

type Address = tuple[int, int]
"""A device code and box number."""

SYSTEM_BUILDER: Final[Address] = (0xDA, 1)
"""The address System Builder sends device requests from."""

HEX_DUMP_WIDTH = 16

FLASH_INTERVAL: Final = 2.0
"""Seconds between "silence" commands while flashing, as System Builder does."""


class NoReplyError(DynetError):
    """A device didn't answer in time."""


def device_request(
    device: Address, source: Address, command: int
) -> Dynet2DeviceRequest:
    """Build a DyNet 2 device request from `source` to `device`."""
    return Dynet2DeviceRequest(
        device_code=device[0],
        box=device[1],
        source_device_code=source[0],
        source_box=source[1],
        command=command,
    )


async def _ask[T](query: Awaitable[T]) -> T | None:
    """Return the answer, or None if the device doesn't answer in time."""
    try:
        return await query
    except TimeoutError:
        return None


def _record(info: dict[str, object], reply: object) -> None:
    """Put what a reply says into `info`."""
    match reply:
        case Dynet2SignonReply(bootloader=True):
            info["bootloader"] = reply.firmware_version
        case Dynet2SignonReply():
            info["firmware"] = reply.firmware_version
            info["max_rx_size"] = reply.max_rx_size
            info["max_tx_size"] = reply.max_tx_size
        case Dynet2BuildNumberReply(bootloader=True):
            info["bootloader_build"] = reply.build
        case Dynet2BuildNumberReply():
            info["firmware_build"] = reply.build
        case Dynet2PcbVersionReply(version=version, revision=revision):
            info["pcb_version"] = f"{version}.{revision}"
        case Dynet2SerialNumberReply(serial_number=serial):
            info["serial_number"] = serial
        case DeviceStatusReport():
            info["application_running"] = reply.application_running


INFO_COMMANDS: Final = (
    Dynet2Command.SIGNON,
    Dynet2Command.BOOTLOADER_SIGNON,
    Dynet2Command.FIRMWARE_BUILD,
    Dynet2Command.BOOTLOADER_BUILD,
    Dynet2Command.PCB_VERSION,
)


async def device_info(
    client: DynetClient, device: Address, source: Address, timeout: float
) -> dict[str, object]:
    """Ask a device everything it is known to answer. Missing answers are None."""
    info: dict[str, object] = dict.fromkeys(INFO_LABELS)
    for command in INFO_COMMANDS:
        request = device_request(device, source, command)
        _record(info, await _ask(client.query(request, timeout=timeout)))
    serial = Dynet2RequestSerialNumber(
        device_code=device[0],
        box=device[1],
        source_device_code=source[0],
        source_box=source[1],
    )
    _record(info, await _ask(client.query(serial, timeout=timeout)))
    status = RequestDeviceStatus(
        device_code=device[0],
        box=device[1],
        status_type=DeviceStatusType.BOOTLOADER_RUNNING_REASON,
    )
    _record(info, await _ask(client.query(status, timeout=timeout)))
    return info


INFO_LABELS: Final = {
    "firmware": "firmware",
    "firmware_build": "firmware build",
    "bootloader": "bootloader",
    "bootloader_build": "bootloader build",
    "pcb_version": "PCB version",
    "serial_number": "serial number",
    "application_running": "running",
    "max_rx_size": "max rx packet",
    "max_tx_size": "max tx packet",
}


def render_info(device: Address, info: dict[str, object]) -> str:
    """Format `device_info` for people."""
    lines = [f"device 0x{device[0]:02x} box {device[1]}"]
    for key, label in INFO_LABELS.items():
        value = info[key]
        if value is None:
            text = "no answer"
        elif key == "application_running":
            text = "application" if value else "bootloader"
        elif key.startswith("max_"):
            text = f"{value} bytes"
        else:
            text = str(value)
        lines.append(f"  {label:<17}{text}")
    return "\n".join(lines)


async def light_level(
    client: DynetClient, device: Address, source: Address, timeout: float
) -> int:
    """Ask a sensor for its light level in lux."""
    request = Dynet2StatusRequest(
        device_code=device[0],
        box=device[1],
        source_device_code=source[0],
        source_box=source[1],
        query=StatusQuery.PE_CELL_LIGHT_LEVEL,
    )
    report = await client.query(request, timeout=timeout)
    return report.value


async def read_memory(  # noqa: PLR0913
    client: DynetClient,
    device: Address,
    source: Address,
    address: int,
    length: int,
    *,
    selector: bytes = MAIN_EEPROM,
    timeout: float,
    retries: int = 0,
    progress: Callable[[int], None] | None = None,
) -> bytes:
    """Read `length` bytes from `address`, in chunks a 128-byte frame holds.

    Each chunk is asked for up to ``1 + retries`` times before giving up, and
    `progress` is called with the number of bytes read so far after each.
    """
    data = bytearray()
    while len(data) < length:
        start = address + len(data)
        size = min(MAX_MEMORY_CHUNK, length - len(data))
        data += await _read_chunk(
            client, device, source, start, size, selector, timeout, retries
        )
        if progress is not None:
            progress(len(data))
    return bytes(data)


async def _read_chunk(  # noqa: PLR0913, PLR0917
    client: DynetClient,
    device: Address,
    source: Address,
    start: int,
    size: int,
    selector: bytes,
    timeout: float,
    retries: int,
) -> bytes:
    for attempt in range(retries + 1):
        request = Dynet2ReadMemory(
            device_code=device[0],
            box=device[1],
            source_device_code=source[0],
            source_box=source[1],
            address=start,
            length=size,
            selector_raw=selector,
        )
        try:
            reply = await client.query(request, timeout=timeout)
        except TimeoutError as err:
            if attempt < retries:
                continue
            msg = f"no answer for {size} byte(s) at 0x{start:04x}"
            raise NoReplyError(msg) from err
        if len(reply.data) != size:
            msg = f"asked for {size} byte(s) at 0x{start:04x}, got {len(reply.data)}"
            raise DynetError(msg)
        return reply.data
    msg = "unreachable"  # pragma: no cover - the loop returns or raises
    raise AssertionError(msg)  # pragma: no cover


BLANK: Final = 0xFF
"""Erased EEPROM reads as 0xFF."""


def compact_hex_dump(address: int, data: bytes) -> str:
    """Like `hex_dump`, but runs of blank (all 0xFF) lines become one line."""
    lines: list[str] = []
    blank_from: int | None = None
    for offset in range(0, len(data), HEX_DUMP_WIDTH):
        row = data[offset : offset + HEX_DUMP_WIDTH]
        if row == bytes((BLANK,)) * len(row):
            if blank_from is None:
                blank_from = address + offset
            continue
        if blank_from is not None:
            lines.append(_blank_line(blank_from, address + offset))
            blank_from = None
        lines.append(f"0x{address + offset:04x}  {row.hex(' ')}")
    if blank_from is not None:
        lines.append(_blank_line(blank_from, address + len(data)))
    return "\n".join(lines)


def _blank_line(start: int, end: int) -> str:
    return f"0x{start:04x}  ff … (blank to 0x{end - 1:04x}, {end - start} bytes)"


def data_ranges(address: int, data: bytes) -> list[tuple[int, int]]:
    """Find the runs that aren't blank 0xFF, as (first, last) addresses."""
    ranges: list[tuple[int, int]] = []
    start: int | None = None
    for offset, byte in enumerate(data):
        if byte != BLANK and start is None:
            start = offset
        elif byte == BLANK and start is not None:
            ranges.append((address + start, address + offset - 1))
            start = None
    if start is not None:
        ranges.append((address + start, address + len(data) - 1))
    return ranges


def hex_dump(address: int, data: bytes) -> str:
    """Format memory as lines of 16 bytes, each with its address."""
    return "\n".join(
        f"0x{address + offset:04x}  {data[offset : offset + HEX_DUMP_WIDTH].hex(' ')}"
        for offset in range(0, len(data), HEX_DUMP_WIDTH)
    )


async def flash(  # noqa: PLR0913
    client: DynetClient,
    device: Address,
    source: Address,
    *,
    count: int,
    interval: float = FLASH_INTERVAL,
    sent: Callable[[Dynet2DeviceRequest], None],
) -> None:
    """Make a device flash `count` times, then always tell it to go back to work.

    This is System Builder's "flash": "Silence. Listen to me" makes the device
    blink three times and stop sending, and "Go back to work" ends that. The
    second is sent even if the first part is interrupted, so the device isn't
    left silent. `sent` is called with each command once it has gone out.
    """
    try:
        for _ in range(count):
            silence = device_request(device, source, Dynet2Command.SILENCE)
            await client.send(silence)
            sent(silence)
            await asyncio.sleep(interval)
    finally:
        await go_back_to_work(client, device, source, sent=sent)


async def go_back_to_work(
    client: DynetClient,
    device: Address,
    source: Address,
    *,
    sent: Callable[[Dynet2DeviceRequest], None],
) -> None:
    """Tell a device to go back to work, ending a "silence"."""
    back = device_request(device, source, Dynet2Command.GO_BACK_TO_WORK)
    await client.send(back)
    sent(back)


async def reset(
    client: DynetClient, device: Address, source: Address, *, timeout: float
) -> DeviceIdentify:
    """Restart a device and wait for it to announce itself.

    This is System Builder's "Reset the device", which it sends after every
    configuration write. The device restarts with the configuration it has
    stored, so settings that were only changed at run time go back to what
    was programmed. It answers with a `DeviceIdentify` that has `after_reset`
    set. Raises `NoReplyError` if that doesn't arrive in `timeout` seconds.
    """
    loop = asyncio.get_running_loop()
    back: asyncio.Future[DeviceIdentify] = loop.create_future()

    def match(received: Received) -> None:
        message = received.message
        if (
            isinstance(message, DeviceIdentify)
            and message.after_reset
            and (message.device_code, message.box) == device
            and not back.done()
        ):
            back.set_result(message)

    unsubscribe = client.subscribe(match)
    try:
        await client.send(device_request(device, source, Dynet2Command.RESET))
        try:
            async with asyncio.timeout(timeout):
                return await back
        except TimeoutError as err:
            msg = f"no Device Identify after the reset within {timeout:g}s"
            raise NoReplyError(msg) from err
    finally:
        unsubscribe()
