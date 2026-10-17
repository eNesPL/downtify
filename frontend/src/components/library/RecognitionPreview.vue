<template>
  <div class="flex flex-col gap-2">
    <div class="flex flex-wrap items-center gap-2">
      <UiButton
        size="sm"
        variant="ghost"
        :icon="playing ? 'pause' : 'play'"
        :disabled="!track"
        :aria-pressed="playing"
        @click="toggle"
        >{{ playing ? t('recognition.pausePreview') : label }}</UiButton
      >
      <span v-if="current && loading" class="text-xs text-muted" role="status">
        {{ t('recognition.loadingPreview') }}
      </span>
      <a
        v-if="track?.youtube_id"
        :href="`https://www.youtube.com/watch?v=${track.youtube_id}`"
        target="_blank"
        rel="noopener noreferrer"
        class="text-xs text-accent"
        >{{ t('recognition.openRecording') }}</a
      >
    </div>
    <template v-if="current">
      <label
        v-if="player.duration.value > 0"
        class="flex items-center gap-2 text-xs text-muted"
      >
        <span>{{ formatDuration(player.currentTime.value) }}</span>
        <input
          type="range"
          class="min-w-0 flex-1 accent-accent"
          min="0"
          :max="player.duration.value"
          step="1"
          :value="player.currentTime.value"
          :aria-label="t('recognition.seekPreview')"
          @input="player.seek(Number($event.target.value))"
        />
        <span>{{ formatDuration(player.duration.value) }}</span>
      </label>
      <p v-if="player.playError.value" class="text-sm text-danger" role="alert">
        {{ t('recognition.previewFailed') }}
      </p>
    </template>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import UiButton from '/src/components/ui/UiButton.vue'
import { formatDuration } from '/src/lib/format'
import { usePlayer } from '/src/model/player'
import { useI18n } from '/src/i18n'

const props = defineProps({
  track: { type: Object, default: null },
  label: { type: String, required: true },
})
const { t } = useI18n()
const player = usePlayer()
const current = computed(() =>
  Boolean(
    props.track &&
    player.currentTrack.value?.recognitionPreviewId ===
      props.track.recognitionPreviewId
  )
)
const playing = computed(() => current.value && player.isPlaying.value)
const loading = computed(
  () => player.isBuffering.value || Boolean(player.resolving.value)
)

function toggle() {
  if (!props.track) return
  if (current.value && !player.playError.value) {
    player.toggle()
  } else {
    player.pause()
    player.playList([{ ...props.track }], {
      context: { type: 'recognition', title: t('recognition.compareTitle') },
    })
  }
}
</script>
