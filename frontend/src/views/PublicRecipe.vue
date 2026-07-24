<script setup>
// Public, read-only recipe view reached via a share link (/share/:token).
// Renders bare (no app shell — App.vue treats meta.public routes as standalone)
// and needs no account: it hits the unauthenticated /public/recipes endpoint.
import { ref, onMounted, computed } from 'vue'
import { useRoute } from 'vue-router'
import { api, apiUrl } from '../api'

const route = useRoute()
const recipe = ref(null)
const loading = ref(true)
const notFound = ref(false)

onMounted(async () => {
  try {
    recipe.value = await api.get(`/public/recipes/${route.params.token}`)
  } catch {
    notFound.value = true
  } finally {
    loading.value = false
  }
})

const imageSrc = computed(() =>
  recipe.value?.hasImage
    ? apiUrl(`/public/recipes/${route.params.token}/image`)
    : null,
)
const NUTRITION = [
  { key: 'calories', label: 'Calories', unit: '' },
  { key: 'protein', label: 'Protein', unit: ' g' },
  { key: 'carbs', label: 'Carbs', unit: ' g' },
  { key: 'fat', label: 'Fat', unit: ' g' },
]
</script>

<template>
  <div class="pub-wrap">
    <header class="pub-brand"><span class="logo">🍽️</span> myMeal</header>

    <div v-if="loading" class="skeleton" style="height:320px;border-radius:var(--radius-sm)"></div>

    <div v-else-if="notFound" class="pub-card center">
      <div style="font-size:2.4rem">🔒</div>
      <h1>Recipe not available</h1>
      <p class="muted">This share link is invalid or has been turned off by its owner.</p>
    </div>

    <article v-else class="pub-card">
      <img v-if="imageSrc" :src="imageSrc" alt="" class="pub-hero" />
      <h1>{{ recipe.name }}</h1>
      <p v-if="recipe.description" class="muted">{{ recipe.description }}</p>
      <div class="row wrap" style="gap:8px;margin:10px 0">
        <span v-if="recipe.servings" class="badge">🍽️ {{ recipe.servings }} servings</span>
        <span v-if="recipe.totalMinutes" class="badge tnum">⏱️ {{ recipe.totalMinutes }} min</span>
        <span v-for="c in recipe.categories" :key="c" class="chip">{{ c }}</span>
        <span v-for="t in recipe.tags" :key="t" class="badge">{{ t }}</span>
      </div>

      <h2>Ingredients</h2>
      <ul>
        <li v-for="(i, n) in recipe.ingredients" :key="n">{{ i }}</li>
      </ul>

      <h2>Steps</h2>
      <ol class="stack">
        <li v-for="(s, n) in recipe.steps" :key="n">{{ s }}</li>
      </ol>

      <template v-if="recipe.nutrition">
        <h2>Nutrition <span class="muted" style="font-weight:400;font-size:0.85rem">— per serving</span></h2>
        <div class="row wrap" style="gap:20px">
          <template v-for="f in NUTRITION" :key="f.key">
            <div v-if="recipe.nutrition[f.key] != null">
              <div class="tnum" style="font-size:1.3rem;font-weight:700">{{ recipe.nutrition[f.key] }}{{ f.unit }}</div>
              <div class="muted" style="font-size:0.75rem;text-transform:uppercase">{{ f.label }}</div>
            </div>
          </template>
        </div>
      </template>

      <p v-if="recipe.sourceUrl" style="margin-top:16px">
        <a :href="recipe.sourceUrl" target="_blank" rel="noreferrer">Original source ↗</a>
      </p>
    </article>

    <footer class="pub-foot muted">Shared with myMeal</footer>
  </div>
</template>

<style scoped>
.pub-wrap { max-width: 720px; margin: 0 auto; padding: 24px 16px 48px; }
.pub-brand { font-weight: 700; font-size: 1.1rem; margin-bottom: 16px; }
.pub-brand .logo { margin-right: 6px; }
.pub-card { background: var(--surface-raised, var(--surface)); border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 24px; }
.pub-card.center { text-align: center; }
.pub-hero { width: 100%; max-height: 340px; object-fit: cover; border-radius: var(--radius-sm); margin-bottom: 16px; }
.pub-card h1 { margin: 0 0 6px; }
.pub-card h2 { margin: 20px 0 8px; }
.pub-card ul, .pub-card ol { padding-left: 20px; margin: 0; }
.pub-foot { text-align: center; margin-top: 20px; font-size: 0.8rem; }
</style>
