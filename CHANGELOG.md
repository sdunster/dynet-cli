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
- `sniff` keeps the hex of skipped bytes in `noise` events, not just a count.

## 0.1.0 (2026-11-18)

First release: `check`, `monitor`, `preset`, `level`, `query`, `scan`, `send`,
`decode` and `sniff`, plus `scripts/build-pyz` for a single-file build, and
a guide to capturing traffic (`docs/capturing-traffic.md`).
