# Channel logos

ErsatzTV Next does not generate channel logos. Jellyfin's Live TV grid shows
whatever PNG you point at via `channel.json`'s `logo.path` (or via the
optional `<icon>` element in the XMLTV the plugin writes). When a logo is
missing the grid shows a generic TV-tuner placeholder for that channel,
which makes a 75-channel lineup look unfinished.

This plugin ships a logo renderer that produces the same style of PNG the
SeaDog Legacy fork generated — a per-genre Google-Fonts typeface with a
fill color and stroke color picked from a curated preset table. Same
classifier, same preset dictionary; Pillow instead of SkiaSharp.

## When the renderer runs

The renderer is invoked from two places:

1. **Setup wizard.** When `/ersatztv-setup` provisions a new channel
   (writes `channel.json`), it also writes `channels/{N}/logo.png` next
   to it. The setup skill picks the genre via the classifier, optionally
   shows the user a thumbnail preview, and only re-renders if the user
   asks to.

2. **Daily refresh routine** (`/ersatztv-program` orchestrator). On every
   run, the orchestrator checks each channel folder for `logo.png`. If
   missing, render once and move on. The intent is "logos exist for every
   channel without any manual step."

It is **not** invoked at programming time per playout. The logo is a
channel-level asset, not a playout-level asset.

## Genre classification

Two-stage classifier (see `tools/render-logo.py:classify`):

1. **Exact match** on the lowercased name — handles brand channels that
   should look authentic: `friends`, `ecw`, `wcw`, `hbo`, `netflix`,
   `nickelodeon`, `cartoon network`, `a24`, `on this day`, `late night`.
2. **Pattern match** on substrings — handles genre channels: anything
   containing `horror movies` or `horror tv` → horror; `anime` anywhere
   → anime; `disney`/`pixar` → disney; `80s`/`90s`/`2000s` → retro_*;
   `christmas`/`halloween`/`thanksgiving` → seasonal styles.
3. **Bucket fallback** — if neither matches, fall back to a
   bucket-flavored type: `core` → `type_core`, `music` → `type_music`,
   `live` → `type_live` (red/aggressive), `rotating` → `type_rotating`,
   `experimental` → `type_experimental` (calligraphy on deep blue). Even
   a generic "My Channel" still gets distinct styling per bucket.

To override, pass `--genre <key>` directly. Useful when a channel name
doesn't match the rules but you know the look you want — e.g.
`--name "The 4 PM Slot" --genre primetime`.

## Preset table

`tools/fonts/preset-table.json` maps genre key → `[font_file, fill_hex,
stroke_hex, font_size]`. Adding a new genre:

1. Add a row to the JSON file.
2. Add a classifier rule in `render-logo.py:PATTERN_RULES` (or
   `EXACT_MATCHES`) so something resolves to that key.
3. Add the font filename to `tools/fonts/fetch.sh` and re-run it.

The font_size value is the logo-tight size (~26–38). The 1920×1080
interstitial card multiplies it by 2.75 internally — don't pre-scale.

## Fonts

All fonts are Google Fonts (OFL/Apache 2.0 license, redistributable).
Run `tools/fonts/fetch.sh` once after install — it downloads each TTF
referenced by the preset table into `tools/fonts/`. Idempotent.

Three names in the preset table — `MetalMania-Regular.ttf`,
`BlackOpsOne-Regular.ttf` (when used for WCW), and any TV-licensed font
the user wants — are stand-ins for fonts with restrictive licenses. The
preset entries pick a similar Google Font as the default; to get the
authentic look, manually drop the original TTF into `tools/fonts/` with
the filename the preset references and the renderer will pick it up
without code changes.

If a font is missing entirely, render-logo.py falls back to Pillow's
default sans. The logo still renders — just without branded typography.
This is intentional: a missing font should never break the routine.

## Two output modes

```bash
# Tight-trimmed transparent PNG (~1600x200), the channel logo:
render-logo.py --name "Horror Movies" --bucket core --out logo.png

# 1920x1080 backdrop card with the channel name centered, used as
# interstitial during music-only filler so the channel has branded
# visuals when the audio has no video track:
render-logo.py --name "Horror Movies" --bucket core --card --out card.png
```

`--print-genre` prints the resolved key without rendering. Useful for
dry runs and unit-testing classifier changes.

## Where the renderer lives at runtime

In Docker, the plugin's `tools/` directory is reachable from the
container running Claude Code (via the agent's bind mounts). The shell
agent invokes `${CLAUDE_PLUGIN_ROOT}/tools/render-logo.py` directly. The
fonts live at `${CLAUDE_PLUGIN_ROOT}/tools/fonts/` by default, or
override with `ERSATZTV_LOGO_FONTS_DIR`.

The renderer never needs network access at render time. Only
`fetch.sh` does, and only once.

## What the renderer never does

- Never re-renders a logo that already exists. Run it once per channel
  per setup; the user can `rm logo.png` to force a regeneration.
- Never picks colors from anything other than the preset table. Don't
  add per-channel color overrides — the whole point is uniform
  curation.
- Never modifies `channel.json`. The setup wizard writes the logo path;
  the renderer just emits PNGs.
- Never embeds the channel number, "TV", "ETV", or any other glyph in
  the logo. The channel name is the logo.
