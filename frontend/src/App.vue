<script setup>
import { computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { useAuth } from './stores/auth'
import { useUI } from './stores/ui'
import Toasts from './components/Toasts.vue'

const route = useRoute()
const auth = useAuth()
const ui = useUI()

const bare = computed(() => route.meta.public)

onMounted(() => {
  ui.applyTheme()
  window
    .matchMedia('(prefers-color-scheme: dark)')
    .addEventListener('change', () => ui.applyTheme())
})

const nav = [
  { to: '/', icon: '🏠', label: 'Dashboard' },
  { to: '/recipes', icon: '📖', label: 'Recipes' },
  { to: '/plan', icon: '🗓️', label: 'Meal Plan' },
  { to: '/pantry', icon: '🧺', label: 'Pantry' },
  { to: '/shopping', icon: '🛒', label: 'Shopping' },
  { to: '/import', icon: '📥', label: 'Import' },
]
</script>

<template>
  <template v-if="!bare">
    <div class="app-shell">
      <aside class="sidebar">
        <div class="brand">
          <span class="logo">🍽️</span> myMeal
        </div>
        <router-link v-for="n in nav" :key="n.to" :to="n.to" class="nav-link">
          <span class="ico">{{ n.icon }}</span> {{ n.label }}
        </router-link>
        <div class="section-label">Utilities</div>
        <router-link to="/settings" class="nav-link">
          <span class="ico">⚙️</span> Settings
        </router-link>
        <router-link to="/home-assistant" class="nav-link">
          <span class="ico">🔌</span> Home Assistant
        </router-link>
        <div class="spacer"></div>
        <div class="nav-link" style="cursor:default">
          <span class="ico">👤</span>
          <span class="muted" style="font-size:0.85rem">{{ auth.user?.name || 'Local' }}</span>
        </div>
      </aside>

      <div class="main">
        <header class="topbar">
          <div class="grow"></div>
          <button class="secondary icon-btn" title="Toggle theme" @click="ui.toggleTheme()">🌓</button>
          <div v-if="!auth.authDisabled" class="dropdown">
            <button class="secondary" @click="auth.logout()">Sign out</button>
          </div>
        </header>

        <div class="content">
          <router-view />
        </div>
      </div>
    </div>
    <Toasts />
  </template>

  <template v-else>
    <router-view />
    <Toasts />
  </template>
</template>
