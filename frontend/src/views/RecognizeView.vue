<template>
  <div class="mx-auto flex max-w-4xl flex-col gap-6 px-4 py-8 sm:px-6">
    <PageHeader
      :title="t('recognition.title')"
      :subtitle="t('recognition.body')"
    />
    <section
      class="flex flex-col gap-4 rounded-panel border border-line-2 bg-surface p-5"
    >
      <p v-if="sourceFile" class="break-all text-sm">{{ sourceFile }}</p>
      <UiButton
        v-if="sourceFile"
        variant="ghost"
        @click="router.push({ name: 'Recognize' })"
        >{{ t('recognition.useLink') }}</UiButton
      >
      <form class="flex flex-col gap-4" @submit.prevent="start(false)">
        <UiInput
          v-if="!sourceFile"
          v-model.trim="url"
          :label="t('recognition.link')"
          placeholder="https://www.youtube.com/watch?v=…"
        />
        <label class="flex items-start gap-3 text-sm text-muted">
          <input
            v-model="useAudio"
            type="checkbox"
            class="mt-1"
            :disabled="!audioAvailable"
          />
          <span
            >{{ t('recognition.audioConsent')
            }}<span v-if="!audioAvailable" class="block">{{
              t('recognition.noProvider')
            }}</span></span
          >
        </label>
        <div class="flex flex-wrap gap-2">
          <UiButton
            type="submit"
            icon="search"
            :loading="busy"
            :disabled="active || (!sourceFile && !url)"
            >{{ t('recognition.start') }}</UiButton
          >
          <UiButton
            v-if="job && !active"
            variant="ghost"
            :disabled="busy"
            @click="start(true)"
            >{{ t('common.retry') }}</UiButton
          >
        </div>
      </form>
      <p v-if="error" class="text-sm text-danger" role="alert">{{ error }}</p>
    </section>

    <section
      v-if="job"
      class="flex flex-col gap-4 rounded-panel border border-line-2 bg-surface p-5"
      aria-live="polite"
    >
      <div>
        <p class="text-sm text-muted">{{ t('recognition.source') }}</p>
        <h2 class="break-words text-lg font-semibold">
          {{ job.source.title || job.source.file || job.source.url }}
        </h2>
        <p v-if="job.source.channel" class="text-sm text-muted">
          {{ t('recognition.uploader') }}: {{ job.source.channel }}
        </p>
        <a
          v-if="job.source.url"
          :href="job.source.url"
          target="_blank"
          rel="noopener noreferrer"
          class="text-sm text-accent"
          >{{ t('recognition.openSource') }}</a
        >
      </div>
      <div class="flex flex-col gap-2 rounded-control border border-line-2 p-3">
        <h3 class="text-sm font-semibold">
          {{ t('recognition.compareTitle') }}
        </h3>
        <p class="text-xs text-muted">{{ t('recognition.compareHint') }}</p>
        <RecognitionPreview
          :track="sourcePreview"
          :label="t('recognition.listenSource')"
        />
      </div>
      <p class="text-sm font-semibold">
        {{ t(`recognition.status.${job.status}`) }}
      </p>
      <UiProgress v-if="active" indeterminate />
      <p
        v-for="warning in job.warnings"
        :key="warning"
        class="text-sm text-muted"
      >
        {{ warning }}
      </p>
      <template v-if="!active">
        <p class="text-sm text-muted">{{ t('recognition.reviewHint') }}</p>
        <ul class="flex flex-col gap-2">
          <li
            v-for="(item, index) in job.candidates"
            :key="`${item.artist}|${item.title}`"
          >
            <button
              type="button"
              class="w-full rounded-control border p-3 text-left"
              :class="
                selected === index
                  ? 'border-accent bg-accent/8'
                  : 'border-line-2'
              "
              :aria-pressed="selected === index"
              @click="choose(index)"
            >
              <span class="block font-semibold"
                >{{ item.artist }} — {{ item.title }}</span
              >
              <span class="text-xs text-muted">{{
                (item.methods || [item.method]).map(methodLabel).join(' · ')
              }}</span>
            </button>
            <div class="px-3 py-2">
              <RecognitionPreview
                :track="candidatePreview(item, index)"
                :label="t('recognition.listenCandidate')"
              />
              <p v-if="!item.video_id" class="mt-1 text-xs text-muted">
                {{ t('recognition.searchPreviewHint') }}
              </p>
            </div>
          </li>
        </ul>
        <div class="grid gap-3 sm:grid-cols-2">
          <UiInput v-model.trim="artist" :label="t('similar.artist')" />
          <UiInput v-model.trim="title" :label="t('similar.track')" />
        </div>
        <RecognitionPreview
          v-if="artist && title && selected === null"
          :track="
            candidatePreview({ artist, title }, `manual:${artist}|${title}`)
          "
          :label="t('recognition.listenEntered')"
        />
        <div class="flex flex-wrap gap-2">
          <UiButton
            :disabled="busy || !artist || !title"
            @click="decide('accept')"
            >{{ t('recognition.accept') }}</UiButton
          >
          <UiButton
            variant="ghost"
            :disabled="busy"
            @click="decide('reject')"
            >{{ t('recognition.reject') }}</UiButton
          >
          <UiButton
            v-if="job.status === 'accepted'"
            icon="wand"
            @click="findSimilar"
            >{{ t('similar.findSimilar') }}</UiButton
          >
        </div>
        <div
          v-if="
            auth.isAdmin.value && job.status === 'accepted' && job.source.file
          "
          class="flex flex-col gap-3 border-t border-line-2 pt-4"
        >
          <p class="text-sm text-muted">{{ t('recognition.tagsHint') }}</p>
          <UiButton
            :disabled="busy"
            @click="editTags(job.tag_backup ? 'undo' : 'apply')"
            >{{
              t(
                job.tag_backup
                  ? 'recognition.undoTags'
                  : 'recognition.applyTags'
              )
            }}</UiButton
          >
        </div>
      </template>
    </section>

    <section
      class="flex flex-col gap-3 rounded-panel border border-line-2 bg-surface p-5"
    >
      <h2 class="text-lg font-semibold">{{ t('recognition.scanTitle') }}</h2>
      <p class="text-sm text-muted">{{ t('recognition.scanHint') }}</p>
      <UiButton :disabled="busy" @click="scanLibrary">{{
        t('recognition.scan')
      }}</UiButton>
      <p v-if="scanMessage" class="text-sm text-muted" role="status">
        {{ scanMessage }}
      </p>
    </section>

    <section v-if="recent.length" class="flex flex-col gap-3">
      <h2 class="text-lg font-semibold">{{ t('recognition.recent') }}</h2>
      <button
        v-for="item in recent.slice(0, 20)"
        :key="item.id"
        type="button"
        class="flex flex-col gap-1 rounded-control border border-line-2 p-3 text-left"
        @click="router.push({ name: 'Recognize', query: { job: item.id } })"
      >
        <span class="break-words text-sm">{{
          item.source.title || item.source.file || item.source.url
        }}</span>
        <span class="text-xs text-muted"
          >{{ t(`recognition.status.${item.status}`)
          }}<template v-if="item.selected">
            · {{ item.selected.artist }} — {{ item.selected.title }}</template
          ></span
        >
      </button>
    </section>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import PageHeader from '/src/components/library/PageHeader.vue'
import RecognitionPreview from '/src/components/library/RecognitionPreview.vue'
import {
  recognitionSourceTrack,
  recognitionCandidateTrack,
} from '/src/model/recognitionPreview'
import UiButton from '/src/components/ui/UiButton.vue'
import UiInput from '/src/components/ui/UiInput.vue'
import UiProgress from '/src/components/ui/UiProgress.vue'
import API from '/src/model/api'
import { useAuth } from '/src/model/auth'
import { useI18n } from '/src/i18n'

const { t } = useI18n()
const auth = useAuth()
const route = useRoute()
const router = useRouter()
const url = ref('')
const job = ref(null)
const artist = ref('')
const title = ref('')
const selected = ref(null)
const useAudio = ref(false)
const available = ref({})
const recent = ref([])
const busy = ref(false)
const error = ref('')
const scanMessage = ref('')
const sourceFile = computed(() =>
  String(route.query.file || job.value?.source.file || '')
)
const sourcePreview = computed(() => recognitionSourceTrack(job.value))
function candidatePreview(item, key) {
  return recognitionCandidateTrack(job.value?.id, item, key)
}
const active = computed(() => ['queued', 'running'].includes(job.value?.status))
const audioAvailable = computed(
  () => available.value.audd || (sourceFile.value && available.value.acoustid)
)
let timer = null
let generation = 0
let recentTimer = null
let refreshing = false
function message(err) {
  return err?.response?.data?.detail || err?.message || t('recognition.failed')
}
function stopPolling() {
  clearTimeout(timer)
  timer = null
}
function methodLabel(method) {
  return t(`recognition.methods.${method}`)
}
function choose(index) {
  selected.value = index
  artist.value = job.value.candidates[index].artist
  title.value = job.value.candidates[index].title
}
async function refreshRecent() {
  if (refreshing) return
  refreshing = true
  try {
    recent.value = (await API.recentRecognition()).data
  } catch {
    /* Opening a job still works. */
  } finally {
    refreshing = false
  }
}
function display(data) {
  job.value = data
  artist.value = data.selected?.artist || ''
  title.value = data.selected?.title || ''
  selected.value = null
}
async function poll(id, mine) {
  try {
    const { data } = await API.getRecognition(id)
    if (mine !== generation) return
    job.value = data
    if (['queued', 'running'].includes(data.status)) {
      timer = setTimeout(() => poll(id, mine), 1500)
    } else {
      display(data)
      refreshRecent()
    }
  } catch (err) {
    if (mine === generation) error.value = message(err)
  }
}
async function start(retry) {
  const mine = ++generation
  stopPolling()
  busy.value = true
  error.value = ''
  try {
    const { data } = await API.recognizeMusic({
      file: sourceFile.value,
      url: sourceFile.value ? '' : url.value || job.value?.source.url || '',
      use_audio: Boolean(useAudio.value && audioAvailable.value),
      retry,
    })
    if (mine !== generation) return
    await router.replace({ name: 'Recognize', query: { job: data.id } })
  } catch (err) {
    if (mine === generation) error.value = message(err)
  } finally {
    busy.value = false
  }
}
async function decide(action) {
  const mine = generation
  busy.value = true
  error.value = ''
  try {
    const { data } = await API.decideRecognition(job.value.id, {
      action,
      artist: artist.value,
      title: title.value,
      candidate_index: selected.value,
    })
    if (mine !== generation) return
    display(data)
    refreshRecent()
  } catch (err) {
    if (mine === generation) error.value = message(err)
  } finally {
    busy.value = false
  }
}
async function scanLibrary() {
  busy.value = true
  error.value = ''
  try {
    const { data } = await API.scanRecognitionLibrary({
      use_audio: Boolean(useAudio.value && audioAvailable.value),
      retry_failed: true,
    })
    scanMessage.value = t('recognition.scanResult', {
      count: data.jobs.length,
      remaining: data.remaining,
    })
    await refreshRecent()
  } catch (err) {
    error.value = message(err)
  } finally {
    busy.value = false
  }
}
async function editTags(action) {
  const mine = generation
  busy.value = true
  error.value = ''
  try {
    const { data } = await API.editRecognitionTags(job.value.id, action)
    if (mine === generation) {
      display(data)
      refreshRecent()
    }
  } catch (err) {
    if (mine === generation) error.value = message(err)
  } finally {
    busy.value = false
  }
}
function findSimilar() {
  const source = job.value.source
  router.push({
    name: 'Similar',
    query: {
      artist: job.value.selected.artist,
      track: job.value.selected.title,
      file: source.file || '',
      url: source.file ? '' : source.url,
    },
  })
}
watch(
  () => [route.query.job, route.query.file, route.query.url],
  () => {
    const mine = ++generation
    stopPolling()
    error.value = ''
    job.value = null
    url.value = String(route.query.url || '')
    if (route.query.job) poll(String(route.query.job), mine)
  },
  { immediate: true }
)
onMounted(async () => {
  try {
    available.value = (await API.recognitionCapabilities()).data
  } catch (err) {
    error.value = message(err)
  }
  refreshRecent()
  recentTimer = setInterval(() => {
    if (
      recent.value.some((item) => ['queued', 'running'].includes(item.status))
    )
      refreshRecent()
  }, 2500)
})
onBeforeUnmount(() => {
  ++generation
  clearInterval(recentTimer)
  stopPolling()
})
</script>
