<template>
  <UiModal
    :open="Boolean(aiReview)"
    :title="t('recognition.guard.title')"
    :description="t('recognition.guard.disclaimer')"
    @close="closeAiReview"
  >
    <div v-if="aiReview" class="flex flex-col gap-4">
      <label v-if="aiReview.batch?.length" class="flex flex-col gap-2 text-sm">
        {{
          t('recognition.guard.skippedCount', { count: aiReview.batch.length })
        }}
        <select
          :value="aiReview.batchIndex"
          class="rounded-control border border-line-2 bg-surface-2 p-3"
          @change="
            openAiBatchReview(
              aiReview.batch,
              aiReview.retrySong,
              Number($event.target.value)
            )
          "
        >
          <option v-for="(item, i) in aiReview.batch" :key="i" :value="i">
            {{ item.song.name }} — {{ item.assessment.status }}
          </option>
        </select>
      </label>
      <p class="font-semibold">
        {{ aiReview.assessment.artist }} — {{ aiReview.assessment.title }}
      </p>
      <p class="text-sm">
        {{ t(`recognition.guard.status.${aiReview.assessment.status}`) }}
      </p>
      <p class="text-xs text-muted">
        {{ new Date(aiReview.assessment.checked_at * 1000).toLocaleString() }} ·
        {{ aiReview.assessment.rule_version }}
      </p>
      <ul class="flex flex-col gap-3">
        <li
          v-for="(evidence, i) in aiReview.assessment.evidence"
          :key="i"
          class="rounded-control border border-line-2 p-3 text-sm"
        >
          <p>{{ evidence.text }}</p>
          <a
            v-if="evidence.source_url"
            :href="evidence.source_url"
            target="_blank"
            rel="noopener noreferrer"
            class="text-accent"
            >{{ t('recognition.guard.source') }}</a
          >
        </li>
      </ul>
      <p v-if="!aiReview.assessment.evidence.length" class="text-sm text-muted">
        {{ t('recognition.guard.noEvidence') }}
      </p>
      <p v-if="error" class="text-sm text-danger" role="alert">{{ error }}</p>
      <div class="flex flex-wrap gap-2">
        <UiButton
          v-if="aiReview.retry"
          :disabled="busy"
          @click="act('allow_once')"
          >{{ t('recognition.guard.once') }}</UiButton
        >
        <UiButton :disabled="busy" @click="act('allow')">{{
          t('recognition.guard.allowTrack')
        }}</UiButton>
        <UiButton
          v-if="
            aiReview.assessment.artist &&
            aiReview.assessment.kind !== 'ai_cover'
          "
          :disabled="busy"
          variant="ghost"
          @click="act('allow', 'artist')"
          >{{ t('recognition.guard.allowArtist') }}</UiButton
        >
        <UiButton :disabled="busy" variant="danger" @click="act('block')">{{
          t('recognition.guard.blockTrack')
        }}</UiButton>
        <UiButton
          v-if="aiReview.assessment.artist"
          :disabled="busy"
          variant="ghost"
          @click="act('block', 'artist')"
          >{{ t('recognition.guard.blockArtist') }}</UiButton
        >
        <UiButton
          v-if="aiReview.assessment.channel"
          :disabled="busy"
          variant="ghost"
          @click="act('block', 'channel')"
          >{{ t('recognition.guard.blockChannel') }}</UiButton
        >
        <UiButton variant="ghost" @click="closeAiReview">{{
          t('common.close')
        }}</UiButton>
      </div>
    </div>
  </UiModal>
</template>
<script setup>
import { ref, watch } from 'vue'
import UiModal from '/src/components/ui/UiModal.vue'
import UiButton from '/src/components/ui/UiButton.vue'
import API from '/src/model/api'
import { aiReview, closeAiReview, openAiBatchReview } from '/src/model/aiGuard'
import { useI18n } from '/src/i18n'
const { t } = useI18n()
const busy = ref(false)
const error = ref('')
watch(aiReview, () => {
  error.value = ''
})
async function act(action, scope = 'track') {
  const current = aiReview.value
  busy.value = true
  error.value = ''
  try {
    const { data } = await API.aiFeedback({
      assessment_id: current.assessment.id,
      action,
      scope,
      request_key: current.request_key || current.assessment.source_key,
    })
    if (aiReview.value !== current) return
    closeAiReview()
    if (action === 'allow_once' && current.retry)
      current.retry(data.confirmation)
  } catch (err) {
    error.value = err?.response?.data?.detail || err?.message
  } finally {
    busy.value = false
  }
}
</script>
