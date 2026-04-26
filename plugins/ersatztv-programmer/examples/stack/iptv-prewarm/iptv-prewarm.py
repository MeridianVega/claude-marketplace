#!/usr/bin/env python3
"""
iptv-prewarm.py — HLS pre-warm proxy in front of ErsatzTV Next.

ETV Next's session manager terminates channel sessions after ~90 s of
client idle. After that, /session/{N}/live.m3u8 still responds with HTTP
200 but the body is an empty playlist (just headers, no segment lines).
Strict HLS clients like Jellyfin Live TV interpret this as a fatal
playback error.

This proxy fronts every Jellyfin tune-in:

  GET /iptv/{N}/live.m3u8
    ↓ if the upstream /session/{N}/live.m3u8 has no segments listed,
      poke /channel/{N}.m3u8 to wake the session, then poll up to
      WARMUP_TIMEOUT_S for segments to appear.
    ↓ once segments are listed, proxy the playlist body back.

  GET /iptv/{N}/{segment}.ts
    → straight proxy to /session/{N}/{segment}.ts

  GET /iptv/{N}/ffmpeg.m3u8
    → straight proxy (debug aid).

The M3U we serve via the existing xmltv sidecar points at /iptv/... URLs
so Jellyfin always hits this proxy and never gets a stale empty playlist.

Defaults are tuned for the bundled stack (ETV at host:18409, this proxy
at :18407). Override via env vars: ETV_BASE, LISTEN_PORT, WARMUP_TIMEOUT_S,
WARMUP_POLL_INTERVAL_S.
"""

from __future__ import annotations

import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import request as urllib_request
from urllib.error import HTTPError, URLError

ETV_BASE = os.environ.get("ETV_BASE", "http://ersatztv-next:8409")
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "8407"))
WARMUP_TIMEOUT_S = float(os.environ.get("WARMUP_TIMEOUT_S", "20"))
WARMUP_POLL_INTERVAL_S = float(os.environ.get("WARMUP_POLL_INTERVAL_S", "0.5"))


def fetch(url: str, timeout: float = 5.0) -> tuple[int, bytes, str]:
    """Return (status, body, content_type) for url. Never raises."""
    try:
        with urllib_request.urlopen(url, timeout=timeout) as resp:
            return resp.status, resp.read(), resp.headers.get("Content-Type", "")
    except HTTPError as e:
        return e.code, e.read() if e.fp else b"", e.headers.get("Content-Type", "") if e.headers else ""
    except (URLError, TimeoutError, ConnectionError):
        return 0, b"", ""


def m3u8_has_segments(body: bytes) -> bool:
    return b".ts" in body


def kick_session(channel: int) -> None:
    """Hit /channel/{N}.m3u8 to wake the session. ETV returns 404 but the
    side-effect is the session worker spinning up + ffmpeg launching."""
    fetch(f"{ETV_BASE}/channel/{channel}.m3u8", timeout=2.0)


def warm_then_fetch_playlist(channel: int) -> tuple[int, bytes, str]:
    """Returns the live.m3u8 body once it has segments, kicking the session
    if needed and polling up to WARMUP_TIMEOUT_S."""
    url = f"{ETV_BASE}/session/{channel}/live.m3u8"

    # Fast path: already warm
    status, body, ctype = fetch(url, timeout=2.0)
    if status == 200 and m3u8_has_segments(body):
        return status, body, ctype

    # Cold — kick and poll
    kick_session(channel)
    deadline = time.monotonic() + WARMUP_TIMEOUT_S
    while time.monotonic() < deadline:
        time.sleep(WARMUP_POLL_INTERVAL_S)
        status, body, ctype = fetch(url, timeout=2.0)
        if status == 200 and m3u8_has_segments(body):
            return status, body, ctype

    # Timed out — return whatever we have so the client sees something
    return status, body, ctype


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:
        # One-line access log so docker logs is readable
        print(f"{self.address_string()} - {fmt % args}", flush=True)

    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler convention)
        path = self.path
        # Expected: /iptv/{N}/{file}
        parts = path.lstrip("/").split("/")
        if len(parts) < 3 or parts[0] != "iptv":
            self.send_error(404, "use /iptv/{channel}/{file}")
            return

        try:
            channel = int(parts[1])
        except ValueError:
            self.send_error(400, "channel must be an integer")
            return

        file_name = parts[2]

        if file_name == "live.m3u8":
            status, body, ctype = warm_then_fetch_playlist(channel)
        else:
            url = f"{ETV_BASE}/session/{channel}/{file_name}"
            status, body, ctype = fetch(url, timeout=15.0)

        if status == 0:
            self.send_error(502, "upstream unreachable")
            return

        self.send_response(status)
        self.send_header("Content-Type", ctype or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", LISTEN_PORT), Handler)
    print(f"iptv-prewarm listening on :{LISTEN_PORT}, upstream {ETV_BASE}", flush=True)
    print(f"  warmup timeout {WARMUP_TIMEOUT_S}s, poll interval {WARMUP_POLL_INTERVAL_S}s", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
