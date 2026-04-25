#!/usr/bin/env bash
#
# SessionStart hook: two checks, both soft-fail.
#
#   1. Marketplace drift — has the MeridianVega/claude-marketplace repo
#      gained commits since this plugin's cached SHA? If so, suggest
#      `/plugin marketplace update`.
#
#   2. ErsatzTV Next schema drift — has upstream bumped the playout
#      schema's $id beyond the version this plugin's `reference` skill
#      pins? If so, suggest filing an issue or `/plugin marketplace
#      update` to pick up a refreshed reference once the maintainer
#      has updated the plugin.
#
# Output contract: a single SessionStart hookSpecificOutput JSON object
# with `additionalContext`, per the official hook spec
# (https://code.claude.com/docs/en/hooks — "SessionStart decision
# control"). Verbatim shape from the spec:
#
#     { "hookSpecificOutput": { "hookEventName": "SessionStart",
#                               "additionalContext": "..." } }
#
# Soft-fail on every error: missing curl, network down, rate limit,
# malformed JSON. A flaky network never blocks a session start.

set -u
LC_ALL=C
export LC_ALL

MARKETPLACE_API="https://api.github.com/repos/MeridianVega/claude-marketplace/commits/main"
SCHEMA_URL="https://raw.githubusercontent.com/ErsatzTV/next/main/schema/playout.json"

# Pin: bump this when the `reference` skill is updated to track a
# newer upstream schema. Source of truth lives at
# https://github.com/ErsatzTV/next/blob/main/schema/playout.json
PINNED_SCHEMA_VERSION="https://ersatztv.org/playout/version/0.0.1"

CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/ersatztv-programmer"
if [ "$(uname -s)" = "Darwin" ]; then
    # Apple File System Programming Guide: per-app caches under
    # ~/Library/Caches/<bundle-id-style>. We don't ship a bundle, so
    # use a reverse-DNS-style identifier under our org.
    CACHE_DIR="$HOME/Library/Caches/com.meridianvega.ersatztv-programmer"
fi
MARKETPLACE_CACHE="$CACHE_DIR/last-seen-sha"
SCHEMA_CACHE="$CACHE_DIR/last-seen-schema"

# atomic_write FILE CONTENT — write to a sibling temp then rename, so two
# concurrent SessionStart hooks can't half-overwrite each other.
atomic_write() {
    local target="$1"
    local content="$2"
    local tmp="${target}.tmp.$$"
    if printf '%s' "$content" > "$tmp" 2>/dev/null; then
        mv "$tmp" "$target" 2>/dev/null || rm -f "$tmp" 2>/dev/null || true
    fi
}

if ! command -v curl >/dev/null 2>&1; then
    exit 0
fi

mkdir -p "$CACHE_DIR" 2>/dev/null || exit 0

NOTICES=()

# --- 1. Marketplace drift ---------------------------------------------

MP_RESPONSE=$(curl -fsS --max-time 5 \
    -H "Accept: application/vnd.github+json" \
    -H "User-Agent: ersatztv-programmer-update-check" \
    "$MARKETPLACE_API" 2>/dev/null) || MP_RESPONSE=""

if [ -n "$MP_RESPONSE" ]; then
    LATEST_MP=$(printf '%s' "$MP_RESPONSE" | grep -m1 '"sha"' | sed -E 's/.*"sha": ?"([0-9a-f]+)".*/\1/')
    if [ -n "$LATEST_MP" ]; then
        LAST_SEEN_MP=""
        [ -f "$MARKETPLACE_CACHE" ] && LAST_SEEN_MP=$(cat "$MARKETPLACE_CACHE" 2>/dev/null || true)

        if [ -z "$LAST_SEEN_MP" ]; then
            # First run — seed the cache, do not nag.
            atomic_write "$MARKETPLACE_CACHE" "$LATEST_MP"
        elif [ "$LAST_SEEN_MP" != "$LATEST_MP" ]; then
            SHORT_NEW="${LATEST_MP:0:7}"
            SHORT_OLD="${LAST_SEEN_MP:0:7}"
            NOTICES+=("ersatztv-programmer plugin: marketplace HEAD is ${SHORT_NEW}, locally cached ${SHORT_OLD}. Run '/plugin marketplace update' then '/reload-plugins' to apply. Source: https://github.com/MeridianVega/claude-marketplace/commits/main")
            atomic_write "$MARKETPLACE_CACHE" "$LATEST_MP"
        fi
    fi
fi

# --- 2. ErsatzTV Next schema drift ------------------------------------

SCHEMA_RESPONSE=$(curl -fsS --max-time 5 \
    -H "Accept: application/json" \
    -H "User-Agent: ersatztv-programmer-update-check" \
    "$SCHEMA_URL" 2>/dev/null) || SCHEMA_RESPONSE=""

if [ -n "$SCHEMA_RESPONSE" ]; then
    LIVE_SCHEMA=$(printf '%s' "$SCHEMA_RESPONSE" | grep -m1 '"\$id"' | sed -E 's/.*"\$id": ?"([^"]+)".*/\1/')
    if [ -n "$LIVE_SCHEMA" ]; then
        if [ "$LIVE_SCHEMA" != "$PINNED_SCHEMA_VERSION" ]; then
            LAST_SEEN_SCHEMA=""
            [ -f "$SCHEMA_CACHE" ] && LAST_SEEN_SCHEMA=$(cat "$SCHEMA_CACHE" 2>/dev/null || true)
            if [ "$LAST_SEEN_SCHEMA" != "$LIVE_SCHEMA" ]; then
                NOTICES+=("ErsatzTV Next playout schema bumped: live ${LIVE_SCHEMA}, ersatztv-programmer pinned ${PINNED_SCHEMA_VERSION}. The bundled 'reference' skill may be out of date. Verify against ${SCHEMA_URL}; if drift confirmed, file an issue at https://github.com/MeridianVega/claude-marketplace/issues so the plugin's reference skill can be updated.")
                atomic_write "$SCHEMA_CACHE" "$LIVE_SCHEMA"
            fi
        else
            # Same version — keep the cache fresh so a later drift starts
            # from the right baseline.
            printf '%s' "$LIVE_SCHEMA" > "$SCHEMA_CACHE" 2>/dev/null || true
        fi
    fi
fi

# --- Emit ---------------------------------------------------------------

if [ ${#NOTICES[@]} -gt 0 ]; then
    # Concatenate notices with literal "; " (additionalContext is plain
    # text, multiple SessionStart hooks' values are concatenated by the
    # runtime).
    JOINED=""
    for n in "${NOTICES[@]}"; do
        if [ -z "$JOINED" ]; then
            JOINED="$n"
        else
            JOINED="${JOINED}; ${n}"
        fi
    done
    # Encode for JSON embedding. Prefer python3 since it ships on macOS
    # and most Linux distros and handles every JSON escape case correctly
    # (newlines, tabs, control chars, unicode). Fall back to a sed-based
    # encoder that handles backslash, quote, tab, CR, LF — sufficient for
    # the notice strings this script produces, but not for arbitrary input.
    if command -v python3 >/dev/null 2>&1; then
        ESCAPED=$(printf '%s' "$JOINED" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read())[1:-1])')
    else
        ESCAPED=$(printf '%s' "$JOINED" \
            | sed -e 's/\\/\\\\/g' \
                  -e 's/"/\\"/g' \
                  -e ':a' -e 'N' -e '$!ba' \
                  -e 's/\n/\\n/g' \
                  -e 's/\r/\\r/g' \
                  -e 's/\t/\\t/g')
    fi
    cat <<EOF
{
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "$ESCAPED"
  }
}
EOF
fi

exit 0
