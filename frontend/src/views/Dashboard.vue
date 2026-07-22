<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import EmptyState from '../components/EmptyState.vue'
import ErrorState from '../components/ErrorState.vue'
import { useLoader } from '../composables/useLoader'

const router = useRouter()
const recipes = ref([])

async function load() {
  recipes.value = (await api.get('/recipes')).items
}
const { loading, error, reload } = useLoader(load)
</script>

<template>
  <div class="page-head">
    <h1>Dashboard</h1>
    <div class="grow"></div>
    <button @click="router.push('/recipes')">Browse recipes</button>
  </div>

  <div class="stat-grid" style="margin-bottom:24px">
    <div class="stat">
      <div class="stat-ico">📖</div>
      <div class="value tnum">{{ recipes.length }}</div>
      <div class="label">Recipes</div>
    </div>
    <div class="stat">
      <div class="stat-ico">⭐</div>
      <div class="value tnum">{{ recipes.filter((r) => r.isFavorite).length }}</div>
      <div class="label">Favorites</div>
    </div>
  </div>

  <div class="card">
    <h2>Recently added</h2>
    <div v-if="loading" class="skeleton" style="height:120px"></div>
    <ErrorState v-else-if="error" :message="error" @retry="reload" />
    <EmptyState
      v-else-if="!recipes.length"
      icon="🍳"
      title="No recipes yet"
      hint="Add your first recipe to start building your collection."
    >
      <button @click="router.push('/recipes')">Add a recipe</button>
    </EmptyState>
    <div v-else class="card-grid">
      <div
        v-for="r in recipes.slice(0, 8)"
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
          <div v-if="r.totalMinutes" class="sub tnum">{{ r.totalMinutes }} min</div>
        </div>
      </div>
    </div>
  </div>
</template>
