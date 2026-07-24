<script setup>
import { ref, computed, watch } from 'vue'
import { api } from '../api'
import { useUI } from '../stores/ui'
import ErrorState from '../components/ErrorState.vue'
import { useLoader } from '../composables/useLoader'

const ui = useUI()
const entries = ref([])
const recipes = ref([])
const view = ref('week') // 'day' | 'week' | 'month'
const anchor = ref(startOfDay(new Date()))
const busy = ref(false)

function startOfDay(d) {
  const x = new Date(d)
  x.setHours(0, 0, 0, 0)
  return x
}
function mondayOf(d) {
  const x = startOfDay(d)
  x.setDate(x.getDate() - ((x.getDay() + 6) % 7)) // 0 = Monday
  return x
}
function iso(d) {
  // Local calendar date (not UTC) so a day never shifts across the tz boundary.
  const x = new Date(d)
  return `${x.getFullYear()}-${String(x.getMonth() + 1).padStart(2, '0')}-${String(x.getDate()).padStart(2, '0')}`
}
function addDays(d, n) {
  const x = new Date(d)
  x.setDate(x.getDate() + n)
  return x
}
const TODAY = iso(new Date())
const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

// Visible date range per view (month spans whole weeks for a clean grid).
const range = computed(() => {
  if (view.value === 'day') return { start: anchor.value, end: anchor.value }
  if (view.value === 'month') {
    const first = new Date(anchor.value.getFullYear(), anchor.value.getMonth(), 1)
    const last = new Date(anchor.value.getFullYear(), anchor.value.getMonth() + 1, 0)
    return { start: mondayOf(first), end: addDays(mondayOf(last), 6) }
  }
  const s = mondayOf(anchor.value)
  return { start: s, end: addDays(s, 6) }
})

// Day cards for day/week (same markup, different container).
const dayList = computed(() => {
  const n = view.value === 'day' ? 1 : 7
  const start = view.value === 'day' ? anchor.value : mondayOf(anchor.value)
  return Array.from({ length: n }, (_, i) => {
    const d = addDays(start, i)
    return {
      date: iso(d),
      label: d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' }),
    }
  })
})

// Compact cells for the month calendar.
const monthCells = computed(() => {
  const out = []
  const m = anchor.value.getMonth()
  for (let d = new Date(range.value.start); iso(d) <= iso(range.value.end); d = addDays(d, 1)) {
    out.push({ date: iso(d), num: d.getDate(), inMonth: d.getMonth() === m, isToday: iso(d) === TODAY })
  }
  return out
})

const title = computed(() => {
  if (view.value === 'day') {
    return anchor.value.toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' })
  }
  if (view.value === 'month') {
    return anchor.value.toLocaleDateString(undefined, { month: 'long', year: 'numeric' })
  }
  return `Week of ${iso(mondayOf(anchor.value))}`
})

async function load() {
  const [plan, recs] = await Promise.all([
    api.get(`/mealplans?start=${iso(range.value.start)}&end=${iso(range.value.end)}`),
    api.get('/recipes'),
  ])
  entries.value = plan.items
  recipes.value = recs.items
}
const { loading, error, reload } = useLoader(load)
watch([view, anchor], reload)

function shift(dir) {
  if (view.value === 'day') anchor.value = addDays(anchor.value, dir)
  else if (view.value === 'week') anchor.value = addDays(anchor.value, dir * 7)
  else {
    const a = new Date(anchor.value)
    a.setDate(1)
    a.setMonth(a.getMonth() + dir)
    anchor.value = a
  }
}
function openDay(date) {
  anchor.value = startOfDay(new Date(`${date}T00:00:00`))
  view.value = 'day'
}

function entriesFor(date) {
  return entries.value.filter((e) => e.date && e.date.slice(0, 10) === date)
}

const adding = ref(null)
const form = ref({ mealType: 'dinner', recipeId: '', title: '' })

async function addEntry(date) {
  try {
    await api.post('/mealplans', { date, ...form.value })
    form.value = { mealType: 'dinner', recipeId: '', title: '' }
    adding.value = null
    await reload()
  } catch (e) {
    ui.error(e.message)
  }
}
async function del(id) {
  try {
    await api.del(`/mealplans/${id}`)
    await reload()
  } catch (e) {
    ui.error(e.message)
  }
}
async function generate() {
  busy.value = true
  try {
    await api.post('/ai/plan', { start: iso(mondayOf(anchor.value)), days: 7 })
    ui.toast('Week planned')
    await reload()
  } catch (e) {
    ui.error(e.message)
  } finally {
    busy.value = false
  }
}
async function buildList() {
  try {
    const sl = await api.post('/shopping-lists', { name: `Plan ${iso(range.value.start)}` })
    const res = await api.post(`/shopping-lists/${sl.id}/from-mealplan`, {
      start: iso(range.value.start),
      end: iso(range.value.end),
    })
    ui.toast(`Shopping list created (${res.added} items)`)
  } catch (e) {
    ui.error(e.message)
  }
}
</script>

<template>
  <div class="page-head">
    <h1>Meal plan</h1>
    <div class="grow"></div>
    <button class="secondary" @click="generate" :disabled="busy">
      {{ busy ? 'Planning…' : '✨ Plan with AI' }}
    </button>
    <button @click="buildList">🛒 Build shopping list</button>
  </div>

  <div class="toolbar">
    <div class="seg" role="tablist" aria-label="Calendar view">
      <button
        v-for="v in ['day', 'week', 'month']"
        :key="v"
        role="tab"
        :aria-selected="view === v"
        :class="{ active: view === v }"
        @click="view = v"
      >{{ v[0].toUpperCase() + v.slice(1) }}</button>
    </div>
    <div class="grow"></div>
    <button class="secondary sm" :aria-label="`Previous ${view}`" @click="shift(-1)">←</button>
    <strong class="mp-title">{{ title }}</strong>
    <button class="secondary sm" :aria-label="`Next ${view}`" @click="shift(1)">→</button>
  </div>

  <div v-if="loading" class="mp-week">
    <div v-for="n in (view === 'day' ? 1 : 7)" :key="n" class="skeleton" style="height:150px"></div>
  </div>

  <ErrorState v-else-if="error" :message="error" @retry="reload" />

  <!-- Month: compact calendar; click a day to plan it. -->
  <template v-else-if="view === 'month'">
    <div class="mp-weekhead"><span v-for="w in WEEKDAYS" :key="w">{{ w }}</span></div>
    <div class="mp-month">
      <div
        v-for="c in monthCells"
        :key="c.date"
        class="mp-cell"
        :class="{ dim: !c.inMonth, today: c.isToday }"
        role="button"
        tabindex="0"
        :aria-label="`Plan ${c.date}`"
        @click="openDay(c.date)"
        @keydown.enter="openDay(c.date)"
        @keydown.space.prevent="openDay(c.date)"
      >
        <div class="num">{{ c.num }}</div>
        <div v-for="e in entriesFor(c.date)" :key="e.id" class="chip-sm">
          {{ e.recipe ? e.recipe.name : e.title }}
        </div>
      </div>
    </div>
  </template>

  <!-- Day (single, focused) / Week (7 equal columns) share the day-card markup. -->
  <div v-else :class="view === 'day' ? 'mp-day' : 'mp-week'">
    <div v-for="d in dayList" :key="d.date" class="card mp-daycard">
      <h3>{{ d.label }}</h3>
      <div v-for="e in entriesFor(d.date)" :key="e.id" class="row" style="margin-bottom:6px">
        <span class="fill">
          <span class="badge">{{ e.mealType }}</span>
          {{ e.recipe ? e.recipe.name : e.title }}
        </span>
        <button class="ghost sm danger" :aria-label="`Remove ${e.recipe ? e.recipe.name : e.title}`" @click="del(e.id)">✕</button>
      </div>

      <template v-if="adding === d.date">
        <select v-model="form.mealType" style="margin-bottom:6px">
          <option>breakfast</option><option>lunch</option>
          <option>dinner</option><option>snack</option>
        </select>
        <select v-model="form.recipeId" style="margin-bottom:6px">
          <option value="">— free text —</option>
          <option v-for="r in recipes" :key="r.id" :value="r.id">{{ r.name }}</option>
        </select>
        <input v-if="!form.recipeId" v-model="form.title" placeholder="Meal" style="margin-bottom:6px" />
        <div class="row">
          <button class="sm" @click="addEntry(d.date)">Add</button>
          <button class="secondary sm" @click="adding = null">Cancel</button>
        </div>
      </template>
      <button v-else class="ghost sm" style="margin-top:4px" @click="adding = d.date">＋ Add meal</button>
    </div>
  </div>
</template>

<style scoped>
/* Segmented view toggle. */
.seg { display: inline-flex; border: 1px solid var(--border); border-radius: var(--radius-sm); overflow: hidden; }
.seg button { border: 0; border-radius: 0; background: var(--surface); color: var(--muted); padding: 6px 14px; font-weight: 600; }
.seg button + button { border-left: 1px solid var(--border); }
.seg button.active { background: var(--accent); color: #fff; }
.mp-title { min-width: 8ch; text-align: center; }

/* Week: 7 equal-width day columns (no uneven/stretched box). Day: one focused card. */
/* stretch (default) → every day box is identical width AND height, regardless of
   how many meals a day has (no "first/today box is larger"). */
.mp-week { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: 12px; }
/* The global `.card + .card { margin-top }` (for STACKED cards) leaks into the
   grid: cards 2–7 are adjacent-sibling .cards and get a top margin the first
   card doesn't — which made the first box look larger/misaligned. Neutralize it. */
.mp-week .card + .card { margin-top: 0; }
.mp-day { max-width: 620px; }
.mp-daycard { padding: 14px; }
.mp-daycard h3 { margin-bottom: 8px; }

/* Month calendar. */
.mp-weekhead, .mp-month { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: 6px; }
.mp-weekhead { margin-bottom: 6px; }
.mp-weekhead span { text-align: center; font-size: 0.75rem; font-weight: 600; color: var(--muted); }
.mp-cell {
  min-height: 92px; padding: 6px; background: var(--surface);
  border: 1px solid var(--border); border-radius: var(--radius-sm); cursor: pointer;
  transition: border-color 0.12s;
}
.mp-cell:hover { border-color: var(--accent); }
.mp-cell.dim { opacity: 0.45; }
.mp-cell.today { border-color: var(--accent); }
.mp-cell .num { font-size: 0.8rem; font-weight: 650; margin-bottom: 2px; }
.mp-cell .chip-sm {
  font-size: 0.68rem; background: var(--accent-soft); color: var(--accent);
  border-radius: 4px; padding: 1px 4px; margin-top: 2px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}

/* Stack the week into a single column on small screens (still uniform boxes). */
@media (max-width: 900px) {
  .mp-week { grid-template-columns: 1fr; }
}
</style>
