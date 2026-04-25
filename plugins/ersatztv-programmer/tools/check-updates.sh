#!/usr/bin/env bash
#
# SessionStart hook: checks the marketplace's GitHub repo for newer commits
# than the cached "last seen" SHA and surfaces a notice in the session if
# the local plugin is behind. Uses the public commits API — no auth, no
# `gh` CLI dependency.
#
# Output contract: writes additionalContext JSON to stdout for the hook
# runner to inject into Claude's context. Exits 0 on success, 0 on
# soft-fail (network error, rate limit, missing curl) so a flaky network
# never blocks a session.

set -u
LC_ALL=C
export LC_ALL

REPO_API="https://api.github.com/repos/MeridianVega/claude-marketplace/commits/main"
CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/ersatztv-programmer"
CACHE_FILE="$CACHE_DIR/last-seen-sha"

emit_context() {
    local message="$1"
    # Hook output schema: hookSpecificOutput.additionalContext gets injected
    # into the model's context for the SessionStart event.
    cat <<EOF
{
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "$message"
  }
}
EOF
}

# Soft-fail if curl is unavailable. Mac and most Linux ship with it.
if ! command -v curl >/dev/null 2>&1; then
    exit 0
fi

mkdir -p "$CACHE_DIR" 2>/dev/null || exit 0

# Fetch latest commit SHA. -fsS: fail silently on HTTP error, show error
# only on stderr; --max-time 5 keeps the session-start cost bounded.
RESPONSE=$(curl -fsS --max-time 5 \
    -H "Accept: application/vnd.github+json" \
    -H "User-Agent: ersatztv-programmer-update-check" \
    "$REPO_API" 2>/dev/null) || exit 0

# Extract the .sha field with grep + sed (avoids requiring jq).
LATEST_SHA=$(printf '%s' "$RESPONSE" | grep -m1 '"sha"' | sed -E 's/.*"sha": ?"([0-9a-f]+)".*/\1/')

if [ -z "$LATEST_SHA" ]; then
    exit 0
fi

LAST_SEEN=""
[ -f "$CACHE_FILE" ] && LAST_SEEN=$(cat "$CACHE_FILE" 2>/dev/null || true)

if [ -z "$LAST_SEEN" ]; then
    # First run — cache the current SHA, do not nag the user about
    # "updates" they couldn't have applied yet.
    printf '%s' "$LATEST_SHA" > "$CACHE_FILE" 2>/dev/null || true
    exit 0
fi

if [ "$LAST_SEEN" != "$LATEST_SHA" ]; then
    SHORT_NEW="${LATEST_SHA:0:7}"
    SHORT_OLD="${LAST_SEEN:0:7}"
    emit_context "ersatztv-programmer plugin update available: marketplace HEAD is ${SHORT_NEW}, locally cached ${SHORT_OLD}. Recommend running '/plugin marketplace update' followed by '/reload-plugins' to apply. Source: https://github.com/MeridianVega/claude-marketplace/commits/main"
    # Update the cache *after* surfacing the notice once, so the next
    # session won't repeat it for the same SHA.
    printf '%s' "$LATEST_SHA" > "$CACHE_FILE" 2>/dev/null || true
fi

exit 0
