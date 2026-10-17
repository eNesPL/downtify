import { normalizeTrack } from '/src/lib/library'

const VIDEO_ID = /^[A-Za-z0-9_-]{11}$/

function streamTrack(item, previewId) {
  const videoId = String(item.video_id || '')
  if (!VIDEO_ID.test(videoId) && (!item.artist || !item.title)) return null
  return {
    file: '',
    stream: true,
    youtube_id: VIDEO_ID.test(videoId) ? videoId : '',
    video_id: VIDEO_ID.test(videoId) ? videoId : '',
    title: item.title || item.url || videoId,
    artist: item.artist || '',
    url: '',
    recognitionPreviewId: previewId,
  }
}

// Source playback must use the exact file/video, never a title search or
// a library lookup that might substitute another version of the song.
export function recognitionSourceTrack(job) {
  const source = job?.source
  if (!source) return null
  const previewId = `recognition:${job.id}:source`
  if (source.file) {
    return {
      ...normalizeTrack(source.file),
      title: source.title || source.file,
      recognitionPreviewId: previewId,
    }
  }
  if (!VIDEO_ID.test(String(source.video_id || ''))) return null
  return streamTrack(source, previewId)
}

// Even when an AMV has approved artist/title tags, audition the candidate
// from YouTube rather than playing that AMV again via a library match.
export function recognitionCandidateTrack(jobId, item, key) {
  if (!item) return null
  return streamTrack(item, `recognition:${jobId}:candidate:${key}`)
}
