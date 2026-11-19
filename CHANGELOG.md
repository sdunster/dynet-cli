# Changelog

## Unreleased

Needs aiodynet 0.2.0.

- `monitor`, `decode` and `sniff` show and record DyNet 2 frames (sync byte
  0xAC) raw, as `{"dynet2": "<hex>"}` capture lines, instead of losing them as
  noise. `decode --summary` lists them.
- `sniff` keeps the hex of skipped bytes in `noise` events, not just a count.

## 0.1.0 (2026-11-18)

First release: `check`, `monitor`, `preset`, `level`, `query`, `scan`, `send`,
`decode` and `sniff`, plus `scripts/build-pyz` for a single-file build, and
a guide to capturing traffic (`docs/capturing-traffic.md`).
