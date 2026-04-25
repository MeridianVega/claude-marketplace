#!/usr/bin/env python3
"""
Validate an ErsatzTV Next playout JSON file against the schema.

Standard library only — no third-party deps. Prints findings to stdout
and exits 0 if the file is valid, 1 if it's not.

Usage:
    playout-validate.py PATH [PATH ...]

What it checks (in order, short-circuit on first fatal error per file):
    1.  File parses as JSON.
    2.  Top-level has `version` and `items`.
    3.  `version` is a known schema URI (warn on unknown, don't fail).
    4.  `items` is a list of objects, each with required `id`, `start`, `finish`.
    5.  Each `id` is unique within the file.
    6.  Each `start` and `finish` parse as RFC 3339 datetimes with explicit
        timezone offsets (no naive datetimes, no `Z` shorthand).
    7.  `start < finish` for every item.
    8.  Items are non-overlapping and contiguous (warn-only — convention,
        not schema-enforced).
    9.  Each item's `source` (or all `tracks[*].source`) is one of the three
        known variants with the correct required fields.

Exit codes:
    0   all files validate (warnings allowed)
    1   one or more files have at least one fatal error
    2   bad invocation (e.g. no path supplied)
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

KNOWN_VERSIONS = {"https://ersatztv.org/playout/version/0.0.1"}

# RFC 3339 with explicit numeric offset. Accepts fractional seconds with any
# number of digits (the schema example uses 9). Rejects "Z" (the schema docs
# specify numeric-offset form).
RFC3339_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[+-]\d{2}:\d{2})$"
)


class Findings:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def err(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    @property
    def ok(self) -> bool:
        return not self.errors

    def emit(self) -> None:
        prefix = "OK " if self.ok else "ERR"
        print(f"{prefix} {self.path}")
        for w in self.warnings:
            print(f"     warn: {w}")
        for e in self.errors:
            print(f"     error: {e}")


def parse_dt(s: str) -> datetime | None:
    if not isinstance(s, str) or not RFC3339_RE.match(s):
        return None
    # datetime.fromisoformat handles full RFC 3339 including offset.
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def validate_source(source: object, where: str, f: Findings) -> None:
    if not isinstance(source, dict):
        f.err(f"{where}: source must be an object, got {type(source).__name__}")
        return
    st = source.get("source_type")
    if st == "local":
        path = source.get("path")
        if not isinstance(path, str) or not path:
            f.err(f"{where}: local source requires non-empty 'path'")
        for k in ("in_point_ms", "out_point_ms"):
            v = source.get(k)
            if v is not None and not (isinstance(v, int) and v >= 0):
                f.err(f"{where}: '{k}' must be a non-negative integer or null")
    elif st == "lavfi":
        params = source.get("params")
        if not isinstance(params, str) or not params:
            f.err(f"{where}: lavfi source requires non-empty 'params'")
    elif st == "http":
        uri = source.get("uri")
        if not isinstance(uri, str) or not uri:
            f.err(f"{where}: http source requires non-empty 'uri'")
        for k in ("in_point_ms", "out_point_ms"):
            v = source.get(k)
            if v is not None and not (isinstance(v, int) and v >= 0):
                f.err(f"{where}: '{k}' must be a non-negative integer or null")
    else:
        f.err(f"{where}: unknown source_type {st!r} (must be local, lavfi, or http)")


def validate_item(idx: int, item: object, f: Findings, prev_finish: datetime | None) -> datetime | None:
    where = f"items[{idx}]"
    if not isinstance(item, dict):
        f.err(f"{where}: must be an object, got {type(item).__name__}")
        return None

    for required in ("id", "start", "finish"):
        if required not in item:
            f.err(f"{where}: missing required field '{required}'")

    item_id = item.get("id")
    if "id" in item and not isinstance(item_id, str):
        f.err(f"{where}.id: must be a string")

    start = parse_dt(item.get("start"))
    if "start" in item and start is None:
        f.err(f"{where}.start: not RFC 3339 with numeric offset (got {item.get('start')!r})")
    finish = parse_dt(item.get("finish"))
    if "finish" in item and finish is None:
        f.err(f"{where}.finish: not RFC 3339 with numeric offset (got {item.get('finish')!r})")

    if start and finish and start >= finish:
        f.err(f"{where}: start must be < finish")

    if prev_finish and start and start != prev_finish:
        if start < prev_finish:
            f.err(f"{where}: overlaps the previous item (start {start.isoformat()} < prev finish {prev_finish.isoformat()})")
        else:
            gap = (start - prev_finish).total_seconds()
            f.warn(f"{where}: gap of {gap:.0f}s after previous item — channel will go dark unless filled")

    has_source = "source" in item and item["source"] is not None
    has_tracks = "tracks" in item and item["tracks"] is not None
    if not (has_source or has_tracks):
        f.err(f"{where}: must have either 'source' or 'tracks'")
    if has_source:
        validate_source(item["source"], f"{where}.source", f)
    if has_tracks:
        tracks = item["tracks"]
        if not isinstance(tracks, dict):
            f.err(f"{where}.tracks: must be an object")
        else:
            for kind in ("video", "audio", "subtitle"):
                t = tracks.get(kind)
                if t is None:
                    continue
                if not isinstance(t, dict):
                    f.err(f"{where}.tracks.{kind}: must be an object or null")
                    continue
                if "source" in t and t["source"] is not None:
                    validate_source(t["source"], f"{where}.tracks.{kind}.source", f)
                idx_field = t.get("stream_index")
                if idx_field is not None and not (isinstance(idx_field, int) and idx_field >= 0):
                    f.err(f"{where}.tracks.{kind}.stream_index: must be a non-negative integer or null")

    return finish


def validate_file(path: Path) -> Findings:
    f = Findings(path)

    if not path.exists():
        f.err(f"file not found")
        return f

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        f.err(f"JSON parse failed: {e.msg} at line {e.lineno} col {e.colno}")
        return f
    except OSError as e:
        f.err(f"cannot read: {e}")
        return f

    if not isinstance(data, dict):
        f.err("top-level must be a JSON object")
        return f

    version = data.get("version")
    if not isinstance(version, str) or not version:
        f.err("missing or empty top-level 'version'")
    elif version not in KNOWN_VERSIONS:
        f.warn(f"version {version!r} not in known set {sorted(KNOWN_VERSIONS)}; reference may be stale")

    items = data.get("items")
    if not isinstance(items, list):
        f.err("missing or non-array top-level 'items'")
        return f

    seen_ids: set[str] = set()
    prev_finish: datetime | None = None
    for idx, item in enumerate(items):
        item_id = item.get("id") if isinstance(item, dict) else None
        if isinstance(item_id, str):
            if item_id in seen_ids:
                f.err(f"items[{idx}].id: duplicate id {item_id!r}")
            seen_ids.add(item_id)
        prev_finish = validate_item(idx, item, f, prev_finish)

    return f


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(f"usage: {Path(argv[0]).name} PATH [PATH ...]", file=sys.stderr)
        return 2

    any_failed = False
    for raw in argv[1:]:
        result = validate_file(Path(raw))
        result.emit()
        if not result.ok:
            any_failed = True

    return 1 if any_failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
