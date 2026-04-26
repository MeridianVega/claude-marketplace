---
name: librarian-taste
description: Builds and maintains the user's content-taste profile (taste.md). Loads on first librarian invocation (psychology session — library survey + targeted Qs, or pure interview if no library), and on every subsequent run to read the stored profile back into context. Also handles append-only feedback hooks ("Recent confirmed loves/misses").
disable-model-invocation: false
user-invocable: false
---

# Taste profile — building and reading

The librarian agent never picks content without a taste profile. This skill
owns the profile's creation, format, and update rules. Two paths into a
profile:

- **Library survey + targeted Qs** — the user already has a Jellyfin library
  with ≥50 movies/shows. Mine the library for signal, draft a profile,
  ask 8–12 disambiguating questions, write the result.
- **Pure interview** — fewer than 50 items in the library (or no Jellyfin at
  all). ~15 questions cold, no library to ground them.

Both paths converge on the same `taste.md` schema described below.

## Where the profile lives

| OS | Path |
| :--- | :--- |
| macOS | `~/Library/Application Support/ersatztv-programmer/taste.md` |
| Linux | `${XDG_CONFIG_HOME:-$HOME/.config}/ersatztv-programmer/taste.md` |
| Windows | `%APPDATA%\ersatztv-programmer\taste.md` |

If the file exists, this skill is in **read mode** — load it into context
and return. If it doesn't exist, run the session.

## taste.md schema

Plain markdown, freeform within sections. The librarian agent is the only
writer. Sections are required even if empty (so future appends know
where to land). Header always carries the version + last-session date.

```markdown
# Taste profile
_Version: 1 — last session: 2026-04-27_

## Always include
- Anything matching: comfort sitcom rewatchability, atmospheric horror,
  late-90s prestige drama, Studio Ghibli.

## Never include
- Hard-R extreme horror (Saw / Hostel-tier gore).
- Crypto/finance documentaries.
- Reality competition shows.
- Anything starring [name] after [date].

## Tilt toward
- 70s–80s slasher canon, especially the unranked-deeper-cut tier.
- A24 catalog (auteur indie horror, slow cinema, festival drama).
- 90s sitcoms with strong ensemble; less interest in solo-lead vehicles.
- WCW/ECW wrestling pre-2001; little interest in WWE.

## Recent confirmed loves
_(append-only; the librarian writes here after the user gives feedback
on a fetch run.)_
- 2026-04-15 — *The Burning* (1981) — felt right for the channel's vibe.
- 2026-04-22 — *Pearl* (2022) — A24 horror; want more like this.

## Recent confirmed misses
_(append-only)_
- 2026-04-18 — *Terrifier 2* (2022) — too gory, drop similar picks.

## Household constraints
- Family-safe by default during 6 AM–9 PM blocks.
- Two adults, no kids in the house — late-night blocks can run R.
- Streaming over cellular is rare; size ceilings are flexible.

## Open questions for next session
_(librarian writes here when a question would have improved a fetch but
wasn't worth blocking the run for. Clears at next psychology session.)_
- Does the user prefer Bluray sources to WEB-DL when both available?
- Is the Wrestling library a personal thing or a family-shared thing?
```

## Path A — library survey + targeted questions

Use when Jellyfin holds ≥50 items.

### Step 1 — survey the library

Direct SQLite read off Jellyfin's library DB (faster than the MCP for bulk
counts). Default path inside the bundled stack:
`~/ersatztv-stack/config/jellyfin/data/library.db`. Open read-only:

```bash
sqlite3 "file:${JELLYFIN_DB}?mode=ro" -readonly <<'SQL'
.headers on
.mode column

-- Per-library item count + total size
SELECT
    json_extract(d.value, '$.Name')             AS Library,
    json_extract(d.value, '$.CollectionType')   AS Kind,
    COUNT(b.guid)                               AS Items,
    printf('%.1f GB', SUM(b.Size) / 1.0e9)      AS Size
FROM TypedBaseItems b
JOIN json_each(...)                             d  -- shape varies; introspect first
GROUP BY Library
ORDER BY Items DESC;

-- Genre frequency across movies + shows
SELECT g.value AS Genre, COUNT(*) AS N
FROM TypedBaseItems b, json_each(b.Genres) g
WHERE b.Type IN (
    'MediaBrowser.Controller.Entities.Movies.Movie',
    'MediaBrowser.Controller.Entities.TV.Series'
)
GROUP BY g.value
ORDER BY N DESC
LIMIT 30;

-- Decade distribution for movies
SELECT (b.ProductionYear / 10 * 10) AS Decade, COUNT(*) AS N
FROM TypedBaseItems b
WHERE b.Type = 'MediaBrowser.Controller.Entities.Movies.Movie'
  AND b.ProductionYear IS NOT NULL
GROUP BY Decade
ORDER BY Decade DESC;

-- Top 50 most-played items (UserDataKeys joins per-user playback state)
SELECT b.Name, b.Type, ud.PlayCount, ud.LastPlayedDate, ud.IsFavorite
FROM TypedBaseItems b
JOIN UserDatas ud ON ud.Key = b.UserDataKey
WHERE ud.PlayCount > 0
ORDER BY ud.PlayCount DESC
LIMIT 50;

-- Anomalies — collections vastly bigger than their genre alone explains
SELECT Studios, COUNT(*) AS N
FROM TypedBaseItems
WHERE Studios IS NOT NULL
GROUP BY Studios
HAVING N >= 8
ORDER BY N DESC
LIMIT 20;
SQL
```

Schema names drift across Jellyfin versions; if a query fails, run
`.tables` and `.schema TypedBaseItems` first to confirm column shapes,
then adapt. The MCP is the fallback if SQLite isn't reachable.

### Step 2 — draft a profile from the data

Synthesize a markdown draft, NOT yet written to disk. Sections:

- **Library snapshot** — counts, sizes, top genres, top decades.
- **Inferred always-include** — anything that's heavily over-represented
  vs. user-base average and has high play counts (e.g. "you have 300+
  Friends episodes with 200+ plays — comfort sitcom is real signal").
- **Inferred tilt toward** — top genres weighted by play count, not just
  presence. A 200-item horror library with 4 plays = collected, not
  consumed; flag for clarification.
- **Anomalies to confirm** — overrepresented studios/franchises (e.g.
  "you have 47 wrestling pay-per-views" — household-shared? personal?
  acquire-more-of? leave-as-archive?).

### Step 3 — targeted question pass

Show the draft to the user in chat, ask 8–12 questions to disambiguate:

> Looking at your library I see four signals I want to confirm:
>
> 1. Friends has 200+ plays — comfort show, or a partner's? Should the
>    librarian prioritize sitcom-format comfort content when filling
>    gaps?
> 2. You have 14 A24 films — auteur/indie focus, or you just liked a
>    few of them? Should I tilt toward A24's full catalog?
> 3. Horror skews 1970s–80s. Should new horror picks lean grindhouse,
>    or modern atmospheric (Pearl, Hereditary)?
> 4. Wrestling library is huge (47 PPVs). Personal nostalgia, or
>    actively curated for new content? If new — WCW/ECW canon, or open
>    to recent stuff?
> 5. What ages live in this house? Default to family-safe?
> 6. Recent watch you loved? (helps anchor "tilt toward")
> 7. Recent watch you regretted? (helps anchor "never include")
> 8. Anything I'd guess wrong about you from this library alone?

Take the answers. Refine the draft. Ask the user to read it before
saving. **Save only when the user confirms** — Ctrl-C aborts cleanly
without a half-written profile.

### Step 4 — write `taste.md`

Render the schema described above using the answers + library-derived
inferences. Set `Version: 1`, `last session: <today>`. Write atomically.

## Path B — pure interview

Use when there's no library to mine. Slower path; ~15 questions across
five themes:

1. **Era anchor** — "What's a defining year of TV/film for you, and what
   did it look like? (e.g. '1996 — sitcoms, Buffy, Scream')."
2. **Comfort vs. discovery balance** — "When you sit down to watch
   something, are you 80% reaching for known comfort and 20% trying new
   stuff, or the inverse, or 50/50?"
3. **Format mix** — "Movies, hour-long drama, half-hour sitcom, music
   video, sports, anime, documentary — rank them, and which would you
   miss most if it disappeared?"
4. **Hard stops** — "What do you actively dislike? Genres, eras, content
   types you'd never voluntarily watch?"
5. **Household constraints** — "Who's watching with you? Kids in the
   house? Family-safe by default, or 'whatever I'm into is fine'?"

Mid-interview, expand one signal that surprised you with two follow-ups
("you said 1996 was your year — what about that year specifically?").
Don't run all 15 robotically — the conversation should feel like
talking to a knowledgeable record-store clerk, not a form.

Same Step 4 — render schema, confirm, save.

## Re-running the session

`/librarian --re-session` (slash command flag) or invocation message
saying "rebuild taste":

1. Read existing `taste.md` for context (what we knew last time).
2. Run Path A or Path B as appropriate.
3. Save with bumped version number.
4. **Preserve "Recent confirmed loves" / "Recent confirmed misses"** —
   those are user-derived signal, not librarian inference. Carry them
   forward into the new file.

Default cadence for re-running: ~6 months, or when the librarian's
inferences start being wrong twice in a row (track via the run logs).

## Append-only feedback hook

After each fetch run, if the caller (or user via `/librarian` follow-up)
gives feedback like *"the Burning was perfect, more like that"* or
*"Terrifier 2 was too gory, drop similar"*, append a line to **Recent
confirmed loves** or **Recent confirmed misses** with the date, title,
and one-sentence reason. Never edit other sections from feedback —
those reflect the user's stated profile, not pattern-matched inferences.

If a feedback signal repeatedly contradicts a stated section ("Never
include" rule fires every run because the user keeps loving stuff in
that genre), add an **Open question** for the next psychology session
rather than editing the section yourself.

## Hard rules for this skill

- Never write `taste.md` without the user reading and confirming the
  draft. The whole point is the user's voice, not the librarian's.
- Never lie about what was inferred — if the survey said "horror is
  collected, not consumed," show the user that and let them clarify.
- Never include credentials or paths in `taste.md` — it's a content
  document and may be shared / git-versioned by the user later.
- Never delete sections; preserve schema even when empty.
- Never overwrite "Recent confirmed loves/misses" during a re-session;
  carry them forward verbatim.
