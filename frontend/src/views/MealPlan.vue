<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { api } from '../api'
import { useUI } from '../stores/ui'

const ui = useUI()
const entries = ref([])
const recipes = ref([])
const weekStart = ref(mondayOf(new Date()))
const busy = ref(false)

function mondayOf(d) {
  const x = new Date(d)
  const day = (x.getDay() + 6) % 7 // 0 = Monday
  x.setDate(x.getDate() - day)
  x.setHours(0, 0, 0, 0)
  return x
}
function iso(d) {
  return d.toISOString().slice(0, 10)
}
function addDays(d, n) {
  const x = new Date(d)
  x.setDate(x.getDate() + n)
  return x
}

const days = computed(() =>
  Array.from({ length: 7 }, (_, i) => {
    const d = addDays(weekStart.value, i)
    return {
      date: iso(d),
      label: d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' }),
    }
  }),
)

async function load() {
  const start = iso(weekStart.value)
  const end = iso(addDays(weekStart.value, 6))
  const [plan, recs] = await Promise.all([
    api.get(`/mealplans?start=${start}&end=${end}`),
    api.get('/recipes'),
  ])
  entries.value = plan.items
  recipes.value = recs.items
}
onMounted(load)
watch(weekStart, load)

function entriesFor(date) {
  return entries.value.filter((e) => e.date && e.date.slice(0, 10) === date)
}

const adding = ref(null) // date being added to
const form = ref({ mealType: 'dinner', recipeId: '', title: '' })

async function addEntry(date) {
  try {
    await api.post('/mealplans', { date, ...form.value })
    form.value = { mealType: 'dinner', recipeId: '', title: '' }
    adding.value = null
    await load()
  } catch (e) {
    ui.error(e.message)
  }
}
async function del(id) {
  await api.del(`/mealplans/${id}`)
  await load()
}

async function generate() {
  busy.value = true
  try {
    await api.post('/ai/plan', { start: iso(weekStart.value), days: 7 })
    ui.toast('Week planned')
    await load()
  } catch (e) {
    ui.error(e.message)
  } finally {
    busy.value = false
  }
}

async function buildList() {
  try {
    const sl = await api.post('/shopping-lists', { name: `Week of ${iso(weekStart.value)}` })
    const res = await api.post(`/shopping-lists/${sl.id}/from-mealplan`, {
      start: iso(weekStart.value),
      end: iso(addDays(weekStart.value, 6)),
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
    <button class="secondary sm" @click="weekStart = addDays(weekStart, -7)">← Prev</button>
    <strong>Week of {{ iso(weekStart) }}</strong>
    <button class="secondary sm" @click="weekStart = addDays(weekStart, 7)">Next →</button>
  </div>

  <div class="grid" style="grid-template-columns:repeat(auto-fill,minmax(200px,1fr))">
    <div v-for="d in days" :key="d.date" class="card" style="padding:14px">
      <h3 style="margin-bottom:8px">{{ d.label }}</h3>
      <div v-for="e in entriesFor(d.date)" :key="e.id" class="row" style="margin-bottom:6px">
        <span class="fill">
          <span class="badge">{{ e.mealType }}</span>
          {{ e.recipe ? e.recipe.name : e.title }}
        </span>
        <button class="ghost sm danger" @click="del(e.id)">✕</button>
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
