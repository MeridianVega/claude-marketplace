---
name: librarian-fetch
description: NZBGet integration for the librarian agent. Submits picked releases to NZBGet's JSON-RPC append endpoint, enforces disk budget + global free-space floor before each submission, writes a per-run audit log, and exposes a queue-status read-only mode. Loads only when approval=auto on the librarian's invocation; never loads in dry-run mode (search-only runs don't need fetch capability).
disable-model-invocation: false
user-invocable: false
---

# Fetch — submit releases to NZBGet

This is the *act* half of the librarian's job. Inputs come from
`librarian-search` already scored and trimmed to the disk budget. This
skill submits each release to NZBGet, records the receipts, and bows out.

NZBGet runs the rest: download, par-repair, unrar, post-processing. When
a job finishes it moves the file into the Jellyfin library path; Jellyfin
auto-scans on filesystem changes; the next channel refresh sees the new
content. The librarian doesn't poll the queue — the user (or another
agent) re-invokes the librarian with `mode: queue-status` if they want
a snapshot.

## Preconditions (re-checked here, on top of the agent's preconditions)

1. **NZBGet credentials.** `NZBGET_USER` and `NZBGET_PASS` env vars; the
   base URL is `config.yaml.acquisition.nzbget.base_url` (default
   `http://localhost:16789`).
2. **Disk floor.** Re-probe `df -B1 ${MEDIA_ROOT}` *immediately* before
   each `appendurl` call. If submission would push free space below the
   floor (worst-case: assume max(release.size, 1 GB) hits the disk),
   abort the rest of the batch and return what was queued so far.
3. **Category mapping configured.** `config.yaml.acquisition.nzbget.categories`
   maps `movie` → NZBGet category name, `tv` → category name. NZBGet's
   per-category post-processing (DestDir, Unrar, etc.) does the
   import. If the mapping is missing, abort — submitting without a
   category sends files to a DestDir that nothing watches.

## NZBGet JSON-RPC: `append`

NZBGet exposes JSON-RPC at `${BASE_URL}/jsonrpc`. Auth is HTTP Basic.

```bash
curl -s --user "${NZBGET_USER}:${NZBGET_PASS}" \
  -H "Content-Type: application/json" \
  -d @- \
  "${NZBGET_BASE_URL}/jsonrpc" <<'JSON'
{
  "method": "append",
  "params": [
    "The.Burning.1981.1080p.BluRay.x265.10bit-GROUP",
    "<base64-encoded NZB content>",
    "movies",
    0,
    false,
    false,
    "",
    0,
    "SCORE",
    [
      ["*Unpack:", "yes"],
      ["Category", "movies"]
    ]
  ]
}
JSON
```

Positional arguments (per NZBGet docs at
<https://nzbget.com/documentation/api/methods/append/>):

1. `NZBFilename` — display name (use the release title).
2. `NZBContent` — base64 of the .nzb body. Fetch first via the
   `downloadUrl` from the Prowlarr search result, then base64-encode.
   Some indexers return raw .nzb XML directly; others 302 to a CDN —
   follow redirects (`curl -L`).
3. `Category` — one of the categories configured in NZBGet
   (`movies`, `tv`, etc., as mapped in `config.yaml`).
4. `Priority` — 0 (normal). The librarian doesn't reorder the queue.
5. `AddToTop` — `false`.
6. `AddPaused` — `false`.
7. `DupeKey` — `""` (NZBGet's own dupe detection works fine for this
   use case; the librarian's dedupe already happened against Jellyfin).
8. `DupeScore` — 0.
9. `DupeMode` — `"SCORE"`.
10. `PPParameters` — array of `[name, value]` post-processing param
    overrides. Use this to ensure unrar runs and the Category lands
    even if the global setting differs.

Returns: an integer NZBID on success, error object on failure. Record
the NZBID + the release info in the run log.

## Submission loop

```python
# Pseudocode showing the order of operations.
for pick in picks_in_score_descending_order:
    # Re-check disk floor before each submission.
    free_gb = df_free_gb(media_root)
    needed = max(pick.release.size_gb, 1.0)
    if free_gb - needed < disk_floor_gb:
        abort_remaining(reason=f"floor would drop to {free_gb - needed:.1f} GB; floor is {disk_floor_gb} GB")
        break

    # Fetch the .nzb body.
    nzb_bytes = http_get_follow_redirects(pick.release.downloadUrl)
    if not looks_like_nzb(nzb_bytes):
        log_skip(pick, reason="downloadUrl returned non-NZB content")
        continue

    nzbid = nzbget_append(
        title=pick.release.title,
        body_b64=base64.b64encode(nzb_bytes),
        category=category_for(pick.candidate.media_kind),
    )
    record_submission(pick, nzbid)
```

Run-time per submission is small (~1 s for the JSON-RPC + NZB fetch);
the librarian doesn't need to parallelize. Sequential is more
predictable for disk-floor accounting.

## Run log

Append a section to the run log file the librarian agent created
during search:

```markdown
## Queued

| # | Title | Size | Category | NZBID | Indexer |
| -: | :--- | -: | :--- | -: | :--- |
| 1 | The.Burning.1981.1080p.BluRay.x265.10bit-GROUP | 4.1 GB | movies | 14837 | DrunkenSlug |
| 2 | Halloween.III.Season.of.the.Witch.1982.1080p.WEB-DL.DDP5.1.H.264-GROUP | 4.4 GB | movies | 14838 | NZBgeek |
| … |

## Disk after submission
- Free before: 412.0 GB
- Total queued: 58.2 GB
- Estimated free after all complete: 353.8 GB (floor 200 GB, headroom 153.8 GB)

## Skipped during fetch
- Halloween II (1981) — downloadUrl returned non-NZB content (likely indexer
  rate-limit; surfaced for next run).
```

Run log path:
`~/Library/Application Support/ersatztv-programmer/librarian-runs/{ISO8601}.md`

## Queue-status mode

When the librarian is invoked with `mode: queue-status`, this skill
provides the read-only side:

```bash
curl -s --user "${NZBGET_USER}:${NZBGET_PASS}" \
  -H "Content-Type: application/json" \
  -d '{"method":"listgroups","params":[0]}' \
  "${NZBGET_BASE_URL}/jsonrpc"
```

Returns the active queue. Cross-reference each NZBID against the most
recent run-log files (read newest-first, stop after 30 days) so the
librarian can attribute queue items back to a librarian run.

For history (completed + failed jobs):

```bash
curl -s --user "${NZBGET_USER}:${NZBGET_PASS}" \
  -H "Content-Type: application/json" \
  -d '{"method":"history","params":[false]}' \
  "${NZBGET_BASE_URL}/jsonrpc"
```

Compose a compact status block like the example in the librarian
agent's docs and return — no further action.

## Failure modes you must handle

- **NZBGet down** — `append` connection refused. Don't retry within
  the same run; abort and return the queue-state-as-of-now to the
  caller. The user re-invokes when NZBGet is back.
- **Indexer rate-limit** — `downloadUrl` returns HTML or 429. Skip
  the pick, log it, continue with the next.
- **NZB body looks wrong** — first bytes aren't `<?xml` and aren't
  gzip-magic. Skip; log.
- **NZBGet `appendresult.code != 0`** — record the error message in
  the run log and skip; don't escalate to the caller unless every
  submission fails (then abort).
- **Category not configured in NZBGet** — `append` succeeds but the
  file lands in a default DestDir Jellyfin doesn't watch. Pre-flight:
  `listgroups` includes a `categories` field at the top; verify the
  configured category names exist before the first submission.
- **Disk floor crossed mid-batch** — abort the remainder. Already-queued
  jobs can complete; NZBGet has its own pause-on-low-disk if configured.

## Hard rules

- **Never submit without `approval: auto`.** The librarian agent gates
  this skill's load on `approval`; never override.
- **Never re-submit a release that's already in NZBGet's queue or
  history.** Pre-flight `listgroups` + `history`, drop dupes by
  `NZBFilename` exact match. (NZBGet's own DupeMode catches most;
  this is belt + suspenders.)
- **Never write the .nzb body to the run log.** Title and ID only —
  the body can carry indexer-specific tracking and shouldn't be in a
  text file the user might commit to git.
- **Never delete completed history.** The librarian's queue-status
  mode reads it; user-initiated cleanup happens through NZBGet's UI.
- **Never bypass the disk-floor pre-check.** That floor is the live
  transcoding cache's headroom. Crossing it means ETV Next stutters
  during peak channel viewership.
