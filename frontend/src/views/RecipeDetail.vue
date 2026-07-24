<script setup>
import { ref, onMounted, computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, apiUrl } from '../api'
import { useUI } from '../stores/ui'

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
const ingredientsText = ref('')
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

function startEdit() {
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
  ingredientsText.value = r.ingredients.map((i) => i.display).join('\n')
  stepsText.value = r.steps.map((s) => s.text).join('\n\n')
  editing.value = true
}

async function save() {
  const payload = {
    ...form.value,
    categoryIds: selectedCategoryIds.value,
    nutrition: nutritionForm.value,
    ingredients: ingredientsText.value
      .split('\n')
      .map((l) => l.trim())
      .filter(Boolean)
      .map((display, position) => ({ display, position })),
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
        <h2>Ingredients <span class="muted" style="font-weight:400">— one per line</span></h2>
        <textarea v-model="ingredientsText" rows="8" placeholder="2 cloves garlic, minced"></textarea>
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
