<script setup>
import { ref, reactive, onMounted, computed, watch, nextTick } from 'vue'
import { api } from '../api'
import { useUI } from '../stores/ui'

const ui = useUI()
const loading = ref(true)
const saving = ref(false)
const providers = ref([])          // status list (name/available/active)
const models = ref([])             // model-picker options for the active provider
const loadingModels = ref(false)
const discovering = ref(false)

// Editable form. apiKey is write-only: we never receive the stored key, so the
// field starts blank and a blank value on save leaves the stored key untouched.
const form = reactive({ provider: '', baseUrl: '', model: '', apiKey: '' })
const apiKeySet = ref(false)

const labels = { '': 'Disabled', claude: 'Claude (Anthropic)', openai: 'OpenAI', ollama: 'Ollama (local)' }
const needsKey = computed(() => form.provider === 'claude' || form.provider === 'openai')
const needsHost = computed(() => form.provider === 'ollama')

// When the USER switches provider, clear the per-provider fields so a prior
// provider's host/model/key don't visually carry over (the backend stores
// per-provider, but the form shows one active view). `suppress` guards the
// hydration assignment in load() — Vue flushes watchers asynchronously, so a
// plain `loading` flag wouldn't cover it.
const suppress = ref(false)
watch(() => form.provider, () => {
  if (suppress.value) return
  form.baseUrl = ''
  form.model = ''
  form.apiKey = ''
  apiKeySet.value = false
  models.value = []
})

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
  } finally {
    loading.value = false
    await nextTick()               // let the watcher flush before re-arming it
    suppress.value = false
  }
}
onMounted(load)

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
          <option v-for="o in ['', 'claude', 'openai', 'ollama']" :key="o" :value="o">
            {{ labels[o] }}
          </option>
        </select>
        <span class="help">Blank disables AI features — the rest of myMeal still works.</span>
      </label>

      <template v-if="form.provider">
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

        <label v-if="needsKey" class="field">
          <span class="lbl">API key</span>
          <input
            v-model="form.apiKey"
            type="password"
            class="fill"
            :placeholder="apiKeySet ? '•••••••• (saved — leave blank to keep)' : 'Paste your API key'"
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
        </label>
      </template>

      <div class="row" style="margin-top:8px">
        <button type="submit" :disabled="saving">{{ saving ? 'Saving…' : 'Save' }}</button>
      </div>
    </form>
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
</style>
