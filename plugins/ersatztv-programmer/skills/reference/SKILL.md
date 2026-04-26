---
name: ersatztv-reference
description: Pinned schema reference for ErsatzTV Next playout.json, channel.json, and lineup.json. Loads when the ersatztv-schedule skill needs exact field shapes, when a validation error mentions a specific field, or when the user asks "what fields does X support?"
disable-model-invocation: false
user-invocable: false
---

# ErsatzTV Next schema reference

Authoritative shapes for the three config tiers. Pinned to schema version `0.0.1`. If upstream bumps the version, sync this file from `https://github.com/ErsatzTV/next/tree/main/schema`.

## playout.json

Top-level required: `version`, `items`.

```json
{
  "version": "https://ersatztv.org/playout/version/0.0.1",
  "items": [
    {
      "id": "stable-id",
      "start": "2026-04-13T20:00:00.000-05:00",
      "finish": "2026-04-13T22:00:00.000-05:00",
      "source": { "source_type": "local", "path": "/abs/path/file.mkv" },
      "tracks": {
        "video":    { "stream_index": 0 },
        "audio":    { "stream_index": 1 },
        "subtitle": { "stream_index": 2 }
      }
    }
  ]
}
```

### PlayoutItem

| Field | Type | Required | Notes |
| :--- | :--- | :--- | :--- |
| `id` | string | yes | Unique within this playout file. |
| `start` | RFC3339 string | yes | Numeric timezone offset (e.g. `-05:00`); not `Z`. |
| `finish` | RFC3339 string | yes | After `start`. |
| `source` | object \| null | conditional | Required unless every track in `tracks` provides its own source. |
| `tracks` | object \| null | optional | Per-track overrides. Omit to let the server pick first video + first audio. |

### Source variants (`source.source_type` discriminator)

**`local`** — file on disk reachable from the ErsatzTV Next process.

| Field | Type | Required | Notes |
| :--- | :--- | :--- | :--- |
| `source_type` | const `"local"` | yes | |
| `path` | string | yes | Absolute path. In Docker, must be the container path. |
| `in_point_ms` | integer ≥ 0 \| null | optional | Trim from start. |
| `out_point_ms` | integer ≥ 0 \| null | optional | Trim to end. |

**`lavfi`** — synthetic source via ffmpeg's `-f lavfi -i` filter graph.

| Field | Type | Required | Notes |
| :--- | :--- | :--- | :--- |
| `source_type` | const `"lavfi"` | yes | |
| `params` | string | yes | Verbatim lavfi params, e.g. `anullsrc=channel_layout=stereo:sample_rate=48000:d=10`. |

**`http`** — remote URL.

| Field | Type | Required | Notes |
| :--- | :--- | :--- | :--- |
| `source_type` | const `"http"` | yes | |
| `uri` | string | yes | URI template; `{{VAR}}` placeholders are expanded from the channel environment. |
| `in_point_ms` | integer ≥ 0 \| null | optional | |
| `out_point_ms` | integer ≥ 0 \| null | optional | |

### TrackSelection

```json
{
  "source": { /* PlayoutItemSource, optional override */ },
  "stream_index": 0
}
```

| Field | Type | Required | Notes |
| :--- | :--- | :--- | :--- |
| `source` | object \| null | optional | Per-track source override. Inherits parent if omitted. |
| `stream_index` | integer ≥ 0 \| null | optional | Server picks first stream of this kind if omitted. |

### File naming

Files live under the channel's `playout.folder` and are named `{start}_{finish}.json` using **compact ISO 8601** in both date and time portions — no `:` and no `-` separators except as the timezone-offset sign. Schema example, verbatim:

```
20260413T000000.000000000-0500_20260414T002131.620000000-0500.json
```

Breakdown:

- `20260413` — date `YYYYMMDD`.
- `T` — date/time separator.
- `000000.000000000` — time `HHMMSS.fffffffff`.
- `-0500` — timezone offset `±HHMM`.
- `_` — start/finish separator.
- `.json` — extension.

## channel.json

```json
{
  "playout": {
    "folder": "/abs/path/to/channel-1/playout",
    "virtual_start": null
  },
  "ffmpeg": {
    "ffmpeg_path": "",
    "ffprobe_path": "",
    "disabled_filters": []
  },
  "normalization": {
    "audio": {
      "format": "aac",
      "bitrate_kbps": 192,
      "buffer_kbps": 384,
      "channels": 2,
      "sample_rate_hz": 48000,
      "normalize_loudness": true,
      "loudness": {
        "integrated_target": -16,
        "range_target": 11,
        "true_peak": -1.5
      }
    },
    "video": {
      "format": "h264",
      "bit_depth": 8,
      "width": 1920,
      "height": 1080,
      "bitrate_kbps": 8000,
      "buffer_kbps": 16000,
      "accel": null,
      "tonemap_algorithm": "linear",
      "vaapi_device": "/dev/dri/renderD128",
      "vaapi_driver": "iHD"
    }
  }
}
```

| Field | Required | Notes |
| :--- | :--- | :--- |
| `playout.folder` | yes | Absolute path. The plugin writes playout JSON files here. |
| `playout.virtual_start` | optional, RFC3339 string \| null | Anchor the playout window to a different wall-clock time (time-shifting). Default `null`. |
| `ffmpeg` | **yes** | The block must be present. All three sub-fields can be empty defaults: `{"ffmpeg_path": "", "ffprobe_path": "", "disabled_filters": []}` lets the container's bundled ffmpeg be auto-discovered. |
| `normalization` | **yes** | The block must be present. Both `audio` and `video` sub-blocks required. Defaults shown above are sane (1080p H.264 8 Mbps + 192 kbps AAC stereo, software encode). |
| `normalization.video.accel` | yes (nullable) | One of `cuda` / `qsv` / `vaapi` / `videotoolbox` / `vulkan` / `null`. **`""` (empty string) is rejected** — use `null` for software-only. ETV Next's Linux Docker container has no GPU access on macOS hosts, so `null` is the right pick there. |

**Empirical note (verified 2026-04-26 against `ghcr.io/ersatztv/next:develop`):** ETV Next rejects `channel.json` if `ffmpeg` or `normalization` blocks are missing — the schema marks them optional but the runtime requires them. The validator at `tools/playout-validate.py` only checks playout JSON; channel.json validation happens at channel-startup time. Always include both blocks when emitting `channel.json`.

## lineup.json

```json
{
  "server": {
    "bind_address": "0.0.0.0",
    "port": 8409
  },
  "output": {
    "folder": "/tmp/hls"
  },
  "channels": [
    { "number": "1", "name": "ErsatzTV", "config": "./channels/1/channel.json" }
  ]
}
```

| Field | Required | Notes |
| :--- | :--- | :--- |
| `server.bind_address` | optional | Default `0.0.0.0`. |
| `server.port` | optional | Default 8409. |
| `output.folder` | yes | Where HLS segments are written; not the playout folder. |
| `channels[].number` | yes | String, channel number (e.g. `"42"`, `"42.1"`). |
| `channels[].name` | yes | Display name. |
| `channels[].config` | yes | Path to that channel's `channel.json` (relative to lineup or absolute). |

## Source of truth

When in doubt, fetch the live schemas:

- `https://github.com/ErsatzTV/next/blob/main/schema/playout.json`
- `https://github.com/ErsatzTV/next/blob/main/schema/channel_config.json`
- `https://github.com/ErsatzTV/next/blob/main/schema/lineup_config.json`

If `version` in playout.json points at a URI different from `https://ersatztv.org/playout/version/0.0.1`, this reference is stale; refetch and update before emitting.
