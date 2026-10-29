<script setup>
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api, mediaUrl } from '../api'
import EmptyState from '../components/EmptyState.vue'
import ErrorState from '../components/ErrorState.vue'
import { useLoader } from '../composables/useLoader'

const router = useRouter()
const recipes = ref([])
const categories = ref([])
const q = ref('')
const category = ref('')   // selected category slug ('' = all)

async function load() {
  const params = new URLSearchParams()
  if (q.value) params.set('q', q.value)
  if (category.value) params.set('category', category.value)
  const qs = params.toString()
  const [res] = await Promise.all([
    api.get('/recipes' + (qs ? `?${qs}` : '')),
    // Load the category list once (cheap) so the filter can populate.
    categories.value.length ? Promise.resolve() : loadCategories(),
  ])
  recipes.value = res.items
}
async function loadCategories() {
  try { categories.value = (await api.get('/categories')) || [] } catch { /* optional */ }
}
const { loading, error, reload } = useLoader(load)

// --- filter by what a recipe contains -------------------------------------
//
// The allergen/diet panel on each recipe is now collapsed by default, so these
// facts needed somewhere to be USEFUL in aggregate. Fetched on demand, not
// with the list: screening is ~15 ms a recipe, which is a tax every visitor
// would pay for a control most never open.
//
// THE POLICY, which the backend deliberately refuses to make (it reports four
// states and no boolean):
//
//   contains / mayContain  -> the recipe is REMOVED.
//   unknown                -> the recipe is KEPT, and MARKED.
//
// Neither half is arbitrary. Dropping `unknown` quietly would be the same
// false clearance in reverse: the cook would never learn that the one recipe
// which might have had it was hidden. Keeping it unmarked would present an
// unverified recipe as though it had been cleared. So it stays, visibly
// unverified, and the judgement is the cook's.
const safetyTags = ref(null)
const safetyLoadErr = ref('')
const safetyBusy = ref(false)
const avoid = ref([])          // allergen labels to avoid
const dietWanted = ref('')     // a single diet to require

async function loadSafetyTags() {
  if (safetyTags.value || safetyBusy.value) return
  safetyBusy.value = true
  safetyLoadErr.value = ''
  try {
    safetyTags.value = await api.get('/recipes/safety-tags')
  } catch (e) {
    safetyLoadErr.value = e.message || 'Could not screen your recipes.'
  } finally {
    safetyBusy.value = false
  }
}

const allergenOptions = computed(() => safetyTags.value?.allergens || [])
const dietOptions = computed(() => safetyTags.value?.diets || [])
const filtersActive = computed(() => avoid.value.length > 0 || !!dietWanted.value)

function tagsFor(id) { return safetyTags.value?.recipes?.[id] || null }

// Why a recipe is uncertain under the current filter, or '' when it is not.
// Text rather than a boolean so the card can SAY which allergen could not be
// determined -- "unverified" alone sends the reader to the recipe to find out
// what for.
function uncertainty(r) {
  const t = tagsFor(r.id)
  if (!t) return ''
  if (t.status === 'no_ingredients') return 'no ingredients to screen'
  const unsure = (avoid.value || []).filter((a) => (t.unknown || []).includes(a))
  if (unsure.length) return `could not tell about ${unsure.join(', ')}`
  if (dietWanted.value && (t.uncertainDiets || []).includes(dietWanted.value)) {
    return `could not tell about ${dietWanted.value}`
  }
  return ''
}

const shown = computed(() => {
  if (!filtersActive.value || !safetyTags.value) return recipes.value
  return recipes.value.filter((r) => {
    const t = tagsFor(r.id)
    // A recipe the screening could not read at all stays: a failure is not
    // evidence of anything, and dropping it would hide food on that basis.
    if (!t || t.status !== 'screened') return true
    for (const a of avoid.value) {
      if ((t.contains || []).includes(a)) return false
      if ((t.mayContain || []).includes(a)) return false
    }
    // Requiring a diet removes only a DECLARED conflict; "uncertain" is
    // surfaced by `uncertainty()` instead, for the same reason as above.
    if (dietWanted.value && (t.incompatibleDiets || []).includes(dietWanted.value)) {
      return false
    }
    return true
  })
})

function clearSafetyFilters() { avoid.value = []; dietWanted.value = '' }


let t
watch(q, () => {
  clearTimeout(t)
  t = setTimeout(reload, 200)
})
watch(category, reload)
</script>

<template>
  <div class="page-head">
    <h1>Recipes</h1>
    <div class="grow"></div>
    <button @click="router.push('/recipes/new')">＋ New recipe</button>
  </div>

  <div class="toolbar">
    <input v-model="q" placeholder="Search recipes…" style="max-width:340px" />
    <select v-if="categories.length" v-model="category" aria-label="Filter by category"
      style="max-width:240px">
      <option value="">All categories</option>
      <option v-for="c in categories" :key="c.id" :value="c.slug">{{ c.name }}</option>
    </select>
    <!-- Opens the screening on first use, so the cost lands on the person who
         asked for it. -->
    <details class="sf-filter" @toggle="loadSafetyTags">
      <summary>Allergens &amp; diets</summary>
      <div class="sf-filter-body">
        <p v-if="safetyBusy" class="muted">Screening your recipes…</p>
        <p v-else-if="safetyLoadErr" class="muted">
          {{ safetyLoadErr }}
          <button class="secondary sm" @click="safetyTags = null; loadSafetyTags()">
            Try again
          </button>
        </p>
        <template v-else-if="safetyTags">
          <p v-if="!allergenOptions.length && !dietOptions.length" class="muted">
            Nothing to filter on yet — no recipe here flags an allergen or a diet.
          </p>
          <template v-else>
            <fieldset v-if="allergenOptions.length" class="sf-set">
              <legend>Avoid</legend>
              <label v-for="a in allergenOptions" :key="a" class="sf-opt">
                <input v-model="avoid" type="checkbox" :value="a" />{{ a }}
              </label>
            </fieldset>
            <label v-if="dietOptions.length" class="sf-diet">
              Must suit
              <select v-model="dietWanted">
                <option value="">Any diet</option>
                <option v-for="d in dietOptions" :key="d" :value="d">{{ d }}</option>
              </select>
            </label>
            <button v-if="filtersActive" class="secondary sm" @click="clearSafetyFilters">
              Clear
            </button>
          </template>
          <!-- Said where the filtering happens, not only on the recipe page.
               A cook using this as an allergy filter has to know its limits
               before they trust the result, and "always read the packaging"
               after the fact is too late. -->
          <p class="muted sf-caveat">
            Read from ingredient text, not from labels. Recipes we could not
            read are kept and marked — never hidden, never cleared. This is not
            a safety check.
          </p>
          <p v-if="safetyTags.truncated" class="muted sf-caveat">
            Only the first 250 recipes were screened, so this filter is
            incomplete.
          </p>
          <p v-if="safetyTags.failed && safetyTags.failed.length" class="muted sf-caveat">
            {{ safetyTags.failed.length }} recipe(s) could not be screened and
            are shown unfiltered.
          </p>
        </template>
      </div>
    </details>
  </div>

  <div v-if="loading" class="card-grid">
    <div v-for="n in 4" :key="n" class="skeleton" style="height:200px"></div>
  </div>

  <ErrorState v-else-if="error" :message="error" @retry="reload" />

  <EmptyState
    v-else-if="!shown.length"
    icon="🍳"
    :title="(q || category || filtersActive) ? 'No matches' : 'No recipes yet'"
    :hint="filtersActive
      ? 'Every recipe here contains, or might contain, something you asked to avoid.'
      : ((q || category) ? 'Try a different search or category.' : 'Create your first recipe to get started.')"
  >
    <button v-if="filtersActive" class="secondary" @click="clearSafetyFilters">
      Clear allergen filters
    </button>
    <button v-if="!q && !category && !filtersActive" @click="router.push('/recipes/new')">＋ New recipe</button>
  </EmptyState>

  <div v-else class="card-grid">
    <div
      v-for="r in shown"
      :key="r.id"
      class="item-card"
      role="button"
      tabindex="0"
      :aria-label="`Open recipe ${r.name}`"
      @click="router.push(`/recipes/${r.id}`)"
      @keydown.enter="router.push(`/recipes/${r.id}`)"
      @keydown.space.prevent="router.push(`/recipes/${r.id}`)"
    >
      <div class="thumb">
        <img v-if="r.image" :src="mediaUrl(r.image)" alt="" />
        <span v-else>🍽️</span>
      </div>
      <div class="body">
        <div class="title">{{ r.name }}</div>
        <div class="sub">
          <span v-if="r.totalMinutes" class="tnum">{{ r.totalMinutes }} min</span>
          <span v-if="r.isFavorite"> · ⭐</span>
        </div>
        <!-- Kept by the filter but NOT cleared by it. The reason is named
             rather than badged, because "unverified" alone makes a reader open
             the recipe to find out what it was unverified for. -->
        <div v-if="filtersActive && uncertainty(r)" class="sf-unsure">
          ⚠ {{ uncertainty(r) }}
        </div>
        <div class="labels">
          <span v-for="c in r.categories" :key="c.id" class="chip">{{ c.name }}</span>
          <span v-for="tag in r.tags" :key="tag.id" class="badge">{{ tag.name }}</span>
        </div>
      </div>
    </div>
  </div>

</template>

<style scoped>
/* This view had no style block: its layout comes entirely from the global
   card/toolbar classes. The filter needs a handful of local rules, so one is
   added here rather than widening style.css with selectors only this page
   uses. */
.sf-filter { border: 1px solid var(--border); border-radius: var(--radius-sm); }
.sf-filter > summary {
  cursor: pointer; padding: 6px 10px; font-size: 0.9rem; white-space: nowrap;
}
.sf-filter > summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.sf-filter-body {
  padding: 4px 10px 10px; display: flex; flex-wrap: wrap;
  gap: 10px 16px; align-items: center;
}
.sf-set {
  border: 0; margin: 0; padding: 0; display: flex; flex-wrap: wrap;
  gap: 4px 12px; align-items: center;
}
.sf-set > legend { float: left; margin-right: 10px; padding: 0; font-size: 0.85rem; color: var(--muted); }
.sf-opt { display: inline-flex; align-items: center; gap: 4px; font-size: 0.9rem; }
.sf-diet { display: inline-flex; align-items: center; gap: 6px; font-size: 0.9rem; }
/* Full width, so the caveat is a sentence under the controls rather than a
   note squeezed in beside them. */
.sf-caveat { flex-basis: 100%; margin: 0; font-size: 0.8rem; }
/* On the CARD. A dashed rail, matching the detail panel's `unscreened` tone —
   the same visual language for the same fact, so a reader who has seen one
   recognises the other. */
.sf-unsure {
  margin-top: 6px; padding-left: 8px; font-size: 0.8rem;
  color: var(--warning-text); border-left: 3px dashed var(--warning);
}
</style>
