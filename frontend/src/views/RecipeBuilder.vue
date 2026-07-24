<script setup>
// Full recipe creator. LLM-forward: "Draft with AI" fills the whole form from a
// one-line idea. Ingredients are edited as structured rows (qty·unit·food·note)
// via IngredientRows; "Tidy up with AI" refines free-text rows into clean
// food/note. Everything stays editable before saving — the model drafts, the
// human decides.
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import { useUI } from '../stores/ui'
import IngredientRows from '../components/IngredientRows.vue'

const router = useRouter()
const ui = useUI()

const form = ref({ name: '', description: '', servings: '', prepMinutes: '', cookMinutes: '', tags: '' })
const ingredients = ref([]) // [{quantity, unit, food, note}]
const stepsText = ref('')

const idea = ref('')
const drafting = ref(false)
const structuring = ref(false)
const saving = ref(false)

const lines = (t) => t.split('\n').map((l) => l.trim()).filter(Boolean)

function rowToDisplay(r) {
  const parts = [String(r.quantity ?? '').trim(), (r.unit || '').trim(), (r.food || '').trim()].filter(Boolean)
  let d = parts.join(' ')
  const note = (r.note || '').trim()
  if (note) d = d ? `${d}, ${note}` : note
  return d
}
// Rows worth saving: anything with a food or a quantity.
const filledRows = () => ingredients.value.filter(
  (r) => (r.food || '').trim() || String(r.quantity ?? '').trim(),
)

async function draft() {
  if (!idea.value.trim() || drafting.value) return
  drafting.value = true
  try {
    const p = await api.post('/ai/generate', {
      prompt: idea.value.trim(), servings: Number(form.value.servings) || 0,
    })
    form.value.name = p.name || form.value.name
    form.value.description = p.description || ''
    if (p.servings) form.value.servings = p.servings
    if (p.prepMinutes) form.value.prepMinutes = p.prepMinutes
    if (p.cookMinutes) form.value.cookMinutes = p.cookMinutes
    stepsText.value = (p.steps || []).map((s) => s.text).join('\n')
    form.value.tags = (p.tags || []).join(', ')
    // Turn the drafted ingredient lines into structured rows.
    const drafted = (p.ingredients || []).map((i) => i.display).filter(Boolean)
    if (drafted.length) {
      const res = await api.post('/recipes/parse', { lines: drafted })
      ingredients.value = res.ingredients.map((r) => ({
        quantity: r.quantity || '', unit: r.unit || '', food: r.food || '', note: '',
      }))
    }
    ui.toast('Draft ready — review and save')
  } catch (e) {
    ui.error(e.message)
  } finally {
    drafting.value = false
  }
}

async function structure() {
  const ls = filledRows().map(rowToDisplay).filter(Boolean)
  if (!ls.length || structuring.value) return
  structuring.value = true
  try {
    const res = await api.post('/ai/parse-ingredients', { lines: ls })
    ingredients.value = res.ingredients.map((r) => ({
      quantity: r.quantity || '', unit: r.unit || '', food: r.food || '', note: r.note || '',
    }))
    ui.toast(`Structured ${res.ingredients.length} ingredient${res.ingredients.length === 1 ? '' : 's'}`)
  } catch (e) {
    ui.error(e.message)
  } finally {
    structuring.value = false
  }
}

async function save() {
  if (!form.value.name.trim()) {
    ui.error('Give the recipe a name.')
    return
  }
  const ings = filledRows().map((r, position) => ({
    display: rowToDisplay(r), quantity: Number(r.quantity) || 0,
    unit: r.unit || '', food: r.food || '', note: r.note || '', position,
  }))
  saving.value = true
  try {
    const r = await api.post('/recipes', {
      name: form.value.name.trim(),
      description: form.value.description,
      servings: Number(form.value.servings) || 0,
      prepMinutes: Number(form.value.prepMinutes) || 0,
      cookMinutes: Number(form.value.cookMinutes) || 0,
      ingredients: ings,
      steps: lines(stepsText.value).map((text) => ({ text })),
      tags: form.value.tags.split(',').map((t) => t.trim()).filter(Boolean),
    })
    ui.toast('Recipe created')
    router.push(`/recipes/${r.id}`)
  } catch (e) {
    ui.error(e.message)
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="page-head">
    <h1>New recipe</h1>
    <div class="grow"></div>
    <button class="secondary" @click="router.push('/recipes')">Cancel</button>
    <button :disabled="saving" @click="save">{{ saving ? 'Saving…' : 'Save recipe' }}</button>
  </div>

  <!-- LLM draft: describe a dish, get a full editable recipe. -->
  <div class="card draft">
    <label class="field" style="margin:0">
      <span class="lbl">✨ Draft with AI</span>
      <div class="row">
        <input v-model="idea" class="fill" placeholder="e.g. a cozy vegetarian chili for 4"
          @keyup.enter="draft" />
        <button class="secondary" :disabled="drafting || !idea.trim()" @click="draft">
          {{ drafting ? 'Drafting…' : 'Draft' }}
        </button>
      </div>
      <span class="help">Fills everything below from your idea — edit before saving.</span>
    </label>
  </div>

  <div class="card">
    <h2>Details</h2>
    <label class="field"><span class="lbl">Name</span>
      <input v-model="form.name" class="fill" placeholder="Recipe name" /></label>
    <label class="field"><span class="lbl">Description</span>
      <textarea v-model="form.description" rows="2" class="fill"></textarea></label>
    <div class="row3">
      <label class="field"><span class="lbl">Servings</span>
        <input v-model="form.servings" type="number" min="0" class="fill" /></label>
      <label class="field"><span class="lbl">Prep (min)</span>
        <input v-model="form.prepMinutes" type="number" min="0" class="fill" /></label>
      <label class="field"><span class="lbl">Cook (min)</span>
        <input v-model="form.cookMinutes" type="number" min="0" class="fill" /></label>
    </div>
    <label class="field"><span class="lbl">Tags</span>
      <input v-model="form.tags" class="fill" placeholder="comma, separated, tags" /></label>
  </div>

  <div class="card">
    <IngredientRows v-model="ingredients" />
    <div class="row" style="margin-top:10px">
      <button class="ghost sm" :disabled="structuring || !filledRows().length" @click="structure">
        {{ structuring ? 'Structuring…' : '✨ Tidy up with AI' }}
      </button>
      <span class="hint">Splits each row into quantity · unit · food · note</span>
    </div>
  </div>

  <div class="card">
    <h2>Steps <span class="hint" style="font-weight:400">— one per line</span></h2>
    <textarea v-model="stepsText" rows="8" class="fill"
      placeholder="Preheat the oven to 200°C&#10;Mix the dry ingredients…"></textarea>
  </div>
</template>

<style scoped>
.draft { background: var(--accent-soft); border-color: var(--accent); }
.row3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
.hint { font-size: 0.78rem; color: var(--muted); font-weight: 500; }
@media (max-width: 560px) { .row3 { grid-template-columns: 1fr; } }
</style>
