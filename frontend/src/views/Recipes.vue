<script setup>
import { ref, watch } from 'vue'
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
  </div>

  <div v-if="loading" class="card-grid">
    <div v-for="n in 4" :key="n" class="skeleton" style="height:200px"></div>
  </div>

  <ErrorState v-else-if="error" :message="error" @retry="reload" />

  <EmptyState
    v-else-if="!recipes.length"
    icon="🍳"
    :title="(q || category) ? 'No matches' : 'No recipes yet'"
    :hint="(q || category) ? 'Try a different search or category.' : 'Create your first recipe to get started.'"
  >
    <button v-if="!q && !category" @click="router.push('/recipes/new')">＋ New recipe</button>
  </EmptyState>

  <div v-else class="card-grid">
    <div
      v-for="r in recipes"
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
        <div class="labels">
          <span v-for="c in r.categories" :key="c.id" class="chip">{{ c.name }}</span>
          <span v-for="tag in r.tags" :key="tag.id" class="badge">{{ tag.name }}</span>
        </div>
      </div>
    </div>
  </div>

</template>
