<script setup>
import { ref, computed, watch, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, streamPost } from '../api'
import { useUI } from '../stores/ui'
import InputMethods from '../components/InputMethods.vue'

const router = useRouter()
const route = useRoute()
const ui = useUI()

// Seeded from the URL so arriving from the New recipe page lands on the method
// that was picked, and so refresh / back / a bookmark all keep it. An unknown
// value falls back rather than rendering a tab that matches nothing.
const MODES = ['search', 'url', 'text', 'photo', 'archive']
const mode = ref(MODES.includes(String(route.query.mode)) ? String(route.query.mode) : 'search')
// Keep the URL in step with the tabs, without stacking history entries for what
// is a change of input, not a change of page.
watch(mode, (m) => {
  if (String(route.query.mode || '') !== m) router.replace({ path: '/import', query: { mode: m } })
})
// ...and the tabs in step with the URL. This is not optional: <router-view> is
// keyed on the PATH, so arriving at /import with a different ?mode reuses this
// component and setup() never runs again — without this, coming from the New
// recipe page would land on whatever tab was open last.
watch(() => route.query.mode, (m) => {
  const next = MODES.includes(String(m)) ? String(m) : 'search'   // same rule as the seed
  if (next !== mode.value) mode.value = next
})
const query = ref('')
const url = ref('')
const text = ref('')
const photoFile = ref(null)
const archiveFile = ref(null)
// The bulk result: {createdCount, created[], skippedCount, skipped[]} — its own
// terminal screen, since a migration's outcome is a tally, not a single recipe.
const bulk = ref(null)
const busy = ref(false)

// A textarea only ever receives the clipboard's PLAIN-TEXT flavour, but a page
// copied from a browser also carries a `text/html` flavour — and that is where
// the recipe's own markup lives (itemprop attributes, `class="ingredients"`).
// The plain flavour has already thrown all of it away. Keeping the HTML lets the
// importer read the page's own labels instead of inferring them from prose.
const richHtml = ref('')
let pastedPlain = ''

async function onPaste(e) {
  const html = (e.clipboardData?.getData('text/html') || '').trim()
  const replacing = !text.value.trim() || _selectionCoversAll(e.target)
  // Let the default paste run first so the textarea shows the readable text; the
  // markup is carried alongside it, not instead of it.
  await nextTick()
  // Only ONE fragment's markup can be sent, so keep it only when this paste is
  // the whole field. Pasting a second recipe on top of a first used to send just
  // the second fragment while the textarea visibly showed both — half the recipe
  // imported, with a tick saying formatting was preserved.
  richHtml.value = replacing ? html : ''
  pastedPlain = text.value
}
function _selectionCoversAll(el) {
  return el && el.selectionStart === 0 && el.selectionEnd === el.value.length
}
// Typing after a paste means the user is correcting what they can SEE, so the
// stashed markup is stale and must not silently win over their edit.
watch(text, (v) => { if (v !== pastedPlain) richHtml.value = '' })

// The whole plan is drawn up front, not appended stage by stage. Showing one
// line at a time is indistinguishable from a spinner, teaches nothing about how
// long this takes, and reflows the card on every event.
const STAGE_LABELS = {
  searching: 'Search the web for the recipe',
  fetching: 'Fetch the page',
  parsing: 'Read the recipe',
  completing: 'Fill in what the text didn’t spell out',
  structuring: 'Work out the tricky ingredient lines',
  converting: 'Look up weights it doesn’t know',
}
const stages = ref([])

function planStages() {
  // 'completing' only exists for a paste: the backend cannot emit it for a URL
  // or a search, where the model has already seen the whole page. Listing it
  // there would render a permanently-skipped row on every import.
  const keys = mode.value === 'search'
    ? ['searching', 'fetching', 'parsing', 'structuring', 'converting']
    : mode.value === 'text'
      ? ['fetching', 'parsing', 'completing', 'structuring', 'converting']
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
  if (mode.value === 'archive') return !!archiveFile.value
  return !!photoFile.value
})

function onArchive(e) {
  archiveFile.value = e.target.files[0] || null
}

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
  if (mode.value !== 'archive') planStages()
  imported.value = null
  bulk.value = null
  try {
    if (mode.value === 'archive') {
      const fd = new FormData()
      fd.append('archive', archiveFile.value)
      bulk.value = await api.uploadPost('/ai/import/archive', fd)
      return    // terminal: the result screen shows the tally
    }
    if (mode.value === 'photo') {
      const fd = new FormData()
      fd.append('image', photoFile.value)
      const recipe = await api.uploadPost('/ai/photo', fd)
      ui.toast('Recipe scanned from photo')
      router.push(`/recipes/${recipe.id}`)
      return
    }

    const body = mode.value === 'search' ? { query: query.value }
      : mode.value === 'url' ? { url: url.value }
        : { text: richHtml.value || text.value }

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
  <!-- Migration result: a tally, not a single recipe. Errors persist on screen
       (the skip list) rather than vanishing in a toast, per the house rule. -->
  <div v-if="bulk" class="card">
    <h2>{{ bulk.createdCount }} recipe{{ bulk.createdCount === 1 ? '' : 's' }} imported</h2>
    <p v-if="bulk.skippedCount" class="muted" style="margin-top:4px">
      {{ bulk.skippedCount }} {{ bulk.skippedCount === 1 ? 'entry' : 'entries' }} skipped:
    </p>
    <ul v-if="bulk.skippedCount" class="notes">
      <li v-for="s in bulk.skipped" :key="s.entry">
        <strong>{{ s.entry }}</strong> — {{ s.reason }}
      </li>
    </ul>
    <div class="row" style="justify-content:flex-end;gap:8px;margin-top:12px">
      <button class="secondary" @click="bulk = null; archiveFile = null">Import another file</button>
      <button @click="router.push('/recipes')">Go to recipes</button>
    </div>
  </div>

  <div v-else-if="imported" class="card">
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
    <!-- The same six-method selector as the New recipe page, so the choice looks
         identical wherever you are — and so there is a way BACK to typing it out,
         which this page never offered. -->
    <InputMethods v-model="mode" />

    <template v-if="mode === 'search'">
      <label class="field">
        <span>Recipe name</span>
        <input v-model="query" placeholder="e.g. chicken tikka masala" @keyup.enter="run" />
      </label>
      <p class="muted" style="font-size:0.85rem">
        Looks the dish up in TheMealDB’s free recipe database — no key, no AI
        needed. If it isn’t there, an Ollama search key (Settings) lets it search
        the wider web instead.
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
        <textarea v-model="text" rows="12" @paste="onPaste"
          placeholder="Paste a whole recipe — copy it straight off a web page, or type it out:&#10;&#10;Lemon Drizzle Cake&#10;Serves 8 | Prep 20 min | Bake 45 min&#10;&#10;Ingredients&#10;225g butter, softened&#10;4 large eggs&#10;&#10;Method&#10;1. Heat the oven to 180C.&#10;2. Bake for 45 minutes."></textarea>
      </label>
      <p v-if="richHtml" class="rich-note">
        ✓ Pasted with formatting — the page’s own recipe markup came with it and
        will be used.
        <button type="button" class="linky" @click="richHtml = ''">Use plain text instead</button>
      </p>
      <p class="muted" style="font-size:0.85rem">
        Copied from a web page, typed out, or a <strong>schema.org JSON-LD</strong>
        block — all read directly, <strong>no AI provider needed</strong>.
        Sub-headings like “For the sauce:” become ingredient groups; styling is
        ignored. Your AI provider is only used if none of it can be read.
      </p>
    </template>

    <template v-else-if="mode === 'archive'">
      <label class="field">
        <span>Export file</span>
        <input type="file"
               accept=".zip,.paprikarecipes,.paprikarecipe,.json,.txt,.md,application/zip,application/json"
               @change="onArchive" />
      </label>
      <p class="muted" style="font-size:0.85rem">
        Bring your whole collection from <strong>Mealie</strong>,
        <strong>Tandoor</strong> or <strong>Paprika</strong> — upload the app’s
        export file and every recipe in it is imported. Works completely offline:
        no key, no AI. Broken entries are skipped and listed, never fatal.
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
        {{ busy ? (mode === 'photo' ? 'Scanning…' : 'Importing…')
          : (mode === 'photo' ? 'Scan photo' : mode === 'archive' ? 'Import everything' : 'Import') }}
      </button>
    </div>
  </div>
</template>

<style scoped>
/* Confirms that the markup came along, since the textarea can only show the
   plain text — without this the richer import would be invisible magic. */
.rich-note {
  display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px;
  margin: 8px 0 0; font-size: 0.85rem; color: var(--muted);
}
.linky {
  border: 0; background: transparent; padding: 0; font: inherit;
  color: var(--accent-text); text-decoration: underline; cursor: pointer;
}


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
