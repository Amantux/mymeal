<script setup>
import { ref, reactive, onMounted, onUnmounted, computed, watch, nextTick } from 'vue'
import { useJobRunner } from '../composables/useJobRunner'
import { api } from '../api'
import { useUI } from '../stores/ui'

const ui = useUI()
const loading = ref(true)
const saving = ref(false)
const providers = ref([])          // status list (name/available/active)
const models = ref([])             // model-picker options for the active provider
const modelsError = ref('')        // why the picker is empty, when it is
const loadingModels = ref(false)
const discovering = ref(false)

// Editable form. apiKey is write-only: we never receive the stored key, so the
// field starts blank and a blank value on save leaves the stored key untouched.
const form = reactive({ provider: '', baseUrl: '', model: '', apiKey: '' })
const apiKeySet = ref(false)

// --- Household food preferences (honoured by AI suggestions & the assistant) ---
const prefs = reactive({ diet: '', allergies: '', dislikes: '', notes: '' })
const prefsBusy = ref(false)

// --- Edibl (companion food-inventory app) connection ---
const edibl = reactive({ url: '', token: '' })
const ediblTokenSet = ref(false)
const ediblStatus = ref(null)        // {configured, reachable} after a test
const ediblBusy = ref(false)

const labels = { '': 'Disabled', claude: 'Claude (Anthropic)', openai: 'OpenAI', ollama: 'Ollama (local)', ollama_cloud: 'Ollama Cloud' }
const needsKey = computed(() => ['claude', 'openai', 'ollama_cloud'].includes(form.provider))
const needsHost = computed(() => form.provider === 'ollama')
// Ollama also accepts a key, but it's OPTIONAL (Ollama Cloud / a secured instance).
const allowsKey = computed(() => needsKey.value || form.provider === 'ollama')

// When the USER switches provider, clear the per-provider fields so a prior
// provider's host/model/key don't visually carry over (the backend stores
// per-provider, but the form shows one active view). `suppress` guards the
// hydration assignment in load() — Vue flushes watchers asynchronously, so a
// plain `loading` flag wouldn't cover it.
const suppress = ref(false)
// Providers whose models we can enumerate from the server (Ollama /api/tags,
// OpenAI /models). Claude has no list endpoint → free-text only.
const canList = computed(() => ['ollama', 'ollama_cloud', 'openai'].includes(form.provider))
watch(() => form.provider, () => {
  if (suppress.value) return
  form.baseUrl = ''
  form.model = ''
  form.apiKey = ''
  apiKeySet.value = false
  models.value = []
  scheduleListModels()   // selecting Ollama probes the default host right away
})
// Re-probe (debounced) as the host is typed, so the available models appear
// without hunting for a button — the whole point of the Ollama setup.
watch(() => form.baseUrl, scheduleListModels)

let _modelsTimer = null
function scheduleListModels() {
  clearTimeout(_modelsTimer)
  _modelsTimer = setTimeout(autoListModels, 500)
}
// Best-effort SILENT model probe for the auto-list path (no toasts); the manual
// "List models" button keeps loadModels() with its feedback.
async function autoListModels() {
  if (!canList.value) return
  loadingModels.value = true
  try {
    const res = await api.post('/ai/models', {
      provider: form.provider, baseUrl: form.baseUrl, apiKey: form.apiKey || undefined,
    })
    models.value = res.models || []
    // Silent means no toast, NOT no feedback: the picker shows why it is empty.
    // Previously any failure was swallowed here and an unreachable host looked
    // exactly like a provider with no models.
    modelsError.value = res.error || ''
  } catch (e) {
    modelsError.value = e.message || 'Could not reach the provider.'
  } finally { loadingModels.value = false }
}

async function load() {
  loading.value = true
  suppress.value = true            // don't let hydration trip the provider watcher
  try {
    const [s, p] = await Promise.all([api.get('/ai/settings'), api.get('/ai/providers')])
    form.provider = s.provider || ''
    form.baseUrl = s.baseUrl || ''
    form.model = s.model || ''
    form.apiKey = ''
    apiKeySet.value = !!s.apiKeySet
    providers.value = p.providers
    try {
      const e = await api.get('/edibl/config')
      edibl.url = e.url || ''
      edibl.token = ''
      ediblTokenSet.value = !!e.tokenSet
    } catch (err) { /* edibl endpoints optional */ }
    try { Object.assign(prefs, await api.get('/preferences')) } catch (err) { /* optional */ }
    await loadKeys()
    await loadMembers()
  } finally {
    loading.value = false
    await nextTick()               // let the watcher flush before re-arming it
    suppress.value = false
  }
}
onMounted(load)

// Household default for streaming chat replies (owner-editable here; each browser
// overrides it on the chat widget). Default is classic POST.
const chatStreamDefault = ref(false)
const chatSaving = ref(false)
async function loadChatDefault() {
  try { chatStreamDefault.value = !!(await api.get('/ai/chat-settings')).stream } catch (e) { /* keep default */ }
}
async function saveChatDefault(on) {
  chatSaving.value = true
  try {
    chatStreamDefault.value = !!(await api.put('/ai/chat-settings', { stream: on })).stream
    ui.toast('Saved chat default')
  } catch (e) { ui.toast(e.message || 'Could not save', 'error') } finally { chatSaving.value = false }
}
onMounted(loadChatDefault)

// Async-job AI preference: a provider+model default for background jobs, separate
// from chat. Blank provider = same as chat. A per-run choice still wins.
const jobAi = ref({ enrich: { provider: '', model: '' }, organize: { provider: '', model: '' } })
const jobAiSaving = ref(false)
const JOB_AREAS = [
  { k: 'enrich', l: 'Nutrition' },
  { k: 'organize', l: 'Background (auto-tag + collections)' },
]
async function loadJobAi() {
  try { jobAi.value = await api.get('/ai/job-settings') } catch (e) { /* keep defaults */ }
}
async function saveJobAi() {
  jobAiSaving.value = true
  try {
    jobAi.value = await api.put('/ai/job-settings', jobAi.value)
    ui.toast('Saved background-task AI')
  } catch (e) { ui.toast(e.message || 'Could not save', 'error') } finally { jobAiSaving.value = false }
}
onMounted(loadJobAi)

// --- Learned unit conversions -------------------------------------------------
// Every gram figure the app looked up on the web, with where it came from. A
// conversion nobody can inspect is a conversion nobody should trust.
const conversions = ref([])
const conversionsBusy = ref('')

async function loadConversions() {
  try { conversions.value = await api.get('/conversions') } catch (e) { /* optional */ }
}
onMounted(loadConversions)

async function confirmConversion(row) {
  conversionsBusy.value = row.id
  try {
    Object.assign(row, await api.put(`/conversions/${row.id}`, { status: 'confirmed' }))
    ui.toast(`Using ${row.gramsPerUnit} g per ${row.unit} of ${row.foodTerm}`)
  } catch (e) { ui.error(e.message) } finally { conversionsBusy.value = '' }
}

async function forgetConversion(row) {
  if (!confirm(`Forget "1 ${row.unit} of ${row.foodTerm} = ${row.gramsPerUnit} g"? `
    + 'Weights using it stop showing, and the next import that needs it looks it up again.')) return
  conversionsBusy.value = row.id
  try {
    await api.del(`/conversions/${row.id}`)
    conversions.value = conversions.value.filter((c) => c.id !== row.id)
  } catch (e) { ui.error(e.message) } finally { conversionsBusy.value = '' }
}


// --- Sync AI settings across the companion apps (Edibl / HomeHoard / myMeal) ---
// They deploy together, so this exports the AI config as a portable string you paste
// into each app's Settings. The API key is NEVER part of the string — it is set per
// app so a copied config can't leak a secret between browsers/apps.
const syncOut = ref('')     // the string we produced (shown for manual copy)
const syncIn = ref('')      // a string pasted from another app
const syncErr = ref('')
const syncBusy = ref(false)
// Opt-in: also embed the API key in the copied string (AICFG2). Its own dedicated
// field — the provider form's apiKey is write-only and cleared after save, so we
// can't read it back here. Default OFF; the key is a secret, so we warn and clear it.
const syncInclKey = ref(false)
const syncKey = ref('')

function b64encode(s) {
  const bytes = new TextEncoder().encode(s)
  let bin = ''
  for (const b of bytes) bin += String.fromCharCode(b)
  return btoa(bin)
}
function b64decode(s) {
  const bytes = Uint8Array.from(atob(s), (c) => c.charCodeAt(0))
  return new TextDecoder().decode(bytes)
}

function encodeAiSettings() {
  // App-agnostic field names so the same string applies in Edibl/HomeHoard/myMeal.
  const cfg = {
    v: 1,
    provider: form.provider || '',
    baseUrl: form.baseUrl || '',
    model: form.model || '',
    stream: !!chatStreamDefault.value,
    jobEnrich: { provider: jobAi.value.enrich?.provider || '', model: jobAi.value.enrich?.model || '' },
    jobOrganize: { provider: jobAi.value.organize?.provider || '', model: jobAi.value.organize?.model || '' },
  }
  // Only when the user opts in AND supplies a key does the string carry it — and
  // then it's tagged AICFG2 so a reader knows a secret may be present.
  const key = syncInclKey.value ? (syncKey.value || '').trim() : ''
  if (key) {
    cfg.apiKey = key
    return 'AICFG2:' + b64encode(JSON.stringify(cfg))
  }
  return 'AICFG1:' + b64encode(JSON.stringify(cfg))
}

async function copyAiSettings() {
  const str = encodeAiSettings()
  const withKey = str.startsWith('AICFG2:')
  try {
    await navigator.clipboard.writeText(str)
    // On success, never leave a key-bearing string rendered on screen; keyless
    // strings can stay visible so the user sees what was copied.
    syncOut.value = withKey ? '' : str
    ui.toast(withKey
      ? 'Copied WITH your API key — treat it like a password; only paste into your own apps'
      : 'Copied — paste into the other apps’ Settings (the API key is not included)')
  } catch (e) {
    // Clipboard blocked: show the string so the user can copy it manually.
    syncOut.value = str
    ui.toast('Select the text below and copy it', 'info')
  }
  syncKey.value = ''  // don't leave the secret sitting in the field
}

async function applyAiSettings() {
  syncErr.value = ''
  const raw = (syncIn.value || '').trim()
  if (!raw.startsWith('AICFG1:') && !raw.startsWith('AICFG2:')) {
    syncErr.value = 'Paste a settings string that starts with AICFG1: or AICFG2:'
    return
  }
  let cfg
  try { cfg = JSON.parse(b64decode(raw.slice(raw.indexOf(':') + 1))) } catch (e) { cfg = null }
  if (!cfg || typeof cfg !== 'object') {
    syncErr.value = 'That settings string is not valid — copy it again from the other app.'
    return
  }
  syncBusy.value = true
  try {
    // Provider settings. Only an AICFG2 string carries a key; when present we store
    // it, otherwise a blank apiKey is not sent and this app's stored key is untouched.
    const body = { provider: cfg.provider || '', baseUrl: cfg.baseUrl || '', model: cfg.model || '' }
    if (typeof cfg.apiKey === 'string' && cfg.apiKey.trim()) body.apiKey = cfg.apiKey.trim()
    await api.put('/ai/settings', body)
    await api.put('/ai/chat-settings', { stream: !!cfg.stream })
    await api.put('/ai/job-settings', {
      enrich: cfg.jobEnrich || { provider: '', model: '' },
      organize: cfg.jobOrganize || { provider: '', model: '' },
    })
    const keyApplied = typeof cfg.apiKey === 'string' && cfg.apiKey.trim()
    syncIn.value = ''
    await Promise.all([load(), loadChatDefault(), loadJobAi()])
    ui.toast(keyApplied
      ? 'AI settings and API key applied'
      : 'AI settings applied — add this app’s API key below if the provider needs one')
  } catch (e) {
    syncErr.value = e.message || 'Could not apply the settings.'
  } finally {
    syncBusy.value = false
  }
}

// Model picker for the background-task preference: probe the chosen provider
// (blank = the chat provider) for its models, so you can pick a small/local SLM
// instead of typing it. Uses that provider's saved config.
const jobModels = ref({ enrich: [], organize: [] })
const jobModelsLoading = ref({ enrich: false, organize: false })
async function listJobModels(area) {
  jobModelsLoading.value[area] = true
  try {
    const r = await api.post('/ai/models', { provider: jobAi.value[area].provider })
    jobModels.value[area] = r.models || []
    if (!jobModels.value[area].length) ui.toast('No models reported — type the model name', 'info')
  } catch (e) { ui.error(e.message || 'Could not list models') } finally { jobModelsLoading.value[area] = false }
}

// Bulk nutrition estimation — an async background job with progress.
const nutriJob = ref(null)
const nutriStarting = ref(false)
let nutriTimer = null
let nutriFails = 0
const nutriActive = computed(() =>
  nutriJob.value && ['pending', 'running'].includes(nutriJob.value.status))

async function startNutrition() {
  if (nutriStarting.value) return
  nutriStarting.value = true
  try { nutriJob.value = await api.post('/jobs/nutrition'); pollNutrition() }
  catch (e) { ui.error(e.message || 'Could not start.') }
  finally { nutriStarting.value = false }
}
async function pollNutrition() {
  if (!nutriJob.value) return
  const id = nutriJob.value.id
  try {
    nutriJob.value = await api.get(`/jobs/${id}`); nutriFails = 0
    if (nutriJob.value.status === 'done') {
      const r = nutriJob.value.result || {}
      ui.toast(`Estimated nutrition for ${r.estimated ?? 0} recipe(s).` +
        (r.remaining ? ` ${r.remaining} left — run again to continue.` : ''))
      return
    }
    if (nutriJob.value.status === 'error') { ui.error(nutriJob.value.error || 'Failed.'); return }
  } catch (e) {
    if (++nutriFails >= 5) { nutriJob.value = null; ui.error('Lost track of the job.'); return }
  }
  nutriTimer = setTimeout(pollNutrition, 1500)
}
async function resumeNutrition() {
  try {
    const r = await api.get('/jobs?kind=nutrition')
    const active = (r.items || []).find(j => ['pending', 'running'].includes(j.status))
    if (active) { nutriJob.value = active; pollNutrition() }
  } catch (e) { /* optional */ }
}
onMounted(resumeNutrition)
onUnmounted(() => clearTimeout(nutriTimer))

// AI organize: auto-tag recipes + propose collections (jobs).
const {
  job: catJob, starting: catStarting, active: catActive,
  start: startCategorize, resume: resumeCategorize, stop: stopCategorize,
} = useJobRunner('categorize', {
  onDone: (j) => j.status === 'error'
    ? ui.error(j.error || 'Tagging failed.')
    : ui.toast(`Tagging: ${j.result?.applied ?? 0} applied, ${j.result?.queued ?? 0} to review.`),
})
const {
  starting: cluStarting, active: cluActive,
  start: startCluster, resume: resumeCluster, stop: stopCluster,
} = useJobRunner('cluster', {
  onDone: (j) => j.status === 'error'
    ? ui.error(j.error || 'Grouping failed.')
    : ui.toast(`Found ${j.result?.proposed ?? 0} collection(s) to review.`),
})
const organizeForm = ref({ note: '', model: '' })
function organizeBody() {
  const b = {}
  if (organizeForm.value.note.trim()) b.note = organizeForm.value.note.trim()
  if (organizeForm.value.model.trim()) b.model = organizeForm.value.model.trim()
  return b
}
onMounted(() => { resumeCategorize(); resumeCluster() })
onUnmounted(() => { stopCategorize(); stopCluster() })

async function save() {
  saving.value = true
  try {
    const payload = { provider: form.provider, baseUrl: form.baseUrl, model: form.model }
    if (form.apiKey) payload.apiKey = form.apiKey   // only send when changing it
    await api.put('/ai/settings', payload)
    ui.toast('AI provider saved')
    await load()
  } catch (e) {
    ui.error(e.message || 'Could not save settings')
  } finally {
    saving.value = false
  }
}

async function savePrefs() {
  prefsBusy.value = true
  try {
    Object.assign(prefs, await api.put('/preferences', { ...prefs }))
    ui.toast('Preferences saved')
  } catch (e) {
    ui.error(e.message || 'Could not save preferences')
  } finally {
    prefsBusy.value = false
  }
}

async function loadModels() {
  loadingModels.value = true
  try {
    // Probe with the CURRENT form values without persisting — "List models" is
    // a read action and must not save the form as a side effect.
    const res = await api.post('/ai/models', {
      provider: form.provider, baseUrl: form.baseUrl, apiKey: form.apiKey || undefined,
    })
    models.value = res.models || []
    if (!models.value.length) ui.toast('No models reported by the provider', 'info')
  } catch (e) {
    ui.error(e.message || 'Could not list models')
  } finally {
    loadingModels.value = false
  }
}

async function clearKey() {
  saving.value = true
  try {
    await api.put('/ai/settings', { provider: form.provider, clearApiKey: true })
    form.apiKey = ''
    apiKeySet.value = false
    ui.toast('API key cleared')
  } catch (e) {
    ui.error(e.message || 'Could not clear key')
  } finally {
    saving.value = false
  }
}

// Decode an Edibl "connect link" (from Edibl → Access & keys) into url + token.
function pasteEdiblConnect(e) {
  const str = (e.target.value || '').trim()
  e.target.value = ''
  const m = /^([a-z]+)-connect:(.+)$/.exec(str)
  let obj = null
  try { if (m) obj = JSON.parse(decodeURIComponent(escape(atob(m[2])))) } catch (err) { obj = null }
  if (!obj || obj.app !== 'edibl') { ui.toast('That doesn’t look like an Edibl connect link', 'error'); return }
  edibl.url = obj.url || edibl.url
  edibl.token = obj.token || ''
  ui.toast('Filled from the connect link — Save to connect')
}

async function saveEdibl() {
  ediblBusy.value = true
  try {
    const payload = { url: edibl.url }
    if (edibl.token) payload.token = edibl.token
    await api.put('/edibl/config', payload)
    edibl.token = ''
    ui.toast('Edibl connection saved')
    await testEdibl()
  } catch (e) {
    ui.error(e.message || 'Could not save Edibl connection')
  } finally {
    ediblBusy.value = false
  }
}

async function findEdibl() {
  ediblBusy.value = true
  try {
    const res = await api.get('/edibl/discover')
    if (res.found) {
      edibl.url = res.url
      ui.toast(`Found Edibl at ${res.url}`)
    } else {
      // Surface WHY it failed, using the debug endpoint — the most common cause
      // is the add-on lacking the manager role to see sibling add-ons.
      let why = res.hint || 'No Edibl found'
      try {
        const dbg = await api.get('/edibl/discover/debug')
        if (dbg.supervisorAddonsQuery === 'denied-need-manager-role') {
          why = "Can't see other add-ons — the myMeal add-on needs the 'manager' " +
                'role. Update the add-on, or enter the Edibl URL manually.'
        } else if (dbg.supervisorAddonsQuery === 'no-supervisor-token') {
          why = 'Not running as a Home Assistant add-on — enter the Edibl URL manually.'
        }
      } catch (e) { /* debug is best-effort */ }
      ui.error(why)
    }
  } finally {
    ediblBusy.value = false
  }
}

async function testEdibl() {
  ediblBusy.value = true
  try {
    // Probe the typed URL WITHOUT persisting it — Test is a read action.
    const q = edibl.url ? `?url=${encodeURIComponent(edibl.url)}` : ''
    ediblStatus.value = await api.get(`/edibl/status${q}`)
  } catch (e) {
    ediblStatus.value = { configured: true, reachable: false, detail: e.message }
  } finally {
    ediblBusy.value = false
  }
}

async function clearEdiblToken() {
  ediblBusy.value = true
  try {
    const res = await api.put('/edibl/config', { clearToken: true })
    ediblTokenSet.value = !!res.tokenSet   // from server truth (env token may remain)
    ui.toast('Edibl token cleared')
  } catch (e) {
    ui.error(e.message || 'Could not clear token')
  } finally {
    ediblBusy.value = false
  }
}

// --- Household members (owner can promote HA users to admin) ---
const members = ref([])
const membersBusy = ref(false)

async function loadMembers() {
  try { members.value = await api.get('/users') } catch (e) { members.value = [] }
}

async function setAdmin(m, makeAdmin) {
  membersBusy.value = true
  try {
    await api.put(`/users/${m.id}/role`, { isOwner: makeAdmin })
    await loadMembers()
    ui.toast(makeAdmin ? `${m.name} is now an admin` : `${m.name} is no longer an admin`)
  } catch (e) {
    ui.error(e.status === 409
      ? "Can't remove the last admin."
      : (e.message || 'Could not change role'))
    await loadMembers()
  } finally {
    membersBusy.value = false
  }
}

// --- API keys (machine-client tokens: HACS integration, MCP, Edibl link) ---
const keys = ref([])
const newKeyName = ref('')
const newKeyScope = ref('full')      // full (REST+MCP) | rest | mcp | debug
const newKeyAccess = ref('write')    // write (read+mutate) | read (read-only)
const mintedKey = ref(null)          // raw token, shown once
const keysBusy = ref(false)

async function loadKeys() {
  try { keys.value = await api.get('/tokens') } catch (e) { keys.value = [] }
}

async function mintKey() {
  keysBusy.value = true
  try {
    const r = await api.post('/tokens', {
      name: newKeyName.value || 'Connected app', scope: newKeyScope.value,
      // A debug key only ever reads.
      access: newKeyScope.value === 'debug' ? 'read' : newKeyAccess.value })
    mintedKey.value = r.token
    newKeyName.value = ''
    newKeyScope.value = 'full'
    newKeyAccess.value = 'write'
    await loadKeys()
  } catch (e) {
    ui.error(e.message || 'Could not create key')
  } finally {
    keysBusy.value = false
  }
}

async function revokeKey(id) {
  if (!confirm('Revoke this API key? Anything using it loses access.')) return
  try { await api.del('/tokens/' + id); await loadKeys() }
  catch (e) { ui.error(e.message || 'Revoke failed') }
}

function copyKey(text) {
  navigator.clipboard?.writeText(text).then(() => ui.toast('Copied'), () => {})
}

async function findOllama() {
  discovering.value = true
  try {
    const res = await api.get('/ai/discover-ollama')
    if (res.found) {
      form.provider = 'ollama'
      form.baseUrl = res.host
      if (res.models && res.models.length) { models.value = res.models; form.model = form.model || res.models[0] }
      ui.toast(`Found Ollama at ${res.host}`)
    } else {
      ui.error(res.hint || 'No Ollama server found')
    }
  } finally {
    discovering.value = false
  }
}
</script>

<template>
  <div class="page-head"><h1>Settings</h1></div>

  <div class="card">
    <h2>AI provider</h2>
    <p class="muted">
      Configure the AI backend for recipe import, meal planning, and the cooking
      assistant. Changes here are remembered and override any Home Assistant
      add-on / environment default.
    </p>

    <div v-if="loading" class="skeleton" style="height:200px;margin-top:12px"></div>

    <form v-else class="ai-form" @submit.prevent="save">
      <label class="field">
        <span class="lbl">Provider</span>
        <select v-model="form.provider">
          <option v-for="o in ['', 'claude', 'openai', 'ollama', 'ollama_cloud']" :key="o" :value="o">
            {{ labels[o] }}
          </option>
        </select>
        <span class="help">Blank disables AI features — the rest of myMeal still works.</span>
      </label>

      <template v-if="form.provider">
        <p v-if="form.provider === 'ollama_cloud'" class="help" style="margin:0">
          Uses Ollama's hosted cloud (ollama.com). Paste your Ollama API key, then
          <em>List models</em> to pick from your account's models.
        </p>
        <label v-if="needsHost" class="field">
          <span class="lbl">Ollama host</span>
          <div class="row">
            <input v-model="form.baseUrl" class="fill" placeholder="http://homeassistant.local:11434" />
            <button type="button" class="secondary" :disabled="discovering" @click="findOllama">
              {{ discovering ? 'Finding…' : 'Find Ollama' }}
            </button>
          </div>
          <span class="help">Already run Ollama for Home Assistant? Point myMeal at the same server.</span>
        </label>

        <label v-if="form.provider === 'openai'" class="field">
          <span class="lbl">Base URL <span class="muted">(optional)</span></span>
          <input v-model="form.baseUrl" class="fill" placeholder="https://api.openai.com/v1" />
        </label>

        <label v-if="allowsKey" class="field">
          <span class="lbl">API key{{ needsKey ? '' : ' (optional)' }}</span>
          <input
            v-model="form.apiKey"
            type="password"
            class="fill"
            :placeholder="apiKeySet ? '•••••••• (saved — leave blank to keep)'
              : (needsKey ? 'Paste your API key' : 'Optional — for Ollama Cloud / a secured instance')"
            autocomplete="off"
          />
          <span class="help">
            Stored on this server only; never shown again or sent to the browser.
            <button v-if="apiKeySet" type="button" class="linkish" @click="clearKey">Clear saved key</button>
          </span>
        </label>

        <label class="field">
          <span class="lbl">Model</span>
          <div class="row">
            <input v-model="form.model" class="fill" list="model-options" placeholder="Model name" />
            <button type="button" class="secondary" :disabled="loadingModels" @click="loadModels">
              {{ loadingModels ? 'Loading…' : 'List models' }}
            </button>
          </div>
          <datalist id="model-options">
            <option v-for="m in models" :key="m" :value="m" />
          </datalist>
          <span v-if="models.length" class="help">{{ models.length }} models available — pick from the list.</span>
          <span v-else-if="loadingModels" class="help">Looking for available models…</span>
          <span v-else-if="modelsError" class="help err">{{ modelsError }}</span>
          <span v-else-if="canList && form.baseUrl" class="help">
            No models found at that host yet — check it's running, or type the model name.
          </span>
        </label>
      </template>

      <div class="row" style="margin-top:8px">
        <button type="submit" class="secondary" :disabled="saving">{{ saving ? 'Saving…' : 'Save' }}</button>
      </div>
    </form>
  </div>

  <div class="card" v-if="!loading">
    <h2>Chat</h2>
    <p class="muted">Household default for how chat replies arrive. <strong>Stream</strong>
      shows the answer as it's written; <strong>classic</strong> shows it all at once.
      Each person can override this for their own browser on the chat widget.</p>
    <label style="display:flex;gap:8px;align-items:center">
      <input type="checkbox" style="width:auto" :checked="chatStreamDefault" :disabled="chatSaving"
        @change="saveChatDefault($event.target.checked)" />
      <span>Stream chat responses by default</span>
    </label>
  </div>

  <div class="card" v-if="!loading">
    <h2>AI for background tasks</h2>
    <p class="muted">Optionally run background jobs on a different model than chat — e.g. a
      cheap or local model for bulk work. <strong>Same as chat</strong> uses the provider above.
      A per-run choice still wins.</p>
    <div v-for="area in JOB_AREAS" :key="area.k" style="margin-bottom:14px">
      <div class="muted" style="font-size:0.85rem;font-weight:600;margin-bottom:4px">{{ area.l }}</div>
      <div class="row" style="gap:8px">
        <select v-model="jobAi[area.k].provider" style="flex:1">
          <option value="">Same as chat</option>
          <option v-for="o in ['claude', 'openai', 'ollama', 'ollama_cloud']" :key="o" :value="o">{{ labels[o] }}</option>
        </select>
        <select v-if="jobModels[area.k].length" v-model="jobAi[area.k].model" style="flex:1">
          <option value="">Default model</option>
          <option v-for="m in jobModels[area.k]" :key="m" :value="m">{{ m }}</option>
          <option v-if="jobAi[area.k].model && !jobModels[area.k].includes(jobAi[area.k].model)"
                  :value="jobAi[area.k].model">{{ jobAi[area.k].model }} (current)</option>
        </select>
        <input v-else v-model="jobAi[area.k].model" placeholder="model (optional)" style="flex:1" />
        <button type="button" class="secondary sm" :disabled="jobModelsLoading[area.k]"
                @click="listJobModels(area.k)">{{ jobModelsLoading[area.k] ? '…' : 'List' }}</button>
      </div>
    </div>
    <div class="row" style="margin-top:8px">
      <button type="button" class="secondary" :disabled="jobAiSaving" @click="saveJobAi">
        {{ jobAiSaving ? 'Saving…' : 'Save' }}</button>
    </div>
  </div>

  <div class="card" v-if="!loading">
    <h2>Sync AI settings across apps</h2>
    <p class="muted">myMeal, Edibl and HomeHoard usually run side by side. Copy this app's AI
      configuration — provider, model, chat and background-task defaults — as a portable string,
      then paste it into the other apps' Settings so they all match. By default the
      <strong>API key is not included</strong>; opt in below to embed it too.</p>
    <label style="display:flex;gap:8px;align-items:center;margin-bottom:6px;font-size:.9rem">
      <input type="checkbox" v-model="syncInclKey" style="width:auto" />
      <span>Also include my API key in the copied text</span>
    </label>
    <div v-if="syncInclKey" style="margin-bottom:8px">
      <input type="password" v-model="syncKey" autocomplete="off" placeholder="Paste the API key to share"
        aria-label="API key to include in the sync string" style="width:100%;max-width:420px" />
      <p class="help" style="margin:4px 0 0">Your API key will be embedded in the copied text —
        treat it like a password and only paste it into your own apps.</p>
    </div>
    <div class="row" style="margin-bottom:8px">
      <button type="button" class="secondary" @click="copyAiSettings">📋 Copy AI settings</button>
    </div>
    <textarea v-if="syncOut" readonly rows="2" :value="syncOut"
      style="width:100%;font-family:monospace;font-size:.78rem" @focus="$event.target.select()"
      aria-label="AI settings string to copy"></textarea>

    <div style="margin-top:14px">
      <label for="ai-sync-in" class="muted" style="font-size:0.85rem;font-weight:600;display:block;margin-bottom:4px">
        Paste settings from another app</label>
      <textarea id="ai-sync-in" v-model="syncIn" rows="2" placeholder="AICFG1:… or AICFG2:…"
        style="width:100%;font-family:monospace;font-size:.78rem"></textarea>
      <p v-if="syncErr" class="help danger" style="margin:4px 0 0">{{ syncErr }}</p>
      <div class="row" style="margin-top:8px">
        <button type="button" class="secondary" :disabled="syncBusy || !syncIn.trim()" @click="applyAiSettings">
          {{ syncBusy ? 'Applying…' : 'Apply settings' }}</button>
      </div>
    </div>
  </div>

  <div class="card" v-if="!loading">
    <h2>Learned weights</h2>
    <p class="muted">
      Some units have no fixed weight — a stick, a can, a clove. When a recipe
      needs one the app doesn't know, it looks the answer up once and remembers
      it here. If the app already knows a unit, it uses its own weight instead
      of anything on this list.
    </p>
    <p v-if="!conversions.length" class="muted" style="font-size:.85rem">
      Nothing learned yet. This fills in on its own as you import recipes.
    </p>
    <ul v-else class="conv">
      <li v-for="c in conversions" :key="c.id" :class="{ pending: c.status === 'pending' }">
        <span class="what">1 {{ c.unit }} of {{ c.foodTerm }}</span>
        <span class="tnum weight">{{ c.gramsPerUnit }} g</span>
        <span class="prov">
          <a v-if="c.sourceUrl" :href="c.sourceUrl" target="_blank" rel="noopener noreferrer">found on the web ↗</a>
          <span v-else class="muted">{{ c.source === 'user' ? 'you set this' : 'found on the web' }}</span>
          <span v-if="c.status === 'pending'" class="badge">not used yet</span>
        </span>
        <span class="acts">
          <button v-if="c.status === 'pending'" type="button" class="secondary"
            :disabled="conversionsBusy === c.id" @click="confirmConversion(c)">Use it</button>
          <button type="button" class="secondary" :disabled="conversionsBusy === c.id"
            @click="forgetConversion(c)">Forget</button>
        </span>
      </li>
    </ul>
  </div>

  <div class="card" v-if="!loading">
    <h2>Nutrition</h2>
    <p class="muted">Estimate per-serving nutrition for every recipe that doesn't have it yet,
      using your configured AI provider. Runs in the background — you can leave this page.</p>
    <button v-if="!nutriActive" type="button" class="secondary" :disabled="nutriStarting"
      @click="startNutrition">{{ nutriStarting ? 'Starting…' : 'Estimate missing nutrition' }}</button>
    <div v-else style="max-width:420px">
      <div class="muted" style="font-size:.85rem;margin-bottom:6px">
        Estimating… {{ nutriJob.done }}<span v-if="nutriJob.total">/{{ nutriJob.total }}</span> recipes</div>
      <progress :value="nutriJob.done" :max="nutriJob.total || 1" style="width:100%"></progress>
    </div>
  </div>

  <div class="card" v-if="!loading">
    <h2>AI organize</h2>
    <p class="muted">Auto-tag recipes and propose collections with your AI provider.
      Confident tags are applied automatically; the rest wait for your review, and your
      accept/reject choices teach later runs.</p>
    <div style="display:flex;gap:8px;max-width:520px;margin-bottom:10px">
      <label style="flex:2">
        <span class="muted" style="font-size:0.85rem">Note (optional guidance)</span>
        <input v-model="organizeForm.note" placeholder="e.g. tag by cuisine" style="width:100%;margin-top:4px" />
      </label>
      <label style="flex:1">
        <span class="muted" style="font-size:0.85rem">Model (optional)</span>
        <input v-model="organizeForm.model" placeholder="override model" style="width:100%;margin-top:4px" />
      </label>
    </div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
      <button type="button" class="secondary" :disabled="catStarting || catActive" @click="startCategorize(organizeBody())">
        {{ catActive ? `Tagging… ${catJob.done}/${catJob.total || '…'}` : 'Auto-tag recipes' }}
      </button>
      <button type="button" class="secondary" :disabled="cluStarting || cluActive" @click="startCluster(organizeBody())">
        {{ cluActive ? 'Finding collections…' : 'Propose collections' }}
      </button>
      <router-link to="/review" class="muted" style="font-size:.9rem">Review suggestions →</router-link>
    </div>
  </div>

  <div class="card" v-if="!loading">
    <h2>Edibl — food inventory</h2>
    <p class="muted">
      Connect the companion <strong>Edibl</strong> app to power inventory-aware
      cooking ("what can I cook") from your real, fresh stock. Running both as
      Home Assistant add-ons? Click <em>Find Edibl</em> — no token needed.
    </p>
    <form class="ai-form" @submit.prevent="saveEdibl">
      <label class="field">
        <span class="lbl">Paste an Edibl <strong>connect link</strong> <span class="muted">(fills URL + token)</span></span>
        <input class="fill" placeholder="edibl-connect:… — from Edibl → Settings → Access &amp; keys"
          @change="pasteEdiblConnect($event)" />
      </label>
      <label class="field">
        <span class="lbl">Edibl URL</span>
        <div class="row">
          <input v-model="edibl.url" class="fill" placeholder="http://edibl:7746" />
          <button type="button" class="secondary" :disabled="ediblBusy" @click="findEdibl">
            {{ ediblBusy ? '…' : 'Find Edibl' }}
          </button>
        </div>
      </label>
      <label class="field">
        <span class="lbl">API token <span class="muted">(only if Edibl requires auth)</span></span>
        <input
          v-model="edibl.token"
          type="password"
          class="fill"
          :placeholder="ediblTokenSet ? '•••••••• (saved — leave blank to keep)' : 'Usually not needed behind HA ingress'"
          autocomplete="off"
        />
        <span class="help">
          <button v-if="ediblTokenSet" type="button" class="linkish" @click="clearEdiblToken">Clear saved token</button>
        </span>
      </label>
      <div class="row" style="margin-top:8px">
        <button type="submit" class="secondary" :disabled="ediblBusy">Save</button>
        <button type="button" class="secondary" :disabled="ediblBusy || !edibl.url" @click="testEdibl">
          Test connection
        </button>
      </div>
      <p v-if="ediblStatus" class="help" :style="{ color: ediblStatus.reachable ? 'var(--success)' : 'var(--danger)' }">
        {{ ediblStatus.reachable ? '✓ Connected to Edibl' : '✕ Not reachable' }}
        <span v-if="ediblStatus.detail" class="muted"> — {{ ediblStatus.detail }}</span>
      </p>
    </form>
  </div>

  <div class="card" v-if="!loading && members.length > 1">
    <h2>Household members</h2>
    <p class="muted">
      Everyone signed in to Home Assistant who has opened myMeal. Make another
      member an <strong>admin</strong> to let them change these settings and
      manage API keys. There must always be at least one admin.
    </p>
    <div
      v-for="m in members"
      :key="m.id"
      class="row"
      style="padding:10px 0;border-bottom:1px solid var(--border)"
    >
      <div class="fill">
        <div style="font-weight:600">
          {{ m.name }}<span v-if="m.isSelf" class="muted"> (you)</span>
        </div>
        <div class="muted" style="font-size:0.78rem">{{ m.isOwner ? 'Admin' : 'Member' }}</div>
      </div>
      <span v-if="m.isSelf && m.isOwner" class="badge ok">Admin</span>
      <button
        v-else-if="m.isOwner"
        type="button"
        class="secondary sm danger"
        :disabled="membersBusy"
        @click="setAdmin(m, false)"
      >Remove admin</button>
      <button
        v-else
        type="button"
        class="secondary sm"
        :disabled="membersBusy"
        @click="setAdmin(m, true)"
      >Make admin</button>
    </div>
  </div>

  <div class="card" v-if="!loading">
    <h2>Food preferences</h2>
    <p class="muted">
      Your household's diet, allergies, and dislikes. The recipe drafter, meal
      planner, and cooking assistant honour these — and the assistant can update
      them for you (“we're vegetarian now”).
    </p>
    <form class="ai-form" @submit.prevent="savePrefs">
      <label class="field">
        <span class="lbl">Diet</span>
        <input v-model="prefs.diet" placeholder="e.g. vegetarian, pescatarian, halal" />
      </label>
      <label class="field">
        <span class="lbl">Allergies <span class="muted">— always avoided</span></span>
        <input v-model="prefs.allergies" placeholder="e.g. peanuts, shellfish" />
      </label>
      <label class="field">
        <span class="lbl">Dislikes</span>
        <input v-model="prefs.dislikes" placeholder="e.g. cilantro, olives" />
      </label>
      <label class="field">
        <span class="lbl">Notes</span>
        <textarea v-model="prefs.notes" rows="2"
          placeholder="Anything else — low-spice, batch-friendly, kid-approved…"></textarea>
      </label>
      <div>
        <button type="submit" class="secondary" :disabled="prefsBusy">
          {{ prefsBusy ? 'Saving…' : 'Save preferences' }}
        </button>
      </div>
    </form>
  </div>

  <div class="card" v-if="!loading">
    <h2>API keys</h2>
    <p class="muted">
      Long-lived keys for machine clients — the Home Assistant integration, the
      MCP server, or a companion app connecting to myMeal. Shown once when
      created; store it somewhere safe.
    </p>

    <div v-if="mintedKey" class="minted">
      <div class="muted" style="font-size:0.8rem">New key (copy it now — it won't be shown again):</div>
      <code class="keyval">{{ mintedKey }}</code>
      <button type="button" class="secondary sm" @click="copyKey(mintedKey)">Copy</button>
      <button type="button" class="linkish" @click="mintedKey = null">Done</button>
    </div>

    <div class="row" style="margin:12px 0;max-width:520px">
      <input v-model="newKeyName" class="fill" placeholder="Name (e.g. Home Assistant)" />
      <select v-model="newKeyScope" aria-label="Key scope">
        <option value="full">Full (REST + MCP)</option>
        <option value="rest">REST only</option>
        <option value="mcp">MCP only</option>
        <option value="debug">Debug only (reads logs)</option>
      </select>
      <select v-model="newKeyAccess" aria-label="Key access" :disabled="newKeyScope === 'debug'">
        <option value="write">Read &amp; write</option>
        <option value="read">Read only</option>
      </select>
      <button type="button" class="secondary" :disabled="keysBusy" @click="mintKey">Create key</button>
    </div>
    <p v-if="newKeyScope === 'debug'" class="muted" style="font-size:0.78rem;max-width:520px;margin:-4px 0 0">
      A <strong>Debug</strong> key reads this add-on’s own logs, recent errors and
      timings — and nothing else. It can’t reach the REST API or the recipe tools.
      Logs can include sign-in email addresses and error details, so treat it like a
      password and delete it when you’re done. Turn on <code>mcp_debug_tools</code>
      in the add-on configuration for it to do anything.
    </p>
    <p v-else class="muted" style="font-size:0.78rem;max-width:520px;margin:-4px 0 0">
      Use an <strong>MCP</strong>-scoped key to expose the MCP server outside Home
      Assistant — it works only against the MCP endpoint, not the REST API.
    </p>

    <div v-if="!keys.length" class="key-empty muted">
      <span class="ke-ico">🔑</span> No API keys yet — create one above to connect a client.
    </div>
    <div
      v-for="k in keys"
      :key="k.id"
      class="row"
      style="padding:10px 0;border-bottom:1px solid var(--border)"
    >
      <div class="fill">
        <div style="font-weight:600">{{ k.name || 'API key' }}</div>
        <div class="muted" style="font-size:0.78rem">
          {{ k.hint }} · <span style="text-transform:uppercase">{{ k.scope || 'full' }}</span> · {{ (k.access || 'write') === 'read' ? 'read-only' : 'read/write' }} · created {{ (k.createdAt || '').slice(0, 10) }}
          <span v-if="k.lastUsedAt"> · last used {{ (k.lastUsedAt || '').slice(0, 10) }}</span>
          <span v-else> · never used</span>
        </div>
      </div>
      <button type="button" class="linkish danger" @click="revokeKey(k.id)">Revoke</button>
    </div>
  </div>

  <div class="card" v-if="!loading">
    <h2>Provider status</h2>
    <div
      v-for="p in providers"
      :key="p.name"
      class="row"
      style="padding:12px 0;border-bottom:1px solid var(--border)"
    >
      <div class="fill">
        <div style="font-weight:600">{{ labels[p.name] || p.name }}</div>
        <div class="muted" style="font-size:0.82rem">{{ p.available ? 'Configured' : 'Not configured' }}</div>
      </div>
      <span v-if="p.active" class="chip">Active</span>
      <span v-else-if="p.available" class="badge ok">Ready</span>
      <span v-else class="badge">Off</span>
    </div>
  </div>
</template>

<style scoped>
.ai-form { display: flex; flex-direction: column; gap: 16px; margin-top: 14px; max-width: 520px; }
.field { display: flex; flex-direction: column; gap: 5px; }
.field .lbl { font-weight: 600; font-size: 0.88rem; }
.field .help { font-size: 0.76rem; color: var(--muted); }
.field select, .field input { width: 100%; }
.row { display: flex; gap: 8px; align-items: center; }
.row .fill { flex: 1; }

.minted { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; padding: 10px 12px; background: var(--accent-soft); border-radius: var(--radius-sm); margin-bottom: 8px; }
.keyval { font-family: monospace; font-size: 0.8rem; word-break: break-all; background: var(--surface); padding: 4px 8px; border-radius: 6px; }
.sm { padding: 4px 10px; font-size: 0.78rem; }
.danger { color: var(--danger); }
.key-empty { display: flex; align-items: center; gap: 8px; padding: 14px 12px; font-size: 0.85rem; background: var(--surface-2); border-radius: var(--radius-sm); }
.key-empty .ke-ico { font-size: 1.1rem; opacity: 0.7; }
/* Why the model picker is empty. Uses the danger token — a provider that can't
   be reached is an error the user must act on, not neutral help text. */
.help.err { color: var(--danger); }

/* Learned weights: a row per remembered value. A list rather than a table so it
   stacks on a phone — a table here pushed the Forget button off-screen, which is
   the one control the row exists for. */
/* A grid, not a flex row: the whole job on this card is scanning the weight
   column for a value that looks wrong ("400 g for a can, sure — 12 g for a
   handful of parsley?"). Flex put the numbers at four different x positions. */
.conv { list-style: none; margin: 12px 0 0; padding: 0; font-size: 0.9rem;
  display: grid; grid-template-columns: minmax(160px, 1fr) 72px auto auto;
  max-width: 720px; }
.conv li { display: contents; }
.conv li > * { padding: 10px 0; border-top: 1px solid var(--border); align-self: center; }
.conv .what { padding-right: 12px; }
.conv .weight { text-align: right; }
.conv .prov { font-size: 0.8rem; padding-left: 16px; white-space: nowrap; }
.conv .acts { display: flex; gap: 6px; justify-content: flex-end; padding-left: 12px; }
/* The row with a decision attached gets the accent tick; nothing else does. */
.conv li.pending .what { box-shadow: inset 2px 0 0 var(--accent); padding-left: 10px; }
.conv button { padding: 4px 10px; font-size: 0.78rem; }
@media (max-width: 620px) {
  /* Two lines, not four: name + right-aligned weight, then provenance and
     actions sharing a baseline. Keeps the weight column readable on a phone. */
  .conv { grid-template-columns: 1fr auto; }
  .conv li > * { border-top: none; padding: 2px 0; }
  .conv .what { border-top: 1px solid var(--border); padding-top: 10px; }
  .conv .weight { border-top: 1px solid var(--border); padding-top: 10px; }
  .conv .prov { padding: 0 0 10px; white-space: normal; }
  .conv .acts { padding: 0 0 10px; }
}
</style>
