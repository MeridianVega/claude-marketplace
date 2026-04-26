---
name: librarian-search
description: Candidate discovery and release selection for the librarian agent. Pulls candidate titles from TMDB, dedupes against the existing Jellyfin library by TMDB/IMDB IDs, scores remaining candidates against the user's taste.md, then queries Prowlarr for release options and picks the best release per candidate using a transparent scoring formula. Loads when the librarian transitions from psychology session to actual content hunting.
disable-model-invocation: false
user-invocable: false
---

# Candidate discovery and release selection

This skill is the *find* half of the librarian's job. It turns a
free-form `need` ("20 mid-budget 80s slasher films") into a concrete
list of `(title, release, size, score)` tuples ready for the
`librarian-fetch` skill to queue.

Three stages: **discover** → **dedupe + score** → **release-select**.

## Stage 1 — discover candidates from TMDB

TMDB is the spine. The librarian agent uses the public REST API
(`api.themoviedb.org/3/...`) with a token from `TMDB_API_TOKEN` env
var. No fallback indexer at this stage — TMDB has the metadata coverage
to seed everything; Prowlarr handles releases later.

### Common queries

For movies:

```bash
# Discover by genre + year band
curl -s -G \
  -H "Authorization: Bearer ${TMDB_API_TOKEN}" \
  https://api.themoviedb.org/3/discover/movie \
  --data-urlencode "with_genres=27"          \
  --data-urlencode "primary_release_date.gte=1980-01-01" \
  --data-urlencode "primary_release_date.lte=1989-12-31" \
  --data-urlencode "vote_count.gte=50"       \
  --data-urlencode "sort_by=vote_average.desc" \
  --data-urlencode "page=1"

# Genre IDs you'll use most:
#   28 Action  12 Adventure  16 Animation  35 Comedy  80 Crime
#   99 Documentary  18 Drama  10751 Family  14 Fantasy  36 History
#   27 Horror  10402 Music  9648 Mystery  10749 Romance
#   878 Science Fiction  10770 TV Movie  53 Thriller  10752 War  37 Western
```

For TV shows: same shape against `/discover/tv` (`with_genres` IDs
overlap but check the TV genre list — e.g. `10759 Action & Adventure`,
`10765 Sci-Fi & Fantasy`).

For "more like X": `/movie/{id}/recommendations` and
`/movie/{id}/similar` give complementary signals. Recommendations is
collaborative-filtered (others-also-watched); similar is genre/keyword-
matched. Use both, dedupe.

For people-driven asks: `/search/person` → `/person/{id}/movie_credits` and
`/person/{id}/tv_credits` for filmographies.

### Pagination + ceiling

TMDB pages are 20 results each. Pull pages until you have ~3× the
caller's `need` count (so dedupe + scoring + release-search has room to
drop). Hard ceiling: 200 candidates per run — past that, the scoring
gets noisy and run time balloons.

### What to capture per candidate

Per row, persist into the run log:

```
{
  "tmdb_id": 11234,
  "imdb_id": "tt0080749",         // from /movie/{id}/external_ids
  "title": "The Burning",
  "year": 1981,
  "runtime": 91,
  "genres": ["Horror"],
  "vote_average": 6.5,
  "vote_count": 314,
  "overview": "...",              // first 200 chars only
  "popularity": 12.4,
  "language": "en"
}
```

`imdb_id` is essential for dedupe + Prowlarr search (some indexers
return cleaner results on IMDB ID than title).

## Stage 2 — dedupe against the Jellyfin library

Read Jellyfin's `library.db` (read-only). Two columns matter:

- `TypedBaseItems.ProviderIds` — newline-delimited `key=value`, e.g.
  `Tmdb=11234\nImdb=tt0080749`.
- `TypedBaseItems.OriginalTitle` + `ProductionYear` as a backup match.

```bash
sqlite3 "file:${JELLYFIN_DB}?mode=ro" -readonly <<SQL
.headers on
.mode tabs

SELECT b.guid, b.Name, b.OriginalTitle, b.ProductionYear,
       b.ProviderIds
  FROM TypedBaseItems b
 WHERE b.Type = 'MediaBrowser.Controller.Entities.Movies.Movie'
   AND b.ProviderIds LIKE '%Tmdb=11234%';
SQL
```

For each TMDB candidate:

1. Match by `Tmdb=` first.
2. Then `Imdb=`.
3. Last-resort fuzzy: lowercased title + production year. If this
   matches but neither ID matches, flag — could be a remake, sequel
   metadata mismatch, or genuinely the same film with bad metadata.
   Surface to the caller in the run log; don't silently dedupe.

Drop deduped candidates from the working set.

For TV: prefer matching at the `Series` level. Don't try to dedupe
individual seasons/episodes from TMDB → Jellyfin in a single run; if
the user has S1 and the librarian fetches S1+S2 it'll just import the
diff via Sonarr-style rename rules. (Or, if you're running without
Sonarr, the NZBGet post-processor moves the .mkv into the library and
Jellyfin's auto-merge sorts it.)

## Stage 3 — score against taste.md

Read the in-memory taste profile (already loaded by the librarian
agent). Apply scores in this order:

### Hard filters first

- **"Never include"** rules drop the candidate to score 0. Don't carry
  it into the next stage. Record the reason in the run log.

### Then weighted signals

Score in `[0, 1]`. Composite:

```
score = 0.45 * tilt_match
      + 0.30 * recent_loves_proximity
      + 0.10 * tmdb_quality
      + 0.10 * fits_household
      + 0.05 * surprise
- 0.40 * recent_misses_proximity
```

| Signal | How |
| :--- | :--- |
| `tilt_match` | 1.0 if candidate's genres + era + tags hit any "Tilt toward" line directly; partial credit by overlap fraction. |
| `recent_loves_proximity` | Closest-neighbor similarity to "Recent confirmed loves" — TMDB's `/movie/{id}/similar` or shared keywords with recent loves. |
| `recent_misses_proximity` | Same metric against "Recent confirmed misses". Subtracted. |
| `tmdb_quality` | Normalized vote_average × log(vote_count+1) / 10. Gates against unreviewed obscurities ranking high purely on era match. |
| `fits_household` | 1.0 if the candidate's certification (G/PG/PG-13/R/NC-17) fits the user's household constraints. 0.5 if R-rated and the user said "late-night R is fine." 0 if the rating is hard-out. |
| `surprise` | Small bonus for candidates the user is unlikely to have already considered (low popularity, deep-cut indicators). Capped at 0.05 so it can't dominate. |

Surface scores in the run log. Sort descending. The librarian agent
takes the top N where N >= caller's `need` count, leaving room for
release-search drops.

## Stage 4 — Prowlarr release search

For each surviving candidate, search Prowlarr's aggregated indexers:

```bash
curl -s -G \
  -H "X-Api-Key: ${PROWLARR_API_KEY}" \
  "${PROWLARR_BASE_URL}/api/v1/search" \
  --data-urlencode "query=The Burning 1981" \
  --data-urlencode "type=search" \
  --data-urlencode "categories=2000"      # 2000=Movies, 5000=TV
```

Returns an array of releases per indexer. For each release:

```json
{
  "title": "The.Burning.1981.1080p.BluRay.x265.10bit-GROUP",
  "size": 4123456789,
  "indexerId": 7,
  "indexer": "DrunkenSlug",
  "downloadUrl": "https://...",
  "infoUrl": "https://...",
  "publishDate": "2024-05-12T...",
  "seeders": null,
  "leechers": null,
  "protocol": "usenet"           // or "torrent"
}
```

### Release-pick formula

Per candidate, score each release. Pick the highest:

```
release_score
  = 0.30 * quality_match     # 1.0 if matches target quality (1080p WEB-DL/Bluray default), 0.5 if 720p, 0.2 if SD/CAM
  + 0.20 * codec_efficiency  # 1.0 HEVC/x265, 0.7 H.264, 0.3 MPEG-2
  + 0.20 * age_freshness     # newer publish_date scores higher; older releases are more likely DMCA'd
  + 0.15 * indexer_trust     # per-indexer trust score (configured in config.yaml; default 0.7 across the board)
  + 0.10 * size_sanity       # penalize wildly-too-small (CAMs) and wildly-too-big (uncompressed BR rips) for the runtime
  + 0.05 * group_repute      # tiny bonus for known-good release groups (configurable allow-list)
```

Hard rejects (don't score, just skip):

- Anything labeled `CAM`, `TS`, `TC`, `WORKPRINT`, `R5`, or with
  resolution `< 720p` if user set quality floor at HD.
- Anything with size < 200 MB for a movie, < 50 MB per episode for TV
  (almost certainly broken or sample).
- Anything from an indexer the user blocklisted in
  `config.yaml.acquisition.prowlarr.blocked_indexers`.

If no release scores above 0.4, mark the candidate as
`no_acceptable_release` and drop it. Don't ship trash to make the
quota.

### Quality preference

Read from `config.yaml.acquisition.quality_target`:

```yaml
acquisition:
  quality_target: 1080p
  prefer_codec: hevc           # hevc, h264, any
  prefer_source: web-dl        # web-dl, bluray, any
  size_ceilings:
    movie_1080p: 8.0           # GB
    tv_episode_1080p: 2.5      # GB
```

If unset, default to 1080p / hevc / web-dl with the size ceilings shown.

## What this skill returns

To the librarian agent (in-memory, not on disk):

```
[
  {
    candidate: { tmdb_id, title, year, ... },
    score: 0.91,
    score_breakdown: { tilt_match: 1.0, recent_loves: 0.85, ... },
    release: {
      title: "The.Burning.1981.1080p.BluRay.x265.10bit-GROUP",
      size_gb: 4.12,
      indexer: "DrunkenSlug",
      downloadUrl: "...",
      release_score: 0.87
    }
  },
  ...
]
```

The librarian agent then applies disk budget cuts, prepares the dry-run
table or hands off to `librarian-fetch` for queueing.

## Hard rules

- Never call NZBGet from this skill. Search and select only — fetch is
  the next skill's job.
- Never bypass `taste.md` "Never include." Hard stops are hard stops.
- Never inflate `tmdb_quality` to push a candidate past a hard filter.
- Never accept a release with no `downloadUrl` or with `protocol`
  outside `["usenet", "torrent"]`. NZBGet only handles usenet; if
  config has no torrent backend, drop torrent results entirely.
- Never log secrets — the run log records release titles + sizes, not
  download URLs (which carry indexer API keys).
