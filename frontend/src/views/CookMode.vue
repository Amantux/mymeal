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

onMounted(async () => {
  try {
    recipe.value = await api.get(`/recipes/${route.params.id}`)
  } catch (e) {
    ui.error(e.message)
    exit()
    return
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
      <ul class="cook-ing-list">
        <li v-for="ing in recipe.ingredients" :key="ing.id">
          <span class="ci-amt tnum">{{ [ing.amountText, ing.unitText].filter(Boolean).join(' ') }}</span>
          <span class="ci-food">{{ ing.restText ?? ing.display }}</span>
        </li>
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
.cook-ing-list { list-style: none; margin: 0; padding: 0; }
.cook-ing-list li {
  display: flex; align-items: baseline; gap: 10px;
  padding: 7px 0; border-bottom: 1px solid var(--border);
}
.cook-ing-list li:last-child { border-bottom: 0; }
/* Sized to content, not a fixed track: cook mode is phone-first and full of
   scaled fractions, which a fixed column would wrap. */
.ci-amt { flex: 0 0 auto; min-width: 5ch; font-weight: 650; white-space: nowrap; }
.ci-food { min-width: 0; }
.cook-step { flex: 1; display: grid; place-items: center; text-align: center; overflow: auto; }
.step-text { font-size: clamp(1.4rem, 4.5vw, 2.4rem); line-height: 1.4; max-width: 24ch; font-weight: 500; }
.cook-nav { display: flex; gap: 12px; }
.cook-nav .big { flex: 1; padding: 18px; font-size: 1.1rem; }
</style>
