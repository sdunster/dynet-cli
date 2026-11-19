# Changelog

## Unreleased

Needs aiodynet 0.2.0.

- `monitor`, `decode` and `sniff` show and record DyNet 2 frames (sync byte
  0xAC) raw, as `{"dynet2": "<hex>"}` capture lines, instead of losing them as
  noise. `decode --summary` lists them.
- New `probe` command: read-only requests of every kind (preset, channel
  level, user preference, preset level, and with `--signon DEVICE:BOX,...`
  a DyNet 2 sign-on request to each device), then a summary of anything that
  didn't decode.
- DyNet 2 sign-on replies and sign-on requests are decoded everywhere, in
  text and JSON. Frames go through the same output as packets: JSON lines
  carry the decoded message's `type` and fields, and `decode --summary`
  counts frames apart from packets and lists unknown ones under "Undecoded".
- New `exercise AREA` command: one of each harmless command to one area,
  showing the replies, then the area's starting preset restored. Only a
  dry run unless `--yes` is given.
- Describes aiodynet's newly decoded opcodes: area cascading sync, hold
  timer and occupancy detection suspend/resume, program levels to preset,
  Device Identify, Report Device Status and Request Firmware Version.
  Temperature reports (preference 0x0C) read as °C, and physical packets
  addressed to 0xAA/0x55 say "all devices".
- Nothing read from the gateway is dropped silently. Bytes that aren't part
  of any packet or frame become `noise` events with their hex, in stream
  order: in `sniff` (which used to keep only a byte count, after the
  segment's packets), `monitor` (shown and recorded), `probe`, `exercise`,
  and `decode` of raw hex. A partial packet cut off by a gap, a close or a
  reconnect is kept as noise too, and `decode --summary` totals noise bytes.

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
