<template>
  <div class="mx-auto flex max-w-4xl flex-col gap-6 px-4 py-8 sm:px-6">
    <PageHeader
      :title="t('recognition.guard.title')"
      :subtitle="t('recognition.guard.disclaimer')"
    />
    <p v-if="error" class="text-sm text-danger" role="alert">{{ error }}</p>
    <section
      class="flex flex-col gap-4 rounded-panel border border-line-2 bg-surface p-5"
    >
      <p v-if="route.query.file" class="break-all text-sm">
        {{ route.query.file }}
      </p>
      <UiInput
        v-else
        v-model.trim="url"
        :label="t('recognition.guard.link')"
        placeholder="https://www.youtube.com/watch?v=…"
      />
      <UiButton
        :disabled="busy || (!url && !route.query.file)"
        @click="check"
        >{{ t('recognition.guard.check') }}</UiButton
      >
    </section>
    <section
      v-if="policy"
      class="flex flex-col gap-4 rounded-panel border border-line-2 bg-surface p-5"
    >
      <h2 class="text-lg font-semibold">
        {{ t('recognition.guard.settings') }}
      </h2>
      <label
        v-for="key in booleanKeys"
        :key="key"
        class="flex items-center gap-3 text-sm"
      >
        <input
          v-model="policy[key]"
          type="checkbox"
          :disabled="!auth.isAdmin.value || busy"
        />{{ t(`recognition.guard.${key}`) }}
      </label>
      <label class="flex flex-col gap-2 text-sm"
        >{{ t('recognition.guard.mode') }}
        <select
          v-model="policy.mode"
          :disabled="!auth.isAdmin.value || busy"
          class="rounded-control border border-line-2 bg-surface-2 p-3"
        >
          <option
            v-for="mode in ['gentle', 'balanced', 'strict']"
            :key="mode"
            :value="mode"
          >
            {{ t(`recognition.guard.${mode}`) }}
          </option>
        </select>
      </label>
      <UiButton v-if="auth.isAdmin.value" :disabled="busy" @click="save">{{
        t('recognition.guard.save')
      }}</UiButton>
    </section>
    <section
      class="flex flex-col gap-3 rounded-panel border border-line-2 bg-surface p-5"
    >
      <UiButton
        :disabled="busy || scanJob?.status === 'running'"
        @click="scan(false)"
        >{{ t('recognition.guard.scan') }}</UiButton
      >
      <UiButton
        v-if="scanJob?.status === 'interrupted'"
        :disabled="busy"
        @click="scan(true)"
        >{{ t('recognition.guard.resume') }}</UiButton
      >
      <p v-if="scanJob" class="text-sm" role="status">
        {{ scanJob.status }} ·
        {{
          t('recognition.guard.scanProgress', {
            count: scanJob.progress,
            total: scanJob.total || 0,
            errors: scanJob.errors,
          })
        }}
      </p>
      <button
        v-for="item in scanJob?.flagged || []"
        :key="item.file"
        class="break-all rounded-control border border-line-2 p-3 text-left text-sm"
        @click="
          openAiReview({ name: item.file }, { assessment: item.assessment })
        "
      >
        {{ item.file }} ·
        {{ t(`recognition.guard.status.${item.assessment.status}`) }}
      </button>
    </section>
    <section v-if="policy?.overrides?.length" class="flex flex-col gap-3">
      <h2 class="text-lg font-semibold">
        {{ t('recognition.guard.exceptions') }}
      </h2>
      <div
        v-for="item in policy.overrides"
        :key="`${item.user_id}|${item.scope}|${item.target}`"
        class="flex flex-wrap items-center gap-3 rounded-control border border-line-2 p-3 text-sm"
      >
        <span class="min-w-0 flex-1 break-all"
          >{{ item.scope }} · {{ item.target }} · {{ item.decision }}</span
        >
        <UiButton
          v-if="item.user_id !== 0 || auth.isAdmin.value"
          size="sm"
          :disabled="busy"
          @click="remove(item)"
          >{{ t('recognition.guard.remove') }}</UiButton
        >
      </div>
    </section>
  </div>
</template>
<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import PageHeader from '/src/components/library/PageHeader.vue'
import UiButton from '/src/components/ui/UiButton.vue'
import UiInput from '/src/components/ui/UiInput.vue'
import API from '/src/model/api'
import { openAiReview } from '/src/model/aiGuard'
import { useAuth } from '/src/model/auth'
import { useI18n } from '/src/i18n'
const { t } = useI18n()
const auth = useAuth()
const route = useRoute()
const policy = ref(null)
const busy = ref(false)
const error = ref('')
const url = ref('')
const scanJob = ref(null)
const booleanKeys = [
  'enabled',
  'hide_confirmed',
  'hide_suspected',
  'check_downloads',
  'block_ai_covers',
  'block_assisted',
]
let timer = null
let alive = true
function errorText(err) {
  return (
    err?.response?.data?.detail?.message ||
    err?.response?.data?.detail ||
    err?.message
  )
}
async function load() {
  policy.value = (await API.aiSettings()).data
}
async function run(operation) {
  busy.value = true
  error.value = ''
  try {
    await operation()
  } catch (err) {
    error.value = errorText(err)
  } finally {
    busy.value = false
  }
}
function check() {
  return run(async () => {
    const file = String(route.query.file || '')
    const song = file ? {} : { url: url.value }
    const { data } = await API.aiCheck({ file, song, recheck: true })
    if (alive) openAiReview(song, { assessment: data })
  })
}
function save() {
  return run(async () => {
    await API.setAiSettings(policy.value)
    await load()
  })
}
function remove(item) {
  return run(async () => {
    await API.aiFeedback({
      action: 'remove',
      scope: item.scope,
      target: item.target,
      global_rule: item.user_id === 0,
    })
    await load()
  })
}
async function poll(id) {
  try {
    const { data } = await API.aiScanStatus(id)
    if (!alive) return
    scanJob.value = data
    if (data.status === 'running') timer = setTimeout(() => poll(id), 1500)
  } catch (err) {
    if (alive) error.value = errorText(err)
  }
}
function scan(resume) {
  return run(async () => {
    clearTimeout(timer)
    const { data } = await API.aiScan(
      resume ? { job_id: scanJob.value.id } : {}
    )
    if (!alive) return
    scanJob.value = data
    try {
      sessionStorage.setItem('downtify-ai-scan', data.id)
    } catch {
      /* Optional recovery shortcut. */
    }
    poll(data.id)
  })
}
onMounted(() =>
  run(async () => {
    await load()
    if (route.query.file) await check()
    let id = ''
    try {
      id = sessionStorage.getItem('downtify-ai-scan') || ''
    } catch {
      /* Optional recovery shortcut. */
    }
    if (id) poll(id)
  })
)
onBeforeUnmount(() => {
  alive = false
  clearTimeout(timer)
})
</script>
