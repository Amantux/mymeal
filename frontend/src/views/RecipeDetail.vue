<script setup>
import { ref, onMounted, computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, apiUrl, mediaUrl } from '../api'
import { useUI } from '../stores/ui'
import IngredientRows from '../components/IngredientRows.vue'
import StepRows from '../components/StepRows.vue'

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
// Linked sub-recipes (components) get their own section; plain ingredients the list.
const plainIngredients = computed(() => shownIngredients.value.filter((i) => !i.refRecipe))
const componentIngredients = computed(() => shownIngredients.value.filter((i) => i.refRecipe))

// True only while the list is showing computed amounts rather than the recipe's
// own, so the reader is told when numbers aren't what the author wrote.
// Independent of the weight toggle: servings scaling is still in effect there,
// so the reader still needs telling that these aren't the recipe's own amounts.
const isScaledView = computed(() =>
  !!scaled.value && !!viewServings.value
  && viewServings.value !== recipe.value?.servings)

function amountOf(ing) {
  return [ing.amountText, ing.unitText].filter(Boolean).join(' ')
}
// Counted once for the scale note rather than tagged per row: a row with no
// amount already shows an empty amount column, so a per-row chip restated it
// while stealing width from the ingredient text at phone size.
const unscaledCount = computed(() => plainIngredients.value.filter((i) => i.scaled === false).length)
function resetServings() { viewServings.value = recipe.value?.servings || null }

// Split the ingredient into what you scan (the amount) and what you read (the
// rest), losing nothing. `restText` is `display` minus its leading amount, so
// every word the user typed survives — rebuilding from food.name would silently
// drop anything canonicalized away on save ("granulated sugar" -> "sugar") and a
// legacy row with no linked food at all would render blank.
//
// The note is peeled off only when the string demonstrably ends with it, so it
// can be styled as the aside it is instead of being comma-spliced into the
// ingredient's name. Anything we can't attribute stays inline, untouched.
function splitOne(ing) {
  let rest = ing.restText ?? ing.display ?? ''
  const note = (ing.note || '').trim()
  // Weight mode appends " (1191 g)" to the line. Hold it aside BEFORE testing for
  // the note, or the test fails and the note silently reverts to being
  // comma-spliced — on exactly the rows a scale user is reading. Only peeled in
  // weight mode, so a food that genuinely ends in brackets is untouched otherwise.
  let weight = ''
  if (useWeight.value) {
    const m = rest.match(/\s*(\([^()]*\))\s*$/)
    if (m) {
      weight = m[1]
      rest = rest.slice(0, m.index)
    }
  }
  // Compare the tail at its ORIGINAL length. Slicing by note.length after a
  // toLowerCase() match is wrong for characters whose lowercase is longer
  // ('İ' -> 2 chars), which shifted the cut and ate a letter of the food name.
  const tail = rest.slice(-(note.length + 2))
  if (note && tail.toLowerCase() === `, ${note.toLowerCase()}`) {
    return { amount: amountOf(ing), food: rest.slice(0, rest.length - tail.length), note, weight }
  }
  return { amount: amountOf(ing), food: rest, note: '', weight }
}
// Mapped once per render rather than calling the splitter twice per row in the
// template (once for the food, once for the note).
const shownRows = computed(() => plainIngredients.value.map((ing) => ({
  id: ing.id, ...splitOne(ing),
})))

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
  // The variety belongs in front of the food, the way a person writes it:
  // "2 tsp Vietnamese cinnamon", not "2 tsp cinnamon, Vietnamese".
  const food = [(r.qualifier || '').trim(), (r.food || '').trim()].filter(Boolean).join(' ')
  const parts = [String(r.quantity ?? '').trim(), (r.unit || '').trim(), food].filter(Boolean)
  let d = parts.join(' ')
  const note = (r.note || '').trim()
  if (note) d = d ? `${d}, ${note}` : note
  return d
}
const filledRows = () => editIngredients.value.filter(
  (r) => (r.food || '').trim() || String(r.quantity ?? '').trim() || r.refRecipeId,
)

// Turn stored ingredients into editor rows. Structured ones (with a food)
// round-trip exactly; legacy free-text lines drop their whole display into the
// food field so nothing is lost and the row can be restructured or AI-tidied.
// The editor rebuilds `display` from these fields on every save, so anything
// missing here is DESTROYED the first time someone edits an unrelated field.
// That is why the qualifier has to round-trip before anything starts writing it.
function ingredientToRow(i) {
  if (i.refRecipe) {
    return { quantity: i.quantity || '', unit: i.unit?.name || '', food: i.refRecipe.name,
             note: i.note || '', qualifier: i.qualifier || '',
             refRecipeId: i.refRecipe.id, refRecipeName: i.refRecipe.name }
  }
  if (i.food) {
    return { quantity: i.quantity || '', unit: i.unit?.name || '', food: i.food.name,
             note: i.note || '', qualifier: i.qualifier || '' }
  }
  return { quantity: '', unit: '', food: i.display || '', note: '', qualifier: i.qualifier || '' }
}

async function tidyIngredients() {
  const ls = filledRows().map(rowToDisplay).filter(Boolean)
  if (!ls.length || structuring.value) return
  structuring.value = true
  try {
    const res = await api.post('/ai/parse-ingredients', { lines: ls })
    editIngredients.value = res.ingredients.map((r) => ({
      quantity: r.quantity || '', unit: r.unit || '', food: r.food || '',
      note: r.note || '', qualifier: r.qualifier || '',
    }))
    ui.toast('Tidied ingredients')
  } catch (e) {
    ui.error(e.message || 'Could not tidy ingredients')
  } finally {
    structuring.value = false
  }
}
const editSteps = ref([]) // [{text}]

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
  if (recipe.value) await loadVersions()
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
    cookTemperatureC: r.cookTemperatureC,
    sourceUrl: r.sourceUrl,
    notes: r.notes,
  }
  const rows = r.ingredients.map(ingredientToRow)
  // Legacy free-text ingredients (no structured food) go through the
  // deterministic parser so the editor shows tidy qty·unit·food rows, not the
  // whole line crammed in the food field. Falls back to display-in-food.
  const legacy = r.ingredients
    .map((i, idx) => (!i.food && !i.refRecipe && i.display ? { idx, display: i.display } : null))
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
  editSteps.value = r.steps.map((s) => ({ text: s.text }))
  editing.value = true
}

async function save() {
  const payload = {
    ...form.value,
    categoryIds: selectedCategoryIds.value,
    nutrition: nutritionForm.value,
    ingredients: filledRows().map((r, position) => ({
      display: rowToDisplay(r), quantity: Number(r.quantity) || 0,
      unit: r.unit || '', food: r.food || '', note: r.note || '',
      qualifier: r.qualifier || '', position,
      refRecipeId: r.refRecipeId || undefined,
    })),
    steps: editSteps.value
      .map((s) => (s.text || '').trim())
      .filter(Boolean)
      .map((text, position) => ({ text, position })),
  }
  try {
    if (editingVersionId.value) {
      // Editing an experiment: save into the version's snapshot, NOT the live recipe.
      await api.put(`/recipes/${recipe.value.id}/versions/${editingVersionId.value}`, payload)
      editingVersionId.value = null
      editing.value = false
      await loadVersions()
      ui.toast('Experiment saved')
    } else {
      recipe.value = await api.put(`/recipes/${recipe.value.id}`, payload)
      editing.value = false
      await loadVersions()   // the pre-edit auto snapshot now exists
      ui.toast('Saved')
    }
  } catch (e) {
    ui.error(e.message)
  }
}

// --- Versions & experiments -------------------------------------------------
const versions = ref([])
const editingVersionId = ref(null)   // set → the editor targets an experiment snapshot

async function loadVersions() {
  try {
    const items = (await api.get(`/recipes/${recipe.value.id}/versions`)).items || []
    // Seed editable feedback buffers so the inputs show any saved rating/notes.
    versions.value = items.map((v) => ({ ...v, _rating: v.rating ?? undefined, _feedback: v.feedback || '' }))
  } catch { versions.value = [] }
}

function loadBuffersFrom(snap) {
  form.value = {
    name: snap.name, description: snap.description, recipeYield: snap.recipeYield,
    servings: snap.servings, prepMinutes: snap.prepMinutes, cookMinutes: snap.cookMinutes,
    totalMinutes: snap.totalMinutes, cookTemperatureC: snap.cookTemperatureC,
    sourceUrl: snap.sourceUrl, notes: snap.notes,
  }
  nutritionForm.value = { ...(snap.nutrition || {}) }
  selectedCategoryIds.value = snap.categoryIds || []
  editIngredients.value = (snap.ingredients || []).map((i) =>
    i.refRecipeId
      ? { quantity: i.quantity || '', unit: i.unit || '', food: i.food || i.display || 'component',
          note: i.note || '', qualifier: i.qualifier || '',
          refRecipeId: i.refRecipeId, refRecipeName: i.food || i.display || 'recipe' }
      : { quantity: i.quantity || '', unit: i.unit || '', food: i.food || i.display || '',
          note: i.note || '', qualifier: i.qualifier || '' })
  editSteps.value = (snap.steps || []).map((s) => ({ text: s.text }))
}

async function startExperiment() {
  const label = prompt('Name this experiment (e.g. "More caramelised onions")')
  if (label === null) return
  try {
    const v = await api.post(`/recipes/${recipe.value.id}/versions`, { label: label || 'Experiment' })
    await loadVersions()
    ui.toast('Experiment started — edit it, cook it, then promote or discard')
    editExperiment(v)
  } catch (e) { ui.error(e.message || 'Could not start experiment') }
}

async function editExperiment(v) {
  try {
    const full = v.snapshot ? v : await api.get(`/recipes/${recipe.value.id}/versions/${v.id}`)
    loadBuffersFrom(full.snapshot || {})
    editingVersionId.value = v.id
    editing.value = true
    window.scrollTo({ top: 0, behavior: 'smooth' })
  } catch (e) { ui.error(e.message || 'Could not open experiment') }
}

async function promoteVersion(v) {
  const what = v.kind === 'experiment' ? 'promote this experiment into' : 'restore this version to'
  if (!confirm(`This will ${what} the live recipe (a snapshot of the current version is kept first). Continue?`)) return
  try {
    const path = v.kind === 'experiment' ? 'promote' : 'restore'
    recipe.value = await api.post(`/recipes/${recipe.value.id}/versions/${v.id}/${path}`)
    await load()
    await loadVersions()
    ui.toast(v.kind === 'experiment' ? 'Experiment promoted' : 'Version restored')
  } catch (e) { ui.error(e.message || 'Could not apply version') }
}

async function discardVersion(v) {
  if (!confirm('Delete this version?')) return
  try {
    await api.del(`/recipes/${recipe.value.id}/versions/${v.id}`)
    await loadVersions()
  } catch (e) { ui.error(e.message || 'Could not delete version') }
}

async function saveFeedback(v) {
  try {
    await api.post(`/recipes/${recipe.value.id}/versions/${v.id}/feedback`,
      { rating: v._rating ?? v.rating, feedback: v._feedback ?? v.feedback })
    await loadVersions()
    ui.toast('Feedback saved')
  } catch (e) { ui.error(e.message || 'Could not save feedback') }
}

const experiments = computed(() => versions.value.filter((v) => v.kind === 'experiment'))
const history = computed(() => versions.value.filter((v) => v.kind === 'auto'))
function fmtDate(s) { try { return new Date(s).toLocaleString() } catch { return s } }

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
    totalMinutes: r.totalMinutes, cookTemperatureC: r.cookTemperatureC,
    sourceUrl: r.sourceUrl, notes: r.notes,
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
// --- How-to videos -------------------------------------------------------
// A video is either a link (YouTube/Vimeo embed inline, anything else opens in
// a new tab) or a file we uploaded and can play here. The server decides which
// is which — the UI never builds an embed URL from a raw address.
const newVideoUrl = ref('')
const newVideoTitle = ref('')
const addingVideo = ref(false)
const videoError = ref('')
const videoFile = ref(null)

const videos = computed(() => recipe.value?.videos || [])

function videoHost(url) {
  try { return new URL(url).hostname.replace(/^www\./, '') } catch { return 'the web' }
}

function videoSrc(v) {
  return v.streamUrl ? mediaUrl(v.streamUrl) : null
}

async function addVideoLink() {
  if (!newVideoUrl.value.trim()) return
  addingVideo.value = true
  videoError.value = ''
  try {
    await api.post(`/recipes/${recipe.value.id}/videos`,
      { url: newVideoUrl.value.trim(), title: newVideoTitle.value.trim() })
    newVideoUrl.value = ''
    newVideoTitle.value = ''
    await load()
    ui.toast('Video added')
  } catch (e) {
    // Stays on screen next to the field rather than as a toast that vanishes:
    // the message says which part of the link is wrong.
    videoError.value = e.message || 'Could not add that video'
  } finally {
    addingVideo.value = false
  }
}

async function uploadVideo(ev) {
  const file = ev.target.files?.[0]
  if (!file) return
  addingVideo.value = true
  videoError.value = ''
  try {
    const fd = new FormData()
    fd.append('file', file)
    if (newVideoTitle.value.trim()) fd.append('title', newVideoTitle.value.trim())
    await api.upload(`/recipes/${recipe.value.id}/videos`, fd)
    newVideoTitle.value = ''
    await load()
    ui.toast('Video uploaded')
  } catch (e) {
    // Uploads hit the size ceiling far more often than photos do, so this
    // path must never fail silently.
    videoError.value = e.message === 'upload too large'
      ? 'That video is larger than this add-on allows. Link to it instead, or raise max_upload_mb in the add-on configuration.'
      : (e.message || 'Could not upload that video')
  } finally {
    addingVideo.value = false
    ev.target.value = ''
  }
}

async function removeVideo(v) {
  if (!confirm(`Remove “${v.title || 'this video'}” from this recipe? The recipe itself is not affected.`)) return
  try {
    await api.del(`/recipes/${recipe.value.id}/videos/${v.id}`)
    await load()
    ui.toast('Video removed')
  } catch (e) {
    ui.error(e.message || 'Could not remove that video')
  }
}

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
        <span v-if="editingVersionId" class="badge" style="align-self:center">✎ Editing experiment</span>
        <button class="secondary" @click="editing = false; editingVersionId = null">Cancel</button>
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
              <span v-if="recipe.cookTemperature" class="badge tnum">🌡️ {{ recipe.cookTemperature }}</span>
              <span v-for="c in recipe.categories" :key="c.id" class="chip">{{ c.name }}</span>
              <span v-for="tag in recipe.tags" :key="tag.id" class="badge">{{ tag.name }}</span>
            </div>
            <p v-if="recipe.sourceUrl" style="margin-top:10px">
              <a :href="recipe.sourceUrl" target="_blank" rel="noreferrer">Source ↗</a>
            </p>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="page-head" style="margin-bottom:10px">
          <h2>How-to videos</h2>
        </div>

        <div v-if="videos.length" class="video-list">
          <div v-for="v in videos" :key="v.id" class="video">
            <!-- Uploaded: played here. preload="metadata" so opening a recipe
                 with several videos doesn't pull them all down. -->
            <video v-if="v.streamUrl" :src="videoSrc(v)" controls preload="metadata"></video>
            <!-- A provider the server is willing to embed. -->
            <iframe v-else-if="v.embedUrl" :src="v.embedUrl" loading="lazy"
                    allowfullscreen referrerpolicy="no-referrer"
                    title="How-to video"></iframe>
            <!-- Anything else: an honest link rather than an empty player. -->
            <a v-else :href="v.url" target="_blank" rel="noopener noreferrer"
               class="video-link">▶ Watch on {{ videoHost(v.url) }} ↗</a>

            <div class="video-foot">
              <span class="grow">{{ v.title }}</span>
              <button class="secondary sm" @click="removeVideo(v)">Remove</button>
            </div>
          </div>
        </div>

        <p v-else class="muted" style="margin:0 0 12px">
          No videos yet. Paste a link to a technique or walkthrough — or upload a
          clip of your own — so you can see how it's done while you cook.
        </p>

        <div class="row" style="gap:8px;align-items:flex-end;flex-wrap:wrap">
          <label class="field" style="margin:0;flex:1;min-width:200px">
            <span>Video link</span>
            <input v-model="newVideoUrl" placeholder="https://youtu.be/…"
                   @keyup.enter="addVideoLink" />
          </label>
          <label class="field" style="margin:0;width:180px">
            <span>Title (optional)</span>
            <input v-model="newVideoTitle" placeholder="e.g. Folding technique" />
          </label>
          <button :disabled="addingVideo" @click="addVideoLink">
            {{ addingVideo ? 'Adding…' : 'Add link' }}
          </button>
          <label class="secondary btnlike">
            {{ addingVideo ? 'Uploading…' : 'Upload a file' }}
            <input type="file" accept="video/*" hidden :disabled="addingVideo"
                   @change="uploadVideo" />
          </label>
        </div>
        <p v-if="videoError" class="err" style="margin:8px 0 0">{{ videoError }}</p>
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
        <p v-if="isScaledView" class="muted scale-note">
          Amounts scaled from
          <button type="button" class="reset-link" @click="resetServings">
            {{ recipe.servings }}
          </button>
          to {{ viewServings }} servings.
          <template v-if="unscaledCount">
            {{ unscaledCount }} {{ unscaledCount === 1 ? 'item has' : 'items have' }}
            no amount and {{ unscaledCount === 1 ? 'is' : 'are' }} unchanged.
          </template>
        </p>
        <ul v-if="plainIngredients.length" class="ing-list">
          <!-- Two zones: the amount as its own tabular column you can run your
               eye down, then what to actually get out of the cupboard. Every row
               uses the same grid, including ones with no amount, so the food text
               keeps one left edge the whole way down the list. -->
          <li v-for="row in shownRows" :key="row.id">
            <span class="ing-amt tnum">{{ row.amount }}</span>
            <span class="ing-food">{{ row.food
              }}<span v-if="row.weight" class="ing-weight">{{ row.weight }}</span
              ><span v-if="row.note" class="ing-note">· {{ row.note }}</span></span>
          </li>
        </ul>
        <p v-else-if="!componentIngredients.length" class="muted">No ingredients listed.</p>

        <div v-if="componentIngredients.length" class="components">
          <h3 class="components-h">🔗 Made with these recipes</h3>
          <ul class="component-list">
            <li v-for="ing in componentIngredients" :key="ing.id" class="component-row">
              <a class="ing-link" href="#"
                 @click.prevent="router.push(`/recipes/${ing.refRecipe.id}`)">
                {{ ing.refRecipe.name }}
              </a>
              <span v-if="ing.quantity" class="muted component-amt">
                × {{ ing.quantity }}{{ ing.quantity == 1 ? ' batch' : ' batches' }}
              </span>
            </li>
          </ul>
          <p class="muted component-note">
            Their ingredients are included in shopping lists, nutrition, and “what can I cook”.
          </p>
        </div>
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
        <div class="page-head" style="margin-bottom:10px">
          <h2>Versions &amp; experiments</h2>
          <div class="grow"></div>
          <button class="secondary sm" @click="startExperiment">🧪 Start an experiment</button>
        </div>
        <p class="muted" style="margin:0 0 12px;font-size:0.88rem">
          Branch an experiment to tweak this recipe, cook it, and log how it turned out —
          then promote the winner or discard it. Every save also keeps an automatic snapshot you can restore.
        </p>

        <div v-if="experiments.length" class="stack" style="gap:10px">
          <div v-for="v in experiments" :key="v.id" class="exp-card">
            <div class="row" style="gap:8px;align-items:center">
              <strong class="fill">{{ v.label || 'Experiment' }}</strong>
              <span class="badge" :class="{ ok: v.status === 'promoted', off: v.status === 'discarded' }">
                {{ v.status }}
              </span>
            </div>
            <div class="row" style="gap:6px;align-items:center;margin-top:8px">
              <span class="muted sm">Rating</span>
              <select v-model.number="v._rating" class="sm" :aria-label="`Rating for ${v.label}`"
                      @change="v._rating = v._rating">
                <option :value="undefined">—</option>
                <option v-for="n in [0,1,2,3,4,5]" :key="n" :value="n">{{ n }}★</option>
              </select>
              <input v-model="v._feedback" class="fill sm" placeholder="How did it turn out? What to change next?"
                     :aria-label="`Feedback for ${v.label}`" />
              <button class="secondary sm" @click="saveFeedback(v)">Save</button>
            </div>
            <div class="row" style="gap:8px;margin-top:8px;flex-wrap:wrap">
              <button v-if="v.status === 'open'" class="secondary sm" @click="editExperiment(v)">✎ Edit</button>
              <button v-if="v.status === 'open'" class="sm" @click="promoteVersion(v)">⬆ Promote</button>
              <button class="secondary sm danger" @click="discardVersion(v)">Discard</button>
              <span class="muted sm" style="align-self:center">{{ fmtDate(v.createdAt) }}</span>
            </div>
          </div>
        </div>

        <details v-if="history.length" class="hist">
          <summary>Edit history ({{ history.length }})</summary>
          <ul class="hist-list">
            <li v-for="v in history" :key="v.id" class="row" style="gap:8px;align-items:center">
              <span class="muted sm fill">{{ fmtDate(v.createdAt) }}</span>
              <button class="secondary sm" @click="promoteVersion(v)">Restore</button>
              <button class="secondary sm danger" @click="discardVersion(v)">✕</button>
            </li>
          </ul>
        </details>
        <p v-if="!experiments.length && !history.length" class="muted" style="margin:0">
          No versions yet — start an experiment, or edit the recipe to begin a history.
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
          <label class="field"><span>Oven (°C)</span>
            <input v-model.number="form.cookTemperatureC" type="number" min="0" max="300"
                   placeholder="e.g. 180" /></label>
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
        <StepRows v-model="editSteps" />
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
/* These are pressed with a wet finger, mid-cook, on a phone. They were
   inheriting generic `button.sm` at ~31px — under the 44px touch guidance and
   only 8px apart. Sized here rather than by changing `.sm`, which is doing its
   job in dense desktop toolbars elsewhere. */
.ing-tools { display: flex; align-items: center; gap: 8px; }
.ing-tools button { min-height: 40px; min-width: 40px; padding: 8px 14px; font-size: 0.9rem; }
@media (max-width: 620px) { .ing-tools button { min-height: 44px; min-width: 44px; } }
/* A pressed VIEW TOGGLE, not a primary action: filled with the accent it won the
   squint test against every amount on the page, on a work surface, which is
   exactly what the "accent only on the primary action" rule exists to prevent.
   It also turned the ⚖️ glyph muddy, since emoji ignore currentColor. */
.ing-tools .active {
  background: var(--surface-2); border-color: var(--text); font-weight: 600;
}

/* The ingredient list is the one surface people read mid-cook, at arm's length,
   often on a phone. Two zones: a fixed tabular amount column your eye can run
   down, then the food. A row divider instead of a bullet, so scanning is done by
   the grid rather than by reading whole sentences. */
/* The GRID LIVES ON THE LIST, not on each row: the amount column then sizes once
   to the widest amount in the recipe, so every food name shares one left edge at
   EVERY viewport. A per-row flex basis can't do that — a fixed `ch` value is a
   guess that wraps "1 1/6 cups" on a phone, and sizing per row abandons the
   column exactly where it's needed most. `display: contents` makes each li's
   spans direct grid items, which is why the row rule is drawn on the cells.
   Capped width because a divider running the full card width past text that ends
   at 40% reads as a table missing its columns. */
/* Cells STRETCH (the grid default) rather than baseline-align: each cell draws
   its own half of the row divider, so unequal heights left a stepped, broken rule
   under any row whose food text wrapped. Stretching also puts the amount level
   with the FIRST line of a wrapped ingredient, which is where you look for it. */
.ing-list {
  display: grid; grid-template-columns: max-content minmax(0, 1fr);
  /* No column-gap: the divider is drawn per cell, so a gap punched a visible
     break in every rule. The spacing lives in the amount cell's padding. */
  column-gap: 0;
  list-style: none; margin: 0; padding: 0; max-width: 62ch;
}
.ing-list li { display: contents; }
.ing-amt, .ing-food { padding: 8px 0; border-bottom: 1px solid var(--border); }
.ing-list li:last-child .ing-amt,
.ing-list li:last-child .ing-food { border-bottom: 0; }
/* Right-aligned so the ragged edge falls on the OUTSIDE. Left-aligned, a short
   "1" sat a whole column away from its ingredient while "3 1/2 cups" touched
   its own — the gap varied inside the pair that has to be read together. */
.ing-amt { font-weight: 650; text-align: right; white-space: nowrap; padding-right: 12px; }
.ing-food { min-width: 0; }
/* Preparation reads as an aside, not as part of the ingredient's name — it used
   to be comma-spliced straight into it. The separator is a real character, not
   generated content, so copying a row yields readable text. */
.ing-note { color: var(--muted); margin-left: 6px; }
.ing-weight { color: var(--muted); margin-left: 6px; }
.scale-note { font-size: 0.875rem; margin: 0 0 12px; }
/* A one-word way back to the authored amounts, mid-cook, without stepping down
   one serving at a time. */
.reset-link {
  border: 0; background: transparent; padding: 0; font: inherit; cursor: pointer;
  color: var(--accent-text); font-weight: 600; text-decoration: underline;
}

@media (max-width: 620px) { .ing-amt { padding-right: 10px; } }
.ing-link { color: var(--accent-text); font-weight: 600; text-decoration: none; }
.ing-link:hover { text-decoration: underline; }
.components { margin-top: 14px; padding-top: 12px; border-top: 1px solid var(--border); }
.components-h { font-size: 0.95rem; margin: 0 0 6px; }
.component-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
.component-row { display: flex; align-items: baseline; gap: 8px; }
.component-amt { font-size: 0.85rem; }
.component-note { font-size: 0.8rem; margin: 8px 0 0; }
.exp-card { border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 12px; }
.badge.ok { background: var(--success-soft, #e6f5ec); color: var(--success, #1a7f43); }
.badge.off { opacity: 0.6; }
.sm { font-size: 0.85rem; }
select.sm { padding: 2px 6px; }
.hist { margin-top: 14px; }
.hist summary { cursor: pointer; color: var(--muted); font-size: 0.88rem; }
.hist-list { list-style: none; margin: 8px 0 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.danger { color: var(--danger, #b3261e); }
.cat-picker { display: flex; flex-wrap: wrap; gap: 8px 16px; margin-top: 4px; }
.cat-opt { display: inline-flex; align-items: center; gap: 6px; font-weight: 400; cursor: pointer; }
.cat-opt input { width: auto; }
.nutri-grid { display: flex; flex-wrap: wrap; gap: 20px 28px; }
.nutri-cell { min-width: 64px; }
.nutri-val { font-size: 1.4rem; font-weight: 700; }
.nutri-lbl { font-size: 0.78rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.03em; }
.video-list { display:grid; gap:14px; margin-bottom:14px }
.video video, .video iframe {
  width:100%; aspect-ratio:16/9; border:0; border-radius:var(--radius-sm);
  background:#000; display:block;
}
.video-link {
  display:block; padding:14px; border:1px solid var(--border);
  border-radius:var(--radius-sm); text-decoration:none;
}
.video-foot { display:flex; align-items:center; gap:8px; margin-top:6px; font-size:.9rem }
.btnlike { cursor:pointer; display:inline-flex; align-items:center; padding:8px 12px;
  border:1px solid var(--border); border-radius:var(--radius-sm); }
@media (max-width:560px) { .video-foot { flex-wrap:wrap } }
</style>
