import { createRouter, createWebHashHistory } from 'vue-router'
import { useAuth } from './stores/auth'

import Dashboard from './views/Dashboard.vue'
import Recipes from './views/Recipes.vue'
import RecipeDetail from './views/RecipeDetail.vue'
import RecipeImport from './views/RecipeImport.vue'
import MealPlan from './views/MealPlan.vue'
import Pantry from './views/Pantry.vue'
import ShoppingList from './views/ShoppingList.vue'
import Settings from './views/Settings.vue'
import HomeAssistant from './views/HomeAssistant.vue'
import Login from './views/Login.vue'

const routes = [
  { path: '/', component: Dashboard },
  { path: '/recipes', component: Recipes },
  { path: '/recipes/:id', component: RecipeDetail },
  { path: '/import', component: RecipeImport },
  { path: '/plan', component: MealPlan },
  { path: '/pantry', component: Pantry },
  { path: '/shopping', component: ShoppingList },
  { path: '/settings', component: Settings },
  { path: '/home-assistant', component: HomeAssistant },
  { path: '/login', component: Login, meta: { public: true } },
]

const router = createRouter({ history: createWebHashHistory(), routes })

router.beforeEach(async (to) => {
  const auth = useAuth()
  if (!auth.ready) await auth.bootstrap()
  if (!to.meta.public && !auth.isAuthed) return '/login'
  if (to.path === '/login' && auth.isAuthed) return '/'
})

export default router
