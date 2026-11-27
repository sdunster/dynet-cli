# Changelog

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
