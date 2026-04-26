#!/bin/sh
# Writes /usr/share/nginx/html/env.js with the +10000 host-port mapping
# the dashboard reads on load. Called by nginx:alpine's
# /docker-entrypoint.d/ hook before nginx starts. Regenerated every
# container start so .env changes are picked up.

set -eu

OUT=/usr/share/nginx/html/env.js

if [ -f "$OUT" ]; then
    chmod 0644 "$OUT" || true
fi

cat > "$OUT" <<EOF
// Auto-generated on container start. DO NOT EDIT — overwritten on restart.
window.seadog_env = Object.freeze({
  ports: Object.freeze({
    ersatztv:   "${ERSATZTV_PORT:-18409}",
    jellyfin:   "${JELLYFIN_PORT:-18096}",
    jellyseerr: "${JELLYSEERR_PORT:-15055}",
    prowlarr:   "${PROWLARR_PORT:-19696}",
    sonarr:     "${SONARR_PORT:-18989}",
    radarr:     "${RADARR_PORT:-17878}",
    lidarr:     "${LIDARR_PORT:-18686}",
    bazarr:     "${BAZARR_PORT:-16767}",
    nzbget:     "${NZBGET_PORT:-16789}",
  }),
});
EOF
