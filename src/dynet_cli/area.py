"""Area commands: panels, panic, presets, join level, RMask, links.

These are the logical area commands decoded with System Builder. Some change
state a device only keeps at run time. On one site, a run of them left a
panel sending nothing until it was reset (`dynet reset`), so the CLI only
sends them with --yes.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Final

from aiodynet import (
    AreaCommand,
    AreaCommandType,
    AreaLinksReport,
    ClearAreaLinks,
    LightControlAllPresets,
    OccupancyDetectionAllPresets,
    Panic,
    RecallPreviousPreset,
    RequestAreaLinks,
    SetAreaLinks,
    SetJoinLevel,
    SetPresetBank,
    SetRMask,
    Unpanic,
)

if TYPE_CHECKING:
    import argparse

    from aiodynet import DynetClient, Received

type AreaMessage = (
    AreaCommand
    | Panic
    | Unpanic
    | RecallPreviousPreset
    | SetJoinLevel
    | SetRMask
    | SetPresetBank
    | LightControlAllPresets
    | OccupancyDetectionAllPresets
    | SetAreaLinks
    | ClearAreaLinks
)
"""Every message an ``area`` action sends."""

SIMPLE_ACTIONS: Final = {
    "enable-panels": "enable the area's panels",
    "disable-panels": "disable the area's panels",
}
FADE_ACTIONS: Final = {
    "panic": "disable panels and recall the panic preset",
    "end-panic": "enable panels and recall the previous preset",
    "recall-previous": "recall the previous preset",
}
VALUE_ACTIONS: Final = {
    "join-level": "set the join level in the area's smart panels (normally 255)",
    "rmask": "set the area's RMask",
    "bank": "set the area's preset bank",
}
SWITCH_ACTIONS: Final = {
    "light-control": "resume or suspend light level control for every preset",
    "occupancy": "resume or suspend occupancy detection for every preset",
}
LINK_ACTIONS: Final = {
    "links-set": "set area link bits (24-bit mask)",
    "links-clear": "clear area link bits (24-bit mask)",
}


def build_message(args: argparse.Namespace) -> AreaMessage:  # noqa: C901, PLR0911
    """Build the message an ``area`` action sends."""
    area, join = args.area, args.join
    match args.action:
        case "enable-panels":
            return AreaCommand(
                area=area, command=AreaCommandType.ENABLE_PANELS, join=join
            )
        case "disable-panels":
            return AreaCommand(
                area=area, command=AreaCommandType.DISABLE_PANELS, join=join
            )
        case "panic":
            return Panic(area=area, fade=args.fade, join=join)
        case "end-panic":
            return Unpanic(area=area, fade=args.fade, join=join)
        case "recall-previous":
            return RecallPreviousPreset(area=area, fade=args.fade, join=join)
        case "join-level":
            return SetJoinLevel(area=area, join_level=args.value, join=join)
        case "rmask":
            return SetRMask(area=area, mask=args.value, join=join)
        case "bank":
            return SetPresetBank(area=area, bank=args.value, join=join)
        case "light-control":
            return LightControlAllPresets(
                area=area, resume=args.state == "resume", join=join
            )
        case "occupancy":
            return OccupancyDetectionAllPresets(
                area=area, resume=args.state == "resume", join=join
            )
        case "links-set":
            return SetAreaLinks(area=area, bits=args.bits, join=join)
    return ClearAreaLinks(area=area, bits=args.bits, join=join)


async def read_links(
    client: DynetClient, area: int, join: int, timeout: float
) -> list[AreaLinksReport]:
    """Ask an area for its link bits and collect every answer for `timeout` s."""
    reports: list[AreaLinksReport] = []

    def collect(received: Received) -> None:
        message = received.message
        if isinstance(message, AreaLinksReport) and message.area == area:
            reports.append(message)

    unsubscribe = client.subscribe(collect)
    try:
        await client.send(RequestAreaLinks(area=area, join=join))
        await asyncio.sleep(timeout)
    finally:
        unsubscribe()
    return reports
