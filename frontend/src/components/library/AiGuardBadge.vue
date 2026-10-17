<template>
  <button
    v-if="song.ai_guard"
    type="button"
    class="max-w-40 truncate rounded-control border border-line-2 px-2 py-1 text-xs text-muted"
    :title="t('recognition.guard.disclaimer')"
    @click.stop="openAiReview(song, { assessment: song.ai_guard })"
  >
    {{ t(`recognition.guard.status.${song.ai_guard.status}`) }}
  </button>
</template>
<script setup>
import { openAiReview } from '/src/model/aiGuard'
import { useI18n } from '/src/i18n'
const { t } = useI18n()
defineProps({ song: { type: Object, required: true } })
</script>
