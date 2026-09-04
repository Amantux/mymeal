<script setup>
// Full-screen guided cooking: one step at a time, big type, keeps the screen
// awake (Wake Lock API), keyboard/tap navigation, and an ingredients peek.
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import { useUI } from '../stores/ui'

const route = useRoute()
const router = useRouter()
const ui = useUI()

const recipe = ref(null)
const i = ref(0)
const showIngredients = ref(false)
let wakeLock = null

const steps = computed(() => recipe.value?.steps || [])
// Consecutive rows sharing a section group under one sub-heading, same as the
// recipe page. Without them a scaled list can show two identical foods ("85 g
// caster sugar" for the cake AND the drizzle) with nothing telling them apart —
// on the one surface where a wrong pick ruins the dish.
const ingredientGroups = computed(() => {
  const groups = []
  for (const ing of recipe.value?.ingredients || []) {
    const section = ing.section || ''
    const last = groups[groups.length - 1]
    if (last && last.section === section) last.rows.push(ing)
    else groups.push({ section, rows: [ing] })
  }
  return groups
})
const total = computed(() => steps.value.length)
const current = computed(() => steps.value[i.value]?.text || '')
const done = computed(() => total.value > 0 && i.value >= total.value - 1)

function next() {
  if (i.value < total.value - 1) i.value++
}
function prev() {
  if (i.value > 0) i.value--
}
function exit() {
  router.push(`/recipes/${route.params.id}`)
}

async function requestWake() {
  try {
    if ('wakeLock' in navigator) wakeLock = await navigator.wakeLock.request('screen')
  } catch {
    // Wake lock is best-effort (unsupported, or denied when tab not focused).
  }
}
function onVisible() {
  if (document.visibilityState === 'visible' && !wakeLock) requestWake()
}
function onKey(e) {
  if (e.key === 'ArrowRight' || e.key === ' ') {
    e.preventDefault()
    next()
  } else if (e.key === 'ArrowLeft') {
    prev()
  } else if (e.key === 'Escape') {
    exit()
  }
}

// The recipe page passes ?servings=N so cook mode opens at the count the
// reader was just looking at — the backend returns amountText/restText already
// scaled, which is exactly what the peek renders. No stepper here: mid-cook is
// the wrong moment to change your mind about quantities, and a control that
// re-fetches numbers under a wet thumb is worse than none.
const scaledFrom = ref(null) // the recipe's own servings, when showing scaled amounts
const wantServings = computed(() => {
  const n = Number(route.query.servings)
  return Number.isInteger(n) && n > 0 ? n : null
})

onMounted(async () => {
  try {
    const q = wantServings.value ? `?servings=${wantServings.value}` : ''
    recipe.value = await api.get(`/recipes/${route.params.id}${q}`)
    if (q && recipe.value.servings && wantServings.value !== recipe.value.servings) {
      scaledFrom.value = recipe.value.servings
    }
  } catch (e) {
    // A bad/stale servings param must not block cooking: fall back to the
    // recipe's own amounts (and no scale note, so the numbers stay honest).
    if (wantServings.value) {
      try {
        recipe.value = await api.get(`/recipes/${route.params.id}`)
      } catch (e2) {
        ui.error(e2.message)
        exit()
        return
      }
    } else {
      ui.error(e.message)
      exit()
      return
    }
  }
  requestWake()
  window.addEventListener('keydown', onKey)
  document.addEventListener('visibilitychange', onVisible)
})
onUnmounted(() => {
  window.removeEventListener('keydown', onKey)
  document.removeEventListener('visibilitychange', onVisible)
  if (wakeLock) {
    wakeLock.release?.()
    wakeLock = null
  }
})
</script>

<template>
  <div v-if="recipe" class="cook">
    <header class="cook-head">
      <div class="fill">
        <div class="cook-name">{{ recipe.name }}</div>
        <div class="cook-count tnum" v-if="total">Step {{ i + 1 }} of {{ total }}</div>
      </div>
      <button class="secondary" @click="showIngredients = !showIngredients">
        {{ showIngredients ? 'Hide' : 'Ingredients' }}
      </button>
      <button class="icon-btn" aria-label="Exit cook mode" @click="exit">✕</button>
    </header>

    <div class="cook-progress" v-if="total"><span :style="{ width: `${((i + 1) / total) * 100}%` }"></span></div>

    <div v-if="showIngredients" class="cook-ings card">
      <!-- Same amount/food split as the recipe page. This is the peek you open
           mid-step with a wet hand to re-check one number, so it's the surface
           that benefits most from the amount being its own scannable column. -->
      <p v-if="scaledFrom" class="muted cook-scale">
        Scaled to {{ wantServings }} servings (recipe makes {{ scaledFrom }}).
      </p>
      <ul class="cook-ing-list">
        <template v-for="(g, gi) in ingredientGroups" :key="gi">
          <li v-if="g.section" class="ci-sec">{{ g.section }}</li>
          <li v-for="ing in g.rows" :key="ing.id">
            <span class="ci-amt tnum">{{ [ing.amountText, ing.unitText].filter(Boolean).join(' ') }}</span>
            <span class="ci-food">{{ ing.restText ?? ing.display }}</span>
          </li>
        </template>
      </ul>
    </div>

    <main class="cook-step">
      <p v-if="total" class="step-text">{{ current }}</p>
      <p v-else class="muted">This recipe has no steps yet.</p>
    </main>

    <footer class="cook-nav">
      <button class="secondary big" :disabled="i === 0" @click="prev">← Back</button>
      <button v-if="!done" class="big" @click="next">Next →</button>
      <button v-else class="big" @click="exit">✓ Finish</button>
    </footer>
  </div>
</template>

<style scoped>
/* Above the ambient chat FAB (z-index 60) so cook mode is truly full-screen. */
.cook { position: fixed; inset: 0; z-index: 90; background: var(--surface); display: flex; flex-direction: column; padding: 16px; }
.cook-head { display: flex; align-items: center; gap: 12px; }
.cook-name { font-weight: 700; font-size: 1.05rem; }
.cook-count { color: var(--muted); font-size: 0.85rem; }
.cook-progress { height: 6px; background: var(--surface-2); border-radius: 999px; overflow: hidden; margin: 12px 0; }
.cook-progress span { display: block; height: 100%; background: var(--accent); transition: width 0.2s; }
.cook-ings { max-height: 34vh; overflow: auto; margin-bottom: 12px; }
.cook-scale { font-size: 0.85rem; margin: 0 0 8px; }
/* Grid on the LIST so the amount column sizes once to the widest amount and every
   food name shares one left edge — same reasoning as the recipe page. */
.cook-ing-list {
  display: grid; grid-template-columns: max-content minmax(0, 1fr);
  /* No column-gap: the divider is drawn per cell, so a gap punched a visible
     break in every rule. The spacing lives in the amount cell's padding. */
  column-gap: 0;
  list-style: none; margin: 0; padding: 0;
}
.cook-ing-list li { display: contents; }
/* Section sub-heading: a full-width grid item so it never consumes an
   amount-column slot; quiet, same treatment as the recipe page. */
.cook-ing-list li.ci-sec {
  display: block; grid-column: 1 / -1; padding: 12px 0 2px;
  font-size: 0.72rem; font-weight: 650; text-transform: uppercase;
  letter-spacing: 0.05em; color: var(--muted);
}
.cook-ing-list li.ci-sec:first-child { padding-top: 0; }
.ci-amt, .ci-food { padding: 8px 0; border-bottom: 1px solid var(--border); }
.cook-ing-list li:last-child .ci-amt,
.cook-ing-list li:last-child .ci-food { border-bottom: 0; }
.ci-amt { font-weight: 650; text-align: right; white-space: nowrap; padding-right: 12px; }
.ci-food { min-width: 0; }
.cook-step { flex: 1; display: grid; place-items: center; text-align: center; overflow: auto; }
.step-text { font-size: clamp(1.4rem, 4.5vw, 2.4rem); line-height: 1.4; max-width: 24ch; font-weight: 500; }
.cook-nav { display: flex; gap: 12px; }
.cook-nav .big { flex: 1; padding: 18px; font-size: 1.1rem; }
</style>
