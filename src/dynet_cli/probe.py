"""Put traffic on the bus on purpose, and check that every reply decodes.

``probe`` sends read-only requests, so that devices answer with every kind of
report aiodynet knows about. ``exercise`` sends one of each harmless command to
an area you name. Both show everything that comes back and finish with the
same summary as ``decode --summary``, so anything aiodynet can't decode yet
stands out.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from aiodynet import (
    AreaCommand,
    AreaCommandType,
    AreaOff,
    AreaToLevel,
    ChannelLevelReport,
    ChannelOff,
    ChannelOn,
    Dynet2SignonReply,
    FadeChannelToLevel,
    FadeChannelToPreset,
    FadeScale,
    Level,
    LinearPresetSelect,
    Packet,
    PresetLevelReport,
    PresetReport,
    PresetSelect,
    RequestChannelLevel,
    RequestPreset,
    RequestPresetLevel,
    RequestSignon,
    RequestUserPreference,
    SetChannelLevel,
    StopFade,
    UserPreferenceReport,
    decode,
)

from .capture import EventRecord, PacketRecord, Summary
from .render import describe, describe_noise

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from aiodynet import AnyMessage, DynetClient, Message, Received

    from .capture import CaptureWriter

ALL_DEVICES: Final = (0xAA, 0x55)
"""The DyNet 1 physical address (device code, box) that means every device."""

type Matcher = Callable[[AnyMessage], bool]


def ask_signon(device_code: int, box: int) -> Step:
    """Ask a device (or `ALL_DEVICES`) to sign on, with `RequestSignon`.

    Addressed to one device, the step ends when that device's
    `Dynet2SignonReply` arrives. To all devices, the whole wait is spent
    listening, because any number of devices may answer. At one site no
    device answered the all-devices form.
    """
    request = RequestSignon(device_code=device_code, box=box)
    if request.to_all_devices:
        return Step(request)
    return Step(
        request,
        lambda m: (
            isinstance(m, Dynet2SignonReply)
            and (m.device_code, m.box) == (device_code, box)
        ),
    )


@dataclass(frozen=True, slots=True)
class Step:
    """One thing to send, and how to recognise its answer."""

    message: Message
    answer: Matcher | None = None
    """Returns true for the reply that answers this step. ``None`` means
    nothing answers it, so the whole wait is spent listening."""

    @property
    def packet(self) -> Packet:
        """The packet that goes on the bus."""
        return self.message.to_packet()


def ask_preset(area: int) -> Step:
    """Ask an area for its current preset."""
    return Step(
        RequestPreset(area=area),
        lambda m: isinstance(m, PresetReport) and m.area == area,
    )


def ask_level(area: int, channel: int) -> Step:
    """Ask a channel for its level."""
    return Step(
        RequestChannelLevel(area=area, channel=channel),
        lambda m: (
            isinstance(m, ChannelLevelReport) and (m.area, m.channel) == (area, channel)
        ),
    )


def ask_preference(area: int, preference: int) -> Step:
    """Ask an area for a user preference, such as 0x0C (temperature)."""
    return Step(
        RequestUserPreference(area=area, preference=preference),
        lambda m: (
            isinstance(m, UserPreferenceReport)
            and (m.area, m.preference) == (area, preference)
        ),
    )


def ask_preset_level(area: int, channel: int, preset: int) -> Step:
    """Ask a channel for the level it has in a preset."""
    return Step(
        RequestPresetLevel(area=area, channel=channel, preset=preset),
        lambda m: (
            isinstance(m, PresetLevelReport)
            and (m.area, m.channel, m.preset) == (area, channel, preset)
        ),
    )


def area_requests(
    area: int,
    *,
    channels: Iterable[int],
    preferences: Iterable[int],
    presets: Iterable[int],
) -> list[Step]:
    """Every request ``probe`` sends to an area once it has answered."""
    channels = list(channels)
    steps = [ask_level(area, channel) for channel in channels]
    steps += [ask_preference(area, preference) for preference in preferences]
    steps += [
        ask_preset_level(area, channel, preset)
        for preset in presets
        for channel in channels
    ]
    return steps


def exercise_steps(area: int, channel: int, level: Level) -> list[Step]:
    """One of each command ``exercise`` sends, in order.

    Left out on purpose: programming or saving presets (they overwrite what is
    stored in the devices), disabling panels, panic, writing user preferences,
    and every physical message.
    """
    half = Level.from_percent(level.percent / 2)
    commands: list[Message] = [
        PresetSelect(area=area, preset=1, fade=1),
        LinearPresetSelect(area=area, preset=2, fade=1),
        SetChannelLevel(area=area, channel=channel, level=level, fade=1),
        FadeChannelToLevel(
            area=area, channel=channel, level=half, scale=FadeScale.TENTHS, fade_raw=10
        ),
        ChannelOff(area=area, channel=channel),
        ChannelOn(area=area, channel=channel),
        SetChannelLevel(area=area, channel=channel, level=level, fade=5),
        StopFade(area=area, channel=channel),
        AreaCommand(area=area, command=AreaCommandType.DECREMENT_LEVEL),
        AreaCommand(area=area, command=AreaCommandType.INCREMENT_LEVEL),
        AreaToLevel(area=area, level=level, fade=1),
        FadeChannelToPreset(area=area, channel=channel, preset=1),
        AreaOff(area=area, fade=1),
    ]
    return [
        *(Step(command) for command in commands),
        ask_level(area, channel),
        ask_preset_level(area, channel, 1),
        ask_preset(area),
    ]


def _timestamp(now: float) -> str:
    millis = int(now * 1000) % 1000
    return time.strftime("%H:%M:%S", time.localtime(now)) + f".{millis:03d}"


class Session:
    """Sends steps and gathers everything the bus says back.

    Every packet and frame received is printed as it arrives, added to a
    `Summary` and, if there's a recorder, written to the capture file. Sent
    packets are printed and recorded too, marked ``>``.
    """

    def __init__(
        self,
        client: DynetClient,
        print_line: Callable[[str], None],
        recorder: CaptureWriter | None = None,
    ) -> None:
        """Start listening on `client`."""
        self._client = client
        self._print = print_line
        self._recorder = recorder
        self.summary = Summary()
        """Everything received (not sent)."""
        self.sent = 0
        self.answered = 0
        self._waiting: tuple[Matcher, asyncio.Future[AnyMessage]] | None = None
        self._unsubscribe = (
            client.subscribe(self._on_received),
            client.subscribe_noise(self._on_noise),
        )

    def close(self) -> None:
        """Stop listening."""
        for unsubscribe in self._unsubscribe:
            unsubscribe()

    async def run(self, step: Step, wait: float) -> AnyMessage | None:
        """Send `step`, then listen for up to `wait` seconds.

        Returns the message that answered it, or ``None`` if none came in
        time. Listening stops as soon as the answer arrives.
        """
        loop = asyncio.get_running_loop()
        answered: asyncio.Future[AnyMessage] = loop.create_future()
        if step.answer is not None:
            self._waiting = (step.answer, answered)
        packet = step.packet
        now = time.time()
        self._print(f"{_timestamp(now)}  >  {packet.hex()}  {describe(decode(packet))}")
        if self._recorder is not None:
            self._recorder.packet(now, "tx", packet)
        try:
            await self._client.send(packet)
            self.sent += 1
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(wait):
                    await asyncio.shield(answered)
        finally:
            self._waiting = None
        if not answered.done():
            return None
        self.answered += 1
        return answered.result()

    def _on_received(self, received: Received) -> None:
        now = time.time()
        raw, message = received.raw, received.message
        self.summary.add(PacketRecord(time=now, direction="rx", raw=raw))
        if self._recorder is not None:
            self._recorder.packet(now, "rx", raw)
        self._print(f"{_timestamp(now)}  <  {raw.hex()}  {describe(message)}")
        if self._waiting is not None:
            matches, future = self._waiting
            if not future.done() and matches(message):
                future.set_result(message)

    def _on_noise(self, data: bytes) -> None:
        now = time.time()
        details = {"dir": "rx", "bytes": len(data), "hex": data.hex()}
        self.summary.add(EventRecord(time=now, event="noise", details=details))
        if self._recorder is not None:
            self._recorder.noise(now, "rx", data)
        self._print(f"{_timestamp(now)}  <  {data.hex(' ')}  {describe_noise(data)}")
