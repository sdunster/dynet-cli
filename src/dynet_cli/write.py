"""Write a new memory image to a device, changing only the bytes that differ.

This follows what System Builder does when it writes a device, as captured
on 2026-11-26 and 27:

1. "Silence. Listen to me" (DyNet 2 command 0x08).
2. Blank the programming stamp at 0x1A (five 0xFF bytes).
3. Write each block (type 0x9C), waiting for the device's ack (0x9D).
4. Write the new stamp.
5. "Reset the device" (0x00), which restarts it with the new configuration;
   it announces itself with a Device Identify.

Before step 1 the bytes about to be overwritten, and the stamp, are read back
and compared with the dump the new image was made from, so a device that has
been changed since (by System Builder, say) isn't written over. After the
reset everything written is read back and compared with the new image.

The images are whole dumps (``dynet dump -o``) starting at address 0. What
the bytes mean is left to the tool that made the new image (such as
``dynalite-eeprom compile``); this only refuses changes to the header, which
says which device the memory belongs to.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from aiodynet import MAX_MEMORY_CHUNK, Dynet2Command, Dynet2WriteMemory, DynetError

from .device import (
    NoReplyError,
    device_name,
    device_request,
    go_back_to_work,
    read_memory,
    reset,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from aiodynet import DeviceIdentify, DynetClient

    from .device import Address

HEADER_LENGTH: Final = 10
"""Bytes 0-9: device code, box, firmware, model and PCB. Never written."""
STAMP_ADDRESS: Final = 0x1A
STAMP_LENGTH: Final = 5
BLANK_STAMP: Final = b"\xff" * STAMP_LENGTH


class WriteError(DynetError):
    """A write that can't go ahead, or that went wrong."""


@dataclass(frozen=True, slots=True)
class Block:
    """Bytes to write at an address, at most `MAX_MEMORY_CHUNK` long."""

    address: int
    data: bytes


@dataclass(frozen=True, slots=True)
class WritePlan:
    """What a write sends, worked out from the old and new images."""

    device: Address
    blocks: list[Block]
    """The changed bytes outside the stamp, in chunks one frame holds."""
    stamp: bytes | None
    """The new stamp, if it changes. It's blanked before the blocks are
    written and set after, as System Builder does."""

    @property
    def size(self) -> int:
        """How many bytes the blocks hold."""
        return sum(len(block.data) for block in self.blocks)

    def writes(self) -> list[Block]:
        """Every write, in the order it's sent."""
        if self.stamp is None:
            return list(self.blocks)
        return [
            Block(STAMP_ADDRESS, BLANK_STAMP),
            *self.blocks,
            Block(STAMP_ADDRESS, self.stamp),
        ]

    def checked_ranges(self) -> list[tuple[int, int]]:
        """(address, length) of what's compared before and after writing."""
        ranges = [(STAMP_ADDRESS, STAMP_LENGTH)]
        ranges += [(block.address, len(block.data)) for block in self.blocks]
        return ranges


def _runs(old: bytes, new: bytes, start: int, end: int) -> list[tuple[int, int]]:
    """(start, end) of each run of differing bytes between `start` and `end`."""
    runs: list[tuple[int, int]] = []
    run_start: int | None = None
    for address in range(start, end):
        differs = old[address] != new[address]
        if differs and run_start is None:
            run_start = address
        elif not differs and run_start is not None:
            runs.append((run_start, address))
            run_start = None
    if run_start is not None:
        runs.append((run_start, end))
    return runs


def plan_write(device: Address, old: bytes, new: bytes) -> WritePlan:
    """Work out what to write to change `device`'s memory from `old` to `new`.

    Raises `WriteError` if the images differ in size, aren't `device`'s, or
    differ in the header.
    """
    if len(old) != len(new):
        msg = f"the images differ in size ({len(old)} and {len(new)} bytes)"
        raise WriteError(msg)
    if len(new) < STAMP_ADDRESS + STAMP_LENGTH:
        msg = f"the images are too short to be dumps ({len(new)} bytes)"
        raise WriteError(msg)
    for name, image in (("base", old), ("new", new)):
        found = (image[0], int.from_bytes(image[1:3]))
        if found != device:
            msg = (
                f"the {name} image is for device 0x{found[0]:02x} box {found[1]}, "
                f"not 0x{device[0]:02x} box {device[1]}"
            )
            raise WriteError(msg)
    if old[:HEADER_LENGTH] != new[:HEADER_LENGTH]:
        msg = "the images differ in the header (bytes 0-9), which is never written"
        raise WriteError(msg)
    stamp_end = STAMP_ADDRESS + STAMP_LENGTH
    runs = _runs(old, new, HEADER_LENGTH, STAMP_ADDRESS)
    runs += _runs(old, new, stamp_end, len(new))
    blocks = [
        Block(address, new[address : min(address + MAX_MEMORY_CHUNK, end)])
        for start, end in runs
        for address in range(start, end, MAX_MEMORY_CHUNK)
    ]
    stamp = new[STAMP_ADDRESS:stamp_end]
    changed = stamp != old[STAMP_ADDRESS:stamp_end]
    return WritePlan(device=device, blocks=blocks, stamp=stamp if changed else None)


def describe_plan(plan: WritePlan) -> list[str]:
    """Describe what a write would send, for people."""
    if not plan.blocks:
        besides = " besides the stamp" if plan.stamp else ""
        return [f"Nothing to write to {device_name(plan.device)}: no changes{besides}."]
    count = f"{plan.size} byte(s) in {len(plan.blocks)} block(s)"
    lines = [f"Would write {count} to {device_name(plan.device)}:"]
    lines.append("  silence the device")
    lines += [f"  0x{b.address:04x}: {b.data.hex(' ')}" for b in plan.writes()]
    lines.append("  reset the device")
    lines.append(
        f"First it reads back {len(plan.checked_ranges())} range(s) to check the "
        "device still matches the base image; afterwards it reads them again "
        "to check the write."
    )
    return lines


async def _write_block(  # noqa: PLR0913
    client: DynetClient,
    device: Address,
    source: Address,
    block: Block,
    *,
    timeout: float,
    retries: int,
) -> None:
    request = Dynet2WriteMemory(
        device_code=source[0],
        box=source[1],
        target_device_code=device[0],
        target_box=device[1],
        address=block.address,
        data=block.data,
    )
    attempts = 0
    while True:
        try:
            await client.query(request, timeout=timeout)
        except TimeoutError as err:
            attempts += 1
            if attempts > retries:
                msg = f"no ack for {len(block.data)} byte(s) at 0x{block.address:04x}"
                raise NoReplyError(msg) from err
        else:
            return


async def _compare(  # noqa: PLR0913
    client: DynetClient,
    device: Address,
    source: Address,
    plan: WritePlan,
    expected: bytes,
    *,
    timeout: float,
    retries: int,
) -> list[int]:
    """Read the plan's ranges; return the addresses that differ from `expected`."""
    differing = []
    for address, length in plan.checked_ranges():
        data = await read_memory(
            client, device, source, address, length, timeout=timeout, retries=retries
        )
        if data != expected[address : address + length]:
            differing.append(address)
    return differing


async def write_image(  # noqa: PLR0913
    client: DynetClient,
    plan: WritePlan,
    old: bytes,
    new: bytes,
    *,
    source: Address,
    timeout: float,
    retries: int,
    wait: float,
    log: Callable[[str], None],
) -> DeviceIdentify:
    """Write `plan` to its device; return the Device Identify after the reset.

    `plan` must have blocks to write. Raises
    `WriteError` if the device doesn't match `old` before writing (nothing is
    sent then), if a write fails part way (the device is told to go back to
    work but not reset, and its stamp is left blank), or if what's read back
    afterwards doesn't match `new`.
    """
    device = plan.device
    differing = await _compare(
        client, device, source, plan, old, timeout=timeout, retries=retries
    )
    if differing:
        where = ", ".join(f"0x{a:04x}" for a in differing)
        msg = (
            f"the device doesn't match the base image at {where}; it has "
            "changed since that dump. Nothing was written: dump it again and "
            "make the new image from that."
        )
        raise WriteError(msg)
    checked = len(plan.checked_ranges())
    log(f"checked: the device matches the base image ({checked} range(s))")
    await client.send(device_request(device, source, Dynet2Command.SILENCE))
    log("sent: silence")
    written = 0
    writes = plan.writes()
    try:
        for block in writes:
            await _write_block(
                client, device, source, block, timeout=timeout, retries=retries
            )
            written += 1
            log(f"wrote 0x{block.address:04x}: {block.data.hex(' ')}")
    except DynetError as err:
        await go_back_to_work(client, device, source, sent=lambda _: None)
        msg = (
            f"{err}. {written} of {len(writes)} write(s) went through; the device "
            "was told to go back to work but not reset. Its stamp may be left "
            "blank. To undo, dump it again and write the base image back with "
            "the new dump as --base."
        )
        raise WriteError(msg) from err
    try:
        identify = await reset(client, device, source, timeout=wait)
    except NoReplyError as err:
        msg = (
            f"every write went through, but after the reset: {err}. Check the "
            "device, and read it back with dynet dump"
        )
        raise WriteError(msg) from err
    log("sent: reset; the device came back")
    differing = await _compare(
        client, device, source, plan, new, timeout=timeout, retries=retries
    )
    if differing:
        where = ", ".join(f"0x{a:04x}" for a in differing)
        msg = (
            "read back after the write, the device differs from the new image "
            f"at {where}"
        )
        raise WriteError(msg)
    log(f"verified: {plan.size + STAMP_LENGTH} byte(s) read back match the new image")
    return identify
