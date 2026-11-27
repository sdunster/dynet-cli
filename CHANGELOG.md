# Changelog

## Unreleased

- New `reset DEVICE:BOX` command: restarts a device with its stored
  configuration, as System Builder does after a write, and waits for it to
  announce itself. Only prints what it would send unless given `--yes`.
- New `dump DEVICE:BOX` command: reads a device's whole EEPROM (its size
  by device type: 32 KiB for 0x4c relay controllers and 0xb3 sensors, 64 KiB
  for 0x64 DALI controllers, 0xdc PDEGs and 0xfd Antumbra panels; 32 KiB
  for others) to a file (`-o`) or a compact hex dump, retrying chunks that get
  no answer, with progress on stderr and a summary of the ranges that hold
  data. Read-only. If the device refuses a read past the end of its
  memory, it finds the exact end, keeps the bytes before it and says where
  the memory ends, so `--end 0x10000` measures an unknown device type.
- `memory` past the end of a device's memory reports the device's refusal
  ("error 19 (memory does not exist)") instead of timing out.
- New `area ACTION AREA` command group: `enable-panels`, `disable-panels`,
  `panic`, `end-panic`, `recall-previous`, `join-level`, `rmask`, `bank`,
  `light-control`, `occupancy`, `links-set` and `links-clear` (each only with
  `--yes`), and `links`, which asks for an area's link bits and prints every
  answer.

## 0.2.2 (2026-11-27)

Needs aiodynet 0.4.0.

- Describes the device messages aiodynet 0.4.0 types: device status (the
  bootloader running reason), physical channel levels, light-level
  readings, the DyNet 2 device commands (sign-on, bootloader sign-on, build
  numbers, PCB version, silence/flash, go back to work, reset) and their
  replies, memory reads and writes, status polls, DALI tunnelling and
  motion statistics. The gateway's greeting reads "who are you?".
- Describes a sensor's raw light level and DyNet 2 channel level and
  temperature requests and replies, which show a tunable-white channel's
  level and colour temperature in kelvin, and System Builder's "fade to
  level and temperature" commands.
- Describes more area commands: light control suspend/resume, preset bank,
  RMask, panel join level, area links and "ramp except channels at 0%".
  Opcode 0x0F now reads "recall previous preset" (it said "reset to current
  preset"), after System Builder's name for it.
- New `info DEVICE:BOX` command: asks one device for its firmware and
  bootloader versions and builds, PCB version, serial number and whether its
  application is running. Read-only; `--json` for scripts.
- New `lux DEVICE:BOX` command: a sensor's light level.
- New `memory DEVICE:BOX ADDRESS LENGTH` command: reads a device's memory in
  111-byte chunks and prints a hex dump (or `--json`).
- These take `--source DEVICE:BOX`, the address replies go to (default
  System Builder's, 0xda:1).
- New `flash DEVICE:BOX` command: System Builder's "flash". The device blinks
  for `--duration` seconds, then always gets "Go back to work" (even after
  Ctrl-C) and a sign-on request to check it's back. `--stop` only sends
  "Go back to work".
- New `device-level DEVICE:BOX CHANNEL LEVEL` command: sets one output
  channel of a device directly (physical opcode 0x92), as System Builder's
  "flash channel" does.

## 0.2.1 (2026-11-27)

Needs aiodynet 0.3.0.

- `dynet send` also takes DyNet 2 frames: hex that starts with `ac` is sent
  as one frame, with its checksum added if you leave it off.
- `probe --firmware DEVICE:BOX,...` asks devices for their firmware version.
- DyNet 2 serial-number requests and replies (type 0x0A) are described, as
  "device 0xb3 box 42: serial number …", instead of "not decoded". JSON lines
  carry their fields.
- The 44-byte sign-on reply is described with its IPv6 address. It comes
  from the all-devices address, so it says "a device (no address given)",
  and `probe --signon` takes it as the answer to an addressed sign-on.
- DyNet 2 frames of up to 128 bytes (System Builder's device memory writes)
  are framed instead of reported as noise, from aiodynet 0.3.0.

## 0.2.0 (2026-11-19)

Needs aiodynet 0.2.0.

- DyNet 2 (0xAC) frames are shown, decoded and recorded instead of being
  lost as noise. Capture files get a `{"t", "dir", "dynet2": hex}` line for
  each frame, and device sign-on replies decode to their address, firmware
  version and packet sizes.
- New `probe` command: sends only requests (preset, channel level, user
  preference, preset level) to the areas you name and prints every reply.
  `--signon DEVICE:BOX,...` asks devices to sign on one at a time (`all`
  broadcasts, which some sites ignore).
- New `exercise AREA` command: sends one of each harmless command to an
  area, then restores its starting preset. Without `--yes` it only prints
  the plan.
- Every byte from the gateway is kept. Bytes outside any packet or frame
  are shown and recorded as `noise` events, with their `hex`, by `monitor`,
  `sniff`, `probe`, `exercise` and `decode`, in stream order. That includes
  partial packets cut off by a gap, close or reconnect. `decode --summary`
  totals them.
- Describes the newly decoded opcodes: 0x09, 0x2F, 0x30, 0x31, 0x3A, 0x3B,
  physical 0x00, 0x41 and 0x80, and temperature reports (preference 0x0C)
  in °C.
- The physical address 0xAA/0x55 now prints as "all devices", including in
  the clock broadcasts, which used to say "device 0xaa box 85".
- `decode --summary` counts DyNet 2 frames apart from packets and lists
  undecoded packets and frames together under "Undecoded".

Capture files written by 0.1.0 read the same as before. A capture with
`dynet2` lines can't be read by 0.1.0.

## 0.1.0 (2026-11-18)

First release: `check`, `monitor`, `preset`, `level`, `query`, `scan`, `send`,
`decode` and `sniff`, plus `scripts/build-pyz` for a single-file build, and
a guide to capturing traffic (`docs/capturing-traffic.md`).
