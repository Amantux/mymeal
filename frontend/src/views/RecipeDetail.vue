<script setup>
import { ref, onMounted, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, apiUrl } from '../api'
import { useUI } from '../stores/ui'

const route = useRoute()
const router = useRouter()
const ui = useUI()

const recipe = ref(null)
const loading = ref(true)
const editing = ref(false)

// Edit buffers (ingredients/steps edited as one line per row).
const form = ref({})
const ingredientsText = ref('')
const stepsText = ref('')

async function load() {
  loading.value = true
  try {
    recipe.value = await api.get(`/recipes/${route.params.id}`)
  } catch (e) {
    ui.error(e.message)
  } finally {
    loading.value = false
  }
}
onMounted(load)

function startEdit() {
  const r = recipe.value
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
              <span v-for="tag in recipe.tags" :key="tag.id" class="chip">{{ tag.name }}</span>
            </div>
            <p v-if="recipe.sourceUrl" style="margin-top:10px">
              <a :href="recipe.sourceUrl" target="_blank" rel="noreferrer">Source ↗</a>
            </p>
          </div>
        </div>
      </div>

      <div class="card">
        <h2>Ingredients</h2>
        <ul v-if="recipe.ingredients.length" style="margin:0;padding-left:20px">
          <li v-for="ing in recipe.ingredients" :key="ing.id">{{ ing.display }}</li>
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
    </template>
  </template>
</template>
