# Jellyfin replication recipe

Detail for setup Step 0d/0e — replicating an existing native Jellyfin install into a fresh containerized one and wiring ErsatzTV Next as a Live TV tuner. Loaded by the `ersatztv-setup` skill on demand; do not auto-load this file.

Sources of truth:

- Jellyfin REST API reference: <https://api.jellyfin.org/>
- Live TV setup docs: <https://jellyfin.org/docs/general/server/live-tv/>
- Image upload spec: <https://api.jellyfin.org/#tag/Image>

## Discovery — read the native install's library configs

On macOS, native Jellyfin's library configs live at:

```
~/Library/Application Support/jellyfin/root/default/<LibraryName>/options.xml
```

Each `options.xml` describes the library's name, content kind, image, and host paths. Parse it (with `plutil`-style XML readers, or `xmllint`, or just `grep -oE`) to collect the inputs the new Jellyfin needs.

Walk every directory under `root/default/`. Skip libraries with empty `PathInfos` (they're disabled).

Default base path on macOS for the data dir mounted at `/config` inside the container is `~/ersatztv-stack/config/jellyfin/data` per the bundled compose. Reference the host paths verbatim as bind mounts in `docker-compose.yml` so the new Jellyfin sees the same media files.

## Step 1 — Bind-mount every host volume the libraries reference

Before bringing the stack up, edit `~/ersatztv-stack/docker-compose.yml`'s `jellyfin` service to add bind mounts for every unique host path used by the discovered libraries. Example:

```yaml
  jellyfin:
    # ...existing config...
    volumes:
      - ./config/jellyfin/data:/config
      - /Volumes/Uranus:/Volumes/Uranus:ro
      - /Volumes/Pluto:/Volumes/Pluto:ro
      - /Volumes/Jupiter:/Volumes/Jupiter:ro
      - /Volumes/Saturn:/Volumes/Saturn:ro
      - /Users/Shared:/Users/Shared:ro
```

The `:ro` suffix keeps writes out of the source filesystems. The container path matches the host path so library `PathInfos` work without rewriting.

`docker compose up -d jellyfin` after editing.

## Step 2 — Complete Jellyfin's first-run wizard

Open `http://localhost:18096` in a browser. Walk the user through the Jellyfin first-run UI — admin user creation, library scaffolding (skip libraries here, we'll add them via API), Live TV (skip, we'll wire it via API too).

This part is owned by Jellyfin and isn't scriptable through their public API; do not try to bypass it.

When the wizard finishes, ask the user for an API key:

> Dashboard → API Keys → "+" → name it "ersatztv-programmer-setup" → copy.

Store as `JELLYFIN_TOKEN` in the user's shell rc and re-source.

## Step 3 — Add libraries via REST

For each library discovered in Step 0, POST a virtual folder. The `paths` array uses the verbatim host paths now visible inside the container.

```bash
curl -X POST "http://localhost:18096/Library/VirtualFolders" \
  -H "X-Emby-Token: $JELLYFIN_TOKEN" \
  -H "Content-Type: application/json" \
  -G \
  --data-urlencode "name=Movies" \
  --data-urlencode "collectionType=movies" \
  --data-urlencode "paths=/Volumes/Uranus/_MEDIA_LIBRARY/Movies" \
  --data-urlencode "paths=/Volumes/Pluto/_MEDIA_LIBRARY/Movies" \
  --data-urlencode "refreshLibrary=false"
```

Common `collectionType` values: `movies`, `tvshows`, `music`, `homevideos`, `musicvideos`, `boxsets`, `mixed`. Pick the one the native install used; default to `mixed` if unclear.

Repeat per library. Pause `refreshLibrary=false` to defer all scans to the end (saves I/O thrash).

## Step 4 — Upload folder images

If the native install had a `folder.png` for each library (under `~/Library/Application Support/jellyfin/data/metadata/<LibraryName>/folder.png` or under the library's options dir), preserve them.

For each library:

1. Fetch its ID:

   ```bash
   LIB_ID=$(curl -s "http://localhost:18096/Library/VirtualFolders" \
     -H "X-Emby-Token: $JELLYFIN_TOKEN" | jq -r '.[] | select(.Name=="Movies") | .ItemId')
   ```

2. Upload the image as a base64 body to the Primary image endpoint:

   ```bash
   base64 -i ~/Library/Application\ Support/jellyfin/.../folder.png \
     | curl -X POST "http://localhost:18096/Items/$LIB_ID/Images/Primary" \
       -H "X-Emby-Token: $JELLYFIN_TOKEN" \
       -H "Content-Type: image/png" \
       --data-binary @-
   ```

Some Jellyfin versions accept the raw bytes directly without base64 (use `--data-binary @file.png`). Try the simpler form first; fall back to base64 if you get a 415.

## Step 5 — Trigger initial library scans

```bash
curl -X POST "http://localhost:18096/Library/Refresh" \
  -H "X-Emby-Token: $JELLYFIN_TOKEN"
```

This kicks off a background scan across all configured libraries. The first scan on a 28k-item library typically takes 30–90 minutes; watch progress at `http://localhost:18096/web/index.html#/dashboard/scheduledtasks`.

## Step 6 — Wire ErsatzTV Next as a Live TV tuner

Two POSTs: one for the M3U tuner, one for the XMLTV listings provider.

```bash
# 1. Add the M3U tuner.
curl -X POST "http://localhost:18096/LiveTv/TunerHosts" \
  -H "X-Emby-Token: $JELLYFIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "Type": "m3u",
    "Url": "http://ersatztv-next:8409/iptv/channels.m3u",
    "FriendlyName": "ErsatzTV Next",
    "ImportFavoritesOnly": false,
    "AllowHWTranscoding": false,
    "EnableStreamLooping": false,
    "Source": "ErsatzTV Next",
    "TunerCount": 4
  }'

# 2. Add the XMLTV listings provider, then bind it to all M3U tuner channels.
curl -X POST "http://localhost:18096/LiveTv/ListingProviders" \
  -H "X-Emby-Token: $JELLYFIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "Type": "xmltv",
    "Path": "http://ersatztv-next:8409/iptv/xmltv.xml",
    "EnableAllTuners": true
  }'
```

Notes:

- The hostname `ersatztv-next` resolves inside the Docker network — both containers share the bundled compose's default network. From the host browser, the URLs would be `http://localhost:18409/iptv/channels.m3u`, but the in-container form is what Jellyfin should hold so it survives container restarts.
- `TunerCount: 4` lets four clients watch live channels simultaneously. ErsatzTV Next handles the actual fan-out.
- `EnableAllTuners: true` on the listings provider means the XMLTV guide applies to every channel from the M3U source.

## Step 7 — Refresh the guide

```bash
curl -X POST "http://localhost:18096/LiveTv/Guide/Refresh" \
  -H "X-Emby-Token: $JELLYFIN_TOKEN"
```

This is the same call the daily refresh routine makes after re-emitting playout JSON, so the EPG always matches what's actually scheduled.

## Verify

```bash
curl -s "http://localhost:18096/Library/VirtualFolders" \
  -H "X-Emby-Token: $JELLYFIN_TOKEN" | jq '.[] | {Name, CollectionType, Locations: .Locations|length}'

curl -s "http://localhost:18096/LiveTv/Channels" \
  -H "X-Emby-Token: $JELLYFIN_TOKEN" | jq '.Items | length'
```

The first should match the discovered library count from Step 0. The second should match the number of channels in `lineup.json` (zero on initial bring-up; populates as `/ersatztv-program` writes channels).

## Things this recipe does not do

- Migrate users (admin user is created fresh by the first-run wizard).
- Migrate watch history, favorites, or play counts.
- Migrate Jellyfin's smart collections — Jellyfin's built-in smart collection feature is sparse; the plugin instead resolves themes via the daily routine.
- Replicate the Recordings library — that's DVR-only and pointless without an HDHR. ErsatzTV Next handles "live" via the M3U tuner above.
