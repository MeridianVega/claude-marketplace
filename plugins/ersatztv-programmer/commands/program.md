---
name: ersatztv-program
description: Build or rebuild an ErsatzTV Next channel. Args optional — pass a channel number to refresh just that one, or omit to start a free-form build conversation. User-invocable only — programming writes playout JSON files, so the user controls when it runs.
argument-hint: "[channel-number] [request]"
disable-model-invocation: true
---

Build or rebuild a channel using the `ersatztv-schedule` skill.

Arguments: $ARGUMENTS

Procedure:

1. Parse $ARGUMENTS:
   - If empty, ask the user what channel to build (number, name, theme/shape).
   - If first token is a number like `42`, treat it as the channel number; the rest is the request. If no rest is provided, look up the channel in the user's plugin config (`config.yaml` from the `setup` skill) and use the stored `request` field. If the channel isn't in config and no request was given, ask the user what to play.
   - If $ARGUMENTS is `--from-config`, the entire request comes from `config.yaml` — refresh every channel listed there. Delegate to the `programmer` agent for that case.

2. Load the `ersatztv-schedule` skill if it isn't already in context. Follow its procedure end-to-end.

3. Validate with `tools/playout-validate.py` before reporting success.

4. Report: channel number, item count, total run time, file path. Note any trims, pads, or skips.

Constraints:

- Never write to `lineup.json` or `channel.json` without explicit user permission.
- Never invent file paths. Every `local` source must come from a real media-server query.
- Live URLs (`http` source) are fine; iptv-org and similar remote streams don't need a media-server query.
