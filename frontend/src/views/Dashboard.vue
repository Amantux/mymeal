<script setup>
// The dashboard is myMeal's "hub" — modelled on Edibl's kitchen landing: a warm
// one-line status, a grid of tap-through next-actions that show real data (not
// bare stats), and supportive microcopy so an empty app teaches its next step
// rather than looking broken.
import { ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import { useUI } from '../stores/ui'
import EmptyState from '../components/EmptyState.vue'
import ErrorState from '../components/ErrorState.vue'
import { useLoader } from '../composables/useLoader'

const router = useRouter()
const ui = useUI()

const recipes = ref([])
const totals = ref({ recipes: 0, mealsThisWeek: 0, shoppingItems: 0 })
const todaysMeals = ref([])
const weekPlan = ref([])

async function load() {
  const [summary, recipeList] = await Promise.all([
    api.get('/ha/summary'),
    api.get('/recipes'),
  ])
  totals.value = summary.totals
  todaysMeals.value = summary.todaysMeals || []
  weekPlan.value = summary.weekPlan || []
  recipes.value = recipeList.items
}
const { loading, error, reload } = useLoader(load)

// Tonight's headline meal — dinner if planned, else whatever is on today.
const tonight = computed(
  () => todaysMeals.value.find((m) => m.mealType === 'dinner') || todaysMeals.value[0] || null,
)

// One honest, supportive line: what (if anything) wants attention today.
const attention = computed(() => {
  if (loading.value || error.value) return ''
  const t = totals.value
  // Fresh install: welcome rather than list what's absent.
  if (!t.recipes && !t.mealsThisWeek && !t.shoppingItems) {
    return 'Welcome — add a recipe or ask the assistant to get going. 👋'
  }
  const parts = []
  // "Nothing this week" already implies nothing today — don't say both.
  if (!t.mealsThisWeek) parts.push('nothing planned this week')
  else if (!todaysMeals.value.length) parts.push('nothing planned for today')
  if (t.shoppingItems) parts.push(`${t.shoppingItems} to buy`)
  if (!parts.length) return "You're all set — today's planned and your list is clear. 🍽️"
  const s = parts.join(' · ')
  return s.charAt(0).toUpperCase() + s.slice(1)
})

// Upcoming plan entries to preview under the cards.
const upcoming = computed(() => weekPlan.value.slice(0, 6))

function fmtDay(iso) {
  const d = new Date(iso + 'T00:00:00')
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  const diff = Math.round((d - today) / 86400000)
  if (diff === 0) return 'Today'
  if (diff === 1) return 'Tomorrow'
  return d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })
}

function askAssistant() {
  ui.openAssistant('What can I cook right now?')
}
</script>

<template>
  <div class="page-head">
    <div>
      <h1>Dashboard</h1>
      <p v-if="attention" class="attention muted">{{ attention }}</p>
    </div>
  </div>

  <ErrorState v-if="error" :message="error" @retry="reload" />

  <template v-else>
    <!-- Next actions — each shows real data and taps through. -->
    <div v-if="loading" class="smart-grid" style="margin-bottom:24px">
      <div v-for="n in 4" :key="n" class="skeleton" style="height:118px"></div>
    </div>
    <div v-else class="smart-grid" style="margin-bottom:24px">
      <button
        class="smart"
        :class="{ attn: !todaysMeals.length }"
        @click="router.push('/plan')"
      >
        <div class="smart-ico">🍽️</div>
        <div class="smart-status">{{ tonight ? tonight.name : 'Not planned yet' }}</div>
        <div class="smart-title">Tonight</div>
        <div class="smart-peek">{{ tonight ? 'Tap to see the plan' : 'Tap to plan today' }}</div>
      </button>

      <button
        class="smart"
        :class="{ attn: !totals.mealsThisWeek }"
        @click="router.push('/plan')"
      >
        <div class="smart-ico">🗓️</div>
        <div class="smart-status"><span class="tnum">{{ totals.mealsThisWeek }}</span></div>
        <div class="smart-title">Meals this week</div>
        <div class="smart-peek">{{ totals.mealsThisWeek ? 'Across your plan' : 'Plan your week' }}</div>
      </button>

      <button
        class="smart"
        :class="{ attn: totals.shoppingItems > 0 }"
        @click="router.push('/shopping')"
      >
        <div class="smart-ico">🛒</div>
        <div class="smart-status"><span class="tnum">{{ totals.shoppingItems }}</span></div>
        <div class="smart-title">To buy</div>
        <div class="smart-peek">{{ totals.shoppingItems ? 'On your shopping list' : 'List is clear 🎉' }}</div>
      </button>

      <button class="smart" @click="askAssistant">
        <div class="smart-ico">💬</div>
        <div class="smart-status">Ask me</div>
        <div class="smart-title">Cooking assistant</div>
        <div class="smart-peek">What can I cook right now?</div>
      </button>
    </div>

    <!-- This week's plan at a glance. -->
    <div class="card" style="margin-bottom:24px">
      <div class="page-head" style="margin-bottom:12px">
        <h2>This week</h2>
        <div class="grow"></div>
        <button v-if="upcoming.length" class="secondary sm" @click="router.push('/plan')">
          See the full plan
        </button>
      </div>
      <div v-if="loading" class="skeleton" style="height:120px"></div>
      <EmptyState
        v-else-if="!upcoming.length"
        icon="🗓️"
        title="Nothing planned yet"
        hint="Plan a few meals and they'll show up here — no more “what's for dinner?” at 6pm."
      >
        <button @click="router.push('/plan')">Plan a meal</button>
      </EmptyState>
      <div v-else class="plan-peek">
        <div
          v-for="(e, i) in upcoming"
          :key="i"
          class="row"
          role="button"
          tabindex="0"
          @click="router.push('/plan')"
          @keydown.enter="router.push('/plan')"
          @keydown.space.prevent="router.push('/plan')"
        >
          <span class="pp-when">{{ fmtDay(e.date) }}</span>
          <span class="pp-meal">{{ e.mealType }}</span>
          <span class="pp-name">{{ e.name }}</span>
        </div>
      </div>
    </div>

    <!-- Recipe collection. -->
    <div class="card">
      <div class="page-head" style="margin-bottom:12px">
        <h2>Recently added</h2>
        <div class="grow"></div>
        <button v-if="recipes.length" class="secondary sm" @click="router.push('/recipes')">
          Browse recipes
        </button>
      </div>
      <div v-if="loading" class="skeleton" style="height:120px"></div>
      <EmptyState
        v-else-if="!recipes.length"
        icon="🍳"
        title="No recipes yet"
        hint="Add your first recipe — or paste a link and let the assistant import it for you."
      >
        <button @click="router.push('/import')">Import a recipe</button>
      </EmptyState>
      <div v-else class="card-grid">
        <div
          v-for="r in recipes.slice(0, 8)"
          :key="r.id"
          class="item-card"
          role="button"
          tabindex="0"
          @click="router.push(`/recipes/${r.id}`)"
          @keydown.enter="router.push(`/recipes/${r.id}`)"
          @keydown.space.prevent="router.push(`/recipes/${r.id}`)"
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
</template>

<style scoped>
.attention { margin-top: 4px; font-size: 0.9rem; }
</style>
