<script setup>
import { ref, onMounted, computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, apiUrl } from '../api'
import { useUI } from '../stores/ui'
import IngredientRows from '../components/IngredientRows.vue'

const route = useRoute()
const router = useRouter()
const ui = useUI()

const recipe = ref(null)
const loading = ref(true)
const editing = ref(false)

// Categories: the group's full list + the ids selected on this recipe (edit mode).
const allCategories = ref([])
const selectedCategoryIds = ref([])
const newCategoryName = ref('')

// Nutrition (per serving). Displayed as a facts grid; editable; AI-estimable.
const NUTRITION_FIELDS = [
  { key: 'calories', label: 'Calories', unit: '' },
  { key: 'protein', label: 'Protein', unit: ' g' },
  { key: 'carbs', label: 'Carbs', unit: ' g' },
  { key: 'fat', label: 'Fat', unit: ' g' },
  { key: 'fiber', label: 'Fiber', unit: ' g' },
  { key: 'sugar', label: 'Sugar', unit: ' g' },
  { key: 'sodium', label: 'Sodium', unit: ' mg' },
]
const nutritionForm = ref({})
const estimatingNutrition = ref(false)

async function estimateNutrition() {
  estimatingNutrition.value = true
  try {
    const res = await api.post(`/ai/nutrition/${recipe.value.id}`)
    recipe.value.nutrition = res.nutrition
    ui.toast(res.nutrition ? 'Nutrition estimated' : 'Could not estimate from these ingredients')
  } catch (e) {
    ui.error(e.message || 'Could not estimate nutrition')
  } finally {
    estimatingNutrition.value = false
  }
}

// Display-only ingredient view: scale to a chosen serving count and/or show
// weights. Never mutates the stored recipe (the backend does the transform).
const viewServings = ref(null)
const useWeight = ref(false)
const scaled = ref(null) // null → show the recipe's own ingredients
const shownIngredients = computed(() => scaled.value || recipe.value?.ingredients || [])

async function refreshView() {
  const r = recipe.value
  if (!r) return
  const base = r.servings || 0
  if ((viewServings.value === base || !viewServings.value) && !useWeight.value) {
    scaled.value = null
    return
  }
  const params = new URLSearchParams()
  if (viewServings.value && base) params.set('servings', String(viewServings.value))
  if (useWeight.value) params.set('units', 'weight')
  try {
    scaled.value = (await api.get(`/recipes/${r.id}?${params}`)).ingredients
  } catch {
    scaled.value = null
  }
}
watch([viewServings, useWeight], refreshView)

// Edit buffers (ingredients/steps edited as one line per row).
const form = ref({})
const editIngredients = ref([]) // structured rows for the edit-mode editor
const structuring = ref(false)

function rowToDisplay(r) {
  const parts = [String(r.quantity ?? '').trim(), (r.unit || '').trim(), (r.food || '').trim()].filter(Boolean)
  let d = parts.join(' ')
  const note = (r.note || '').trim()
  if (note) d = d ? `${d}, ${note}` : note
  return d
}
const filledRows = () => editIngredients.value.filter(
  (r) => (r.food || '').trim() || String(r.quantity ?? '').trim(),
)

// Turn stored ingredients into editor rows. Structured ones (with a food)
// round-trip exactly; legacy free-text lines drop their whole display into the
// food field so nothing is lost and the row can be restructured or AI-tidied.
function ingredientToRow(i) {
  if (i.food) {
    return { quantity: i.quantity || '', unit: i.unit?.name || '', food: i.food.name, note: i.note || '' }
  }
  return { quantity: '', unit: '', food: i.display || '', note: '' }
}

async function tidyIngredients() {
  const ls = filledRows().map(rowToDisplay).filter(Boolean)
  if (!ls.length || structuring.value) return
  structuring.value = true
  try {
    const res = await api.post('/ai/parse-ingredients', { lines: ls })
    editIngredients.value = res.ingredients.map((r) => ({
      quantity: r.quantity || '', unit: r.unit || '', food: r.food || '', note: r.note || '',
    }))
    ui.toast('Tidied ingredients')
  } catch (e) {
    ui.error(e.message || 'Could not tidy ingredients')
  } finally {
    structuring.value = false
  }
}
const stepsText = ref('')

async function load() {
  loading.value = true
  try {
    recipe.value = await api.get(`/recipes/${route.params.id}`)
    viewServings.value = recipe.value.servings || null // reset the view to base
    useWeight.value = false
    scaled.value = null
  } catch (e) {
    ui.error(e.message)
  } finally {
    loading.value = false
  }
}
onMounted(async () => {
  await load()
  try { allCategories.value = (await api.get('/categories')) || [] } catch { /* optional */ }
})

async function createCategory() {
  const name = newCategoryName.value.trim()
  if (!name) return
  try {
    const cat = await api.post('/categories', { name })
    allCategories.value.push(cat)
    selectedCategoryIds.value.push(cat.id)
    newCategoryName.value = ''
  } catch (e) {
    ui.error(e.message || 'Could not create category')
  }
}

async function startEdit() {
  const r = recipe.value
  selectedCategoryIds.value = (r.categories || []).map((c) => c.id)
  nutritionForm.value = { ...(r.nutrition || {}) }
  form.value = {
    name: r.name,
    description: r.description,
    recipeYield: r.recipeYield,
    servings: r.servings,
    prepMinutes: r.prepMinutes,
    cookMinutes: r.cookMinutes,
    totalMinutes: r.totalMinutes,
    sourceUrl: r.sourceUrl,
    notes: r.notes,
  }
  const rows = r.ingredients.map(ingredientToRow)
  // Legacy free-text ingredients (no structured food) go through the
  // deterministic parser so the editor shows tidy qty·unit·food rows, not the
  // whole line crammed in the food field. Falls back to display-in-food.
  const legacy = r.ingredients
    .map((i, idx) => (!i.food && i.display ? { idx, display: i.display } : null))
    .filter(Boolean)
  if (legacy.length) {
    try {
      const res = await api.post('/recipes/parse', { lines: legacy.map((l) => l.display) })
      res.ingredients.forEach((p, k) => {
        const row = rows[legacy[k].idx]
        row.quantity = p.quantity || ''
        row.unit = p.unit || ''
        row.food = p.food || row.food
      })
    } catch { /* keep the display-in-food fallback */ }
  }
  editIngredients.value = rows
  stepsText.value = r.steps.map((s) => s.text).join('\n\n')
  editing.value = true
}

async function save() {
  const payload = {
    ...form.value,
    categoryIds: selectedCategoryIds.value,
    nutrition: nutritionForm.value,
    ingredients: filledRows().map((r, position) => ({
      display: rowToDisplay(r), quantity: Number(r.quantity) || 0,
      unit: r.unit || '', food: r.food || '', note: r.note || '', position,
    })),
    steps: stepsText.value
      .split('\n\n')
      .map((l) => l.trim())
      .filter(Boolean)
      .map((text, position) => ({ text, position })),
  }
  try {
    recipe.value = await api.put(`/recipes/${recipe.value.id}`, payload)
    editing.value = false
    ui.toast('Saved')
  } catch (e) {
    ui.error(e.message)
  }
}

const shoppingBusy = ref(false)
async function addToShopping() {
  shoppingBusy.value = true
  try {
    // Add to the household's first list, creating a default one if none exists.
    // /from-recipes consolidates duplicate foods across everything on the list.
    const lists = (await api.get('/shopping-lists')).items || []
    const list = lists[0] || (await api.post('/shopping-lists', { name: 'Shopping List' }))
    const res = await api.post(`/shopping-lists/${list.id}/from-recipes`, {
      recipeIds: [recipe.value.id],
    })
    ui.toast(`Added ${res.added} item${res.added === 1 ? '' : 's'} to ${list.name}`)
  } catch (e) {
    ui.error(e.message || 'Could not add to shopping list')
  } finally {
    shoppingBusy.value = false
  }
}

// --- Share & export (feature #10) ---
const shareUrl = computed(() =>
  recipe.value?.shareToken
    ? `${window.location.href.split('#')[0]}#/share/${recipe.value.shareToken}`
    : ''
)
async function createShare() {
  try {
    const res = await api.post(`/recipes/${recipe.value.id}/share`)
    recipe.value.shareToken = res.shareToken
    ui.toast('Public link created')
  } catch (e) { ui.error(e.message || 'Could not create link') }
}
async function stopShare() {
  try {
    await api.del(`/recipes/${recipe.value.id}/share`)
    recipe.value.shareToken = null
    ui.toast('Sharing stopped')
  } catch (e) { ui.error(e.message || 'Could not stop sharing') }
}
function copyShare() {
  navigator.clipboard?.writeText(shareUrl.value).then(() => ui.toast('Link copied'), () => {})
}
function slugify(s) {
  return (s || 'recipe').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'recipe'
}
function download(content, filename, type) {
  const blob = new Blob([content], { type })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}
function recipeToMarkdown(r) {
  const lines = [`# ${r.name}`, '']
  if (r.description) lines.push(r.description, '')
  const meta = []
  if (r.servings) meta.push(`**Servings:** ${r.servings}`)
  if (r.totalMinutes) meta.push(`**Time:** ${r.totalMinutes} min`)
  if (meta.length) lines.push(meta.join(' · '), '')
  lines.push('## Ingredients', '')
  ;(r.ingredients || []).forEach((i) => lines.push(`- ${i.display}`))
  lines.push('', '## Steps', '')
  ;(r.steps || []).forEach((s, n) => lines.push(`${n + 1}. ${s.text}`))
  if (r.notes) lines.push('', '## Notes', '', r.notes)
  return lines.join('\n')
}
function exportMarkdown() {
  download(recipeToMarkdown(recipe.value), `${slugify(recipe.value.name)}.md`, 'text/markdown')
}
function copyMarkdown() {
  navigator.clipboard
    ?.writeText(recipeToMarkdown(recipe.value))
    .then(() => ui.toast('Markdown copied'), () => ui.error('Could not copy'))
}
function emailRecipe() {
  const r = recipe.value
  // Lead with the public link (if shared) so even an email client that truncates
  // a long mailto body still carries a working link; then the full Markdown.
  const link = shareUrl.value ? `View it online: ${shareUrl.value}\n\n` : ''
  const subject = `Recipe: ${r.name}`
  const body = `${link}${recipeToMarkdown(r)}`
  window.location.href =
    `mailto:?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`
}
function exportJson() {
  const r = recipe.value
  const out = {
    name: r.name, description: r.description, servings: r.servings,
    recipeYield: r.recipeYield, prepMinutes: r.prepMinutes, cookMinutes: r.cookMinutes,
    totalMinutes: r.totalMinutes, sourceUrl: r.sourceUrl, notes: r.notes,
    nutrition: r.nutrition || null,
    tags: (r.tags || []).map((t) => t.name),
    categories: (r.categories || []).map((c) => c.name),
    ingredients: (r.ingredients || []).map((i) => i.display),
    steps: (r.steps || []).map((s) => s.text),
  }
  download(JSON.stringify(out, null, 2), `${slugify(r.name)}.json`, 'application/json')
}

async function toggleFavorite() {
  recipe.value = await api.put(`/recipes/${recipe.value.id}`, {
    isFavorite: !recipe.value.isFavorite,
  })
}

async function remove() {
  if (!confirm(`Delete "${recipe.value.name}"? This cannot be undone.`)) return
  await api.del(`/recipes/${recipe.value.id}`)
  ui.toast('Recipe deleted')
  router.push('/recipes')
}

async function uploadImage(e) {
  const file = e.target.files[0]
  if (!file) return
  const fd = new FormData()
  fd.append('image', file)
  try {
    recipe.value = await api.upload(`/recipes/${recipe.value.id}/image`, fd)
    ui.toast('Image updated')
  } catch (err) {
    ui.error(err.message)
  }
}

// Cache-bust the image after upload so the new one shows. NOTE: a plain <img>
// can't send the bearer token, so images display when running behind Home
// Assistant ingress (auth disabled). Token-authenticated blob loading lands in
// a later milestone.
const imageSrc = computed(() =>
  recipe.value?.image ? apiUrl(`/recipes/${recipe.value.id}/image`) + `?t=${recipe.value.updatedAt}` : null
)
</script>

<template>
  <div v-if="loading" class="skeleton" style="height:300px"></div>

  <template v-else-if="recipe">
    <div class="page-head">
      <button class="ghost" @click="router.push('/recipes')">← Recipes</button>
      <div class="grow"></div>
      <template v-if="!editing">
        <button v-if="recipe.steps.length" @click="router.push(`/recipes/${recipe.id}/cook`)">
          👨‍🍳 Cook
        </button>
        <button v-if="recipe.ingredients.length" class="secondary" :disabled="shoppingBusy"
          @click="addToShopping">🛒 Add to list</button>
        <button class="secondary" @click="toggleFavorite">
          {{ recipe.isFavorite ? '★ Favorited' : '☆ Favorite' }}
        </button>
        <button class="secondary" @click="startEdit">Edit</button>
        <button class="danger" @click="remove">Delete</button>
      </template>
      <template v-else>
        <button class="secondary" @click="editing = false">Cancel</button>
        <button @click="save">Save</button>
      </template>
    </div>

    <!-- VIEW MODE -->
    <template v-if="!editing">
      <div class="card">
        <div class="row top" style="gap:20px">
          <div v-if="imageSrc" class="thumb" style="width:200px;height:150px;border-radius:var(--radius-sm);overflow:hidden;flex-shrink:0">
            <img :src="imageSrc" alt="" style="width:100%;height:100%;object-fit:cover" />
          </div>
          <div class="fill">
            <h1>{{ recipe.name }}</h1>
            <p class="muted">{{ recipe.description }}</p>
            <div class="row wrap" style="gap:8px;margin-top:8px">
              <span v-if="recipe.servings" class="badge">🍽️ {{ recipe.servings }} servings</span>
              <span v-if="recipe.totalMinutes" class="badge tnum">⏱️ {{ recipe.totalMinutes }} min</span>
              <span v-for="c in recipe.categories" :key="c.id" class="chip cat">{{ c.name }}</span>
              <span v-for="tag in recipe.tags" :key="tag.id" class="chip">{{ tag.name }}</span>
            </div>
            <p v-if="recipe.sourceUrl" style="margin-top:10px">
              <a :href="recipe.sourceUrl" target="_blank" rel="noreferrer">Source ↗</a>
            </p>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="page-head" style="margin-bottom:10px">
          <h2>Ingredients</h2>
          <div class="grow"></div>
          <div v-if="recipe.ingredients.length" class="ing-tools">
            <template v-if="recipe.servings">
              <button class="secondary sm" aria-label="Fewer servings"
                @click="viewServings = Math.max(1, (viewServings || recipe.servings) - 1)">−</button>
              <span class="tnum" style="min-width:5ch;text-align:center"
                :title="`${viewServings || recipe.servings} servings`">🍽 {{ viewServings || recipe.servings }}</span>
              <button class="secondary sm" aria-label="More servings"
                @click="viewServings = (viewServings || recipe.servings) + 1">＋</button>
            </template>
            <button class="secondary sm" :class="{ active: useWeight }"
              :aria-pressed="useWeight" @click="useWeight = !useWeight">⚖️ Weights</button>
          </div>
        </div>
        <ul v-if="shownIngredients.length" style="margin:0;padding-left:20px">
          <li v-for="ing in shownIngredients" :key="ing.id">{{ ing.display }}</li>
        </ul>
        <p v-else class="muted">No ingredients listed.</p>
      </div>

      <div class="card">
        <h2>Steps</h2>
        <ol v-if="recipe.steps.length" style="margin:0;padding-left:20px" class="stack">
          <li v-for="s in recipe.steps" :key="s.id">{{ s.text }}</li>
        </ol>
        <p v-else class="muted">No steps listed.</p>
      </div>

      <div v-if="recipe.notes" class="card">
        <h2>Notes</h2>
        <p style="white-space:pre-wrap;margin:0">{{ recipe.notes }}</p>
      </div>

      <div class="card">
        <div class="page-head" style="margin-bottom:10px">
          <h2>Nutrition <span class="muted" style="font-weight:400">— per serving</span></h2>
          <div class="grow"></div>
          <button class="secondary sm" :disabled="estimatingNutrition" @click="estimateNutrition">
            {{ estimatingNutrition ? 'Estimating…' : '✨ Estimate with AI' }}
          </button>
        </div>
        <div v-if="recipe.nutrition" class="nutri-grid">
          <template v-for="f in NUTRITION_FIELDS" :key="f.key">
            <div v-if="recipe.nutrition[f.key] != null" class="nutri-cell">
              <div class="nutri-val tnum">{{ recipe.nutrition[f.key] }}{{ f.unit }}</div>
              <div class="nutri-lbl">{{ f.label }}</div>
            </div>
          </template>
        </div>
        <p v-else class="muted" style="margin:0">
          No nutrition yet — estimate it with AI, or add it in edit mode.
        </p>
      </div>

      <div class="card">
        <h2>Share &amp; export</h2>
        <div v-if="recipe.shareToken" class="share-live">
          <p class="muted" style="font-size:0.85rem;margin:8px 0">
            Anyone with this link can view a read-only copy — no account needed.
          </p>
          <div class="row" style="gap:8px">
            <input class="fill" :value="shareUrl" readonly @focus="$event.target.select()" />
            <button class="secondary" @click="copyShare">Copy</button>
            <button class="secondary danger" @click="stopShare">Stop sharing</button>
          </div>
        </div>
        <div v-else class="row" style="margin:8px 0 12px">
          <button class="secondary" @click="createShare">🔗 Create public link</button>
        </div>
        <div class="row" style="gap:8px;flex-wrap:wrap">
          <button class="secondary sm" @click="emailRecipe">✉️ Email</button>
          <button class="secondary sm" @click="copyMarkdown">⧉ Copy Markdown</button>
          <button class="secondary sm" @click="exportMarkdown">⬇ Markdown</button>
          <button class="secondary sm" @click="exportJson">⬇ JSON</button>
        </div>
      </div>
    </template>

    <!-- EDIT MODE -->
    <template v-else>
      <div class="card">
        <label class="field"><span>Name</span><input v-model="form.name" /></label>
        <label class="field"><span>Description</span><textarea v-model="form.description" rows="2"></textarea></label>
        <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(140px,1fr))">
          <label class="field"><span>Servings</span><input v-model.number="form.servings" type="number" min="0" /></label>
          <label class="field"><span>Prep (min)</span><input v-model.number="form.prepMinutes" type="number" min="0" /></label>
          <label class="field"><span>Cook (min)</span><input v-model.number="form.cookMinutes" type="number" min="0" /></label>
          <label class="field"><span>Total (min)</span><input v-model.number="form.totalMinutes" type="number" min="0" /></label>
        </div>
        <label class="field"><span>Source URL</span><input v-model="form.sourceUrl" /></label>
        <label class="field"><span>Image</span><input type="file" accept="image/*" @change="uploadImage" /></label>
        <div class="field">
          <span>Categories</span>
          <div v-if="allCategories.length" class="cat-picker">
            <label v-for="c in allCategories" :key="c.id" class="cat-opt">
              <input type="checkbox" :value="c.id" v-model="selectedCategoryIds" />
              <span>{{ c.name }}</span>
            </label>
          </div>
          <p v-else class="muted" style="margin:4px 0 0;font-size:0.85rem">No categories yet — add one below.</p>
          <div class="row" style="margin-top:8px;gap:8px">
            <input v-model="newCategoryName" class="fill" placeholder="New category (e.g. Weeknight dinners)"
              @keydown.enter.prevent="createCategory" />
            <button type="button" class="secondary" @click="createCategory">Add</button>
          </div>
        </div>
      </div>
      <div class="card">
        <IngredientRows v-model="editIngredients" />
        <div class="row" style="margin-top:10px">
          <button class="ghost sm" :disabled="structuring || !filledRows().length" @click="tidyIngredients">
            {{ structuring ? 'Structuring…' : '✨ Tidy up with AI' }}
          </button>
          <span class="muted" style="font-size:0.78rem">Splits each row into quantity · unit · food · note</span>
        </div>
      </div>
      <div class="card">
        <h2>Steps <span class="muted" style="font-weight:400">— blank line between steps</span></h2>
        <textarea v-model="stepsText" rows="10"></textarea>
      </div>
      <div class="card">
        <label class="field"><span>Notes</span><textarea v-model="form.notes" rows="3"></textarea></label>
      </div>
      <div class="card">
        <h2>Nutrition <span class="muted" style="font-weight:400">— per serving, leave blank if unknown</span></h2>
        <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(120px,1fr))">
          <label v-for="f in NUTRITION_FIELDS" :key="f.key" class="field">
            <span>{{ f.label }}{{ f.unit }}</span>
            <input v-model.number="nutritionForm[f.key]" type="number" min="0" step="any" />
          </label>
        </div>
      </div>
    </template>
  </template>
</template>

<style scoped>
.ing-tools { display: flex; align-items: center; gap: 8px; }
.ing-tools .active { background: var(--accent); color: #fff; border-color: var(--accent); }
.chip.cat { background: var(--accent-soft); border-color: var(--accent); }
.cat-picker { display: flex; flex-wrap: wrap; gap: 8px 16px; margin-top: 4px; }
.cat-opt { display: inline-flex; align-items: center; gap: 6px; font-weight: 400; cursor: pointer; }
.cat-opt input { width: auto; }
.nutri-grid { display: flex; flex-wrap: wrap; gap: 20px 28px; }
.nutri-cell { min-width: 64px; }
.nutri-val { font-size: 1.4rem; font-weight: 700; }
.nutri-lbl { font-size: 0.78rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.03em; }
</style>
