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

// The whole plan is drawn up front, not appended stage by stage. Showing one
// line at a time is indistinguishable from a spinner, teaches nothing about how
// long this takes, and reflows the card on every event.
const STAGE_LABELS = {
  searching: 'Search the web for the recipe',
  fetching: 'Fetch the page',
  parsing: 'Read the recipe',
  structuring: 'Work out the tricky ingredient lines',
  converting: 'Look up weights it doesn’t know',
}
const stages = ref([])

function planStages() {
  const keys = mode.value === 'search'
    ? ['searching', 'fetching', 'parsing', 'structuring', 'converting']
    : ['fetching', 'parsing', 'structuring', 'converting']
  // 'skipped' is a real outcome, not a failure: a tidy recipe never needs the
  // model, and saying so is more honest than quietly dropping the row.
  stages.value = keys.map((key) => ({ key, label: STAGE_LABELS[key], state: 'pending' }))
}

// The review step. Non-empty only when the model proposed structure for lines
// the parser couldn't read — a clean import goes straight to the recipe.
const imported = ref(null)
const proposals = ref([])
const warnings = ref([])   // non-fatal notes about the source (lint)
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
  let reached = false
  stages.value.forEach((s) => {
    if (s.key === key) {
      reached = true
      s.state = 'active'
      // Just the host: a full URL wraps mid-token on a phone and reads as
      // corrupted text, and the scheme carries nothing.
      s.detail = detail && detail.startsWith('http')
        ? detail.replace(/^https?:\/\//, '').split('/')[0]
        : detail || ''
    } else if (!reached) {
      s.state = 'done'
    }
  })
}

function stagesFinished() {
  // Anything the server never reported was genuinely not needed.
  stages.value.forEach((s) => {
    s.state = s.state === 'pending' ? 'skipped' : 'done'
  })
}

// A two-significant-figure percentage from a small language model is not a
// measurement, and showing one invites the user to trust a threshold nobody
// chose. Three plain bands instead.
function sureness(confidence) {
  if (confidence >= 0.85) return { label: 'confident', tone: '' }
  if (confidence >= 0.6) return { label: 'fairly sure', tone: '' }
  return { label: 'unsure — check this', tone: 'warn' }
}

function beginReview(recipe) {
  imported.value = recipe
  warnings.value = recipe.warnings || []
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
  planStages()
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

    stagesFinished()
    // Stop on the review screen when there is something to confirm OR something
    // worth telling the user about the source. A silent import that quietly
    // lost the timings is worse than one that says so.
    if ((recipe.ingredientProposals || []).length || (recipe.warnings || []).length) {
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
        // A component (another recipe used as an ingredient) has no food, and
        // omitting this would silently unlink it. An import can't produce one
        // today, but this payload is a full replacement — it has to carry
        // everything a row can be.
        refRecipeId: ing.refRecipe?.id || null,
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
    <h2 style="margin-top:0">
      {{ proposals.length ? 'A few lines needed interpreting' : 'Imported, with notes' }}
    </h2>

    <!-- Lint: the import succeeded; these say what was thin about the source. -->
    <ul v-if="warnings.length" class="notes">
      <li v-for="(w, i) in warnings" :key="i">{{ w }}</li>
    </ul>

    <p v-if="proposals.length" class="muted intro">
      “{{ imported.name }}” is saved. {{ proposals.length }} ingredient
      line{{ proposals.length === 1 ? '' : 's' }} weren’t written in a form the app
      could measure, so a language model suggested what
      {{ proposals.length === 1 ? 'it means' : 'they mean' }} — please check
      {{ proposals.length === 1 ? 'it' : 'them' }}. Your original wording is kept
      either way; this only affects scaling and shopping lists.
      <strong>Unticked lines stay exactly as written.</strong>
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
              <!-- type=number so a phone gets the numeric keypad and the browser
                   rejects letters. The server also parses "1/2" and falls back
                   to no-amount, so a stray value costs scaling, not the row. -->
              <input class="tnum" type="number" min="0" step="any"
                v-model.number="p.quantity" :disabled="!p.keep" />
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
        <span class="badge" :class="sureness(p.confidence || 0).tone">
          {{ sureness(p.confidence || 0).label }}
        </span>
      </div>
    </div>

    <div class="row actions">
      <template v-if="proposals.length">
        <button class="secondary" :disabled="saving" @click="skipReview">Leave them as written</button>
        <button :disabled="saving" @click="applyReview">
          {{ saving ? 'Saving…' : 'Use these' }}
        </button>
      </template>
      <button v-else @click="skipReview">View recipe</button>
    </div>
  </div>

  <!-- Step 1b: the whole plan, drawn once, with the current step marked. -->
  <div v-else-if="busy && stages.length" class="card">
    <ol class="stages" aria-live="polite">
      <li v-for="s in stages" :key="s.key" :class="s.state">
        <span class="marker" aria-hidden="true">{{ s.state === 'active' ? '◌' : (s.state === 'done' ? '✓' : '·') }}</span>
        <span class="label">
          {{ s.label }}
          <span v-if="s.detail" class="detail">{{ s.detail }}</span>
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
        Paste a recipe’s <strong>schema.org JSON-LD</strong> (a
        <code>{"@type": "Recipe", …}</code> block, a <code>@graph</code>, or the
        whole <code>&lt;script type="application/ld+json"&gt;</code> tag) and it is
        read directly — <strong>no AI provider needed</strong>. Anything else is
        parsed by your configured AI provider.
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

/* --- progress ---
   Every stage is on screen from the first frame, so the card never changes
   height and the user can see what is still to come. A 1px spine ties the
   markers together and its filled portion is the actual progress bar. */
.stages { list-style: none; margin: 0; padding: 0 0 0 4px; position: relative; }
.stages::before {
  content: ''; position: absolute; left: 10px; top: 12px; bottom: 12px;
  width: 1px; background: var(--border);
}
.stages li {
  display: flex; gap: 14px; align-items: baseline;
  padding: 7px 0; color: var(--muted); position: relative;
}
.stages li.done { color: var(--text); }
.stages li.active { color: var(--text); font-weight: 650; }
.stages li.skipped .label { text-decoration: line-through; opacity: 0.7; }
.marker {
  width: 13px; text-align: center; flex: none;
  background: var(--surface); position: relative; z-index: 1;
}
.stages li.done .marker { color: var(--success); }
.stages li.active .marker { color: var(--accent); display: inline-block; animation: spin 1.4s linear infinite; }
.detail { font-weight: 400; color: var(--muted); margin-left: 8px; }
@keyframes spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) {
  .stages li.active .marker { animation: none; }
}

/* --- review --- */
/* Capped: at 1440 an unbounded row put the original line and its confidence
   badge a thousand pixels apart — exactly the two things the user has to
   associate to make the decision. */
.proposals { display: flex; flex-direction: column; gap: 12px; margin: 24px 0; max-width: 760px; }
.proposal {
  display: flex; gap: 12px; align-items: flex-start;
  padding: 12px; border: 1px solid var(--border); border-radius: var(--radius);
  background: var(--surface-2);
}
.proposal.off { opacity: 0.55; }
.notes {
  max-width: 760px; margin: 16px 0 0; padding: 12px 12px 12px 30px;
  background: var(--surface-2); border-left: 3px solid var(--warning);
  border-radius: var(--radius); font-size: 0.88rem;
}
.notes li + li { margin-top: 6px; }

/* Intro, rows and buttons share one measure, so the eye tracks a single column
   instead of the paragraph running to 1100px above 760px-wide rows. */
.intro { max-width: 760px; }
.actions { max-width: 760px; justify-content: flex-end; }
.keep { padding-top: 20px; }
.lines { flex: 1; min-width: 0; }
.original { font-size: 0.85rem; color: var(--muted); margin-bottom: 8px; }
.fields { display: flex; gap: 10px; flex-wrap: wrap; }
.field.mini { margin: 0; }
.field.mini input { width: 92px; }
.field.mini.grow { flex: 1; min-width: 140px; max-width: 320px; }
.field.mini.grow input { width: 100%; }
/* A checked box is not an action, so it doesn't get the accent — that stays on
   "Use these", the one primary action on this view. */
.keep input { accent-color: var(--muted); }
.badge.warn { color: var(--warning); }
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
