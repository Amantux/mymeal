<script setup>
import { ref, onMounted, computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, apiUrl, mediaUrl } from '../api'
import { useUI } from '../stores/ui'
import IngredientRows from '../components/IngredientRows.vue'
import StepRows from '../components/StepRows.vue'
import { rowToDisplay, ingredientToRow, rowToPayload } from '../utils/ingredientEdit'

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

// Cook mode opens at the servings the reader is LOOKING at, not the recipe's
// base — walking into the kitchen must not silently revert the numbers they
// just scaled. Only passed when the view is actually scaled (isScaledView), so
// a failed scale request can never send cook mode a count the reader never saw.
function openCookMode() {
  const q = isScaledView.value ? `?servings=${viewServings.value}` : ''
  router.push(`/recipes/${recipe.value.id}/cook${q}`)
}

function amountOf(ing) {
  return [ing.amountText, ing.unitText].filter(Boolean).join(' ')
}
// Counted once for the scale note rather than tagged per row: a row with no
// amount already shows an empty amount column, so a per-row chip restated it
// while stealing width from the ingredient text at phone size.
const unscaledCount = computed(() => plainIngredients.value.filter((i) => i.scaled === false).length)

// What the stored nutrition figure IS. A number multiplied from reference data
// and one guessed by a language model are different claims, and the panel used
// to present them identically. An ABSENT label means a value saved before
// provenance was recorded — reported as unknown rather than silently assumed
// sound, the same rule as a missing nutrient not being zero.
const nutritionProvenance = computed(() => {
  const r = recipe.value
  if (!r || !r.nutrition) return ''
  if (r.nutritionSource === 'computed') {
    const cov = r.nutritionCompleteness
    return cov != null && cov < 100
      ? `Calculated from food reference data \u00b7 covers ${cov}% of this recipe by weight`
      : 'Calculated from food reference data'
  }
  if (r.nutritionSource === 'estimated') return 'Estimated by AI \u2014 not calculated'
  if (r.nutritionSource === 'manual') return 'Entered by hand'
  return 'Source unknown \u2014 saved before figures recorded where they came from'
})
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
  // An alternative way to measure the same amount, and the "optional" flag,
  // both lifted out of the food name by the backend. `alt` is the one a cook
  // with no kitchen scale actually reads, so it is rendered, not dropped —
  // split_amount's three parts no longer account for the whole line on their
  // own. `amount` becomes an explicit dash when the line HAS no amount: an
  // empty cell in a bordered grid reads as a rendering fault, and these rows
  // ("Salt, to taste") are deliberate. Only when the backend says the line is
  // unmeasured — "About 2 cups milk" also has an empty amount cell, but its
  // amount is real and is being shown inside the text beside it.
  const extra = {
    alt: ing.altText || '',
    optional: !!ing.optional,
    amount: ing.unmeasured ? '—' : amountOf(ing),
  }
  const tail = rest.slice(-(note.length + 2))
  if (note && tail.toLowerCase() === `, ${note.toLowerCase()}`) {
    return { ...extra, food: rest.slice(0, rest.length - tail.length), note, weight }
  }
  return { ...extra, food: rest, note: '', weight }
}
// Mapped once per render rather than calling the splitter twice per row in the
// template (once for the food, once for the note).
const shownRows = computed(() => plainIngredients.value.map((ing) => ({
  id: ing.id, section: ing.section || '', ...splitOne(ing),
})))
// Consecutive rows sharing a section render under one sub-heading. Grouping is
// by adjacency, never by sorting: the author's order is the recipe's order.
// When no row has a section (most recipes) this is a single unheaded group and
// the list renders exactly as before.
const shownGroups = computed(() => {
  const groups = []
  for (const row of shownRows.value) {
    const last = groups[groups.length - 1]
    if (last && last.section === row.section) last.rows.push(row)
    else groups.push({ section: row.section, rows: [row] })
  }
  return groups
})

// ---- Step structure -------------------------------------------------------
// The backend derives phase headings and reads every stated duration and
// temperature out of the step text (services/step_structure.py). All of it is
// optional: a recipe with no stated times and no inferable phases falls through
// to exactly the flat numbered list this view rendered before.
const stepStructure = computed(() => recipe.value?.stepStructure || null)

// Index-addressed so a group can render `<ol :start>` over the real positions
// and the numbering stays continuous across headings.
const stepRows = computed(() => stepStructure.value?.steps || [])
const stepGroups = computed(() => {
  const groups = stepStructure.value?.groups
  if (groups?.length) return groups
  // No structure (an older cached payload, or a recipe loaded before this
  // shipped): one unheaded group, which renders as the plain list.
  return [{ title: '', indexes: (recipe.value?.steps || []).map((_, i) => i), derived: false }]
})

// Three kinds of time, because "3 days in the fridge" and "20 minutes in the
// oven" are different planning facts even though neither is hands-on. Only the
// kinds the method actually states are shown — a zero would read as a claim.
const stepClock = computed(() => {
  const s = stepStructure.value?.summary
  if (!s) return []
  return [
    { key: 'wait', icon: '💤', label: 'Waiting', text: s.waitText },
    { key: 'cook', icon: '🔥', label: 'Cooking', text: s.cookText },
    { key: 'active', icon: '⏱', label: 'Hands-on', text: s.activeText },
  ].filter((x) => x.text)
})
const stepClockNote = computed(() => {
  const s = stepStructure.value?.summary
  if (!s) return ''
  const parts = []
  // Honesty about the arithmetic: a method can repeat a rest without restating
  // it ("3 more times with 5-minute rests"), and we refuse to multiply on its
  // behalf, so these totals are a floor.
  if (s.atLeast) parts.push('Times are the ones this method states; a repeated rest may add more.')
  if (s.derivedHeadings) parts.push('Headings are suggested from the method text.')
  return parts.join(' ')
})
const DURATION_ICON = { wait: '💤', cook: '🔥', active: '⏱' }

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

// A free-text row has no food and no quantity by construction, so it has to be
// recognised explicitly here — otherwise every prose line is filtered out of the
// payload and the save silently DELETES it.
const filledRows = () => editIngredients.value.filter(
  (r) => (r.food || '').trim() || String(r.quantity ?? '').trim() || r.refRecipeId
    || (r.freeText && (r.display || '').trim()),
)

// ingredientToRow / rowToDisplay / rowToPayload live in utils/ingredientEdit.js:
// the editor rebuilds every ingredient from these rows on save, so a serialized
// field the pair doesn't carry is DESTROYED by any unrelated edit. The module's
// tests are the regression guard (qualifier nearly shipped that way; section did).

async function tidyIngredients() {
  const src = filledRows()
  // Free-text rows are held OUT of the tidy and spliced back untouched.
  // Restructuring them is exactly what their author opted out of, and Tidy
  // replaces the whole list — so including them would be a third way to
  // silently convert prose back into catalog-minting structured rows.
  //
  // Pairing each line with the index it came from also fixes a latent desync:
  // a row whose display is empty was dropped by `.filter(Boolean)` while the
  // results were still zipped by position, shifting every later row's section
  // onto the wrong ingredient.
  const pairs = src
    .map((r, idx) => ({ r, idx, line: rowToDisplay(r) }))
    .filter((p) => !p.r.freeText && !p.r.refRecipeId && p.line)
  if (!pairs.length || structuring.value) return
  structuring.value = true
  try {
    const res = await api.post('/ai/parse-ingredients', { lines: pairs.map((p) => p.line) })
    const out = src.slice()
    res.ingredients.forEach((r, k) => {
      // The model is not guaranteed to return one row per line — the backend
      // only trusts the positional mapping when the counts match, and drops
      // items it can't read. An extra row here used to destructure undefined
      // and surface as a raw TypeError toast.
      const pair = pairs[k]
      if (!pair) return
      const { r: was, idx } = pair
      out[idx] = {
        quantity: r.quantity || '', unit: r.unit || '', food: r.food || '',
        note: r.note || '', qualifier: r.qualifier || '',
        // The parser never sees the section (it isn't part of the line), so it
        // must be carried across from the row that produced the line, or Tidy
        // becomes another way to silently wipe every grouping.
        section: was.section || '',
        freeText: false, display: '',
      }
    })
    editIngredients.value = out
    ui.toast('Tidied ingredients')
  } catch (e) {
    ui.error(e.message || 'Could not tidy ingredients')
  } finally {
    structuring.value = false
  }
}
const editSteps = ref([]) // [{title, text}]

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
  // Re-screen whenever the recipe is re-read, not only on mount. `load()` is
  // what restoring a version and the media actions call, and with the call
  // sitting in `onMounted` instead, promoting an experiment swapped the whole
  // ingredient list underneath a verdict list that never changed — showing
  // last week's allergen states, with "Screened 3 ingredient lines" under
  // them, as if they described what is now on screen.
  if (recipe.value) await loadSafety()
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
  // `!i.freeText` is what separates the two meanings of "has no food". A legacy
  // line is one an importer COULDN'T structure, and parsing it is a service to
  // the user. A free-text line is one the author decided NOT to structure, and
  // parsing it would hand their own prose back through the parser and mint the
  // Food the flag exists to prevent — the round-trip loss, one edit later.
  const legacy = r.ingredients
    .map((i, idx) => (!i.food && !i.refRecipe && !i.freeText && i.display
      ? { idx, display: i.display } : null))
    .filter(Boolean)
  if (legacy.length) {
    try {
      const res = await api.post('/recipes/parse', { lines: legacy.map((l) => l.display) })
      res.ingredients.forEach((p, k) => {
        const row = rows[legacy[k].idx]
        row.quantity = p.quantity || ''
        row.unit = p.unit || ''
        row.food = p.food || row.food
        // The parser now lifts an alternative measure ("or 1/2 tsp") and
        // "optional" OUT of the food name and returns them here, so a row that
        // takes `food` and drops `note` loses them for good: rowToDisplay
        // rebuilds `display` from these fields, and this re-parse runs on every
        // startEdit. Opening Edit to fix a typo in the title would have deleted
        // the alternative measure from the database. Appended rather than
        // assigned, so a row that already carries preparation keeps it, and
        // guarded on `includes` so a second trip cannot say it twice.
        const lifted = p.note && !(row.note || '').includes(p.note) ? p.note : ''
        row.note = [row.note, lifted].filter(Boolean).join(', ')
      })
    } catch { /* keep the display-in-food fallback */ }
  }
  editIngredients.value = rows
  // `title` must be carried, not just `text`. The schema.org and AI import
  // paths both populate step titles, and dropping them here silently wiped a
  // cook's own phase headings the first time they opened the editor — which is
  // why the column looked unused despite three writers.
  editSteps.value = r.steps.map((s) => ({ title: s.title || '', text: s.text }))
  editing.value = true
}

async function save() {
  const payload = {
    ...form.value,
    categoryIds: selectedCategoryIds.value,
    nutrition: nutritionForm.value,
    ingredients: filledRows().map((r, position) => rowToPayload(r, position)),
    // A heading with no step under it is not a step, so the filter stays on
    // text; the title rides along with whatever survives it.
    steps: editSteps.value
      .map((s) => ({ title: (s.title || '').trim(), text: (s.text || '').trim() }))
      .filter((s) => s.text)
      .map((s, position) => ({ ...s, position })),
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
      // The ingredient list just changed, so every verdict below it is now
      // about a recipe that no longer exists. A stale "no known ingredient
      // identified" sitting under a freshly-added jar of peanut butter is the
      // worst thing this panel can do, and it is silent.
      await loadSafety()
      ui.toast('Saved')
    }
  } catch (e) {
    ui.error(e.message)
  }
}

// --- What this recipe contains (allergens + diets) --------------------------
//
// A WORK surface, so no flair: one dense list, no badge competing with the
// accent, and the accent untouched because this card has no primary action.
//
// The hard part is `unknown`, and the rule it has to satisfy is that it must
// look like NEITHER of the two easy readings:
//
//   * not "fine" — so it is never muted, never collapsed into the quiet
//     summary line, and it sorts ABOVE the quiet states. The word used is
//     "Not screened", which is a statement about us rather than about the
//     food; "no nuts found" would be a claim we cannot make.
//   * not an error — so it is not `--danger`, carries no error iconography,
//     and the card renders normally around it. An unreadable ingredient is an
//     ordinary fact about a hand-typed recipe, not a malfunction.
//
// It lands in the `--warning` family, separated from `may_contain` by a DASHED
// rather than solid rail: same "needs your attention" register, visibly
// incomplete rather than visibly suspected. And `fullyScreened: false` gets a
// block at the top of the card naming the lines and the consequence, because
// the actionable thing is "go read these labels", which a per-row state word
// cannot say.
const safety = ref(null)
const safetyLoading = ref(true)
// A separate ref from `safety`, deliberately: this panel must be able to say
// "we could not screen this" out loud. Swallowing the failure into an empty
// panel — the house `loadVersions()` pattern — would render exactly like a
// clean result, which is the one outcome this feature exists to prevent.
const safetyError = ref('')

async function loadSafety() {
  safetyLoading.value = true
  safetyError.value = ''
  try {
    safety.value = await api.get(`/recipes/${recipe.value.id}/safety`)
  } catch (e) {
    safety.value = null
    safetyError.value = e.message || 'Screening could not be loaded.'
  } finally {
    safetyLoading.value = false
  }
}

// Severity first. `unknown` sits ABOVE the two quiet states on purpose — an
// ingredient nobody could read is more worth a cook's eye than a rule that
// matched nothing.
const SAFETY_ORDER = {
  contains: 0, incompatible: 0,
  may_contain: 1, uncertain: 1,
  unknown: 2,
  no_known_ingredient_identified: 3, no_conflict_identified: 3,
}
const QUIET_STATES = ['no_known_ingredient_identified', 'no_conflict_identified']

// The human word for a state. Not derived from the enum: "contains" is already
// English, but `no_known_ingredient_identified` is not, and `unknown` on its
// own is ambiguous about WHO is uncertain.
const SAFETY_WORD = {
  contains: 'Contains',
  may_contain: 'May contain',
  incompatible: 'Incompatible',
  uncertain: 'Uncertain',
  unknown: 'Not screened',
}

// The two QUIET states are listed explicitly and everything else falls through
// to `unscreened`, not to `quiet`. A state this view has never heard of — a
// future foodcore release adding one — must surface as something that wants
// attention, not as a muted afterthought at the bottom of the card. Failing
// open is how a new state would get rendered as "fine".
const QUIET_TONE_STATES = ['no_known_ingredient_identified', 'no_conflict_identified']

function safetyTone(state) {
  if (state === 'contains' || state === 'incompatible') return 'bad'
  if (state === 'may_contain' || state === 'uncertain') return 'warn'
  if (QUIET_TONE_STATES.includes(state)) return 'quiet'
  return 'unscreened'
}

// Why a verdict says what it says. Matched terms first — the shortest true
// answer — then, for the HEDGED states, the engine's own note.
//
// The note matters because without it a hedge is indistinguishable from a
// plain match: foodcore downgrades a `contains` to `may_contain` when the line
// claims to be free of the allergen ("declared vegan; claim not verified") or
// when the ingredient is optional, and both rendered as a bare
// "May contain Milk — butter". That reads as an ordinary precautionary match
// and loses the one fact a cook would act on.
//
// Returns `{ terms, extra, full }`: `full` is the ungrouped term list, which
// is what `loudRows` keys on — grouping on the TRUNCATED string merged two
// verdicts that differed only in their fifth term.
function safetyWhy(verdict) {
  const terms = []
  const notes = []
  const hedged = verdict.state === 'may_contain' || verdict.state === 'uncertain'
  for (const e of verdict.evidence || []) {
    if (e.matchedTerm && !terms.includes(e.matchedTerm)) terms.push(e.matchedTerm)
    if (hedged && e.note && !notes.includes(e.note)) notes.push(e.note)
  }
  const shown = terms.slice(0, 4).join(', ')
  const more = terms.length > 4 ? ` +${terms.length - 4} more` : ''
  return { terms: shown + more, extra: notes[0] || '', full: terms.join(' ') }
}

// A row exists to tell the reader something specific. When nine allergens are
// `unknown` for the same unreadable ingredient, nine rows say one thing nine
// times and bury anything that differs — the first screenshot of this panel had
// exactly that, and the two `contains` rows above it were lost in the noise.
// So rows that agree on state AND reason are merged into one, and only then
// sorted by severity. Rows whose reasons differ stay separate, because then the
// reason is the information.
function loudRows(rows) {
  const groups = new Map()
  for (const r of rows || []) {
    if (QUIET_STATES.includes(r.state)) continue
    const why = safetyWhy(r)
    // Keyed on the FULL term list and the note, never on the displayed string:
    // that one is truncated at four terms, so two verdicts differing only in
    // their fifth term merged into a single row that was wrong for both.
    const key = `${r.state}|${why.full}|${why.extra}`
    if (!groups.has(key)) {
      groups.set(key, {
        key, state: r.state, why: why.terms, extra: why.extra, labels: [],
      })
    }
    groups.get(key).labels.push(r.label)
  }
  return [...groups.values()]
    .map((g) => ({ ...g, labels: g.labels.sort((a, b) => a.localeCompare(b)) }))
    // `?? -1`, not `?? 9`: an unrecognised state sorts FIRST. Same reasoning as
    // `safetyTone` — something this view does not understand belongs where it
    // will be read, not at the bottom.
    .sort((a, b) => (SAFETY_ORDER[a.state] ?? -1) - (SAFETY_ORDER[b.state] ?? -1)
      || a.labels[0].localeCompare(b.labels[0]))
}

// The quiet states get ONE line listing their labels, not a row each. Twelve
// rows of "nothing found" would bury the three that matter, and the heading
// those labels sit under is the engine's own careful phrasing rather than
// anything resembling "free from".
function quietLabels(rows) {
  return (rows || [])
    .filter((r) => QUIET_STATES.includes(r.state))
    .map((r) => r.label)
    .sort((a, b) => a.localeCompare(b))
}

const allergensLoud = computed(() => loudRows(safety.value?.allergens))
const allergensQuiet = computed(() => quietLabels(safety.value?.allergens))
const dietsLoud = computed(() => loudRows(safety.value?.diets))
const dietsQuiet = computed(() => quietLabels(safety.value?.diets))

// An advisory is a property of the DIET, not of this recipe's verdict — the
// same sentence every time, for every recipe. Inline on each row it was most
// of the card's height and repeated under states it did not qualify. Collected
// here instead: the headline (never certified) is always visible, the engine's
// own reasons are one click away, and nothing is paraphrased.
const advisoryDiets = computed(() =>
  (safety.value?.diets || [])
    .filter((d) => d.advisory)
    .slice()
    .sort((a, b) => a.label.localeCompare(b.label)))

// Lines the engine could not fully read, with its reason for each.
const unreadable = computed(() =>
  (safety.value?.coverage || []).filter((c) => (c.unscreened || []).length))

// The scope statement. Every number in it is reported by the server; none of
// it is this component's opinion about how trustworthy the panel is.
const safetyScope = computed(() => {
  const s = safety.value
  if (!s || s.status !== 'screened') return ''
  return `Screened ${s.screenedLines} ingredient line${s.screenedLines === 1 ? '' : 's'} `
    + `against ${s.rulesetVersions.allergens} and ${s.rulesetVersions.dietary}.`
})

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
  editIngredients.value = (snap.ingredients || []).map((i) => {
    const carried = { note: i.note || '', qualifier: i.qualifier || '', section: i.section || '' }
    if (i.refRecipeId) {
      return { quantity: i.quantity || '', unit: i.unit || '',
               food: i.food || i.display || 'component', ...carried,
               refRecipeId: i.refRecipeId, refRecipeName: i.food || i.display || 'recipe' }
    }
    // A snapshot carries the free-text flag, so loading one into the editor has
    // to reproduce the lane. Falling through to the line below would drop the
    // prose into `food` and the next save would mint it as a Food — the same
    // round-trip loss, reached by editing an experiment instead of the recipe.
    if (i.freeText) {
      return { quantity: '', unit: '', food: '', display: i.display || '',
               freeText: true, ...carried }
    }
    return { quantity: i.quantity || '', unit: i.unit || '',
             food: i.food || i.display || '', freeText: false, ...carried }
  })
  editSteps.value = (snap.steps || []).map((s) => ({ title: s.title || '', text: s.text }))
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
// --- Media (photos and how-to videos) ------------------------------------
// One slot per recipe. An item is a photo we stored, a video we stored, a
// YouTube/Vimeo link the server is willing to embed, or any other link that
// opens in a new tab. The server decides which — the UI never builds an embed
// URL from a raw address, and never guesses an item's kind.
//
// Photos are uploads only: the app's CSP is img-src 'self', so an external
// image address would render as a broken icon. The add panel says so rather
// than letting the user discover it.
const newMediaUrl = ref('')
const newMediaTitle = ref('')
const addingMedia = ref(false)
const mediaError = ref('')
const showAddMedia = ref(false)

// `media` is the current key; `videos` is the same list under its former name,
// so a cached payload from a pre-media backend still renders.
const media = computed(() => recipe.value?.media || recipe.value?.videos || [])

function mediaHost(url) {
  try { return new URL(url).hostname.replace(/^www\./, '') } catch { return 'the web' }
}

function mediaFileSrc(m) {
  return m.fileUrl ? mediaUrl(m.fileUrl) : null
}

function toggleAddMedia() {
  showAddMedia.value = !showAddMedia.value
  // Clearing on close as well as open: a stale "that link is not http(s)"
  // sitting there when the panel is reopened is a lie about the current input.
  mediaError.value = ''
}

async function addMediaLink() {
  if (!newMediaUrl.value.trim()) return
  addingMedia.value = true
  mediaError.value = ''
  try {
    await api.post(`/recipes/${recipe.value.id}/media`,
      { url: newMediaUrl.value.trim(), title: newMediaTitle.value.trim() })
    newMediaUrl.value = ''
    newMediaTitle.value = ''
    showAddMedia.value = false
    await load()
    ui.toast('Video link added')
  } catch (e) {
    // Stays on screen next to the field rather than as a toast that vanishes:
    // the message says which part of the link is wrong.
    mediaError.value = e.message || 'Could not add that link'
  } finally {
    addingMedia.value = false
  }
}

async function uploadMedia(ev) {
  const file = ev.target.files?.[0]
  if (!file) return
  addingMedia.value = true
  mediaError.value = ''
  try {
    const fd = new FormData()
    fd.append('file', file)
    if (newMediaTitle.value.trim()) fd.append('title', newMediaTitle.value.trim())
    // No `kind`: the server reads it off the extension, which is the one case
    // where it cannot be wrong. One picker, no "is this a photo or a video?".
    await api.upload(`/recipes/${recipe.value.id}/media`, fd)
    newMediaTitle.value = ''
    showAddMedia.value = false
    await load()
    ui.toast('Added to this recipe')
  } catch (e) {
    // Uploads hit the size ceiling far more often than a pasted link does, so
    // this path must never fail silently.
    mediaError.value = e.message === 'upload too large'
      ? 'That file is larger than this add-on allows. Link to the video instead, or raise max_upload_mb in the add-on configuration.'
      : (e.message || 'Could not upload that file')
  } finally {
    addingMedia.value = false
    ev.target.value = ''
  }
}

async function removeMedia(m) {
  const what = m.kind === 'image' ? 'photo' : 'video'
  const name = m.title || `this ${what}`
  if (!confirm(`Remove “${name}” from this recipe? The ${what} is deleted; the recipe itself is not affected.`)) return
  try {
    await api.del(`/recipes/${recipe.value.id}/media/${m.id}`)
    await load()
    ui.toast(`${what === 'photo' ? 'Photo' : 'Video'} removed`)
  } catch (e) {
    ui.error(e.message || `Could not remove that ${what}`)
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
      <!-- The actions are ONE flex item so a phone wraps the whole cluster to
           its own line instead of orphaning whichever button ran out of room
           (Delete used to land alone, left-aligned, looking like a mistake). -->
      <div class="head-actions">
        <template v-if="!editing">
          <button v-if="recipe.steps.length" @click="openCookMode">
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

      <!-- Media sits here, below the hero card and above Ingredients, NOT
           beside the hero thumbnail. Two reasons: the hero `image` is the
           recipe's single identity picture (what the card and list views
           render) and putting a growable gallery next to it makes "which one
           is the thumbnail?" ambiguous; and on a phone the hero card is
           already a full screen, so a gallery inside it would push the
           ingredients — the reason you opened the page — further down. Reading
           order stays identity → reference material → ingredients → steps. -->
      <div class="card">
        <div class="page-head" style="margin-bottom:10px">
          <h2>Media</h2>
          <!-- No count badge: a "2" next to two visible tiles encodes nothing
               the eye has not already read, and it made the header's right edge
               move between the empty and filled states. -->
        </div>

        <!-- One grid, always rendered: the add tile is the last cell whether
             or not anything is in the slot, so "add more" is never a different
             affordance from "add the first one". -->
        <div class="media-grid">
          <div v-for="m in media" :key="m.id" class="media-item">
            <!-- A photo we stored. -->
            <img v-if="m.kind === 'image'" :src="mediaFileSrc(m)" :alt="m.title"
                 class="media-frame" loading="lazy" />
            <!-- A video we stored, played here. preload="metadata" so opening
                 a recipe with several clips doesn't pull them all down. -->
            <video v-else-if="m.fileUrl" :src="mediaFileSrc(m)" class="media-frame"
                   controls preload="metadata"></video>
            <!-- A provider the server is willing to embed. -->
            <iframe v-else-if="m.embedUrl" :src="m.embedUrl" class="media-frame"
                    loading="lazy" allowfullscreen referrerpolicy="no-referrer"
                    title="How-to video"></iframe>
            <!-- Anything else: an honest link rather than an empty player. -->
            <a v-else :href="m.url" target="_blank" rel="noopener noreferrer"
               class="media-frame media-link">▶ Watch on {{ mediaHost(m.url) }} ↗</a>

            <!-- min-width:0 on the caption is what makes the ellipsis work in a
                 flex row; without it a long title pushes Remove out of the tile
                 and desynchronises the row's heights. -->
            <div class="media-foot">
              <span class="media-title" :title="m.title">{{ m.title }}</span>
              <button class="media-remove" @click="removeMedia(m)">Remove</button>
            </div>
          </div>

          <!-- The empty slot. A dashed tile that says what belongs in it, not a
               paragraph of prose: when the slot is empty it is the only thing
               in the card and has to teach, and when it is full it still reads
               as "there is room for another". Copy shortens once the user has
               demonstrably learned what goes here. -->
          <button type="button" class="media-add" :class="{ open: showAddMedia }"
                  :disabled="addingMedia" :aria-expanded="showAddMedia"
                  aria-controls="media-add-panel" @click="toggleAddMedia">
            <!-- Once the panel is open the tile stops teaching: the user has
                 already acted, the form below says the same thing in fields,
                 and repeating the pitch above a live form is marketing. It
                 becomes the toggle's OFF switch instead — which is also the
                 panel's close affordance. -->
            <template v-if="showAddMedia">
              <span class="media-add-plus" aria-hidden="true">×</span>
              <span class="media-add-copy"><strong>Cancel</strong></span>
            </template>
            <template v-else>
              <span class="media-add-plus" aria-hidden="true">＋</span>
              <span v-if="!media.length" class="media-add-copy">
                <strong>Add a photo or video</strong>
                A photo of how it should look, or a clip of the tricky step.
              </span>
              <span v-else class="media-add-copy">
                <strong>Add another</strong>
                Photo, clip, or a video link.
              </span>
            </template>
          </button>
        </div>

        <!-- One column at one width. Title and the file picker were briefly on
             a shared row, which put the "Title (optional)" label visually over
             the button too — they are not a pair, so they do not share a row. -->
        <div v-if="showAddMedia" id="media-add-panel" class="media-panel">
          <label class="field" style="margin:0">
            <span>Title (optional)</span>
            <input v-model="newMediaTitle" placeholder="e.g. Folding technique" />
          </label>
          <!-- Secondary, not accent: Cook is this page's one primary action.
               A card-level action never earns the orange fill. -->
          <label class="secondary btnlike media-file">
            {{ addingMedia ? 'Uploading…' : '📷 Choose a photo or video' }}
            <input type="file" accept="image/*,video/*" hidden
                   :disabled="addingMedia" @change="uploadMedia" />
          </label>
          <label class="field" style="margin:0">
            <span>Or paste a video link</span>
            <input v-model="newMediaUrl" placeholder="https://youtu.be/…"
                   @keyup.enter="addMediaLink" />
            <!-- Directly under the field it constrains, not floating at the end
                 of the form where it reads as being about the file picker. -->
            <small class="muted media-hint">
              Video links only (YouTube, Vimeo) — photos must be uploaded.
            </small>
          </label>
          <div class="row" style="gap:8px">
            <button class="secondary" :disabled="addingMedia" @click="addMediaLink">
              {{ addingMedia ? 'Adding…' : 'Add link' }}
            </button>
          </div>
          <p v-if="mediaError" class="err" style="margin:0">{{ mediaError }}</p>
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
          <!-- Sub-headings stay INSIDE the one grid (a full-width item), so the
               amount column keeps a single shared width across every section
               instead of re-sizing per group. -->
          <template v-for="(g, gi) in shownGroups" :key="`g${gi}`">
            <li v-if="g.section" class="ing-sec">{{ g.section }}</li>
            <li v-for="row in g.rows" :key="row.id">
              <!-- The dash needs a name of its own: a screen reader announces a
                   bare "—" as nothing, which is worse than the blank cell it
                   replaced, and the sighted explanation ("2 items have no
                   amount") only appears once the list is scaled. -->
              <span class="ing-amt tnum" :class="{ 'is-none': row.amount === '—' }"
                    :aria-label="row.amount === '—' ? 'No set amount' : null"
                    :title="row.amount === '—' ? 'No set amount' : null">{{ row.amount }}</span>
              <span class="ing-food">{{ row.food
                }}<span v-if="row.alt" class="ing-alt">or {{ row.alt }}</span
                ><span v-if="row.optional" class="ing-alt">· optional</span
                ><span v-if="row.weight" class="ing-weight">{{ row.weight }}</span
                ><span v-if="row.note" class="ing-note">· {{ row.note }}</span></span>
            </li>
          </template>
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
        <!-- The clock first, because it is the thing a cook needs BEFORE step 1.
             This method hides 12-72 hours of fridge inside step 4; reading the
             list top to bottom is how you find that out too late. -->
        <div v-if="stepClock.length" class="step-clock">
          <div v-for="c in stepClock" :key="c.key" class="clock-item">
            <span class="clock-k">{{ c.icon }} {{ c.label }}</span>
            <span class="clock-v tnum">{{ c.text }}</span>
          </div>
        </div>
        <template v-if="recipe.steps.length">
          <!-- One <ol> per phase, each continuing the count with :start, so the
               headings are real headings rather than numbered list items and
               the step numbers still run 1..n across the whole method. -->
          <div class="step-list">
            <template v-for="(g, gi) in stepGroups" :key="`sg${gi}`">
              <h3 v-if="g.title" class="step-sec">{{ g.title }}</h3>
              <ol class="step-ol" :start="g.indexes[0] + 1">
                <li v-for="i in g.indexes" :key="recipe.steps[i]?.id || i">
                  <span class="step-text">{{ recipe.steps[i]?.text }}</span>
                  <span v-if="stepRows[i]?.durations?.length || stepRows[i]?.temperature"
                        class="step-chips">
                    <span v-for="(d, di) in (stepRows[i].durations || [])" :key="`d${di}`"
                          class="chip" :class="{ wait: d.kind === 'wait' }">
                      {{ DURATION_ICON[d.kind] }} {{ d.label }}
                    </span>
                    <span v-if="stepRows[i].temperature" class="chip">
                      🌡 {{ stepRows[i].temperature }}
                    </span>
                  </span>
                </li>
              </ol>
            </template>
          </div>
          <p v-if="stepClockNote" class="muted step-note">{{ stepClockNote }}</p>
        </template>
        <p v-else class="muted">No steps listed.</p>
      </div>

      <div v-if="recipe.notes" class="card">
        <h2>Notes</h2>
        <p style="white-space:pre-wrap;margin:0">{{ recipe.notes }}</p>
      </div>

      <!-- What this recipe contains. Sits with Nutrition rather than with
           Ingredients: both are derived facts about the food that carry their
           own provenance, and a cook mid-recipe should not have this between
           the ingredient list and the steps. -->
      <div class="card">
        <h2>What this contains
          <span class="muted" style="font-weight:400">— allergens &amp; diets</span>
        </h2>

        <!-- Loading: a skeleton shaped like the rows it replaces. -->
        <div v-if="safetyLoading" class="sf-skel">
          <div class="skeleton sf-skel-a"></div>
          <div class="skeleton sf-skel-b"></div>
          <div class="skeleton sf-skel-c"></div>
        </div>

        <!-- Unavailable. Said out loud, because an empty panel here is
             indistinguishable from a clean one. -->
        <div v-else-if="safetyError" class="sf-notice">
          <strong>This recipe has not been screened.</strong>
          {{ safetyError }}
          <!-- On its own line: inline, it broke the sentence's line box and
               left a ragged gap above itself at 390px. -->
          <button class="secondary sm sf-retry" @click="loadSafety">
            Try again
          </button>
        </div>

        <p v-else-if="safety && safety.status === 'no_ingredients'" class="muted" style="margin:0">
          No ingredients yet. Add them and this panel will report what the
          recipe contains — and what it could not read.
        </p>

        <template v-else-if="safety && safety.status === 'screened'">
          <!-- The consequence of an unreadable line, stated before any verdict
               the reader might take at face value. -->
          <div v-if="!safety.fullyScreened" class="sf-notice">
            <strong>
              {{ unreadable.length }} of {{ safety.screenedLines }} ingredient{{ safety.screenedLines === 1 ? '' : 's' }}
              could not be read.
            </strong>
            Nothing below rules out an allergen in {{ unreadable.length === 1 ? 'it' : 'them' }} — check the label yourself.
            <ul class="sf-reasons">
              <!-- Keyed by index: two identical ingredient lines are legal and
                   produce two coverage rows with the same label. -->
              <li v-for="(c, i) in unreadable" :key="i">
                <span class="sf-line">{{ c.ingredient }}</span>
                <span class="muted"> — {{ c.unscreened.join('; ') }}</span>
              </li>
            </ul>
          </div>

          <h3 class="sf-h">Allergens</h3>
          <dl class="sf-rows">
            <template v-for="row in allergensLoud" :key="row.key">
              <dt :class="['sf-dt', 'sf-tone-' + safetyTone(row.state),
                           'sf-text-' + safetyTone(row.state)]">
                {{ SAFETY_WORD[row.state] || row.state }}
              </dt>
              <dd :class="['sf-dd', 'sf-tone-' + safetyTone(row.state)]">
                <span class="sf-labels">{{ row.labels.join(' · ') }}</span>
                <span v-if="row.why" class="muted sf-why">{{ row.why }}</span>
                <!-- The engine's own note on a HEDGED state. Without it,
                     "May contain Milk — butter" hides the fact that the line
                     claimed to be vegan, or that the ingredient is optional —
                     which is the part a cook would act on. -->
                <span v-if="row.extra" class="muted sf-why sf-note">{{ row.extra }}</span>
                <span v-else-if="!row.why && row.state === 'unknown'" class="muted sf-why">
                  no rule could be applied to {{ unreadable.length === 1 ? 'the unreadable ingredient' : 'the unreadable ingredients' }} above
                </span>
              </dd>
            </template>
          </dl>
          <!-- The quiet state. The DANGEROUS one, and the first draft got it
               wrong: it led with "No known ingredient identified" in bold and
               put "(not a safety claim)" after it, parenthesised and a size
               SMALLER than the line it qualified. On a recipe where nothing
               matched, that made the entire allergens section read, at a
               glance, as "No … Egg · Milk · Peanuts · Tree nuts" — a free-from
               list. The caveat is now in the headline, at full size, and the
               reassuring half never stands alone.

               When there is nothing else in the section it gets a full
               sentence first, because that is the screen where a cook has
               least to compare against. -->
          <p v-if="allergensQuiet.length && !allergensLoud.length"
             class="muted sf-quiet-lead">
            No ingredient matched an allergen rule. The rules read common
            ingredient names, not labels — so this is "nothing found", not
            "free from".
          </p>
          <p v-if="allergensQuiet.length" class="muted sf-quiet">
            <span class="sf-quiet-h">No rule matched these — which is not the
              same as free from them</span>:
            {{ allergensQuiet.join(' · ') }}
          </p>

          <h3 class="sf-h">Diets</h3>
          <dl class="sf-rows">
            <template v-for="row in dietsLoud" :key="row.key">
              <dt :class="['sf-dt', 'sf-tone-' + safetyTone(row.state),
                           'sf-text-' + safetyTone(row.state)]">
                {{ SAFETY_WORD[row.state] || row.state }}
              </dt>
              <dd :class="['sf-dd', 'sf-tone-' + safetyTone(row.state)]">
                <span class="sf-labels">{{ row.labels.join(' · ') }}</span>
                <span v-if="row.why" class="muted sf-why">{{ row.why }}</span>
                <!-- Same fallback the allergen rows get. Without it this row
                     stood alone with no qualifier at all, and on a phone it can
                     sit a screen and a half below the notice that explains it. -->
                <span v-else-if="row.state === 'unknown'" class="muted sf-why">
                  no rule could be applied to {{ unreadable.length === 1 ? 'the unreadable ingredient' : 'the unreadable ingredients' }} above
                </span>
              </dd>
            </template>
          </dl>
          <p v-if="dietsQuiet.length" class="muted sf-quiet">
            <span class="sf-quiet-h">No rule conflicted with these — which is
              not a certification</span>:
            {{ dietsQuiet.join(' · ') }}
          </p>
          <!-- Headline always visible, the engine's own reasons on demand. -->
          <details v-if="advisoryDiets.length" class="sf-advisory">
            <summary>
              {{ advisoryDiets.map((d) => d.label).join(', ') }}
              {{ advisoryDiets.length === 1 ? 'is' : 'are' }}
              informational — never certified from ingredient text.
            </summary>
            <ul class="sf-reasons">
              <li v-for="d in advisoryDiets" :key="d.key">
                <span class="sf-line">{{ d.label }}</span>
                <span class="muted"> — {{ d.advisory }}</span>
              </li>
            </ul>
          </details>

          <!-- Provenance, in the same quiet register as the nutrition panel's
               line: a muted sentence under the data, not a badge above it. -->
          <p class="muted sf-prov">
            {{ safetyScope }}
            This reports what the ingredient text says — it is not a safety
            check. Always read the packaging.
          </p>
        </template>
      </div>

      <div class="card">
        <div class="page-head" style="margin-bottom:10px">
          <h2>Nutrition <span class="muted" style="font-weight:400">— per serving</span></h2>
          <div class="grow"></div>
          <button class="secondary sm" :disabled="estimatingNutrition" @click="estimateNutrition">
            {{ estimatingNutrition ? 'Working…' : (recipe.nutrition ? 'Recalculate' : 'Calculate') }}
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
        <!-- Where the number came from. A figure computed from reference data
             and one guessed by a language model are different claims, and until
             now the panel presented them identically. Deliberately quiet: this
             is a work surface, so it is a muted line under the grid, not a
             badge competing with the values. -->
        <p v-if="recipe.nutrition && nutritionProvenance" class="muted nutri-prov">
          {{ nutritionProvenance }}
        </p>
        <p v-else class="muted" style="margin:0">
          No nutrition yet — calculate it, or add it in edit mode.
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
              <button v-if="v.status === 'open'" class="secondary sm" @click="promoteVersion(v)">⬆ Promote</button>
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
/* A section sub-heading is a full-width grid item, not a cell pair: it must not
   consume an amount-column slot or the rows below it shift a column over. No
   border — the rows draw their own rules — and quiet by design: it's wayfinding
   inside a work surface, not a card heading. */
.ing-list li.ing-sec {
  display: block; grid-column: 1 / -1; padding: 14px 0 2px;
  font-size: 0.72rem; font-weight: 650; text-transform: uppercase;
  letter-spacing: 0.05em; color: var(--muted);
}
.ing-list li.ing-sec:first-child { padding-top: 2px; }
/* ---------- Steps ---------- */
/* The recipe-level clock. Three labelled values, not three stat cards: this is
   a work surface and the numbers are reference, not a dashboard. Sits above the
   method because its whole job is to be read before step 1. */
.step-clock {
  display: flex; flex-wrap: wrap; gap: 8px 24px;
  margin: 0 0 16px; padding: 10px 14px;
  background: var(--surface-2); border: 1px solid var(--border);
  border-radius: var(--radius);
}
.clock-item { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.clock-k { font-size: 0.72rem; font-weight: 650; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); }
.clock-v { font-size: 0.95rem; font-weight: 650; }
/* A phase heading. Deliberately the same quiet uppercase as an ingredient
   section (.ing-sec) — the two halves of a recipe are grouped by the same idea
   and must not look like two different features. */
.step-sec {
  margin: 20px 0 2px; font-size: 0.72rem; font-weight: 650;
  text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted);
}
.step-sec:first-child { margin-top: 0; }
.step-list { max-width: 68ch; }
.step-ol { margin: 0; padding-left: 26px; }
.step-ol li { padding: 7px 0; }
/* The number is wayfinding, not content: tabular so the column stays straight
   past step 9, and muted so it never competes with the instruction. */
.step-ol li::marker { color: var(--muted); font-weight: 650; font-variant-numeric: tabular-nums; }
.step-text { display: block; }
/* Chips sit UNDER their step, not inline: inline, a "12–72 hr" badge reflows
   mid-sentence on a phone and the sentence stops being readable. */
.step-chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
/* Chips are deliberately uncoloured. Tinting the waiting ones amber was tried
   and rejected twice over: `warning` is a status token and waiting is not a
   warning, and six amber pills down a method compete with the one orange the
   page spends on its primary action. The 💤/🔥/⏱ emoji already distinguishes
   them, and the emphasis belongs in the summary bar above — that is the thing
   a cook needs to notice, and it only has to shout once. */
.step-chips .chip { color: var(--muted); font-weight: 600; }
.step-note { margin: 16px 0 0; font-size: 0.78rem; max-width: 68ch; }
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
/* Annotations lifted out of the food name — the alternative measure ("or 1/2
   tsp") and "optional". ONE class for both, deliberately: they are the same
   kind of thing (a property of the ingredient, not part of its name) and giving
   them two treatments made the row argue with itself.
   "optional" was a bordered uppercase pill, which was wrong twice over — it was
   the only 999px radius in an app with a single radius token, and it was the
   boldest SHAPE on a dense work surface while its muted colour said "ignore
   me". As muted lowercase it matches the alternative and both read as asides.
   Each carries its own leading separator ("or", "·") so two of them on one row
   read as a list instead of running together. */
.ing-alt { color: var(--muted); margin-left: 8px; }
/* An em dash where an amount would be. Lighter than a real amount so a column
   of them doesn't compete with the numbers it sits among, but present — the
   empty cell it replaces read as a rendering fault rather than a deliberate
   "this one has no amount". */
.ing-amt.is-none { color: var(--muted); font-weight: 400; }
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
.nutri-prov { margin: 10px 0 0; font-size: 0.82rem; }
.nutri-lbl { font-size: 0.78rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.03em; }

/* --- What this contains ---------------------------------------------------
   A work surface: tokens only, no new colour, and the accent is deliberately
   absent — this card has no primary action, so spending the accent here would
   compete with the page's real one.

   The whole visual system is a 3px left rail, because the rail is the only
   decoration that encodes something true: how much attention the row wants.
     solid --danger   contains / incompatible
     solid --warning  may contain / uncertain
     dashed --warning NOT SCREENED
   Dashed is doing real work. It keeps `unknown` in the attention family (so it
   cannot be mistaken for "fine") while reading as structurally incomplete
   rather than as a positive finding — and it is a second, non-colour signal,
   so the distinction survives for a colour-blind reader and in a screenshot.
   The state word is always spelled out too; colour is never the only signal. */
.sf-skel { display: grid; gap: 8px; }
/* Shaped like the rows it replaces: three ragged lines, not a spinner. */
.sf-skel > div { height: 16px; }
.sf-skel-a { width: 45%; }
.sf-skel-b { width: 62%; }
.sf-skel-c { width: 38%; }
.sf-h {
  margin: 16px 0 8px; font-size: 0.78rem; color: var(--muted);
  text-transform: uppercase; letter-spacing: 0.03em; font-weight: 600;
}
.sf-h:first-of-type { margin-top: 4px; }

/* STATE | labels + reason. State on the left so severity is the axis the eye
   scans — the first attempt put the allergen names there, and once rows that
   agree are merged a nine-name list shreds a narrow label column while leaving
   the right half of the card empty. The left column is now four known words,
   so it is a fixed, quiet rhythm and the names get the width they need.
   Stacked on a phone, where the reason text needs all of it. */
.sf-rows {
  /* 132px fits the longest state word ("Incompatible") on one line at this
     size; narrower and it either wrapped to two lines or ran under the names.
     `stretch`, not `baseline`: the rail is only as tall as the box it is drawn
     on, and a 3px dashed border across one line of text renders about two
     dashes — indistinguishable from solid unless a solid sibling happens to be
     directly adjacent. Stretching the term to the row's height gives the dash
     pattern room to actually read as a dash pattern. */
  display: grid; grid-template-columns: 132px 1fr;
  gap: 6px 12px; margin: 0; align-items: stretch;
}
.sf-dt {
  margin: 0; padding: 2px 0 2px 10px; font-weight: 600; font-size: 0.9rem;
  border-left: 3px solid var(--border);
}
.sf-dd {
  margin: 0; padding: 2px 0; font-size: 0.9rem;
  display: flex; flex-wrap: wrap; align-content: flex-start; gap: 2px 8px;
  /* Width 0, not `border-left: none`, and NOT re-declared as a shorthand in
     the media query below. The tone classes set only colour and style, so a
     later shorthand would silently reset the colour back to --border — which
     is exactly what happened: every stacked row drew a neutral grey rail
     regardless of its state. Only the WIDTH changes at the breakpoint. */
  border-left: 0 solid var(--border);
}
.sf-labels { font-weight: 600; }
.sf-why { font-size: 0.84rem; }
/* The engine's note gets its own line: it is a sentence, not a term list. */
.sf-note { flex-basis: 100%; }
.sf-advisory { margin: 10px 0 0; font-size: 0.82rem; color: var(--muted); }
.sf-advisory > summary { cursor: pointer; }
.sf-advisory > summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }

.sf-tone-bad { border-left-color: var(--danger); }
.sf-tone-warn { border-left-color: var(--warning); }
/* The one that matters. See the note above. */
.sf-tone-unscreened { border-left-style: dashed; border-left-color: var(--warning); }
.sf-tone-quiet { border-left-color: var(--border); }
/* The -text variants, not the raw status tokens: a state word is the finding
   itself, and --danger fails AA as text in dark while --warning fails it in
   light. The rails above keep the raw tokens — a 3px rule only needs 3:1. */
.sf-text-bad { color: var(--danger-text); }
.sf-text-warn { color: var(--warning-text); }
.sf-text-unscreened { color: var(--warning-text); }
.sf-text-quiet { color: var(--muted); }

/* The unreadable-ingredients notice, and the unavailable state — same shape,
   because they say the same thing to a cook: part of this is unscreened.
   The rail is a ::before, not a border-left: a left-only dashed border on a
   rounded box bends its end dashes around the corner radius into little hooks,
   which made the card's signature device look like a rendering bug. */
.sf-notice {
  position: relative;
  margin: 0 0 12px; padding: 10px 12px 10px 15px; font-size: 0.88rem;
  background: var(--surface-2); border-radius: var(--radius-sm);
}
.sf-notice::before {
  content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 3px;
  border-radius: var(--radius-sm) 0 0 var(--radius-sm);
  background: repeating-linear-gradient(
    to bottom, var(--warning) 0 5px, transparent 5px 10px);
}
.sf-reasons { margin: 6px 0 0; padding-left: 18px; font-size: 0.84rem; }
.sf-reasons li { margin: 2px 0; }
.sf-line { font-weight: 600; }
.sf-retry { display: block; margin: 10px 0 0; }

/* The quiet states, collapsed to one line. Indented to the rail (10px padding
   + 3px border) so it reads as part of the same list, but with no rail of its
   own — it is the absence of a finding, and a marker would make it look like
   one. The caveat is NOT shrunk: it was 0.8rem against the 0.84rem line it
   qualified, which made the most load-bearing clause on the card the least
   legible thing in it. */
.sf-quiet-lead { margin: 6px 0 0; padding-left: 13px; font-size: 0.84rem; }
.sf-quiet { margin: 6px 0 0; padding-left: 13px; font-size: 0.84rem; }
.sf-quiet-h { font-weight: 600; }
.sf-prov { margin: 16px 0 0; font-size: 0.82rem; }

@media (max-width: 560px) {
  /* One column: the reason text is what a phone needs the width for, and a
     132px state column would leave it two words wide.

     Stacked, the dd is directly below the dt, so the rail CAN continue through
     it — and must. It carried `transparent` before, which left a ~20px stub
     next to the state word: two dashes, which is not a pattern anyone reads as
     dashed. Running the tone border down the labels too makes the rail 4–6
     dashes tall AND brackets state→labels as one unit, which is the thing the
     two-column alignment was doing before it collapsed. */
  .sf-rows { grid-template-columns: 1fr; gap: 0; }
  .sf-dt { padding-bottom: 0; }
  .sf-dd { padding: 0 0 10px 10px; margin-bottom: 2px; border-left-width: 3px; }
  .sf-quiet, .sf-quiet-lead { padding-left: 10px; }
}
/* Media: one grid for photos, clips, embeds and the add tile. auto-fill so the
   slot is two-up on a desktop card and one-up on a phone with no breakpoint of
   its own, and every cell shares the 16/9 frame so a portrait photo, a video
   player and the dashed add tile all line up on the same baseline. */
.media-grid {
  display:grid; gap:14px;
  grid-template-columns:repeat(auto-fill, minmax(240px, 1fr));
}
.media-frame {
  width:100%; aspect-ratio:16/9; border:0; border-radius:var(--radius-sm);
  background:var(--surface-2); display:block; object-fit:cover;
}
.media-link {
  display:flex; align-items:center; justify-content:center; padding:14px;
  border:1px solid var(--border); text-decoration:none; text-align:center;
}
.media-foot { display:flex; align-items:center; gap:8px; margin-top:6px; font-size:.9rem }
/* min-width:0 is the load-bearing part — a flex item defaults to min-content
   width, so without it the title refuses to shrink and pushes Remove out of
   the tile instead of ellipsising. */
.media-title {
  flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
}
/* Quiet by default: removing a photo is destructive but it is not the card's
   headline, and a full bordered button made it louder than the add tile.
   Danger colour carries the meaning; the border only appears on interaction. */
.media-remove {
  flex-shrink:0; padding:4px 8px; font-size:.85rem;
  background:transparent; color:var(--danger); border:1px solid transparent;
}
.media-remove:hover, .media-remove:focus-visible {
  background:var(--danger-soft); border-color:var(--danger); color:var(--danger);
}

/* The empty slot. A dashed frame rather than a filled card so it reads as a
   space waiting to be filled, next to siblings that are solid. */
.media-add {
  width:100%; aspect-ratio:16/9; display:flex; flex-direction:column;
  align-items:center; justify-content:center; gap:8px; padding:16px;
  background:transparent; color:var(--muted); text-align:center;
  /* --muted, not --border, for the dashed stroke. --border (#382e26 in dark)
     is tuned for a solid hairline between filled surfaces; as a 1px DASHED
     line it loses half its pixels and disappears against the dark card, so
     the only affordance in an empty slot became invisible in one theme. The
     foreground-muted token is the same semantic (a placeholder waiting to be
     filled) and reads in both. Not a new token — a deliberate reuse. */
  border:1px dashed var(--muted); border-radius:var(--radius-sm);
  cursor:pointer;
  /* The global button rule is white-space:nowrap, which is right for a label
     and wrong for a tile whose whole job is to carry a sentence — without
     this the copy runs straight out of the tile and across the card. */
  white-space:normal;
}
.media-add:hover:not(:disabled) {
  border-color:var(--accent); color:var(--text); background:var(--accent-soft);
}
/* The OPEN state is deliberately NEUTRAL, not accent-tinted. --accent-soft is a
   translucent red-orange: over the light surface it reads as a pink error fill,
   and this page already spends red on Delete, so "panel is open" was saying
   "something went wrong". A pressed surface + a solid border says open. */
/* `.open:hover` is listed explicitly: `.media-add:hover:not(:disabled)` scores
   higher than a plain `.media-add.open`, so without it the accent-soft hover
   kept winning and an open panel still looked pink while the pointer rested on
   the tile — which is most of the time, since the pointer is there from the
   click that opened it. */
.media-add.open, .media-add.open:hover {
  background:var(--surface-2); border-style:solid; border-color:var(--border);
  color:var(--text);
}
.media-add.open .media-add-plus { color:var(--muted) }
.media-add-plus { font-size:1.6rem; line-height:1; color:var(--accent) }
/* font-weight back to normal: the global button rule is 600, which makes a
   sentence read as a shout. Only the lead line stays bold. */
.media-add-copy { display:block; font-size:.85rem; line-height:1.35; font-weight:400 }
.media-add-copy strong { display:block; color:var(--text); font-size:.95rem; margin-bottom:4px }

/* Single column at one width, per the forms rule. max-width rather than a
   fixed px on each field: three inputs at three different widths inside one
   form was the most machine-made thing on the card. */
.media-panel {
  margin-top:16px; padding-top:16px; border-top:1px solid var(--border);
  display:grid; gap:12px; max-width:32rem;
}
.media-file { justify-content:center }
.media-hint { display:block; margin-top:6px; font-size:.8rem; line-height:1.4 }

.btnlike { cursor:pointer; display:inline-flex; align-items:center; padding:8px 12px;
  border:1px solid var(--border); border-radius:var(--radius-sm); }
@media (max-width:560px) { .media-foot { flex-wrap:wrap } }
@media (prefers-reduced-motion: reduce) { .media-add { transition:none } }
</style>
