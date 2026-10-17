import { shallowRef } from 'vue'

export const aiReview = shallowRef(null)
export function openAiReview(song, detail, retry = null) {
  aiReview.value = { song, ...detail, retry }
}
export function closeAiReview() {
  aiReview.value = null
}

export function openAiBatchReview(items, retrySong, index = 0) {
  const item = items[index]
  if (!item) return
  openAiReview(
    item.song,
    { ...item, batch: items, batchIndex: index, retrySong },
    (confirmation) => retrySong({ ...item.song, ai_confirmation: confirmation })
  )
}
