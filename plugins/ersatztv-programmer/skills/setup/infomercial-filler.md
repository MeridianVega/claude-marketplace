# Infomercial filler library

Detail for setup Step 5 — building a filler library of vintage infomercials, station bumpers, and "be right back" cards. Loaded by the `ersatztv-setup` skill on demand.

The midnight–1 AM slot on most home-channel setups runs filler. This recipe acquires that filler legally (creative-commons / public-domain content from YouTube + the Internet Archive), stores it on an external drive, and references it from playout JSON.

## What "filler" means in this plugin

- **Infomercials** — vintage "as seen on TV" content, late-night ad blocks. Best 1980s–1990s for feel.
- **Station bumpers / idents** — the 5–15 second clips between programs.
- **Test patterns / SMPTE bars** — actual idle filler, mostly for technical use.
- **Be-right-back cards** — single-frame stills shown during gaps.

Each gets a distinct subfolder so the schedule skill can address them by category.

## Where to store

Recommended layout on an external drive:

```
/Volumes/<DRIVE>/_FILLER_LIBRARY/
  infomercials/
    1980s/
    1990s/
    2000s/
  bumpers/
    network-idents/
    custom/
  test-patterns/
  cards/
    be-right-back.png
    technical-difficulties.png
```

Bind-mount this read-only into the ErsatzTV Next container in `docker-compose.yml`:

```yaml
  ersatztv-next:
    volumes:
      # ...existing volumes...
      - /Volumes/Pluto/_FILLER_LIBRARY:/filler:ro
```

Then the in-container path is `/filler/infomercials/1990s/...`.

## Acquisition with yt-dlp

`yt-dlp` (a maintained fork of youtube-dl) handles YouTube and the Internet Archive cleanly. Install:

```bash
brew install yt-dlp     # macOS
# or
pip install --user yt-dlp
```

### Vintage infomercials from the Internet Archive

The Internet Archive has thousands of public-domain infomercial collections. Browse <https://archive.org/details/televisionads> or search for `subject:"Infomercials"`. Each item has a stable URL like `https://archive.org/details/<id>`.

```bash
mkdir -p /Volumes/Pluto/_FILLER_LIBRARY/infomercials/1990s
cd /Volumes/Pluto/_FILLER_LIBRARY/infomercials/1990s

# Pull a single item with the best available video format.
yt-dlp \
  --format "best[height<=720]/best" \
  --output "%(title)s.%(ext)s" \
  --restrict-filenames \
  --no-mtime \
  https://archive.org/details/SomeInfomercial1995

# Pull a whole collection (an Archive "playlist"). Use --max-downloads to cap.
yt-dlp \
  --format "best[height<=720]/best" \
  --output "%(playlist_index)03d-%(title)s.%(ext)s" \
  --restrict-filenames \
  --no-mtime \
  --max-downloads 50 \
  "https://archive.org/details/<collection-id>"
```

`--no-mtime` keeps the download timestamp as "now" so you can sort by acquisition date if useful.

### Station bumpers from YouTube

Bumper compilations on YouTube are often re-uploaded by enthusiasts; respect the original creator and use only when the upload appears to be authorized or the original content is clearly out of copyright. Channels like *EyesOnCinema* (network archives) or *Retrontario* (TV ephemera) are good starting points.

```bash
mkdir -p /Volumes/Pluto/_FILLER_LIBRARY/bumpers/network-idents
cd /Volumes/Pluto/_FILLER_LIBRARY/bumpers/network-idents

yt-dlp \
  --format "bestvideo[height<=720]+bestaudio/best[height<=720]" \
  --merge-output-format mp4 \
  --output "%(title)s.%(ext)s" \
  --restrict-filenames \
  https://www.youtube.com/playlist?list=<playlist-id>
```

### Refining what was downloaded

After the pulls, run a probe pass to drop anything broken or wrongly tagged:

```bash
cd /Volumes/Pluto/_FILLER_LIBRARY
find . -type f \( -name "*.mp4" -o -name "*.mkv" -o -name "*.avi" \) \
  -exec ffprobe -v error -show_entries stream=codec_type,duration -of default=nw=1 {} \; \
  -exec echo --- {} \;
```

Anything that fails `ffprobe` should be deleted; ErsatzTV Next will reject malformed sources at playout time.

## Referencing filler in playout JSON

When the schedule skill needs to fill a gap, it can either pull a random item from the filler library at programming time (preferred — variety) or rely on the bundled `lavfi` synthetic source (boring fallback).

Random pick at programming time:

```python
# Pseudocode the schedule skill uses
import random
from pathlib import Path

filler_pool = sorted(
    Path("/filler/infomercials").rglob("*.mp4"),
    key=lambda p: hash((p.name, today))    # stable per-day shuffle
)
chosen = filler_pool[random.randint(0, len(filler_pool) - 1)]
# emit a PlayoutItem with source.path = str(chosen)
```

Concrete playout JSON item using a chosen filler:

```json
{
  "id": "filler-2026-04-26-23h00",
  "start": "2026-04-26T23:00:00.000-07:00",
  "finish": "2026-04-27T00:00:00.000-07:00",
  "source": {
    "source_type": "local",
    "path": "/filler/infomercials/1990s/abdominizer-1992.mp4"
  }
}
```

For bumpers between programs (5–15 s slots), keep them brief and pre-trimmed via `in_point_ms` / `out_point_ms`.

For "be right back" cards, the `lavfi` source can show a still PNG with silent audio:

```json
{
  "id": "brb-card",
  "start": "...",
  "finish": "...",
  "tracks": {
    "video": {
      "source": { "source_type": "local", "path": "/filler/cards/be-right-back.png" }
    },
    "audio": {
      "source": { "source_type": "lavfi",
                  "params": "anullsrc=channel_layout=stereo:sample_rate=48000" }
    }
  }
}
```

## What this recipe does not do

- Acquire copyrighted content from streaming platforms — that's not in scope.
- Run on a schedule. Acquisition is one-time-and-occasional; the daily refresh routine just picks from what's already on disk.
- De-dupe across re-uploads. If the same infomercial shows up twice from different sources, that's tolerable.
- Compress / re-encode. ErsatzTV Next handles transcoding at playout time per the channel's normalization config; raw acquisition is fine.
