---
name: librarian
description: Find and download new content for the library. First run does a psychology session to learn your taste; subsequent runs take a freeform request and return picks (dry-run by default), then queue downloads via NZBGet on approval. Args optional — pass a request to skip the conversational prelude.
argument-hint: "[request]"
disable-model-invocation: true
---

Spawn the `librarian` agent to find and acquire content.

Arguments: $ARGUMENTS

Procedure:

1. **Check for taste profile.** Look for `taste.md` at the OS-appropriate
   plugin config directory:
   - macOS: `~/Library/Application Support/ersatztv-programmer/taste.md`
   - Linux: `${XDG_CONFIG_HOME:-$HOME/.config}/ersatztv-programmer/taste.md`
   - Windows: `%APPDATA%\ersatztv-programmer\taste.md`

2. **If taste.md is missing** — spawn the librarian agent in
   psychology-session mode. Prompt:
   ```
   No taste profile yet. Please run the psychology session per the
   librarian-taste skill: survey the user's Jellyfin library if it has
   ≥50 items (path from config.yaml.media_server), otherwise run the
   pure interview. Save taste.md when the user confirms the draft.
   Return immediately after — do NOT proceed to content acquisition in
   this same invocation.
   ```
   After the session writes `taste.md`, tell the user to re-run
   `/librarian <request>` to actually fetch content.

3. **If taste.md exists and $ARGUMENTS is empty** — ask the user what
   they want to find. Hint at what the librarian can do:
   ```
   What should I look for? Some shapes that work:
     - "20 mid-budget 80s slasher films, max 60 GB"
     - "more like Pearl and Hereditary, 1080p, max 30 GB"
     - "Christopher Nolan filmography I don't already have"
     - "fill out the WCW library — pre-2001 PPVs"
     - "queue-status" — just show me what's downloading
     - "rebuild taste" — re-run the psychology session
   ```

4. **If taste.md exists and $ARGUMENTS is set** — spawn the librarian
   agent. Special arg shapes first:
   - `queue-status` → spawn with `mode: queue-status`. No new picks.
   - `rebuild taste` or `--re-session` → spawn in re-session mode per
     `librarian-taste`'s "Re-running the session" path.
   - Otherwise: spawn with the prompt:
     ```
     Need: $ARGUMENTS
     Reason: Direct user request via /librarian.
     Disk budget: <ask the user, default to 60 GB>
     Approval: dry-run
     ```

5. **Dry-run review.** When the librarian returns picks, show the
   markdown table the agent rendered. Ask the user to approve, drop,
   or modify (e.g. "go on 1-12, skip 13-17").

6. **Queue.** Re-spawn the librarian with `approval: auto, only_pick_ids: [...]`
   to actually submit to NZBGet. Return the run-log path so the user
   can revisit later.

Constraints:

- Always default to `approval: dry-run` for the first call. Never queue
  downloads silently.
- Never bypass the taste-profile precondition. If `taste.md` is
  missing, the psychology session runs first — period.
- Never store NZBGet/Prowlarr/TMDB credentials in `config.yaml`. Env
  vars only.
- After a successful queue, point the user at NZBGet's UI
  (`http://localhost:16789` or whatever `acquisition.nzbget.base_url`
  resolves to) for live download progress — the librarian doesn't
  poll.
