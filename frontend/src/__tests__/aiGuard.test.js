import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  aiReview,
  closeAiReview,
  openAiBatchReview,
  openAiReview,
} from '../model/aiGuard'

afterEach(closeAiReview)
describe('AI Music Guard review', () => {
  it('preserves the exact rejected audio version and request identity', () => {
    const detail = {
      assessment: { id: 'version', source_key: 'youtube:cover' },
      request_key: 'spotify:original',
    }
    openAiReview({ name: 'Original' }, detail)
    expect(aiReview.value.assessment.id).toBe('version')
    expect(aiReview.value.request_key).toBe('spotify:original')
  })
  it('retries only the selected blocked playlist track with its confirmation', () => {
    const items = [
      { song: { song_id: 'first' } },
      { song: { song_id: 'second' } },
    ]
    const retry = vi.fn()
    openAiBatchReview(items, retry, 1)
    aiReview.value.retry('opaque-confirmation')
    expect(retry).toHaveBeenCalledWith({
      song_id: 'second',
      ai_confirmation: 'opaque-confirmation',
    })
    expect(aiReview.value.batch).toBe(items)
    closeAiReview()
    expect(aiReview.value).toBe(null)
  })
})
