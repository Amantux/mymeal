<script setup>
// Full recipe creator. LLM-forward: "Draft with AI" fills the whole form from a
// one-line idea; "Structure with AI" parses the ingredient lines into
// qty/unit/food (matched to the group's foods on save). Everything stays
// editable before saving — the model drafts, the human decides.
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import { useUI } from '../stores/ui'

const router = useRouter()
const ui = useUI()

const form = ref({ name: '', description: '', servings: '', prepMinutes: '', cookMinutes: '', tags: '' })
const ingredientsText = ref('')
const stepsText = ref('')
const structured = ref(null) // AI-parsed rows, used at save if they still match the text

const idea = ref('')
const drafting = ref(false)
const structuring = ref(false)
const saving = ref(false)

const lines = (t) => t.split('\n').map((l) => l.trim()).filter(Boolean)

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
    ingredientsText.value = (p.ingredients || []).map((i) => i.display).join('\n')
    stepsText.value = (p.steps || []).map((s) => s.text).join('\n')
    form.value.tags = (p.tags || []).join(', ')
    structured.value = null
    ui.toast('Draft ready — review and save')
  } catch (e) {
    ui.error(e.message)
  } finally {
    drafting.value = false
  }
}

async function structure() {
  const ls = lines(ingredientsText.value)
  if (!ls.length || structuring.value) return
  structuring.value = true
  try {
    const res = await api.post('/ai/parse-ingredients', { lines: ls })
    structured.value = res.ingredients
    ingredientsText.value = res.ingredients.map((i) => i.display).join('\n')
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
  const ls = lines(ingredientsText.value)
  // Use the AI-parsed rows only if the text hasn't changed since parsing;
  // otherwise send plain lines (the server parses qty/unit on save anyway).
  const parsedMatches =
    structured.value &&
    structured.value.length === ls.length &&
    structured.value.every((r, i) => (r.display || '').trim() === ls[i])
  const ingredients = parsedMatches
    ? structured.value.map((r) => ({
        display: r.display, quantity: r.quantity, unit: r.unit, food: r.food, note: r.note,
      }))
    : ls.map((display) => ({ display }))

  saving.value = true
  try {
    const r = await api.post('/recipes', {
      name: form.value.name.trim(),
      description: form.value.description,
      servings: Number(form.value.servings) || 0,
      prepMinutes: Number(form.value.prepMinutes) || 0,
      cookMinutes: Number(form.value.cookMinutes) || 0,
      ingredients,
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
      <span class="help">Fills the form below from your idea — you can edit everything before saving.</span>
    </label>
  </div>

  <div class="card">
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

    <label class="field">
      <span class="lbl row" style="justify-content:space-between">
        Ingredients <span class="hint">one per line</span>
      </span>
      <textarea v-model="ingredientsText" rows="7" class="fill"
        placeholder="2 cups flour&#10;1 tsp salt&#10;3 eggs"></textarea>
    </label>
    <div class="row" style="margin:-4px 0 8px">
      <button class="ghost sm" :disabled="structuring || !ingredientsText.trim()" @click="structure">
        {{ structuring ? 'Structuring…' : '✨ Structure ingredients with AI' }}
      </button>
      <span v-if="structured" class="hint">✓ parsed into quantity · unit · food</span>
    </div>

    <label class="field">
      <span class="lbl row" style="justify-content:space-between">Steps <span class="hint">one per line</span></span>
      <textarea v-model="stepsText" rows="7" class="fill"
        placeholder="Preheat the oven to 200°C&#10;Mix the dry ingredients…"></textarea>
    </label>

    <label class="field"><span class="lbl">Tags</span>
      <input v-model="form.tags" class="fill" placeholder="comma, separated, tags" /></label>
  </div>
</template>

<style scoped>
.draft { background: var(--accent-soft); border-color: var(--accent); }
.row3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
.hint { font-size: 0.78rem; color: var(--muted); font-weight: 500; }
@media (max-width: 560px) { .row3 { grid-template-columns: 1fr; } }
</style>
