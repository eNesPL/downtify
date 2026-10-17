---
title: AMV song recognition
icon: lucide/audio-lines
---

# AMV song recognition

Keep the AMV you enjoy while using the actual musician and song for discovery.
Open **Similar → Recognize AMV song**, paste one YouTube/YouTube Music video
link, or choose **Recognize AMV song** from a library track's menu.

1. **Find original song** reads the video's music metadata and description,
   then searches YouTube Music for candidates. The uploader is never treated
   as evidence of the musician. Search suggestions are explicitly marked as
   text matches, not audio identifications.
2. Review the candidates. Select one, or enter the correct title and artist
   yourself. Nothing is approved automatically, including audio matches.
3. **Save song identity** stores a per-account source → song association in
   the existing library database. It leaves the source audio, tags, path,
   playlists and likes intact. **Reject / remove identity** removes that
   job's approval. A failed or rejected re-analysis keeps an older approval.
4. **Find similar** uses the approved artist and song with the existing
   YouTube Music radio. Results retain the normal playback and download
   controls. Library track menus, player controls and source-aware YouTube
   result buttons also use an existing approval.

The source title, channel and canonical YouTube URL remain available alongside
its recognized identity. Local files with no known YouTube source are processed
as local files; the application does not invent an AMV URL.

## Optional audio recognition

No new Python dependencies are needed. Configure providers on the **server**:

| Variable | Purpose |
|---|---|
| `DOWNTIFY_AUDD_TOKEN` | AudD token, with FFmpeg available. Up to three 12-second mono samples (around 30, 90 and 150 seconds) are sent per job. Samples in very short songs are deduplicated. |
| `DOWNTIFY_ACOUSTID_KEY` | AcoustID application key. Requires the `fpcalc` executable from Chromaprint. Sends the fingerprint and duration of a local file; it does not upload the file. |

For example, pass the token through a Docker Compose environment variable or a
Kubernetes Secret. Restart the server after changing it. Tokens are never
returned by the capability endpoint or stored in recognition jobs.

```yaml
environment:
  DOWNTIFY_AUDD_TOKEN: ${DOWNTIFY_AUDD_TOKEN}
  DOWNTIFY_ACOUSTID_KEY: ${DOWNTIFY_ACOUSTID_KEY:-}
```

The **Use external audio recognition** checkbox is off by default. Enabling it
explicitly permits the configured providers for that job or library batch.
Provider quotas, fees and usage terms apply. Without a provider the metadata
search and manual correction still work. YouTube reads reuse the server's
streaming cookies and anti-bot options. Audio samples and signed media URLs
are temporary and are not written to the job database.

AcoustID works best with recognizable recordings; it is not a universal
fragment recognizer. Dialogue, mashups, pitch shifts and remixes can defeat
both providers. Their results require review. Scores are not presented as
calibrated confidence percentages. A mashup can yield several candidates;
this version saves one selected song identity per source and account.

## Existing library and restart recovery

**Scan next library batch** queues up to ten files not analyzed by your account.
The recent list shows each job's state and results. Press it again after the
batch completes to continue. Failed or interrupted jobs are eligible for retry;
approvals and reviewed jobs are not silently processed again. This provides a
checkpoint without an unbounded whole-library queue.

Two recognition jobs run at a time. Each account may queue ten active jobs
and start thirty jobs per five minutes; the server allows fifty active jobs
in total. Duplicate requests reuse the existing job unless explicitly retried.
The recent list shows up to one hundred jobs, with older non-approved cache
entries pruned. Approved identities are retained. A server restart marks
unfinished jobs **Interrupted**; retry them explicitly. A changed local file
must be analyzed again before applying a stale recognition.

Accounts can read, approve and reject only their own jobs, and local sources
must still be visible in their library. Paired clients may use the recognition
API. The native Android app has not acquired a recognition screen in this
change; use the responsive web interface, or integrate the API below.

## Optional, reversible file tag correction

After approving a **local library file**, an administrator can choose
**Apply title and artist to file tags**. This changes the shared file's title,
artist and album artist for every user; album, artwork, lyrics and audio stay
intact. Library discovery then sees the corrected file tags as usual.

The server first backs up the complete original under
`DATABASE_DIR/recognition-backups/`, writes and verifies a staging copy, then
atomically replaces the file at the same path. **Undo file tag correction**
restores the exact original bytes. It refuses to overwrite a later file edit
or audio replacement. Undo a tag correction before editing or rejecting that
job's identity. Backups can be as large as the original audio and should be
included with the data directory in your backups.

## API

All routes use the existing authentication and same-origin policy. The server
adds these endpoints without changing the existing `/api/similar/tracks` API.

| Method | Endpoint | Input / result |
|---|---|---|
| GET | `/api/music/recognition/capabilities` | `{metadata, audd, acoustid}` booleans; no secrets |
| POST | `/api/music/recognize` | Exactly one of `{url}` or `{file}`, optional `use_audio` and `retry` booleans; HTTP 202 with job |
| GET | `/api/music/recognition` | Your recent visible jobs |
| GET | `/api/music/recognition/{id}` | One job and candidates |
| PATCH | `/api/music/recognition/{id}` | `{action: "accept", candidate_index}` and/or `title`, `artist`; or `{action: "reject"}` |
| POST | `/api/music/library/scan` | `{use_audio, retry_failed}`; HTTP 202 with `{jobs, remaining}` |
| GET | `/api/music/identity?file=...` or `?url=...` | `{recognition: job-or-null}` |
| GET | `/api/music/similar` | `file` or `url`, fallback `artist`, `track`, optional `limit`; existing similar-track result plus `recognition` |
| POST | `/api/music/recognition/{id}/tags` | Admin only: `{action: "apply"}` or `{action: "undo"}` |

A job contains `id`, `status`, `source`, `candidates`, `warnings`, timestamps,
and `selected` after approval. Candidate methods include `youtube-metadata`,
`text-search`, `audd`, `acoustid` and `manual`. A manual edit discards an
incompatible candidate video id. Jobs can be `queued`, `running`, `review`,
`no_candidates`, `accepted`, `rejected`, `superseded`, `interrupted` or `error`.

Recognition and discovery calls are separate from playback, so opening a song
never waits for the recognition queue. Missing providers or provider failures
produce warnings while keeping manual correction available.
