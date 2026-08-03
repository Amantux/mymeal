<script setup>
import { ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import { api, streamPost } from '../api'
import { useUI } from '../stores/ui'

const router = useRouter()
const ui = useUI()

const mode = ref('search')
const query = ref('')
const url = ref('')
const text = ref('')
const photoFile = ref(null)
const busy = ref(false)

// What the server says it is doing, in the order it said it. An import can sit
// on a local model for a while; silence reads as "stuck".
const STAGE_LABELS = {
  searching: 'Searching the web',
  fetching: 'Fetching the page',
  parsing: 'Reading the recipe',
  structuring: 'Working out the tricky ingredient lines',
  converting: 'Looking up weights it doesn’t know',
}
const stages = ref([])

// The review step. Non-empty only when the model proposed structure for lines
// the parser couldn't read — a clean import goes straight to the recipe.
const imported = ref(null)
const proposals = ref([])
const saving = ref(false)

const canRun = computed(() => {
  if (mode.value === 'search') return !!query.value
  if (mode.value === 'url') return !!url.value
  if (mode.value === 'text') return !!text.value
  return !!photoFile.value
})

function onPhoto(e) {
  photoFile.value = e.target.files[0] || null
}

function stageStarted(key, detail) {
  stages.value.forEach((s) => { s.active = false })
  stages.value.push({
    key,
    label: STAGE_LABELS[key] || key,
    detail: detail || '',
    active: true,
  })
}

function beginReview(recipe) {
  imported.value = recipe
  proposals.value = (recipe.ingredientProposals || []).map((p) => ({
    ...p,
    // Opt-in per row. A proposal is a suggestion, and accepting all of them
    // silently is the same as not asking.
    keep: true,
  }))
}

async function run() {
  if (!canRun.value) return
  busy.value = true
  stages.value = []
  imported.value = null
  try {
    if (mode.value === 'photo') {
      const fd = new FormData()
      fd.append('image', photoFile.value)
      const recipe = await api.uploadPost('/ai/photo', fd)
      ui.toast('Recipe scanned from photo')
      router.push(`/recipes/${recipe.id}`)
      return
    }

    const body = mode.value === 'search' ? { query: query.value }
      : mode.value === 'url' ? { url: url.value } : { text: text.value }

    let failure = null
    let recipe = null
    await streamPost('/ai/import/stream', body, (event) => {
      if (event.type === 'stage') stageStarted(event.stage, event.detail)
      // The error arrives as an event, not a rejection: by the time the import
      // can fail the response has already started.
      else if (event.type === 'error') failure = event.error
      else if (event.type === 'done') recipe = event
    })
    if (failure) throw new Error(failure)
    if (!recipe) throw new Error('The import finished without producing a recipe.')

    stages.value.forEach((s) => { s.active = false })
    if ((recipe.ingredientProposals || []).length) {
      beginReview(recipe)
    } else {
      ui.toast('Recipe imported')
      router.push(`/recipes/${recipe.id}`)
    }
  } catch (e) {
    ui.error(e.message)
    stages.value = []
  } finally {
    busy.value = false
  }
}

function skipReview() {
  ui.toast('Recipe imported')
  router.push(`/recipes/${imported.value.id}`)
}

async function applyReview() {
  const kept = proposals.value.filter((p) => p.keep)
  if (!kept.length) return skipReview()
  saving.value = true
  try {
    const byDisplay = new Map(kept.map((p) => [p.display, p]))
    // Send every line back, not just the changed ones: the update endpoint
    // replaces the ingredient list wholesale, so a partial payload would drop
    // the rest of the recipe.
    const rows = imported.value.ingredients.map((ing, i) => {
      const p = byDisplay.get(ing.display)
      return {
        display: ing.display,
        quantity: p ? p.quantity : ing.quantity,
        unit: p ? p.unit : ing.unit?.name || '',
        food: p ? p.food : ing.food?.name || '',
        note: p ? p.note : ing.note,
        section: ing.section,
        position: i,
      }
    })
    await api.put(`/recipes/${imported.value.id}`, { ingredients: rows })
    ui.toast(`Recipe imported — ${kept.length} line${kept.length === 1 ? '' : 's'} tidied up`)
    router.push(`/recipes/${imported.value.id}`)
  } catch (e) {
    ui.error(e.message)
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="page-head"><h1>Import a recipe</h1></div>

  <!-- Step 2: confirm what the model worked out. Only shown when it proposed
       something; most imports never see this. -->
  <div v-if="imported" class="card">
    <h2 style="margin-top:0">A few lines needed interpreting</h2>
    <p class="muted">
      “{{ imported.name }}” is saved. These {{ proposals.length }} ingredient
      line{{ proposals.length === 1 ? '' : 's' }} weren’t written in a form the app
      could measure, so it worked out what {{ proposals.length === 1 ? 'it' : 'they' }}
      probably mean. Your original wording is kept either way — this only affects
      scaling and shopping lists.
    </p>

    <div class="proposals">
      <div v-for="(p, i) in proposals" :key="i" class="proposal" :class="{ off: !p.keep }">
        <label class="keep">
          <input type="checkbox" v-model="p.keep" />
          <span class="sr-only">Use the interpretation of “{{ p.display }}”</span>
        </label>
        <div class="lines">
          <div class="original">{{ p.display }}</div>
          <div class="fields">
            <label class="field mini">
              <span>Amount</span>
              <input class="tnum" v-model.number="p.quantity" :disabled="!p.keep" />
            </label>
            <label class="field mini">
              <span>Unit</span>
              <input v-model="p.unit" :disabled="!p.keep" placeholder="—" />
            </label>
            <label class="field mini grow">
              <span>Ingredient</span>
              <input v-model="p.food" :disabled="!p.keep" />
            </label>
          </div>
        </div>
        <span class="badge" :class="{ ok: p.confidence >= 0.8 }">
          {{ Math.round((p.confidence || 0) * 100) }}% sure
        </span>
      </div>
    </div>

    <div class="row" style="justify-content:flex-end;margin-top:16px">
      <button class="secondary" :disabled="saving" @click="skipReview">Leave them as written</button>
      <button :disabled="saving" @click="applyReview">
        {{ saving ? 'Saving…' : 'Use these' }}
      </button>
    </div>
  </div>

  <!-- Step 1b: what the import is doing right now. -->
  <div v-else-if="busy && stages.length" class="card">
    <h2 style="margin-top:0">Importing…</h2>
    <ol class="stages">
      <li v-for="s in stages" :key="s.key" :class="{ active: s.active }">
        <span class="marker" aria-hidden="true">{{ s.active ? '◌' : '✓' }}</span>
        <span>
          {{ s.label }}
          <span v-if="s.detail" class="muted detail">{{ s.detail }}</span>
        </span>
      </li>
    </ol>
  </div>

  <!-- Step 1a: the form. -->
  <div v-else class="card">
    <div class="tabs">
      <button class="secondary" :class="{ active: mode === 'search' }" @click="mode = 'search'">By name</button>
      <button class="secondary" :class="{ active: mode === 'url' }" @click="mode = 'url'">From a link</button>
      <button class="secondary" :class="{ active: mode === 'text' }" @click="mode = 'text'">Paste text</button>
      <button class="secondary" :class="{ active: mode === 'photo' }" @click="mode = 'photo'">From a photo</button>
    </div>

    <template v-if="mode === 'search'">
      <label class="field">
        <span>Recipe name</span>
        <input v-model="query" placeholder="e.g. chicken tikka masala" @keyup.enter="run" />
      </label>
      <p class="muted" style="font-size:0.85rem">
        Searches the web (Ollama web search) for the recipe and imports the best match.
        Needs an Ollama search key — set it in Settings.
      </p>
    </template>

    <template v-else-if="mode === 'url'">
      <label class="field">
        <span>Recipe URL</span>
        <input v-model="url" placeholder="https://…" @keyup.enter="run" />
      </label>
      <p class="muted" style="font-size:0.85rem">
        Structured recipe pages import instantly. For pages without recipe markup,
        your configured AI provider parses the content.
      </p>
    </template>

    <template v-else-if="mode === 'text'">
      <label class="field">
        <span>Recipe text</span>
        <textarea v-model="text" rows="12" placeholder="Paste a full recipe here…"></textarea>
      </label>
      <p class="muted" style="font-size:0.85rem">
        Parsed by your configured AI provider — set one up in Settings if import fails.
      </p>
    </template>

    <template v-else>
      <label class="field">
        <span>Recipe photo</span>
        <input type="file" accept="image/jpeg,image/png,image/webp" @change="onPhoto" />
      </label>
      <p class="muted" style="font-size:0.85rem">
        Snap a recipe card, cookbook page, or handwritten note — your AI provider
        reads it (needs a vision-capable model). The photo becomes the recipe image.
      </p>
    </template>

    <div class="row" style="justify-content:flex-end;margin-top:8px">
      <button :disabled="busy || !canRun" @click="run">
        {{ busy ? (mode === 'photo' ? 'Scanning…' : 'Importing…') : (mode === 'photo' ? 'Scan photo' : 'Import') }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.tabs { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 16px; }
/* Active tab is a neutral raised fill (not the accent) so the page's primary
   action stays the only terracotta element. */
.tabs button.active { background: var(--surface-2); color: var(--text); border-color: var(--border); font-weight: 700; }

/* --- progress --- */
.stages { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 12px; }
.stages li { display: flex; gap: 12px; align-items: baseline; color: var(--muted); }
.stages li.active { color: var(--text); font-weight: 600; }
.marker { color: var(--success); }
.stages li.active .marker { color: var(--accent); display: inline-block; animation: spin 1.4s linear infinite; }
.detail { font-weight: 400; margin-left: 8px; word-break: break-all; }
@keyframes spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) {
  .stages li.active .marker { animation: none; }
}

/* --- review --- */
.proposals { display: flex; flex-direction: column; gap: 12px; margin-top: 16px; }
.proposal {
  display: flex; gap: 12px; align-items: flex-start;
  padding: 12px; border: 1px solid var(--border); border-radius: var(--radius);
  background: var(--surface-2);
}
.proposal.off { opacity: 0.55; }
.keep { padding-top: 20px; }
.lines { flex: 1; min-width: 0; }
.original { font-size: 0.85rem; color: var(--muted); margin-bottom: 8px; }
.fields { display: flex; gap: 10px; flex-wrap: wrap; }
.field.mini { margin: 0; }
.field.mini input { width: 92px; }
.field.mini.grow { flex: 1; min-width: 140px; }
.field.mini.grow input { width: 100%; }
.sr-only {
  position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
  overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; border: 0;
}
@media (max-width: 560px) {
  /* The confidence badge drops below the fields rather than squeezing them. */
  .proposal { flex-wrap: wrap; }
  .field.mini input { width: 76px; }
}
</style>
