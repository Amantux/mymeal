<script setup>
import { ref, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import { useUI } from '../stores/ui'
import EmptyState from '../components/EmptyState.vue'

const router = useRouter()
const ui = useUI()
const recipes = ref([])
const loading = ref(true)
const q = ref('')
const creating = ref(false)
const newName = ref('')

async function load() {
  loading.value = true
  try {
    const res = await api.get('/recipes' + (q.value ? `?q=${encodeURIComponent(q.value)}` : ''))
    recipes.value = res.items
  } finally {
    loading.value = false
  }
}

let t
watch(q, () => {
  clearTimeout(t)
  t = setTimeout(load, 200)
})

onMounted(load)

async function create() {
  const name = newName.value.trim()
  if (!name) return
  try {
    const r = await api.post('/recipes', { name })
    ui.toast('Recipe created')
    router.push(`/recipes/${r.id}`)
  } catch (e) {
    ui.error(e.message)
  }
}
</script>

<template>
  <div class="page-head">
    <h1>Recipes</h1>
    <div class="grow"></div>
    <button @click="creating = true">＋ New recipe</button>
  </div>

  <div class="toolbar">
    <input v-model="q" placeholder="Search recipes…" style="max-width:340px" />
  </div>

  <div v-if="loading" class="card-grid">
    <div v-for="n in 4" :key="n" class="skeleton" style="height:200px"></div>
  </div>

  <EmptyState
    v-else-if="!recipes.length"
    icon="🍳"
    :title="q ? 'No matches' : 'No recipes yet'"
    :hint="q ? 'Try a different search.' : 'Create your first recipe to get started.'"
  >
    <button v-if="!q" @click="creating = true">＋ New recipe</button>
  </EmptyState>

  <div v-else class="card-grid">
    <div
      v-for="r in recipes"
      :key="r.id"
      class="item-card"
      @click="router.push(`/recipes/${r.id}`)"
    >
      <div class="thumb">
        <img v-if="r.image" :src="r.image" alt="" />
        <span v-else>🍽️</span>
      </div>
      <div class="body">
        <div class="title">{{ r.name }}</div>
        <div class="sub">
          <span v-if="r.totalMinutes" class="tnum">{{ r.totalMinutes }} min</span>
          <span v-if="r.isFavorite"> · ⭐</span>
        </div>
        <div class="labels">
          <span v-for="tag in r.tags" :key="tag.id" class="badge">{{ tag.name }}</span>
        </div>
      </div>
    </div>
  </div>

  <!-- Minimal create prompt -->
  <div v-if="creating" class="center-screen" style="position:fixed;inset:0;background:rgba(28,25,23,0.5);z-index:100">
    <div class="card auth-card">
      <h2>New recipe</h2>
      <label class="field">
        <span>Name</span>
        <input v-model="newName" placeholder="e.g. Roast Chicken" @keyup.enter="create" autofocus />
      </label>
      <div class="row" style="justify-content:flex-end">
        <button class="secondary" @click="creating = false; newName = ''">Cancel</button>
        <button @click="create">Create</button>
      </div>
    </div>
  </div>
</template>
