---
title: AI Music Guard
icon: lucide/shield-check
---

# AI Music Guard

AI Music Guard reduces accidental discovery and downloading of generated songs,
AI covers and cloned vocals. It uses source metadata, explicit uploader
declarations and your own exceptions. It is **not a universal AI detector**.
No evidence, a MusicBrainz identity or a recognized original does not prove that
a particular upload was made by a human.

Open **Similar → AI Music Guard**, or choose **AI Music Guard** from a library
track's menu. The server protects downloads from existing clients too; the
responsive web interface provides policy settings and review controls. This
change does not add screens to the native Android app or produce a new APK.

## Classification and evidence

| Status | Meaning |
|---|---|
| `confirmed_ai` | A source description declares generation, or a user explicitly blocked the recording/artist/channel. Inspect the evidence to distinguish the two. |
| `suspected_ai` | Metadata contains a hint such as Suno, Udio, AI cover or cloned vocals. Hints alone are not treated as proof. |
| `unknown` | There is insufficient source information or metadata could not be read. |
| `no_ai_evidence` | A source description was read and no current rule found AI evidence. This is not human-author certification. |
| `user_allowed` | An explicit account exception allows the recording, original artist or channel. |

Every assessment contains evidence excerpts, the source URL when known, a check
time, an expiry time, a rule version and an explanation. A title keyword is a
weak hint. Strong automated evidence requires a positive generation declaration
from the server-read upload description; client-supplied descriptions cannot
claim that provenance. Rules ignore simple negations such as “not AI generated”.
Quoted statements, unusual wording and misleading descriptions can still cause
mistakes. Review the evidence and report an error with an allow/block exception.
There are no fabricated detection percentages.

## Default policy

The administrator controls the server policy. Account exceptions stay private
unless an administrator deliberately creates a global rule through the API.

| Mode | Confirmed AI / manual block | Suspected AI | Unknown or no AI evidence |
|---|---|---|---|
| Gentle | Block download | Allow with annotation | Allow with annotation |
| Balanced (default) | Block download | Require a decision | Allow with annotation |
| Strict | Block download | Require a decision | Require a decision |

Confirmed AI is hidden from recommendation results by default. Hiding suspected
AI is a separate option. AI covers and cloned vocals are included by default;
AI assisted mastering is a separate preference, off by default. Disabling checks
for downloads does not automatically disable recommendation filtering, and
vice versa. Disable the main switch to turn off enforcement and filtering.

**Download this version once** issues a short-lived, user-bound confirmation for
one exact audio version and requested song. **Always allow this recording** and
**Trust original artist** are persistent exceptions. Trusting the original
artist does not automatically allow an AI cover. Block a recording, artist or
channel from its evidence dialog. Remove exceptions from the guard settings.

The review dialog for a playlist/CSV includes a selector for every skipped row,
so a decision for one song cannot accidentally approve the entire import.

## Download and recommendation enforcement

The server checks known assessments and local metadata before a queue job is
registered. Playlist and CSV items are evaluated independently; rejected items
are returned in `ai_guard_skipped`, while allowed items continue normally.
A newly discovered problem in the resolved source is also reported by its
queue error and, for a single URL download, HTTP 409 with structured evidence.

The downloader checks the **actual chosen YouTube video** before yt-dlp fetches
its audio. This also covers the direct audio-fetch path used by manual audio
replacement. The selected video's title, description and channel are assessed
separately from the requested original song. An approval from AMV recognition
is not an AI exemption, and a human original cannot authorize an AI cover by
itself. Resolved version associations are cached, so a known blocked version
can be rejected earlier on the next attempt. Sharing a file between accounts
does not grant another account an AI exception.

Existing file and skip-download paths still enforce available assessments.
For slskd and local files, only the metadata actually available is checked;
there is no claim of acoustic AI detection. Standalone instances of the Python
`Downloader` must explicitly attach the guard; the web server does so at library
startup, including Playlist Monitor's downloader.

The server annotates and filters Similar, AMV-based Similar, Discover artists,
Discover collections, chart tracks and song search results before returning
JSON. Metadata-only recommendation checks use cached evidence; they do not
make a new network request for every row. Unknown tracks may therefore remain
visible in balanced mode. The chosen YouTube source is checked again before
a download. If guard storage fails during discovery filtering, the response is
503 rather than an unchecked recommendation list.

Playback and previews do not wait for a recognition queue. This filter cannot
guarantee that every unlabelled AI upload is hidden or that metadata is honest.

## Cache, scanning, privacy and costs

All guard data is stored in the existing library database. Unknown assessments
expire after one day; other assessments after seven days. Explicit rechecking
can refresh metadata. Provider failures do not replace previously verified
positive evidence with an unknown result. Rule-version changes invalidate the
fresh cache. Live source metadata lookups are serialized and throttled to at
most two starts per second. A successful lookup can take longer than that.

Library scanning reads owned files' tags without uploading audio, deleting or
rewriting files. Jobs persist progress, totals and up to 500 flagged detail
links. The browser remembers the last scan id for the session; interrupted jobs
can resume from their saved position. Rerunning a scan reuses stable assessment
keys. A local file whose size changes gets a new file assessment. Local metadata
caches and scan results are scoped to the account; another account cannot open
them through an assessment id. Account exceptions, one-use grants and scan
results are checked server-side.

AI Music Guard requires no paid API key and sends no audio to an external
detector. Optional AMV audio recognition is a separate feature with its own
explicit consent and provider settings. No general audio AI model, official
artist-profile crawler or automatic historical channel classifier is included
in this metadata-based version.

## API extension

These additive routes are under `/api/v1/music/`. Existing authentication,
paired-device tokens and browser same-origin rules apply. A 404 on an older
server means the extension is unavailable.

| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/ai-check` | `{song, recheck?}` or `{file, recheck?}`; assess a song or visible local file |
| GET | `/ai-status/{assessment_id}` | Your previously seen assessment with current exceptions applied |
| POST | `/ai-feedback` | `{assessment_id, action, scope?, note?, request_key?, global_rule?}` |
| GET | `/ai-settings` | Server policy and account/global exceptions visible to you |
| PUT | `/ai-settings` | Administrator-only policy update |
| POST | `/library/ai-scan` | `{}` for a new scan or `{job_id}` to resume |
| GET | `/library/ai-scan/{job_id}` | Your scan's saved status, totals and flagged rows |

Feedback actions are `allow`, `block`, `remove` and `allow_once`; scopes are
`track`, `artist` and `channel`. Track exceptions use assessment ids so uploads,
remixes and covers remain separate. To remove an exception from the settings
list, use `{action: "remove", scope, target}`. Global rules require an admin.
`allow_once` returns an opaque `confirmation`, valid for fifteen minutes. Send
it as `ai_confirmation` with the retried download's song hints. It is bound to
the account, request and exact version; forged flags or another user's token
cannot bypass checks.

Blocked downloads return HTTP 409:

```json
{
  "detail": {
    "code": "ai_guard",
    "message": "AI Music Guard: suspected_ai — review required",
    "request_key": "catalog:requested-recording",
    "assessment": {
      "id": "assessment-id",
      "source_key": "youtube:actual-video",
      "audio_version_id": "youtube:actual-video",
      "status": "suspected_ai"
    }
  }
}
```

Actual responses include evidence, times, rule version and explanation.
Playlist/CSV submissions return an optional `ai_guard_skipped` array alongside
the existing job ids/count. `POST /api/download/album?guard_report=true` returns
`{downloads, ai_guard_skipped}`; its default response remains the legacy
song-id-to-filename map with blocked rows omitted. A client that needs per-track
album reasons should opt into the report. Queue errors retain late source
assessment details in `ai_guard`.
