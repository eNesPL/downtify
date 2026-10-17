import { describe, expect, it, vi } from 'vitest'
import {
  recognitionCandidateTrack,
  recognitionSourceTrack,
} from '../model/recognitionPreview'

const { search } = vi.hoisted(() => ({ search: vi.fn() }))
vi.mock('/src/model/api', () => ({ default: { search } }))
import { resolveTrackUrl } from '../model/streamResolve'

describe('recognition comparison playback', () => {
  it('plays the exact local source even when its tags match a candidate', () => {
    const track = recognitionSourceTrack({
      id: 'job',
      source: {
        file: 'AMVs/Artist - Song.mp3',
        title: 'Song',
        video_id: 'source00001',
      },
    })
    expect(track.file).toBe('AMVs/Artist - Song.mp3')
    expect(track.url).toContain('AMVs/Artist%20-%20Song.mp3')
    expect(track.stream).toBeUndefined()
  })

  it('pins the AMV and candidate to different exact videos', async () => {
    search.mockReset()
    const source = recognitionSourceTrack({
      id: 'job',
      source: {
        video_id: 'source00001',
        title: 'AMV',
      },
    })
    const candidate = recognitionCandidateTrack(
      'job',
      {
        video_id: 'original001',
        title: 'Song',
        artist: 'Artist',
      },
      0
    )
    expect((await resolveTrackUrl(source)).videoId).toBe('source00001')
    expect((await resolveTrackUrl(candidate)).videoId).toBe('original001')
    expect(search).not.toHaveBeenCalled()
    expect(candidate.file).toBe('')
    expect(candidate.recognitionPreviewId).not.toBe(source.recognitionPreviewId)
  })

  it('can audition fingerprint and manually entered identities without video IDs', async () => {
    search.mockReset()
    search.mockResolvedValue({ data: [{ song_id: 'original001' }] })
    const track = recognitionCandidateTrack(
      'job',
      {
        title: 'Song',
        artist: 'Artist',
        method: 'acoustid',
      },
      'manual'
    )
    expect((await resolveTrackUrl(track)).videoId).toBe('original001')
    expect(search).toHaveBeenCalledWith('Artist Song')
  })

  it('does not replace a missing source video with a text search', () => {
    expect(
      recognitionSourceTrack({
        id: 'job',
        source: {
          title: 'AMV',
          artist: 'Uploader',
        },
      })
    ).toBeNull()
    expect(recognitionSourceTrack(null)).toBeNull()
    expect(recognitionCandidateTrack('job', {}, 0)).toBeNull()
  })
})
